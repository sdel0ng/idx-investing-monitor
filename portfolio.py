"""
Portfolio monitor: your holdings with exit levels, targets, daily alerts, a volume-flow proxy
and broker-summary (broksum) analysis.

Per holding:
  - P/L vs your average and breakeven (after ~0.25% sell fee)
  - supports / resistances from swing lows/highs, moving averages, 20/60-day and 52-week extremes
  - EXIT level (daily close below a structural support at least ~1 ATR under the price),
    a last-line level, and targets T1/T2/T3 at the next resistances; trailing-stop rule after T1
  - alerts: exit, near exit, targets hit, breakeven reclaimed/lost, heavy-volume selling, flow turning
  - volume-flow proxy (automatic, free): Chaikin money flow + up/down volume ratio
  - broksum: paste top net buyers/sellers from your broker app -> institutional vs retail flow,
    buyers' average price, multi-day accumulation/distribution (saved per stock in broksum/)

Writes portfolio_summary.json and portfolio_<CODE>.png. Run manually:  python portfolio.py
Education only - not financial advice.
"""

import csv
import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import bmri_daily_check as core
import dividend_watchlist as engine
import market_context

FOLDER = engine.FOLDER
JAKARTA = engine.JAKARTA
PORTFOLIO_FILE = FOLDER / "portfolio.json"
SUMMARY_FILE = FOLDER / "portfolio_summary.json"
ALERT_STATE_FILE = FOLDER / "portfolio_alert_state.json"
BROKSUM_FOLDER = FOLDER / "broksum"
SELL_FEE = 0.0025


# ---------------------------------------------------------------- helpers
def idx_tick(price: float) -> int:
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def to_tick(price: float, mode: str = "round") -> int:
    tick = idx_tick(price)
    if mode == "down":
        return int(math.floor(price / tick) * tick)
    if mode == "up":
        return int(math.ceil(price / tick) * tick)
    return int(round(price / tick) * tick)


def pct(now, before):
    if not before:
        return None
    return (now / before - 1) * 100


def load_portfolio() -> dict:
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text(encoding="utf-8"))
    return {"max_loss_pct": 8, "holdings": []}


def save_portfolio(data: dict) -> None:
    PORTFOLIO_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def swing_points(series: pd.Series, kind: str, window: int = 3, lookback: int = 90) -> list:
    """Local lows/highs: lower (higher) than `window` bars on each side."""
    data = series.iloc[-lookback:]
    points = []
    for position in range(window, len(data) - window):
        value = data.iloc[position]
        neighbours = pd.concat([data.iloc[position - window:position], data.iloc[position + 1:position + 1 + window]])
        if kind == "low" and value <= neighbours.min():
            points.append((float(value), data.index[position]))
        if kind == "high" and value >= neighbours.max():
            points.append((float(value), data.index[position]))
    return points


def merge_levels(levels: list, reference: float, tolerance: float = 0.015) -> list:
    """Merge (price, name) levels within `tolerance` of each other."""
    merged = []
    for value, name in sorted(levels):
        if merged and abs(value - merged[-1][0]) / reference < tolerance:
            merged[-1] = (merged[-1][0], merged[-1][1] + " / " + name)
        else:
            merged.append((value, name))
    return merged


# ---------------------------------------------------------------- volume-flow proxy
def flow_proxy(prices: pd.DataFrame, days: int = 20) -> dict:
    high, low, close, volume = prices["High"], prices["Low"], prices["Close"], prices["Volume"].fillna(0)
    span = (high - low).replace(0, np.nan)
    multiplier = (((close - low) - (high - close)) / span).fillna(0)
    cmf = float((multiplier * volume).iloc[-days:].sum() / volume.iloc[-days:].sum()) if volume.iloc[-days:].sum() else 0.0
    change = close.diff()
    up_volume = float(volume[change > 0].iloc[-days:].sum()) if len(volume) else 0.0
    window = volume.iloc[-days:]
    window_change = change.iloc[-days:]
    up_volume = float(window[window_change > 0].sum())
    down_volume = float(window[window_change < 0].sum())
    ratio = up_volume / down_volume if down_volume else None
    cmf_prev = float((multiplier * volume).iloc[-days - 5:-5].sum() / volume.iloc[-days - 5:-5].sum()) if volume.iloc[-days - 5:-5].sum() else 0.0
    if cmf > 0.05 and (ratio or 0) > 1.15:
        label, css = "Accumulation (volume proxy)", "green"
    elif cmf < -0.05 and (ratio or 1) < 0.87:
        label, css = "Distribution (volume proxy)", "red"
    else:
        label, css = "Neutral flow", "grey"
    return {"cmf": cmf, "cmf_prev": cmf_prev, "up_down_ratio": ratio, "label": label, "class": css}


# ---------------------------------------------------------------- broksum
UNIT_TOKEN = re.compile(r"^([+-]?\d[\d.,]*)\s*([KMBT]?)$", re.IGNORECASE)


def parse_value_bn(raw: str):
    """'12.5', '12,5B', '850M', '-1.2T', 'Rp 3.4 B' -> Rp billion."""
    text = raw.replace("Rp", "").replace("rp", "").replace(" ", "")
    match = UNIT_TOKEN.match(text)
    if not match:
        return None
    number, unit = match.groups()
    value = core.parse_number(number)
    scale = {"": 1, "B": 1, "K": 1e-6, "M": 1e-3, "T": 1e3}[unit.upper()]
    return value * scale


def parse_broker_lines(text: str) -> list:
    """Lines like 'AK 12.5 1,300', 'YP -850M 1310', 'BK: 4,2B @1.295'."""
    brokers = []
    for line in text.splitlines():
        tokens = [token for token in re.split(r"[\s:@=;|]+", line.strip()) if token]
        if len(tokens) < 2 or not re.fullmatch(r"[A-Za-z]{2}", tokens[0]):
            continue
        value = parse_value_bn(tokens[1])
        if value is None:
            continue
        avg_price = None
        if len(tokens) >= 3:
            digits = re.sub(r"[^\d]", "", tokens[2])
            avg_price = float(digits) if digits else None
        brokers.append({"code": tokens[0].upper(), "net_bn": value, "avg_price": avg_price})
    return brokers


def broksum_file(code: str):
    BROKSUM_FOLDER.mkdir(exist_ok=True)
    return BROKSUM_FOLDER / f"{code.upper()}.csv"


def save_broksum(code: str, date: str, close: float, net_foreign, broker_text: str) -> dict:
    brokers = parse_broker_lines(broker_text)
    if not brokers and net_foreign is None:
        raise ValueError("No broker lines or net foreign value could be read.")
    entry = {"net_foreign": net_foreign, "brokers": brokers}
    analysis = core.analyse_broksum(entry, core.load_broker_map(), close)
    path = broksum_file(code)
    rows = []
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            rows = [row for row in csv.DictReader(handle) if row["date"] != date]
    rows.append({
        "date": date,
        "close": round(close, 2),
        "net_foreign": "" if net_foreign is None else round(net_foreign, 2),
        "institutional_net": round(analysis["institutional_net"], 2),
        "retail_net": round(analysis["retail_net"], 2),
        "signal": analysis["signal"],
        "buyer_avg": "" if not analysis["buyer_avg"] else round(analysis["buyer_avg"]),
        "seller_avg": "" if not analysis["seller_avg"] else round(analysis["seller_avg"]),
        "top_buyers": " ".join(f"{b['code']}:{b['net_bn']:+.1f}" for b in analysis["buyers"][:5]),
        "top_sellers": " ".join(f"{s['code']}:{s['net_bn']:+.1f}" for s in analysis["sellers"][:5]),
        "raw": broker_text.strip().replace("\n", " | ")[:500],
    })
    rows.sort(key=lambda row: row["date"])
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[-1].keys()))
        writer.writeheader()
        writer.writerows(rows)
    analysis["date"] = date
    return analysis


def broksum_history(code: str, days: int = 10) -> dict:
    path = broksum_file(code)
    if not path.exists():
        return {"entries": [], "summary": None}
    with path.open(encoding="utf-8") as handle:
        entries = list(csv.DictReader(handle))[-days:]
    if not entries:
        return {"entries": [], "summary": None}
    last5 = entries[-5:]
    accumulation = sum(row["signal"].startswith("ACCUMULATION") for row in last5)
    distribution = sum(row["signal"].startswith("DISTRIBUTION") for row in last5)
    inst_total = sum(float(row["institutional_net"] or 0) for row in last5)
    priced = [(float(row["institutional_net"] or 0), float(row["buyer_avg"])) for row in last5 if row["buyer_avg"]]
    weights = sum(abs(w) for w, _ in priced)
    buyer_avg = sum(abs(w) * p for w, p in priced) / weights if weights else None
    if accumulation >= 3 and inst_total > 0:
        verdict, css = "Institutions accumulating (multi-day)", "green"
    elif distribution >= 3 and inst_total < 0:
        verdict, css = "Institutions distributing (multi-day)", "red"
    elif inst_total > 0:
        verdict, css = "Leaning accumulation", "green"
    elif inst_total < 0:
        verdict, css = "Leaning distribution", "red"
    else:
        verdict, css = "No clear direction", "grey"
    return {
        "entries": entries[::-1],
        "summary": {
            "days": len(last5),
            "accumulation_days": accumulation,
            "distribution_days": distribution,
            "institutional_net_total": inst_total,
            "buyer_avg": buyer_avg,
            "verdict": verdict,
            "class": css,
        },
    }


# ---------------------------------------------------------------- holding analysis
def analyse_holding(holding: dict, max_loss_pct: float) -> dict:
    code = holding["code"].upper()
    prices, _ = engine.fetch_history(f"{code}.JK", years=2)
    prices = market_context.clean_bad_ticks(prices)
    for column in ("Open", "High", "Low"):
        prices[column] = prices[column].fillna(prices["Close"])
    close, high, low = prices["Close"], prices["High"], prices["Low"]
    last = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    avg = float(holding["avg"])
    lots = holding.get("lots")
    breakeven = to_tick(avg / (1 - SELL_FEE), "up")

    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = float(true_range.rolling(14).mean().iloc[-1])
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = float((100 - 100 / (1 + gain / loss.replace(0, np.nan))).iloc[-1])
    volume_avg = float(prices["Volume"].iloc[-21:-1].mean() or 0)
    volume_ratio = float(prices["Volume"].iloc[-1] / volume_avg) if volume_avg else None

    # --- levels
    supports_raw = [(value, f"swing low {when:%d %b}") for value, when in swing_points(low, "low") if value < last]
    resistances_raw = [(value, f"swing high {when:%d %b}") for value, when in swing_points(high, "high") if value > last]
    named = {
        "MA20": ma20, "MA50": ma50, "MA200": ma200,
        "5-day low": float(low.iloc[-5:].min()), "5-day high": float(high.iloc[-5:].max()),
        "20-day low": float(low.iloc[-20:].min()), "20-day high": float(high.iloc[-20:].max()),
        "60-day high": float(high.iloc[-60:].max()), "52-week low": float(low.iloc[-250:].min()),
        "52-week high": float(high.iloc[-250:].max()),
    }
    if not holding.get("hypothetical"):
        named["your breakeven"] = breakeven
    for name, value in named.items():
        if value is None:
            continue
        if value < last * 0.995:
            supports_raw.append((value, name))
        elif value > last * 1.005:
            resistances_raw.append((value, name))
    supports = sorted(merge_levels(supports_raw, last), reverse=True)
    resistances = merge_levels(resistances_raw, last)

    # --- exit plan
    candidates = [level for level in supports if level[0] <= last - 0.8 * atr]
    structural = candidates[0] if candidates else (last - 2 * atr, "2x ATR below price")
    exit_level = to_tick(structural[0] - idx_tick(structural[0]), "down")
    deeper = [level for level in supports if level[0] <= exit_level - 0.3 * atr]
    last_line = deeper[-1] if deeper else (exit_level - atr, "1x ATR below the exit")
    last_line_level = to_tick(last_line[0] - idx_tick(last_line[0]), "down")
    max_loss_level = to_tick(avg * (1 - max_loss_pct / 100), "down")
    # First hurdle = nearest resistance (often your breakeven / MA20); targets spaced >= 0.8 ATR apart
    hurdle = resistances[0] if resistances else None
    targets = []
    floor = last + 0.8 * atr
    for level in resistances:
        if level[0] >= floor:
            targets.append(level)
            floor = level[0] + 0.8 * atr
        if len(targets) == 3:
            break
    if len(targets) < 3:
        step = max(atr * 2, last * 0.05)
        base = targets[-1][0] if targets else last
        while len(targets) < 3:
            base += step
            targets.append((base, "ATR projection"))
    target_levels = [(to_tick(value, "down"), name) for value, name in targets]
    exit_from_avg = pct(exit_level, avg)
    risk_to_exit = pct(exit_level, last)
    reward_t1 = pct(target_levels[0][0], last)

    # --- verdict
    if last < exit_level:
        verdict, css = f"EXIT: closed below {exit_level:,}", "red"
    elif last - exit_level < 0.5 * atr:
        verdict, css = f"On alert: only {last - exit_level:,.0f} above the exit level", "amber"
    elif last >= target_levels[0][0]:
        verdict, css = "T1 reached: take partial profit, move exit to breakeven", "green"
    elif last >= breakeven:
        verdict, css = "HOLD in profit: protect breakeven", "green"
    else:
        verdict, css = "HOLD within plan (below breakeven)", "amber"

    notes = []
    if exit_from_avg is not None and exit_from_avg < -max_loss_pct:
        notes.append(
            f"The structural exit {exit_level:,} is {abs(exit_from_avg):.0f}% below your average - more than your "
            f"{max_loss_pct:.0f}% max loss. A {max_loss_level:,} stop would sit inside normal daily noise "
            f"(ATR {atr / last * 100:.0f}%), so consider cutting size instead of tightening the stop."
        )
    if atr / last > 0.06:
        notes.append(f"Very volatile: average daily range {atr / last * 100:.0f}%. Use exits on daily CLOSE, not intraday spikes.")
    if ma200 and last < ma200 and ma50 < ma200:
        notes.append("Long-term downtrend (below MA200, MA50 under MA200): treat rallies as chances to reduce, not to add.")
    if rsi < 30:
        notes.append(f"RSI {rsi:.0f} oversold: bounces are likely, but oversold can stay oversold.")
    if rsi > 72:
        notes.append(f"RSI {rsi:.0f} overbought: good place to take some profit.")

    flow = flow_proxy(prices)
    broksum = broksum_history(code)
    draw_chart(code, prices, ma20, ma50, avg, exit_level, target_levels, broksum)

    pnl_pct = pct(last, avg)
    pnl_rp = (last - avg) * lots * 100 if lots else None
    return {
        "code": code,
        "avg": avg,
        "lots": lots,
        "breakeven": breakeven,
        "date": close.index[-1].strftime("%Y-%m-%d"),
        "price": last,
        "change_1d": pct(last, previous),
        "high": float(high.iloc[-1]),
        "low": float(low.iloc[-1]),
        "pnl_pct": pnl_pct,
        "pnl_rp": pnl_rp,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "rsi": rsi,
        "atr": atr,
        "atr_pct": atr / last * 100,
        "volume_ratio": volume_ratio,
        "supports": [(to_tick(v), n) for v, n in supports[:5]],
        "resistances": [(to_tick(v), n) for v, n in resistances[:5]],
        "hurdle": (to_tick(hurdle[0]), hurdle[1]) if hurdle else None,
        "exit_level": exit_level,
        "exit_basis": structural[1],
        "exit_from_avg": exit_from_avg,
        "risk_to_exit": risk_to_exit,
        "last_line": last_line_level,
        "max_loss_level": max_loss_level,
        "targets": target_levels,
        "reward_t1": reward_t1,
        "verdict": verdict,
        "verdict_class": css,
        "notes": notes,
        "flow": flow,
        "broksum": broksum,
        "plan": ([
            f"First hurdle {to_tick(hurdle[0]):,} ({hurdle[1]}): a close above it turns the short-term picture up."
        ] if hurdle else []) + [
            f"Exit on a daily CLOSE below {exit_level:,} ({structural[1]}).",
            f"Last line: {last_line_level:,} - if it closes there, no excuses.",
            f"T1 {target_levels[0][0]:,}: sell 1/3-1/2 and move the exit for the rest to breakeven {breakeven:,}.",
            f"T2 {target_levels[1][0]:,}: sell another part; trail the rest 2.5x ATR (~{2.5 * atr:,.0f}) below the highest close.",
            f"T3 {target_levels[2][0]:,}: final target - sell into strength.",
        ],
    }


def holding_alerts(row: dict) -> dict:
    alerts = {}
    code, price = row["code"], row["price"]
    if price < row["exit_level"]:
        alerts[f"{code}_exit"] = f"{code} EXIT: closed {price:,.0f} below the exit level {row['exit_level']:,} ({row['exit_basis']})."
    elif price - row["exit_level"] < 0.5 * row["atr"]:
        alerts[f"{code}_near_exit"] = f"{code} near exit: {price:,.0f}, exit level {row['exit_level']:,} is only {price - row['exit_level']:,.0f} away."
    for index, (level, name) in enumerate(row["targets"], start=1):
        if row["high"] >= level:
            alerts[f"{code}_t{index}"] = f"{code} reached T{index} {level:,} ({name}). Plan: take partial profit; exit for the rest at breakeven {row['breakeven']:,}."
    if row["volume_ratio"] and row["volume_ratio"] > 2 and (row["change_1d"] or 0) < -3:
        alerts[f"{code}_heavy_selling"] = f"{code} heavy selling: {row['change_1d']:+.1f}% on {row['volume_ratio']:.1f}x average volume."
    if row["flow"]["label"].startswith("Distribution") and row["flow"]["cmf_prev"] >= 0:
        alerts[f"{code}_flow_distribution"] = f"{code} volume flow turned to distribution (CMF {row['flow']['cmf']:+.2f})."
    summary = (row["broksum"] or {}).get("summary")
    if summary and summary["class"] == "red" and summary["distribution_days"] >= 3:
        alerts[f"{code}_broksum_distribution"] = f"{code} broksum: institutions distributing {summary['distribution_days']} of the last {summary['days']} entries."
    return alerts


def new_alerts(summary: dict) -> list:
    """Alerts not yet sent today (dedup per trade date)."""
    state = json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8")) if ALERT_STATE_FILE.exists() else {}
    fresh = []
    for row in summary.get("holdings", []):
        sent = set(state.get(row["code"], {}).get(row["date"], []))
        for key, message in row.get("alerts", {}).items():
            if key not in sent:
                fresh.append(message)
                sent.add(key)
        state[row["code"]] = {row["date"]: sorted(sent)}
    ALERT_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return fresh


def draw_chart(code, prices, ma20, ma50, avg, exit_level, targets, broksum, sessions=60):
    view = prices.iloc[-sessions:]
    figure, axis = plt.subplots(figsize=(9, 4.2))
    axis.plot(view.index, view["Close"], color="#12355b", linewidth=1.6, label=code)
    axis.plot(view.index, prices["Close"].rolling(20).mean().iloc[-sessions:], color="#d69e2e", linewidth=1, label="MA20")
    axis.plot(view.index, prices["Close"].rolling(50).mean().iloc[-sessions:], color="#2f855a", linewidth=1, label="MA50")
    lines = [(avg, f"Your avg {avg:,.0f}", "#12355b", "-.")]
    lines.append((exit_level, f"Exit {exit_level:,}", "#c53030", "-"))
    for index, (level, _) in enumerate(targets, start=1):
        lines.append((level, f"T{index} {level:,}", "#2f855a", "--"))
    summary = (broksum or {}).get("summary")
    if summary and summary.get("buyer_avg"):
        lines.append((summary["buyer_avg"], f"Broksum buyers avg {summary['buyer_avg']:,.0f}", "#6b46c1", ":"))
    for level, label, color, style in lines:
        axis.axhline(level, color=color, linestyle=style, linewidth=0.9, alpha=0.85)
        axis.text(view.index[1], level, f" {label}", fontsize=8, color=color, va="bottom")
    # Zoom on the recent range and the plan levels (old crashes would squash the chart)
    plan_levels = [level for level, *_ in lines]
    low_bound = min(view["Low"].min(), *plan_levels)
    high_bound = max(view["High"].max(), *plan_levels)
    padding = (high_bound - low_bound) * 0.06
    axis.set_ylim(low_bound - padding, high_bound + padding)
    axis.set_title(f"{code} - last {view.index[-1]:%d %b %Y}: {view['Close'].iloc[-1]:,.0f}")
    axis.legend(loc="upper right", fontsize=8)
    axis.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(FOLDER / f"portfolio_{code}.png", dpi=105)
    plt.close(figure)


def build() -> dict:
    data = load_portfolio()
    max_loss = float(data.get("max_loss_pct", 8))

    def run(holding):
        try:
            row = analyse_holding(holding, max_loss)
            row["alerts"] = holding_alerts(row)
            return row
        except Exception as error:
            return {"code": holding["code"].upper(), "error": repr(error)[:150]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(run, data.get("holdings", [])))
    summary = {
        "generated": datetime.now(JAKARTA).strftime("%Y-%m-%d %H:%M"),
        "max_loss_pct": max_loss,
        "holdings": rows,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return summary


def load_summary() -> dict:
    if SUMMARY_FILE.exists():
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    return {}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = build()
    for row in result["holdings"]:
        if "error" in row:
            print(row["code"], "ERROR", row["error"])
            continue
        print(f"{row['code']}: {row['price']:,.0f} ({row['change_1d']:+.1f}%) avg {row['avg']:,.0f} P/L {row['pnl_pct']:+.1f}% | {row['verdict']}")
        print(f"  ATR {row['atr']:,.0f} ({row['atr_pct']:.1f}%) RSI {row['rsi']:.0f} MA20 {row['ma20']:,.0f} MA50 {row['ma50']:,.0f}")
        print(f"  supports: {row['supports']}")
        print(f"  resistances: {row['resistances']}")
        print(f"  EXIT {row['exit_level']:,} ({row['exit_basis']}, {row['exit_from_avg']:+.1f}% from avg) | last line {row['last_line']:,} | max-loss level {row['max_loss_level']:,}")
        print(f"  targets: {row['targets']}")
        print(f"  flow: {row['flow']['label']} CMF {row['flow']['cmf']:+.2f} up/down {row['flow']['up_down_ratio']}")
        for line in row["plan"]:
            print("   -", line)
        for note in row["notes"]:
            print("   !", note)
        print("  alerts:", row["alerts"])

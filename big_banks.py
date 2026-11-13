"""
Big 4 Indonesian banks — daily technical snapshot (BBCA, BBRI, BMRI, BBNI).

Used by the after-close report (HTML section), the morning brief and the recap deck.
Run manually:  python big_banks.py
"""

import base64
import html
import json
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import bmri_daily_check as core

BANKS = [
    ("BBCA", "Bank Central Asia"),
    ("BBRI", "Bank Rakyat Indonesia"),
    ("BMRI", "Bank Mandiri"),
    ("BBNI", "Bank Negara Indonesia"),
]
INDEX_SYMBOL = "^JKSE"
PRE_SHOCK_DATE = pd.Timestamp("2026-10-15")   # last close before the MSCI shock
SHOCK_DATE = pd.Timestamp("2026-10-16")

# Entry alerts (BMRI excluded: it is your open position with its own plan)
ENTRY_ALERT_CODES = ("BBCA", "BBRI", "BBNI")
MIN_REWARD_RISK = 1.5        # only alert when reward/risk from the actual close is at least this
PULLBACK_ZONE = (0.382, 0.5)  # retracement of the post-shock rebound

FOLDER = core.FOLDER
DATA_DIR = FOLDER / "data"
SUMMARY_FILE = FOLDER / "big_banks_summary.json"
RELATIVE_CHART = FOLDER / "big_banks_relative.png"
GRID_CHART = FOLDER / "big_banks_grid.png"
COLORS = {
    "BBCA": "#2F6FD6",
    "BBRI": "#E09F1F",
    "BMRI": "#0E9F6E",
    "BBNI": "#D64545",
    "IHSG": "#52606D",
}


# ---------------------------------------------------------------- data
def fetch_daily(symbol: str) -> pd.DataFrame:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        "?range=2y&interval=1d"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    dates = (
        pd.to_datetime(result["timestamp"], unit="s", utc=True)
        .tz_convert("Asia/Jakarta")
        .normalize()
        .tz_localize(None)
    )
    prices = pd.DataFrame(
        {
            "Open": quote["open"],
            "High": quote["high"],
            "Low": quote["low"],
            "Close": quote["close"],
            "Volume": quote["volume"],
        },
        index=dates,
    ).dropna()
    prices.index.name = "Date"
    # Yahoo adds the latest daily bar late for some symbols (seen on ^JKSE): if the live
    # quote is from a newer trading day, append it (volume 0 = treated as "not final")
    meta = result.get("meta", {})
    live_price = meta.get("regularMarketPrice")
    if live_price and meta.get("regularMarketTime") and len(prices):
        exchange_tz = timezone(timedelta(seconds=meta.get("gmtoffset", 0)))
        live_day = pd.Timestamp(datetime.fromtimestamp(meta["regularMarketTime"], exchange_tz).date())
        if live_day > prices.index[-1]:
            prices.loc[live_day] = [
                float(prices["Close"].iloc[-1]),
                float(meta.get("regularMarketDayHigh") or live_price),
                float(meta.get("regularMarketDayLow") or live_price),
                float(live_price),
                0.0,
            ]
    return prices[~prices.index.duplicated(keep="last")]


def round_to_tick(price: float) -> int:
    """Round to the IDX tick for the price band."""
    if price < 200:
        tick = 1
    elif price < 500:
        tick = 2
    elif price < 2000:
        tick = 5
    elif price < 5000:
        tick = 10
    else:
        tick = 25
    return int(round(price / tick) * tick)


def pct(value, base):
    if value is None or base in (None, 0):
        return None
    return (value / base - 1) * 100


# ---------------------------------------------------------------- entry setups
def entry_plan(code: str, prices: pd.DataFrame) -> dict:
    """Pullback and breakout entry setups derived from the post-shock structure.

    prices must already contain indicators (core.add_indicators).
    """
    last = prices.iloc[-1]
    previous = prices.iloc[-2]
    close = float(last["Close"])
    atr = float(last["ATR14"])
    # Structure (shock low, rebound high) comes from data up to the PREVIOUS session,
    # so today's bar can be tested against it instead of moving the levels.
    history = prices.iloc[:-1]
    after_shock = history.loc[SHOCK_DATE:]
    pre_shock = history.loc[:PRE_SHOCK_DATE]
    if len(after_shock) < 2 or pre_shock.empty:
        return {"available": False, "reason": "Not enough post-shock data"}

    low_date = after_shock["Low"].idxmin()
    shock_low = float(after_shock["Low"].min())
    rebound_high = float(history.loc[low_date:]["High"].max())
    pre_close = float(pre_shock["Close"].iloc[-1])
    swing = rebound_high - shock_low
    candle = core.detect_candle(prices)
    reversal_candle = candle.startswith(("Hammer", "Bullish engulfing")) or (
        close > last["Open"] and close >= last["Low"] + 0.6 * (last["High"] - last["Low"])
    )

    plan = {
        "available": True,
        "close": close,
        "atr": round(atr),
        "shock_low": round_to_tick(shock_low),
        "rebound_high": round_to_tick(rebound_high),
        "pre_shock_close": round_to_tick(pre_close),
        "triggers": [],          # (alert_key, message) that pass the R:R filter
    }

    # ---- invalidation
    if close < shock_low:
        plan["pullback"] = {"status": "Invalid: closed below the post-shock low; no long setups"}
        plan["breakout"] = {"status": "Invalid: below the post-shock low"}
        return plan

    # ---- pullback (higher-low) setup
    if swing < 2 * atr:
        plan["pullback"] = {"status": "Not formed: rebound too small to define a pullback zone"}
    else:
        zone_top = round_to_tick(rebound_high - PULLBACK_ZONE[0] * swing)
        zone_bottom = round_to_tick(rebound_high - PULLBACK_ZONE[1] * swing)
        stop = round_to_tick(zone_bottom - 0.5 * atr)
        target_1 = round_to_tick(rebound_high)                    # sell ~half here
        target = round_to_tick(max(rebound_high, pre_close))      # T2: gap fill / pre-shock close
        planned_rr = (target - zone_top) / max(zone_top - stop, 1)
        pullback = {
            "zone": [zone_bottom, zone_top],
            "stop": stop,
            "target_1": target_1,
            "target": target,
            "planned_rr": round(planned_rr, 1),
        }
        held_zone = last["Low"] <= zone_top and close >= zone_bottom and last["Low"] > shock_low
        if held_zone and reversal_candle:
            risk_now = max(close - stop, 1)
            rr_now = (target - close) / risk_now
            rr_t1 = (target_1 - close) / risk_now
            pullback["rr_now"] = round(rr_now, 1)
            if rr_now >= MIN_REWARD_RISK and rr_t1 >= 1.0:
                pullback["status"] = (
                    f"TRIGGERED: held the zone with a reversal ({rr_t1:.1f}R to T1, {rr_now:.1f}R to T2)"
                )
                plan["triggers"].append(
                    (
                        f"{code}_pullback_entry",
                        f"{code} ENTRY (pullback): held {zone_bottom:,}–{zone_top:,} with a reversal candle. "
                        f"Entry ~{close:,.0f}, stop {stop:,} ({pct(stop, close):+.1f}%). "
                        f"T1 {target_1:,} ({pct(target_1, close):+.1f}%, sell ~half), "
                        f"T2 {target:,} ({pct(target, close):+.1f}%) → {rr_t1:.1f}R / {rr_now:.1f}R. "
                        f"Risk per lot Rp{(close - stop) * 100:,.0f}.",
                    )
                )
            else:
                pullback["status"] = (
                    f"Trigger, but R:R only {rr_t1:.1f} (T1) / {rr_now:.1f} (T2) from close: "
                    "skip or wait for a lower entry"
                )
        elif held_zone:
            pullback["status"] = "In the zone: wait for a reversal candle (hammer / engulfing / strong close)"
        elif zone_top < close <= zone_top + atr:
            pullback["status"] = f"Approaching the zone (within 1 ATR): get ready"
        elif close > zone_top:
            pullback["status"] = f"Waiting: price {close - zone_top:,.0f} above the zone"
        else:
            pullback["status"] = "Below the zone: wait for a base before buying"
        plan["pullback"] = pullback

    # ---- breakout setup (close above the gap top / rebound high)
    level = round_to_tick(max(pre_close, rebound_high))
    stop = round_to_tick(level - 0.8 * atr)          # back below the reclaimed level = failed breakout
    risk = max(level - stop, 1)
    overhead = sorted(
        round_to_tick(value)
        for value in (
            float(last["MA50"]),
            float(last["MA200"]),
            float(history["High"].iloc[-60:].max()),
        )
        if value >= level + atr
    )
    target_1 = overhead[0] if overhead else round_to_tick(level + 2 * risk)
    target = overhead[1] if len(overhead) > 1 else max(target_1, round_to_tick(level + 2 * risk))
    breakout = {
        "level": level,
        "stop": stop,
        "target_1": target_1,
        "target": target,
        "planned_rr": round((target - level) / risk, 1),
    }
    crossed = close > level and previous["Close"] <= level
    heavy_volume = last["Volume"] > last["Vol20"]
    if crossed and heavy_volume:
        risk_now = max(close - stop, 1)
        rr_now = (target - close) / risk_now
        rr_t1 = (target_1 - close) / risk_now
        breakout["rr_now"] = round(rr_now, 1)
        if rr_now >= MIN_REWARD_RISK and rr_t1 >= 1.0:
            breakout["status"] = (
                f"TRIGGERED: closed above {level:,} on volume ({rr_t1:.1f}R to T1, {rr_now:.1f}R to T2)"
            )
            plan["triggers"].append(
                (
                    f"{code}_breakout_entry",
                    f"{code} ENTRY (breakout): closed {close:,.0f} above {level:,} on "
                    f"{last['Volume'] / last['Vol20']:.1f}x volume. Stop {stop:,} ({pct(stop, close):+.1f}%). "
                    f"T1 {target_1:,} ({pct(target_1, close):+.1f}%, sell ~half), "
                    f"T2 {target:,} ({pct(target, close):+.1f}%) → {rr_t1:.1f}R / {rr_now:.1f}R. "
                    f"Risk per lot Rp{(close - stop) * 100:,.0f}.",
                )
            )
        else:
            breakout["status"] = (
                f"Breakout, but R:R only {rr_t1:.1f} (T1) / {rr_now:.1f} (T2) from close: "
                f"wait for a retest of {level:,}"
            )
    elif crossed:
        breakout["status"] = "Unconfirmed: closed above the level on light volume"
    elif close > level:
        breakout["status"] = f"Already above {level:,}: buy a retest, don't chase"
    elif level - atr <= close <= level:
        breakout["status"] = "Approaching the breakout level (within 1 ATR)"
    else:
        breakout["status"] = f"Waiting: {level - close:,.0f} below the level"
    plan["breakout"] = breakout
    return plan


def entry_alerts(results: list) -> dict:
    """{alert_key: message} for entry triggers of the alert-enabled banks."""
    alerts = {}
    for result in results:
        plan = result.get("entry") or {}
        if result["code"] in ENTRY_ALERT_CODES:
            for key, message in plan.get("triggers", []):
                alerts[key] = message
    return alerts


# ---------------------------------------------------------------- direction outlook
def latest_broksum(code: str, date_text: str):
    """Broksum is only collected for BMRI (your position). Returns (signal, net_foreign, 3-day signals)."""
    if code != "BMRI" or not core.BROKSUM_FILE.exists():
        return None, None, []
    history = pd.read_csv(core.BROKSUM_FILE).sort_values("Date")
    recent = history.tail(3)["Signal"].astype(str).tolist()
    today = history[history["Date"] == date_text]
    if today.empty:
        return None, None, recent
    row = today.iloc[-1]
    net_foreign = None if pd.isna(row["NetForeignBn"]) else float(row["NetForeignBn"])
    return str(row["Signal"]), net_foreign, recent


def direction_outlook(code: str, prices: pd.DataFrame, plan: dict, ihsg_5d: float,
                      extra_factors: list = None) -> dict:
    """Rule-based lean: is the post-shock rebound continuing, stalling, or retracing?

    Combines momentum, volume flow, relative strength, location in the rebound range and,
    for BMRI, the broksum you entered. A lean, not a prediction.
    """
    last = prices.iloc[-1]
    close = float(last["Close"])
    score = 0
    reasons = []

    rsi = float(last["RSI14"])
    rsi_before = float(prices["RSI14"].iloc[-4])
    if rsi > rsi_before and rsi >= 50:
        score += 1
        reasons.append(f"RSI {rsi:.0f} rising above 50")
    elif rsi < rsi_before and rsi < 50:
        score -= 1
        reasons.append(f"RSI {rsi:.0f} falling below 50")

    hist = float(last["Hist"])
    hist_before = float(prices["Hist"].iloc[-2])
    if hist > hist_before:
        score += 1
        reasons.append("MACD momentum improving")
    elif hist < hist_before:
        score -= 1
        reasons.append("MACD momentum fading")

    recent = prices.iloc[-10:]
    signed_volume = np.sign(recent["Close"].diff().fillna(0)) * recent["Volume"]
    volume_flow = float(signed_volume.sum() / recent["Volume"].sum())
    if volume_flow > 0.15:
        score += 1
        reasons.append("more volume on up-days (accumulation)")
    elif volume_flow < -0.15:
        score -= 1
        reasons.append("more volume on down-days (distribution)")

    change_5d = (close / float(prices["Close"].iloc[-6]) - 1) * 100
    relative = change_5d - ihsg_5d
    if code == "IHSG":
        relative = 0.0          # the index is the benchmark itself
    if relative > 2:
        score += 1
        reasons.append(f"outperforming IHSG by {relative:.1f}% (5D)")
    elif relative < -2:
        score -= 1
        reasons.append(f"underperforming IHSG by {abs(relative):.1f}% (5D)")

    position = None
    retrace_to = None
    rebound_to = None
    if plan.get("available"):
        shock_low = plan["shock_low"]
        rebound_high = plan["rebound_high"]
        span = max(rebound_high - shock_low, 1)
        position = (close - shock_low) / span * 100
        zone = (plan.get("pullback") or {}).get("zone")
        retrace_to = f"{zone[0]:,}–{zone[1]:,}" if zone else f"{shock_low:,}"
        rebound_to = f"{rebound_high:,} → {max(rebound_high, plan['pre_shock_close']):,}"
        if close > rebound_high:
            score += 2
            reasons.append(f"broke above the rebound high {rebound_high:,}")
        elif position >= 85:
            score -= 1
            reasons.append(f"stalling just under the rebound high {rebound_high:,}")
        if zone and close < zone[0]:
            score -= 2
            reasons.append(f"lost the pullback zone {zone[0]:,}–{zone[1]:,}")
        if close < shock_low:
            score -= 2
            reasons.append("below the post-shock low")

    signal, net_foreign, recent_signals = latest_broksum(code, prices.index[-1].strftime("%Y-%m-%d"))
    if signal:
        if signal.startswith("ACCUMULATION"):
            score += 1
            reasons.append("broksum: institutional accumulation")
        elif signal.startswith("DISTRIBUTION"):
            score -= 1
            reasons.append("broksum: institutional distribution")
        if len(recent_signals) == 3 and all(s.startswith("ACCUMULATION") for s in recent_signals):
            score += 1
            reasons.append("3 days of accumulation")
    if net_foreign is not None:
        if net_foreign > 0:
            score += 1
            reasons.append(f"foreign net buy Rp{net_foreign:,.0f}bn")
        elif net_foreign < 0:
            score -= 1
            reasons.append(f"foreign net sell Rp{abs(net_foreign):,.0f}bn")

    for delta, reason in extra_factors or []:
        score += delta
        reasons.append(reason)

    if score >= 2:
        label = "REBOUND continuing"
        expectation = f"next targets {rebound_to}" if rebound_to else "upside"
    elif score == 1:
        label = "Sideways, leaning rebound"
        expectation = f"needs a close above {plan.get('rebound_high', 0):,} to confirm" if plan.get("available") else ""
    elif score == 0:
        label = "Sideways / undecided"
        expectation = f"range {retrace_to} to {plan.get('rebound_high', 0):,}" if plan.get("available") else ""
    elif score == -1:
        label = "Sideways, leaning retrace"
        expectation = f"a dip toward {retrace_to} is likely first" if retrace_to else ""
    else:
        label = "RETRACE likely"
        expectation = f"toward {retrace_to}" if retrace_to else "lower"

    zone = (plan.get("pullback") or {}).get("zone") if plan.get("available") else None
    if zone and zone[0] <= close <= zone[1] and score <= 0:
        expectation = (
            f"testing the pullback zone {zone[0]:,}–{zone[1]:,} now: holding it = higher low; "
            f"a close below {zone[0]:,} opens {plan['shock_low']:,}"
        )
    elif zone and close < zone[0] and score <= 0:
        # Already below the pullback zone: targets must point lower, never back up to the zone
        shock_low = plan.get("shock_low")
        if shock_low and close > shock_low:
            expectation = (
                f"below the pullback zone (now resistance {zone[0]:,}–{zone[1]:,}); "
                f"next support is the shock low {shock_low:,}"
            )
        elif shock_low:
            expectation = (
                f"broke the shock low {shock_low:,}: the rebound has failed - wait for a new base; "
                f"{shock_low:,} and {zone[0]:,}–{zone[1]:,} are now resistance"
            )

    return {
        "score": score,
        "label": label,
        "confidence": "medium" if abs(score) >= 3 else "low",
        "expectation": expectation,
        "reasons": reasons,
        "volume_flow": round(volume_flow, 2),
        "relative_5d": round(relative, 1),
        "position_pct": None if position is None else round(position),
        "broksum_used": bool(signal or net_foreign is not None),
    }


def sector_outlook(results: list) -> dict:
    scores = [r["outlook"]["score"] for r in results if r.get("outlook")]
    if not scores:
        return {}
    average = sum(scores) / len(scores)
    if average >= 1.5:
        label = "Big 4 REBOUND continuing"
    elif average >= 0.5:
        label = "Big 4 sideways, leaning rebound"
    elif average > -0.5:
        label = "Big 4 sideways / undecided"
    elif average > -1.5:
        label = "Big 4 sideways, leaning retrace"
    else:
        label = "Big 4 RETRACE likely"
    return {"score": round(average, 2), "label": label}


# ---------------------------------------------------------------- analysis
def analyse_bank(code: str, name: str, prices: pd.DataFrame, ihsg_5d: float = 0.0) -> dict:
    prices = core.add_indicators(prices)
    ta = core.technical_analysis(prices)          # trend / momentum / volume / candle / bias
    entry = entry_plan(code, prices)
    outlook = direction_outlook(code, prices, entry, ihsg_5d)
    last = prices.iloc[-1]
    previous = prices.iloc[-2]
    close = float(last["Close"])

    pre_shock = prices.loc[:PRE_SHOCK_DATE]
    pre_close = float(pre_shock["Close"].iloc[-1]) if len(pre_shock) else None
    after_shock = prices.loc[SHOCK_DATE:]
    shock_low = float(after_shock["Low"].min()) if len(after_shock) else None

    levels = {
        "MA20": float(last["MA20"]),
        "MA50": float(last["MA50"]),
        "MA200": float(last["MA200"]),
        "20-day high": float(prices["High"].iloc[-20:].max()),
        "20-day low": float(prices["Low"].iloc[-20:].min()),
    }
    if pre_close:
        levels["Pre-shock close"] = pre_close
    if shock_low:
        levels["Post-shock low"] = shock_low
    supports = sorted(
        [(round_to_tick(v), n) for n, v in levels.items() if v < close], reverse=True
    )[:3]
    resistances = sorted(
        [(round_to_tick(v), n) for n, v in levels.items() if v > close]
    )[:3]

    signals = []
    if close > last["MA20"] and previous["Close"] <= previous["MA20"]:
        signals.append("Reclaimed MA20")
    if close < last["MA20"] and previous["Close"] >= previous["MA20"]:
        signals.append("Lost MA20")
    if last["RSI14"] >= 50 > previous["RSI14"]:
        signals.append("RSI crossed above 50")
    if last["RSI14"] < 50 <= previous["RSI14"]:
        signals.append("RSI fell below 50")
    if last["Hist"] > 0 >= previous["Hist"]:
        signals.append("MACD bullish cross")
    if last["Hist"] < 0 <= previous["Hist"]:
        signals.append("MACD bearish cross")
    prior_20 = prices.iloc[-21:-1]
    heavy_volume = last["Volume"] > last["Vol20"]
    if close > prior_20["High"].max():
        signals.append("20-day breakout" + (" on volume" if heavy_volume else " (light volume)"))
    if close < prior_20["Low"].min():
        signals.append("20-day breakdown" + (" on volume" if heavy_volume else " (light volume)"))

    watch_parts = []
    if resistances:
        watch_parts.append(f"close above {resistances[0][0]:,} ({resistances[0][1]}) = strength")
    if supports:
        watch_parts.append(f"below {supports[0][0]:,} ({supports[0][1]}) = weakness")

    return {
        "code": code,
        "name": name,
        "date": prices.index[-1].strftime("%Y-%m-%d"),
        "close": close,
        "change_1d": pct(close, float(previous["Close"])),
        "change_5d": pct(close, float(prices["Close"].iloc[-6])),
        "vs_pre_shock": pct(close, pre_close),
        "from_low": pct(close, shock_low),
        "vs_ma200": pct(close, float(last["MA200"])),
        "rsi": float(last["RSI14"]),
        "volume_ratio": float(last["Volume"] / last["Vol20"]) if last["Vol20"] else None,
        "bias": ta["bias"],
        "score": ta["score"],
        "trend": ta["trend"],
        "momentum": ta["momentum"],
        "volume": ta["volume"],
        "candle": ta["candle"],
        "supports": supports,
        "resistances": resistances,
        "signals": signals,
        "watch": "; ".join(watch_parts),
        "pre_shock_close": pre_close,
        "shock_low": shock_low,
        "entry": entry,
        "outlook": outlook,
    }


# ---------------------------------------------------------------- charts
def draw_relative_chart(price_map: dict, sessions: int = 60) -> None:
    fig, ax = plt.subplots(figsize=(12, 2.9))
    for code, prices in price_map.items():
        recent = prices["Close"].iloc[-sessions:]
        rebased = recent / recent.iloc[0] * 100
        style = "--" if code == "IHSG" else "-"
        width = 1.4 if code == "IHSG" else 2.0
        ax.plot(rebased.index, rebased, style, color=COLORS[code], linewidth=width,
                label=f"{code} {rebased.iloc[-1] - 100:+.1f}%")
    ax.axvline(SHOCK_DATE, color="#999999", linewidth=1, linestyle=":")
    ax.text(SHOCK_DATE, ax.get_ylim()[1], " MSCI shock", fontsize=8, color="#666666", va="top")
    ax.axhline(100, color="#cccccc", linewidth=0.8)
    ax.set_ylabel(f"Rebased = 100\n({sessions} sessions)")
    ax.grid(alpha=0.25)
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=9)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(RELATIVE_CHART, dpi=120)
    plt.close(fig)


def draw_grid_chart(price_map: dict, results: list, sessions: int = 120) -> None:
    fig, axes = plt.subplots(2, 2, figsize=(8, 5.6))
    for ax, result in zip(axes.flat, results):
        prices = core.add_indicators(price_map[result["code"]].copy()).iloc[-sessions:]
        ax.plot(prices.index, prices["Close"], color=COLORS[result["code"]], linewidth=1.6)
        for column, color in (("MA20", "orange"), ("MA50", "royalblue"), ("MA200", "purple")):
            ax.plot(prices.index, prices[column], color=color, linewidth=0.9, alpha=0.8)
        ax.axvline(SHOCK_DATE, color="#999999", linewidth=0.8, linestyle=":")
        ax.set_title(
            f"{result['code']}  {result['close']:,.0f}  ({result['change_1d']:+.1f}%)  ·  {result['bias']}",
            fontsize=9,
        )
        ax.tick_params(labelsize=7)
        ax.xaxis.set_major_locator(plt.MaxNLocator(4))
        ax.grid(alpha=0.25)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.text(0.5, 0.005, "Close (colour) · MA20 orange · MA50 blue · MA200 purple · dotted = 16 Oct shock",
             ha="center", fontsize=7, color="#666666")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(GRID_CHART, dpi=130)
    plt.close(fig)


# ---------------------------------------------------------------- run
def run() -> list:
    symbols = [f"{code}.JK" for code, _ in BANKS] + [INDEX_SYMBOL]
    with ThreadPoolExecutor(max_workers=5) as pool:
        frames = dict(zip(symbols, pool.map(fetch_daily, symbols)))
    DATA_DIR.mkdir(exist_ok=True)
    ihsg_close = frames[INDEX_SYMBOL]["Close"]
    ihsg_5d = (float(ihsg_close.iloc[-1]) / float(ihsg_close.iloc[-6]) - 1) * 100
    price_map = {}
    results = []
    for code, name in BANKS:
        prices = frames[f"{code}.JK"]
        prices.to_csv(DATA_DIR / f"{code}_daily.csv")
        price_map[code] = prices
        results.append(analyse_bank(code, name, prices.copy(), ihsg_5d))
    price_map["IHSG"] = frames[INDEX_SYMBOL]

    draw_relative_chart(price_map)
    draw_grid_chart(price_map, results)
    ihsg = frames[INDEX_SYMBOL]["Close"]
    summary = {
        "date": results[0]["date"],
        "ihsg_close": float(ihsg.iloc[-1]),
        "ihsg_change_1d": pct(float(ihsg.iloc[-1]), float(ihsg.iloc[-2])),
        "ihsg_change_5d": ihsg_5d,
        "sector": sector_outlook(results),
        "banks": results,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return results


def load_summary() -> dict:
    if SUMMARY_FILE.exists():
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------- HTML
def fmt_pct(value) -> str:
    return "–" if value is None else f"{value:+.1f}%"


def html_table(results: list) -> str:
    header = (
        "<table><tr><th>Bank</th><th>Close</th><th>1D</th><th>5D</th><th>vs 15 Oct</th>"
        "<th>From shock low</th><th>vs MA200</th><th>RSI</th><th>Bias</th><th>Signal</th></tr>"
    )
    rows = []
    for r in results:
        cells = [
            f"<td><b>{r['code']}</b></td>",
            f"<td>{r['close']:,.0f}</td>",
        ]
        for key in ("change_1d", "change_5d", "vs_pre_shock", "from_low", "vs_ma200"):
            cells.append(f"<td class='{core.signed_class(r[key])}'>{fmt_pct(r[key])}</td>")
        cells.append(f"<td>{r['rsi']:.0f}</td>")
        cells.append(f"<td>{html.escape(r['bias'])}</td>")
        cells.append(f"<td>{html.escape(', '.join(r['signals']) or 'None')}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return header + "".join(rows) + "</table>"


def embedded_image(path, alt: str) -> str:
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode()
    return f"<img src='data:image/png;base64,{encoded}' alt='{alt}' style='margin-top:10px'>"


def outlook_html(results: list) -> str:
    sector = sector_outlook(results)
    rows = "".join(
        f"<tr><td><b>{r['code']}</b></td><td><b>{html.escape(r['outlook']['label'])}</b>"
        f"<br><span class='muted'>{html.escape(r['outlook']['expectation'])}</span></td>"
        f"<td>{r['outlook']['score']:+d} ({r['outlook']['confidence']})</td>"
        f"<td>{html.escape('; '.join(r['outlook']['reasons']) or 'mixed signals')}</td></tr>"
        for r in results
    )
    return (
        f"<p class='big'>{html.escape(sector.get('label', ''))}</p>"
        "<table><tr><th>Bank</th><th>Direction lean</th><th>Score</th><th>Why</th></tr>"
        + rows
        + "</table><p class='muted'>Lean for the next few sessions from momentum, volume flow, "
        "relative strength, position in the post-shock range and (BMRI only) your broksum. "
        "Confidence is never high; confirm with the levels.</p>"
    )


def html_section(results: list) -> str:
    details = "".join(
        f"<li><b>{r['code']}</b> ({html.escape(r['bias'])}): {html.escape(r['trend'])}. "
        f"<br><span class='muted'>Watch: {html.escape(r['watch'])}</span></li>"
        for r in results
    )
    return (
        "<p><b>Direction outlook</b></p>"
        + outlook_html(results)
        + html_table(results)
        + "<p><b>Entry setups (alerts for " + ", ".join(ENTRY_ALERT_CODES) + ")</b></p>"
        + entry_table_html(results)
        + embedded_image(RELATIVE_CHART, "relative performance")
        + embedded_image(GRID_CHART, "big banks grid")
        + f"<ul>{details}</ul>"
        + "<p class='muted'>Entry alerts fire once per trigger and only at ≥ "
        f"{MIN_REWARD_RISK}R from the close. BMRI is managed by your position plan.</p>"
    )


def entry_rows(results: list) -> list:
    """Flat rows for tables: one row per bank per setup."""
    rows = []
    for result in results:
        plan = result.get("entry") or {}
        if result["code"] not in ENTRY_ALERT_CODES or not plan.get("available"):
            continue
        pullback = plan.get("pullback", {})
        breakout = plan.get("breakout", {})
        if "zone" in pullback:
            rows.append({
                "code": result["code"],
                "setup": "Pullback",
                "trigger": f"Hold {pullback['zone'][0]:,}–{pullback['zone'][1]:,} + reversal",
                "stop": pullback["stop"],
                "target": pullback["target"],
                "rr": pullback.get("rr_now", pullback["planned_rr"]),
                "status": pullback["status"],
            })
        else:
            rows.append({"code": result["code"], "setup": "Pullback", "trigger": "–", "stop": None,
                         "target": None, "rr": None, "status": pullback.get("status", "–")})
        if "level" in breakout:
            rows.append({
                "code": result["code"],
                "setup": "Breakout",
                "trigger": f"Close > {breakout['level']:,} + volume",
                "stop": breakout["stop"],
                "target": breakout["target"],
                "rr": breakout.get("rr_now", breakout["planned_rr"]),
                "status": breakout["status"],
            })
        else:
            rows.append({"code": result["code"], "setup": "Breakout", "trigger": "–", "stop": None,
                         "target": None, "rr": None, "status": breakout.get("status", "–")})
    return rows


def entry_table_html(results: list) -> str:
    rows = entry_rows(results)
    if not rows:
        return "<p class='muted'>No entry setups available.</p>"
    lines = []
    for row in rows:
        stop = "–" if row["stop"] is None else f"{row['stop']:,}"
        target = "–" if row["target"] is None else f"{row['target']:,}"
        rr = "–" if row["rr"] is None else f"{row['rr']:.1f}R"
        css = " class='pos'" if row["status"].startswith("TRIGGERED") else ""
        lines.append(
            f"<tr><td><b>{row['code']}</b></td><td>{row['setup']}</td>"
            f"<td>{html.escape(row['trigger'])}</td><td>{stop}</td><td>{target}</td><td>{rr}</td>"
            f"<td{css}>{html.escape(row['status'])}</td></tr>"
        )
    return (
        "<table><tr><th>Bank</th><th>Setup</th><th>Trigger</th><th>Stop</th><th>Target</th>"
        "<th>R:R</th><th>Status</th></tr>" + "".join(lines) + "</table>"
    )


if __name__ == "__main__":
    import sys

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    for item in run():
        print(
            f"{item['code']}: {item['close']:,.0f} ({fmt_pct(item['change_1d'])}) "
            f"bias {item['bias']} | signals: {', '.join(item['signals']) or 'none'} | {item['watch']}"
        )

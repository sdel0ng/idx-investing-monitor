"""
Daily macro-driven stock ideas for IDX.

1. Each theme in macro_themes.json links a macro driver (gold, copper, oil, soybean oil, Hang Seng,
   USD/IDR, US 10y, VIX) to the IDX stocks that usually benefit.
2. Theme strength (0-5) = how clearly the driver is trending in the theme's direction
   (vs MA20/MA50, 5-day and 1-month move compared with its normal volatility).
3. Every stock in an active theme is screened: liquidity, correlation with the driver
   (same day or next day, 120 sessions), trend, RSI, relative strength vs IHSG.
4. Top ideas get a simple swing plan: entry zone, stop (2x ATR / recent low), targets at 1.5R and 3R.
5. Ideas are logged to ideas_history.csv so the app can show how past ideas performed.

Writes macro_ideas.json. Run manually:  python macro_ideas.py
Education only - these are research candidates, not buy signals.
"""

import csv
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import pandas as pd

import dividend_watchlist as engine
import market_context

FOLDER = engine.FOLDER
JAKARTA = engine.JAKARTA
THEMES_FILE = FOLDER / "macro_themes.json"
SUMMARY_FILE = FOLDER / "macro_ideas.json"
HISTORY_FILE = FOLDER / "ideas_history.csv"
MAX_IDEAS = 6
MAX_PER_THEME = 2
MAX_EARLY = 8
# Themes that act slowly through valuations/flows: daily correlation with the driver is naturally weak
MACRO_THEMES = {"yields_down", "rupiah_strong", "rupiah_weak", "risk_off", "oil_down"}


def min_correlation(theme_id: str) -> float:
    return 0.0 if theme_id in MACRO_THEMES else 0.15


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


def rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    value = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).iloc[-1]
    return 50.0 if math.isnan(value) else float(value)


def load_themes() -> dict:
    return json.loads(THEMES_FILE.read_text(encoding="utf-8"))


def fetch(symbol: str):
    try:
        prices, _ = engine.fetch_history(symbol, years=1)
        prices = market_context.clean_bad_ticks(prices)
        return symbol, prices
    except Exception as error:
        return symbol, error


# ---------------------------------------------------------------- themes
def theme_strength(theme: dict, prices: pd.DataFrame, regime_label: str) -> dict:
    close = prices["Close"]
    last = float(close.iloc[-1])
    sign = 1 if theme["direction"] == "up" else -1
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    daily_vol = float(close.pct_change().iloc[-120:].std() * 100) or 1.0
    change_1d = pct(last, float(close.iloc[-2]))
    change_5d = pct(last, float(close.iloc[-6]))
    change_1m = pct(last, float(close.iloc[-22]))
    weekly_vol, monthly_vol = daily_vol * math.sqrt(5), daily_vol * math.sqrt(21)

    points = 0
    reasons = []
    if sign * (last - ma20) > 0:
        points += 1
        reasons.append(f"{'above' if sign > 0 else 'below'} MA20")
    if sign * (last - ma50) > 0:
        points += 1
        reasons.append(f"{'above' if sign > 0 else 'below'} MA50")
    if sign * change_5d > 0.5 * weekly_vol:
        points += 1
        reasons.append(f"5-day move {change_5d:+.1f}%")
    if sign * change_1m > 0.5 * monthly_vol:
        points += 1
        reasons.append(f"1-month move {change_1m:+.1f}%")
    if sign * change_1m > 1.5 * monthly_vol:
        points += 1
        reasons.append("strong trend")
    if theme["id"] == "risk_off" and regime_label in ("Risk-off", "Cautious"):
        points = min(5, points + (2 if regime_label == "Risk-off" else 1))
        reasons.append(f"market regime {regime_label}")

    if points >= 4:
        status, css = "Active - strong", "green"
    elif points == 3:
        status, css = "Active", "green"
    elif points == 2:
        status, css = "Emerging - watch", "amber"
    else:
        status, css = "Inactive", "grey"

    level = ma20 if sign * (last - ma20) <= 0 else ma50
    word = "above" if sign > 0 else "below"
    trigger = (
        f"Turns on with a close {word} MA20 {ma20:,.2f}" if sign * (last - ma20) <= 0
        else f"Weakens on a close {'below' if sign > 0 else 'above'} MA20 {ma20:,.2f}"
    )
    return {
        "id": theme["id"],
        "name": theme["name"],
        "driver": theme["driver"],
        "driver_name": theme["driver_name"],
        "direction": theme["direction"],
        "why": theme["why"],
        "last": last,
        "change_1d": change_1d,
        "change_5d": change_5d,
        "change_1m": change_1m,
        "ma20": ma20,
        "ma50": ma50,
        "strength": points,
        "status": status,
        "status_class": css,
        "reasons": reasons,
        "trigger": trigger,
        "_level": level,
    }


# ---------------------------------------------------------------- stocks
def driver_correlation(stock_returns: pd.Series, driver_returns: pd.Series) -> float:
    """Correlation with the driver on the same day or the next IDX day (US-hours futures)."""
    best = 0.0
    for lag in (0, 1):
        joined = pd.concat([stock_returns, driver_returns.shift(lag)], axis=1, join="inner").dropna().iloc[-120:]
        if len(joined) > 40:
            value = float(joined.iloc[:, 0].corr(joined.iloc[:, 1]))
            if not math.isnan(value) and abs(value) > abs(best):
                best = value
    return best


def screen_stock(code: str, prices: pd.DataFrame, theme: dict, driver_prices: pd.DataFrame,
                 index_prices: pd.DataFrame, min_value_bn: float) -> dict:
    close = prices["Close"]
    high, low = prices["High"].fillna(close), prices["Low"].fillna(close)
    last = float(close.iloc[-1])
    ma20 = float(close.rolling(20).mean().iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma50_prev = float(close.rolling(50).mean().iloc[-11])
    rsi14 = rsi(close)
    true_range = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = float(true_range.rolling(14).mean().iloc[-1])
    avg_value_bn = float((close * prices["Volume"].fillna(0)).iloc[-21:-1].mean() / 1e9)
    volume_ratio = float(prices["Volume"].iloc[-1] / prices["Volume"].iloc[-21:-1].mean()) if prices["Volume"].iloc[-21:-1].mean() else None
    change_5d = pct(last, float(close.iloc[-6]))
    change_1m = pct(last, float(close.iloc[-22]))
    index_close = index_prices["Close"]
    index_1m = pct(float(index_close.iloc[-1]), float(index_close.iloc[-22]))
    relative_1m = (change_1m or 0) - (index_1m or 0)
    sign = 1 if theme["direction"] == "up" else -1
    correlation = driver_correlation(close.pct_change(), driver_prices["Close"].pct_change()) * sign
    high_20 = float(high.iloc[-20:].max())
    high_60 = float(high.iloc[-60:].max())
    low_10 = float(low.iloc[-10:].min())

    if rsi14 >= 72 or last > ma20 * 1.12:
        status, css = "Overextended - wait for a dip", "amber"
    elif last > ma20 > ma50:
        status, css = "Momentum - riding the theme", "green"
    elif last > ma50 and last <= ma20 * 1.01:
        status, css = "Pullback in uptrend - buy zone", "green"
    elif last > ma20 and last <= ma50:
        status, css = "Turning up - needs close above MA50", "amber"
    elif ma50 < ma50_prev:
        status, css = "Downtrend - not following yet", "red"
    else:
        status, css = "Basing below MA50", "amber"

    liquid = avg_value_bn >= min_value_bn
    trend_points = (last > ma20) + (last > ma50) + (ma20 > ma50) + (ma50 > ma50_prev)
    score = (
        theme["strength"] * 8
        + max(-0.2, min(correlation, 0.7)) * 40
        + trend_points * 7
        + max(-15, min(relative_1m, 15)) * 0.8
        - (12 if css == "amber" and status.startswith("Overextended") else 0)
        - (25 if not liquid else 0)
    )

    # Swing plan: entry near MA20 for pullbacks, around the price for momentum
    if status.startswith("Pullback"):
        entry_low, entry_high = max(ma50, last - 0.5 * atr), last
    else:
        entry_low, entry_high = max(ma20, last - 0.8 * atr), last
    entry = (entry_low + entry_high) / 2

    def swing_stop(level: float) -> float:
        """Tighter of 2x ATR and the 10-day low, but always at least 1 ATR below the level."""
        return min(max(level - 2 * atr, low_10 - idx_tick(low_10)), level - atr)

    stop = swing_stop(entry)
    risk = max(entry - stop, idx_tick(entry))
    # Trigger plan for stocks that are not in an uptrend yet: buy only on a close above the trigger
    trigger = to_tick(max(ma20, ma50) * 1.005, "up")
    trigger_stop = to_tick(swing_stop(trigger), "down")
    trigger_risk = max(trigger - trigger_stop, idx_tick(trigger))
    trigger_plan = {
        "trigger": trigger,
        "trigger_label": "MA50" if ma50 >= ma20 else "MA20",
        "distance_pct": (trigger / last - 1) * 100,
        "stop": trigger_stop,
        "target_1": to_tick(trigger + 1.5 * trigger_risk, "up"),
        "target_2": to_tick(trigger + 3 * trigger_risk, "up"),
        "risk_pct": trigger_risk / trigger * 100,
    }
    plan = {
        "entry_low": to_tick(entry_low, "down"),
        "entry_high": to_tick(entry_high, "up"),
        "stop": to_tick(stop, "down"),
        "target_1": to_tick(entry + 1.5 * risk, "up"),
        "target_2": to_tick(entry + 3 * risk, "up"),
        "risk_pct": risk / entry * 100,
        "resistance_60d": to_tick(high_60),
    }

    why = []
    if correlation >= 0.3:
        why.append(f"moves with {theme['driver_name']} (correlation {correlation:.2f})")
    elif correlation < 0.1:
        why.append(f"weak link to {theme['driver_name']} lately (correlation {correlation:.2f})")
    if relative_1m > 5:
        why.append(f"outperforming IHSG by {relative_1m:.0f}% over 1 month")
    elif relative_1m < -5:
        why.append(f"lagging IHSG by {abs(relative_1m):.0f}% over 1 month")
    if volume_ratio and volume_ratio > 1.5:
        why.append(f"volume {volume_ratio:.1f}x average today")
    if not liquid:
        why.append(f"thin liquidity (~Rp{avg_value_bn:.0f}bn/day)")
    if last >= high_20 * 0.99:
        why.append("at a 20-day high (breakout zone)")
    if plan["risk_pct"] > 10 or trigger_plan["risk_pct"] > 10:
        why.append("volatile: stop is >10% away - use a smaller position")

    return {
        "code": code,
        "theme_id": theme["id"],
        "theme": theme["name"],
        "price": last,
        "change_1d": pct(last, float(close.iloc[-2])),
        "change_5d": change_5d,
        "change_1m": change_1m,
        "relative_1m": relative_1m,
        "ma20": round(ma20),
        "ma50": round(ma50),
        "rsi": rsi14,
        "atr_pct": atr / last * 100,
        "avg_value_bn": avg_value_bn,
        "liquid": liquid,
        "correlation": correlation,
        "status": status,
        "status_class": css,
        "score": round(score, 1),
        "plan": plan,
        "trigger_plan": trigger_plan,
        "why": why,
        "date": close.index[-1].strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------- history
def log_ideas(ideas: list, date: str) -> None:
    existing = set()
    if HISTORY_FILE.exists():
        with HISTORY_FILE.open(encoding="utf-8") as handle:
            existing = {(row["date"], row["code"]) for row in csv.DictReader(handle)}
    new_file = not HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new_file:
            writer.writerow(["date", "code", "theme", "price", "stop", "target_1", "score"])
        for idea in ideas:
            if (date, idea["code"]) not in existing:
                writer.writerow([date, idea["code"], idea["theme"], round(idea["price"], 2),
                                 idea["plan"]["stop"], idea["plan"]["target_1"], idea["score"]])


def track_record(prices_by_code: dict, today: str, limit: int = 25) -> list:
    if not HISTORY_FILE.exists():
        return []
    with HISTORY_FILE.open(encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["date"] < today]
    output = []
    for row in rows[-limit:][::-1]:
        prices = prices_by_code.get(row["code"])
        if prices is None or isinstance(prices, Exception):
            continue
        since = prices[prices.index > pd.Timestamp(row["date"])]
        entry = float(row["price"])
        now = float(prices["Close"].iloc[-1])
        hit_stop = bool(len(since) and since["Low"].fillna(since["Close"]).min() <= float(row["stop"]))
        hit_target = bool(len(since) and since["High"].fillna(since["Close"]).max() >= float(row["target_1"]))
        output.append({
            "date": row["date"], "code": row["code"], "theme": row["theme"], "entry": entry, "now": now,
            "return_pct": pct(now, entry), "hit_target": hit_target, "hit_stop": hit_stop,
        })
    return output


# ---------------------------------------------------------------- build
def build() -> dict:
    config = load_themes()
    themes_config = config["themes"]
    min_value_bn = config.get("min_avg_value_bn", 10)
    context = market_context.load_summary()
    regime = (context.get("regime") or {}).get("label", "Neutral")

    drivers = {theme["driver"] for theme in themes_config}
    codes = sorted({code for theme in themes_config for code in theme["stocks"]})
    symbols = list(drivers) + ["^JKSE"] + [f"{code}.JK" for code in codes]
    with ThreadPoolExecutor(max_workers=16) as pool:
        fetched = dict(pool.map(fetch, symbols))

    index_prices = fetched["^JKSE"]
    themes, candidates, errors = [], [], []
    for theme in themes_config:
        driver_prices = fetched.get(theme["driver"])
        if isinstance(driver_prices, Exception):
            errors.append(f"{theme['driver']}: {driver_prices!r}"[:100])
            continue
        result = theme_strength(theme, driver_prices, regime)
        stocks = []
        for code in theme["stocks"]:
            prices = fetched.get(f"{code}.JK")
            if isinstance(prices, Exception) or prices is None or len(prices) < 60:
                errors.append(f"{code}: no data")
                continue
            stocks.append(screen_stock(code, prices, result, driver_prices, index_prices, min_value_bn))
        stocks.sort(key=lambda row: row["score"], reverse=True)
        result["stocks"] = stocks
        result.pop("_level", None)
        themes.append(result)
        if result["strength"] >= 3:
            candidates.extend(stocks)

    # Daily ideas: active themes only, liquid, trend-confirmed, linked to the driver
    candidates.sort(key=lambda row: row["score"], reverse=True)
    ideas, per_theme, seen = [], {}, set()
    for row in candidates:
        if row["code"] in seen or not row["liquid"] or row["correlation"] < min_correlation(row["theme_id"]):
            continue
        if not (row["status"].startswith("Momentum") or row["status"].startswith("Pullback")):
            continue
        if per_theme.get(row["theme_id"], 0) >= MAX_PER_THEME:
            continue
        ideas.append(row)
        seen.add(row["code"])
        per_theme[row["theme_id"]] = per_theme.get(row["theme_id"], 0) + 1
        if len(ideas) >= MAX_IDEAS:
            break

    # Early-turn candidates: active/emerging themes, stock basing or turning up -> buy only on a trigger close
    early_pool = []
    for theme in themes:
        if theme["strength"] < 2:
            continue
        for row in theme["stocks"]:
            if row["code"] in seen or not row["liquid"] or row["correlation"] < min_correlation(row["theme_id"]):
                continue
            if row["status"].startswith(("Turning up", "Basing", "Pullback", "Momentum")):
                early_pool.append({**row, "theme_status": theme["status"], "theme_trigger": theme["trigger"],
                                   "theme_strength": theme["strength"]})
    early_pool.sort(key=lambda row: (row["theme_strength"], row["score"]), reverse=True)
    watch, early_per_theme = [], {}
    for row in early_pool:
        if row["code"] in seen or early_per_theme.get(row["theme_id"], 0) >= MAX_PER_THEME:
            continue
        watch.append(row)
        seen.add(row["code"])
        early_per_theme[row["theme_id"]] = early_per_theme.get(row["theme_id"], 0) + 1
        if len(watch) >= MAX_EARLY:
            break

    sizing = {
        "Risk-off": "Market is risk-off: use half your normal size, take profits faster, and respect stops strictly.",
        "Cautious": "Market is cautious: smaller size than usual; prefer pullback entries over chasing.",
    }.get(regime, "Normal position sizing; still cap risk per trade at ~1% of capital.")

    today = datetime.now(JAKARTA).strftime("%Y-%m-%d")
    if ideas:
        log_ideas(ideas, today)
    prices_by_code = {symbol.replace(".JK", ""): value for symbol, value in fetched.items()}
    summary = {
        "generated": datetime.now(JAKARTA).strftime("%Y-%m-%d %H:%M"),
        "regime": regime,
        "sizing": sizing,
        "ideas": ideas,
        "watch": watch,
        "themes": sorted(themes, key=lambda theme: theme["strength"], reverse=True),
        "track_record": track_record(prices_by_code, today),
        "errors": errors,
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
    print(f"Macro ideas {result['generated']} | regime {result['regime']}")
    for theme in result["themes"]:
        top = ", ".join(f"{s['code']}({s['score']:.0f})" for s in theme["stocks"][:3])
        print(f"  {theme['strength']}/5 {theme['status']:18s} {theme['name']:28s} "
              f"{theme['driver_name']} {theme['last']:,.2f} 1M {theme['change_1m']:+.1f}% | {top}")
    print("IDEAS:")
    for idea in result["ideas"]:
        plan = idea["plan"]
        print(f"  {idea['code']:5s} {idea['theme']:24s} {idea['price']:>8,.0f} {idea['status']:34s} "
              f"corr {idea['correlation']:.2f} entry {plan['entry_low']:,}-{plan['entry_high']:,} "
              f"stop {plan['stop']:,} T1 {plan['target_1']:,} T2 {plan['target_2']:,} score {idea['score']}")
    print("EARLY-TURN (buy only on a close above the trigger):")
    for row in result["watch"]:
        plan = row["trigger_plan"]
        print(f"  {row['code']:5s} {row['theme']:26s} {row['price']:>8,.0f} {row['status']:36s} corr {row['correlation']:+.2f} "
              f"trigger {plan['trigger']:,} ({plan['distance_pct']:+.1f}%) stop {plan['stop']:,} T1 {plan['target_1']:,} T2 {plan['target_2']:,}")
    print("errors:", result["errors"])

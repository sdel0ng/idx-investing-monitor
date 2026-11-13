"""
All IDX stocks (the full IHSG universe, ~840 listed shares) in one snapshot.

Source: TradingView's public market scanner (one request, delayed data, unofficial endpoint).
Per stock: price, performance (1D/1W/1M/3M/YTD/1Y), liquidity, market cap, valuation (P/E, P/BV, ROE),
trend vs MA20/MA50/MA200, RSI, ATR, 52-week range, TradingView technical rating, and screen signals
(breakouts, volume spikes, heavy selling, oversold, early trend turns).

Writes all_stocks.json. Run manually:  python all_stocks.py
Education only - not financial advice.
"""

import json
import math
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

FOLDER = Path(__file__).resolve().parent
SUMMARY_FILE = FOLDER / "all_stocks.json"
JAKARTA = timezone(timedelta(hours=7))
SCAN_URL = "https://scanner.tradingview.com/indonesia/scan"

COLUMNS = [
    "name", "description", "sector", "industry", "close", "change", "Perf.W", "Perf.1M", "Perf.3M", "Perf.YTD", "Perf.Y",
    "volume", "Value.Traded", "average_volume_10d_calc", "relative_volume_10d_calc", "market_cap_basic",
    "RSI", "SMA20", "SMA50", "SMA200", "ATR", "price_52_week_high", "price_52_week_low", "High.1M", "Low.1M",
    "price_earnings_ttm", "price_book_ratio", "return_on_equity", "dividends_yield_current", "Recommend.All",
    "MACD.macd", "MACD.signal",
]


def number(value):
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def pct(now, before):
    if not now or not before:
        return None
    return (now / before - 1) * 100


def fetch_rows() -> list:
    payload = {
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "options": {"lang": "en"},
        "markets": ["indonesia"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": COLUMNS,
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, 3000],
    }
    request = urllib.request.Request(
        SCAN_URL,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        result = json.load(response)
    return [dict(zip(COLUMNS, row["d"])) for row in result.get("data", [])]


def rating_label(value):
    if value is None:
        return "–"
    if value >= 0.5:
        return "Strong buy"
    if value >= 0.1:
        return "Buy"
    if value > -0.1:
        return "Neutral"
    if value > -0.5:
        return "Sell"
    return "Strong sell"


def trend_status(close, ma20, ma50, ma200):
    if not all([close, ma50, ma200]):
        return "n/a", "grey"
    if close > ma50 > ma200 and (ma20 is None or close > ma20):
        return "Uptrend", "green"
    if close > ma200 and close <= ma50:
        return "Pullback (uptrend intact)", "amber"
    if close > ma200:
        return "Recovering above MA200", "green"
    if ma50 > ma200:
        return "Weakening (lost MA200)", "red"
    if ma20 and close > ma20 and close > ma50:
        return "Bouncing in downtrend", "amber"
    return "Downtrend", "red"


def analyse(raw: dict) -> dict:
    close = number(raw["close"])
    ma20, ma50, ma200 = number(raw["SMA20"]), number(raw["SMA50"]), number(raw["SMA200"])
    rsi = number(raw["RSI"])
    change = number(raw["change"]) or 0.0
    relative_volume = number(raw["relative_volume_10d_calc"])
    average_volume = number(raw["average_volume_10d_calc"]) or 0.0
    high_52w, low_52w = number(raw["price_52_week_high"]), number(raw["price_52_week_low"])
    high_1m = number(raw["High.1M"])
    macd, macd_signal = number(raw["MACD.macd"]), number(raw["MACD.signal"])
    value_today_bn = (number(raw["Value.Traded"]) or 0) / 1e9
    typical_value_bn = (average_volume * (close or 0)) / 1e9
    trend, trend_class = trend_status(close, ma20, ma50, ma200)

    signals = []
    if close and high_52w and close >= high_52w * 0.97 and trend_class == "green":
        signals.append("52-week high zone")
    elif close and high_1m and close >= high_1m * 0.99 and change > 0:
        signals.append("New 1-month high")
    if relative_volume and relative_volume >= 2 and change > 0:
        signals.append("Volume spike up")
    if relative_volume and relative_volume >= 2 and change <= -3:
        signals.append("Heavy selling")
    if rsi is not None and rsi < 30:
        signals.append("Oversold")
    if rsi is not None and rsi > 75:
        signals.append("Overbought")
    if close and ma200 and ma50 and close > ma200 and ma50 < ma200:
        signals.append("Early turn: back above MA200")
    if macd is not None and macd_signal is not None and macd > macd_signal and macd < 0:
        signals.append("MACD turning up")

    if typical_value_bn >= 10:
        liquidity = "Liquid"
    elif typical_value_bn >= 1:
        liquidity = "Thin"
    else:
        liquidity = "Illiquid"

    return {
        "code": raw["name"],
        "name": raw["description"],
        "sector": raw["sector"] or "Other",
        "industry": raw["industry"] or "",
        "price": close,
        "change_1d": change,
        "perf_1w": number(raw["Perf.W"]),
        "perf_1m": number(raw["Perf.1M"]),
        "perf_3m": number(raw["Perf.3M"]),
        "perf_ytd": number(raw["Perf.YTD"]),
        "perf_1y": number(raw["Perf.Y"]),
        "value_today_bn": round(value_today_bn, 2),
        "typical_value_bn": round(typical_value_bn, 2),
        "relative_volume": relative_volume,
        "market_cap_t": round((number(raw["market_cap_basic"]) or 0) / 1e12, 2),
        "rsi": rsi,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "atr_pct": (number(raw["ATR"]) or 0) / close * 100 if close else None,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "from_high": pct(close, high_52w),
        "from_low": pct(close, low_52w),
        "pe": number(raw["price_earnings_ttm"]),
        "pbv": number(raw["price_book_ratio"]),
        "roe": number(raw["return_on_equity"]),
        "div_yield": number(raw["dividends_yield_current"]),
        "rating": number(raw["Recommend.All"]),
        "rating_label": rating_label(number(raw["Recommend.All"])),
        "trend": trend,
        "trend_class": trend_class,
        "signals": signals,
        "liquidity": liquidity,
    }


def build() -> dict:
    rows = [analyse(raw) for raw in fetch_rows() if raw.get("close")]
    liquid = [row for row in rows if row["liquidity"] == "Liquid"]
    advancers = sum(row["change_1d"] > 0 for row in rows)
    decliners = sum(row["change_1d"] < 0 for row in rows)
    above_ma200 = sum(1 for row in liquid if row["price"] and row["ma200"] and row["price"] > row["ma200"])
    summary = {
        "generated": datetime.now(JAKARTA).strftime("%Y-%m-%d %H:%M"),
        "source": "TradingView scanner (delayed)",
        "count": len(rows),
        "breadth": {
            "advancers": advancers,
            "decliners": decliners,
            "unchanged": len(rows) - advancers - decliners,
            "liquid": len(liquid),
            "liquid_above_ma200_pct": round(above_ma200 / len(liquid) * 100) if liquid else None,
        },
        "sectors": sorted({row["sector"] for row in rows}),
        "stocks": rows,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
    return summary


def load_summary() -> dict:
    if SUMMARY_FILE.exists():
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    return {}


def find(code: str):
    code = code.upper()
    for row in load_summary().get("stocks", []):
        if row["code"] == code:
            return row
    return None


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = build()
    breadth = result["breadth"]
    print(f"{result['count']} stocks @ {result['generated']} | up {breadth['advancers']} / down {breadth['decliners']} | "
          f"liquid {breadth['liquid']} ({breadth['liquid_above_ma200_pct']}% above MA200) | sectors {len(result['sectors'])}")
    signal_counts = {}
    for row in result["stocks"]:
        for signal in row["signals"]:
            signal_counts[signal] = signal_counts.get(signal, 0) + 1
    print("signals:", signal_counts)
    print("div yield filled:", sum(1 for r in result["stocks"] if r["div_yield"]), "| PE filled:", sum(1 for r in result["stocks"] if r["pe"]))
    for code in ("BBCA", "TPIA", "ANTM", "TLKM"):
        row = find(code)
        print(code, {k: row[k] for k in ("price", "change_1d", "trend", "rsi", "div_yield", "pe", "liquidity", "signals")} if row else "missing")

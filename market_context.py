"""
Market context for the Dividend Watchlist app:
  - IHSG technical analysis (trend, momentum, Bollinger, ATR, pivots, supports/resistances, scenarios) + chart
  - global index status (US, Europe, Asia, Indonesia) + 5-day performance chart
  - macro view (USD/IDR, DXY, US 10y, VIX, Brent, gold, copper + manual domestic items) with IDX impact
  - news (IHSG, Indonesian macro, global markets, dividend news for the watchlist)
  - one overall market regime with what it means for dividend buyers

Writes market_context.json, market_ihsg_chart.png and market_global_chart.png.
Run manually:  python market_context.py
Education only - not financial advice.
"""

import json
import math
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import dividend_watchlist as engine

FOLDER = engine.FOLDER
JAKARTA = engine.JAKARTA
SUMMARY_FILE = FOLDER / "market_context.json"
IHSG_CHART = FOLDER / "market_ihsg_chart.png"
GLOBAL_CHART = FOLDER / "market_global_chart.png"
MANUAL_MACRO_FILE = FOLDER / "macro_manual.json"
IHSG_PLAN_FILE = FOLDER / "ihsg_summary.json"   # shock-low / pullback levels from the 16:20 report

GLOBAL_INDICES = [
    ("US", "^GSPC", "S&P 500"),
    ("US", "^IXIC", "Nasdaq"),
    ("US", "^DJI", "Dow Jones"),
    ("Europe", "^GDAXI", "DAX (Germany)"),
    ("Europe", "^FTSE", "FTSE 100 (UK)"),
    ("Asia", "^N225", "Nikkei 225 (Japan)"),
    ("Asia", "^HSI", "Hang Seng (HK)"),
    ("Asia", "000001.SS", "Shanghai Composite"),
    ("Asia", "^KS11", "KOSPI (Korea)"),
    ("Asia", "^STI", "Straits Times (SG)"),
    ("Asia", "^KLSE", "KLCI (Malaysia)"),
    ("Indonesia", "^JKSE", "IHSG"),
    ("Indonesia", "^JKLQ45", "LQ45"),
    ("Indonesia", "EIDO", "EIDO (MSCI Indonesia ETF, US)"),
]

MACRO_ASSETS = [
    ("IDR=X", "USD/IDR", 0),
    ("DX-Y.NYB", "US Dollar Index (DXY)", 2),
    ("^TNX", "US 10-year yield (%)", 2),
    ("^VIX", "VIX (US fear gauge)", 1),
    ("BZ=F", "Brent oil (USD/bbl)", 2),
    ("GC=F", "Gold (USD/oz)", 0),
    ("HG=F", "Copper (USD/lb)", 2),
]

NEWS_QUERIES = [
    ("IHSG & Bursa", 'IHSG OR "Bursa Efek Indonesia" OR "investor asing" when:1d',
     ("ihsg", "bursa", "saham", "bei", "asing", "msci"), "id", 30),
    ("Indonesia macro", '"BI rate" OR "suku bunga acuan" OR rupiah OR "inflasi Indonesia" OR "cadangan devisa" '
                        'OR "neraca perdagangan" OR "pertumbuhan ekonomi" OR APBN when:2d',
     ("bi rate", "suku bunga", "rupiah", "inflasi", "devisa", "neraca", "apbn", "pertumbuhan ekonomi", "pdb", "fiskal"),
     "id", 48),
    ("Global markets", '"Wall Street" OR "Federal Reserve" OR "global stocks" OR Treasury when:1d',
     ("stock", "wall street", "fed", "market", "s&p", "nasdaq", "treasury", "dow", "yield", "dollar"), "en", 30),
    ("Dividends (watchlist)", None, ("dividen", "dividend"), "id", 24 * 7),
]

POSITIVE_WORDS = ("naik", "menguat", "rebound", "melonjak", "positif", "rally", "rise", "gain", "jump",
                  "surge", "higher", "record", "beli bersih", "net buy", "optimis", "tumbuh", "lift", "boost",
                  "asing masuk", "hijau")
# Local-government / human-interest noise that matches macro keywords
NOISE_WORDS = ("pemkab", "pemkot", "pemprov", "tukar uang", "uang logam", "bupati", "wali kota", "desa ")
NEGATIVE_WORDS = ("turun", "melemah", "anjlok", "jatuh", "merosot", "negatif", "fall", "drop", "slump",
                  "plunge", "lower", "tumble", "jual bersih", "net sell", "khawatir", "tekanan", "koreksi")


# ---------------------------------------------------------------- helpers
def pct(now, before):
    if before in (None, 0) or now is None or (isinstance(before, float) and math.isnan(before)):
        return None
    return (now / before - 1) * 100


def value_days_ago(close: pd.Series, days: int):
    target = close.index[-1] - pd.Timedelta(days=days)
    earlier = close[close.index <= target]
    return float(earlier.iloc[-1]) if len(earlier) else None


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, np.nan))


def clean_bad_ticks(prices: pd.DataFrame) -> pd.DataFrame:
    """Fix obvious Yahoo data errors: a one-day spike of >10x the normal daily move that fully
    reverses the next day (e.g. USD/IDR 3 Nov 2026: 17,472 between 18,114 and 18,135)."""
    prices = prices.copy()
    # Rule 1: a close outside its own bar's high-low range is a data error -> use the bar midpoint
    if {"High", "Low"} <= set(prices.columns):
        high, low = prices["High"], prices["Low"]
        valid_range = high.notna() & low.notna() & (high >= low)
        outside = valid_range & ((prices["Close"] < low * 0.998) | (prices["Close"] > high * 1.002))
        prices.loc[outside, "Close"] = (high[outside] + low[outside]) / 2
    # Rule 2: a one-day spike that fully reverses the next day
    close = prices["Close"]
    returns = close.pct_change()
    typical = returns.abs().rolling(60, min_periods=20).median()
    for position in range(1, len(close) - 1):
        jump, back = returns.iloc[position], returns.iloc[position + 1]
        limit = 10 * (typical.iloc[position] or 0)
        if limit and abs(jump) > limit and abs(back) > limit and jump * back < 0:
            prices.iloc[position, prices.columns.get_loc("Close")] = (close.iloc[position - 1] + close.iloc[position + 1]) / 2
    return prices


def trend_status(close, ma50, ma200) -> tuple:
    if any(v is None or math.isnan(v) for v in (ma50, ma200)):
        return "n/a", "grey"
    if close > ma50 > ma200:
        return "Uptrend", "green"
    if close > ma200 and close <= ma50:
        return "Pullback (uptrend intact)", "amber"
    if close > ma200:
        return "Recovering above MA200", "green"
    if ma50 > ma200:
        return "Weakening (lost MA200)", "red"
    if close > ma50:
        return "Bouncing in downtrend", "amber"
    return "Downtrend", "red"


def tone(title: str) -> str:
    text = title.lower()
    positive = sum(word in text for word in POSITIVE_WORDS)
    negative = sum(word in text for word in NEGATIVE_WORDS)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


# ---------------------------------------------------------------- global indices
def analyse_index(item) -> dict:
    region, symbol, name = item
    try:
        prices, _ = engine.fetch_history(symbol, years=2)
        prices = clean_bad_ticks(prices)
    except Exception as error:
        return {"region": region, "symbol": symbol, "name": name, "error": repr(error)[:80]}
    close = prices["Close"]
    last = float(close.iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200 = float(close.rolling(200).mean().iloc[-1])
    year_start = close[close.index.year == close.index[-1].year]
    status, status_class = trend_status(last, ma50, ma200)
    high_52w = float(close.iloc[-250:].max())
    return {
        "region": region,
        "symbol": symbol,
        "name": name,
        "date": close.index[-1].strftime("%Y-%m-%d"),
        "last": last,
        "change_1d": pct(last, float(close.iloc[-2])),
        "change_5d": pct(last, float(close.iloc[-6])) if len(close) > 6 else None,
        "change_1m": pct(last, value_days_ago(close, 30)),
        "change_3m": pct(last, value_days_ago(close, 91)),
        "change_ytd": pct(last, float(year_start.iloc[0])) if len(year_start) > 1 else None,
        "vs_ma200": pct(last, ma200),
        "from_high": pct(last, high_52w),
        "rsi": float(rsi(close).iloc[-1]),
        "status": status,
        "status_class": status_class,
    }


# ---------------------------------------------------------------- macro
def macro_impact(symbol: str, row: dict) -> tuple:
    """(score -1/0/+1, explanation) of what the move means for IDX stocks."""
    c5, c1m, last, above_ma50 = row["change_5d"] or 0, row["change_1m"] or 0, row["last"], row["above_ma50"]
    if symbol == "IDR=X":
        if c5 > 0.5 or (above_ma50 and c1m > 1):
            return -1, "Rupiah weakening: foreign outflow risk, pressure on banks and importers."
        if c5 < -0.5 and not above_ma50:
            return 1, "Rupiah strengthening: supports foreign inflows and banks."
        return 0, "Rupiah stable."
    if symbol == "DX-Y.NYB":
        if c5 > 0.5 and above_ma50:
            return -1, "Strong dollar: money tends to leave emerging markets like Indonesia."
        if c5 < -0.5 and not above_ma50:
            return 1, "Weaker dollar: supportive for emerging markets and the rupiah."
        return 0, "Dollar roughly flat."
    if symbol == "^TNX":
        change_1m_points = last - (row["value_1m"] or last)
        if last >= 4.75 or change_1m_points > 0.2:
            return -1, f"US 10y at {last:.2f}%: high US yields compete with EM stocks and dividend yields."
        if change_1m_points < -0.2:
            return 1, "US yields falling: makes Indonesian dividend yields more attractive."
        return 0, "US yields steady."
    if symbol == "^VIX":
        if last > 25:
            return -1, f"VIX {last:.0f}: global risk-off, foreign funds sell EM first."
        if last < 16:
            return 1, f"VIX {last:.0f}: calm markets, risk appetite healthy."
        return 0, f"VIX {last:.0f}: somewhat elevated, not panic."
    if symbol == "BZ=F":
        if c1m > 8:
            return -1, "Oil rising fast: inflation and fuel-subsidy pressure (rupiah); good for energy stocks only."
        if c1m < -8:
            return 1, "Oil falling: eases inflation, fuel subsidies and the rupiah."
        return 0, "Oil not moving much over a month."
    if symbol == "GC=F":
        return 0, "Gold up = investors hedging risk; positive for gold miners (ANTM, MDKA)."
    if symbol == "HG=F":
        if c1m > 5:
            return 1, "Copper rising: sign of stronger global growth, good for commodity exporters."
        if c1m < -5:
            return -1, "Copper falling: global growth worries."
        return 0, "Copper steady."
    return 0, ""


def analyse_macro(item) -> dict:
    symbol, name, decimals = item
    try:
        prices, _ = engine.fetch_history(symbol, years=2)
        prices = clean_bad_ticks(prices)
    except Exception as error:
        return {"symbol": symbol, "name": name, "error": repr(error)[:80]}
    close = prices["Close"]
    last = float(close.iloc[-1])
    ma50 = float(close.rolling(50).mean().iloc[-1])
    row = {
        "symbol": symbol,
        "name": name,
        "decimals": decimals,
        "date": close.index[-1].strftime("%Y-%m-%d"),
        "last": last,
        "change_1d": pct(last, float(close.iloc[-2])),
        "change_5d": pct(last, float(close.iloc[-6])),
        "change_1m": pct(last, value_days_ago(close, 30)),
        "value_1m": value_days_ago(close, 30),
        "above_ma50": last > ma50,
        "trend": "Up" if last > ma50 else "Down",
    }
    row["impact"], row["impact_note"] = macro_impact(symbol, row)
    return row


def macro_view(rows: list) -> dict:
    valid = [row for row in rows if "error" not in row]
    score = sum(row["impact"] for row in valid)
    if score <= -3:
        label, css = "Macro headwind", "red"
    elif score <= -1:
        label, css = "Mild macro headwind", "amber"
    elif score >= 2:
        label, css = "Macro supportive", "green"
    else:
        label, css = "Macro neutral", "grey"
    drivers = [row["impact_note"] for row in valid if row["impact"] != 0]
    return {"score": score, "label": label, "class": css, "drivers": drivers}


def load_manual_macro() -> dict:
    if MANUAL_MACRO_FILE.exists():
        return json.loads(MANUAL_MACRO_FILE.read_text(encoding="utf-8"))
    return {"as_of": "", "items": []}


# ---------------------------------------------------------------- IHSG technical analysis
def last_completed_bar(prices: pd.DataFrame) -> pd.Series:
    """Yesterday's bar while today's session is still running (IDX closes ~16:00 WIB)."""
    now = datetime.now(JAKARTA)
    if prices.index[-1].date() == now.date() and now.hour < 16 and len(prices) > 1:
        return prices.iloc[-2]
    return prices.iloc[-1]


def ihsg_technical() -> dict:
    prices, _ = engine.fetch_history("^JKSE", years=2)
    close = prices["Close"]
    # Fill missing OHLC on a live-quote row
    for column in ("Open", "High", "Low"):
        prices[column] = prices[column].fillna(close)

    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()
    rsi14 = rsi(close)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    histogram = macd - signal
    std20 = close.rolling(20).std()
    upper_band, lower_band = ma20 + 2 * std20, ma20 - 2 * std20
    true_range = pd.concat(
        [prices["High"] - prices["Low"],
         (prices["High"] - close.shift()).abs(),
         (prices["Low"] - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr14 = true_range.rolling(14).mean()

    last = float(close.iloc[-1])
    previous = float(close.iloc[-2])
    m20, m50, m200 = float(ma20.iloc[-1]), float(ma50.iloc[-1]), float(ma200.iloc[-1])
    r = float(rsi14.iloc[-1])
    h, h_prev = float(histogram.iloc[-1]), float(histogram.iloc[-2])
    band_position = (last - float(lower_band.iloc[-1])) / (float(upper_band.iloc[-1]) - float(lower_band.iloc[-1])) * 100

    score = 0
    signals = []
    if last > m20:
        score += 1
        signals.append(("+", f"Above MA20 ({m20:,.0f}) - short-term buyers in control"))
    else:
        score -= 1
        signals.append(("-", f"Below MA20 ({m20:,.0f}) - short-term trend down"))
    if last > m50:
        score += 1
        signals.append(("+", f"Above MA50 ({m50:,.0f})"))
    else:
        score -= 1
        signals.append(("-", f"Below MA50 ({m50:,.0f}) - medium-term trend down"))
    if last > m200:
        score += 1
        signals.append(("+", f"Above MA200 ({m200:,.0f}) - long-term uptrend"))
    else:
        score -= 1
        signals.append(("-", f"Below MA200 ({m200:,.0f}) - long-term downtrend / bear phase"))
    if m50 < m200:
        signals.append(("-", "MA50 below MA200 (death-cross structure)"))
    if h > 0 and h > h_prev:
        score += 1
        signals.append(("+", "MACD momentum rising"))
    elif h < 0 and h < h_prev:
        score -= 1
        signals.append(("-", "MACD momentum falling"))
    elif h > h_prev:
        signals.append(("+", "MACD selling momentum fading (early sign of a bounce)"))
    else:
        signals.append(("-", "MACD buying momentum fading"))
    if r < 30:
        signals.append(("+", f"RSI {r:.0f} oversold - bounce risk rises, but oversold can stay oversold"))
    elif r > 70:
        signals.append(("-", f"RSI {r:.0f} overbought - pullback risk"))
    else:
        signals.append(("+" if r >= 50 else "-", f"RSI {r:.0f} ({'above' if r >= 50 else 'below'} 50)"))
    if band_position < 5:
        signals.append(("+", "At/below the lower Bollinger band - stretched to the downside"))
    elif band_position > 95:
        signals.append(("-", "At/above the upper Bollinger band - stretched to the upside"))

    if score >= 3:
        bias, bias_class = "Bullish", "green"
    elif score >= 1:
        bias, bias_class = "Mildly bullish", "green"
    elif score <= -3:
        bias, bias_class = "Bearish", "red"
    elif score <= -1:
        bias, bias_class = "Mildly bearish", "amber"
    else:
        bias, bias_class = "Neutral", "grey"

    # Pivots from the last completed session
    bar = last_completed_bar(prices)
    pivot = (bar["High"] + bar["Low"] + bar["Close"]) / 3
    pivots = {
        "based_on": bar.name.strftime("%a %d %b"),
        "R2": pivot + (bar["High"] - bar["Low"]),
        "R1": 2 * pivot - bar["Low"],
        "P": pivot,
        "S1": 2 * pivot - bar["High"],
        "S2": pivot - (bar["High"] - bar["Low"]),
    }

    # Key levels: moving averages, recent highs/lows, the post-shock plan from the close report
    levels = {
        "MA20": m20,
        "MA50": m50,
        "MA200": m200,
        "20-day high": float(prices["High"].iloc[-20:].max()),
        "20-day low": float(prices["Low"].iloc[-20:].min()),
        "52-week high": float(prices["High"].iloc[-250:].max()),
        "52-week low": float(prices["Low"].iloc[-250:].min()),
    }
    if IHSG_PLAN_FILE.exists():
        plan = json.loads(IHSG_PLAN_FILE.read_text(encoding="utf-8")).get("plan", {})
        if plan.get("available"):
            levels["Shock low (19 Oct)"] = plan["shock_low"]
            levels["Rebound high"] = plan["rebound_high"]
            levels["Pre-shock close (15 Oct)"] = plan["pre_shock_close"]
            levels["Old pullback zone"] = plan["pullback"]["zone"][0]
    # Pivot levels and round psychological numbers add context beyond the extremes
    levels["Pivot S1"] = pivots["S1"]
    levels["Pivot S2"] = pivots["S2"]
    levels["Pivot R1"] = pivots["R1"]
    step = 250
    for multiple in range(int(last // step) - 2, int(last // step) + 3):
        levels.setdefault(f"Round {multiple * step:,}", multiple * step)

    def merged(candidates):
        """Merge levels within 0.3% of each other, keeping all their names."""
        output = []
        for value, name in candidates:
            if output and abs(value - output[-1][0]) / last < 0.003:
                output[-1] = (output[-1][0], output[-1][1] + " / " + name)
            else:
                output.append((value, name))
        return output

    supports = merged(sorted([(round(v), n) for n, v in levels.items() if v < last * 0.999], reverse=True))[:4]
    resistances = merged(sorted([(round(v), n) for n, v in levels.items() if v > last * 1.001]))[:4]

    first_resistance = resistances[0] if resistances else (round(last * 1.03), "+3%")
    first_support = supports[0] if supports else (round(last * 0.97), "-3%")
    scenarios = [
        ("Bullish", f"A daily close above {first_resistance[0]:,} ({first_resistance[1]}) would be the first sign of a "
                    f"base; a close back above MA50 {m50:,.0f} would confirm a real recovery."),
        ("Base case", f"Choppy between {first_support[0]:,} and {first_resistance[0]:,} while momentum "
                      f"{'improves' if h > h_prev else 'stays weak'}; typical daily move ±{float(atr14.iloc[-1]):,.0f} pts "
                      f"({float(atr14.iloc[-1]) / last * 100:.1f}%)."),
        ("Bearish", f"A daily close below {first_support[0]:,} ({first_support[1]}) opens the next leg down"
                    + (f" toward {supports[1][0]:,} ({supports[1][1]})." if len(supports) > 1 else ".")),
    ]

    official = {}
    if IHSG_PLAN_FILE.exists():
        data = json.loads(IHSG_PLAN_FILE.read_text(encoding="utf-8"))
        official = {
            "date": data.get("date"),
            "label": data.get("outlook", {}).get("label"),
            "expectation": data.get("outlook", {}).get("expectation"),
        }

    draw_ihsg_chart(prices, ma20, ma50, ma200, upper_band, lower_band, rsi14, supports, resistances)
    live = prices.index[-1].date() == datetime.now(JAKARTA).date() and datetime.now(JAKARTA).hour < 16
    return {
        "date": prices.index[-1].strftime("%Y-%m-%d"),
        "completed_date": bar.name.strftime("%Y-%m-%d"),
        "live": live,
        "last": last,
        "change_1d": pct(last, previous),
        "change_5d": pct(last, float(close.iloc[-6])),
        "change_1m": pct(last, value_days_ago(close, 30)),
        "change_ytd": pct(last, float(close[close.index.year == close.index[-1].year].iloc[0])),
        "ma20": m20,
        "ma50": m50,
        "ma200": m200,
        "rsi": r,
        "macd_hist": h,
        "atr": float(atr14.iloc[-1]),
        "band_position": band_position,
        "score": score,
        "bias": bias,
        "bias_class": bias_class,
        "signals": signals,
        "pivots": {k: (round(v) if not isinstance(v, str) else v) for k, v in pivots.items()},
        "supports": supports,
        "resistances": resistances,
        "scenarios": scenarios,
        "official_call": official,
    }


def draw_ihsg_chart(prices, ma20, ma50, ma200, upper, lower, rsi14, supports, resistances, sessions=160):
    view = slice(-sessions, None)
    index = prices.index[view]
    figure, (ax_price, ax_rsi) = plt.subplots(
        2, 1, figsize=(11, 6.2), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    ax_price.fill_between(index, lower.iloc[view], upper.iloc[view], color="#4c7cb3", alpha=0.08, label="Bollinger 20,2")
    ax_price.plot(index, prices["Close"].iloc[view], color="#12355b", linewidth=1.6, label="IHSG")
    ax_price.plot(index, ma20.iloc[view], color="#d69e2e", linewidth=1, label="MA20")
    ax_price.plot(index, ma50.iloc[view], color="#2f855a", linewidth=1, label="MA50")
    ax_price.plot(index, ma200.iloc[view], color="#c53030", linewidth=1.2, label="MA200")
    for level, name in supports[:2]:
        ax_price.axhline(level, color="#2f855a", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_price.text(index[2], level, f" S {level:,} {name}", fontsize=8, color="#2f855a", va="bottom")
    for level, name in resistances[:2]:
        ax_price.axhline(level, color="#c53030", linestyle="--", linewidth=0.8, alpha=0.7)
        ax_price.text(index[2], level, f" R {level:,} {name}", fontsize=8, color="#c53030", va="bottom")
    ax_price.set_title(f"IHSG daily - last {index[-1]:%d %b %Y}: {prices['Close'].iloc[-1]:,.0f}")
    ax_price.legend(loc="upper left", fontsize=8, ncol=5)
    ax_price.grid(alpha=0.2)
    ax_rsi.plot(index, rsi14.iloc[view], color="#6b46c1", linewidth=1)
    ax_rsi.axhline(70, color="#c53030", linewidth=0.7, linestyle=":")
    ax_rsi.axhline(30, color="#2f855a", linewidth=0.7, linestyle=":")
    ax_rsi.set_ylim(0, 100)
    ax_rsi.set_ylabel("RSI 14")
    ax_rsi.grid(alpha=0.2)
    figure.tight_layout()
    figure.savefig(IHSG_CHART, dpi=110)
    plt.close(figure)


def draw_global_chart(rows: list) -> None:
    valid = [row for row in rows if "error" not in row and row.get("change_5d") is not None]
    valid.sort(key=lambda row: row["change_5d"])
    figure, axis = plt.subplots(figsize=(7.5, 0.34 * len(valid) + 1))
    colors = ["#2f855a" if row["change_5d"] >= 0 else "#c53030" for row in valid]
    axis.barh([row["name"] for row in valid], [row["change_5d"] for row in valid], color=colors)
    for position, row in enumerate(valid):
        axis.text(row["change_5d"], position, f" {row['change_5d']:+.1f}%", va="center",
                  ha="left" if row["change_5d"] >= 0 else "right", fontsize=8)
    axis.axvline(0, color="#52606d", linewidth=0.8)
    axis.set_title("5-day performance")
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    figure.savefig(GLOBAL_CHART, dpi=110)
    plt.close(figure)


# ---------------------------------------------------------------- news
def fetch_news(category, query, keywords, language, max_age_hours, limit=8) -> list:
    if query is None:  # dividend news for the top watchlist names
        watchlist = engine.load_summary().get("stocks", [])
        codes = [row["code"] for row in watchlist if row["class"] != "Cyclical"][:10] or ["TLKM", "BBRI", "BMRI"]
        query = "dividen (" + " OR ".join(codes) + ") when:7d"
    locale = "&hl=id&gl=ID&ceid=ID:id" if language == "id" else "&hl=en-US&gl=US&ceid=US:en"
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + locale
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            feed = ET.fromstring(response.read())
    except Exception as error:
        return [{"title": f"News unavailable ({error!r:.60})", "source": "", "link": "", "time": None, "tone": "neutral"}]
    cutoff = datetime.now(JAKARTA) - timedelta(hours=max_age_hours)
    items, seen = [], set()
    for item in feed.findall("./channel/item"):
        full_title = item.findtext("title") or ""
        title, _, source = full_title.rpartition(" - ")
        title = title or full_title
        key = title.lower()[:70]
        lowered = full_title.lower()
        if key in seen or not any(word in lowered for word in keywords) or any(word in lowered for word in NOISE_WORDS):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate")).astimezone(JAKARTA)
        except Exception:
            published = None
        if published is not None and published < cutoff:
            continue
        seen.add(key)
        items.append({
            "title": title,
            "source": source,
            "link": item.findtext("link") or "",
            "time": published.strftime("%d %b %H:%M") if published else "",
            "sort": published.timestamp() if published else 0,
            "tone": tone(title),
        })
    items.sort(key=lambda entry: entry["sort"], reverse=True)
    return items[:limit]


# ---------------------------------------------------------------- overall regime
def market_regime(ihsg: dict, indices: list, macro: dict) -> dict:
    valid = [row for row in indices if "error" not in row and row["region"] != "Indonesia"]
    up = sum((row.get("vs_ma200") or 0) > 0 for row in valid)   # above MA200 = long-term uptrend intact
    breadth = up / len(valid) if valid else 0.5
    score = 0
    reasons = []
    if ihsg["score"] <= -3:
        score -= 2
        reasons.append(f"IHSG technicals {ihsg['bias'].lower()} (below MA20/50/200)")
    elif ihsg["score"] < 0:
        score -= 1
        reasons.append(f"IHSG technicals {ihsg['bias'].lower()}")
    elif ihsg["score"] >= 3:
        score += 2
        reasons.append("IHSG technicals bullish")
    elif ihsg["score"] > 0:
        score += 1
        reasons.append("IHSG technicals mildly bullish")
    if breadth >= 0.6:
        score += 1
        reasons.append(f"global markets healthy ({up}/{len(valid)} indices above MA200)")
    elif breadth <= 0.3:
        score -= 1
        reasons.append(f"global markets weak ({up}/{len(valid)} indices above MA200)")
    else:
        reasons.append(f"global markets mixed ({up}/{len(valid)} indices above MA200)")
    if macro["score"] <= -2:
        score -= 1
        reasons.append(macro["label"].lower())
    elif macro["score"] >= 2:
        score += 1
        reasons.append(macro["label"].lower())

    if score <= -3:
        label, css = "Risk-off", "red"
        advice = ("Protect cash. Don't buy dividend stocks in one go - wait for IHSG to build a base (a close back "
                  "above its first resistance), then buy in 3-4 small tranches over weeks.")
    elif score <= -1:
        label, css = "Cautious", "amber"
        advice = ("Only small starter positions in the strongest defensive names; keep most cash for lower prices "
                  "or a confirmed turn.")
    elif score == 0:
        label, css = "Neutral", "grey"
        advice = "Accumulate gradually on dips toward MA50 in quality dividend payers."
    elif score <= 2:
        label, css = "Constructive", "green"
        advice = "Good environment to build dividend positions on pullbacks."
    else:
        label, css = "Risk-on", "green"
        advice = "Strong market - avoid chasing; dividend stocks are best bought on dips."
    return {"score": score, "label": label, "class": css, "reasons": reasons, "advice": advice,
            "global_breadth": round(breadth * 100)}


# ---------------------------------------------------------------- build
def build() -> dict:
    with ThreadPoolExecutor(max_workers=12) as pool:
        index_future = pool.map(analyse_index, GLOBAL_INDICES)
        macro_future = pool.map(analyse_macro, MACRO_ASSETS)
        news_futures = [pool.submit(fetch_news, *query) for query in NEWS_QUERIES]
        ihsg_future = pool.submit(ihsg_technical)
        indices = list(index_future)
        macro_rows = list(macro_future)
        news = {query[0]: future.result() for query, future in zip(NEWS_QUERIES, news_futures)}
        ihsg = ihsg_future.result()

    draw_global_chart(indices)
    macro = macro_view(macro_rows)
    summary = {
        "generated": datetime.now(JAKARTA).strftime("%Y-%m-%d %H:%M"),
        "regime": market_regime(ihsg, indices, macro),
        "ihsg": ihsg,
        "indices": indices,
        "macro": {"rows": macro_rows, "view": macro, "manual": load_manual_macro()},
        "news": news,
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
    regime = result["regime"]
    ihsg = result["ihsg"]
    print(f"Market context {result['generated']}: {regime['label']} ({regime['score']:+d}) - {'; '.join(regime['reasons'])}")
    print(f"IHSG {ihsg['last']:,.0f} ({ihsg['change_1d']:+.2f}%) {ihsg['bias']} ({ihsg['score']:+d}) RSI {ihsg['rsi']:.0f}")
    print("  S:", ihsg["supports"], " R:", ihsg["resistances"])
    print("  pivots:", ihsg["pivots"])
    for row in result["indices"]:
        if "error" in row:
            print("  ERR", row["name"], row["error"])
        else:
            print(f"  {row['name']:30s} {row['last']:>12,.2f} 1D {row['change_1d']:+6.2f}% 5D {row['change_5d']:+6.2f}% {row['status']}")
    print("Macro:", result["macro"]["view"]["label"], result["macro"]["view"]["score"])
    for row in result["macro"]["rows"]:
        print(f"  {row['name']:24s} {row.get('last', 0):>10,.2f} 5D {row.get('change_5d') or 0:+.2f}% impact {row.get('impact')}")
    for category, items in result["news"].items():
        print(f"News - {category}: {len(items)}")
        for item in items[:2]:
            print(f"   [{item['tone']}] {item['time']} {item['title'][:90]}")

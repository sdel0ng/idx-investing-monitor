"""
BMRI end-of-day report — runs after the IDX close (scheduled 16:20 WIB).

1. Re-pulls daily prices (Yahoo Finance), waiting for the final close if needed.
2. Writes a rule-based technical analysis (trend, momentum, volume, candle, levels).
3. Asks for today's broker summary (broksum) and analyses accumulation/distribution.
4. Checks the trading-plan triggers and alerts the first time each one fires.
5. Saves an HTML report (chart embedded) and opens it.

Usage:
    python bmri_daily_check.py              # normal run
    python bmri_daily_check.py --no-ask     # skip the broksum form
    python bmri_daily_check.py --no-open    # don't open the HTML report
    python bmri_daily_check.py --test-alert # send a test notification

Email / WhatsApp settings live in alert_config.json next to this script.
"""

import argparse
import base64
import ctypes
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# ---------------------------------------------------------------- plan / config
TICKER = "BMRI.JK"
AVERAGE_PRICE = 3809
BREAKEVEN_PRICE = 3820          # avg / (1 - ~0.25% sell fee), rounded to tick
STOP_CLOSE = 3600               # exit on a daily CLOSE below this
CAUTION_CLOSE = 3650            # warn when closing this close to the stop
TARGET_1 = 3960                 # gap fill: sell ~half, stop to breakeven
BREAKOUT_LEVEL = 3980           # gap top: confirmed on close above + volume
TARGET_2 = 4100                 # MA50 area
TARGET_3 = 4300                 # Sep range, only after confirmed breakout
CRASH_LOW = 3200                # 19 Oct 2026 capitulation low

FOLDER = Path(__file__).resolve().parent
PRICE_FILE = FOLDER / "BMRI_daily.csv"
FOREIGN_FILE = FOLDER / "BMRI_foreign_flow.csv"
IHSG_FLOW_FILE = FOLDER / "IHSG_foreign_flow.csv"
BROKSUM_FILE = FOLDER / "BMRI_broksum_history.csv"
BROKSUM_DETAIL_FILE = FOLDER / "BMRI_broksum_detail.csv"
BROKER_MAP_FILE = FOLDER / "brokers.json"
STATE_FILE = FOLDER / "alert_state.json"
CHART_FILE = FOLDER / "BMRI_chart.png"
REPORT_FILE = FOLDER / "BMRI_latest_report.txt"
SUMMARY_FILE = FOLDER / "latest_close_summary.json"
LOG_FILE = FOLDER / "BMRI_daily_log.csv"
CONFIG_FILE = FOLDER / "alert_config.json"
REPORT_DIR = FOLDER / "reports"
POSITIONS_FILE = FOLDER / "positions.json"
SELL_FEE = 0.0025                # typical IDX sell fee incl. 0.1% final tax


def idx_tick(price: float) -> int:
    """IDX price tick for the price band."""
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def breakeven_for(average_price: float) -> int:
    raw = average_price / (1 - SELL_FEE)
    tick = idx_tick(raw)
    return int(-(-raw // tick) * tick)     # round UP to the next tick


def load_positions() -> dict:
    """{code: {"average_price": float|None, "lots": int|None}} edited from the dashboard."""
    defaults = {
        "BMRI": {"average_price": 3809, "lots": None},
        "TEBE": {"average_price": 2220, "lots": None},
    }
    if POSITIONS_FILE.exists():
        try:
            saved = json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
            for code, values in saved.items():
                defaults.setdefault(code, {}).update(values)
        except (ValueError, OSError):
            pass
    else:
        POSITIONS_FILE.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
    return defaults


_bmri_position = load_positions().get("BMRI", {})
# Empty average price in positions.json = position closed: BMRI becomes watch-only
HOLDING_BMRI = bool(_bmri_position.get("average_price"))
if HOLDING_BMRI:
    AVERAGE_PRICE = _bmri_position["average_price"]
    BREAKEVEN_PRICE = breakeven_for(AVERAGE_PRICE)

DEFAULT_CONFIG = {
    "popup": {"enabled": True},
    "send_daily_summary": False,
    "ihsg_daily_call": {"enabled": False},   # daily IHSG rebound/retrace call after the 18:00 run
    "email": {
        "enabled": False,
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 465,
        "sender": "your.name@gmail.com",
        "app_password": "xxxx xxxx xxxx xxxx",
        "recipients": ["your.name@gmail.com"],
        "attach_chart": True,
    },
    "whatsapp": {
        "enabled": False,
        "provider": "callmebot",
        "phone": "+628xxxxxxxxxx",
        "callmebot_apikey": "",
        "fonnte_token": "",
    },
}

CATEGORY_LABELS = {
    "foreign_inst": "Foreign inst.",
    "local_inst": "Local inst.",
    "retail": "Retail",
    "unknown": "Unclassified",
}


# ---------------------------------------------------------------- data
def fetch_prices():
    """Return (daily OHLCV DataFrame, last trade time in WIB)."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{TICKER}"
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
    prices = prices[~prices.index.duplicated(keep="last")]
    prices.to_csv(PRICE_FILE)
    last_trade = pd.Timestamp(result["meta"]["regularMarketTime"], unit="s", tz="UTC")
    last_trade = last_trade.tz_convert("Asia/Jakarta").tz_localize(None)
    return prices, last_trade


def add_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    close = prices["Close"]
    for window in (20, 50, 200):
        prices[f"MA{window}"] = close.rolling(window).mean()

    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    prices["RSI14"] = 100 - 100 / (1 + gain / loss)

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    prices["MACD"] = ema12 - ema26
    prices["Signal"] = prices["MACD"].ewm(span=9, adjust=False).mean()
    prices["Hist"] = prices["MACD"] - prices["Signal"]

    previous_close = close.shift()
    true_range = pd.concat(
        [
            prices["High"] - prices["Low"],
            (prices["High"] - previous_close).abs(),
            (prices["Low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    prices["ATR14"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    # Average volume of the PREVIOUS 20 days, so today's bar is compared fairly
    prices["Vol20"] = prices["Volume"].rolling(20).mean().shift()
    return prices


# ---------------------------------------------------------------- technical analysis
def detect_candle(prices: pd.DataFrame) -> str:
    today = prices.iloc[-1]
    yesterday = prices.iloc[-2]
    open_, high, low, close = today["Open"], today["High"], today["Low"], today["Close"]
    body = abs(close - open_)
    full_range = high - low
    if full_range <= 0:
        return "Flat bar (no range)"
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    is_green = close >= open_
    y_green = yesterday["Close"] >= yesterday["Open"]

    if is_green and not y_green and close >= yesterday["Open"] and open_ <= yesterday["Close"]:
        return "Bullish engulfing: buyers overwhelmed yesterday's sellers (reversal signal, needs follow-through)"
    if not is_green and y_green and open_ >= yesterday["Close"] and close <= yesterday["Open"]:
        return "Bearish engulfing: sellers overwhelmed yesterday's buyers (warning sign)"
    if body <= 0.1 * full_range:
        return "Doji: indecision; the next candle decides direction"
    if lower_wick >= 2 * max(body, 1) and upper_wick <= 0.6 * max(body, 1) + 0.1 * full_range:
        return f"Hammer (long lower wick): buyers rejected prices near {low:,.0f}"
    if upper_wick >= 2 * max(body, 1) and lower_wick <= 0.6 * max(body, 1) + 0.1 * full_range:
        return f"Shooting star (long upper wick): sellers rejected prices near {high:,.0f}"
    if high < yesterday["High"] and low > yesterday["Low"]:
        return (
            f"Inside day: consolidation; watch a break of {yesterday['High']:,.0f} "
            f"(up) or {yesterday['Low']:,.0f} (down)"
        )
    if body >= 0.7 * full_range:
        side = "near the high (strong buyers)" if is_green else "near the low (strong sellers)"
        return f"Strong {'green' if is_green else 'red'} bar closing {side}"
    return f"Ordinary {'green' if is_green else 'red'} candle"


def technical_analysis(prices: pd.DataFrame) -> dict:
    today = prices.iloc[-1]
    close = today["Close"]
    score = 0

    # Trend
    above = [name for name in ("MA20", "MA50", "MA200") if close > today[name]]
    ma50_slope = today["MA50"] - prices["MA50"].iloc[-6]
    ma200_slope = today["MA200"] - prices["MA200"].iloc[-6]
    if not above:
        trend = "Downtrend: price below MA20, MA50 and MA200"
        score -= 2
    elif len(above) == 3:
        trend = "Uptrend: price above MA20, MA50 and MA200"
        score += 2
    else:
        below = [name for name in ("MA20", "MA50", "MA200") if name not in above]
        trend = f"Mixed: above {', '.join(above)}, below {', '.join(below)}"
        score += 1 if "MA20" in above else -1
    trend += (
        f". MA50 {'rising' if ma50_slope > 0 else 'falling'}, "
        f"MA200 {'rising' if ma200_slope > 0 else 'falling'}"
    )
    if today["MA50"] < today["MA200"]:
        trend += " (MA50 below MA200 = bearish structure)"

    # Momentum
    rsi = today["RSI14"]
    rsi_before = prices["RSI14"].iloc[-4]
    direction = "rising" if rsi > rsi_before else "falling"
    if rsi >= 70:
        zone = "overbought"
    elif rsi <= 30:
        zone = "oversold"
    elif rsi >= 50:
        zone = "bullish zone (above 50)"
        score += 1
    else:
        zone = "bearish zone (below 50)"
        score -= 1
    hist = today["Hist"]
    hist_before = prices["Hist"].iloc[-2]
    if hist > 0 and hist_before <= 0:
        macd_text = "MACD crossed UP through its signal today"
        score += 1
    elif hist < 0 and hist_before >= 0:
        macd_text = "MACD crossed DOWN through its signal today"
        score -= 1
    else:
        macd_text = (
            f"MACD histogram {'positive' if hist > 0 else 'negative'} and "
            f"{'improving' if hist > hist_before else 'weakening'}"
        )
    macd_text += f" ({'above' if today['MACD'] > 0 else 'below'} zero line)"
    momentum = f"RSI14 {rsi:.1f}, {zone}, {direction}. {macd_text}"

    # Volume
    ratio = today["Volume"] / today["Vol20"] if today["Vol20"] else 1.0
    up_day = close >= prices["Close"].iloc[-2]
    if ratio >= 1.5:
        volume = f"High volume ({ratio:.1f}x 20-day avg) on a {'rising' if up_day else 'falling'} day: strong conviction"
        score += 1 if up_day else -1
    elif ratio <= 0.7:
        volume = f"Light volume ({ratio:.1f}x avg): little conviction behind today's move"
    else:
        volume = f"Normal volume ({ratio:.1f}x avg)"

    # Levels
    last_10 = prices.iloc[-10:]
    levels = {
        "Stop (close)": STOP_CLOSE,
        "T1 gap fill": TARGET_1,
        "Breakout": BREAKOUT_LEVEL,
        "T2": TARGET_2,
        "MA20": today["MA20"],
        "MA50": today["MA50"],
        "MA200": today["MA200"],
        "10-day high": last_10["High"].max(),
        "10-day low": last_10["Low"].min(),
        "Crash low": CRASH_LOW,
    }
    supports = sorted(
        [(value, name) for name, value in levels.items() if value < close],
        reverse=True,
    )[:3]
    resistances = sorted(
        [(value, name) for name, value in levels.items() if value > close]
    )[:3]

    candle = detect_candle(prices)
    if candle.startswith(("Bullish engulfing", "Hammer")):
        score += 1
    elif candle.startswith(("Bearish engulfing", "Shooting star")):
        score -= 1

    if score >= 3:
        bias = "Bullish"
    elif score >= 1:
        bias = "Mildly bullish"
    elif score <= -3:
        bias = "Bearish"
    elif score <= -1:
        bias = "Mildly bearish"
    else:
        bias = "Neutral"

    # Plan-aware verdict for the open position
    if not HOLDING_BMRI:
        verdict = f"Not holding BMRI (watch only). Technical bias {bias.lower()}."
    elif close < STOP_CLOSE:
        verdict = f"EXIT per plan: closed below the {STOP_CLOSE:,} stop."
    elif today["High"] >= TARGET_1:
        verdict = (
            f"Take partial profit per plan (T1 {TARGET_1:,} reached); "
            f"move the stop on the rest to {BREAKEVEN_PRICE:,}."
        )
    elif close <= CAUTION_CLOSE:
        verdict = f"Hold, but on alert: close is only Rp{close - STOP_CLOSE:,.0f} above the stop."
    else:
        verdict = (
            f"HOLD: inside the plan range (stop {STOP_CLOSE:,} close / T1 {TARGET_1:,}). "
            "No action needed."
        )

    return {
        "trend": trend,
        "momentum": momentum,
        "volume": volume,
        "candle": candle,
        "supports": [[round(float(v)), n] for v, n in supports],
        "resistances": [[round(float(v)), n] for v, n in resistances],
        "atr": round(float(today["ATR14"])),
        "score": score,
        "bias": bias,
        "verdict": verdict,
    }


# ---------------------------------------------------------------- broksum
def load_broker_map() -> dict:
    if BROKER_MAP_FILE.exists():
        return json.loads(BROKER_MAP_FILE.read_text(encoding="utf-8"))
    return {}


def parse_number(raw: str) -> float:
    cleaned = raw.strip().replace("Rp", "").replace("rp", "").replace("bn", "").replace("B", "")
    cleaned = cleaned.replace(" ", "")
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def parse_price(raw: str) -> float:
    return float(re.sub(r"[^\d]", "", raw))


BROKER_LINE = re.compile(
    r"^\s*([A-Za-z]{2})\b[\s:=,]*([+-]?\d[\d.,]*)(?:[\s@,]+(\d[\d.,]*))?"
)


def parse_broksum(foreign_text: str, broker_text: str, ihsg_text: str = ""):
    ihsg_net_foreign = None
    if ihsg_text and ihsg_text.strip():
        try:
            ihsg_net_foreign = parse_number(ihsg_text)
        except ValueError:
            ihsg_net_foreign = None
    net_foreign = None
    if foreign_text and foreign_text.strip():
        try:
            net_foreign = parse_number(foreign_text)
        except ValueError:
            net_foreign = None
    brokers = []
    for line in broker_text.splitlines():
        match = BROKER_LINE.match(line)
        if not match:
            continue
        code, value, price = match.groups()
        try:
            net_bn = parse_number(value)
        except ValueError:
            continue
        avg_price = parse_price(price) if price else None
        brokers.append({"code": code.upper(), "net_bn": net_bn, "avg_price": avg_price})
    if net_foreign is None and not brokers and ihsg_net_foreign is None:
        return None
    return {"net_foreign": net_foreign, "brokers": brokers, "ihsg_net_foreign": ihsg_net_foreign}


def ask_broksum(date_label: str, close_price: float):
    """Small form for today's broker summary. Returns parsed dict or None."""
    try:
        import tkinter as tk
    except ImportError:
        return None

    answers = {}
    root = tk.Tk()
    root.title(f"BMRI broker summary — {date_label}")
    root.attributes("-topmost", True)

    tk.Label(
        root,
        text=f"BMRI closed {close_price:,.0f}. Enter today's broksum from your broker app.",
        font=("Segoe UI", 10, "bold"),
    ).pack(anchor="w", padx=10, pady=(10, 4))

    foreign_row = tk.Frame(root)
    foreign_row.pack(fill="x", padx=10)
    tk.Label(foreign_row, text="Net foreign (Rp bn, minus = net sell):").pack(side="left")
    foreign_entry = tk.Entry(foreign_row, width=12)
    foreign_entry.pack(side="left", padx=6)

    ihsg_row = tk.Frame(root)
    ihsg_row.pack(fill="x", padx=10, pady=(4, 0))
    tk.Label(ihsg_row, text="IHSG market net foreign (Rp bn, minus = net sell):").pack(side="left")
    ihsg_entry = tk.Entry(ihsg_row, width=12)
    ihsg_entry.pack(side="left", padx=6)

    tk.Label(
        root,
        justify="left",
        text=(
            "Top net buyers / sellers, one per line:  CODE  value(Rp bn)  [avg price]\n"
            "Buyers positive, sellers negative. Example:\n"
            "   AK 125.3 3790\n"
            "   BK 80\n"
            "   YP -150 3805\n"
            "   XL -60"
        ),
    ).pack(anchor="w", padx=10, pady=(8, 2))
    broker_box = tk.Text(root, width=50, height=10, font=("Consolas", 10))
    broker_box.pack(padx=10)

    def save():
        answers["foreign"] = foreign_entry.get()
        answers["ihsg"] = ihsg_entry.get()
        answers["brokers"] = broker_box.get("1.0", "end")
        root.destroy()

    def skip():
        root.destroy()

    button_row = tk.Frame(root)
    button_row.pack(fill="x", padx=10, pady=10)
    tk.Button(button_row, text="Save & analyse", width=16, command=save).pack(side="right")
    tk.Button(button_row, text="Skip today", width=12, command=skip).pack(side="right", padx=6)
    root.protocol("WM_DELETE_WINDOW", skip)
    root.after(200, foreign_entry.focus_force)
    root.mainloop()

    if not answers:
        return None
    return parse_broksum(answers["foreign"], answers["brokers"], answers.get("ihsg", ""))


def weighted_average_price(rows: list):
    priced = [row for row in rows if row.get("avg_price")]
    total = sum(abs(row["net_bn"]) for row in priced)
    if not priced or total == 0:
        return None
    return sum(abs(row["net_bn"]) * row["avg_price"] for row in priced) / total


def analyse_broksum(entry: dict, broker_map: dict, close_price: float) -> dict:
    category_of = {}
    names = {}
    for category in ("foreign_inst", "local_inst", "retail"):
        for code, name in broker_map.get(category, {}).items():
            category_of[code] = category
            names[code] = name

    rows = []
    for broker in entry["brokers"]:
        category = category_of.get(broker["code"], "unknown")
        rows.append(
            {
                **broker,
                "category": category,
                "name": names.get(broker["code"], ""),
            }
        )
    buyers = sorted([r for r in rows if r["net_bn"] > 0], key=lambda r: -r["net_bn"])
    sellers = sorted([r for r in rows if r["net_bn"] < 0], key=lambda r: r["net_bn"])

    net_by_category = {key: 0.0 for key in CATEGORY_LABELS}
    for row in rows:
        net_by_category[row["category"]] += row["net_bn"]
    institutional_net = net_by_category["foreign_inst"] + net_by_category["local_inst"]
    retail_net = net_by_category["retail"]

    if rows:
        if institutional_net > 0 and retail_net < 0:
            signal = "ACCUMULATION"
            meaning = "Institutions net buying while retail sells: shares moving into stronger hands."
        elif institutional_net < 0 and retail_net > 0:
            signal = "DISTRIBUTION"
            meaning = "Institutions net selling into retail buying: a warning sign."
        elif institutional_net > 0:
            signal = "ACCUMULATION-LEANING"
            meaning = "Institutions net buying; retail flow not clearly opposite."
        elif institutional_net < 0:
            signal = "DISTRIBUTION-LEANING"
            meaning = "Institutions net selling; retail flow not clearly opposite."
        else:
            signal = "NEUTRAL"
            meaning = "No clear institutional direction in the entered brokers."
    else:
        signal = "NO BROKER DATA"
        meaning = "Only net foreign was entered."

    buyer_avg = weighted_average_price(buyers)
    seller_avg = weighted_average_price(sellers)
    notes = []
    if buyer_avg:
        relation = "above" if buyer_avg > close_price else "below"
        notes.append(
            f"Top buyers' average price {buyer_avg:,.0f} is {relation} the close {close_price:,.0f}"
            + (
                ": they are under water and may defend this level."
                if buyer_avg > close_price
                else ": they are in profit; this area often acts as support."
            )
        )
    total_buy = sum(r["net_bn"] for r in buyers)
    if buyers and total_buy > 0:
        share = buyers[0]["net_bn"] / total_buy * 100
        if share >= 50:
            notes.append(
                f"Buying is concentrated: {buyers[0]['code']} did {share:.0f}% of the top-buyer value."
            )
    if entry.get("net_foreign") is not None:
        side = "net BUY" if entry["net_foreign"] > 0 else "net SELL"
        notes.append(f"Foreign flow: {side} Rp{abs(entry['net_foreign']):,.1f}bn.")

    return {
        "net_foreign": entry.get("net_foreign"),
        "rows": rows,
        "buyers": buyers,
        "sellers": sellers,
        "net_by_category": net_by_category,
        "institutional_net": institutional_net,
        "retail_net": retail_net,
        "signal": signal,
        "meaning": meaning,
        "buyer_avg": buyer_avg,
        "seller_avg": seller_avg,
        "notes": notes,
    }


def save_broksum(trade_date: pd.Timestamp, analysis: dict) -> None:
    date_text = trade_date.strftime("%Y-%m-%d")
    summary_row = pd.DataFrame(
        {
            "Date": [date_text],
            "NetForeignBn": [analysis["net_foreign"]],
            "InstitutionalNetBn": [round(analysis["institutional_net"], 2)],
            "RetailNetBn": [round(analysis["retail_net"], 2)],
            "Signal": [analysis["signal"]],
            "TopBuyer": [analysis["buyers"][0]["code"] if analysis["buyers"] else ""],
            "TopSeller": [analysis["sellers"][0]["code"] if analysis["sellers"] else ""],
            "BuyerAvg": [round(analysis["buyer_avg"]) if analysis["buyer_avg"] else None],
            "SellerAvg": [round(analysis["seller_avg"]) if analysis["seller_avg"] else None],
        }
    )
    if BROKSUM_FILE.exists():
        history = pd.read_csv(BROKSUM_FILE)
        history = pd.concat([history[history["Date"] != date_text], summary_row], ignore_index=True)
    else:
        history = summary_row
    history.sort_values("Date").to_csv(BROKSUM_FILE, index=False)

    detail = pd.DataFrame(
        [
            {
                "Date": date_text,
                "Code": row["code"],
                "NetBn": row["net_bn"],
                "AvgPrice": row["avg_price"],
                "Category": row["category"],
            }
            for row in analysis["rows"]
        ]
    )
    if len(detail):
        if BROKSUM_DETAIL_FILE.exists():
            old = pd.read_csv(BROKSUM_DETAIL_FILE)
            detail = pd.concat([old[old["Date"] != date_text], detail], ignore_index=True)
        detail.to_csv(BROKSUM_DETAIL_FILE, index=False)

    if analysis["net_foreign"] is not None:
        flow = load_foreign_flow()
        flow = flow[flow["Date"] != trade_date] if len(flow) else flow
        new_row = pd.DataFrame({"Date": [trade_date], "NetForeignBn": [analysis["net_foreign"]]})
        flow = pd.concat([flow, new_row], ignore_index=True).sort_values("Date")
        flow.to_csv(FOREIGN_FILE, index=False, date_format="%Y-%m-%d")


def save_ihsg_flow(trade_date: pd.Timestamp, value: float) -> None:
    if IHSG_FLOW_FILE.exists():
        flow = pd.read_csv(IHSG_FLOW_FILE, parse_dates=["Date"])
        flow = flow[flow["Date"] != trade_date]
    else:
        flow = pd.DataFrame(columns=["Date", "NetForeignBn"])
    new_row = pd.DataFrame({"Date": [trade_date], "NetForeignBn": [value]})
    flow = pd.concat([flow, new_row], ignore_index=True).sort_values("Date")
    flow.to_csv(IHSG_FLOW_FILE, index=False, date_format="%Y-%m-%d")


def broksum_history_summary(days: int = 5) -> dict:
    if not BROKSUM_FILE.exists():
        return {}
    history = pd.read_csv(BROKSUM_FILE).sort_values("Date").tail(days)
    if history.empty:
        return {}
    return {
        "days": len(history),
        "foreign_sum": float(history["NetForeignBn"].fillna(0).sum()),
        "institutional_sum": float(history["InstitutionalNetBn"].fillna(0).sum()),
        "retail_sum": float(history["RetailNetBn"].fillna(0).sum()),
        "signals": history["Signal"].tolist(),
        "dates": history["Date"].tolist(),
    }


# ---------------------------------------------------------------- foreign flow
def load_foreign_flow() -> pd.DataFrame:
    if FOREIGN_FILE.exists():
        flow = pd.read_csv(FOREIGN_FILE, parse_dates=["Date"])
    else:
        flow = pd.DataFrame(columns=["Date", "NetForeignBn"])
    return flow


# ---------------------------------------------------------------- alerts
def evaluate_alerts(prices: pd.DataFrame, flow: pd.DataFrame) -> dict:
    """Return {alert_key: message} for every trigger active today."""
    today = prices.iloc[-1]
    alerts = {}
    if not HOLDING_BMRI:
        # Position closed: no stop / target / plan alerts for BMRI
        return alerts

    if today["Close"] < STOP_CLOSE:
        alerts["stop_close"] = (
            f"STOP: closed {today['Close']:,.0f} < {STOP_CLOSE:,}. "
            "Plan says EXIT the position (rebound failed; 3,460 / 3,200 next)."
        )
    elif today["Low"] <= STOP_CLOSE:
        alerts["stop_tested"] = (
            f"Stop level tested intraday (low {today['Low']:,.0f}) but closed "
            f"{today['Close']:,.0f} above {STOP_CLOSE:,}. Holding is still within plan; "
            "watch for a reversal candle."
        )
    elif today["Close"] <= CAUTION_CLOSE:
        alerts["near_stop"] = (
            f"Caution: close {today['Close']:,.0f} is within Rp{CAUTION_CLOSE - STOP_CLOSE} "
            f"of the {STOP_CLOSE:,} stop."
        )

    if today["Close"] > BREAKOUT_LEVEL and today["Volume"] > today["Vol20"]:
        alerts["breakout_confirmed"] = (
            f"BREAKOUT CONFIRMED: close {today['Close']:,.0f} > {BREAKOUT_LEVEL:,} on volume "
            f"{today['Volume'] / 1e6:,.0f}M (> 20d avg {today['Vol20'] / 1e6:,.0f}M). "
            f"Hold the rest toward {TARGET_2:,} then {TARGET_3:,}; trail the stop."
        )
    elif today["High"] >= BREAKOUT_LEVEL:
        alerts["breakout_touch"] = (
            f"Touched {BREAKOUT_LEVEL:,} (high {today['High']:,.0f}) but NOT confirmed "
            "(needs a close above it with volume above the 20-day average). "
            "Expect resistance; stick to partial profit-taking."
        )

    if today["High"] >= TARGET_1:
        alerts["target_1"] = (
            f"TARGET 1 hit: high {today['High']:,.0f} >= {TARGET_1:,}. "
            f"Plan: sell ~half, move the stop on the rest to breakeven ({BREAKEVEN_PRICE:,})."
        )

    if today["High"] >= TARGET_2:
        alerts["target_2"] = (
            f"TARGET 2 hit: high {today['High']:,.0f} >= {TARGET_2:,} (MA50 area). "
            "Plan: take more profit / tighten the trailing stop."
        )

    if len(flow):
        flow_sorted = flow.sort_values("Date")
        latest = flow_sorted.iloc[-1]
        if latest["Date"] == prices.index[-1] and latest["NetForeignBn"] > 0:
            previous = flow_sorted.iloc[:-1]
            was_selling = len(previous) == 0 or previous.iloc[-1]["NetForeignBn"] <= 0
            if was_selling:
                alerts["foreign_turned_buy"] = (
                    f"FOREIGN FLOW turned NET BUY: +Rp{latest['NetForeignBn']:,.1f}bn today. "
                    "Strongest confirmation for the pullback-hold or breakout setups."
                )
            last_three = flow_sorted.tail(3)
            if len(last_three) == 3 and (last_three["NetForeignBn"] > 0).all():
                alerts["foreign_3day_buy"] = (
                    "Foreigners net BUY 3 days in a row "
                    f"(total +Rp{last_three['NetForeignBn'].sum():,.1f}bn)."
                )

    if BROKSUM_FILE.exists():
        history = pd.read_csv(BROKSUM_FILE).sort_values("Date").tail(3)
        if (
            len(history) == 3
            and history["Date"].iloc[-1] == prices.index[-1].strftime("%Y-%m-%d")
        ):
            signals = history["Signal"].astype(str)
            if signals.str.startswith("ACCUMULATION").all():
                alerts["accumulation_3day"] = (
                    "BROKSUM: institutional ACCUMULATION 3 sessions in a row. "
                    "Supports holding; a close above 3,980 would confirm."
                )
            elif signals.str.startswith("DISTRIBUTION").all():
                alerts["distribution_3day"] = (
                    "BROKSUM: institutional DISTRIBUTION 3 sessions in a row. "
                    "Respect the 3,600 stop; consider reducing on strength."
                )
    return alerts


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"last_trade_date": None, "active_alerts": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def show_popup(title: str, message: str) -> None:
    if sys.platform != "win32":
        return
    MB_ICONINFORMATION = 0x40
    MB_TOPMOST = 0x40000
    MB_SETFOREGROUND = 0x10000
    ctypes.windll.user32.MessageBoxW(
        0, message, title, MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND
    )


# ---------------------------------------------------------------- notifications
def load_config() -> dict:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        return json.loads(json.dumps(DEFAULT_CONFIG))
    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in config.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def send_email(settings: dict, subject: str, body: str, attachment: Path = None) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings["sender"]
    message["To"] = ", ".join(settings["recipients"])
    message.set_content(body)
    if attachment is not None and settings.get("attach_chart") and attachment.exists():
        message.add_attachment(
            attachment.read_bytes(),
            maintype="image",
            subtype="png",
            filename=attachment.name,
        )
    port = int(settings["smtp_port"])
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(settings["smtp_server"], port, context=context, timeout=30) as server:
            server.login(settings["sender"], settings["app_password"])
            server.send_message(message)
    else:
        with smtplib.SMTP(settings["smtp_server"], port, timeout=30) as server:
            server.starttls(context=context)
            server.login(settings["sender"], settings["app_password"])
            server.send_message(message)


def send_whatsapp(settings: dict, body: str) -> None:
    provider = settings.get("provider", "callmebot").lower()
    if provider == "callmebot":
        query = urllib.parse.urlencode(
            {
                "phone": settings["phone"],
                "text": body,
                "apikey": settings["callmebot_apikey"],
            }
        )
        request = urllib.request.Request(
            f"https://api.callmebot.com/whatsapp.php?{query}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
    elif provider == "fonnte":
        form = urllib.parse.urlencode(
            {"target": settings["phone"].lstrip("+"), "message": body}
        ).encode()
        request = urllib.request.Request(
            "https://api.fonnte.com/send",
            data=form,
            headers={"Authorization": settings["fonnte_token"]},
        )
    else:
        raise ValueError(f"Unknown WhatsApp provider: {provider}")
    with urllib.request.urlopen(request, timeout=30) as response:
        reply = response.read().decode("utf-8", "replace")
        status_code = response.status
    if status_code >= 400 or "error" in reply.lower()[:200]:
        raise RuntimeError(f"WhatsApp gateway replied: {reply[:200]}")


def notify(config: dict, subject: str, body: str, attach_chart: bool = True) -> list:
    """Send through every enabled channel. Returns a list of status lines."""
    status = []
    email_settings = config["email"]
    if email_settings.get("enabled"):
        try:
            send_email(email_settings, subject, body, CHART_FILE if attach_chart else None)
            status.append("email: sent")
        except Exception as error:
            status.append(f"email: FAILED {error!r}")

    whatsapp_settings = config["whatsapp"]
    if whatsapp_settings.get("enabled"):
        try:
            send_whatsapp(whatsapp_settings, f"*{subject}*\n\n{body}")
            status.append("whatsapp: sent")
        except Exception as error:
            status.append(f"whatsapp: FAILED {error!r}")

    remote_ok = any(line.endswith("sent") for line in status)
    remote_failed = any("FAILED" in line for line in status)
    if config["popup"].get("enabled") or remote_failed or not remote_ok:
        popup_body = body
        if remote_failed:
            popup_body += "\n\n" + "\n".join(status)
        show_popup(subject, popup_body)
        status.append("popup: shown")
    return status


# ---------------------------------------------------------------- chart
def draw_chart(prices: pd.DataFrame, flow: pd.DataFrame) -> None:
    view = prices.iloc[-150:]
    fig, (ax_price, ax_volume, ax_rsi, ax_macd) = plt.subplots(
        4,
        1,
        figsize=(13, 11),
        sharex=True,
        gridspec_kw={"height_ratios": [4, 1.3, 1.1, 1.1]},
    )

    rising = view["Close"] >= view["Open"]
    colors = np.where(rising, "#26a69a", "#ef5350")
    ax_price.vlines(view.index, view["Low"], view["High"], color=colors, linewidth=0.8)
    ax_price.bar(
        view.index,
        (view["Close"] - view["Open"]).abs().clip(lower=5),
        bottom=view[["Open", "Close"]].min(axis=1),
        color=colors,
        width=0.7,
    )
    for column, color in (("MA20", "orange"), ("MA50", "royalblue"), ("MA200", "purple")):
        ax_price.plot(view.index, view[column], color=color, linewidth=1.2, label=column)

    if HOLDING_BMRI:
        plan_levels = [
            (TARGET_2, f"T2 {TARGET_2:,}", "green", ":", 12),
            (BREAKOUT_LEVEL, f"Breakout {BREAKOUT_LEVEL:,}", "green", "--", 12),
            (TARGET_1, f"T1 {TARGET_1:,}", "green", "-", -45),
            (AVERAGE_PRICE, f"Avg {AVERAGE_PRICE:,}", "black", "-.", 12),
            (STOP_CLOSE, f"Stop (close) {STOP_CLOSE:,}", "red", "-", 12),
        ]
    else:
        plan_levels = [
            (BREAKOUT_LEVEL, f"Resistance {BREAKOUT_LEVEL:,}", "green", "--", 12),
            (STOP_CLOSE, f"Support {STOP_CLOSE:,}", "red", "--", 12),
        ]
    for level, label, color, style, label_offset in plan_levels:
        ax_price.axhline(level, color=color, linestyle=style, linewidth=0.9, alpha=0.8)
        ax_price.text(view.index[1], level + label_offset, label, fontsize=8, color=color)

    last = view.iloc[-1]
    ax_price.set_title(
        f"{TICKER} daily — last {view.index[-1]:%d %b %Y}: close {last['Close']:,.0f} "
        + (f"({(last['Close'] / AVERAGE_PRICE - 1) * 100:+.1f}% vs avg {AVERAGE_PRICE:,})"
           if HOLDING_BMRI else "(watch only)")
    )
    ax_price.legend(loc="upper right")
    ax_price.grid(alpha=0.3)

    ax_volume.bar(view.index, view["Volume"] / 1e6, color=colors, width=0.7)
    ax_volume.plot(view.index, view["Vol20"] / 1e6, color="black", linewidth=1, label="Vol20")
    ax_volume.set_ylabel("Vol (M sh)")
    if len(flow):
        recent_flow = flow[flow["Date"] >= view.index[0]]
        if len(recent_flow):
            ax_flow = ax_volume.twinx()
            ax_flow.plot(
                recent_flow["Date"],
                recent_flow["NetForeignBn"],
                "o-",
                color="navy",
                markersize=3,
                label="Net foreign (Rp bn)",
            )
            ax_flow.axhline(0, color="navy", linewidth=0.5)
            ax_flow.set_ylabel("Foreign Rp bn", color="navy")

    ax_rsi.plot(view.index, view["RSI14"], color="teal")
    for level, color in ((70, "red"), (50, "gray"), (30, "green")):
        ax_rsi.axhline(level, color=color, linewidth=0.6)
    ax_rsi.set_ylabel("RSI14")

    ax_macd.plot(view.index, view["MACD"], color="black", label="MACD")
    ax_macd.plot(view.index, view["Signal"], color="orange", label="Signal")
    ax_macd.bar(
        view.index,
        view["Hist"],
        color=np.where(view["Hist"] >= 0, "#26a69a", "#ef5350"),
        width=0.7,
    )
    ax_macd.legend(loc="lower left", fontsize=8)
    ax_macd.set_ylabel("MACD")

    fig.tight_layout()
    fig.savefig(CHART_FILE, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------- HTML reports
HTML_STYLE = """
body { font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f8; color: #1f2933;
       max-width: 1100px; margin: 24px auto; padding: 0 16px; }
h1 { margin-bottom: 4px; } .sub { color: #52606d; margin-top: 0; }
.card { background: #fff; border-radius: 10px; padding: 16px 20px; margin: 14px 0;
        box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card h2 { margin-top: 0; font-size: 18px; }
.alert { border-left: 6px solid #d64545; } .ok { border-left: 6px solid #3ebd93; }
.warn { border-left: 6px solid #f0b429; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #e4e7eb; }
th { background: #f0f4f8; } .pos { color: #0e7c5a; font-weight: 600; }
.neg { color: #c62828; font-weight: 600; } .muted { color: #7b8794; font-size: 13px; }
.big { font-size: 26px; font-weight: 700; } img { max-width: 100%; border-radius: 6px; }
li { margin: 4px 0; }
"""


def signed_class(value) -> str:
    if value is None:
        return ""
    return "pos" if value > 0 else "neg" if value < 0 else ""


def render_html(title: str, subtitle: str, sections: list, chart_path: Path = None) -> str:
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{html.escape(title)}</title><style>{HTML_STYLE}</style></head><body>",
        f"<h1>{html.escape(title)}</h1><p class='sub'>{html.escape(subtitle)}</p>",
    ]
    for heading, body, css_class in sections:
        parts.append(f"<div class='card {css_class}'><h2>{html.escape(heading)}</h2>{body}</div>")
    if chart_path is not None and chart_path.exists():
        encoded = base64.b64encode(chart_path.read_bytes()).decode()
        parts.append(
            "<div class='card'><h2>Chart</h2>"
            f"<img src='data:image/png;base64,{encoded}' alt='chart'></div>"
        )
    parts.append(
        "<p class='muted'>Automated, rule-based analysis for education and planning. "
        "Not financial advice. Prices from Yahoo Finance (unofficial; verify with your broker). "
        "Broksum data entered manually.</p></body></html>"
    )
    return "".join(parts)


def bullet_list(items: list) -> str:
    return "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in items) + "</ul>"


def broksum_html(analysis: dict, history: dict) -> str:
    if analysis is None:
        body = "<p>Not entered today.</p>"
    else:
        rows_html = []
        for row in analysis["buyers"] + analysis["sellers"]:
            avg_text = f"{row['avg_price']:,.0f}" if row["avg_price"] else ""
            rows_html.append(
                "<tr>"
                f"<td><b>{row['code']}</b></td>"
                f"<td>{html.escape(row['name'])}</td>"
                f"<td>{CATEGORY_LABELS[row['category']]}</td>"
                f"<td class='{signed_class(row['net_bn'])}'>{row['net_bn']:+,.1f}</td>"
                f"<td>{avg_text}</td>"
                "</tr>"
            )
        category_cells = "".join(
            f"<td class='{signed_class(analysis['net_by_category'][key])}'>"
            f"{analysis['net_by_category'][key]:+,.1f}</td>"
            for key in CATEGORY_LABELS
        )
        foreign = analysis["net_foreign"]
        body = (
            f"<p class='big'>{html.escape(analysis['signal'])}</p>"
            f"<p>{html.escape(analysis['meaning'])}</p>"
            + bullet_list(analysis["notes"])
            + (
                "<table><tr><th>Broker</th><th>Name</th><th>Type</th><th>Net (Rp bn)</th>"
                "<th>Avg price</th></tr>" + "".join(rows_html) + "</table>"
                if rows_html
                else ""
            )
            + "<p><b>Net by type (entered brokers, Rp bn)</b></p><table><tr>"
            + "".join(f"<th>{label}</th>" for label in CATEGORY_LABELS.values())
            + f"</tr><tr>{category_cells}</tr></table>"
            + (
                f"<p>Net foreign: <span class='{signed_class(foreign)}'>Rp{foreign:+,.1f}bn</span></p>"
                if foreign is not None
                else ""
            )
        )
    if history:
        body += (
            f"<p><b>Last {history['days']} entered sessions:</b> "
            f"foreign <span class='{signed_class(history['foreign_sum'])}'>Rp{history['foreign_sum']:+,.1f}bn</span>, "
            f"institutions <span class='{signed_class(history['institutional_sum'])}'>Rp{history['institutional_sum']:+,.1f}bn</span>, "
            f"retail <span class='{signed_class(history['retail_sum'])}'>Rp{history['retail_sum']:+,.1f}bn</span>"
            f"<br><span class='muted'>Signals: {html.escape(' → '.join(history['signals']))}</span></p>"
        )
    return body


def open_report(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))


# ---------------------------------------------------------------- text report / log
def build_text_report(prices, ta, analysis, alerts, new_keys) -> str:
    today = prices.iloc[-1]
    previous = prices.iloc[-2]
    trade_date = prices.index[-1]
    lines = [
        f"BMRI close report — {trade_date:%a %d %b %Y} (run {datetime.now():%d %b %H:%M})",
        f"Close {today['Close']:,.0f} ({(today['Close'] / previous['Close'] - 1) * 100:+.2f}%)  "
        f"H {today['High']:,.0f}  L {today['Low']:,.0f}  "
        f"Vol {today['Volume'] / 1e6:,.0f}M ({today['Volume'] / today['Vol20']:.1f}x avg)",
        (f"Position vs breakeven {BREAKEVEN_PRICE:,}: {(today['Close'] / BREAKEVEN_PRICE - 1) * 100:+.1f}%"
         if HOLDING_BMRI else "Position: none (watch only)"),
        f"Bias: {ta['bias']} | Verdict: {ta['verdict']}",
        f"Trend: {ta['trend']}",
        f"Momentum: {ta['momentum']}",
        f"Volume: {ta['volume']}",
        f"Candle: {ta['candle']}",
    ]
    if analysis:
        lines.append(f"Broksum: {analysis['signal']}: {analysis['meaning']}")
    else:
        lines.append("Broksum: not entered")
    if alerts:
        lines.append("ALERTS:")
        for key, message in alerts.items():
            lines.append(f" {'NEW ' if key in new_keys else '    '}- {message}")
    else:
        lines.append("No plan triggers today.")
    return "\n".join(lines)


def append_log(prices: pd.DataFrame, alerts: dict, ta: dict) -> None:
    today = prices.iloc[-1]
    row = pd.DataFrame(
        {
            "Date": [prices.index[-1].strftime("%Y-%m-%d")],
            "Close": [today["Close"]],
            "High": [today["High"]],
            "Low": [today["Low"]],
            "Volume": [int(today["Volume"])],
            "RSI14": [round(today["RSI14"], 1)],
            "Bias": [ta["bias"]],
            "Alerts": ["; ".join(alerts.keys())],
        }
    )
    if LOG_FILE.exists():
        log = pd.read_csv(LOG_FILE)
        log = pd.concat([log[log["Date"] != row["Date"][0]], row], ignore_index=True)
    else:
        log = row
    log.to_csv(LOG_FILE, index=False)


# ---------------------------------------------------------------- main
def fetch_final_prices(wait_allowed: bool):
    """Fetch prices; between 16:00 and 17:00 wait (up to ~15 min) for the final close."""
    prices, last_trade = fetch_prices()
    for _ in range(3):
        now = datetime.now()
        is_final = last_trade.date() == now.date() and (last_trade.hour, last_trade.minute) >= (15, 50)
        if is_final or not wait_allowed or not (16 <= now.hour < 17):
            break
        time.sleep(300)
        prices, last_trade = fetch_prices()
    return prices, last_trade


def legacy_main() -> None:
    # Console windows on Windows may not be UTF-8: never crash on symbols like → or –
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ask", action="store_true", help="skip the broksum form")
    parser.add_argument("--no-popup", action="store_true", help="never show alert popups")
    parser.add_argument("--no-open", action="store_true", help="don't open the HTML report")
    parser.add_argument("--no-wait", action="store_true", help="don't wait for the final close")
    parser.add_argument("--test-alert", action="store_true", help="send a test notification")
    parser.add_argument("--legacy", action="store_true", help="old BMRI-only report")
    args = parser.parse_args()
    config = load_config()
    if args.no_popup:
        config["popup"]["enabled"] = False

    if args.test_alert:
        status = notify(
            config,
            "BMRI monitor — test alert",
            "If you can read this, BMRI alerts are working.",
            attach_chart=True,
        )
        print("\n".join(status) or "No channel enabled in alert_config.json")
        return

    try:
        prices, last_trade = fetch_final_prices(wait_allowed=not args.no_wait)
        prices = add_indicators(prices)
    except Exception as error:
        message = f"Could not download BMRI data: {error!r}"
        REPORT_FILE.write_text(message, encoding="utf-8")
        print(message)
        if not args.no_popup:
            notify(config, "BMRI check failed", message, attach_chart=False)
        return

    trade_date = prices.index[-1]
    now = datetime.now()
    is_today = trade_date.date() == now.date()
    is_final = is_today and (last_trade.hour, last_trade.minute) >= (15, 50)
    today_bar = prices.iloc[-1]

    # Broksum: only ask once per session, after the close
    analysis = None
    broker_map = load_broker_map()
    already_entered = False
    if BROKSUM_FILE.exists():
        already_entered = trade_date.strftime("%Y-%m-%d") in set(pd.read_csv(BROKSUM_FILE)["Date"])
    if is_today and not args.no_ask and not already_entered and now.hour >= 15:
        entry = ask_broksum(trade_date.strftime("%a %d %b %Y"), today_bar["Close"])
        if entry is not None:
            if entry.get("ihsg_net_foreign") is not None:
                save_ihsg_flow(trade_date, entry["ihsg_net_foreign"])
            if entry["brokers"] or entry["net_foreign"] is not None:
                analysis = analyse_broksum(entry, broker_map, today_bar["Close"])
                save_broksum(trade_date, analysis)
    elif already_entered and BROKSUM_DETAIL_FILE.exists():
        detail = pd.read_csv(BROKSUM_DETAIL_FILE)
        detail = detail[detail["Date"] == trade_date.strftime("%Y-%m-%d")]
        history_row = pd.read_csv(BROKSUM_FILE).set_index("Date").loc[trade_date.strftime("%Y-%m-%d")]
        entry = {
            "net_foreign": None if pd.isna(history_row["NetForeignBn"]) else float(history_row["NetForeignBn"]),
            "brokers": [
                {
                    "code": row["Code"],
                    "net_bn": float(row["NetBn"]),
                    "avg_price": None if pd.isna(row["AvgPrice"]) else float(row["AvgPrice"]),
                }
                for _, row in detail.iterrows()
            ],
        }
        analysis = analyse_broksum(entry, broker_map, today_bar["Close"])

    flow = load_foreign_flow()
    ta = technical_analysis(prices)
    alerts = evaluate_alerts(prices, flow)

    # Big 4 banks: watchlist analysis + entry alerts for BBCA / BBRI / BBNI
    bank_results = None
    bank_error = None
    try:
        import big_banks

        bank_results = big_banks.run()
        if is_today:
            alerts.update(big_banks.entry_alerts(bank_results))
    except Exception as error:
        bank_error = error

    tebe_summary = None
    try:
        import tebe_watch

        tebe_summary = tebe_watch.run()
        if is_final:
            alerts.update(tebe_summary["alerts"])
    except Exception as error:
        tebe_summary = {"error": repr(error)}

    ihsg_summary = None
    try:
        import ihsg_analysis

        ihsg_summary = ihsg_analysis.run(bank_results)
        if is_final:
            alerts.update(ihsg_analysis.ihsg_alerts(ihsg_summary))
    except Exception as error:
        ihsg_summary = {"error": repr(error)}

    # Pop up only for alerts that were not already active on the previous trading day
    state = load_state()
    if state.get("last_trade_date") == str(trade_date.date()):
        previously_active = set(state.get("previous_active_alerts", []))
    else:
        previously_active = set(state.get("active_alerts", []))
        state["previous_active_alerts"] = state.get("active_alerts", [])
    trade_day = str(trade_date.date())
    notified = state.get("notified", {})
    already_sent_today = set(notified.get("keys", [])) if notified.get("date") == trade_day else set()
    new_keys = [
        key for key in alerts if key not in previously_active and key not in already_sent_today
    ]
    if is_today:
        state["notified"] = {"date": trade_day, "keys": sorted(already_sent_today | set(new_keys))}
    state["last_trade_date"] = str(trade_date.date())
    state["active_alerts"] = list(alerts.keys())
    save_state(state)

    draw_chart(prices, flow)
    history = broksum_history_summary()
    text_report = build_text_report(prices, ta, analysis, alerts, new_keys)
    if not is_today:
        text_report = "(No new session today — showing last trading day.)\n" + text_report
    elif not is_final:
        text_report = "(Intraday run — today's bar is not final yet.)\n" + text_report

    # ---- HTML report
    previous_close = prices["Close"].iloc[-2]
    change_pct = (today_bar["Close"] / previous_close - 1) * 100
    status_note = "" if is_final else " · INTRADAY (not final)"
    headline = (
        f"<p class='big'>{today_bar['Close']:,.0f} "
        f"<span class='{signed_class(change_pct)}'>{change_pct:+.2f}%</span></p>"
        f"<p>H {today_bar['High']:,.0f} · L {today_bar['Low']:,.0f} · "
        f"Vol {today_bar['Volume'] / 1e6:,.0f}M ({today_bar['Volume'] / today_bar['Vol20']:.1f}× avg) · "
        f"ATR {ta['atr']:,}</p>"
        + (
            f"<p>Your position: avg {AVERAGE_PRICE:,} → "
            f"<span class='{signed_class(today_bar['Close'] - BREAKEVEN_PRICE)}'>"
            f"{(today_bar['Close'] / BREAKEVEN_PRICE - 1) * 100:+.1f}% vs breakeven {BREAKEVEN_PRICE:,}</span></p>"
            if HOLDING_BMRI else "<p>Position: none (watch only).</p>"
        )
        + 
        f"<p><b>Verdict:</b> {html.escape(ta['verdict'])}<br><b>Technical bias:</b> {ta['bias']} "
        f"(score {ta['score']:+d})</p>"
    )
    sections = [("Today", headline, "alert" if "stop_close" in alerts else "ok")]
    if alerts:
        alert_items = [("NEW: " if key in new_keys else "") + message for key, message in alerts.items()]
        sections.append(("Plan alerts", bullet_list(alert_items), "alert"))
    sections.append(
        (
            "Technical analysis",
            bullet_list(
                [
                    f"Trend: {ta['trend']}",
                    f"Momentum: {ta['momentum']}",
                    f"Volume: {ta['volume']}",
                    f"Candle: {ta['candle']}",
                ]
            ),
            "",
        )
    )
    levels_rows = "".join(
        f"<tr><td>Resistance</td><td>{value:,}</td><td>{html.escape(name)}</td>"
        f"<td>{value - today_bar['Close']:+,.0f}</td></tr>"
        for value, name in reversed(ta["resistances"])
    ) + "".join(
        f"<tr><td>Support</td><td>{value:,}</td><td>{html.escape(name)}</td>"
        f"<td>{value - today_bar['Close']:+,.0f}</td></tr>"
        for value, name in ta["supports"]
    )
    sections.append(
        (
            "Key levels",
            "<table><tr><th></th><th>Level</th><th>What</th><th>Distance</th></tr>"
            + levels_rows
            + "</table>",
            "",
        )
    )
    broksum_class = ""
    if analysis is not None:
        if analysis["signal"].startswith("ACCUMULATION"):
            broksum_class = "ok"
        elif analysis["signal"].startswith("DISTRIBUTION"):
            broksum_class = "warn"
    sections.append(("Broker summary (broksum)", broksum_html(analysis, history), broksum_class))

    # IHSG market direction
    if ihsg_summary and "error" not in ihsg_summary:
        sections.append(("IHSG (market)", ihsg_analysis.section_html(ihsg_summary), ""))
        ihsg_outlook = ihsg_summary["outlook"]
        text_report += (
            f"\n\nIHSG {ihsg_summary['close']:,.0f} ({big_banks.fmt_pct(ihsg_summary['change_1d'])}): "
            f"{ihsg_outlook['label']} ({ihsg_outlook['score']:+d}) - {ihsg_outlook['expectation']}"
        )
    else:
        sections.append(("IHSG (market)", f"<p>Unavailable: {html.escape(str((ihsg_summary or {}).get('error')))}</p>", "warn"))

    # TEBE speculative watch
    if tebe_summary and "error" not in tebe_summary:
        sections.append(("TEBE (speculative watch)", tebe_watch.section_html(tebe_summary), "warn"))
        text_report += (
            f"\n\nTEBE {tebe_summary['close']:,.0f} ({tebe_summary['change_1d']:+.1f}%): "
            f"{tebe_summary['verdict']}"
        )
    else:
        sections.append(("TEBE (speculative watch)", f"<p>Unavailable: {html.escape(str((tebe_summary or {}).get('error')))}</p>", "warn"))

    # Macro: Gold, Brent, DXY
    try:
        import macro_levels

        macro_summary = macro_levels.run()
        sections.append(("Macro: Gold · Brent · DXY", macro_levels.section_html(macro_summary), ""))
        text_report += f"\n\nMACRO: {macro_summary['overall']}\n" + "\n".join(
            f" {a['name']}: {macro_levels.fmt(a['close'], a['decimals'])} ({a['change_1d']:+.2f}%, "
            f"5D {a['change_5d']:+.2f}%) {a['trend']} | S {macro_levels.level_text(a['supports'], a['decimals'])}"
            f" | R {macro_levels.level_text(a['resistances'], a['decimals'])}"
            for a in macro_summary["assets"]
        )
    except Exception as error:
        sections.append(("Macro: Gold · Brent · DXY", f"<p>Unavailable: {html.escape(repr(error))}</p>", "warn"))

    # Big 4 banks watchlist + entry setups
    if bank_results:
        sections.append(("Big 4 banks", big_banks.html_section(bank_results), ""))
        text_report += "\n\nBIG 4 BANKS:\n" + "\n".join(
            f" {r['code']} {r['close']:,.0f} ({big_banks.fmt_pct(r['change_1d'])}) {r['bias']}"
            f" | {', '.join(r['signals']) or 'no new signal'} | watch: {r['watch']}"
            for r in bank_results
        )
        sector = big_banks.sector_outlook(bank_results)
        text_report += f"\nDIRECTION: {sector.get('label', '')}\n" + "\n".join(
            f" {r['code']}: {r['outlook']['label']} ({r['outlook']['score']:+d}) - {r['outlook']['expectation']}"
            for r in bank_results
        )
        text_report += "\nENTRY SETUPS:\n" + "\n".join(
            f" {row['code']} {row['setup']}: {row['trigger']} | {row['status']}"
            for row in big_banks.entry_rows(bank_results)
        )
    else:
        sections.append(("Big 4 banks", f"<p>Unavailable today: {html.escape(repr(bank_error))}</p>", "warn"))

    REPORT_DIR.mkdir(exist_ok=True)
    report_html = render_html(
        f"BMRI close report — {trade_date:%a %d %b %Y}",
        f"Generated {now:%d %b %Y %H:%M} WIB{status_note}",
        sections,
        CHART_FILE,
    )
    dated_report = REPORT_DIR / f"close_{trade_date:%Y-%m-%d}.html"
    latest_report = REPORT_DIR / "latest_close.html"
    dated_report.write_text(report_html, encoding="utf-8")
    latest_report.write_text(report_html, encoding="utf-8")

    # ---- summary for the morning brief
    summary = {
        "date": trade_date.strftime("%Y-%m-%d"),
        "final": bool(is_final),
        "open": float(today_bar["Open"]),
        "high": float(today_bar["High"]),
        "low": float(today_bar["Low"]),
        "close": float(today_bar["Close"]),
        "previous_close": float(previous_close),
        "volume": int(today_bar["Volume"]),
        "vol20": float(today_bar["Vol20"]),
        "ta": ta,
        "broksum_signal": analysis["signal"] if analysis else None,
        "net_foreign": analysis["net_foreign"] if analysis else None,
        "alerts": list(alerts.values()),
        "alert_keys": list(alerts.keys()),
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    append_log(prices, alerts, ta)

    # Refresh the recap deck with today's data (never let a deck problem break the report)
    try:
        import make_recap_pptx

        deck_path = make_recap_pptx.build_deck()
        deck_note = f"Recap deck updated: {deck_path}"
    except Exception as error:
        deck_note = f"Recap deck NOT updated: {error!r}"

    # Refresh the dividend watchlist (Dividend Watchlist app) - never break the report
    try:
        import dividend_watchlist

        watchlist = dividend_watchlist.build()
        deck_note += f" | Dividend watchlist updated ({len(watchlist['stocks'])} stocks)"
        import market_context

        context = market_context.build()
        deck_note += f" | Market view updated ({context['regime']['label']})"
        import macro_ideas

        ideas_data = macro_ideas.build()
        deck_note += f" | Macro ideas: {len(ideas_data['ideas'])} ready, {len(ideas_data['watch'])} on trigger"
    except Exception as error:
        deck_note += f" | Dividend watchlist NOT updated: {error!r}"

    # Full IDX market snapshot (All Stocks page)
    try:
        import all_stocks

        all_stocks.build()
    except Exception as error:
        deck_note += f" | All-stocks snapshot NOT updated: {error!r}"

    # Portfolio: exit / target / flow alerts for your own holdings (popup / email)
    try:
        import portfolio

        portfolio_summary = portfolio.build()
        portfolio_messages = portfolio.new_alerts(portfolio_summary) if is_today else []
        if portfolio_messages:
            notify(config, "PORTFOLIO ALERT", "\n\n".join(portfolio_messages), attach_chart=False)
        deck_note += f" | Portfolio checked ({len(portfolio_messages)} new alerts)"
    except Exception as error:
        deck_note += f" | Portfolio NOT checked: {error!r}"

    if new_keys and is_today:
        alert_text = "\n\n".join(alerts[key] for key in new_keys)
        has_entry = any(key.endswith("_entry") for key in new_keys)
        has_ihsg = any(key.startswith("ihsg_") for key in new_keys)
        has_tebe = any(key.startswith("tebe_") for key in new_keys)
        if has_tebe and not (has_ihsg or has_entry):
            title = "TEBE ALERT"
        elif has_ihsg:
            title = "IHSG CALL ALERT"
        elif has_entry:
            title = "ENTRY / PLAN ALERT"
        else:
            title = "BMRI ALERT"
        if has_ihsg and ihsg_summary and "error" not in ihsg_summary:
            subject = (
                f"IHSG CALL ALERT — IHSG {ihsg_summary['close']:,.0f}: {ihsg_summary['outlook']['label']}"
            )
        else:
            subject = f"{title} — BMRI close {today_bar['Close']:,.0f}"
        status = notify(
            config,
            subject,
            f"{alert_text}\n\n----\n{text_report}",
        )
        text_report += "\n\nNotifications: " + "; ".join(status)
    elif config.get("send_daily_summary") and is_final:
        summary_config = json.loads(json.dumps(config))
        summary_config["popup"]["enabled"] = False
        status = notify(summary_config, f"BMRI daily — close {today_bar['Close']:,.0f}", text_report)
        text_report += "\n\nDaily summary: " + "; ".join(status)

    # Daily IHSG call by email / WhatsApp after the evening (broksum) run
    remote_enabled = config["email"].get("enabled") or config["whatsapp"].get("enabled")
    if (
        config.get("ihsg_daily_call", {}).get("enabled")
        and remote_enabled
        and is_final
        and now.hour >= 17
        and ihsg_summary
        and "error" not in ihsg_summary
    ):
        daily_config = json.loads(json.dumps(config))
        daily_config["popup"]["enabled"] = False
        status = notify(
            daily_config,
            f"IHSG daily call — {ihsg_summary['outlook']['label']}",
            ihsg_analysis.call_summary_text(ihsg_summary),
            attach_chart=False,
        )
        text_report += "\n\nIHSG daily call: " + "; ".join(status)

    REPORT_FILE.write_text(text_report, encoding="utf-8")
    print(text_report)
    print(f"\nHTML report: {dated_report}")
    print(deck_note)
    if not args.no_open:
        open_report(latest_report)


def main() -> None:
    """Closing report for YOUR holdings, queue plans and the market (replaces the BMRI-only report).

    16:20 task (--no-ask): full report, popup with the key points, opens the report.
    18:00 task (no flags):  rebuilds the report quietly (e.g. after you pasted broksum), popup only for new alerts.
    --legacy runs the old BMRI report.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if "--legacy" in sys.argv:
        legacy_main()
        return
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-ask", action="store_true", help="the 16:20 close run")
    parser.add_argument("--no-popup", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--open", action="store_true", help="open the report even on the evening run")
    parser.add_argument("--no-wait", action="store_true")
    parser.add_argument("--test-alert", action="store_true")
    args = parser.parse_args()
    config = load_config()
    if args.no_popup:
        config["popup"]["enabled"] = False
    if args.test_alert:
        print("\n".join(notify(config, "IDX monitor - test alert", "If you can read this, alerts work.", attach_chart=False)))
        return
    close_run = args.no_ask
    notes = []

    # refresh the data behind the app pages (never let one failure stop the report)
    for name, job in (("dividend watchlist", "dividend_watchlist"), ("macro ideas", "macro_ideas")):
        try:
            __import__(job).build()
        except Exception as error:
            notes.append(f"{name} NOT updated: {error!r}"[:150])

    import daily_reports
    import portfolio

    try:
        result = daily_reports.build_close()
    except Exception as error:
        message = f"Closing report failed: {error!r}"
        print(message)
        if close_run and not args.no_popup:
            notify(config, "Closing report failed", message, attach_chart=False)
        return

    new_alerts = []
    try:
        new_alerts = portfolio.new_alerts(portfolio.load_summary())
    except Exception as error:
        notes.append(f"portfolio alerts failed: {error!r}")
    is_trading_day = datetime.now().weekday() < 5
    if is_trading_day and not args.no_popup:
        if close_run:
            body = result["text"]
            if result["messages"] or new_alerts:
                body = "\n".join(["ACTION:"] + [f"- {m}" for m in result["messages"]] + [f"- {a}" for a in new_alerts]) + "\n\n" + body
            daily_reports.notify_once("close", "CLOSING REPORT - your stocks", body + "\n\n(Full report opened in your browser / app Reports page)")
        elif new_alerts:
            notify(config, "PORTFOLIO ALERT", "\n\n".join(new_alerts), attach_chart=False)
    print(result["text"])
    print("\n".join(result["messages"]))
    print("\n".join(notes))
    print(f"Report: {result['path']}")
    if (close_run and not args.no_open) or args.open:
        open_report(result["path"])


if __name__ == "__main__":
    main()

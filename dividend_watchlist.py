"""
Defensive dividend watchlist for big-cap IDX stocks.

For every stock in watchlist_universe.json it pulls ~5 years of daily prices and
dividends (Yahoo Finance, free) and computes:
  - trailing-12-month (TTM) dividend yield and 3-year average yield
  - dividend consistency (years paid out of the last 5 full years) and growth
  - stability: beta vs IHSG (1 year), annualised volatility, 1-year max drawdown
  - size: approximate market cap = price x shares outstanding (shares from the JSON)
  - timing: trend vs MA50 / MA200 and RSI -> a simple "when" tag
and combines them into a 0-100 "defensive dividend" quality score.

Results go to watchlist_summary.json (read by dividend_app.py).
Run manually:  python dividend_watchlist.py
Education only - dividends are never guaranteed; check the company's own announcements.
"""

import json
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

FOLDER = Path(__file__).resolve().parent
UNIVERSE_FILE = FOLDER / "watchlist_universe.json"
SUMMARY_FILE = FOLDER / "watchlist_summary.json"
JAKARTA = timezone(timedelta(hours=7))
INDEX_SYMBOL = "^JKSE"

CLASS_POINTS = {"Defensive": 10, "Semi-defensive": 6, "Cyclical": 2}
YIELD_FULL_MARKS = 8.0          # % yield that earns the full yield score
SPECIAL_DIVIDEND_RATIO = 1.5    # TTM yield this many times the 3y average = probably a one-off


# ---------------------------------------------------------------- data
def fetch_history(symbol: str, years: int = 7) -> tuple:
    """Daily OHLCV DataFrame and a dividend Series (index = ex-date) for a Yahoo symbol."""
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        f"?range={years}y&interval=1d&events=div"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)["chart"]["result"][0]

    quote = result["indicators"]["quote"][0]
    index = pd.to_datetime(result["timestamp"], unit="s", utc=True).tz_convert(JAKARTA).normalize().tz_localize(None)
    prices = pd.DataFrame(
        {
            "Open": quote["open"],
            "High": quote["high"],
            "Low": quote["low"],
            "Close": quote["close"],
            "Volume": quote["volume"],
        },
        index=index,
    ).dropna(subset=["Close"])
    prices = prices[~prices.index.duplicated(keep="last")]

    # Yahoo sometimes adds the latest daily bar late: use the live quote if it is newer
    meta = result["meta"]
    exchange_tz = timezone(timedelta(seconds=meta.get("gmtoffset", 0)))
    live_day = datetime.fromtimestamp(meta["regularMarketTime"], exchange_tz).date()
    live_price = meta.get("regularMarketPrice")
    if live_price and len(prices) and live_day > prices.index[-1].date():
        prices.loc[pd.Timestamp(live_day)] = [np.nan, np.nan, np.nan, live_price, np.nan]

    dividend_events = result.get("events", {}).get("dividends", {})
    dividends = pd.Series(
        {
            pd.Timestamp(datetime.fromtimestamp(int(stamp), JAKARTA).date()): event["amount"]
            for stamp, event in dividend_events.items()
        },
        dtype=float,
    ).sort_index()
    return prices, dividends


def load_universe() -> list:
    return json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))["stocks"]


def save_universe(stocks: list) -> None:
    data = json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
    data["stocks"] = stocks
    UNIVERSE_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- metrics
def rsi(close: pd.Series, period: int = 14) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    relative = gain / loss.replace(0, np.nan)
    return float(100 - 100 / (1 + relative.iloc[-1])) if not math.isnan(relative.iloc[-1]) else 50.0


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def timing_tag(close, ma50, ma200, ma200_prev, rsi_value) -> tuple:
    """(tag, css class, explanation) - WHEN, separate from the quality score."""
    if ma200 is None or math.isnan(ma200):
        return "Not enough history", "grey", "Less than 200 trading days of data."
    above_200 = close > ma200
    ma200_rising = ma200 > ma200_prev
    if above_200 and (rsi_value > 68 or close > ma50 * 1.08):
        return "Uptrend, stretched", "amber", "Strong, but extended - wait for a dip toward MA50."
    if above_200 and close <= ma50 * 1.03 and rsi_value < 55:
        return "Pullback in uptrend", "green", "Above MA200 and back near MA50 - the classic accumulate zone."
    if above_200:
        return "Uptrend", "green", "Above MA200 - trend supports buying on dips."
    if close > ma50 and ma50 > ma200 * 0.97:
        return "Recovering", "amber", "Back above MA50 but still under MA200 - watch for a close above MA200."
    if not ma200_rising and ma50 < ma200:
        return "Downtrend - wait", "red", "Below a falling MA200 - let it build a base before buying."
    return "Below MA200", "red", "Under MA200 - only small starter buys, if any."


def analyse_stock(stock: dict, index_returns: pd.Series) -> dict:
    code = stock["code"]
    prices, dividends = fetch_history(f"{code}.JK")
    close = prices["Close"]
    last_date = close.index[-1]
    price = float(close.iloc[-1])

    # --- dividends
    ttm = float(dividends[dividends.index > last_date - pd.Timedelta(days=365)].sum())
    by_year = dividends.groupby(dividends.index.year).sum()
    this_year = last_date.year
    full_years = [year for year in range(this_year - 5, this_year)]
    paid_years = sum(1 for year in full_years if by_year.get(year, 0) > 0)
    last3 = [float(by_year.get(year, 0.0)) for year in range(this_year - 3, this_year)]
    avg3 = float(np.mean(last3)) if last3 else 0.0
    ttm_yield = ttm / price * 100 if price else 0.0
    avg3_yield = avg3 / price * 100 if price else 0.0
    # Damp one-off specials: score on the lower of TTM and 1.25x the 3-year average
    yield_used = min(ttm_yield, avg3_yield * 1.25) if avg3_yield > 0 else ttm_yield * 0.5
    first, latest = float(by_year.get(this_year - 4, 0.0)), float(by_year.get(this_year - 1, 0.0))
    growth = ((latest / first) ** (1 / 3) - 1) * 100 if first > 0 and latest > 0 else None
    recent_ex = dividends[dividends.index > last_date - pd.Timedelta(days=730)]
    ex_months = sorted({ex.month for ex in recent_ex.index})
    month_names = [datetime(2000, month, 1).strftime("%b") for month in ex_months]
    last_ex = recent_ex.index[-1].strftime("%d %b %Y") if len(recent_ex) else None

    # --- stability (1 year)
    one_year = close[close.index > last_date - pd.Timedelta(days=365)]
    returns = one_year.pct_change().dropna()
    volatility = float(returns.std() * math.sqrt(245) * 100)
    joined = pd.concat([returns, index_returns], axis=1, join="inner").dropna()
    joined.columns = ["stock", "index"]
    beta = float(joined["stock"].cov(joined["index"]) / joined["index"].var()) if len(joined) > 60 else None
    running_max = one_year.cummax()
    max_drawdown = float((one_year / running_max - 1).min() * 100)
    return_1y = float((one_year.iloc[-1] / one_year.iloc[0] - 1) * 100)
    total_return_1y = return_1y + ttm_yield

    # --- trend / timing
    ma50 = float(close.rolling(50).mean().iloc[-1])
    ma200_series = close.rolling(200).mean()
    ma200 = float(ma200_series.iloc[-1])
    ma200_prev = float(ma200_series.iloc[-21]) if len(ma200_series) > 21 else ma200
    rsi_value = rsi(close)
    tag, tag_class, tag_note = timing_tag(price, ma50, ma200, ma200_prev, rsi_value)
    high_52w = float(one_year.max())
    low_52w = float(one_year.min())

    # --- size
    market_cap_t = price * stock.get("shares_bn", 0) / 1000  # IDR trillion

    # --- score (0-100)
    yield_points = clamp(yield_used / YIELD_FULL_MARKS) * 35
    consistency_points = paid_years / 5 * 15
    growth_points = clamp((growth or 0) / 10) * 5 if growth is not None else 0
    beta_points = clamp((1.3 - beta) / 1.0) * 15 if beta is not None else 7
    volatility_points = clamp((45 - volatility) / 30) * 10
    size_points = clamp(math.log10(max(market_cap_t, 1) / 10) / math.log10(50)) * 10
    class_points = CLASS_POINTS.get(stock.get("class"), 4)
    score = round(
        yield_points + consistency_points + growth_points + beta_points
        + volatility_points + size_points + class_points
    )

    # --- flags (honest warnings)
    flags = []
    if avg3_yield > 0 and ttm_yield > avg3_yield * SPECIAL_DIVIDEND_RATIO:
        flags.append("TTM yield boosted by a large/special payout - the 3-year average is the safer guide.")
    if ttm_yield > 0 and avg3_yield > ttm_yield * 2:
        flags.append(
            f"3-year average ({avg3_yield:.0f}%) inflated by past special/boom-year payouts - "
            "do not expect it to repeat."
        )
    if growth is not None and growth < -10:
        flags.append(f"Dividend shrinking ({growth:+.0f}%/yr over 3 years) - possible yield trap.")
    if return_1y < -30:
        flags.append(f"Price {return_1y:+.0f}% in a year - high yield may reflect business risk.")
    if paid_years < 4:
        flags.append(f"Paid in only {paid_years} of the last 5 years.")
    if ttm_yield > 15:
        flags.append("Yield above 15% - double-check the dividend data with the company announcement.")
    if stock.get("class") == "Cyclical":
        flags.append("Cyclical: dividends follow commodity / economic cycles, not defensive.")

    reasons = []
    if yield_used >= 5:
        reasons.append(f"solid yield (~{yield_used:.1f}% normalised)")
    if paid_years == 5:
        reasons.append("paid every year for 5 years")
    if growth is not None and growth > 3:
        reasons.append(f"dividend growing {growth:+.0f}%/yr")
    if beta is not None and beta < 0.8:
        reasons.append(f"low beta {beta:.2f} (moves less than IHSG)")
    if market_cap_t >= 100:
        reasons.append("mega cap")

    return {
        "code": code,
        "name": stock.get("name", code),
        "sector": stock.get("sector", ""),
        "class": stock.get("class", ""),
        "date": last_date.strftime("%Y-%m-%d"),
        "price": price,
        "change_1d": float((close.iloc[-1] / close.iloc[-2] - 1) * 100),
        "market_cap_t": round(market_cap_t, 1),
        "ttm_dps": round(ttm, 2),
        "ttm_yield": round(ttm_yield, 2),
        "avg3_yield": round(avg3_yield, 2),
        "yield_used": round(yield_used, 2),
        "paid_years": paid_years,
        "dividend_growth": None if growth is None else round(growth, 1),
        "dps_by_year": {str(year): round(float(by_year.get(year, 0.0)), 2) for year in range(this_year - 5, this_year + 1)},
        "ex_months": month_names,
        "last_ex_date": last_ex,
        "beta": None if beta is None else round(beta, 2),
        "volatility": round(volatility, 1),
        "max_drawdown_1y": round(max_drawdown, 1),
        "return_1y": round(return_1y, 1),
        "total_return_1y": round(total_return_1y, 1),
        "ma50": round(ma50),
        "ma200": round(ma200),
        "rsi": round(rsi_value, 1),
        "high_52w": round(high_52w),
        "low_52w": round(low_52w),
        "from_high": round((price / high_52w - 1) * 100, 1),
        "timing": tag,
        "timing_class": tag_class,
        "timing_note": tag_note,
        "score": int(score),
        "score_parts": {
            "yield": round(yield_points, 1),
            "consistency": round(consistency_points + growth_points, 1),
            "stability": round(beta_points + volatility_points, 1),
            "size": round(size_points, 1),
            "defensive": class_points,
        },
        "reasons": reasons,
        "flags": flags,
    }


def build(max_workers: int = 8) -> dict:
    universe = load_universe()
    index_prices, _ = fetch_history(INDEX_SYMBOL, years=2)
    index_close = index_prices["Close"]
    index_returns = index_close[index_close.index > index_close.index[-1] - pd.Timedelta(days=365)].pct_change().dropna()

    rows, errors = [], []

    def run(stock):
        try:
            return analyse_stock(stock, index_returns)
        except Exception as error:  # keep going if one ticker fails
            errors.append({"code": stock["code"], "error": repr(error)[:120]})
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for result in pool.map(run, universe):
            if result:
                rows.append(result)

    rows.sort(key=lambda row: row["score"], reverse=True)
    summary = {
        "generated": datetime.now(JAKARTA).strftime("%Y-%m-%d %H:%M"),
        "index_close": float(index_close.iloc[-1]),
        "index_change_1d": float((index_close.iloc[-1] / index_close.iloc[-2] - 1) * 100),
        "stocks": rows,
        "errors": errors,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


def load_summary() -> dict:
    if SUMMARY_FILE.exists():
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    return {}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    result = build()
    print(f"Watchlist built {result['generated']}: {len(result['stocks'])} stocks, {len(result['errors'])} errors")
    for row in result["stocks"]:
        print(
            f"{row['score']:3d}  {row['code']:5s} {row['class'][:4]:4s} "
            f"yield {row['ttm_yield']:5.1f}% (3y {row['avg3_yield']:4.1f}%)  "
            f"paid {row['paid_years']}/5  beta {row['beta']}  cap Rp{row['market_cap_t']:,.0f}T  {row['timing']}"
        )
    for error in result["errors"]:
        print("ERROR", error)

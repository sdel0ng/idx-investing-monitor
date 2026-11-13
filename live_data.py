"""
Live(ish) IDX quotes and chart candles for the desktop app.

Free sources only:
  quotes  - TradingView scanner (one request for many tickers), Yahoo chart API as fallback
  candles - Yahoo Finance chart API (1m / 5m / 15m / 1h / daily / weekly)
Both free feeds are delayed about 10 minutes for IDX. Real-time IDX data needs a paid feed
(broker app or a TradingView IDX real-time subscription).

Times are shifted to WIB (UTC+7) so the chart axis shows Jakarta time.
Education only - not financial advice.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
WIB_SHIFT = 7 * 3600
TV_URL = "https://scanner.tradingview.com/indonesia/scan"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range}"
USER_AGENT = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

QUOTE_TTL = 8          # seconds a quote is reused before asking TradingView again
INTRADAY_TTL = 20      # seconds intraday candles are reused
DAILY_TTL = 60

# app code -> (TradingView ticker, Yahoo symbol, display name)
INDEXES = {
    "IHSG": ("IDX:COMPOSITE", "^JKSE", "IHSG"),
    "LQ45": ("IDX:LQ45", "^JKLQ45", "LQ45"),
}

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600}

TIMEFRAMES = {
    # tf: (yahoo interval, yahoo range, intraday?)
    "1m": ("1m", "1d", True),
    "5m": ("5m", "5d", True),
    "15m": ("15m", "1mo", True),
    "1H": ("60m", "6mo", True),
    "1D": ("1d", "2y", False),
    "1W": ("1wk", "10y", False),
}

_quote_cache = {}
_candle_cache = {}
_lock = threading.Lock()


def _get_json(url, payload=None, timeout=12):
    headers = dict(USER_AGENT)
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def clean_code(code: str) -> str:
    return code.strip().upper().replace(".JK", "").replace("^JKSE", "IHSG").replace("COMPOSITE", "IHSG")


def tv_ticker(code: str) -> str:
    return INDEXES[code][0] if code in INDEXES else f"IDX:{code}"


def yahoo_symbol(code: str) -> str:
    return INDEXES[code][1] if code in INDEXES else f"{code}.JK"


# ---------------------------------------------------------------- market session

def market_status(now=None) -> dict:
    """IDX regular-market session state in WIB (does not know exchange holidays)."""
    now = now or datetime.now(WIB)
    minutes = now.hour * 60 + now.minute
    friday = now.weekday() == 4
    if now.weekday() >= 5:
        state, label = "closed", "Closed (weekend)"
    elif minutes < 8 * 60 + 45:
        state, label = "closed", "Pre-market"
    elif minutes < 9 * 60:
        state, label = "pre", "Pre-opening"
    elif minutes < (11 * 60 + 30 if friday else 12 * 60):
        state, label = "open", "Session 1"
    elif minutes < (14 * 60 if friday else 13 * 60 + 30):
        state, label = "break", "Lunch break"
    elif minutes < 15 * 60 + 50:
        state, label = "open", "Session 2"
    elif minutes < 16 * 60 + 1:
        state, label = "pre", "Pre-closing auction"
    else:
        state, label = "closed", "Closed"
    return {
        "state": state,
        "label": label,
        "live": state in ("open", "pre"),
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
    }


# ---------------------------------------------------------------- quotes

TV_COLUMNS = ["close", "change", "change_abs", "open", "high", "low", "volume", "Value.Traded",
              "update_mode", "time", "description"]


def _fetch_tv_quotes(codes):
    payload = {"symbols": {"tickers": [tv_ticker(code) for code in codes]}, "columns": TV_COLUMNS}
    data = _get_json(TV_URL, payload)
    by_ticker = {code: tv_ticker(code) for code in codes}
    reverse = {ticker: code for code, ticker in by_ticker.items()}
    quotes = {}
    for row in data.get("data", []):
        code = reverse.get(row["s"])
        values = dict(zip(TV_COLUMNS, row["d"]))
        if code is None or values["close"] is None:
            continue
        delay = values.get("update_mode") or ""
        delay_min = None
        if delay.startswith("delayed_streaming_"):
            delay_min = int(delay.rsplit("_", 1)[-1]) // 60
        elif delay == "streaming":
            delay_min = 0
        quotes[code] = {
            "code": code,
            "price": values["close"],
            "change_pct": values["change"],
            "change": values["change_abs"],
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
            "volume": values["volume"],
            "value_bn": (values["Value.Traded"] or 0) / 1e9 if values["Value.Traded"] else None,
            "bar_time": values["time"],
            "name": values["description"],
            "delay_min": delay_min,
            "source": "TradingView",
        }
    return quotes


def _fetch_yahoo_quote(code):
    url = YAHOO_URL.format(symbol=urllib.parse.quote(yahoo_symbol(code)), interval="1d", range="5d")
    result = _get_json(url)["chart"]["result"][0]
    meta = result["meta"]
    price = meta.get("regularMarketPrice")
    previous = meta.get("chartPreviousClose") or meta.get("previousClose")
    closes = [c for c in result["indicators"]["quote"][0].get("close", []) if c is not None]
    if len(closes) >= 2:
        previous = closes[-2]
    change = price - previous if price is not None and previous else None
    return {
        "code": code,
        "price": price,
        "change_pct": change / previous * 100 if change is not None else None,
        "change": change,
        "open": None,
        "high": meta.get("regularMarketDayHigh"),
        "low": meta.get("regularMarketDayLow"),
        "volume": meta.get("regularMarketVolume"),
        "value_bn": None,
        "bar_time": None,
        "name": meta.get("longName") or code,
        "delay_min": 10,
        "source": "Yahoo",
    }


def get_quotes(codes) -> dict:
    """Quotes for many codes, fetched in one TradingView request and cached briefly."""
    codes = [clean_code(code) for code in codes if code and code.strip()]
    codes = list(dict.fromkeys(codes))[:400]
    now = time.time()
    with _lock:
        stale = [code for code in codes if now - _quote_cache.get(code, (0, None))[0] > QUOTE_TTL]
    if stale:
        fresh = {}
        try:
            for start in range(0, len(stale), 200):
                fresh.update(_fetch_tv_quotes(stale[start:start + 200]))
        except Exception:
            fresh = {}
        missing = [code for code in stale if code not in fresh][:12]
        for code in missing:
            try:
                fresh[code] = _fetch_yahoo_quote(code)
            except Exception:
                pass
        with _lock:
            for code, quote in fresh.items():
                _quote_cache[code] = (now, quote)
    with _lock:
        return {code: _quote_cache[code][1] for code in codes if code in _quote_cache}


# ---------------------------------------------------------------- candles

def _sma(values, length):
    out = []
    total = 0.0
    for index, value in enumerate(values):
        total += value
        if index >= length:
            total -= values[index - length]
        out.append(total / length if index >= length - 1 else None)
    return out


def get_candles(code: str, tf: str = "1D") -> dict:
    code = clean_code(code)
    tf = tf if tf in TIMEFRAMES else "1D"
    interval, period, intraday = TIMEFRAMES[tf]
    key = (code, tf)
    now = time.time()
    ttl = INTRADAY_TTL if intraday else DAILY_TTL
    with _lock:
        cached = _candle_cache.get(key)
    if cached and now - cached[0] < ttl:
        return cached[1]

    url = YAHOO_URL.format(symbol=urllib.parse.quote(yahoo_symbol(code)), interval=interval, range=period)
    result = _get_json(url)["chart"]["result"][0]
    stamps = result.get("timestamp") or []
    quote = result["indicators"]["quote"][0]
    bars = []
    for i, stamp in enumerate(stamps):
        o, h, l, c = quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i]
        v = quote["volume"][i] or 0
        if None in (o, h, l, c) or c <= 0:
            continue
        # bad-tick guard: keep the body inside the bar's own range
        h = max(h, o, c)
        l = min(l, o, c)
        if not intraday:
            # one bar per day / week, keyed on the WIB date (Yahoo sometimes repeats the live bar)
            day = datetime.fromtimestamp(stamp + WIB_SHIFT, timezone.utc)
            t = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            if tf == "1W":
                t -= day.weekday() * 86400  # snap the live weekly bar to its Monday
        else:
            # Yahoo appends the live partial bar with the current timestamp - snap it to its bucket
            step = INTERVAL_SECONDS[tf]
            t = (int(stamp) + WIB_SHIFT) // step * step
        bar = {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}
        if bars and bars[-1]["time"] == t:
            previous = bars[-1]
            if intraday or tf == "1W":
                bar = {
                    "time": t,
                    "open": previous["open"],
                    "high": max(previous["high"], h),
                    "low": min(previous["low"], l),
                    "close": c,
                    "volume": max(previous["volume"], v),
                }
            bars[-1] = bar
        elif not bars or t > bars[-1]["time"]:
            bars.append(bar)

    # spike guard for intraday: a single bar far away from both neighbours is a bad print
    if intraday and len(bars) > 3:
        for i in range(1, len(bars) - 1):
            prev_close, next_close = bars[i - 1]["close"], bars[i + 1]["close"]
            bar = bars[i]
            if abs(bar["close"] / prev_close - 1) > 0.15 and abs(bar["close"] / next_close - 1) > 0.15:
                bar.update(open=prev_close, high=max(prev_close, next_close), low=min(prev_close, next_close), close=next_close)

    # make today's daily bar match the latest quote (Yahoo's daily bar can lag)
    if tf == "1D" and bars:
        live = get_quotes([code]).get(code)
        if live and live.get("price") and live.get("bar_time"):
            day = datetime.fromtimestamp(live["bar_time"] + WIB_SHIFT, timezone.utc)
            t = int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp())
            bar = {
                "time": t,
                "open": live["open"] or live["price"],
                "high": max(live["high"] or live["price"], live["price"]),
                "low": min(live["low"] or live["price"], live["price"]),
                "close": live["price"],
                "volume": live["volume"] or 0,
            }
            if bars[-1]["time"] == t:
                bars[-1] = bar
            elif t > bars[-1]["time"]:
                bars.append(bar)

    closes = [bar["close"] for bar in bars]
    averages = {}
    for length in (20, 50, 200):
        values = _sma(closes, length)
        averages[f"ma{length}"] = [
            {"time": bar["time"], "value": round(value, 2)}
            for bar, value in zip(bars, values) if value is not None
        ]
    if intraday:
        # VWAP of the latest session for intraday frames
        last_day = None
        cum_pv = cum_v = 0.0
        vwap = []
        for bar in bars:
            day = bar["time"] // 86400
            if day != last_day:
                last_day, cum_pv, cum_v = day, 0.0, 0.0
            typical = (bar["high"] + bar["low"] + bar["close"]) / 3
            cum_pv += typical * bar["volume"]
            cum_v += bar["volume"]
            if cum_v > 0:
                vwap.append({"time": bar["time"], "value": round(cum_pv / cum_v, 2)})
        averages["vwap"] = vwap

    meta = result.get("meta", {})
    payload = {
        "code": code,
        "tf": tf,
        "intraday": intraday,
        "bars": bars,
        "lines": averages,
        "previous_close": meta.get("chartPreviousClose") if tf == "1m" else None,
        "market": market_status(),
        "source": "Yahoo Finance (≈10 min delayed)",
    }
    with _lock:
        _candle_cache[key] = (now, payload)
    return payload


if __name__ == "__main__":
    print(market_status())
    print(json.dumps(get_quotes(["IHSG", "LQ45", "TPIA", "BBCA"]), indent=1)[:1500])
    for frame in ("1m", "5m", "1D"):
        data = get_candles("TPIA", frame)
        print(frame, len(data["bars"]), data["bars"][-1], {k: len(v) for k, v in data["lines"].items()})

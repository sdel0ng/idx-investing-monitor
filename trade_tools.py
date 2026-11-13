"""
Trading-app style tools for the IDX Investing Monitor (free data only).

- IDX rules: tick size (fraksi harga) and ARA / ARB auto-rejection limits
- stock_detail(): fundamentals + technical indicators for one stock (TradingView scanner)
- market_live(): every IDX stock in one request (movers, heatmap, sectors, breadth)
- sparks(), stock_news(), dividend_history()
- favorites, user settings (fees, capital, risk), price alerts (+ background checker),
  trade journal (record buy / sell -> updates portfolio avg and lots, realized P/L)

Not available for free: order book (bid/offer), running trade, broker / foreign flow intraday.
Education only - not financial advice.
"""

import json
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import live_data
import portfolio

FOLDER = Path(__file__).resolve().parent
SETTINGS_FILE = FOLDER / "app_settings.json"
FAVORITES_FILE = FOLDER / "favorites.json"
ALERTS_FILE = FOLDER / "price_alerts.json"
TRANSACTIONS_FILE = FOLDER / "transactions.json"
PLANS_FILE = FOLDER / "trade_plans.json"
SCAN_URL = "https://scanner.tradingview.com/indonesia/scan"
LOGO_URL = "https://s3-symbol-logo.tradingview.com/{}.svg"

DEFAULT_SETTINGS = {
    "fee_buy_pct": 0.15,     # typical online broker fee incl. levy - check your broker
    "fee_sell_pct": 0.25,    # includes 0.1% final sales tax
    "capital": 0,            # trading capital in Rp (0 = not set)
    "risk_pct": 1.0,         # max loss per trade as % of capital
}
DEFAULT_FAVORITES = ["BBCA", "BBRI", "BMRI", "TLKM", "ASII", "ANTM", "ADRO", "GOTO"]

_lock = threading.Lock()
_cache = {}
_file_lock = threading.Lock()


def _cached(key, ttl, producer):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    with _lock:
        _cache[key] = (now, value)
    return value


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    with _file_lock:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)


def _scan(payload: dict, timeout=30) -> dict:
    request = urllib.request.Request(
        SCAN_URL,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


# ---------------------------------------------------------------- IDX rules

def tick_size(price: float) -> int:
    if price < 200:
        return 1
    if price < 500:
        return 2
    if price < 2000:
        return 5
    if price < 5000:
        return 10
    return 25


def round_to_tick(price: float, mode: str = "nearest") -> int:
    tick = tick_size(price)
    if mode == "down":
        return int(price // tick * tick)
    if mode == "up":
        return int(-(-price // tick) * tick)
    return int(round(price / tick) * tick)


def auto_rejection(prev_close: float, today: date = None) -> dict:
    """ARA / ARB price limits (IDX Kep-00136/BEI/09-2026: ARB 15% until 31 Dec 2026, symmetric from 2027)."""
    today = today or date.today()
    if prev_close <= 10:
        return {"ara": prev_close + 1, "arb": max(1, prev_close - 1), "ara_pct": None, "arb_pct": None}
    if prev_close <= 200:
        up = 0.35
    elif prev_close <= 5000:
        up = 0.25
    else:
        up = 0.20
    down = 0.15 if today <= date(2026, 12, 31) else up
    ara = round_to_tick(prev_close * (1 + up), "down")
    arb = max(round_to_tick(prev_close * (1 - down), "up"), 1)
    return {"ara": ara, "arb": arb, "ara_pct": up * 100, "arb_pct": down * 100}


# ---------------------------------------------------------------- one stock in depth

DETAIL_COLUMNS = [
    "name", "description", "logoid", "sector", "industry", "close", "change", "change_abs", "open", "high", "low",
    "volume", "Value.Traded", "market_cap_basic", "price_earnings_ttm", "price_book_fq", "price_sales_current",
    "return_on_equity", "return_on_assets", "earnings_per_share_basic_ttm", "total_revenue_ttm", "net_income_ttm",
    "net_margin_ttm", "operating_margin_ttm", "debt_to_equity_fq", "current_ratio_fq", "dividends_yield_current",
    "total_shares_outstanding_fundamental", "float_shares_outstanding", "beta_1_year", "enterprise_value_fq",
    "book_value_per_share_fq", "total_revenue_yoy_growth_ttm", "earnings_per_share_diluted_yoy_growth_ttm",
    "average_volume_10d_calc", "average_volume_30d_calc", "relative_volume_10d_calc", "High.All", "Low.All",
    "price_52_week_high", "price_52_week_low", "Recommend.All", "Recommend.MA", "Recommend.Other",
    "RSI", "Stoch.K", "Stoch.D", "CCI20", "ADX", "ADX+DI", "ADX-DI", "AO", "Mom", "MACD.macd", "MACD.signal",
    "W.R", "BBPower", "UO", "EMA10", "SMA10", "EMA20", "SMA20", "EMA30", "SMA30", "EMA50", "SMA50",
    "EMA100", "SMA100", "EMA200", "SMA200", "Ichimoku.BLine", "VWMA", "HullMA9",
    "Pivot.M.Classic.S2", "Pivot.M.Classic.S1", "Pivot.M.Classic.Middle", "Pivot.M.Classic.R1", "Pivot.M.Classic.R2",
    "Perf.W", "Perf.1M", "Perf.3M", "Perf.6M", "Perf.YTD", "Perf.Y", "Perf.5Y", "Volatility.D", "ATR",
    "number_of_employees", "earnings_release_next_date", "earnings_release_date", "ex_dividend_date_recent",
    "free_cash_flow_ttm", "total_debt_fq", "total_assets_fq", "update_mode",
]


def rating_label(value) -> str:
    if value is None:
        return "–"
    if value > 0.5:
        return "Strong buy"
    if value > 0.1:
        return "Buy"
    if value >= -0.1:
        return "Neutral"
    if value >= -0.5:
        return "Sell"
    return "Strong sell"


def technical_summary(d: dict) -> dict:
    """TradingView-style counts of Buy / Neutral / Sell signals for MAs and oscillators."""
    price = d.get("close")
    moving = []
    for kind in ("EMA", "SMA"):
        for length in (10, 20, 30, 50, 100, 200):
            value = d.get(f"{kind}{length}")
            if value is None or price is None:
                continue
            moving.append({"name": f"{kind} {length}", "value": value,
                           "signal": "Buy" if price > value else "Sell" if price < value else "Neutral"})
    for name, key in (("Ichimoku base line", "Ichimoku.BLine"), ("VWMA 20", "VWMA"), ("Hull MA 9", "HullMA9")):
        value = d.get(key)
        if value is not None and price is not None:
            moving.append({"name": name, "value": value, "signal": "Buy" if price > value else "Sell"})

    def osc(name, value, signal):
        return {"name": name, "value": value, "signal": signal}

    oscillators = []
    rsi = d.get("RSI")
    if rsi is not None:
        oscillators.append(osc("RSI (14)", rsi, "Buy" if rsi < 30 else "Sell" if rsi > 70 else "Neutral"))
    k, dd = d.get("Stoch.K"), d.get("Stoch.D")
    if k is not None and dd is not None:
        oscillators.append(osc("Stochastic %K", k, "Buy" if k < 20 and k > dd else "Sell" if k > 80 and k < dd else "Neutral"))
    cci = d.get("CCI20")
    if cci is not None:
        oscillators.append(osc("CCI (20)", cci, "Buy" if cci < -100 else "Sell" if cci > 100 else "Neutral"))
    adx, plus, minus = d.get("ADX"), d.get("ADX+DI"), d.get("ADX-DI")
    if None not in (adx, plus, minus):
        oscillators.append(osc("ADX (14)", adx, "Buy" if adx > 20 and plus > minus else "Sell" if adx > 20 and minus > plus else "Neutral"))
    ao = d.get("AO")
    if ao is not None:
        oscillators.append(osc("Awesome oscillator", ao, "Buy" if ao > 0 else "Sell" if ao < 0 else "Neutral"))
    mom = d.get("Mom")
    if mom is not None:
        oscillators.append(osc("Momentum (10)", mom, "Buy" if mom > 0 else "Sell" if mom < 0 else "Neutral"))
    macd, signal = d.get("MACD.macd"), d.get("MACD.signal")
    if macd is not None and signal is not None:
        oscillators.append(osc("MACD (12, 26)", macd, "Buy" if macd > signal else "Sell"))
    wr = d.get("W.R")
    if wr is not None:
        oscillators.append(osc("Williams %R", wr, "Buy" if wr < -80 else "Sell" if wr > -20 else "Neutral"))
    bbp = d.get("BBPower")
    if bbp is not None:
        oscillators.append(osc("Bull bear power", bbp, "Buy" if bbp > 0 else "Sell" if bbp < 0 else "Neutral"))
    uo = d.get("UO")
    if uo is not None:
        oscillators.append(osc("Ultimate oscillator", uo, "Buy" if uo > 70 else "Sell" if uo < 30 else "Neutral"))

    def counts(rows):
        return {s: sum(1 for r in rows if r["signal"] == s) for s in ("Buy", "Neutral", "Sell")}

    return {
        "summary": {"value": d.get("Recommend.All"), "label": rating_label(d.get("Recommend.All")),
                    "counts": counts(moving + oscillators)},
        "moving": {"value": d.get("Recommend.MA"), "label": rating_label(d.get("Recommend.MA")),
                   "counts": counts(moving), "rows": moving},
        "oscillators": {"value": d.get("Recommend.Other"), "label": rating_label(d.get("Recommend.Other")),
                        "counts": counts(oscillators), "rows": oscillators},
        "pivots": {name: d.get(f"Pivot.M.Classic.{key}") for name, key in
                   (("S2", "S2"), ("S1", "S1"), ("P", "Middle"), ("R1", "R1"), ("R2", "R2"))},
    }


def stock_detail(code: str) -> dict:
    code = live_data.clean_code(code)

    def produce():
        data = _scan({"symbols": {"tickers": [f"IDX:{code}"]}, "columns": DETAIL_COLUMNS}, timeout=15)
        rows = data.get("data") or []
        if not rows:
            return None
        d = dict(zip(DETAIL_COLUMNS, rows[0]["d"]))
        price = d.get("close")
        prev_close = price - (d.get("change_abs") or 0) if price else None
        d["prev_close"] = prev_close
        d["tick"] = tick_size(price) if price else None
        d["limits"] = auto_rejection(prev_close) if prev_close else None
        d["lot_value"] = price * 100 if price else None
        d["logo"] = LOGO_URL.format(d["logoid"]) if d.get("logoid") else None
        d["technicals"] = technical_summary(d)
        for key in ("earnings_release_next_date", "earnings_release_date", "ex_dividend_date_recent"):
            stamp = d.get(key)
            d[key] = datetime.fromtimestamp(stamp).strftime("%d %b %Y") if stamp else None
        return d

    return _cached(("detail", code), 60, produce)


# ---------------------------------------------------------------- whole market (live)

MARKET_COLUMNS = ["name", "description", "logoid", "sector", "close", "change", "change_abs", "volume",
                  "Value.Traded", "market_cap_basic", "relative_volume_10d_calc", "SMA200"]


def market_live() -> dict:
    def produce():
        payload = {
            "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
            "markets": ["indonesia"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": MARKET_COLUMNS,
            "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
            "range": [0, 3000],
        }
        rows = []
        for item in _scan(payload).get("data", []):
            d = dict(zip(MARKET_COLUMNS, item["d"]))
            if not d["close"]:
                continue
            prev = d["close"] - (d["change_abs"] or 0)
            limits = auto_rejection(prev) if prev > 0 else {"ara_pct": None}
            rows.append({
                "code": d["name"],
                "name": d["description"],
                "logo": LOGO_URL.format(d["logoid"]) if d["logoid"] else None,
                "sector": d["sector"] or "Other",
                "price": d["close"],
                "change": round(d["change"] or 0, 2),
                "volume": d["volume"] or 0,
                "value_bn": round((d["Value.Traded"] or 0) / 1e9, 2),
                "mcap_t": round((d["market_cap_basic"] or 0) / 1e12, 2),
                "rvol": round(d["relative_volume_10d_calc"], 2) if d["relative_volume_10d_calc"] else None,
                "above_ma200": bool(d["SMA200"] and d["close"] > d["SMA200"]),
                "ara": limits.get("ara"),
                "arb": limits.get("arb"),
                "ara_pct": limits.get("ara_pct"),
            })
        return {"stocks": rows, "time": datetime.now().strftime("%H:%M:%S"), "market": live_data.market_status()}

    return _cached("market", 30, produce)


def movers(min_value_bn: float = 1.0, limit: int = 15) -> dict:
    stocks = market_live()["stocks"]
    traded = [s for s in stocks if s["value_bn"] >= min_value_bn]
    by = lambda key, reverse=True, rows=traded: sorted(rows, key=lambda s: s[key], reverse=reverse)[:limit]
    near_ara = [s for s in stocks if s["ara"] and s["price"] >= s["ara"] * 0.97 and s["change"] > 0]
    near_arb = [s for s in stocks if s["arb"] and s["price"] <= s["arb"] * 1.03 and s["change"] < 0]
    return {
        "gainers": by("change"),
        "losers": by("change", reverse=False),
        "value": by("value_bn", rows=stocks),
        "volume": by("volume", rows=stocks),
        "unusual": sorted([s for s in traded if s["rvol"]], key=lambda s: s["rvol"], reverse=True)[:limit],
        "ara": sorted(near_ara, key=lambda s: s["change"], reverse=True)[:limit],
        "arb": sorted(near_arb, key=lambda s: s["change"])[:limit],
    }


def breadth_and_sectors() -> dict:
    stocks = market_live()["stocks"]
    advancers = sum(1 for s in stocks if s["change"] > 0)
    decliners = sum(1 for s in stocks if s["change"] < 0)
    sectors = {}
    for s in stocks:
        sector = sectors.setdefault(s["sector"], {"sector": s["sector"], "mcap": 0.0, "weighted": 0.0,
                                                  "value_bn": 0.0, "up": 0, "down": 0, "count": 0})
        sector["mcap"] += s["mcap_t"]
        sector["weighted"] += s["mcap_t"] * s["change"]
        sector["value_bn"] += s["value_bn"]
        sector["count"] += 1
        sector["up"] += s["change"] > 0
        sector["down"] += s["change"] < 0
    rows = []
    for sector in sectors.values():
        sector["change"] = round(sector["weighted"] / sector["mcap"], 2) if sector["mcap"] else 0
        sector["value_bn"] = round(sector["value_bn"], 1)
        sector["mcap"] = round(sector["mcap"], 1)
        del sector["weighted"]
        rows.append(sector)
    rows.sort(key=lambda r: r["change"], reverse=True)
    total_value = sum(s["value_bn"] for s in stocks)
    return {
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": len(stocks) - advancers - decliners,
        "total_value_bn": round(total_value, 0),
        "above_ma200_pct": round(100 * sum(s["above_ma200"] for s in stocks if s["value_bn"] >= 10)
                                 / max(1, sum(1 for s in stocks if s["value_bn"] >= 10))),
        "sectors": rows,
    }


# ---------------------------------------------------------------- sparklines, news, dividends

def sparks(codes) -> dict:
    """Closes of the latest session (5-minute bars) for small inline charts."""
    codes = [live_data.clean_code(c) for c in codes if c.strip()][:40]

    def one(code):
        try:
            data = live_data.get_candles(code, "5m")
            bars = data["bars"]
            if not bars:
                return code, []
            last_day = bars[-1]["time"] // 86400
            return code, [round(b["close"], 2) for b in bars if b["time"] // 86400 == last_day]
        except Exception:
            return code, []

    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, codes))


def stock_news(code: str, name: str = "") -> list:
    code = live_data.clean_code(code)

    def produce():
        import market_context
        short = " ".join(w for w in name.replace("PT ", "").replace(" Tbk", "").split()[:2])
        query = f'"{code}" saham OR "{short}" when:30d' if short else f'"{code}" saham when:30d'
        keywords = [code.lower()] + ([short.lower()] if short else [])
        return market_context.fetch_news("stock", query, keywords, "id", max_age_hours=24 * 30, limit=15)

    return _cached(("news", code), 900, produce)


def dividend_history(code: str) -> list:
    code = live_data.clean_code(code)

    def produce():
        import dividend_watchlist
        prices, dividends = dividend_watchlist.fetch_history(f"{code}.JK", years=10)
        rows = []
        for ex_date, amount in dividends.sort_index(ascending=False).items():
            before = prices[prices.index < ex_date]["Close"]
            close = float(before.iloc[-1]) if len(before) else None
            rows.append({
                "ex_date": ex_date.strftime("%d %b %Y"),
                "year": ex_date.year,
                "amount": round(float(amount), 2),
                "yield_pct": round(amount / close * 100, 2) if close else None,
            })
        return rows

    return _cached(("div", code), 6 * 3600, produce)


# ---------------------------------------------------------------- settings & favorites

def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    settings.update(_read_json(SETTINGS_FILE, {}))
    return settings


def save_settings(values: dict) -> dict:
    settings = load_settings()
    for key in DEFAULT_SETTINGS:
        if key in values and values[key] not in (None, ""):
            settings[key] = float(values[key])
    _write_json(SETTINGS_FILE, settings)
    return settings


def load_favorites() -> list:
    favorites = _read_json(FAVORITES_FILE, None)
    if favorites is None:
        held = [h["code"] for h in portfolio.load_portfolio().get("holdings", [])]
        favorites = list(dict.fromkeys(held + DEFAULT_FAVORITES))
        _write_json(FAVORITES_FILE, favorites)
    return favorites


def toggle_favorite(code: str) -> bool:
    code = live_data.clean_code(code)
    favorites = load_favorites()
    if code in favorites:
        favorites.remove(code)
        on = False
    else:
        favorites.append(code)
        on = True
    _write_json(FAVORITES_FILE, favorites)
    return on


# ---------------------------------------------------------------- price alerts

ALERT_TYPES = {
    "above": "price rises to or above",
    "below": "price falls to or below",
    "change_up": "day change reaches +%",
    "change_down": "day change reaches -%",
}
fired_events = []   # in-memory feed for in-app toasts: {"id", "time", "code", "message"}


def load_alerts() -> list:
    return _read_json(ALERTS_FILE, [])


def add_alert(code: str, kind: str, value: float, note: str = "", plan: str = None) -> dict:
    if kind not in ALERT_TYPES:
        raise ValueError(f"unknown alert type {kind}")
    alert = {
        "id": uuid.uuid4().hex[:10],
        "code": live_data.clean_code(code),
        "type": kind,
        "value": float(value),
        "note": note.strip()[:120],
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "active": True,
        "triggered": None,
        "trigger_price": None,
    }
    if plan:
        alert["plan"] = plan
    alerts = load_alerts()
    alerts.append(alert)
    _write_json(ALERTS_FILE, alerts)
    return alert


def delete_alert(alert_id: str) -> None:
    _write_json(ALERTS_FILE, [a for a in load_alerts() if a["id"] != alert_id])


def describe_alert(alert: dict) -> str:
    if alert["type"] in ("above", "below"):
        return f"{alert['code']} {ALERT_TYPES[alert['type']]} {alert['value']:,.0f}"
    sign = "+" if alert["type"] == "change_up" else "-"
    return f"{alert['code']} day change reaches {sign}{abs(alert['value']):.1f}%"


def check_alerts(show_popup=None) -> list:
    alerts = load_alerts()
    active = [a for a in alerts if a["active"]]
    if not active:
        return []
    quotes = live_data.get_quotes([a["code"] for a in active])
    hits = []
    for alert in active:
        quote = quotes.get(alert["code"])
        if not quote or quote.get("price") is None:
            continue
        price, change = quote["price"], quote.get("change_pct") or 0
        hit = (
            (alert["type"] == "above" and price >= alert["value"])
            or (alert["type"] == "below" and price <= alert["value"])
            or (alert["type"] == "change_up" and change >= abs(alert["value"]))
            or (alert["type"] == "change_down" and change <= -abs(alert["value"]))
        )
        if hit:
            alert["active"] = False
            alert["triggered"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            alert["trigger_price"] = price
            message = f"{describe_alert(alert)} - now {price:,.0f} ({change:+.2f}%)" + (
                f"\nNote: {alert['note']}" if alert["note"] else "")
            hits.append(message)
            fired_events.append({"id": alert["id"], "time": time.time(), "code": alert["code"], "message": message})
    if hits:
        _write_json(ALERTS_FILE, alerts)
        if show_popup:
            for message in hits:
                threading.Thread(target=show_popup, args=("IDX price alert", message + "\n\n(prices ~10 min delayed)"),
                                 daemon=True).start()
    del fired_events[:-50]
    return hits


def start_alert_checker(show_popup=None, interval: int = 30) -> None:
    def loop():
        last_macro_check = 0
        while True:
            try:
                if live_data.market_status()["state"] in ("open", "pre", "break"):
                    check_alerts(show_popup)
                    if time.time() - last_macro_check > 15 * 60:
                        last_macro_check = time.time()
                        check_macro_rules(show_popup)
            except Exception:
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="alert-checker").start()


# ---------------------------------------------------------------- trade journal

def load_transactions() -> list:
    return _read_json(TRANSACTIONS_FILE, [])


def record_trade(code: str, side: str, price: float, lots: int, trade_date: str = "", note: str = "") -> dict:
    """Record a real buy / sell: updates portfolio.json avg + lots and logs it with fees and realized P/L."""
    code = live_data.clean_code(code)
    side = side.lower()
    if side not in ("buy", "sell") or price <= 0 or lots <= 0:
        raise ValueError("Need side buy/sell, a price and lots above zero.")
    settings = load_settings()
    shares = lots * 100
    gross = price * shares
    data = portfolio.load_portfolio()
    holdings = data.setdefault("holdings", [])
    holding = next((h for h in holdings if h["code"] == code), None)
    entry = {
        "id": uuid.uuid4().hex[:10],
        "date": trade_date or date.today().isoformat(),
        "code": code,
        "side": side,
        "price": price,
        "lots": lots,
        "gross": gross,
        "note": note.strip()[:160],
        "realized": None,
        "realized_pct": None,
    }
    if side == "buy":
        fee = gross * settings["fee_buy_pct"] / 100
        if holding is None:
            holdings.append({"code": code, "avg": price, "lots": lots, "note": ""})
        else:
            if not holding.get("lots"):
                raise ValueError(f"Set your current {code} lots on the Portfolio page first, so the new average is correct.")
            total_lots = holding["lots"] + lots
            holding["avg"] = round((holding["avg"] * holding["lots"] + price * lots) / total_lots, 2)
            holding["lots"] = total_lots
    else:
        fee = gross * settings["fee_sell_pct"] / 100
        if holding is None or not holding.get("lots"):
            raise ValueError(f"No {code} lots recorded in your portfolio - set lots on the Portfolio page first.")
        if lots > holding["lots"]:
            raise ValueError(f"You only hold {holding['lots']} lots of {code}.")
        buy_fee_share = holding["avg"] * shares * settings["fee_buy_pct"] / 100
        entry["avg_cost"] = holding["avg"]
        entry["realized"] = round((price - holding["avg"]) * shares - fee - buy_fee_share)
        entry["realized_pct"] = round(entry["realized"] / (holding["avg"] * shares) * 100, 2)
        holding["lots"] -= lots
        if holding["lots"] == 0:
            holdings.remove(holding)
    entry["fee"] = round(fee)
    entry["net"] = round(gross + fee) if side == "buy" else round(gross - fee)
    portfolio.save_portfolio(data)
    transactions = load_transactions()
    transactions.append(entry)
    _write_json(TRANSACTIONS_FILE, transactions)
    return entry


def delete_transaction(entry_id: str) -> None:
    """Removes the log line only (does not undo the portfolio change)."""
    _write_json(TRANSACTIONS_FILE, [t for t in load_transactions() if t["id"] != entry_id])


def journal_stats() -> dict:
    transactions = sorted(load_transactions(), key=lambda t: (t["date"], t["id"]))
    sells = [t for t in transactions if t["side"] == "sell" and t.get("realized") is not None]
    wins = [t for t in sells if t["realized"] > 0]
    losses = [t for t in sells if t["realized"] <= 0]
    equity, running = [], 0
    for t in sells:
        running += t["realized"]
        equity.append({"date": t["date"], "value": running})
    gross_win = sum(t["realized"] for t in wins)
    gross_loss = -sum(t["realized"] for t in losses)
    return {
        "trades": len(transactions),
        "closed": len(sells),
        "realized": sum(t["realized"] for t in sells),
        "fees": sum(t.get("fee", 0) for t in transactions),
        "win_rate": round(len(wins) / len(sells) * 100) if sells else None,
        "avg_win": round(gross_win / len(wins)) if wins else None,
        "avg_loss": round(-gross_loss / len(losses)) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "best": max(sells, key=lambda t: t["realized"]) if sells else None,
        "worst": min(sells, key=lambda t: t["realized"]) if sells else None,
        "equity": equity,
        "transactions": list(reversed(transactions)),
    }


def portfolio_live() -> dict:
    """Holdings valued at the latest (delayed) price."""
    holdings = portfolio.load_portfolio().get("holdings", [])
    quotes = live_data.get_quotes([h["code"] for h in holdings]) if holdings else {}
    rows, value, cost, today = [], 0.0, 0.0, 0.0
    known = True
    for h in holdings:
        q = quotes.get(h["code"], {})
        price = q.get("price")
        lots = h.get("lots")
        row = {"code": h["code"], "avg": h["avg"], "lots": lots, "price": price,
               "change_pct": q.get("change_pct"),
               "pnl_pct": round((price / h["avg"] - 1) * 100, 2) if price else None}
        if lots and price:
            row["value"] = price * lots * 100
            row["pnl_rp"] = (price - h["avg"]) * lots * 100
            value += row["value"]
            cost += h["avg"] * lots * 100
            today += (q.get("change") or 0) * lots * 100
        else:
            known = False
        rows.append(row)
    return {
        "holdings": rows,
        "value": value,
        "cost": cost,
        "pnl_rp": value - cost,
        "pnl_pct": (value / cost - 1) * 100 if cost else None,
        "today_rp": today,
        "lots_known": known,
    }


# ---------------------------------------------------------------- queue / entry plans

def load_plans() -> list:
    return _read_json(PLANS_FILE, [])


def get_plan(code: str):
    code = live_data.clean_code(code)
    return next((p for p in load_plans() if p["code"] == code and p.get("status", "active") == "active"), None)


def _sync_plan_alerts(plan: dict) -> None:
    """One alert per open queue, the breakout trigger and the exit zone; replaces this plan's old alerts."""
    alerts = [a for a in load_alerts() if a.get("plan") != plan["code"] or not a["active"]]
    _write_json(ALERTS_FILE, alerts)
    if plan.get("status", "active") != "active":
        return
    for index, entry in enumerate(plan["entries"], start=1):
        if not entry.get("filled"):
            add_alert(plan["code"], "below", entry["price"],
                      f"Plan queue {index}: {entry['lots']} lots @ {entry['price']:,.0f} - check your order filled",
                      plan=plan["code"])
    breakout = plan.get("breakout") or {}
    if breakout.get("trigger"):
        add_alert(plan["code"], "above", breakout["trigger"],
                  f"Plan breakout: buy only on a daily CLOSE above {breakout['trigger']:,.0f}", plan=plan["code"])
    exit_price = (plan.get("exit") or {}).get("price")
    if exit_price:
        add_alert(plan["code"], "below", exit_price,
                  f"Plan exit zone {exit_price:,.0f}: exit / cancel if the daily CLOSE stays below", plan=plan["code"])


def save_plan(data: dict) -> dict:
    code = live_data.clean_code(data["code"])
    previous = get_plan(code)
    entries = []
    for entry in data.get("entries", []):
        price = float(entry.get("price") or 0)
        lots = int(float(entry.get("lots") or 0))
        if price > 0 and lots > 0:
            entries.append({
                "price": round_to_tick(price),
                "lots": lots,
                "filled": bool(entry.get("filled")),
                "filled_date": entry.get("filled_date"),
                "fill_price": entry.get("fill_price"),
            })
    if not entries:
        raise ValueError("Add at least one queue price with lots.")
    entries.sort(key=lambda e: -e["price"])
    exit_price = float((data.get("exit") or {}).get("price") or 0)
    if not exit_price or exit_price >= min(e["price"] for e in entries):
        raise ValueError("The exit must be below your lowest queue price.")
    targets = sorted({round_to_tick(float(t)) for t in data.get("targets", []) if t and float(t) > 0})
    breakout = data.get("breakout") or {}
    plan = {
        "code": code,
        "status": "active",
        "created": (previous or {}).get("created") or datetime.now().strftime("%Y-%m-%d %H:%M"),
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "entries": entries,
        "exit": {"price": round_to_tick(exit_price), "rule": "daily close below"},
        "targets": targets,
        "breakout": {
            "trigger": round_to_tick(float(breakout["trigger"])) if breakout.get("trigger") else None,
            "stop": round_to_tick(float(breakout["stop"])) if breakout.get("stop") else None,
        },
        "note": (data.get("note") or "").strip()[:500],
    }
    macro = data.get("macro") or (previous or {}).get("macro")
    if macro:
        plan["macro"] = macro
    plans = [p for p in load_plans() if not (p["code"] == code and p.get("status", "active") == "active")]
    plans.append(plan)
    _write_json(PLANS_FILE, plans)
    _sync_plan_alerts(plan)
    return plan


def close_plan(code: str, status: str = "cancelled") -> None:
    code = live_data.clean_code(code)
    plans = load_plans()
    for plan in plans:
        if plan["code"] == code and plan.get("status", "active") == "active":
            plan["status"] = status
            plan["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            _sync_plan_alerts(plan)
    _write_json(PLANS_FILE, plans)


def fill_plan_entry(code: str, index: int, price: float = None) -> dict:
    """Mark a queue filled AND record the buy in the journal / portfolio (only after a real fill)."""
    plan = get_plan(code)
    if not plan:
        raise ValueError(f"No active plan for {code}.")
    entry = plan["entries"][index]
    if entry.get("filled"):
        raise ValueError("That queue is already marked filled.")
    trade = record_trade(plan["code"], "buy", float(price or entry["price"]), entry["lots"], note=f"plan queue {index + 1}")
    entry["filled"] = True
    entry["filled_date"] = date.today().isoformat()
    entry["fill_price"] = trade["price"]
    plans = [p for p in load_plans() if not (p["code"] == plan["code"] and p.get("status", "active") == "active")]
    plans.append(plan)
    _write_json(PLANS_FILE, plans)
    _sync_plan_alerts(plan)
    return trade


def set_plan_macro(code: str, rules: list) -> dict:
    """Save macro rules on the active plan: [{symbol, label, op above/below, level, action pause/confirm, why}]."""
    plan = get_plan(code)
    if not plan:
        raise ValueError(f"No active plan for {code}.")
    clean = []
    for rule in rules or []:
        if rule.get("op") not in ("above", "below") or rule.get("action") not in ("pause", "confirm"):
            continue
        level = float(rule.get("level") or 0)
        if level <= 0:
            continue
        clean.append({
            "symbol": str(rule["symbol"])[:12],
            "label": str(rule.get("label") or rule["symbol"])[:30],
            "op": rule["op"],
            "level": level,
            "action": rule["action"],
            "why": str(rule.get("why") or "")[:160],
            "last_hit": rule.get("last_hit"),
        })
    plan["macro"] = {"rules": clean, "updated": datetime.now().strftime("%Y-%m-%d %H:%M")}
    plans = [p for p in load_plans() if not (p["code"] == plan["code"] and p.get("status", "active") == "active")]
    plans.append(plan)
    _write_json(PLANS_FILE, plans)
    return plan


def check_macro_rules(show_popup=None) -> list:
    """Popup once per day when a plan's macro rule is hit (gold / IHSG / USD-IDR level)."""
    import macro_sync

    messages = []
    plans = load_plans()
    changed = False
    today = date.today().isoformat()
    for plan in plans:
        rules = (plan.get("macro") or {}).get("rules") or []
        if plan.get("status", "active") != "active" or not rules:
            continue
        for rule, state in zip(rules, macro_sync.evaluate_rules(rules)):
            if not state.get("hit") or rule.get("last_hit") == today:
                continue
            rule["last_hit"] = today
            changed = True
            verb = "PAUSE the queues" if rule["action"] == "pause" else "macro CONFIRMS the plan"
            message = (f"{plan['code']} plan - {rule['label']} {state['last']:,.2f} is {rule['op']} {rule['level']:,.2f}: {verb}.\n"
                       f"{rule.get('why', '')}\nConfirm on the daily close before acting.")
            messages.append(message)
            fired_events.append({"id": f"macro-{plan['code']}-{rule['symbol']}-{rule['op']}", "time": time.time(),
                                 "code": plan["code"], "message": message})
    if changed:
        _write_json(PLANS_FILE, plans)
    if show_popup:
        for message in messages:
            threading.Thread(target=show_popup, args=("Macro rule - queue plan", message), daemon=True).start()
    return messages


def plan_summary(plan: dict, price: float = None) -> dict:
    """Average, risk and reward if every queue fills; distance of each queue from the current price."""
    entries = plan["entries"]
    total_lots = sum(e["lots"] for e in entries)
    avg = sum(e["price"] * e["lots"] for e in entries) / total_lots
    stop = plan["exit"]["price"]
    risk_share = avg - stop
    settings = load_settings()
    risk_rp = risk_share * total_lots * 100
    first_target = plan["targets"][0] if plan["targets"] else None
    rows = []
    for index, e in enumerate(entries, start=1):
        rows.append(dict(
            e,
            label=f"Queue {index}",
            distance_pct=((e["price"] / price - 1) * 100) if price else None,
            rr_t1=((first_target - e["price"]) / (e["price"] - stop)) if first_target else None,
            cost=e["price"] * e["lots"] * 100,
        ))
    status, status_class = "Waiting - queues not reached", "grey"
    if price:
        open_entries = [e for e in entries if not e["filled"]]
        if price <= stop:
            status, status_class = "Below the exit zone - plan is invalid if it CLOSES here", "red"
        elif not open_entries:
            status, status_class = "All queues filled - manage with the exit and targets", "green"
        elif any(price <= e["price"] for e in open_entries):
            status, status_class = "Price reached a queue level - check whether your order filled", "amber"
        elif plan["breakout"].get("trigger") and price >= plan["breakout"]["trigger"]:
            status, status_class = "Above the breakout trigger - valid only on a daily CLOSE above it", "amber"
    return {
        "avg": avg,
        "total_lots": total_lots,
        "cost": avg * total_lots * 100,
        "risk_share": risk_share,
        "risk_pct": risk_share / avg * 100,
        "risk_rp": risk_rp,
        "risk_of_capital": risk_rp / settings["capital"] * 100 if settings.get("capital") else None,
        "targets": [
            {"price": t, "gain_pct": (t / avg - 1) * 100, "rr": (t - avg) / risk_share if risk_share > 0 else None}
            for t in plan["targets"]
        ],
        "rows": rows,
        "status": status,
        "status_class": status_class,
        "price": price,
    }

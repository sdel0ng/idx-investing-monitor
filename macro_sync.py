"""
Macro sync for queue plans: links a stock to the macro drivers that matter for it
(IHSG + USD/IDR for every stock, plus a commodity for miners / energy names),
measures how strongly the stock has actually followed each driver, and checks
the plan's macro rules (e.g. "pause the ANTM queues if gold closes below 3,899").

Free data: Yahoo Finance daily bars (commodity futures ~10-15 min delayed).
Correlations are on daily % changes; they describe the past, not a promise.
Education only - not financial advice.
"""

import json
import math
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1d&range=1y"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CACHE_SECONDS = 15 * 60
# Max believable one-day move; bigger jumps that snap back within a few bars are bad ticks (Yahoo FX has them).
SPIKE_LIMITS = {"IDR=X": 0.02}
TROY_OUNCE_GRAMS = 31.1035

DRIVER_INFO = {
    "^JKSE": {"label": "IHSG", "unit": "", "decimals": 0},
    "IDR=X": {"label": "USD/IDR", "unit": "Rp", "decimals": 0},
    "GC=F": {"label": "Gold (XAU)", "unit": "USD/oz", "decimals": 0},
    "SI=F": {"label": "Silver", "unit": "USD/oz", "decimals": 2},
    "HG=F": {"label": "Copper", "unit": "USD/lb", "decimals": 2},
    "BZ=F": {"label": "Brent oil", "unit": "USD/bbl", "decimals": 2},
}

# Commodity drivers per stock (no free coal / nickel feed, so coal and nickel names get IHSG + USD/IDR only).
SECTOR_DRIVERS = {
    "gold": ["GC=F"],
    "copper_gold": ["HG=F", "GC=F"],
    "oil": ["BZ=F"],
}
STOCK_SECTOR = {
    "ANTM": "gold", "MDKA": "gold", "BRMS": "gold", "PSAB": "gold", "ARCI": "gold", "HRTA": "gold",
    "AMMN": "copper_gold",
    "MEDC": "oil", "ENRG": "oil", "PGAS": "oil", "AKRA": "oil", "ELSA": "oil", "RAJA": "oil",
}
SECTOR_NOTES = {
    "gold": "Gold miner / gold seller: gold in Rupiah drives its earnings, but day to day the stock still trades with IHSG and fund flows.",
    "copper_gold": "Copper-gold producer: copper first, gold second.",
    "oil": "Oil & gas: Brent drives sentiment and earnings.",
}

_cache = {}
_lock = threading.Lock()


# ---------------------------------------------------------------- data

def _fetch_daily(symbol: str) -> list:
    """[(date, close, low, high)] for about one year of daily bars, cached."""
    now = time.time()
    with _lock:
        hit = _cache.get(symbol)
    if hit and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    request = urllib.request.Request(YAHOO_URL.format(symbol), headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    result = payload["chart"]["result"][0]
    quote = result["indicators"]["quote"][0]
    bars = {}
    for stamp, close, low, high in zip(result["timestamp"], quote["close"], quote["low"], quote["high"]):
        if close is None:
            continue
        day = datetime.fromtimestamp(stamp, WIB).date()
        bars[day] = (day, float(close), float(low if low is not None else close), float(high if high is not None else close))
    rows = [bars[d] for d in sorted(bars)]
    # Yahoo sometimes lags a day on daily bars (e.g. IHSG): add the latest quote as today's bar.
    meta = result.get("meta") or {}
    if meta.get("regularMarketPrice") and meta.get("regularMarketTime"):
        day = datetime.fromtimestamp(meta["regularMarketTime"], WIB).date()
        if rows and day > rows[-1][0]:
            price = float(meta["regularMarketPrice"])
            rows.append((day, price, price, price))
    rows = _remove_spikes(rows, SPIKE_LIMITS.get(symbol))
    with _lock:
        _cache[symbol] = (now, rows)
    return rows


def _remove_spikes(rows: list, limit: float = None) -> list:
    """Replace closes that jump more than `limit` and snap back within 5 bars with the previous good close."""
    if not limit or len(rows) < 3:
        return rows
    clean = [rows[0]]
    for i in range(1, len(rows)):
        day, close, low, high = rows[i]
        previous = clean[-1][1]
        if abs(close / previous - 1) > limit:
            ahead = rows[i + 1:i + 6]
            snaps_back = any(abs(r[1] / previous - 1) <= limit for r in ahead)
            if snaps_back:
                clean.append((day, previous, min(low, previous), max(high, previous)))
                continue
        clean.append(rows[i])
    return clean


def _stock_symbol(code: str) -> str:
    return f"{code}.JK"


def _mean(values):
    return sum(values) / len(values) if values else None


def _returns_by_date(rows: list) -> dict:
    return {rows[i][0]: rows[i][1] / rows[i - 1][1] - 1 for i in range(1, len(rows)) if rows[i - 1][1]}


def _correlation(a: dict, b: dict, window: int):
    days = sorted(set(a) & set(b))[-window:]
    if len(days) < 15:
        return None
    x = [a[d] for d in days]
    y = [b[d] for d in days]
    mx, my = _mean(x), _mean(y)
    cov = sum((i - mx) * (j - my) for i, j in zip(x, y))
    vx = sum((i - mx) ** 2 for i in x)
    vy = sum((j - my) ** 2 for j in y)
    return cov / math.sqrt(vx * vy) if vx and vy else None


def _trend(rows: list) -> dict:
    closes = [r[1] for r in rows]
    last = closes[-1]

    def ma(n):
        return _mean(closes[-n:]) if len(closes) >= n else None

    def change(n):
        return (last / closes[-1 - n] - 1) * 100 if len(closes) > n else None

    ma20, ma50, ma200 = ma(20), ma(50), ma(200)
    recent = rows[-20:]
    swing_low = min(recent, key=lambda r: r[2])
    swing_high = max(recent, key=lambda r: r[3])
    if ma20 and ma50 and last > ma20 and last > ma50:
        state, score = "up", 1
    elif ma20 and ma50 and last < ma20 and last < ma50:
        state, score = "down", -1
    else:
        state, score = "mixed", 0
    return {
        "last": last,
        "date": rows[-1][0].isoformat(),
        "chg_1d": change(1),
        "chg_5d": change(5),
        "chg_20d": change(20),
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "above_ma20": bool(ma20 and last > ma20),
        "above_ma50": bool(ma50 and last > ma50),
        "above_ma200": bool(ma200 and last > ma200),
        "swing_low_20d": swing_low[2],
        "swing_low_date": swing_low[0].isoformat(),
        "swing_high_20d": swing_high[3],
        "state": state,
        "score": score,
    }


# ---------------------------------------------------------------- analysis

def drivers_for(code: str) -> list:
    sector = STOCK_SECTOR.get(code)
    return SECTOR_DRIVERS.get(sector, []) + ["^JKSE", "IDR=X"]


def _read(symbol: str, trend: dict, sector: str) -> str:
    label = DRIVER_INFO[symbol]["label"]
    if symbol == "IDR=X":
        if trend["above_ma20"]:
            return "Rupiah weakening: foreign outflow risk for IHSG, but it lifts gold prices in Rupiah."
        return "Rupiah steady / firming: supportive for IHSG flows."
    if symbol == "^JKSE":
        return {"up": "IHSG above MA20 and MA50: market tide is helping.",
                "down": "IHSG below MA20 and MA50: market tide is against new buys.",
                "mixed": "IHSG mixed: between its averages, no clear tide."}[trend["state"]]
    above200 = "above" if trend["above_ma200"] else "below"
    return {"up": f"{label} above MA20 and MA50 (and {above200} MA200): tailwind.",
            "down": f"{label} below MA20 and MA50: headwind.",
            "mixed": f"{label} short-term bounce/dip but not a clear trend (still {above200} MA200)."}[trend["state"]]


def analyse(code: str) -> dict:
    """Driver trends, correlation with the stock, gold in Rupiah (gold names) and an overall macro read."""
    code = code.upper().strip()
    sector = STOCK_SECTOR.get(code)
    symbols = drivers_for(code)
    stock_rows = _fetch_daily(_stock_symbol(code))
    stock_returns = _returns_by_date(stock_rows)
    drivers = []
    for symbol in symbols:
        try:
            rows = _fetch_daily(symbol)
        except Exception as error:
            drivers.append({"symbol": symbol, **DRIVER_INFO[symbol], "error": str(error)})
            continue
        trend = _trend(rows)
        driver_returns = _returns_by_date(rows)
        drivers.append({
            "symbol": symbol,
            **DRIVER_INFO[symbol],
            **trend,
            "corr_20": _correlation(stock_returns, driver_returns, 20),
            "corr_60": _correlation(stock_returns, driver_returns, 60),
            "corr_120": _correlation(stock_returns, driver_returns, 120),
            "role": "commodity" if symbol in SECTOR_DRIVERS.get(sector, []) else "market",
            "read": _read(symbol, trend, sector),
        })

    gold_idr = None
    if sector in ("gold", "copper_gold"):
        try:
            gold_rows = {r[0]: r[1] for r in _fetch_daily("GC=F")}
            fx_rows = {r[0]: r[1] for r in _fetch_daily("IDR=X")}
            days = sorted(set(gold_rows) & set(fx_rows))
            per_gram = [(d, gold_rows[d] * fx_rows[d] / TROY_OUNCE_GRAMS, 0, 0) for d in days]
            per_gram = [(d, v, v, v) for d, v, _, _ in per_gram]
            gold_idr = _trend(per_gram)
            gold_idr["label"] = "Gold in Rupiah (per gram, approx.)"
        except Exception:
            gold_idr = None

    valid = [d for d in drivers if "error" not in d]
    commodity = [d for d in valid if d["role"] == "commodity"]
    index = next((d for d in valid if d["symbol"] == "^JKSE"), None)
    strongest = max(valid, key=lambda d: abs(d.get("corr_60") or 0), default=None)

    # Weight each driver by how much the stock has actually followed it (60-day correlation, floor 0.1).
    weighted, weights = 0.0, 0.0
    for d in valid:
        if d["symbol"] == "IDR=X":
            continue
        weight = max(abs(d.get("corr_60") or 0), 0.1)
        weighted += d["score"] * weight
        weights += weight
    score = weighted / weights if weights else 0
    if score >= 0.35:
        verdict, verdict_class = "Tailwind - macro supports buying at the queues", "green"
    elif score <= -0.35:
        verdict, verdict_class = "Headwind - macro argues for patience / smaller size", "red"
    else:
        verdict, verdict_class = "Mixed - macro neither confirms nor blocks; let price levels decide", "amber"

    lessons = []
    if strongest and index and strongest["symbol"] == "^JKSE" and commodity:
        c = commodity[0]
        lessons.append(
            f"{code} has tracked IHSG (60-day correlation {index['corr_60']:.2f}) far more than "
            f"{c['label']} ({(c.get('corr_60') or 0):.2f}). A rising commodity alone is not enough - watch IHSG too.")
    if gold_idr and commodity:
        g = next((d for d in commodity if d["symbol"] == "GC=F"), None)
        if g and gold_idr["chg_20d"] is not None and g["chg_20d"] is not None:
            gap = gold_idr["chg_20d"] - g["chg_20d"]
            effect = ("the weaker Rupiah is cushioning gold for " if gap > 0.3
                      else "a firmer Rupiah is taking some of gold's gain away from " if gap < -0.3
                      else "the Rupiah made little difference for ")
            lessons.append(
                f"Gold in Rupiah {gold_idr['chg_20d']:+.1f}% vs {g['chg_20d']:+.1f}% in USD over 20 days - "
                f"{effect}{code}'s gold sales.")
    return {
        "code": code,
        "sector": sector,
        "sector_note": SECTOR_NOTES.get(sector, "No direct commodity feed for this stock - market drivers only."),
        "drivers": drivers,
        "gold_idr": gold_idr,
        "score": score,
        "verdict": verdict,
        "verdict_class": verdict_class,
        "strongest": strongest["label"] if strongest else None,
        "lessons": lessons,
        "updated": datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB"),
        "source": "Yahoo Finance daily bars (futures ~10-15 min delayed)",
    }


# ---------------------------------------------------------------- plan rules

def suggest_rules(code: str, analysis: dict = None) -> list:
    """Default rules: pause queues if the main commodity or IHSG breaks its recent swing low;
    'confirmed' when the commodity reclaims its MA50."""
    analysis = analysis or analyse(code)
    rules = []
    for d in analysis["drivers"]:
        if "error" in d:
            continue
        if d["role"] == "commodity" and d["symbol"] in ("GC=F", "HG=F", "BZ=F"):
            rules.append({"symbol": d["symbol"], "label": d["label"], "op": "below",
                          "level": round(d["swing_low_20d"], 2), "action": "pause",
                          "why": f"{d['label']} breaks its {d['swing_low_date']} low - commodity downtrend resumes"})
            if d["ma50"] and not d["above_ma50"]:
                rules.append({"symbol": d["symbol"], "label": d["label"], "op": "above",
                              "level": round(d["ma50"], 2), "action": "confirm",
                              "why": f"{d['label']} reclaims its MA50 - real tailwind, not just a bounce"})
            break
    index = next((d for d in analysis["drivers"] if d["symbol"] == "^JKSE" and "error" not in d), None)
    if index:
        rules.append({"symbol": "^JKSE", "label": "IHSG", "op": "below",
                      "level": round(index["swing_low_20d"]), "action": "pause",
                      "why": f"IHSG breaks its {index['swing_low_date']} low - market sell-off resumes"})
    return rules


def evaluate_rules(rules: list, analysis: dict = None) -> list:
    """State of each macro rule on the latest daily close (and intraday value for futures)."""
    by_symbol = {d["symbol"]: d for d in (analysis or {}).get("drivers", []) if "error" not in d}
    out = []
    for rule in rules or []:
        driver = by_symbol.get(rule["symbol"])
        if driver is None:
            try:
                driver = {"last": _fetch_daily(rule["symbol"])[-1][1]}
            except Exception:
                out.append(dict(rule, state="unknown", last=None))
                continue
        last = driver["last"]
        hit = last < rule["level"] if rule["op"] == "below" else last > rule["level"]
        out.append(dict(rule, last=last, hit=hit,
                        distance_pct=(rule["level"] / last - 1) * 100 if last else None,
                        state=("active" if hit else "not yet")))
    return out


def plan_gate(evaluated: list) -> dict:
    paused = [r for r in evaluated if r.get("hit") and r["action"] == "pause"]
    confirmed = [r for r in evaluated if r.get("hit") and r["action"] == "confirm"]
    if paused:
        return {"state": "paused", "class": "red",
                "text": "Macro rule hit - pause the queues (don't add) until it recovers: " + "; ".join(r["why"] for r in paused)}
    if confirmed:
        return {"state": "confirmed", "class": "green", "text": "Macro confirmed - " + "; ".join(r["why"] for r in confirmed)}
    return {"state": "neutral", "class": "grey", "text": "No macro rule triggered - follow the price levels."}


def plan_macro(plan: dict) -> dict:
    analysis = analyse(plan["code"])
    rules = (plan.get("macro") or {}).get("rules")
    suggested = False
    if not rules:
        rules = suggest_rules(plan["code"], analysis)
        suggested = True
    evaluated = evaluate_rules(rules, analysis)
    return dict(analysis, rules=evaluated, rules_suggested=suggested, gate=plan_gate(evaluated))


if __name__ == "__main__":
    import sys

    result = analyse(sys.argv[1] if len(sys.argv) > 1 else "ANTM")
    print(json.dumps(result, indent=1, default=str)[:4000])
    print(json.dumps(suggest_rules(result["code"], result), indent=1))

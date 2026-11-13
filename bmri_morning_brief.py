"""
BMRI morning brief — runs at 08:00 WIB, before the IDX pre-opening (08:45) and open (09:00).

Contents:
  * Yesterday's BMRI recap (close, technical bias, broksum signal, plan alerts)
  * Overnight / global markets and a rough opening-bias estimate
  * News headlines from the last ~24 h (Google News)
  * Today's game plan: pivot levels + plan levels + if/then rules

Usage:
    python bmri_morning_brief.py            # build and open the brief
    python bmri_morning_brief.py --no-open  # build only
"""

import argparse
import html
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import bmri_daily_check as core
import big_banks
import ihsg_analysis

GLOBAL_MARKETS = [
    ("EIDO", "EIDO: MSCI Indonesia ETF (US, overnight)"),
    ("IDR=X", "USD/IDR"),
    ("DX-Y.NYB", "US Dollar Index (DXY)"),
    ("BZ=F", "Brent crude (USD/bbl)"),
    ("GC=F", "Gold XAU (USD/oz)"),
    ("^GSPC", "S&P 500"),
    ("^IXIC", "Nasdaq"),
    ("^DJI", "Dow Jones"),
    ("^TNX", "US 10-year yield (%)"),
    ("^N225", "Nikkei 225 (live)"),
    ("^HSI", "Hang Seng"),
    ("^JKSE", "IHSG (yesterday)"),
    ("^JKLQ45", "LQ45 (yesterday)"),
    ("BBCA.JK", "BBCA (yesterday)"),
    ("BBRI.JK", "BBRI (yesterday)"),
    ("BBNI.JK", "BBNI (yesterday)"),
]

NEWS_QUERIES = [
    ("BMRI / Bank Mandiri", 'BMRI OR "Bank Mandiri" when:1d', ("bmri", "mandiri")),
    ("IHSG, MSCI & foreign flow", "IHSG OR \"MSCI Indonesia\" OR \"investor asing\" when:1d", ("ihsg", "msci", "asing", "bursa", "saham")),
]
JAKARTA = timezone(timedelta(hours=7))


def fetch_quote(symbol: str) -> dict:
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
        "?range=10d&interval=1d"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)["chart"]["result"][0]
        bars = [
            (stamp, value)
            for stamp, value in zip(result["timestamp"], result["indicators"]["quote"][0]["close"])
            if value is not None
        ]
        closes = [value for _, value in bars]
        meta = result["meta"]
        last_time = datetime.fromtimestamp(meta["regularMarketTime"], JAKARTA)
        exchange_tz = timezone(timedelta(seconds=meta.get("gmtoffset", 0)))
        last_bar_day = datetime.fromtimestamp(bars[-1][0], exchange_tz).date()
        live_day = datetime.fromtimestamp(meta["regularMarketTime"], exchange_tz).date()
        live_price = meta.get("regularMarketPrice")
        # Yahoo sometimes adds the latest daily bar late (seen on ^JKSE / ^JKLQ45):
        # if the live quote is from a newer day than the last bar, treat it as the latest close.
        # Dates are compared in the exchange's own time zone so US bars are not double-counted.
        if live_price and live_day > last_bar_day:
            closes.append(live_price)
        return {
            "symbol": symbol,
            "last": closes[-1],
            "previous": closes[-2],
            "change_pct": (closes[-1] / closes[-2] - 1) * 100,
            "time": last_time,
        }
    except Exception as error:
        return {"symbol": symbol, "error": repr(error)[:80]}


def fetch_news(query: str, keywords: tuple, limit: int = 6) -> list:
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=id&gl=ID&ceid=ID:id"
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            feed = ET.fromstring(response.read())
    except Exception as error:
        return [{"title": f"News unavailable ({error!r:.60})", "link": "", "time": None}]
    items = []
    seen = set()
    cutoff = datetime.now(JAKARTA) - timedelta(hours=30)
    for item in feed.findall("./channel/item"):
        title = item.findtext("title") or ""
        key = title.lower()[:70]
        if key in seen or not any(word in title.lower() for word in keywords):
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate")).astimezone(JAKARTA)
        except Exception:
            published = None
        if published is not None and published < cutoff:
            continue
        seen.add(key)
        items.append({"title": title, "link": item.findtext("link") or "", "time": published})
    items.sort(key=lambda entry: entry["time"] or datetime.min.replace(tzinfo=JAKARTA), reverse=True)
    return items[:limit]


def opening_bias(quotes: dict) -> tuple:
    score = 0
    reasons = []

    def change(symbol):
        quote = quotes.get(symbol, {})
        return quote.get("change_pct") if "error" not in quote else None

    eido = change("EIDO")
    if eido is not None:
        if eido >= 1:
            score += 2
            reasons.append(f"EIDO +{eido:.1f}%: foreigners bought Indonesia overnight")
        elif eido >= 0.3:
            score += 1
            reasons.append(f"EIDO +{eido:.1f}%: mild foreign interest overnight")
        elif eido <= -1:
            score -= 2
            reasons.append(f"EIDO {eido:.1f}%: foreigners sold Indonesia overnight")
        elif eido <= -0.3:
            score -= 1
            reasons.append(f"EIDO {eido:.1f}%: mild foreign selling overnight")

    usd_idr = change("IDR=X")
    if usd_idr is not None:
        if usd_idr >= 0.3:
            score -= 1
            reasons.append(f"Rupiah weaker (USD/IDR +{usd_idr:.2f}%): negative for banks and foreign flow")
        elif usd_idr <= -0.3:
            score += 1
            reasons.append(f"Rupiah stronger (USD/IDR {usd_idr:.2f}%): supportive for banks")

    spx = change("^GSPC")
    if spx is not None:
        if spx >= 0.7:
            score += 1
            reasons.append(f"Wall Street up (S&P {spx:+.1f}%)")
        elif spx <= -0.7:
            score -= 1
            reasons.append(f"Wall Street down (S&P {spx:+.1f}%)")

    tnx = quotes.get("^TNX", {})
    if "error" not in tnx and "last" in tnx:
        basis_points = (tnx["last"] - tnx["previous"]) * 100
        if basis_points >= 8:
            score -= 1
            reasons.append(f"US 10Y yield +{basis_points:.0f}bp: pressure on EM assets")
        elif basis_points <= -8:
            score += 1
            reasons.append(f"US 10Y yield {basis_points:.0f}bp: relief for EM assets")

    nikkei = change("^N225")
    if nikkei is not None:
        if nikkei >= 0.7:
            score += 1
            reasons.append(f"Asia opening firm (Nikkei {nikkei:+.1f}%)")
        elif nikkei <= -0.7:
            score -= 1
            reasons.append(f"Asia opening weak (Nikkei {nikkei:+.1f}%)")

    if score >= 2:
        label = "Positive"
    elif score <= -2:
        label = "Negative"
    else:
        label = "Neutral"
    if not reasons:
        reasons.append("No strong overnight signals")
    return label, score, reasons


def pivot_levels(high: float, low: float, close: float) -> dict:
    pivot = (high + low + close) / 3
    return {
        "R2": pivot + (high - low),
        "R1": 2 * pivot - low,
        "Pivot": pivot,
        "S1": 2 * pivot - high,
        "S2": pivot - (high - low),
    }


def round_to_tick(price: float) -> int:
    """Round to the IDX tick for this price band (Rp10 between 2,000 and 5,000)."""
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


def legacy_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-open", action="store_true", help="don't open the HTML brief")
    args = parser.parse_args()
    now = datetime.now()

    # Yesterday's BMRI data (fresh pull so the brief works even if the close job didn't run)
    prices, _ = core.fetch_prices()
    prices = core.add_indicators(prices)
    if prices.index[-1].date() == now.date() and now.hour < 9:
        prices = prices.iloc[:-1]   # ignore any pre-open placeholder bar
    last = prices.iloc[-1]
    previous_close = prices["Close"].iloc[-2]
    ta = core.technical_analysis(prices)
    summary = {}
    if core.SUMMARY_FILE.exists():
        summary = json.loads(core.SUMMARY_FILE.read_text(encoding="utf-8"))
    broksum_signal = summary.get("broksum_signal") if summary.get("date") == f"{prices.index[-1]:%Y-%m-%d}" else None
    net_foreign = summary.get("net_foreign") if broksum_signal or summary.get("net_foreign") else None
    history = core.broksum_history_summary()

    with ThreadPoolExecutor(max_workers=8) as pool:
        quotes = {quote["symbol"]: quote for quote in pool.map(fetch_quote, [s for s, _ in GLOBAL_MARKETS])}
        news_futures = {
            label: pool.submit(fetch_news, query, keywords)
            for label, query, keywords in NEWS_QUERIES
        }
        news = {label: future.result() for label, future in news_futures.items()}

    bias_label, bias_score, bias_reasons = opening_bias(quotes)
    pivots = pivot_levels(last["High"], last["Low"], last["Close"])

    # ---- sections
    change_pct = (last["Close"] / previous_close - 1) * 100
    recap = (
        f"<p class='big'>{last['Close']:,.0f} "
        f"<span class='{core.signed_class(change_pct)}'>{change_pct:+.2f}%</span>"
        f" <span class='muted'>({prices.index[-1]:%a %d %b})</span></p>"
        + (
            f"<p>Your position: avg {core.AVERAGE_PRICE:,} → "
            f"<span class='{core.signed_class(last['Close'] - core.BREAKEVEN_PRICE)}'>"
            f"{(last['Close'] / core.BREAKEVEN_PRICE - 1) * 100:+.1f}% vs breakeven</span>. "
            f"Distance to stop {last['Close'] - core.STOP_CLOSE:+,.0f} · to T1 {core.TARGET_1 - last['Close']:+,.0f} · "
            f"to breakout {core.BREAKOUT_LEVEL - last['Close']:+,.0f}</p>"
            if core.HOLDING_BMRI
            else "<p>Position: none (watch only).</p>"
        )
        + core.bullet_list(
            [
                f"Technical bias: {ta['bias']} (score {ta['score']:+d})",
                f"Trend: {ta['trend']}",
                f"Momentum: {ta['momentum']}",
                f"Candle: {ta['candle']}",
                "Broksum yesterday: " + (broksum_signal or "not entered")
                + (f" · net foreign Rp{net_foreign:+,.1f}bn" if net_foreign is not None else ""),
            ]
            + (
                [
                    f"Broksum last {history['days']} sessions: foreign Rp{history['foreign_sum']:+,.1f}bn, "
                    f"institutions Rp{history['institutional_sum']:+,.1f}bn, retail Rp{history['retail_sum']:+,.1f}bn"
                ]
                if history
                else []
            )
            + [f"Alert yesterday: {alert}" for alert in summary.get("alerts", [])]
        )
    )

    market_rows = []
    for symbol, label in GLOBAL_MARKETS:
        quote = quotes.get(symbol, {})
        if "error" in quote or "last" not in quote:
            market_rows.append(f"<tr><td>{html.escape(label)}</td><td colspan='3' class='muted'>unavailable</td></tr>")
            continue
        if symbol == "^TNX":
            move = f"{(quote['last'] - quote['previous']) * 100:+.0f}bp"
            css = core.signed_class(-(quote["last"] - quote["previous"]))
        elif symbol == "IDR=X":
            move = f"{quote['change_pct']:+.2f}%"
            css = core.signed_class(-quote["change_pct"])     # weaker rupiah shown red
        else:
            move = f"{quote['change_pct']:+.2f}%"
            css = core.signed_class(quote["change_pct"])
        market_rows.append(
            f"<tr><td>{html.escape(label)}</td><td>{quote['last']:,.2f}</td>"
            f"<td class='{css}'>{move}</td><td class='muted'>{quote['time']:%d %b %H:%M} WIB</td></tr>"
        )
    markets = (
        f"<p class='big'>Opening bias: {bias_label} <span class='muted'>(score {bias_score:+d})</span></p>"
        + core.bullet_list(bias_reasons)
        + "<table><tr><th>Market</th><th>Last</th><th>Change</th><th>As of</th></tr>"
        + "".join(market_rows)
        + "</table><p class='muted'>Rough heuristic from overnight moves; EIDO is the best "
        "free overnight proxy for foreign appetite for Indonesian stocks.</p>"
    )

    plan_rows = [
        ("Breakout (close + volume)", core.BREAKOUT_LEVEL),
        ("T1: sell ~half", core.TARGET_1),
        ("Pivot R2", round_to_tick(pivots["R2"])),
        ("Pivot R1", round_to_tick(pivots["R1"])),
        ("Pivot", round_to_tick(pivots["Pivot"])),
        ("Pivot S1", round_to_tick(pivots["S1"])),
        ("Pivot S2", round_to_tick(pivots["S2"])),
        ("Stop (daily CLOSE below)", core.STOP_CLOSE),
    ]
    plan_rows.sort(key=lambda row: -row[1])
    levels_table = "<table><tr><th>Level</th><th>Price</th><th>Distance</th></tr>" + "".join(
        f"<tr><td>{html.escape(name)}</td><td>{value:,}</td><td>{value - last['Close']:+,.0f}</td></tr>"
        for name, value in plan_rows
    ) + "</table>"
    r1 = round_to_tick(pivots["R1"])
    s1 = round_to_tick(pivots["S1"])
    rules = [
        "08:45–09:00 pre-opening: compare BMRI's IEP (indicative opening price) in your app with "
        f"yesterday's close {last['Close']:,.0f}.",
        f"Strength: opens and holds above R1 {r1:,} for the first 30 minutes → momentum toward "
        f"T1 {core.TARGET_1:,}; have your partial-sell order ready there.",
        f"Weakness: breaks below S1 {s1:,} on heavy volume → don't add; next supports "
        + ", ".join(f"{value:,} ({name})" for value, name in ta["supports"][:2]) + ".",
        f"Stop rule: only a daily CLOSE below {core.STOP_CLOSE:,} triggers the exit; intraday dips alone don't.",
        f"Opening bias {bias_label.lower()} + technical bias {ta['bias'].lower()}"
        + (f" + broksum {broksum_signal.lower()}" if broksum_signal else "")
        + ": " + (
            "conditions supportive; let the plan work."
            if bias_label == "Positive" and "bullish" in ta["bias"].lower()
            else "be patient; no reason to add today."
            if bias_label != "Negative"
            else "expect pressure at the open; avoid adding, respect the stop."
        ),
    ]
    plan = levels_table + core.bullet_list(rules)

    news_html = ""
    for label, items in news.items():
        news_html += f"<p><b>{html.escape(label)}</b></p><ul>"
        if not items:
            news_html += "<li class='muted'>No headlines in the last 24 h</li>"
        for item in items:
            when = f"{item['time']:%d %b %H:%M}" if item["time"] else ""
            news_html += (
                f"<li><span class='muted'>{when}</span> "
                f"<a href='{html.escape(item['link'])}' target='_blank'>{html.escape(item['title'])}</a></li>"
            )
        news_html += "</ul>"

    css_bias = {"Positive": "ok", "Negative": "alert"}.get(bias_label, "warn")

    # Big 4 banks: use the close job's summary; rebuild it if missing or stale
    banks_summary = big_banks.load_summary()
    if banks_summary.get("date") != f"{prices.index[-1]:%Y-%m-%d}":
        try:
            big_banks.run()
            banks_summary = big_banks.load_summary()
        except Exception:
            pass
    if banks_summary.get("banks"):
        banks_html = (
            f"<p class='muted'>As of {banks_summary['date']} close · IHSG "
            f"{banks_summary['ihsg_close']:,.0f} ({big_banks.fmt_pct(banks_summary['ihsg_change_1d'])})</p>"
            + "<p><b>Direction outlook</b></p>"
            + big_banks.outlook_html(banks_summary["banks"])
            + big_banks.html_table(banks_summary["banks"])
            + "<p><b>Entry setups (BBCA / BBRI / BBNI)</b></p>"
            + big_banks.entry_table_html(banks_summary["banks"])
            + "<ul>"
            + "".join(
                f"<li><b>{r['code']}</b>: watch {html.escape(r['watch'])}</li>"
                for r in banks_summary["banks"]
            )
            + "</ul>"
        )
    else:
        banks_html = "<p>Big banks data unavailable.</p>"

    ihsg_summary = ihsg_analysis.load_summary()
    if ihsg_summary.get("date") != f"{prices.index[-1]:%Y-%m-%d}":
        try:
            ihsg_summary = ihsg_analysis.run(banks_summary.get("banks"))
        except Exception:
            pass

    sections = [
        ("BMRI: yesterday's recap", recap, "alert" if summary.get("alerts") else "ok"),
        ("Overnight markets", markets, css_bias),
        ("IHSG (market direction)", ihsg_analysis.section_html(ihsg_summary), ""),
        ("Big 4 banks", banks_html, ""),
        ("Today's game plan", plan, ""),
        ("News (last 24 h)", news_html, ""),
    ]
    core.REPORT_DIR.mkdir(exist_ok=True)
    brief_html = core.render_html(
        f"BMRI morning brief — {now:%a %d %b %Y}",
        f"Generated {now:%H:%M} WIB · pre-opening 08:45 · market opens 09:00",
        sections,
        core.CHART_FILE,
    )
    dated = core.REPORT_DIR / f"morning_{now:%Y-%m-%d}.html"
    latest = core.REPORT_DIR / "latest_morning.html"
    dated.write_text(brief_html, encoding="utf-8")
    latest.write_text(brief_html, encoding="utf-8")

    print(f"Morning brief {now:%d %b %H:%M}: BMRI {last['Close']:,.0f} ({change_pct:+.2f}%), "
          f"opening bias {bias_label} ({bias_score:+d}), technical {ta['bias']}")
    for reason in bias_reasons:
        print(f"  - {reason}")
    print(f"Pivots: R1 {r1:,} | P {round_to_tick(pivots['Pivot']):,} | S1 {s1:,}")
    print(f"News: " + ", ".join(f"{label}: {len(items)}" for label, items in news.items()))
    print(f"HTML brief: {dated}")
    if not args.no_open:
        core.open_report(latest)


def main() -> None:
    """Morning brief for YOUR holdings, queue plans and market conditions (the old BMRI brief: --legacy)."""
    import sys
    if "--legacy" in sys.argv:
        sys.argv.remove("--legacy")
        legacy_main()
        return
    import daily_reports
    result = daily_reports.build_morning()
    print(result["text"])
    print(f"Morning brief: {result['path']}")
    if datetime.now().weekday() < 5:
        bias = result["summary"]["bias"]
        urgent = [daily_reports.html_to_text(m) for m in result["summary"]["messages"] if "red" in m or "amber" in m]
        body = "\n".join((["ACTION:"] + [f"- {m}" for m in urgent] + [""] if urgent else []) + [result["text"]])
        try:
            daily_reports.notify_once("morning", f"MORNING BRIEF - opening bias {bias}", body + "\n\n(Full brief opened in your browser / app Reports page)")
        except Exception:
            pass
    if "--no-open" not in sys.argv:
        core.open_report(result["path"])


if __name__ == "__main__":
    import sys

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()

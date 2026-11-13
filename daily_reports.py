"""
Morning brief (08:00) and closing report (16:20) built around YOUR holdings, queue plans and the market.

  python daily_reports.py morning      build + open the morning brief
  python daily_reports.py close        build + open the closing report

Holdings come from portfolio.json, queue plans from trade_plans.json, alerts from price_alerts.json.
Reports are written to reports/ (morning_YYYY-MM-DD.html, close_YYYY-MM-DD.html, latest_*.html)
and are also shown in the app (Reports page).
Free data, ~10 min delayed during the session. Education only - not financial advice.
"""

import html
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import live_data
import market_context
import portfolio
import trade_tools

FOLDER = Path(__file__).resolve().parent
REPORT_DIR = FOLDER / "reports"
STATE_FILE = FOLDER / "report_notify_state.json"


def esc(value) -> str:
    return html.escape(str(value))


def num(value, digits=0, suffix=""):
    return "–" if value is None else f"{value:,.{digits}f}{suffix}"


def pct(value, digits=1):
    if value is None:
        return "<span class='grey'>–</span>"
    return f"<span class='{'green' if value >= 0 else 'red'}'>{value:+.{digits}f}%</span>"


def rupiah(value):
    if value is None:
        return "–"
    sign = "−" if value < 0 else "+"
    value = abs(value)
    if value >= 1e9:
        return f"{sign}Rp{value / 1e9:,.2f}B"
    if value >= 1e6:
        return f"{sign}Rp{value / 1e6:,.1f}M"
    return f"{sign}Rp{value:,.0f}"


def bullets(items) -> str:
    items = [item for item in items if item]
    return "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>" if items else ""


def pivots(high, low, close) -> dict:
    p = (high + low + close) / 3
    return {
        "R2": trade_tools.round_to_tick(p + (high - low)),
        "R1": trade_tools.round_to_tick(2 * p - low),
        "P": trade_tools.round_to_tick(p),
        "S1": trade_tools.round_to_tick(2 * p - high),
        "S2": trade_tools.round_to_tick(p - (high - low)),
    }


# ---------------------------------------------------------------- shared data

def last_completed_bar(code: str, morning: bool):
    """Latest daily bar; in the morning ignore a placeholder bar for today."""
    bars = live_data.get_candles(code, "1D")["bars"]
    today = datetime.now().date()
    if morning and bars and datetime.utcfromtimestamp(bars[-1]["time"]).date() >= today:
        bars = bars[:-1]
    return bars


def holding_rows(morning: bool) -> list:
    """Portfolio analysis + today's bar, plan, news and limits for each holding."""
    summary = portfolio.build()
    rows = [row for row in summary.get("holdings", []) if "error" not in row]
    errors = [row for row in summary.get("holdings", []) if "error" in row]

    def enrich(row):
        code = row["code"]
        out = dict(row)
        try:
            bars = last_completed_bar(code, morning)
            bar, previous = bars[-1], bars[-2]
            out["bar"] = bar
            out["bar_change"] = (bar["close"] / previous["close"] - 1) * 100
            vols = [b["volume"] for b in bars[-21:-1] if b["volume"]]
            out["rel_volume"] = bar["volume"] / (sum(vols) / len(vols)) if vols else None
            out["pivots"] = pivots(bar["high"], bar["low"], bar["close"])
            out["limits"] = trade_tools.auto_rejection(bar["close"])
        except Exception as error:
            out["bar_error"] = str(error)
        try:
            name = (trade_tools.stock_detail(code) or {}).get("description", "")
            cutoff = time.time() - (36 if morning else 24) * 3600
            out["news"] = [n for n in trade_tools.stock_news(code, name) if n.get("sort", 0) >= cutoff][:4]
        except Exception:
            out["news"] = []
        out["queue_plan"] = trade_tools.get_plan(code)
        return out

    with ThreadPoolExecutor(max_workers=6) as pool:
        enriched = list(pool.map(enrich, rows))
    return enriched + errors


def plan_rows(morning: bool) -> list:
    plans = [p for p in trade_tools.load_plans() if p.get("status", "active") == "active"]
    out = []
    for plan in plans:
        try:
            bars = last_completed_bar(plan["code"], morning)
            bar = bars[-1]
            out.append({"plan": plan, "bar": bar, "summary": trade_tools.plan_summary(plan, bar["close"]),
                        "limits": trade_tools.auto_rejection(bar["close"])})
        except Exception as error:
            out.append({"plan": plan, "error": str(error)})
    return out


def market_block(context: dict, close_mode: bool) -> tuple:
    """HTML for the market conditions section + short text lines."""
    regime = context["regime"]
    ihsg = context["ihsg"]
    lines = [f"IHSG {ihsg['last']:,.0f} ({ihsg['change_1d']:+.2f}%) - {ihsg['bias']}; regime {regime['label']}"]
    piv = ihsg["pivots"]
    levels = (
        "<table><tr><th></th><th>Level</th><th>Why</th><th>From last</th></tr>"
        + "".join(f"<tr><td class='red'>R{i + 1}</td><td><b>{v:,}</b></td><td>{esc(n)}</td><td>{(v / ihsg['last'] - 1) * 100:+.1f}%</td></tr>"
                  for i, (v, n) in reversed(list(enumerate(ihsg["resistances"]))))
        + f"<tr class='now'><td><b>{'Close' if close_mode else 'Last'}</b></td><td><b>{ihsg['last']:,.0f}</b></td><td></td><td></td></tr>"
        + "".join(f"<tr><td class='green'>S{i + 1}</td><td><b>{v:,}</b></td><td>{esc(n)}</td><td>{(v / ihsg['last'] - 1) * 100:+.1f}%</td></tr>"
                  for i, (v, n) in enumerate(ihsg["supports"]))
        + "</table>"
        + f"<p class='muted'>Pivots ({esc(piv['based_on'])}): "
        + " · ".join(f"{k} <b>{piv[k]:,}</b>" for k in ("S2", "S1", "P", "R1", "R2")) + "</p>"
    )
    scenarios = bullets(f"<b>{esc(name)}:</b> {esc(body)}" for name, body in ihsg["scenarios"])
    signals = bullets(f"{'▲' if s == '+' else '▼'} {esc(t)}" for s, t in ihsg["signals"][:6])
    regime_html = (
        f"<div class='banner {esc(regime['class'])}'><b>Market regime: {esc(regime['label'])} ({regime['score']:+d})</b><br>"
        f"{esc('; '.join(regime['reasons']))}.<br><i>{esc(regime['advice'])}</i></div>"
    )
    ihsg_html = (
        f"<div class='grid2'><div><p class='big'>IHSG {ihsg['last']:,.2f} {pct(ihsg['change_1d'], 2)}</p>"
        f"<p>5D {pct(ihsg['change_5d'])} · 1M {pct(ihsg['change_1m'])} · YTD {pct(ihsg['change_ytd'])} · RSI {ihsg['rsi']:.0f} · "
        f"ATR {ihsg['atr']:,.0f}</p><p><b>Technical bias: {esc(ihsg['bias'])} ({ihsg['score']:+d})</b></p>{signals}</div>"
        f"<div>{levels}</div></div><h3>Scenarios</h3>{scenarios}"
    )
    return regime_html + ihsg_html, lines


def breadth_block() -> tuple:
    try:
        b = trade_tools.breadth_and_sectors()
        m = trade_tools.movers(min_value_bn=10, limit=5)
    except Exception as error:
        return f"<p class='muted'>Breadth unavailable: {esc(error)}</p>", []
    sectors = b["sectors"]
    top = ", ".join(f"{esc(s['sector'])} {s['change']:+.1f}%" for s in sectors[:3])
    bottom = ", ".join(f"{esc(s['sector'])} {s['change']:+.1f}%" for s in sectors[-3:])

    def mover_list(rows):
        return ", ".join(f"<b>{esc(s['code'])}</b> {s['change']:+.1f}%" for s in rows) or "–"

    html_out = (
        f"<p>Advancers <b class='green'>{b['advancers']}</b> · decliners <b class='red'>{b['decliners']}</b> · "
        f"unchanged {b['unchanged']} · value traded <b>Rp{b['total_value_bn'] / 1000:,.2f}T</b> · "
        f"liquid stocks above MA200 <b>{b['above_ma200_pct']}%</b></p>"
        f"<p>Strongest sectors: {top}<br>Weakest sectors: {bottom}</p>"
        f"<p>Top liquid gainers: {mover_list(m['gainers'])}<br>Top liquid losers: {mover_list(m['losers'])}<br>"
        f"Most traded: {mover_list(m['value'])}</p>"
    )
    line = f"Breadth {b['advancers']} up / {b['decliners']} down, value Rp{b['total_value_bn'] / 1000:,.2f}T"
    return html_out, [line]


def global_block(context: dict) -> str:
    rows = ""
    for row in context.get("indices", []):
        if "error" in row or row["region"] == "Indonesia":
            continue
        rows += (f"<tr><td>{esc(row['name'])} <span class='muted'>{esc(row['date'])}</span></td><td>{row['last']:,.2f}</td>"
                 f"<td>{pct(row['change_1d'], 2)}</td><td>{pct(row['change_5d'])}</td><td class='{esc(row['status_class'])}'>{esc(row['status'])}</td></tr>")
    macro = context.get("macro", {})
    macro_rows = "".join(
        f"<tr><td>{esc(r['name'])}</td><td>{r['last']:,.{r['decimals']}f}</td><td>{pct(r['change_1d'], 2)}</td>"
        f"<td>{pct(r['change_5d'])}</td><td class='small'>{esc(r['impact_note'])}</td></tr>"
        for r in macro.get("rows", []) if "error" not in r
    )
    view = macro.get("view", {})
    return (
        "<div class='grid2'><div><h3>Global indices</h3><table><tr><th>Index</th><th>Last</th><th>1D</th><th>5D</th><th>Trend</th></tr>"
        + rows + "</table></div><div><h3>Macro</h3>"
        + (f"<p><b>{esc(view.get('label', ''))}</b> for IDX ({view.get('score', 0):+d})</p>" if view else "")
        + "<table><tr><th></th><th>Last</th><th>1D</th><th>5D</th><th>Impact</th></tr>" + macro_rows + "</table></div></div>"
    )


def news_block(context: dict, limit=4) -> str:
    out = ""
    for category, items in (context.get("news") or {}).items():
        entries = "".join(
            f"<li><span class='dot {esc(i.get('tone', 'neutral'))}'></span><a href='{esc(i['link'])}' target='_blank'>{esc(i['title'])}</a> "
            f"<span class='muted'>{esc(i.get('source', ''))} · {esc(i.get('time') or '')}</span></li>"
            for i in items[:limit] if i.get("link")
        )
        if entries:
            out += f"<h3>{esc(category)}</h3><ul class='news'>{entries}</ul>"
    return out or "<p class='muted'>No fresh headlines.</p>"


# ---------------------------------------------------------------- holding cards

def holding_card(row: dict, close_mode: bool) -> tuple:
    """Card HTML, short text line, list of important messages."""
    code = row["code"]
    if "error" in row:
        return f"<div class='card'><h2>{esc(code)}</h2><p class='red'>Data error: {esc(row['error'])}</p></div>", f"{code}: data error", []
    bar = row.get("bar") or {}
    close = bar.get("close", row["price"])
    lots = row.get("lots")
    shares = (lots or 0) * 100
    pnl_pct = (close / row["avg"] - 1) * 100
    day_rp = (close - close / (1 + row.get("bar_change", 0) / 100)) * shares if lots and row.get("bar_change") is not None else None
    pnl_rp = (close - row["avg"]) * shares if lots else None
    targets = row["targets"]
    hurdle = row.get("hurdle")
    exit_level, last_line = row["exit_level"], row["last_line"]
    messages = []
    status_class = row.get("verdict_class", "grey")

    if close < last_line:
        messages.append(f"<b class='red'>{code} closed {close:,.0f} below the LAST LINE {last_line:,}</b> - exit, no excuses.")
        status_class = "red"
    elif close < exit_level:
        messages.append(f"<b class='red'>{code} closed {close:,.0f} below the EXIT {exit_level:,}</b> ({row['exit_basis']}) - "
                        "the plan says sell at the next session (or cut size).")
        status_class = "red"
    elif close < exit_level * 1.03:
        messages.append(f"<b class='amber'>{code} {close:,.0f} is within 3% of the exit {exit_level:,}</b> - be ready.")
    for i, (level, name) in enumerate(targets, start=1):
        if bar.get("high") and bar["high"] >= level:
            messages.append(f"<b class='green'>{code} reached T{i} {level:,}</b> ({esc(name)}) - take the planned partial profit"
                            f"{' and move the exit up' if i == 1 else ''}.")
    rel = row.get("rel_volume")
    if rel and rel >= 1.8:
        direction = "up" if row.get("bar_change", 0) >= 0 else "down"
        messages.append(f"{code} volume {rel:.1f}× the 20-day average on a {direction} day - "
                        + ("buyers stepping in." if direction == "up" else "heavy selling, respect the exit."))

    levels = (
        "<table><tr><th></th><th>Level</th><th>Why</th><th>From close</th></tr>"
        + "".join(f"<tr><td class='green'>T{i}</td><td><b>{lv:,}</b></td><td>{esc(n)}</td><td>{(lv / close - 1) * 100:+.1f}%</td></tr>"
                  for i, (lv, n) in reversed(list(enumerate(targets, start=1))))
        + (f"<tr><td class='amber'>Hurdle</td><td>{hurdle[0]:,}</td><td>{esc(hurdle[1])}</td><td>{(hurdle[0] / close - 1) * 100:+.1f}%</td></tr>" if hurdle else "")
        + f"<tr class='now'><td><b>Close</b></td><td><b>{close:,.0f}</b></td><td>avg {row['avg']:,.0f} · breakeven {row['breakeven']:,}</td><td>{pct(pnl_pct)}</td></tr>"
        + f"<tr><td class='red'><b>EXIT</b></td><td class='red'><b>{exit_level:,}</b></td><td>daily close below · {esc(row['exit_basis'])}</td><td>{(exit_level / close - 1) * 100:+.1f}%</td></tr>"
        + f"<tr><td class='red'>Last line</td><td class='red'>{last_line:,}</td><td>no excuses below</td><td>{(last_line / close - 1) * 100:+.1f}%</td></tr></table>"
    )
    piv = row.get("pivots")
    limits = row.get("limits") or {}
    if close_mode:
        today_html = (
            f"<p>O {num(bar.get('open'))} · H {num(bar.get('high'))} · L {num(bar.get('low'))} · C <b>{num(close)}</b> "
            f"{pct(row.get('bar_change'), 2)} · volume {num((bar.get('volume') or 0) / 1e6, 1, 'M')}"
            f"{f' ({rel:.1f}× avg)' if rel else ''}</p>"
        )
        game = [
            f"Tomorrow's pivots: S1 <b>{piv['S1']:,}</b> · P <b>{piv['P']:,}</b> · R1 <b>{piv['R1']:,}</b> · "
            f"ARA {num(limits.get('ara'))} / ARB {num(limits.get('arb'))}" if piv else "",
            f"Still above the exit {exit_level:,}: hold within plan." if close >= exit_level else
            f"Closed below the exit {exit_level:,}: follow the plan at the next open - don't average down.",
            f"Next upside check: {('hurdle ' + format(hurdle[0], ',')) if hurdle and close < hurdle[0] else ('T1 ' + format(targets[0][0], ',')) if targets else '–'}.",
            "Paste today's broker summary in the app (stock page → Broksum) after 17:00 if you have it."
            if not (row.get("broksum") or {}).get("entries") or (row["broksum"]["entries"][0]["date"] != date.today().isoformat()) else "",
        ]
    else:
        today_html = (
            f"<p>Last close <b>{num(close)}</b> {pct(row.get('bar_change'), 2)} ({esc(datetime.utcfromtimestamp(bar['time']).strftime('%a %d %b')) if bar else ''}) · "
            f"today's limits ARA <b>{num(limits.get('ara'))}</b> / ARB <b>{num(limits.get('arb'))}</b></p>"
        )
        game = [
            f"Pre-open 08:45–09:00: compare the IEP (indicative opening price) with yesterday's close {close:,.0f}." ,
            f"Strength: holds above R1 <b>{piv['R1']:,}</b> after 30 min → room toward "
            f"{('hurdle ' + format(hurdle[0], ',')) if hurdle and close < hurdle[0] else ('T1 ' + format(targets[0][0], ',')) if targets else 'the next resistance'}." if piv else "",
            f"Weakness: breaks S1 <b>{piv['S1']:,}</b> / S2 <b>{piv['S2']:,}</b> on heavy volume → don't add." if piv else "",
            f"Exit rule: only a daily <b>CLOSE below {exit_level:,}</b> triggers the exit - intraday dips alone don't.",
            f"Take profit: have a sell order ready at T1 <b>{targets[0][0]:,}</b> (sell ⅓–½)." if targets else "",
        ]
    plan = row.get("queue_plan")
    plan_html = ""
    if plan:
        open_q = [e for e in plan["entries"] if not e.get("filled")]
        plan_html = (
            "<h3>Queue plan</h3><p>"
            + " · ".join(f"Q{i + 1} {e['price']:,} × {e['lots']} lot{' ✓ filled' if e.get('filled') else ''}"
                         for i, e in enumerate(plan["entries"]))
            + f" · plan exit (close) <b class='red'>{plan['exit']['price']:,}</b></p>"
            + (f"<p class='muted'>{len(open_q)} queue(s) still open - re-enter them in your broker app {'today' if not close_mode else 'tomorrow morning'}.</p>" if open_q else "")
        )
        if plan["exit"]["price"] != exit_level:
            plan_html += (f"<p class='muted'>Note: your queue plan's exit ({plan['exit']['price']:,}) differs from the "
                          f"automatic exit ({exit_level:,}). Decide which one you follow - and stick to it.</p>")
    news = row.get("news") or []
    news_html = ("<h3>News</h3><ul class='news'>" + "".join(
        f"<li><span class='dot {esc(n.get('tone', 'neutral'))}'></span><a href='{esc(n['link'])}' target='_blank'>{esc(n['title'])}</a> "
        f"<span class='muted'>{esc(n.get('source', ''))} · {esc(n.get('time') or '')}</span></li>" for n in news) + "</ul>") if news else ""
    lots_line = (f"{lots} lots · value {rupiah(close * shares).lstrip('+')} · "
                 f"{'today ' + rupiah(day_rp) + ' · ' if day_rp is not None else ''}P/L {rupiah(pnl_rp)}"
                 if lots else "<span class='muted'>lots not set - add them on the Portfolio page to see Rupiah P/L</span>")
    card = f"""
<div class='card {status_class}-border'>
  <div class='head'><h2>{esc(code)}</h2><span class='big'>{close:,.0f}</span> {pct(row.get('bar_change'), 2)}
    <span>P/L {pct(pnl_pct)}</span><span class='verdict {esc(row.get('verdict_class', ''))}'>{esc(row['verdict'])}</span></div>
  <p class='muted'>avg {row['avg']:,.0f} · {lots_line} · ATR {row['atr']:,.0f} ({row['atr_pct']:.0f}%/day) · RSI {row['rsi']:.0f} ·
    flow: {esc(row['flow']['label'])}</p>
  {bullets(messages)}
  <div class='grid2'><div>{today_html}<h3>{'Plan for tomorrow' if close_mode else "Today's game plan"}</h3>{bullets(game)}{plan_html}</div>
  <div>{levels}</div></div>
  {news_html}
</div>"""
    text = f"{code} {close:,.0f} ({row.get('bar_change', 0):+.2f}%), P/L {pnl_pct:+.1f}% · exit {exit_level:,} · {row['verdict']}"
    return card, text, messages


def plans_section(rows: list, close_mode: bool, held: set) -> tuple:
    cards, messages = "", []
    for item in rows:
        plan = item["plan"]
        code = plan["code"]
        if "error" in item:
            cards += f"<div class='card'><h2>{esc(code)}</h2><p class='muted'>{esc(item['error'])}</p></div>"
            continue
        bar, s, limits = item["bar"], item["summary"], item["limits"]
        rows_html = ""
        for i, e in enumerate(plan["entries"], start=1):
            touched = (not e.get("filled")) and bar["low"] <= e["price"]
            state = ("✓ filled" if e.get("filled") else
                     ("<b class='amber'>price traded here today - check your fill</b>" if close_mode and touched else
                      ("<span class='muted'>below today's ARB - can't fill today</span>" if not close_mode and e["price"] < limits["arb"] else "open")))
            if close_mode and touched:
                messages.append(f"<b class='amber'>{code} queue {i} ({e['price']:,} × {e['lots']} lot)</b>: today's low {bar['low']:,.0f} "
                                "reached it - confirm the fill in your broker app, then press 'Mark filled' in the app.")
            rows_html += (f"<tr><td>Q{i}</td><td><b>{e['price']:,}</b></td><td>{e['lots']}</td><td>{(e['price'] / bar['close'] - 1) * 100:+.1f}%</td>"
                          f"<td>{state}</td></tr>")
        if close_mode and bar["close"] < plan["exit"]["price"]:
            messages.append(f"<b class='red'>{code} closed {bar['close']:,.0f} below the plan exit {plan['exit']['price']:,}</b> - "
                            "the plan is invalid: cancel open queues, exit any filled part.")
        trig = (plan.get("breakout") or {}).get("trigger")
        if close_mode and trig and bar["close"] > trig:
            messages.append(f"<b class='green'>{code} closed {bar['close']:,.0f} above the breakout trigger {trig:,}</b> - "
                            "Plan B (breakout entry) is valid for tomorrow.")
        cards += f"""
<div class='card'>
  <div class='head'><h2>{esc(code)}</h2><span class='big'>{bar['close']:,.0f}</span>
    <span class='muted'>{'held' if code in held else 'not held yet'} · L {bar['low']:,.0f} · H {bar['high']:,.0f}</span>
    <span class='verdict {esc(s['status_class'])}'>{esc(s['status'])}</span></div>
  <div class='grid2'><div><table><tr><th>Queue</th><th>Price</th><th>Lots</th><th>From close</th><th>Status</th></tr>{rows_html}</table></div>
  <div><p>If all fill: avg <b>{s['avg']:,.0f}</b> · {s['total_lots']} lots · max loss <b class='red'>{rupiah(-s['risk_rp'])}</b>
    ({s['risk_pct']:.1f}%)</p>
  <p>Exit: daily close below <b class='red'>{plan['exit']['price']:,}</b> · targets {', '.join(format(t, ',') for t in plan['targets']) or '–'}
  {f" · Plan B: close above <b>{trig:,}</b>" if trig else ''}</p>
  {'' if close_mode else f"<p class='muted'>Today's ARA {limits['ara']:,} / ARB {limits['arb']:,}. Day orders expire at 16:00 - re-enter open queues now.</p>"}
  </div></div>
</div>"""
    return cards, messages


# ---------------------------------------------------------------- render

CSS = """
body { font-family: 'Segoe UI', Arial, sans-serif; background: #0a1020; color: #e6edf7; margin: 0; font-size: 14px; }
main { max-width: 1180px; margin: 0 auto; padding: 18px 22px 40px; }
h1 { font-size: 22px; margin: 0; } h2 { font-size: 18px; margin: 0; } h3 { font-size: 12px; color: #8b9bb4; text-transform: uppercase; letter-spacing: .5px; margin: 14px 0 6px; }
.top { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.card { background: #111a2e; border: 1px solid #22304d; border-radius: 14px; padding: 14px 18px; margin-bottom: 14px; }
.section-title { font-size: 13px; text-transform: uppercase; letter-spacing: .6px; color: #38bdf8; margin: 22px 0 8px; }
.head { display: flex; gap: 14px; align-items: baseline; flex-wrap: wrap; }
.big { font-size: 22px; font-weight: 800; }
.verdict { margin-left: auto; font-weight: 700; }
.grid2 { display: grid; grid-template-columns: 1.1fr 1fr; gap: 18px; } @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
table { border-collapse: collapse; width: 100%; font-size: 13px; } th { text-align: left; color: #8b9bb4; font-size: 11px; text-transform: uppercase; padding: 5px 6px; }
td { padding: 5px 6px; border-top: 1px solid #1c2842; } tr.now td { background: rgba(56,189,248,.1); }
.green { color: #22c55e; } .red { color: #f04461; } .amber { color: #f5b942; } .grey { color: #8b9bb4; } .muted { color: #8b9bb4; font-size: 12px; } .small { font-size: 12px; color: #a9b6cc; }
.red-border { border-left: 4px solid #f04461; } .green-border { border-left: 4px solid #22c55e; } .amber-border { border-left: 4px solid #f5b942; } .grey-border { border-left: 4px solid #64748b; }
.banner { border-radius: 10px; padding: 10px 14px; margin-bottom: 12px; border: 1px solid #22304d; background: rgba(100,116,139,.12); }
.banner.red { background: rgba(240,68,97,.12); border-color: rgba(240,68,97,.5); color: #fecdd3; }
.banner.green { background: rgba(34,197,94,.12); border-color: rgba(34,197,94,.45); }
.banner.amber { background: rgba(245,185,66,.12); border-color: rgba(245,185,66,.5); color: #fde68a; }
.todo { background: rgba(56,189,248,.08); border: 1px solid rgba(56,189,248,.4); border-radius: 12px; padding: 10px 16px; }
ul { margin: 6px 0; padding-left: 20px; } li { margin: 4px 0; } ul.news { list-style: none; padding: 0; } ul.news li { margin: 6px 0; }
a { color: #e6edf7; } .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; background: #64748b; }
.dot.positive { background: #22c55e; } .dot.negative { background: #f04461; }
"""


def render(title: str, subtitle: str, parts: list) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{esc(title)}</title><style>{CSS}</style></head><body><main>"
            f"<div class='top'><h1>{esc(title)}</h1><span class='muted'>{esc(subtitle)}</span></div>"
            + "".join(parts)
            + "<p class='muted'>Free data (TradingView / Yahoo Finance / Google News), delayed and sometimes wrong - confirm in your broker app. "
              "Exits are on the daily CLOSE. Education only, not financial advice.</p></main></body></html>")


def save(kind: str, html_text: str, summary: dict) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    dated = REPORT_DIR / f"{kind}_{date.today():%Y-%m-%d}.html"
    dated.write_text(html_text, encoding="utf-8")
    (REPORT_DIR / f"latest_{kind}.html").write_text(html_text, encoding="utf-8")
    (REPORT_DIR / f"latest_{kind}.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    return dated


def todo_box(items) -> str:
    items = [i for i in items if i]
    return f"<div class='todo'><b>To do</b>{bullets(items)}</div>" if items else ""


# ---------------------------------------------------------------- morning

def build_morning() -> dict:
    now = datetime.now()
    context = market_context.build()
    try:
        import bmri_morning_brief as mb
        with ThreadPoolExecutor(max_workers=8) as pool:
            quotes = {q["symbol"]: q for q in pool.map(mb.fetch_quote, [s for s, _ in mb.GLOBAL_MARKETS])}
        bias_label, bias_score, bias_reasons = mb.opening_bias(quotes)
    except Exception as error:
        bias_label, bias_score, bias_reasons = "Unknown", 0, [f"overnight data unavailable ({error!r:.60})"]
    holdings = holding_rows(morning=True)
    plans = plan_rows(morning=True)
    held = {h["code"] for h in holdings}

    market_html, market_lines = market_block(context, close_mode=False)
    breadth_html, breadth_lines = breadth_block()
    bias_class = {"Positive": "green", "Negative": "red"}.get(bias_label, "amber")
    cards, lines, messages = "", [], []
    for row in holdings:
        card, text, msgs = holding_card(row, close_mode=False)
        cards += card
        lines.append(text)
        messages += msgs
    plan_cards, plan_msgs = plans_section([p for p in plans if p["plan"]["code"] not in held] , False, held)
    active_alerts = [a for a in trade_tools.load_alerts() if a["active"]]
    todo = [
        f"Opening bias <b>{esc(bias_label)}</b> + regime <b>{esc(context['regime']['label'])}</b>: "
        + ("conditions are supportive - let winners run, stick to the plan." if bias_label == "Positive" and context['regime']['class'] == "green"
           else "expect pressure at the open - don't add, respect exits, keep queues at support only." if bias_label == "Negative" or context['regime']['class'] == "red"
           else "mixed - be patient, no chasing."),
        *(m for m in messages if "red" in m or "amber" in m),
        f"Re-enter open buy queues for: {', '.join(p['plan']['code'] for p in plans if any(not e.get('filled') for e in p['plan']['entries']))} "
        "(IDX day orders expire every day)." if any(any(not e.get('filled') for e in p['plan']['entries']) for p in plans) else "",
        f"{len(active_alerts)} price alerts are active - keep the app open during the session so they can pop up." if active_alerts else "",
    ]
    parts = [
        todo_box(todo),
        "<div class='section-title'>Market conditions</div>",
        f"<div class='card'><div class='banner {bias_class}'><b>Overnight / opening bias: {esc(bias_label)} ({bias_score:+d})</b>"
        f"{bullets(esc(r) for r in bias_reasons)}</div>{market_html}</div>",
        f"<div class='card'><h3>Yesterday's market breadth</h3>{breadth_html}</div>",
        f"<div class='card'>{global_block(context)}</div>",
        "<div class='section-title'>My holdings</div>",
        cards or "<div class='card muted'>No holdings in portfolio.json - add them on the Portfolio page.</div>",
    ]
    if plan_cards:
        parts += ["<div class='section-title'>Queue plans (not held yet)</div>", plan_cards]
    parts += ["<div class='section-title'>News</div>", f"<div class='card'>{news_block(context)}</div>"]
    report = render(f"Morning brief - {now:%a %d %b %Y}", f"Generated {now:%H:%M} WIB · pre-opening 08:45 · market opens 09:00", parts)
    text = "\n".join([f"Opening bias: {bias_label} ({bias_score:+d}) · regime {context['regime']['label']}"] + market_lines + lines)
    summary = {"generated": now.strftime("%Y-%m-%d %H:%M"), "text": text, "bias": bias_label, "regime": context["regime"]["label"],
               "holdings": lines, "messages": messages + plan_msgs}
    path = save("morning", report, summary)
    return {"path": path, "text": text, "summary": summary}


# ---------------------------------------------------------------- close

def build_close() -> dict:
    now = datetime.now()
    context = market_context.build()
    try:
        import all_stocks
        all_stocks.build()
    except Exception:
        pass
    holdings = holding_rows(morning=False)
    plans = plan_rows(morning=False)
    held = {h["code"] for h in holdings}
    market_html, market_lines = market_block(context, close_mode=True)
    breadth_html, breadth_lines = breadth_block()
    cards, lines, messages = "", [], []
    for row in holdings:
        card, text, msgs = holding_card(row, close_mode=True)
        cards += card
        lines.append(text)
        messages += msgs
    plan_cards, plan_msgs = plans_section(plans, True, held)
    messages += plan_msgs

    today = date.today().isoformat()
    fired = [a for a in trade_tools.load_alerts() if (a.get("triggered") or "").startswith(today)]
    fired_html = bullets(f"{esc(a['triggered'][11:])} · {esc(trade_tools.describe_alert(a))} @ {num(a.get('trigger_price'))}"
                         + (f" <span class='muted'>({esc(a['note'])})</span>" if a.get("note") else "") for a in fired)
    trades = [t for t in trade_tools.load_transactions() if t["date"] == today]
    trades_html = bullets(f"{t['side'].upper()} {esc(t['code'])} {t['lots']:,} lots @ {t['price']:,.0f}"
                          + (f" · realized {rupiah(t['realized'])}" if t.get("realized") is not None else "") for t in trades)

    pf = trade_tools.portfolio_live()
    pf_line = (f"Portfolio value {rupiah(pf['value']).lstrip('+')} · today {rupiah(pf['today_rp'])} · unrealized {rupiah(pf['pnl_rp'])}"
               if pf["value"] else "Set lots on the Portfolio page to see Rupiah totals.")
    todo = [
        *(m for m in messages),
        "Paste today's broker summaries in the app (stock page → Broksum) if you have them." if holdings else "",
        "Review the plan for tomorrow below - decide BEFORE the open, not during it.",
    ]
    parts = [
        todo_box(todo),
        "<div class='section-title'>Market close</div>",
        f"<div class='card'>{market_html}</div>",
        f"<div class='card'><h3>Breadth &amp; sectors</h3>{breadth_html}</div>",
        "<div class='section-title'>My holdings</div>",
        f"<p class='muted'>{pf_line}</p>",
        cards or "<div class='card muted'>No holdings in portfolio.json.</div>",
    ]
    if plan_cards:
        parts += ["<div class='section-title'>Queue plans</div>", plan_cards]
    parts += [
        "<div class='section-title'>Today</div>",
        f"<div class='card grid2'><div><h3>Alerts triggered today</h3>{fired_html or '<p class=muted>None.</p>'}</div>"
        f"<div><h3>Trades recorded today</h3>{trades_html or '<p class=muted>None.</p>'}</div></div>",
        f"<div class='card'>{global_block(context)}</div>",
        "<div class='section-title'>News</div>", f"<div class='card'>{news_block(context)}</div>",
    ]
    report = render(f"Closing report - {now:%a %d %b %Y}", f"Generated {now:%H:%M} WIB · IDX closed 16:00", parts)
    text = "\n".join(market_lines + breadth_lines + [pf_line] + lines)
    plain_messages = [html_to_text(m) for m in messages]
    summary = {"generated": now.strftime("%Y-%m-%d %H:%M"), "text": text, "regime": context["regime"]["label"],
               "holdings": lines, "messages": plain_messages}
    path = save("close", report, summary)
    return {"path": path, "text": text, "messages": plain_messages, "summary": summary}


def html_to_text(fragment: str) -> str:
    import re
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def notify_once(kind: str, title: str, body: str) -> bool:
    """Popup / email through the existing alert channels, at most once per report type per day."""
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
    key = f"{kind}:{date.today()}:{hash(body) & 0xffffffff}"
    if state.get(kind) == key:
        return False
    import bmri_daily_check as core
    core.notify(core.load_config(), title, body, attach_chart=False)
    state[kind] = key
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    return True


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    kind = sys.argv[1] if len(sys.argv) > 1 else "close"
    result = build_morning() if kind == "morning" else build_close()
    print(result["text"])
    print("Report:", result["path"])
    if "--no-open" not in sys.argv:
        import os
        os.startfile(str(result["path"]))

"""
Dividend Watchlist - simple local app for defensive, high-dividend, big-cap IDX stocks.

Double-click "Dividend Watchlist.vbs" (or run: python dividend_app.py)
Opens:  http://127.0.0.1:8766   (local only)

Pages
  /         watchlist: filters, top picks, sortable table, click a row for details
  /stocks   add / remove stocks in the universe
Data is rebuilt by dividend_watchlist.py (Refresh button, on start-up if older than 20 h,
and after the 16:20 close report).
Education only - not financial advice.
"""

import html
import json
import urllib.parse
import sys
import threading
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, redirect, request, send_file, url_for

import pandas as pd

import dividend_watchlist as engine
import macro_ideas
import all_stocks
import live_data
import market_context
import pages_trade
import portfolio
import trade_tools

PORT = 8766
app = Flask(__name__)
job = {"running": False, "message": "", "finished": None}
job_lock = threading.Lock()


def esc(value) -> str:
    return html.escape(str(value))


def start_refresh() -> bool:
    with job_lock:
        if job["running"]:
            return False
        job.update(running=True, message="Refreshing prices and dividends...")

    def worker():
        messages = []
        try:
            try:
                job["message"] = "Refreshing market view (IHSG, global, macro, news)..."
                context = market_context.build()
                messages.append(f"market: {context['regime']['label']}")
            except Exception as error:
                messages.append(f"market view failed: {error!r}"[:120])
            try:
                job["message"] = "Refreshing macro ideas..."
                ideas_data = macro_ideas.build()
                messages.append(f"{len(ideas_data['ideas'])} ready / {len(ideas_data['watch'])} early ideas")
            except Exception as error:
                messages.append(f"ideas failed: {error!r}"[:120])
            try:
                job["message"] = "Refreshing all IDX stocks..."
                all_stocks.build()
            except Exception as error:
                messages.append(f"all stocks failed: {error!r}"[:120])
            try:
                job["message"] = "Refreshing portfolio..."
                portfolio.build()
            except Exception as error:
                messages.append(f"portfolio failed: {error!r}"[:120])
            try:
                job["message"] = "Refreshing dividend watchlist..."
                result = engine.build()
                messages.append(f"{len(result['stocks'])} stocks")
                if result["errors"]:
                    messages.append(f"{len(result['errors'])} failed: " + ", ".join(e["code"] for e in result["errors"]))
            except Exception as error:
                messages.append(f"watchlist failed: {error!r}"[:120])
            job["message"] = f"Updated {datetime.now():%H:%M} - " + ", ".join(messages)
        finally:
            job["running"] = False
            job["finished"] = datetime.now().strftime("%H:%M:%S")

    threading.Thread(target=worker, daemon=True).start()
    return True


def summary_age_hours():
    summary = engine.load_summary()
    if not summary:
        return None
    generated = datetime.strptime(summary["generated"], "%Y-%m-%d %H:%M")
    return (datetime.now() - generated).total_seconds() / 3600


def market_age_hours():
    context = market_context.load_summary()
    if not context:
        return None
    generated = datetime.strptime(context["generated"], "%Y-%m-%d %H:%M")
    return (datetime.now() - generated).total_seconds() / 3600


def ihsg_call() -> str:
    path = engine.FOLDER / "ihsg_summary.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        outlook = data.get("outlook", {})
        return f"IHSG call ({data.get('date')}): {outlook.get('label', '')} {outlook.get('expectation', '')}".strip()
    except Exception:
        return ""


STYLE = """
:root { --bg: #0a1020; --panel: #111a2e; --panel2: #16223b; --line: #22304d; --text: #e6edf7; --muted: #8b9bb4;
  --green: #22c55e; --red: #f04461; --amber: #f5b942; --accent: #38bdf8; --violet: #a78bfa; }
* { box-sizing: border-box; }
html { color-scheme: dark; }
body { font-family: 'Segoe UI', Inter, Arial, sans-serif; margin: 0; color: var(--text); font-size: 14px;
  background: radial-gradient(1200px 600px at 10% -10%, rgba(56,189,248,.10), transparent 60%),
              radial-gradient(900px 500px at 100% 0%, rgba(167,139,250,.08), transparent 60%), var(--bg);
  background-attachment: fixed; min-height: 100vh; }
a { color: var(--accent); }
::-webkit-scrollbar { width: 10px; height: 10px; } ::-webkit-scrollbar-thumb { background: #22304d; border-radius: 6px; }
::-webkit-scrollbar-track { background: transparent; }

header { position: sticky; top: 0; z-index: 50; background: rgba(10,16,32,.82); backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--line); }
.topbar { display: flex; align-items: center; gap: 16px; padding: 10px 22px; flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--text); }
.brand .logo { width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center; font-weight: 800;
  background: linear-gradient(135deg, #38bdf8, #a78bfa); color: #0a1020; font-size: 15px; box-shadow: 0 0 18px rgba(56,189,248,.35); }
.brand h1 { font-size: 16px; margin: 0; font-weight: 700; letter-spacing: .2px; }
.brand .sub { color: var(--muted); font-size: 11px; }
header nav { display: flex; gap: 4px; margin-left: 8px; flex-wrap: wrap; }
header nav a, header nav button { color: var(--muted); background: transparent; border: 0; padding: 7px 12px; border-radius: 8px;
  text-decoration: none; font-size: 13px; font-weight: 600; cursor: pointer; font-family: inherit; }
header nav a:hover, header nav button:hover { color: var(--text); background: rgba(255,255,255,.06); }
header nav a.active { color: #0a1020; background: var(--accent); }
.tools { margin-left: auto; display: flex; gap: 10px; align-items: center; }
#jump { background: var(--panel); border: 1px solid var(--line); color: var(--text); border-radius: 8px; padding: 7px 10px;
  width: 190px; font-size: 13px; }
#jump:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px rgba(56,189,248,.18); }
.mkt { display: flex; align-items: center; gap: 8px; padding: 5px 11px; border-radius: 999px; border: 1px solid var(--line);
  background: var(--panel); line-height: 1.1; }
.mkt i { width: 8px; height: 8px; border-radius: 50%; background: #64748b; display: inline-block; }
.mkt b { font-size: 12px; display: block; } .mkt small { font-size: 10px; color: var(--muted); display: block; }
.mkt.open i, .mkt.pre i { background: var(--green); box-shadow: 0 0 0 0 rgba(34,197,94,.7); animation: pulse 1.6s infinite; }
.mkt.open b { color: var(--green); } .mkt.break i { background: var(--amber); } .mkt.break b { color: var(--amber); }
.mkt.offline i { background: var(--red); } .mkt.offline b { color: var(--red); }
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,.7); } 70% { box-shadow: 0 0 0 8px rgba(34,197,94,0); } 100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); } }

.tape { overflow: hidden; border-top: 1px solid var(--line); background: rgba(17,26,46,.7); white-space: nowrap; }
.tape-track { display: inline-flex; animation: tape 70s linear infinite; }
.tape:hover .tape-track { animation-play-state: paused; }
@keyframes tape { from { transform: translateX(0); } to { transform: translateX(-50%); } }
.tick { display: inline-flex; gap: 7px; align-items: baseline; padding: 6px 18px; color: var(--text); text-decoration: none;
  font-size: 12.5px; border-right: 1px solid rgba(34,48,77,.6); font-variant-numeric: tabular-nums; }
.tick:hover { background: rgba(56,189,248,.08); } .tick b { color: var(--text); } .tick .held { color: var(--accent); }

main { max-width: 1360px; margin: 0 auto; padding: 18px 22px 40px; }
.card, .strip, .filters, .pick, table { background: linear-gradient(180deg, rgba(22,34,59,.92), rgba(17,26,46,.92));
  border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }
.card { padding: 16px 18px; margin-bottom: 14px; }
.strip { padding: 10px 16px; margin-bottom: 14px; display: flex; gap: 22px; flex-wrap: wrap; align-items: center; }
.strip a { color: var(--accent); font-weight: 600; }
.filters { display: flex; gap: 14px; flex-wrap: wrap; align-items: end; padding: 12px 16px; margin-bottom: 14px; }
.filters label { font-size: 11px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; text-transform: uppercase; letter-spacing: .4px; }
input, select, textarea { background: #0d1528; color: var(--text); border: 1px solid var(--line); border-radius: 8px;
  padding: 7px 9px; font-size: 13px; font-family: inherit; }
input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent); }
input[type=checkbox] { accent-color: var(--accent); }
.picks { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 14px; }
.pick { padding: 14px 16px; border-top: 3px solid var(--green); }
.pick h3 { margin: 0 0 4px; font-size: 18px; } .pick .name { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
.pick .row { display: flex; justify-content: space-between; font-size: 13px; margin: 3px 0; }
table { width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden; font-size: 13px; }
th { background: rgba(10,16,32,.6); text-align: left; padding: 10px 9px; font-size: 11px; color: var(--muted); cursor: pointer;
  user-select: none; white-space: nowrap; text-transform: uppercase; letter-spacing: .4px; position: sticky; top: 0; }
th:hover { color: var(--text); }
td { padding: 8px 9px; border-top: 1px solid rgba(34,48,77,.7); white-space: nowrap; }
tr.main-row { cursor: pointer; transition: background .15s; }
tr.main-row:hover td { background: rgba(56,189,248,.06); }
tr.detail td { background: rgba(10,16,32,.45); white-space: normal; padding: 12px 16px; }
tr.now-row td { background: rgba(56,189,248,.10); }
table.mini { box-shadow: none; font-size: 12.5px; border-radius: 10px; background: rgba(10,16,32,.35); }
table.mini td, table.mini th { padding: 6px 8px; }
td.region { background: rgba(56,189,248,.07); font-weight: 700; font-size: 11px; color: var(--accent); text-transform: uppercase; letter-spacing: .5px; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.pill { display: inline-block; padding: 2px 9px; border-radius: 10px; font-size: 11px; font-weight: 600; }
.Defensive { background: rgba(34,197,94,.14); color: #4ade80; } .Semi-defensive { background: rgba(245,185,66,.14); color: #fbbf24; }
.Cyclical { background: rgba(240,68,97,.14); color: #fb7185; }
.green { color: var(--green); } .amber { color: var(--amber); } .red { color: var(--red); } .grey { color: #64748b; }
.bar { background: #1e2b47; border-radius: 4px; width: 90px; height: 7px; display: inline-block; vertical-align: middle; margin-right: 6px; }
.bar span { display: block; height: 7px; border-radius: 4px; background: linear-gradient(90deg, #22c55e, #38bdf8); }
.flag { color: #fb7185; } .muted { color: var(--muted); font-size: 12px; }
.detail-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; }
.detail-grid h4 { margin: 0 0 6px; }
.dps { display: flex; gap: 6px; align-items: end; height: 70px; }
.dps div { background: linear-gradient(180deg, #38bdf8, #2563eb); width: 28px; border-radius: 4px 4px 0 0; position: relative; }
.dps div span { position: absolute; bottom: -18px; left: 0; right: 0; text-align: center; font-size: 11px; color: var(--muted); }
.dps div b { position: absolute; top: -16px; left: -6px; right: -6px; text-align: center; font-size: 10px; font-weight: normal; }
.notice { background: rgba(245,185,66,.10); border: 1px solid rgba(245,185,66,.4); color: #fde68a; border-radius: 10px;
  padding: 9px 13px; margin-bottom: 14px; font-size: 13px; }
.notice:empty { display: none; }
.exitwatch { background: rgba(245,185,66,.10); border: 1px solid rgba(245,185,66,.45); color: #fde68a; border-radius: 10px;
  padding: 9px 13px; margin: 10px 0 0; font-weight: 600; }
.exitwatch.danger { background: rgba(240,68,97,.14); border-color: rgba(240,68,97,.6); color: #fecdd3; animation: blink 1.4s ease-in-out infinite; }
@keyframes blink { 50% { border-color: rgba(240,68,97,.15); } }
details summary { cursor: pointer; font-weight: 600; }
h2 { font-size: 17px; margin: 0 0 10px; font-weight: 700; } h3 { font-size: 15px; }
h4 { margin: 14px 0 6px; font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: .6px; }
.regime { border-left: 4px solid #64748b; } .red-border { border-left-color: var(--red); }
.amber-border { border-left-color: var(--amber); } .green-border { border-left-color: var(--green); } .grey-border { border-left-color: #64748b; }
.regime-label { font-size: 16px; font-weight: 700; margin-bottom: 6px; }
.two-col { display: grid; grid-template-columns: 1.35fr 1fr; gap: 20px; }
.two-col.wide-left { grid-template-columns: 1.6fr 1fr; }
@media (max-width: 1050px) { .two-col, .two-col.wide-left { grid-template-columns: 1fr; } }
img.chart { width: 100%; border-radius: 10px; border: 1px solid var(--line); filter: invert(.92) hue-rotate(180deg) saturate(1.3); }
.stat-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin-top: 10px; }
.stat-row > div { background: rgba(10,16,32,.45); border: 1px solid rgba(34,48,77,.7); border-radius: 10px; padding: 8px 10px; font-size: 13.5px; }
.stat-row span { display: block; font-size: 10.5px; color: var(--muted); text-transform: uppercase; letter-spacing: .4px; margin-bottom: 2px; }
ul.signals { list-style: none; padding: 0; margin: 6px 0; font-size: 13.5px; } ul.signals li { margin: 4px 0; }
.small { font-size: 12px; color: #a9b6cc; white-space: normal; }
.pill-green, .pill-red, .pill-grey { display: inline-block; padding: 1px 8px; border-radius: 9px; font-size: 11px; font-weight: 600; }
.pill-green { background: rgba(34,197,94,.14); color: #4ade80; } .pill-red { background: rgba(240,68,97,.14); color: #fb7185; }
.pill-grey { background: rgba(100,116,139,.2); color: #a9b6cc; }
.news-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; }
.news h3 { margin: 0 0 8px; font-size: 14px; } .news ul { list-style: none; padding: 0; margin: 0; }
.news li { margin: 0 0 10px; font-size: 13px; line-height: 1.35; } .news a { color: var(--text); text-decoration: none; }
.news a:hover { color: var(--accent); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
.dot.positive { background: var(--green); } .dot.negative { background: var(--red); } .dot.neutral { background: #64748b; }
.strength { display: inline-flex; gap: 2px; vertical-align: middle; }
.btn { padding: 8px 18px; border: 0; border-radius: 9px; background: linear-gradient(135deg, #38bdf8, #6366f1); color: #fff;
  font-size: 13px; font-weight: 700; cursor: pointer; font-family: inherit; box-shadow: 0 4px 14px rgba(56,189,248,.25); }
.btn:hover { filter: brightness(1.12); }
.chip { padding: 6px 13px; border: 1px solid var(--line); border-radius: 999px; background: var(--panel); color: var(--text);
  cursor: pointer; font-size: 12.5px; font-family: inherit; }
.chip:hover { border-color: var(--accent); color: var(--accent); }
.seg { width: 12px; height: 9px; border-radius: 2px; background: #1e2b47; display: inline-block; }
.seg.on.green { background: var(--green); } .seg.on.amber { background: var(--amber); } .seg.on.grey { background: #64748b; }
.pick ul.small { padding-left: 18px; margin: 8px 0 0; }
.global-chart { display: grid; grid-template-columns: 640px 1fr; gap: 16px; align-items: start; margin-top: 14px; }
@media (max-width: 1000px) { .global-chart { grid-template-columns: 1fr; } }
.big { font-variant-numeric: tabular-nums; }
.hero-price { font-size: 30px; font-weight: 800; letter-spacing: -.5px; font-variant-numeric: tabular-nums; padding: 0 4px; border-radius: 6px; }
.flash-up { animation: flashUp 1s ease-out; } .flash-down { animation: flashDown 1s ease-out; }
@keyframes flashUp { 0% { background: rgba(34,197,94,.55); } 100% { background: transparent; } }
@keyframes flashDown { 0% { background: rgba(240,68,97,.55); } 100% { background: transparent; } }

/* live chart */
.lc { background: rgba(10,16,32,.5); border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }
.lc-bar { display: flex; align-items: center; gap: 12px; padding: 7px 10px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.lc-tfs { display: flex; gap: 2px; background: rgba(10,16,32,.6); border-radius: 8px; padding: 2px; }
.lc-tfs button { background: transparent; border: 0; color: var(--muted); padding: 4px 9px; border-radius: 6px; cursor: pointer;
  font-size: 12px; font-weight: 700; font-family: inherit; }
.lc-tfs button:hover { color: var(--text); } .lc-tfs button.on { background: var(--accent); color: #0a1020; }
.lc-legend { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; } .lc-legend b { color: var(--text); font-weight: 600; }
.lc-toggles { margin-left: auto; display: flex; gap: 12px; font-size: 12px; color: var(--muted); }
.lc-toggles label { display: flex; gap: 4px; align-items: center; cursor: pointer; }
.lc-box { height: 430px; position: relative; }
.lc-box.tall { height: 520px; }
.lc-loading { position: absolute; inset: 0; display: grid; place-items: center; color: var(--muted); }
.lc-foot { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; padding: 5px 10px; font-size: 11px;
  color: var(--muted); border-top: 1px solid var(--line); }
.lc-keys i { display: inline-block; width: 12px; height: 3px; border-radius: 2px; margin: 0 4px 2px 8px; vertical-align: middle; }
.footer-note { margin-top: 26px; padding-top: 14px; border-top: 1px solid var(--line); }
"""

TAPE_EXTRA = ["BBCA", "BBRI", "BMRI", "BBNI", "TLKM", "ASII", "AMMN", "BREN", "ADRO", "ANTM", "GOTO", "UNVR"]
NAV = [
    ("/", "Home"),
    ("/portfolio", "Portfolio"),
    ("/all", "Screener"),
    ("/heatmap", "Heatmap"),
    ("/market", "Macro"),
    ("/ideas", "Ideas"),
    ("/dividends", "Dividends"),
    ("/reports", "Reports"),
    ("/journal", "Journal"),
    ("/alerts", "Alerts"),
    ("/settings", "⚙"),
]


def live_tape() -> str:
    held = [h["code"] for h in portfolio.load_portfolio().get("holdings", [])]
    codes = ["IHSG", "LQ45"] + held + [code for code in TAPE_EXTRA if code not in held]
    items = "".join(
        f"<a class='tick' href='{'/market' if code in ('IHSG', 'LQ45') else '/stock/' + code}'>"
        f"<b class='{'held' if code in held else ''}'>{code}</b>"
        f"<span data-q='{code}' data-f='price'>–</span><span data-q='{code}' data-f='chg'></span></a>"
        for code in codes
    )
    # the list is doubled so the CSS scroll can loop seamlessly
    return f"<div class='tape'><div class='tape-track'>{items}{items}</div></div>"


def page(title: str, body: str, refreshing: bool = False) -> str:
    poll = """
<script>
function poll(){ fetch('/status').then(r=>r.json()).then(s=>{
  const box=document.getElementById('job'); if(box){ box.textContent=s.message; }
  if(s.running){ setTimeout(poll,2500); } else if(window.wasRunning){ location.reload(); }
  window.wasRunning = window.wasRunning || s.running; }); }
poll();
</script>"""
    path = request.path
    nav = "".join(
        f"<a href='{href}' class='{'active' if (path == href or (href != '/' and path.startswith(href) and not (href == '/stocks')) or (href == '/all' and path.startswith('/stock/')) or (href == '/dividends' and path == '/stocks')) else ''}'>{label}</a>"
        for href, label in NAV
    )
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)} · IDX Monitor</title>
<style>{STYLE}</style><link rel="stylesheet" href="/static/app.css">
<script src="/static/lightweight-charts.js"></script><script src="/static/ta.js"></script><script src="/static/live.js"></script></head><body>
<header>
  <div class="topbar">
    <a class="brand" href="/"><div class="logo">ID</div><div><h1>IDX Investing Monitor</h1>
      <div class="sub">Portfolio · screener · market · ideas</div></div></a>
    <nav>{nav}<form method="post" action="/refresh" style="margin:0"><button title="Rebuild all analysis">⟳ Refresh</button></form></nav>
    <div class="tools">
      <input id="jump" list="jump-list" placeholder="Search stock…  ( / )" autocomplete="off"><datalist id="jump-list"></datalist>
      <div id="mkt-badge" class="mkt"><i></i><div><b>Connecting…</b><small></small></div></div>
    </div>
  </div>
  {live_tape()}
</header>
<main>{body}<p class="muted footer-note">Live prices: TradingView scanner; charts: Yahoo Finance - both free and delayed about
10 minutes for IDX (not true real-time). Analysis pages use daily data rebuilt by Refresh / the 16:20 task. Dividends are never
guaranteed - confirm on IDX announcements. Charts: TradingView Lightweight Charts™. Company logos: TradingView.
Education only, not financial advice.</p></main>{poll}</body></html>"""


@app.route("/dividends")
def index():
    summary = engine.load_summary()
    job_line = f"<div class='notice' id='job'>{esc(job['message'])}</div>" if job["running"] or job["message"] else "<div id='job'></div>"
    if not summary:
        return page("Dividend Watchlist", job_line + "<div class='card'>No data yet - building the first watchlist (about 30 seconds)...</div>")

    context = market_context.load_summary()
    if context:
        regime, ihsg = context["regime"], context["ihsg"]
        strip = (
            "<div class='strip'>"
            f"<div><b>IHSG</b> {ihsg['last']:,.0f} "
            f"<span class='{'green' if ihsg['change_1d'] >= 0 else 'red'}'>{ihsg['change_1d']:+.2f}%</span></div>"
            f"<div>Regime: <b class='{regime['class']}'>{esc(regime['label'])}</b></div>"
            f"<div>IHSG technicals: <b class='{ihsg['bias_class']}'>{esc(ihsg['bias'])}</b></div>"
            f"<div>Macro: <b class='{context['macro']['view']['class']}'>{esc(context['macro']['view']['label'])}</b></div>"
            "<div><a href='/market'>Full market view →</a></div>"
            + (lambda d: f"<div><a href='/ideas'>Macro ideas: {len(d.get('ideas', []))} ready · "
                         f"{len(d.get('watch', []))} on trigger →</a></div>" if d else "")(macro_ideas.load_summary())
            +
            f"<div class='muted' style='margin-left:auto'>Watchlist {esc(summary['generated'])} · market {esc(context['generated'])}</div></div>"
            f"<div class='notice'><b>{esc(regime['label'])}:</b> {esc(regime['advice'])}</div>"
        )
    else:
        call = ihsg_call()
        strip = (
            "<div class='strip'>"
            f"<div><b>IHSG</b> {summary['index_close']:,.0f} "
            f"<span class='{'green' if summary['index_change_1d'] >= 0 else 'red'}'>{summary['index_change_1d']:+.2f}%</span></div>"
            + (f"<div>{esc(call)}</div>" if call else "")
            + f"<div class='muted' style='margin-left:auto'>Updated {esc(summary['generated'])} WIB</div></div>"
        )
    data_json = json.dumps(summary["stocks"]).replace("</", "<\\/")
    body = job_line + strip + """
<details class="card"><summary>How to read this</summary>
<p><b>Score (0-100)</b> = quality as a defensive dividend stock: yield 35 (normalised, so one-off specials don't
inflate it) · consistency &amp; growth 20 · stability 25 (low beta = moves less than IHSG, low volatility) ·
size 10 · defensive sector 10.</p>
<p><b>Timing</b> is separate: it says <i>when</i>, not <i>what</i>. "Pullback in uptrend" is the classic
accumulate zone; "Downtrend - wait" means let it build a base first, or buy only small, staggered pieces.</p>
<p><b>TTM yield</b> = dividends with ex-date in the last 12 months ÷ price. <b>3y avg</b> = average yearly dividend of
the last 3 full years ÷ price. Red flags warn about yield traps, shrinking or one-off dividends.</p>
<p><b>Defensive</b> = demand holds up in bad times (telco, staples, tobacco, healthcare, utilities, toll roads).
<b>Semi</b> = big banks and similar: solid dividends but follow the economy. <b>Cyclical</b> = coal / autos: big
dividends in good years, cut in bad ones.</p></details>

<div class="filters">
  <label>Min yield (normalised)<select id="minYield">
    <option value="0">Any</option><option value="3">3%+</option><option value="4" selected>4%+</option>
    <option value="5">5%+</option><option value="6">6%+</option><option value="8">8%+</option></select></label>
  <label>Min market cap<select id="minCap">
    <option value="0">Any</option><option value="10">Rp 10T+</option><option value="20" selected>Rp 20T+</option>
    <option value="50">Rp 50T+</option><option value="100">Rp 100T+</option></select></label>
  <label>Type<select id="cls">
    <option value="DS" selected>Defensive + Semi</option><option value="D">Defensive only</option>
    <option value="ALL">All (incl. cyclical)</option></select></label>
  <label>Timing<select id="timing">
    <option value="ALL" selected>Any</option><option value="UP">Uptrend / pullback only</option></select></label>
  <label>Hide red flags<select id="flags"><option value="0" selected>No</option><option value="1">Yes</option></select></label>
  <label>Search<input id="search" placeholder="e.g. TLKM, bank" size="14"></label>
  <span class="muted" id="count"></span>
</div>

<h3 style="margin:6px 0 10px">Top picks for these filters</h3>
<div class="picks" id="picks"></div>

<table><thead><tr>
  <th data-k="rank">#</th><th data-k="code">Stock</th><th data-k="class">Type</th>
  <th data-k="price" class="num">Price</th><th data-k="market_cap_t" class="num">Mkt cap</th>
  <th data-k="ttm_yield" class="num">TTM yield</th><th data-k="avg3_yield" class="num">3y avg</th>
  <th data-k="paid_years" class="num">Paid</th><th data-k="beta" class="num">Beta</th>
  <th data-k="return_1y" class="num">1y price</th><th data-k="timing">Timing</th><th data-k="score">Score</th>
</tr></thead><tbody id="rows"></tbody></table>

<script>
const DATA = """ + data_json + """;
let sortKey = 'score', sortDir = -1;
const fmt = (v, d=0) => v === null || v === undefined ? '–' : Number(v).toLocaleString('en-US', {minimumFractionDigits: d, maximumFractionDigits: d});
const sign = (v, d=1) => v === null || v === undefined ? '–' : (v >= 0 ? '+' : '') + fmt(v, d);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function filtered() {
  const minYield = +document.getElementById('minYield').value;
  const minCap = +document.getElementById('minCap').value;
  const cls = document.getElementById('cls').value;
  const timing = document.getElementById('timing').value;
  const hideFlags = document.getElementById('flags').value === '1';
  const q = document.getElementById('search').value.trim().toLowerCase();
  return DATA.filter(s =>
    s.yield_used >= minYield && s.market_cap_t >= minCap &&
    (cls === 'ALL' || (cls === 'D' ? s.class === 'Defensive' : s.class !== 'Cyclical')) &&
    (timing === 'ALL' || s.timing_class === 'green') &&
    (!hideFlags || s.flags.filter(f => !f.startsWith('Cyclical')).length === 0) &&
    (!q || (s.code + ' ' + s.name + ' ' + s.sector).toLowerCase().includes(q)));
}

function render() {
  const rows = filtered();
  rows.sort((a, b) => {
    const x = a[sortKey], y = b[sortKey];
    if (typeof x === 'string') return sortDir * x.localeCompare(y);
    return sortDir * ((x ?? -1e9) - (y ?? -1e9));
  });
  document.getElementById('count').textContent = rows.length + ' of ' + DATA.length + ' stocks';

  const top = [...rows].sort((a, b) => b.score - a.score).slice(0, 3);
  document.getElementById('picks').innerHTML = top.length ? top.map(s => `
    <div class="pick" style="border-top-color:${s.timing_class === 'green' ? '#2f855a' : s.timing_class === 'amber' ? '#d69e2e' : '#c53030'}">
      <h3>${s.code} <span class="pill ${s.class}">${s.class}</span></h3>
      <div class="name">${esc(s.name)} · ${esc(s.sector)}</div>
      <div class="row"><span>Price</span><b>${fmt(s.price)} <span class="${s.change_1d >= 0 ? 'green' : 'red'}">${sign(s.change_1d, 1)}%</span></b></div>
      <div class="row"><span>Yield TTM / 3y avg</span><b>${fmt(s.ttm_yield, 1)}% / ${fmt(s.avg3_yield, 1)}%</b></div>
      <div class="row"><span>Paid / beta</span><b>${s.paid_years}/5 yrs · β ${fmt(s.beta, 2)}</b></div>
      <div class="row"><span>Market cap</span><b>Rp ${fmt(s.market_cap_t)}T</b></div>
      <div class="row"><span>Timing</span><b class="${s.timing_class}">${s.timing}</b></div>
      <div class="row"><span>Score</span><b>${s.score}/100</b></div>
      <div class="muted" style="margin-top:6px">${esc(s.timing_note)}</div>
    </div>`).join('') : '<div class="card">No stocks match - loosen the filters.</div>';

  document.getElementById('rows').innerHTML = rows.map((s, i) => `
    <tr class="main-row" onclick="toggle('${s.code}')">
      <td>${i + 1}</td>
      <td><b>${s.code}</b> <span class="muted">${esc(s.name)}</span>${s.flags.filter(f => !f.startsWith('Cyclical')).length ? ' <span class="flag" title="has warnings">⚠</span>' : ''}</td>
      <td><span class="pill ${s.class}">${s.class === 'Semi-defensive' ? 'Semi' : s.class}</span></td>
      <td class="num">${fmt(s.price)} <span class="${s.change_1d >= 0 ? 'green' : 'red'}" style="font-size:12px">${sign(s.change_1d, 1)}%</span></td>
      <td class="num">${fmt(s.market_cap_t)}T</td>
      <td class="num"><b>${fmt(s.ttm_yield, 1)}%</b></td>
      <td class="num">${fmt(s.avg3_yield, 1)}%</td>
      <td class="num">${s.paid_years}/5</td>
      <td class="num">${fmt(s.beta, 2)}</td>
      <td class="num ${s.return_1y >= 0 ? 'green' : 'red'}">${sign(s.return_1y, 0)}%</td>
      <td class="${s.timing_class}">${s.timing}</td>
      <td><span class="bar"><span style="width:${s.score}%"></span></span>${s.score}</td>
    </tr>
    <tr class="detail" id="d-${s.code}" style="display:none"><td colspan="12">${detail(s)}</td></tr>`).join('');
}

function detail(s) {
  const years = Object.entries(s.dps_by_year);
  const maxDps = Math.max(...years.map(([, v]) => v), 1);
  const bars = years.map(([y, v]) => `<div style="height:${Math.max(2, v / maxDps * 60)}px"><b>${fmt(v)}</b><span>${y.slice(2)}${y == new Date().getFullYear() ? '*' : ''}</span></div>`).join('');
  const reasons = s.reasons.length ? s.reasons.map(r => `<li>${esc(r)}</li>`).join('') : '<li>No standout strengths</li>';
  const flags = s.flags.length ? s.flags.map(f => `<li class="flag">${esc(f)}</li>`).join('') : '<li class="green">No red flags</li>';
  const p = s.score_parts;
  return `<div class="detail-grid">
    <div><h4>Dividend per share (Rp, by ex-date year)</h4><div class="dps">${bars}</div>
      <p class="muted" style="margin-top:24px">* this year so far · usually goes ex in: ${s.ex_months.join(', ') || '–'} · last ex: ${s.last_ex_date || '–'}
      · growth ${s.dividend_growth === null ? '–' : sign(s.dividend_growth, 0) + '%/yr'}</p></div>
    <div><h4>Price levels</h4>
      MA50 ${fmt(s.ma50)} · MA200 ${fmt(s.ma200)}<br>52w range ${fmt(s.low_52w)} – ${fmt(s.high_52w)} (${sign(s.from_high, 0)}% from high)<br>
      RSI ${fmt(s.rsi, 0)} · volatility ${fmt(s.volatility, 0)}%/yr · max drop 1y ${fmt(s.max_drawdown_1y, 0)}%<br>
      1y total return (price + dividends) ≈ <b class="${s.total_return_1y >= 0 ? 'green' : 'red'}">${sign(s.total_return_1y, 0)}%</b>
      <p class="${s.timing_class}"><b>${s.timing}:</b> ${esc(s.timing_note)}</p></div>
    <div><h4>Why it scores ${s.score}</h4>
      <div class="muted">yield ${p.yield}/35 · consistency ${p.consistency}/20 · stability ${p.stability}/25 · size ${p.size}/10 · defensive ${p.defensive}/10</div>
      <ul>${reasons}</ul><h4>Watch out</h4><ul>${flags}</ul></div>
  </div>`;
}

function toggle(code) {
  const row = document.getElementById('d-' + code);
  row.style.display = row.style.display === 'none' ? '' : 'none';
}

document.querySelectorAll('.filters select, .filters input').forEach(el => el.addEventListener('input', render));
document.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => {
  const k = th.dataset.k === 'rank' ? 'score' : th.dataset.k;
  sortDir = (sortKey === k) ? -sortDir : (['code', 'class', 'timing'].includes(k) ? 1 : -1);
  sortKey = k; render();
}));
render();
</script>"""
    return page("Dividend Watchlist", body)


def market_page_html(context: dict) -> str:
    regime = context["regime"]
    ihsg = context["ihsg"]
    macro = context["macro"]

    def pct(value, digits=2):
        if value is None:
            return "<span class='grey'>–</span>"
        css = "green" if value >= 0 else "red"
        return f"<span class='{css}'>{value:+.{digits}f}%</span>"

    # --- regime banner
    reasons_text = "; ".join(regime["reasons"])
    banner = (
        f"<div class='card regime {regime['class']}-border'>"
        f"<div class='regime-label {regime['class']}'>Market regime: {esc(regime['label'])} ({regime['score']:+d})</div>"
        f"<div>{esc(reasons_text[:1].upper() + reasons_text[1:])}.</div>"
        f"<div style='margin-top:6px'><b>For dividend buyers:</b> {esc(regime['advice'])}</div></div>"
    )

    # --- IHSG technical analysis
    signals = "".join(
        f"<li class='{'green' if sign == '+' else 'red'}'>{'▲' if sign == '+' else '▼'} {esc(text_)}</li>"
        for sign, text_ in ihsg["signals"]
    )
    levels = "".join(
        f"<tr><td class='red'>R{i + 1}</td><td class='num'>{value:,}</td><td>{esc(name)}</td>"
        f"<td class='num'>{(value / ihsg['last'] - 1) * 100:+.1f}%</td></tr>"
        for i, (value, name) in reversed(list(enumerate(ihsg["resistances"])))
    ) + (
        f"<tr class='now-row'><td><b>Now</b></td><td class='num'><b data-q='IHSG' data-f='price'>{ihsg['last']:,.0f}</b></td>"
        f"<td>{'live (session open)' if ihsg['live'] else 'last close'}</td><td></td></tr>"
    ) + "".join(
        f"<tr><td class='green'>S{i + 1}</td><td class='num'>{value:,}</td><td>{esc(name)}</td>"
        f"<td class='num'>{(value / ihsg['last'] - 1) * 100:+.1f}%</td></tr>"
        for i, (value, name) in enumerate(ihsg["supports"])
    )
    pivots = ihsg["pivots"]
    pivot_row = " · ".join(f"{key} <b>{pivots[key]:,}</b>" for key in ("S2", "S1", "P", "R1", "R2"))
    scenarios = "".join(
        f"<li><b class='{ {'Bullish': 'green', 'Bearish': 'red'}.get(name, 'amber') }'>{esc(name)}:</b> {esc(body)}</li>"
        for name, body in ihsg["scenarios"]
    )
    official = ihsg.get("official_call") or {}
    if official.get("label") and (official.get("date") or "") >= ihsg.get("completed_date", ""):
        official_html = (
            f"<p class='muted'>Close-report call ({esc(official.get('date'))}): <b>{esc(official.get('label'))}</b> - "
            f"{esc(official.get('expectation'))}</p>"
        )
    elif official.get("label"):
        official_html = (
            f"<p class='muted'>The 16:20 close-report call is from {esc(official.get('date'))} (older than the last "
            "session) - it refreshes after today's close.</p>"
        )
    else:
        official_html = ""
    ihsg_levels = esc(json.dumps(
        [{"price": value, "color": "#f04461", "title": f"R{i + 1}"} for i, (value, _n) in enumerate(ihsg["resistances"])]
        + [{"price": value, "color": "#22c55e", "title": f"S{i + 1}"} for i, (value, _n) in enumerate(ihsg["supports"])]
    ))
    ihsg_html = f"""
<div class="card"><div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:10px">
  <h2 style="margin:0">IHSG</h2><span class="hero-price" data-q="IHSG" data-f="price">{ihsg['last']:,.2f}</span>
  <b data-q="IHSG" data-f="chgabs"></b><b data-q="IHSG" data-f="chg">{pct(ihsg['change_1d'])}</b>
  <span class="small">day range <b data-q="IHSG" data-f="range">–</b></span>
  <span class="small">LQ45 <b data-q="LQ45" data-f="price">–</b> <b data-q="LQ45" data-f="chg"></b></span>
  <span class="muted" style="margin-left:auto">Technical analysis below uses the daily close</span></div>
<div class="two-col">
  <div><div data-chart data-code="IHSG" data-levels="{ihsg_levels}"></div>
    <div class="stat-row">
      <div><span>Last analysed</span><b>{ihsg['last']:,.0f}</b></div><div><span>1D</span>{pct(ihsg['change_1d'])}</div>
      <div><span>5D</span>{pct(ihsg['change_5d'])}</div><div><span>1M</span>{pct(ihsg['change_1m'])}</div>
      <div><span>YTD</span>{pct(ihsg['change_ytd'])}</div><div><span>RSI 14</span><b>{ihsg['rsi']:.0f}</b></div>
      <div><span>ATR (daily range)</span><b>{ihsg['atr']:,.0f}</b></div>
      <div><span>Bollinger position</span><b>{ihsg['band_position']:.0f}%</b></div>
    </div></div>
  <div>
    <div class="regime-label {ihsg['bias_class']}">Technical bias: {esc(ihsg['bias'])} ({ihsg['score']:+d})</div>
    <ul class="signals">{signals}</ul>
    <h4>Key levels</h4>
    <table class="mini"><tbody>{levels}</tbody></table>
    <p class="muted">Pivots for the session (from {esc(pivots['based_on'])}): {pivot_row}</p>
    <h4>Scenarios</h4><ul>{scenarios}</ul>{official_html}
  </div>
</div></div>"""

    # --- global indices
    rows = ""
    for region in ("US", "Europe", "Asia", "Indonesia"):
        rows += f"<tr><td colspan='9' class='region'>{region}</td></tr>"
        for row in [r for r in context["indices"] if r["region"] == region]:
            if "error" in row:
                rows += f"<tr><td>{esc(row['name'])}</td><td colspan='8' class='grey'>no data</td></tr>"
                continue
            rows += (
                f"<tr><td><b>{esc(row['name'])}</b> <span class='muted'>{esc(row['date'])}</span></td>"
                f"<td class='num'>{row['last']:,.2f}</td><td class='num'>{pct(row['change_1d'])}</td>"
                f"<td class='num'>{pct(row['change_5d'])}</td><td class='num'>{pct(row['change_1m'], 1)}</td>"
                f"<td class='num'>{pct(row['change_ytd'], 1)}</td><td class='num'>{pct(row['vs_ma200'], 1)}</td>"
                f"<td class='num'>{row['rsi']:.0f}</td><td class='{row['status_class']}'>{esc(row['status'])}</td></tr>"
            )
    global_html = f"""
<div class="card"><h2>Global indices</h2>
<div>
<table class="mini"><thead><tr><th>Index</th><th class="num">Last</th><th class="num">1D</th><th class="num">5D</th>
<th class="num">1M</th><th class="num">YTD</th><th class="num">vs MA200</th><th class="num">RSI</th><th>Trend</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="global-chart"><img class="chart" src="/img/global?{esc(context['generated'])}">
<p class="muted">{regime['global_breadth']}% of major non-Indonesian indices are above their 200-day average.
EIDO trades in New York overnight - it shows what foreign funds did after the IDX closed.</p></div>
</div></div>"""

    # --- macro
    macro_rows = ""
    for row in macro["rows"]:
        if "error" in row:
            macro_rows += f"<tr><td>{esc(row['name'])}</td><td colspan='6' class='grey'>no data</td></tr>"
            continue
        impact_css = {1: "green", -1: "red"}.get(row["impact"], "grey")
        impact_word = {1: "Positive", -1: "Negative"}.get(row["impact"], "Neutral")
        macro_rows += (
            f"<tr><td><b>{esc(row['name'])}</b></td><td class='num'>{row['last']:,.{row['decimals']}f}</td>"
            f"<td class='num'>{pct(row['change_1d'])}</td><td class='num'>{pct(row['change_5d'])}</td>"
            f"<td class='num'>{pct(row['change_1m'], 1)}</td><td>{esc(row['trend'])}</td>"
            f"<td><span class='pill-{impact_css}'>{impact_word}</span> <span class='small'>{esc(row['impact_note'])}</span></td></tr>"
        )
    manual = macro.get("manual", {})
    manual_items = "".join(
        f"<li><b>{esc(item['name'])}:</b> {esc(item['value'])} <span class='muted'>- {esc(item['note'])}</span></li>"
        for item in manual.get("items", [])
    )
    view = macro["view"]
    drivers = "".join(f"<li>{esc(driver)}</li>" for driver in view["drivers"]) or "<li>No strong macro driver right now.</li>"
    macro_html = f"""
<div class="card"><h2>Macro view</h2>
<div class="regime-label {view['class']}">{esc(view['label'])} for IDX ({view['score']:+d})</div>
<div class="two-col wide-left">
<table class="mini"><thead><tr><th>Indicator</th><th class="num">Last</th><th class="num">1D</th><th class="num">5D</th>
<th class="num">1M</th><th>vs MA50</th><th>Impact on IDX stocks</th></tr></thead><tbody>{macro_rows}</tbody></table>
<div><h4>What matters now</h4><ul>{drivers}</ul>
<h4>Indonesia (manual, as of {esc(manual.get('as_of', ''))})</h4><ul>{manual_items}</ul>
<p class="muted">Edit macro_manual.json when BI changes rates or MSCI news comes out.</p></div>
</div></div>"""

    # --- news
    news_cards = ""
    for category, items in context["news"].items():
        entries = "".join(
            f"<li><span class='dot {item['tone']}' title='headline tone: {item['tone']}'></span>"
            f"<a href='{esc(item['link'])}' target='_blank' rel='noopener'>{esc(item['title'])}</a>"
            f"<div class='muted'>{esc(item.get('source', ''))} · {esc(item.get('time', ''))}</div></li>"
            for item in items
        ) or "<li class='muted'>No fresh headlines.</li>"
        news_cards += f"<div class='card news'><h3>{esc(category)}</h3><ul>{entries}</ul></div>"
    news_html = (
        "<h2 style='margin:18px 0 8px'>News</h2><div class='news-grid'>" + news_cards + "</div>"
        "<p class='muted'>Headlines from Google News. The coloured dot is a rough keyword-based tone "
        "(green positive, red negative, grey neutral) - read the article before acting.</p>"
    )

    return banner + ihsg_html + global_html + macro_html + news_html


@app.route("/market")
def market():
    context = market_context.load_summary()
    job_line = f"<div class='notice' id='job'>{esc(job['message'])}</div>" if job["running"] or job["message"] else "<div id='job'></div>"
    if not context:
        start_refresh()
        return page("Market", job_line + "<div class='card'>Building the market view (about 20 seconds)...</div>")
    age = market_age_hours()
    if age is not None and age > 2:
        start_refresh()
    header = f"<p class='muted'>Market data updated {esc(context['generated'])} WIB" + (
        " - refreshing now..." if job["running"] else "") + "</p>"
    return page("Market", job_line + header + market_page_html(context))


@app.route("/img/<name>")
def image(name):
    files = {"ihsg": market_context.IHSG_CHART, "global": market_context.GLOBAL_CHART}
    path = files.get(name)
    if not path or not path.exists():
        return ("", 404)
    return send_file(path, mimetype="image/png", max_age=0)


def ideas_page_html(data: dict) -> str:
    def pct(value, digits=1):
        if value is None:
            return "<span class='grey'>–</span>"
        return f"<span class='{'green' if value >= 0 else 'red'}'>{value:+.{digits}f}%</span>"

    def why_list(row):
        return "".join(f"<li>{esc(item)}</li>" for item in row["why"]) or "<li class='muted'>-</li>"

    regime_css = {"Risk-off": "red", "Cautious": "amber"}.get(data["regime"], "green")
    head = (
        f"<div class='card regime {regime_css}-border'>"
        f"<div class='regime-label {regime_css}'>Daily macro ideas - market regime {esc(data['regime'])}</div>"
        f"<div>{esc(data['sizing'])}</div>"
        "<div class='muted' style='margin-top:6px'>Ideas = stocks linked to a macro theme that is moving today, screened for trend, "
        "liquidity (≥ Rp10bn/day) and how closely they track the driver. Research candidates, not buy signals.</div></div>"
    )

    # --- buy-ready ideas
    if data["ideas"]:
        cards = ""
        for row in data["ideas"]:
            plan = row["plan"]
            cards += (
                f"<div class='pick'><h3>{esc(row['code'])} <span class='pill Defensive'>{esc(row['theme'])}</span></h3>"
                f"<div class='name'>{esc(row['status'])}</div>"
                f"<div class='row'><span>Price</span><b>{row['price']:,.0f} {pct(row['change_1d'])}</b></div>"
                f"<div class='row'><span>Entry zone</span><b>{plan['entry_low']:,} – {plan['entry_high']:,}</b></div>"
                f"<div class='row'><span>Stop (close below)</span><b class='red'>{plan['stop']:,}</b></div>"
                f"<div class='row'><span>Targets 1.5R / 3R</span><b class='green'>{plan['target_1']:,} / {plan['target_2']:,}</b></div>"
                f"<div class='row'><span>Risk per share</span><b>{plan['risk_pct']:.1f}%</b></div>"
                f"<div class='row'><span>Link to driver</span><b>corr {row['correlation']:+.2f}</b></div>"
                f"<ul class='small'>{why_list(row)}</ul></div>"
            )
        ready_html = f"<h2 style='margin:16px 0 8px'>Buy-ready ideas</h2><div class='picks'>{cards}</div>"
    else:
        ready_html = (
            "<h2 style='margin:16px 0 8px'>Buy-ready ideas</h2>"
            "<div class='notice'><b>No buy-ready setups today.</b> Macro themes are moving, but none of their stocks is in a "
            "confirmed uptrend yet (price above MA20 and MA50). That is information too: wait for the triggers below "
            "instead of buying falling prices.</div>"
        )

    # --- early-turn candidates
    if data["watch"]:
        rows = ""
        for row in data["watch"]:
            plan = row["trigger_plan"]
            rows += (
                f"<tr><td><b>{esc(row['code'])}</b></td><td>{esc(row['theme'])}<div class='muted'>{esc(row['theme_status'])}</div></td>"
                f"<td class='num'>{row['price']:,.0f}</td><td class='{row['status_class']}'>{esc(row['status'])}</td>"
                f"<td class='num'><b>{plan['trigger']:,}</b><div class='muted'>{plan['distance_pct']:+.1f}% · {plan['trigger_label']}</div></td>"
                f"<td class='num red'>{plan['stop']:,}</td><td class='num green'>{plan['target_1']:,} / {plan['target_2']:,}</td>"
                f"<td class='num'>{plan['risk_pct']:.1f}%</td><td class='num'>{row['correlation']:+.2f}</td>"
                f"<td class='small'>{'; '.join(esc(item) for item in row['why']) or '-'}</td></tr>"
            )
        early_html = (
            "<h2 style='margin:18px 0 4px'>Early-turn candidates</h2>"
            "<p class='muted' style='margin:0 0 8px'>Buy only if the stock <b>closes above the trigger</b> (ideally on above-average "
            "volume). Until then it's a watch, not a buy. Stop and targets are measured from the trigger.</p>"
            "<table class='mini'><thead><tr><th>Stock</th><th>Theme</th><th class='num'>Price</th><th>Status</th>"
            "<th class='num'>Buy trigger (close)</th><th class='num'>Stop</th><th class='num'>T1 / T2</th>"
            "<th class='num'>Risk</th><th class='num'>Corr</th><th>Notes</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )
    else:
        early_html = "<h2 style='margin:18px 0 8px'>Early-turn candidates</h2><div class='card muted'>None today.</div>"

    # --- themes
    theme_rows = ""
    for theme in data["themes"]:
        bar = "".join(
            f"<span class='seg {'on' if i < theme['strength'] else ''} {theme['status_class']}'></span>" for i in range(5)
        )
        stock_rows = ""
        for row in theme["stocks"]:
            plan, trig = row["plan"], row["trigger_plan"]
            in_trend = row["status"].startswith(("Momentum", "Pullback"))
            plan_text = (
                f"entry {plan['entry_low']:,}–{plan['entry_high']:,} · stop {plan['stop']:,} · T1 {plan['target_1']:,}"
                if in_trend else f"buy on close > {trig['trigger']:,} · stop {trig['stop']:,} · T1 {trig['target_1']:,}"
            )
            stock_rows += (
                f"<tr><td><b>{esc(row['code'])}</b></td><td class='num'>{row['price']:,.0f}</td><td class='num'>{pct(row['change_1d'])}</td>"
                f"<td class='num'>{pct(row['change_1m'])}</td><td class='num'>{pct(row['relative_1m'])}</td>"
                f"<td class='num'>{row['correlation']:+.2f}</td><td class='num'>{row['rsi']:.0f}</td>"
                f"<td class='num {'grey' if not row['liquid'] else ''}'>{row['avg_value_bn']:,.0f}bn</td>"
                f"<td class='{row['status_class']}'>{esc(row['status'])}</td><td class='small'>{plan_text}</td></tr>"
            )
        direction = "rising" if theme["direction"] == "up" else "falling"
        theme_rows += f"""
<tr class="main-row" onclick="toggleTheme('{theme['id']}')">
  <td><b>{esc(theme['name'])}</b><div class='muted'>needs {esc(theme['driver_name'])} {direction}</div></td>
  <td class='num'>{theme['last']:,.2f}</td><td class='num'>{pct(theme['change_1d'], 2)}</td>
  <td class='num'>{pct(theme['change_5d'])}</td><td class='num'>{pct(theme['change_1m'])}</td>
  <td><span class='strength'>{bar}</span> {theme['strength']}/5</td>
  <td class='{theme['status_class']}'><b>{esc(theme['status'])}</b></td>
  <td class='small'>{esc(', '.join(theme['reasons']) or 'no signal')}<br><span class='muted'>{esc(theme['trigger'])}</span></td>
</tr>
<tr id="t-{theme['id']}" class="detail" style="display:none"><td colspan="8">
  <p class="small" style="margin:0 0 8px">{esc(theme['why'])}</p>
  <table class="mini"><thead><tr><th>Stock</th><th class='num'>Price</th><th class='num'>1D</th><th class='num'>1M</th>
  <th class='num'>vs IHSG 1M</th><th class='num'>Corr</th><th class='num'>RSI</th><th class='num'>Avg value</th>
  <th>Status</th><th>Plan</th></tr></thead><tbody>{stock_rows}</tbody></table>
</td></tr>"""
    themes_html = (
        "<h2 style='margin:18px 0 4px'>Macro themes</h2>"
        "<p class='muted' style='margin:0 0 8px'>Click a theme to see all its stocks with an entry or trigger plan. "
        "Strength: trend vs MA20/MA50 plus 5-day and 1-month moves compared with the driver's normal volatility.</p>"
        "<table><thead><tr><th>Theme</th><th class='num'>Driver</th><th class='num'>1D</th><th class='num'>5D</th>"
        f"<th class='num'>1M</th><th>Strength</th><th>Status</th><th>Why / trigger</th></tr></thead><tbody>{theme_rows}</tbody></table>"
        "<script>function toggleTheme(id){const r=document.getElementById('t-'+id);"
        "r.style.display=r.style.display==='none'?'':'none';}</script>"
    )

    # --- track record
    if data["track_record"]:
        rows = "".join(
            f"<tr><td>{esc(r['date'])}</td><td><b>{esc(r['code'])}</b></td><td>{esc(r['theme'])}</td>"
            f"<td class='num'>{r['entry']:,.0f}</td><td class='num'>{r['now']:,.0f}</td><td class='num'>{pct(r['return_pct'])}</td>"
            f"<td>{'✅ T1 hit' if r['hit_target'] else ''} {'❌ stop hit' if r['hit_stop'] else ''}</td></tr>"
            for r in data["track_record"]
        )
        track_html = (
            "<h2 style='margin:18px 0 8px'>Track record of past buy-ready ideas</h2><table class='mini'><thead><tr>"
            "<th>Date</th><th>Stock</th><th>Theme</th><th class='num'>Idea price</th><th class='num'>Now</th>"
            f"<th class='num'>Return</th><th>Outcome</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    else:
        track_html = (
            "<h2 style='margin:18px 0 8px'>Track record</h2><div class='card muted'>Buy-ready ideas are logged to "
            "ideas_history.csv from today; their results appear here in the following sessions, so you can judge "
            "whether this method works before trusting it.</div>"
        )
    return head + ready_html + early_html + themes_html + track_html


@app.route("/ideas")
def ideas():
    data = macro_ideas.load_summary()
    job_line = f"<div class='notice' id='job'>{esc(job['message'])}</div>" if job["running"] or job["message"] else "<div id='job'></div>"
    if not data:
        start_refresh()
        return page("Ideas", job_line + "<div class='card'>Building today's macro ideas (about 30 seconds)...</div>")
    generated = datetime.strptime(data["generated"], "%Y-%m-%d %H:%M")
    if (datetime.now() - generated).total_seconds() > 2 * 3600:
        start_refresh()
    header = f"<p class='muted'>Ideas updated {esc(data['generated'])} WIB" + (" - refreshing now..." if job["running"] else "") + "</p>"
    return page("Ideas", job_line + header + ideas_page_html(data))


def portfolio_page_html(summary: dict, message: str = "") -> str:
    def pct(value, digits=1):
        if value is None:
            return "<span class='grey'>–</span>"
        return f"<span class='{'green' if value >= 0 else 'red'}'>{value:+.{digits}f}%</span>"

    data = portfolio.load_portfolio()
    cards = ""
    for row in summary.get("holdings", []):
        if "error" in row:
            cards += f"<div class='card'><h2>{esc(row['code'])}</h2><p class='red'>Data error: {esc(row['error'])}</p></div>"
            continue
        price = row["price"]
        targets = "".join(
            f"<tr><td class='green'>T{i}</td><td class='num'><b>{level:,}</b></td><td>{esc(name)}</td>"
            f"<td class='num'>{(level / price - 1) * 100:+.1f}%</td><td class='num'>{(level / row['avg'] - 1) * 100:+.1f}%</td></tr>"
            for i, (level, name) in reversed(list(enumerate(row["targets"], start=1)))
        )
        hurdle = row.get("hurdle")
        hurdle_row = (
            f"<tr><td class='amber'>Hurdle</td><td class='num'>{hurdle[0]:,}</td><td>{esc(hurdle[1])}</td>"
            f"<td class='num'>{(hurdle[0] / price - 1) * 100:+.1f}%</td><td class='num'>{(hurdle[0] / row['avg'] - 1) * 100:+.1f}%</td></tr>"
            if hurdle else ""
        )
        level_table = (
            "<table class='mini'><thead><tr><th></th><th class='num'>Level</th><th>Why</th>"
            "<th class='num'>From price</th><th class='num'>From your avg</th></tr></thead><tbody>"
            + targets + hurdle_row
            + f"<tr class='now-row'><td><b>Now</b></td><td class='num'><b data-q='{esc(row['code'])}' data-f='price'>{price:,.0f}</b></td><td>avg {row['avg']:,.0f} · breakeven {row['breakeven']:,}</td>"
              f"<td></td><td class='num' data-q='{esc(row['code'])}' data-f='pnl' data-avg='{row['avg']}'>{pct(row['pnl_pct'])}</td></tr>"
            + f"<tr><td class='red'><b>EXIT</b></td><td class='num red'><b>{row['exit_level']:,}</b></td><td>daily close below · {esc(row['exit_basis'])}</td>"
              f"<td class='num'>{pct(row['risk_to_exit'])}</td><td class='num'>{pct(row['exit_from_avg'])}</td></tr>"
            + f"<tr><td class='red'>Last line</td><td class='num red'>{row['last_line']:,}</td><td>no excuses below this</td>"
              f"<td class='num'>{pct((row['last_line'] / price - 1) * 100)}</td><td class='num'>{pct((row['last_line'] / row['avg'] - 1) * 100)}</td></tr>"
            + "</tbody></table>"
        )
        alerts = "".join(f"<li class='red'>{esc(text_)}</li>" for text_ in row["alerts"].values())
        notes = "".join(f"<li>{esc(note)}</li>" for note in row["notes"])
        plan = "".join(f"<li>{esc(step)}</li>" for step in row["plan"])
        flow = row["flow"]
        ud = f"{flow['up_down_ratio']:.2f}" if flow.get("up_down_ratio") else "–"
        broksum = row.get("broksum") or {}
        bsum = broksum.get("summary")
        if bsum:
            broksum_html = (
                f"<div class='regime-label {bsum['class']}' style='font-size:15px'>Broksum: {esc(bsum['verdict'])}</div>"
                f"<div class='small'>Last {bsum['days']} entries: {bsum['accumulation_days']} accumulation / "
                f"{bsum['distribution_days']} distribution days · institutional net Rp{bsum['institutional_net_total']:+,.1f}bn"
                + (f" · institutional buyers' average ≈ <b>{bsum['buyer_avg']:,.0f}</b> "
                   f"({'price above it: they are in profit' if price > bsum['buyer_avg'] else 'price below it: they are under water and may defend it'})"
                   if bsum.get("buyer_avg") else "")
                + "</div>"
            )
            history_rows = "".join(
                f"<tr><td>{esc(e['date'])}</td><td>{esc(e['signal'])}</td><td class='num'>{esc(e['institutional_net'])}</td>"
                f"<td class='num'>{esc(e['retail_net'])}</td><td class='num'>{esc(e['net_foreign'])}</td><td class='num'>{esc(e['buyer_avg'])}</td>"
                f"<td class='small'>{esc(e['top_buyers'])}</td><td class='small'>{esc(e['top_sellers'])}</td></tr>"
                for e in broksum["entries"][:8]
            )
            broksum_html += (
                "<table class='mini' style='margin-top:6px'><thead><tr><th>Date</th><th>Signal</th><th class='num'>Inst. net</th>"
                "<th class='num'>Retail net</th><th class='num'>Foreign</th><th class='num'>Buyers avg</th><th>Top buyers</th>"
                f"<th>Top sellers</th></tr></thead><tbody>{history_rows}</tbody></table>"
            )
        else:
            broksum_html = "<div class='muted'>No broksum entered yet - paste it below after the close.</div>"
        code_ = esc(row["code"])
        hypothetical = row.get("hypothetical")
        chart_levels = [
            {"price": level, "color": "#22c55e", "title": f"T{i}"}
            for i, (level, _name) in enumerate(row["targets"], start=1)
        ]
        if hurdle:
            chart_levels.append({"price": hurdle[0], "color": "#f5b942", "title": "Hurdle"})
        if not hypothetical:
            chart_levels.append({"price": row["avg"], "color": "#94a3b8", "title": "Avg", "style": 1})
        chart_levels += [
            {"price": row["exit_level"], "color": "#f04461", "title": "EXIT", "width": 2, "style": 0},
            {"price": row["last_line"], "color": "#9f1239", "title": "Last line"},
        ]
        levels_attr = esc(json.dumps(chart_levels))
        pnl_rp = (
            f" · <span data-q='{code_}' data-f='pnlrp' data-avg='{row['avg']}' data-lots='{row.get('lots') or 0}'>Rp{row['pnl_rp']:+,.0f}</span>"
            if row.get("pnl_rp") is not None else ""
        )
        pnl_html = "" if hypothetical else (
            f"<div>P/L <b data-q='{code_}' data-f='pnl' data-avg='{row['avg']}'>{pct(row['pnl_pct'])}</b>{pnl_rp}</div>"
        )
        cards += f"""
<div class="card {row['verdict_class']}-border regime">
  <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
    <h2 style="margin:0;font-size:22px">{code_}</h2>
    <div><span class="hero-price" data-q="{code_}" data-f="price">{price:,.0f}</span>
      <b data-q="{code_}" data-f="chgabs"></b> <b data-q="{code_}" data-f="chg">{pct(row['change_1d'])}</b></div>
    {pnl_html}
    <div class="small">to EXIT <b class="red" data-q="{code_}" data-f="dist" data-level="{row['exit_level']}">{pct(row['risk_to_exit'])}</b>
      · day range <b data-q="{code_}" data-f="range">–</b> · value <b data-q="{code_}" data-f="value">–</b></div>
    <div class="regime-label {row['verdict_class']}" style="margin:0 0 0 auto;font-size:15px">{esc(row['verdict'])}</div>
  </div>
  <div class="exitwatch" data-exitwatch="{code_}" data-exit="{row['exit_level']}" data-lastline="{row['last_line']}" style="display:none"></div>
  <div class="muted" style="margin-top:4px">Analysis on daily close {esc(row['date'])} · ATR {row['atr']:,.0f} ({row['atr_pct']:.0f}%/day) · RSI {row['rsi']:.0f}</div>
  {f"<ul>{alerts}</ul>" if alerts else ""}
  <div class="two-col" style="margin-top:10px">
    <div><div data-chart data-code="{code_}" data-levels="{levels_attr}"></div>
      <h4>Volume-flow proxy (automatic)</h4>
      <div class="{flow['class']}"><b>{esc(flow['label'])}</b> · Chaikin money flow {flow['cmf']:+.2f} · up/down volume {ud}</div>
      <h4>Broker summary</h4>{broksum_html}
    </div>
    <div><h4>Levels</h4>{level_table}
      <h4>Plan</h4><ol class="small">{plan}</ol>
      {f"<h4>Watch out</h4><ul class='small'>{notes}</ul>" if notes else ""}
    </div>
  </div>
</div>"""

    holding_rows = "".join(
        f"<tr><td><input type='checkbox' name='remove' value='{esc(h['code'])}'></td><td><b>{esc(h['code'])}</b></td>"
        f"<td><input name='avg_{esc(h['code'])}' value='{h['avg']:g}' size='8'></td>"
        f"<td><input name='lots_{esc(h['code'])}' value='{'' if h.get('lots') is None else h['lots']}' size='6'></td></tr>"
        for h in data.get("holdings", [])
    )
    codes = [h["code"] for h in data.get("holdings", [])]
    options = "".join(f"<option>{esc(code)}</option>" for code in codes)
    forms = f"""
<div class="two-col">
<form method="post" action="/portfolio/holdings" class="card">
  <h3 style="margin-top:0">My holdings</h3>
  <table class="mini"><thead><tr><th>Remove</th><th>Code</th><th>Avg price</th><th>Lots</th></tr></thead><tbody>{holding_rows}</tbody></table>
  <div class="filters" style="box-shadow:none;padding:8px 0">
    <label>Add code<input name="new_code" size="6" placeholder="e.g. ANTM"></label>
    <label>Avg price<input name="new_avg" size="8"></label>
    <label>Lots (optional)<input name="new_lots" size="6"></label>
    <label>Max loss %<input name="max_loss" size="4" value="{data.get('max_loss_pct', 8):g}"></label>
  </div>
  <button class="btn">Save holdings</button>
</form>
<form method="post" action="/portfolio/broksum" class="card">
  <h3 style="margin-top:0">Paste broker summary (broksum)</h3>
  <div class="filters" style="box-shadow:none;padding:0 0 8px">
    <label>Stock<input name="code" list="codes" size="7" value="{esc(codes[0]) if codes else ''}"><datalist id="codes">{options}</datalist></label>
    <label>Date<input name="date" type="date" value="{datetime.now():%Y-%m-%d}"></label>
    <label>Net foreign (Rp bn, minus = sell)<input name="net_foreign" size="8"></label>
  </div>
  <textarea name="brokers" rows="8" style="width:100%;font-family:Consolas,monospace" placeholder="One broker per line: CODE  net value  [avg price]
Buyers positive, sellers negative. Units: 12.5 = Rp12.5bn, 850M, 1.2T also OK.
AK 12.5 1300
BK 8.2B 1295
YP -9.4 1310
XL -850M 1320"></textarea>
  <button class="btn">Save &amp; analyse</button>
  <p class="muted">Use the top 5-10 net buyers and sellers from your broker app's broker summary (net value, average price).
  Broker types (foreign / local institution / retail) come from brokers.json - check them against your app.</p>
</form>
</div>"""
    note = f"<div class='notice'>{esc(message)}</div>" if message else ""
    intro = (
        "<p class='muted'>Exit and targets come from the chart (swing lows/highs, moving averages, recent extremes) and the stock's "
        "normal daily range (ATR). Exits are on the daily CLOSE. Alerts are sent by the 16:20 close report (popup / email).</p>"
    )
    return note + intro + (cards or "<div class='card'>No holdings yet - add one below.</div>") + forms


@app.route("/portfolio")
def portfolio_view():
    summary = portfolio.load_summary()
    stale = not summary or (datetime.now() - datetime.strptime(summary["generated"], "%Y-%m-%d %H:%M")).total_seconds() > 1800
    if stale:
        summary = portfolio.build()
    message = request.args.get("msg", "")
    return page("Portfolio", f"<p class='muted'>Portfolio updated {esc(summary['generated'])} WIB</p>" + portfolio_page_html(summary, message))


@app.route("/portfolio/holdings", methods=["POST"])
def portfolio_holdings():
    data = portfolio.load_portfolio()
    remove = set(request.form.getlist("remove"))
    holdings = []
    for holding in data.get("holdings", []):
        code = holding["code"]
        if code in remove:
            continue
        try:
            holding["avg"] = float(request.form.get(f"avg_{code}", holding["avg"]))
        except ValueError:
            pass
        lots = request.form.get(f"lots_{code}", "").strip()
        holding["lots"] = int(float(lots)) if lots else None
        holdings.append(holding)
    new_code = request.form.get("new_code", "").strip().upper().replace(".JK", "")
    new_avg = request.form.get("new_avg", "").strip()
    if new_code and new_avg:
        holdings = [h for h in holdings if h["code"] != new_code]
        new_lots = request.form.get("new_lots", "").strip()
        holdings.append({"code": new_code, "avg": float(new_avg), "lots": int(float(new_lots)) if new_lots else None, "note": ""})
    try:
        data["max_loss_pct"] = float(request.form.get("max_loss", data.get("max_loss_pct", 8)))
    except ValueError:
        pass
    data["holdings"] = holdings
    portfolio.save_portfolio(data)
    portfolio.build()
    return redirect(url_for("portfolio_view", msg="Holdings saved."))


@app.route("/portfolio/broksum", methods=["POST"])
def portfolio_broksum():
    code = request.form.get("code", "").strip().upper().replace(".JK", "")
    date = request.form.get("date") or datetime.now().strftime("%Y-%m-%d")
    raw_foreign = request.form.get("net_foreign", "").strip()
    try:
        net_foreign = portfolio.parse_value_bn(raw_foreign) if raw_foreign else None
        prices, _ = engine.fetch_history(f"{code}.JK", years=1)
        on_date = prices[prices.index <= pd.Timestamp(date)]
        close = float((on_date if len(on_date) else prices)["Close"].iloc[-1])
        analysis = portfolio.save_broksum(code, date, close, net_foreign, request.form.get("brokers", ""))
        message = (f"{code} {date}: {analysis['signal']} - {analysis['meaning']} "
                   + " ".join(analysis["notes"]))
    except Exception as error:
        message = f"Could not read the broksum for {code}: {error}"
    portfolio.build()
    next_page = request.form.get("next", "")
    if next_page.startswith("/stock/"):
        return redirect(f"{next_page}?msg={urllib.parse.quote(message)}")
    return redirect(url_for("portfolio_view", msg=message))


@app.route("/img/portfolio/<code>")
def portfolio_image(code):
    path = portfolio.FOLDER / f"portfolio_{code.upper()}.png"
    if not path.exists():
        return ("", 404)
    return send_file(path, mimetype="image/png", max_age=0)


def all_stocks_page_html(data: dict) -> str:
    breadth = data["breadth"]
    stocks_json = json.dumps(data["stocks"]).replace("</", "<\\/")
    sector_options = "".join(f"<option>{esc(sector)}</option>" for sector in data["sectors"])
    return f"""
<div class="strip">
  <div><b>{data['count']}</b> IDX stocks</div>
  <div><span class="green">▲ {breadth['advancers']}</span> · <span class="red">▼ {breadth['decliners']}</span> · {breadth['unchanged']} flat</div>
  <div>{breadth['liquid']} liquid (≥ Rp10bn/day) · <b>{breadth['liquid_above_ma200_pct']}%</b> of them above MA200</div>
  <div class="muted" style="margin-left:auto">{esc(data['source'])} · {esc(data['generated'])} WIB</div>
</div>
<div class="filters">
  <label>Search<input id="q" placeholder="code, name, industry" size="18"></label>
  <label>Sector<select id="sector"><option value="">All sectors</option>{sector_options}</select></label>
  <label>Liquidity<select id="liq"><option value="Liquid" selected>Liquid (≥ Rp10bn/day)</option>
    <option value="Thin">Liquid + thin (≥ Rp1bn)</option><option value="">All, incl. illiquid</option></select></label>
  <label>Trend<select id="trend"><option value="">Any</option><option value="green">Uptrend / recovering</option>
    <option value="amber">Pullback / bouncing</option><option value="red">Downtrend / weakening</option></select></label>
  <label>Signal<select id="signal"><option value="">Any</option>
    <option>52-week high zone</option><option>New 1-month high</option><option>Volume spike up</option>
    <option>Heavy selling</option><option>Oversold</option><option>Overbought</option>
    <option>Early turn: back above MA200</option><option>MACD turning up</option></select></label>
  <label>Min dividend yield<select id="div"><option value="0">Any</option><option value="3">3%+</option>
    <option value="5">5%+</option><option value="8">8%+</option></select></label>
  <span class="muted" id="count"></span>
</div>
<div style="margin:-4px 0 12px;display:flex;gap:8px;flex-wrap:wrap">
  <button class="chip" onclick="preset('gainers')">Top gainers</button>
  <button class="chip" onclick="preset('losers')">Top losers</button>
  <button class="chip" onclick="preset('active')">Most active</button>
  <button class="chip" onclick="preset('breakout')">Breakouts</button>
  <button class="chip" onclick="preset('volume')">Volume spikes</button>
  <button class="chip" onclick="preset('oversold')">Oversold (liquid)</button>
  <button class="chip" onclick="preset('uptrend')">Uptrends</button>
  <button class="chip" onclick="preset('reset')">Reset</button>
</div>
<table><thead><tr>
  <th data-k="code">Stock</th><th data-k="sector">Sector</th><th data-k="price" class="num">Price</th>
  <th data-k="change_1d" class="num">1D</th><th data-k="perf_1w" class="num">1W</th><th data-k="perf_1m" class="num">1M</th>
  <th data-k="perf_ytd" class="num">YTD</th><th data-k="typical_value_bn" class="num">Value/day</th>
  <th data-k="market_cap_t" class="num">Mkt cap</th><th data-k="pe" class="num">P/E</th><th data-k="div_yield" class="num">Div yld</th>
  <th data-k="rsi" class="num">RSI</th><th data-k="trend">Trend</th><th data-k="rating">TV rating</th><th>Signals</th>
</tr></thead><tbody id="rows"></tbody></table>
<p style="text-align:center"><button class="btn" id="more" onclick="limit += 150; render()">Show more</button></p>
<script>
const DATA = {stocks_json};
let sortKey = 'market_cap_t', sortDir = -1, limit = 150;
const fmt = (v, d=0) => v === null || v === undefined ? '–' : Number(v).toLocaleString('en-US', {{minimumFractionDigits: d, maximumFractionDigits: d}});
const pc = (v, d=1) => v === null || v === undefined ? '<span class="grey">–</span>' : `<span class="${{v >= 0 ? 'green' : 'red'}}">${{v >= 0 ? '+' : ''}}${{fmt(v, d)}}%</span>`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const LIQ = {{Liquid: 2, Thin: 1, Illiquid: 0}};
function val(id) {{ return document.getElementById(id).value; }}
function filtered() {{
  const q = val('q').trim().toLowerCase(), sector = val('sector'), liq = val('liq'), trend = val('trend'),
        signal = val('signal'), div = +val('div');
  return DATA.filter(s =>
    (!q || (s.code + ' ' + s.name + ' ' + s.industry).toLowerCase().includes(q)) &&
    (!sector || s.sector === sector) &&
    (!liq || LIQ[s.liquidity] >= LIQ[liq]) &&
    (!trend || s.trend_class === trend) &&
    (!signal || s.signals.includes(signal)) &&
    (!div || (s.div_yield || 0) >= div));
}}
function render() {{
  const rows = filtered();
  rows.sort((a, b) => {{
    const x = a[sortKey], y = b[sortKey];
    if (typeof x === 'string' || typeof y === 'string') return sortDir * String(x || '').localeCompare(String(y || ''));
    return sortDir * ((x ?? -1e15) - (y ?? -1e15));
  }});
  document.getElementById('count').textContent = rows.length + ' stocks';
  document.getElementById('more').style.display = rows.length > limit ? '' : 'none';
  setTimeout(() => window.Live && Live.paintStars(), 0);
  document.getElementById('rows').innerHTML = rows.slice(0, limit).map(s => `
    <tr class="main-row" onclick="location.href='/stock/${{s.code}}'">
      <td><span class="star" data-star="${{s.code}}">☆</span> <b>${{s.code}}</b> <span class="muted">${{esc(s.name).slice(0, 34)}}</span></td>
      <td class="small">${{esc(s.sector)}}</td>
      <td class="num"><b data-q="${{s.code}}" data-f="price">${{fmt(s.price)}}</b></td><td class="num" data-q="${{s.code}}" data-f="chg">${{pc(s.change_1d)}}</td><td class="num">${{pc(s.perf_1w)}}</td>
      <td class="num">${{pc(s.perf_1m)}}</td><td class="num">${{pc(s.perf_ytd, 0)}}</td>
      <td class="num">${{fmt(s.typical_value_bn, s.typical_value_bn < 10 ? 1 : 0)}}bn</td><td class="num">${{fmt(s.market_cap_t, 1)}}T</td>
      <td class="num">${{s.pe && s.pe > 0 ? fmt(s.pe, 1) : '–'}}</td><td class="num">${{s.div_yield ? fmt(s.div_yield, 1) + '%' : '–'}}</td>
      <td class="num">${{fmt(s.rsi, 0)}}</td><td class="${{s.trend_class}} small">${{s.trend}}</td>
      <td class="small">${{s.rating_label}}</td><td class="small">${{s.signals.join(' · ')}}</td>
    </tr>`).join('');
}}
function preset(name) {{
  ['q'].forEach(id => document.getElementById(id).value = '');
  document.getElementById('sector').value = ''; document.getElementById('trend').value = '';
  document.getElementById('signal').value = ''; document.getElementById('div').value = '0';
  document.getElementById('liq').value = 'Liquid';
  if (name === 'gainers') {{ sortKey = 'change_1d'; sortDir = -1; }}
  else if (name === 'losers') {{ sortKey = 'change_1d'; sortDir = 1; }}
  else if (name === 'active') {{ sortKey = 'typical_value_bn'; sortDir = -1; }}
  else if (name === 'breakout') {{ document.getElementById('signal').value = '52-week high zone'; sortKey = 'change_1d'; sortDir = -1;
    if (!filtered().length) document.getElementById('signal').value = 'New 1-month high'; }}
  else if (name === 'volume') {{ document.getElementById('signal').value = 'Volume spike up'; document.getElementById('liq').value = 'Thin'; sortKey = 'relative_volume'; sortDir = -1; }}
  else if (name === 'oversold') {{ document.getElementById('signal').value = 'Oversold'; sortKey = 'rsi'; sortDir = 1; }}
  else if (name === 'uptrend') {{ document.getElementById('trend').value = 'green'; sortKey = 'perf_1m'; sortDir = -1; }}
  else {{ sortKey = 'market_cap_t'; sortDir = -1; }}
  limit = 150; render();
}}
document.querySelectorAll('.filters select, .filters input').forEach(el => el.addEventListener('input', () => {{ limit = 150; render(); }}));
document.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => {{
  const k = th.dataset.k;
  sortDir = (sortKey === k) ? -sortDir : (['code', 'sector', 'trend'].includes(k) ? 1 : -1);
  sortKey = k; render();
}}));
render();
</script>"""


@app.route("/all")
def all_stocks_view():
    data = all_stocks.load_summary()
    stale = not data or (datetime.now() - datetime.strptime(data["generated"], "%Y-%m-%d %H:%M")).total_seconds() > 1800
    if stale:
        try:
            data = all_stocks.build()
        except Exception as error:
            if not data:
                return page("All stocks", f"<div class='card red'>Could not load the market list: {esc(error)}</div>")
    return page("All stocks", all_stocks_page_html(data))


@app.route("/stocks", methods=["GET", "POST"])
def stocks():
    universe = engine.load_universe()
    message = ""
    if request.method == "POST":
        remove = set(request.form.getlist("remove"))
        universe = [stock for stock in universe if stock["code"] not in remove]
        code = request.form.get("code", "").strip().upper().replace(".JK", "")
        if code:
            try:
                shares = float(request.form.get("shares_bn") or 0)
            except ValueError:
                shares = 0
            universe = [stock for stock in universe if stock["code"] != code]
            universe.append({
                "code": code,
                "name": request.form.get("name", "").strip() or code,
                "sector": request.form.get("sector", "").strip() or "Other",
                "class": request.form.get("class", "Defensive"),
                "shares_bn": shares,
            })
        engine.save_universe(universe)
        start_refresh()
        return redirect(url_for("index"))

    rows = "".join(
        f"<tr><td><input type='checkbox' name='remove' value='{esc(s['code'])}'></td><td><b>{esc(s['code'])}</b></td>"
        f"<td>{esc(s['name'])}</td><td>{esc(s['sector'])}</td><td><span class='pill {esc(s['class'])}'>{esc(s['class'])}</span></td>"
        f"<td class='num'>{s.get('shares_bn', 0):g}</td></tr>"
        for s in universe
    )
    body = f"""{message}
<form method="post" class="card">
<h3 style="margin-top:0">Add a stock</h3>
<div class="filters" style="box-shadow:none;padding:0">
<label>Code<input name="code" placeholder="e.g. SMSM" size="8"></label>
<label>Name<input name="name" size="24"></label>
<label>Sector<input name="sector" size="16"></label>
<label>Type<select name="class"><option>Defensive</option><option>Semi-defensive</option><option>Cyclical</option></select></label>
<label>Shares (billion)<input name="shares_bn" size="8" placeholder="for market cap"></label>
</div>
<h3>Current stocks ({len(universe)}) - tick to remove</h3>
<table><thead><tr><th></th><th>Code</th><th>Name</th><th>Sector</th><th>Type</th><th class="num">Shares (bn)</th></tr></thead>
<tbody>{rows}</tbody></table>
<p><button style="padding:8px 18px;border:0;border-radius:6px;background:#12355b;color:white;font-size:14px;cursor:pointer">
Save &amp; refresh</button></p>
<p class="muted">Shares outstanding: check the company profile on idx.co.id. Only used for market cap.</p>
</form>"""
    return page("Stocks", body)


@app.route("/api/quotes")
def api_quotes():
    codes = [code for code in request.args.get("codes", "").split(",") if code.strip()]
    try:
        quotes = live_data.get_quotes(codes) if codes else {}
    except Exception as error:
        return jsonify({"quotes": {}, "market": live_data.market_status(), "error": str(error)})
    return jsonify({"quotes": quotes, "market": live_data.market_status(), "fired": fired_feed()})


@app.route("/api/candles/<code>")
def api_candles(code):
    try:
        return jsonify(live_data.get_candles(code, request.args.get("tf", "1D")))
    except Exception as error:
        return jsonify({"error": f"No chart data for {code}: {error}"})


@app.route("/api/codes")
def api_codes():
    data = all_stocks.load_summary() or {"stocks": []}
    codes = [{"code": "IHSG", "name": "IDX Composite"}] + [
        {"code": s["code"], "name": esc(s["name"])[:40]} for s in data["stocks"]
    ]
    return jsonify(codes)


@app.route("/refresh", methods=["POST"])
def refresh():
    start_refresh()
    return redirect(url_for("index"))


@app.route("/status")
def status():
    return jsonify(job)


fired_feed = pages_trade.register(app, page, esc, portfolio_page_html)


def _already_running() -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    url = f"http://127.0.0.1:{PORT}"
    open_browser = "--no-browser" not in sys.argv
    if _already_running():
        if open_browser:
            webbrowser.open(url)
        return
    age = summary_age_hours()
    market_age = market_age_hours()
    if age is None or age > 20 or market_age is None or market_age > 2:
        start_refresh()
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

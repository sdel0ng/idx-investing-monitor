"""
Trading Monitor - local dashboard for the BMRI / IHSG / Big 4 / Macro / TEBE system.

Start:  double-click "Trading Monitor.bat" (or: python dashboard_app.py)
Opens:  http://127.0.0.1:8765  (local only, nothing is exposed to the network)

Pages
  /              overview: positions, IHSG call, Big 4, macro, alerts, broksum
  /broksum       enter today's broksum (replaces the 18:00 popup if you prefer)
  /positions     edit average prices and lots
  /report/close  full after-close report      /report/morning  morning brief
  /deck          download the latest PowerPoint deck
"""

import html
import json
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import pandas as pd
from flask import Flask, abort, redirect, request, send_file, url_for

import bmri_daily_check as core

FOLDER = Path(__file__).resolve().parent
DECK_FILE = FOLDER.parent / "BMRI_Swing_Recap.pptx"
PORT = 8765
CHARTS = {
    "bmri": core.CHART_FILE,
    "ihsg": FOLDER / "ihsg_chart.png",
    "banks": FOLDER / "big_banks_relative.png",
    "macro": FOLDER / "macro_chart.png",
    "tebe": FOLDER / "tebe_chart.png",
}

app = Flask(__name__)
job = {"process": None, "label": "", "started": None, "finished": None, "returncode": None, "log": ""}


# ---------------------------------------------------------------- data helpers
def load_json(name: str) -> dict:
    path = FOLDER / name
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
    return {}


def esc(value) -> str:
    return html.escape(str(value))


def pct_html(value, decimals: int = 1) -> str:
    if value is None:
        return "–"
    css = "pos" if value > 0 else "neg" if value < 0 else ""
    return f"<span class='{css}'>{value:+.{decimals}f}%</span>"


def file_time(path: Path) -> str:
    if not path.exists():
        return "never"
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %H:%M")


def run_job(label: str, args: list) -> bool:
    """Start a background refresh (one at a time)."""
    if job["process"] is not None and job["process"].poll() is None:
        return False
    log_path = FOLDER / "dashboard_job.log"
    log_file = open(log_path, "w", encoding="utf-8", errors="replace")
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")
    creation_flags = 0x08000000 if sys.platform == "win32" else 0   # CREATE_NO_WINDOW
    job.update(
        process=subprocess.Popen(
            [str(python)] + args,
            cwd=FOLDER,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        ),
        label=label,
        started=datetime.now(),
        finished=None,
        returncode=None,
    )

    def watch(process, handle):
        process.wait()
        handle.close()
        job["finished"] = datetime.now()
        job["returncode"] = process.returncode
        job["log"] = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]

    threading.Thread(target=watch, args=(job["process"], log_file), daemon=True).start()
    return True


def job_banner() -> str:
    process = job["process"]
    if process is None:
        return ""
    if process.poll() is None:
        seconds = int((datetime.now() - job["started"]).total_seconds())
        return (
            f"<div class='banner run'>⏳ {esc(job['label'])} running… {seconds}s "
            "(usually 30–90 s; this page refreshes itself)</div>"
            "<meta http-equiv='refresh' content='5'>"
        )
    if job["returncode"] == 0:
        return f"<div class='banner ok'>✓ {esc(job['label'])} finished at {job['finished']:%H:%M:%S}</div>"
    return (
        f"<div class='banner err'>✗ {esc(job['label'])} failed (code {job['returncode']}). "
        f"<details><summary>log</summary><pre>{esc(job['log'])}</pre></details></div>"
    )


# ---------------------------------------------------------------- layout
STYLE = """
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', Arial, sans-serif; background: #eef1f5; color: #1f2933; margin: 0; }
header { background: #1F2A44; color: #fff; padding: 14px 24px; display: flex; align-items: center; gap: 24px;
         position: sticky; top: 0; z-index: 5; flex-wrap: wrap; }
header h1 { font-size: 20px; margin: 0; } header nav a { color: #c8d3e0; margin-right: 16px; text-decoration: none; }
header nav a:hover { color: #fff; } header .spacer { flex: 1; }
header form { display: inline; } button, .btn { background: #0E9F6E; color: #fff; border: 0; padding: 8px 14px;
         border-radius: 6px; cursor: pointer; font-size: 14px; text-decoration: none; display: inline-block; }
button.secondary, .btn.secondary { background: #2F6FD6; } button.grey { background: #52606D; }
main { max-width: 1400px; margin: 0 auto; padding: 18px 24px 40px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 16px; }
.card { background: #fff; border-radius: 10px; padding: 16px 18px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
        border-top: 5px solid #d9dee4; }
.card h2 { margin: 0 0 8px; font-size: 17px; } .card.green { border-top-color: #0E9F6E; }
.card.red { border-top-color: #D64545; } .card.amber { border-top-color: #E09F1F; } .card.blue { border-top-color: #2F6FD6; }
.big { font-size: 28px; font-weight: 700; } .label { font-weight: 700; font-size: 15px; margin: 4px 0 8px; }
.muted { color: #7b8794; font-size: 13px; } .pos { color: #0e7c5a; font-weight: 600; } .neg { color: #c62828; font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: 14px; } th, td { text-align: left; padding: 5px 6px;
        border-bottom: 1px solid #e4e7eb; } th { background: #f0f4f8; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 14px; margin-top: 6px; }
.kv div:nth-child(odd) { color: #52606D; }
.banner { padding: 10px 14px; border-radius: 8px; margin-bottom: 14px; } .banner.run { background: #fff6dd; }
.banner.ok { background: #e3f8ee; } .banner.err { background: #fde8e8; }
.alerts li { margin: 6px 0; } img.chart { width: 100%; border-radius: 6px; margin-top: 8px; }
.wide { grid-column: 1 / -1; } input, textarea { font-size: 14px; padding: 6px; border: 1px solid #c9d1d9; border-radius: 6px; }
textarea { width: 100%; font-family: Consolas, monospace; } form.stack label { display: block; margin: 10px 0 4px; font-weight: 600; }
"""


def page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{esc(title)}</title><style>{STYLE}</style></head><body>"
        "<header><h1>📈 Trading Monitor</h1><nav>"
        f"<a href='{url_for('overview')}'>Overview</a>"
        f"<a href='{url_for('report', kind='close')}' target='_blank'>Close report</a>"
        f"<a href='{url_for('report', kind='morning')}' target='_blank'>Morning brief</a>"
        f"<a href='{url_for('broksum')}'>Broksum</a>"
        f"<a href='{url_for('positions')}'>Positions</a>"
        f"<a href='{url_for('deck')}'>Deck (PPT)</a></nav><span class='spacer'></span>"
        f"<form method='post' action='{url_for('refresh', kind='close')}'><button>⟳ Refresh market data</button></form>"
        f"<form method='post' action='{url_for('refresh', kind='morning')}'><button class='secondary'>☀ Morning brief</button></form>"
        "</header><main>" + job_banner() + body + "</main></body></html>"
    )


# ---------------------------------------------------------------- overview cards
def position_card_bmri(positions: dict) -> str:
    summary = load_json("latest_close_summary.json")
    if not summary:
        return "<div class='card'><h2>BMRI</h2><p>No data yet - press Refresh.</p></div>"
    close = summary["close"]
    average = positions.get("BMRI", {}).get("average_price")
    lots = positions.get("BMRI", {}).get("lots")
    ta = summary.get("ta", {})
    if not average:
        alerts = "".join(f"<li>{esc(a)}</li>" for a in summary.get("alerts", []))
        return (
            "<div class='card'><h2>BMRI · watch only</h2>"
            f"<div class='big'>{close:,.0f} {pct_html((close / summary['previous_close'] - 1) * 100, 2)}</div>"
            f"<div class='label'>{esc(ta.get('verdict', ''))}</div>"
            "<div class='kv'>"
            f"<div>Technical bias</div><div>{esc(ta.get('bias', '–'))} ({ta.get('score', 0):+d})</div>"
            f"<div>Trend</div><div>{esc(ta.get('trend', '–'))}</div>"
            "</div>"
            + (f"<ul class='alerts'>{alerts}</ul>" if alerts else "")
            + "<p class='muted'>No position. Enter an average price on the Positions page if you buy again.</p>"
            f"<img class='chart' src='{url_for('chart', name='bmri')}'></div>"
        )
    pnl = (close / average - 1) * 100 if average else None
    color = "red" if close < core.STOP_CLOSE else "green" if close >= core.BREAKEVEN_PRICE else "amber"
    pnl_rp = f" · Rp{(close - average) * lots * 100:+,.0f}" if average and lots else ""
    alerts = "".join(f"<li>{esc(a)}</li>" for a in summary.get("alerts", []))
    return (
        f"<div class='card {color}'><h2>BMRI · your position</h2>"
        f"<div class='big'>{close:,.0f} {pct_html((close / summary['previous_close'] - 1) * 100, 2)}</div>"
        f"<div class='label'>{esc(ta.get('verdict', ''))}</div>"
        "<div class='kv'>"
        f"<div>Average</div><div>{average:,.0f} → {pct_html(pnl)}{pnl_rp}</div>"
        f"<div>Stop (close)</div><div>{core.STOP_CLOSE:,} ({close - core.STOP_CLOSE:+,.0f})</div>"
        f"<div>T1 / breakout</div><div>{core.TARGET_1:,} ({core.TARGET_1 - close:+,.0f}) / {core.BREAKOUT_LEVEL:,}</div>"
        f"<div>T2 / T3</div><div>{core.TARGET_2:,} / {core.TARGET_3:,}</div>"
        f"<div>Technical bias</div><div>{esc(ta.get('bias', '–'))} ({ta.get('score', 0):+d})</div>"
        f"<div>Broksum</div><div>{esc(summary.get('broksum_signal') or 'not entered')}</div>"
        "</div>"
        + (f"<ul class='alerts'>{alerts}</ul>" if alerts else "")
        + f"<p class='muted'>{esc(summary['date'])} {'close' if summary.get('final') else 'intraday'}</p>"
        f"<img class='chart' src='{url_for('chart', name='bmri')}'></div>"
    )


def position_card_tebe(positions: dict) -> str:
    tebe = load_json("tebe_summary.json")
    if not tebe:
        return "<div class='card'><h2>TEBE</h2><p>No data yet - press Refresh.</p></div>"
    close = tebe["close"]
    average = positions.get("TEBE", {}).get("average_price")
    lots = positions.get("TEBE", {}).get("lots")
    levels = tebe.get("levels", {})
    color = "red" if close < levels.get("support", 0) else "amber" if close < levels.get("gap", 1e9) else "green"
    position_row = ""
    if average:
        pnl_rp = f" · Rp{(close - average) * lots * 100:+,.0f}" if lots else ""
        position_row = f"<div>Average</div><div>{average:,.0f} → {pct_html((close / average - 1) * 100)}{pnl_rp}</div>"
    alerts = "".join(f"<li>{esc(a)}</li>" for a in tebe.get("alerts", {}).values())
    return (
        f"<div class='card {color}'><h2>TEBE · {'your position' if average else 'watch'}</h2>"
        f"<div class='big'>{close:,.0f} {pct_html(tebe['change_1d'])}</div>"
        f"<div class='label'>{esc(tebe['verdict'])}</div>"
        f"<div class='kv'>{position_row}"
        f"<div>Stop (close)</div><div>{levels.get('support', 0):,} ({close - levels.get('support', 0):+,.0f})</div>"
        f"<div>Sell part / gap</div><div>{levels.get('resistance', 0):,}–2,110 / {levels.get('gap', 0):,}</div>"
        f"<div>Volatility</div><div>ATR ~{tebe['atr_pct']:.0f}% per day</div>"
        "</div>"
        f"<p>{esc(tebe['guidance'])}</p>"
        + (f"<ul class='alerts'>{alerts}</ul>" if alerts else "")
        + f"<img class='chart' src='{url_for('chart', name='tebe')}'></div>"
    )


def ihsg_card() -> str:
    ihsg = load_json("ihsg_summary.json")
    if not ihsg:
        return "<div class='card'><h2>IHSG</h2><p>No data yet.</p></div>"
    outlook = ihsg["outlook"]
    color = "green" if outlook["score"] >= 1 else "red" if outlook["score"] <= -1 else "amber"
    flow = ihsg.get("net_foreign_today")
    reasons = "".join(f"<li>{esc(r)}</li>" for r in outlook["reasons"])
    return (
        f"<div class='card {color}'><h2>IHSG · market call</h2>"
        f"<div class='big'>{ihsg['close']:,.0f} {pct_html(ihsg['change_1d'], 2)}</div>"
        f"<div class='label'>{esc(outlook['label'])} ({outlook['score']:+d}, {esc(outlook['confidence'])})</div>"
        f"<p>{esc(outlook['expectation'])}</p><ul>{reasons}</ul>"
        "<div class='kv'>"
        f"<div>vs 15 Oct</div><div>{pct_html(ihsg.get('vs_pre_shock'))}</div>"
        f"<div>Foreign today</div><div>{'not entered' if flow is None else f'Rp{flow:+,.0f}bn'}</div>"
        "</div>"
        f"<img class='chart' src='{url_for('chart', name='ihsg')}'></div>"
    )


def banks_card() -> str:
    banks = load_json("big_banks_summary.json")
    if not banks:
        return "<div class='card'><h2>Big 4</h2><p>No data yet.</p></div>"
    sector = banks.get("sector", {})
    rows = ""
    triggers = []
    for bank in banks["banks"]:
        outlook = bank.get("outlook", {})
        rows += (
            f"<tr><td><b>{bank['code']}</b></td><td>{bank['close']:,.0f}</td><td>{pct_html(bank['change_1d'])}</td>"
            f"<td>{pct_html(bank['vs_pre_shock'])}</td><td>{esc(outlook.get('label', bank['bias']))}</td></tr>"
        )
        for key, message in (bank.get("entry") or {}).get("triggers", []):
            triggers.append(message)
    trigger_html = "".join(f"<li>{esc(t)}</li>" for t in triggers) or "<li class='muted'>No entry triggers today</li>"
    return (
        f"<div class='card blue'><h2>Big 4 banks</h2><div class='label'>{esc(sector.get('label', ''))}</div>"
        "<table><tr><th>Bank</th><th>Close</th><th>1D</th><th>vs 15 Oct</th><th>Direction lean</th></tr>"
        f"{rows}</table><p class='label'>Entry alerts (BBCA / BBRI / BBNI)</p><ul class='alerts'>{trigger_html}</ul>"
        f"<img class='chart' src='{url_for('chart', name='banks')}'></div>"
    )


def macro_card() -> str:
    macro = load_json("macro_summary.json")
    if not macro:
        return "<div class='card'><h2>Macro</h2><p>No data yet.</p></div>"
    rows = ""
    for asset in macro["assets"]:
        d = asset["decimals"]
        support = asset["supports"][0][0] if asset["supports"] else None
        resistance = asset["resistances"][0][0] if asset["resistances"] else None
        rows += (
            f"<tr><td><b>{esc(asset['name'])}</b></td><td>{asset['close']:,.{d}f}</td><td>{pct_html(asset['change_1d'], 2)}</td>"
            f"<td>{esc(asset['trend'])}</td><td>{'–' if support is None else f'{support:,.{d}f}'}</td>"
            f"<td>{'–' if resistance is None else f'{resistance:,.{d}f}'}</td></tr>"
            f"<tr><td></td><td colspan='5' class='muted'>{esc(asset.get('impact_short') or asset['impact'])}</td></tr>"
        )
    color = "red" if "headwind" in macro["overall"].lower() else "green" if "supportive" in macro["overall"].lower() else "amber"
    return (
        f"<div class='card {color}'><h2>Macro · Gold · Brent · DXY</h2><div class='label'>{esc(macro['overall'])}</div>"
        "<table><tr><th>Asset</th><th>Last</th><th>1D</th><th>Trend</th><th>Support</th><th>Resistance</th></tr>"
        f"{rows}</table><p class='muted'>Quotes {esc(macro['generated'])} WIB</p>"
        f"<img class='chart' src='{url_for('chart', name='macro')}'></div>"
    )


def broksum_card() -> str:
    path = core.BROKSUM_FILE
    if not path.exists():
        return (
            "<div class='card'><h2>Broksum (BMRI)</h2><p>No broksum entered yet.</p>"
            f"<a class='btn' href='{url_for('broksum')}'>Enter today's broksum</a></div>"
        )
    history = pd.read_csv(path).sort_values("Date").tail(5)
    rows = "".join(
        f"<tr><td>{esc(r.Date)}</td><td>{esc(r.Signal)}</td><td>{'' if pd.isna(r.NetForeignBn) else f'{r.NetForeignBn:+,.1f}'}</td>"
        f"<td>{esc(r.TopBuyer)}</td><td>{esc(r.TopSeller)}</td></tr>"
        for r in history.itertuples()
    )
    return (
        "<div class='card'><h2>Broksum (BMRI)</h2><table><tr><th>Date</th><th>Signal</th><th>Foreign bn</th>"
        f"<th>Top buyer</th><th>Top seller</th></tr>{rows}</table><br>"
        f"<a class='btn' href='{url_for('broksum')}'>Enter today's broksum</a></div>"
    )


def status_card() -> str:
    state = load_json("alert_state.json")
    notified = state.get("notified", {})
    items = [
        ("Close report", file_time(FOLDER / "reports" / "latest_close.html")),
        ("Morning brief", file_time(FOLDER / "reports" / "latest_morning.html")),
        ("Deck", file_time(DECK_FILE)),
        ("Alerts sent today", ", ".join(notified.get("keys", [])) or "none"),
    ]
    rows = "".join(f"<div>{esc(k)}</div><div>{esc(v)}</div>" for k, v in items)
    return (
        "<div class='card'><h2>System</h2><div class='kv'>" + rows + "</div>"
        "<p class='muted'>Scheduled: 08:00 morning brief · 16:20 close report · 18:00 broksum update (Mon–Fri). "
        "Education/planning tool, not financial advice.</p></div>"
    )


# ---------------------------------------------------------------- routes
@app.route("/")
def overview():
    positions = core.load_positions()
    body = (
        "<div class='grid'>"
        + position_card_bmri(positions)
        + position_card_tebe(positions)
        + ihsg_card()
        + banks_card()
        + macro_card()
        + broksum_card()
        + status_card()
        + "</div>"
    )
    return page("Trading Monitor", body)


@app.route("/chart/<name>")
def chart(name):
    path = CHARTS.get(name)
    if path is None or not path.exists():
        abort(404)
    return send_file(path, max_age=0)


@app.route("/report/<kind>")
def report(kind):
    files = {"close": "latest_close.html", "morning": "latest_morning.html"}
    path = FOLDER / "reports" / files.get(kind, "")
    if kind not in files or not path.exists():
        return page("Report", "<div class='card'><p>Report not generated yet - press a refresh button.</p></div>")
    return send_file(path, max_age=0)


@app.route("/deck")
def deck():
    if not DECK_FILE.exists():
        return page("Deck", "<div class='card'><p>Deck not generated yet.</p></div>")
    return send_file(DECK_FILE, as_attachment=True, download_name=f"BMRI_Swing_Recap_{datetime.now():%Y%m%d}.pptx")


@app.route("/refresh/<kind>", methods=["POST"])
def refresh(kind):
    if kind == "morning":
        run_job("Morning brief", ["bmri_morning_brief.py", "--no-open"])
    else:
        run_job("Market data refresh", ["bmri_daily_check.py", "--no-ask", "--no-open", "--no-popup", "--no-wait"])
    return redirect(url_for("overview"))


@app.route("/broksum", methods=["GET", "POST"])
def broksum():
    message = ""
    if request.method == "POST":
        entry = core.parse_broksum(
            request.form.get("foreign", ""),
            request.form.get("brokers", ""),
            request.form.get("ihsg", ""),
        )
        if entry is None:
            message = "<div class='banner err'>Nothing to save - enter at least one value.</div>"
        else:
            prices = pd.read_csv(core.PRICE_FILE, index_col=0, parse_dates=True)
            trade_date = prices.index[-1]
            if entry.get("ihsg_net_foreign") is not None:
                core.save_ihsg_flow(trade_date, entry["ihsg_net_foreign"])
            if entry["brokers"] or entry["net_foreign"] is not None:
                analysis = core.analyse_broksum(entry, core.load_broker_map(), float(prices["Close"].iloc[-1]))
                core.save_broksum(trade_date, analysis)
                message = (
                    f"<div class='banner ok'>Saved for {trade_date:%d %b}: <b>{esc(analysis['signal'])}</b> - "
                    f"{esc(analysis['meaning'])} Refreshing the analysis…</div>"
                )
            else:
                message = f"<div class='banner ok'>IHSG foreign flow saved for {trade_date:%d %b}. Refreshing…</div>"
            run_job("Analysis with broksum", ["bmri_daily_check.py", "--no-ask", "--no-open", "--no-popup", "--no-wait"])
    body = message + (
        "<div class='card' style='max-width:720px'><h2>Today's broker summary</h2>"
        f"<form method='post' class='stack' action='{url_for('broksum')}'>"
        "<label>BMRI net foreign (Rp bn, minus = net sell)</label><input name='foreign' placeholder='-45.2'>"
        "<label>IHSG market net foreign (Rp bn, minus = net sell)</label><input name='ihsg' placeholder='-812.5'>"
        "<label>BMRI top net buyers / sellers, one per line: CODE value(Rp bn) [avg price]</label>"
        "<textarea name='brokers' rows='9' placeholder='AK 125.3 3790&#10;BK 80&#10;YP -150 3805&#10;XL -60'></textarea>"
        "<br><br><button>Save & re-analyse</button></form>"
        "<p class='muted'>Same analysis as the 18:00 popup. Broker types are in brokers.json.</p></div>"
    )
    return page("Broksum", body)


@app.route("/positions", methods=["GET", "POST"])
def positions():
    data = core.load_positions()
    message = ""
    if request.method == "POST":
        for code in list(data):
            average = request.form.get(f"{code}_avg", "").replace(",", "").strip()
            lots = request.form.get(f"{code}_lots", "").replace(",", "").strip()
            data[code]["average_price"] = float(average) if average else None
            data[code]["lots"] = int(float(lots)) if lots else None
        core.POSITIONS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        message = "<div class='banner ok'>Positions saved. Refreshing so reports and alerts use them…</div>"
        run_job("Refresh with new positions", ["bmri_daily_check.py", "--no-ask", "--no-open", "--no-popup", "--no-wait"])
    rows = "".join(
        f"<tr><td><b>{esc(code)}</b></td>"
        f"<td><input name='{code}_avg' value='{'' if v.get('average_price') is None else format(v['average_price'], 'g')}' size='10'></td>"
        f"<td><input name='{code}_lots' value='{'' if v.get('lots') is None else v['lots']}' size='8'></td></tr>"
        for code, v in data.items()
    )
    body = message + (
        "<div class='card' style='max-width:720px'><h2>Your positions</h2>"
        f"<form method='post' action='{url_for('positions')}'><table><tr><th>Stock</th><th>Average price</th>"
        f"<th>Lots (optional, for Rp P/L)</th></tr>{rows}</table><br><button>Save positions</button></form>"
        "<p class='muted'>Leave the average empty if you sold (the stock goes back to watch mode). "
        "Breakeven is recalculated with a ~0.25% sell fee. BMRI stop/targets stay as planned (3,600 / 3,960 / 4,100).</p></div>"
    )
    return page("Positions", body)


def _already_running():
    """True if a dashboard is already listening on PORT (second double-click)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    url = f"http://127.0.0.1:{PORT}"
    open_browser = "--no-browser" not in sys.argv
    if _already_running():
        # Server is up already: just open the page instead of starting a second copy
        if open_browser:
            webbrowser.open(url)
        return
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()

"""
Trading-app pages for the IDX Investing Monitor (registered on the main Flask app).

  /            Home dashboard: IHSG / LQ45, breadth, sectors, movers, favorites, portfolio, alerts, news
  /heatmap     Market heatmap (sector treemap)
  /stock/CODE  Pro stock page: chart + indicator pane, order ticket & position sizer, technicals,
               key stats, dividends, news, plan, broksum, alerts
  /journal     Trade journal (record buys / sells, realized P/L, equity curve)
  /alerts      Price alerts
  /settings    Fees, capital, risk per trade
Education only - not financial advice. The app never sends orders to a broker.
"""

import ctypes
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from flask import jsonify, redirect, request

import all_stocks
import dividend_watchlist as engine
import live_data
import market_context
import portfolio
import trade_tools


def show_popup(title: str, message: str) -> None:
    if sys.platform == "win32":
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40 | 0x40000 | 0x10000)


def _json_script(name: str, data) -> str:
    return f"<script>window.{name} = {json.dumps(data, default=str).replace('</', '<' + chr(92) + '/')};</script>"


def register(app, page, esc, portfolio_page_html):
    trade_tools.start_alert_checker(show_popup)

    def fmt(value, digits=0, suffix="", prefix=""):
        if value is None:
            return "–"
        return f"{prefix}{value:,.{digits}f}{suffix}"

    def big_rp(value):
        if value is None:
            return "–"
        for size, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
            if abs(value) >= size:
                return f"Rp{value / size:,.2f}{unit}"
        return f"Rp{value:,.0f}"

    def logo_html(url, code, size=40):
        initials = esc(code[:2])
        if not url:
            return f"<span class='logo-fallback' style='width:{size}px;height:{size}px'>{initials}</span>"
        return (f"<img class='stock-logo' src='{esc(url)}' width='{size}' height='{size}' alt='' "
                f"onerror=\"this.outerHTML='<span class=&quot;logo-fallback&quot; style=&quot;width:{size}px;height:{size}px&quot;>{initials}</span>'\">")

    # ------------------------------------------------------------------ home
    @app.route("/")
    def home():
        context = market_context.load_summary() or {}
        regime = context.get("regime", {})
        news = []
        for items in (context.get("news") or {}).values():
            news.extend(items[:3])
        news_html = "".join(
            f"<li><span class='dot {esc(n.get('tone', 'neutral'))}'></span><a href='{esc(n['link'])}' target='_blank' rel='noopener'>"
            f"{esc(n['title'])}</a><div class='muted'>{esc(n.get('source', ''))} · {esc(n.get('time') or '')}</div></li>"
            for n in news[:9] if n.get("link")
        ) or "<li class='muted'>Refresh the Macro page to load news.</li>"
        body = f"""
<div class="home-top">
  <a class="card idx-card" href="/market"><div class="idx-head"><b>IHSG</b><span class="muted">IDX Composite</span></div>
    <div class="idx-price"><span data-q="IHSG" data-f="price">–</span> <small data-q="IHSG" data-f="chg"></small></div>
    <div class="muted">Range <span data-q="IHSG" data-f="range">–</span></div><div class="mini" id="mini-IHSG"></div></a>
  <a class="card idx-card" href="/market"><div class="idx-head"><b>LQ45</b><span class="muted">45 most liquid</span></div>
    <div class="idx-price"><span data-q="LQ45" data-f="price">–</span> <small data-q="LQ45" data-f="chg"></small></div>
    <div class="muted">Range <span data-q="LQ45" data-f="range">–</span></div><div class="mini" id="mini-LQ45"></div></a>
  <div class="card"><h4 style="margin-top:0">Market breadth</h4><div id="breadth" class="muted">Loading…</div></div>
  <a class="card" href="/portfolio"><h4 style="margin-top:0">My portfolio</h4><div id="pf" class="muted">Loading…</div></a>
  <a class="card regime {esc(regime.get('class', 'grey'))}-border" href="/market"><h4 style="margin-top:0">Market regime</h4>
    <div class="regime-label {esc(regime.get('class', 'grey'))}" style="font-size:15px">{esc(regime.get('label', 'Open Macro page'))}</div>
    <div class="small">{esc((regime.get('advice') or '')[:150])}</div></a>
</div>
<div class="home-grid">
  <div class="card tabs" id="movers">
    <div class="tab-bar">
      <button data-tab="gainers" class="on">Top gainers</button><button data-tab="losers">Top losers</button>
      <button data-tab="value">Top value</button><button data-tab="volume">Top volume</button>
      <button data-tab="unusual">Unusual volume</button><button data-tab="ara">Near ARA</button><button data-tab="arb">Near ARB</button>
      <label class="muted" style="margin-left:auto">Min value <select id="minval"><option value="10">Rp10bn</option>
        <option value="1">Rp1bn</option><option value="0">Any</option></select></label>
    </div>
    <div data-panel="gainers"></div><div data-panel="losers" style="display:none"></div><div data-panel="value" style="display:none"></div>
    <div data-panel="volume" style="display:none"></div><div data-panel="unusual" style="display:none"></div>
    <div data-panel="ara" style="display:none"></div><div data-panel="arb" style="display:none"></div>
  </div>
  <div class="card"><div class="card-head"><h2>Sectors</h2><a href="/heatmap" class="muted">Heatmap →</a></div><div id="sectors" class="muted">Loading…</div></div>
</div>
<div class="home-grid">
  <div class="card"><div class="card-head"><h2>★ Favorites</h2><span class="muted">tap ☆ on any stock to add</span></div><div id="favs" class="muted">Loading…</div></div>
  <div>
    <div class="card"><div class="card-head"><h2>📋 Queue plans</h2><span class="muted">open a stock → Queue plan tab</span></div>
      <div id="plans-box" class="muted">Loading…</div></div>
    <div class="card"><div class="card-head"><h2>🔔 Alerts</h2><a href="/alerts" class="muted">Manage →</a></div><div id="alerts-box" class="muted">Loading…</div></div>
    <div class="card news"><div class="card-head"><h2>News</h2><a href="/market" class="muted">More →</a></div><ul>{news_html}</ul></div>
  </div>
</div>
<script src="/static/home.js"></script>"""
        return page("Home", body)

    @app.route("/api/home")
    def api_home():
        min_value = float(request.args.get("min_value", 1))
        result = {}
        try:
            result["breadth"] = trade_tools.breadth_and_sectors()
            result["movers"] = trade_tools.movers(min_value_bn=min_value)
            stocks = {s["code"]: s for s in trade_tools.market_live()["stocks"]}
            result["favorites"] = [stocks.get(code, {"code": code}) for code in trade_tools.load_favorites()]
        except Exception as error:
            result["error_market"] = str(error)
        try:
            result["portfolio"] = trade_tools.portfolio_live()
        except Exception as error:
            result["error_portfolio"] = str(error)
        result["alerts"] = [dict(a, text=trade_tools.describe_alert(a)) for a in trade_tools.load_alerts() if a["active"]]
        result["plans"] = plans_with_summary()
        result["market"] = live_data.market_status()
        return jsonify(result)

    def plans_with_summary():
        plans = [p for p in trade_tools.load_plans() if p.get("status", "active") == "active"]
        quotes = live_data.get_quotes([p["code"] for p in plans]) if plans else {}
        return [dict(p, summary=trade_tools.plan_summary(p, (quotes.get(p["code"]) or {}).get("price"))) for p in plans]

    @app.route("/api/plans", methods=["GET", "POST"])
    def api_plans():
        if request.method == "POST":
            try:
                plan = trade_tools.save_plan(request.get_json(silent=True) or {})
            except Exception as error:
                return jsonify({"error": str(error)}), 400
            price = (live_data.get_quotes([plan["code"]]).get(plan["code"]) or {}).get("price")
            return jsonify(dict(plan, summary=trade_tools.plan_summary(plan, price)))
        return jsonify(plans_with_summary())

    @app.route("/api/plans/<code>", methods=["GET"])
    def api_plan(code):
        plan = trade_tools.get_plan(code)
        if not plan:
            return jsonify(None)
        price = (live_data.get_quotes([plan["code"]]).get(plan["code"]) or {}).get("price")
        return jsonify(dict(plan, summary=trade_tools.plan_summary(plan, price)))

    @app.route("/api/plans/<code>/macro", methods=["GET", "POST"])
    def api_plan_macro(code):
        import macro_sync

        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            try:
                trade_tools.set_plan_macro(code, body.get("rules") or [])
            except Exception as error:
                return jsonify({"error": str(error)}), 400
        plan = trade_tools.get_plan(code)
        try:
            if plan:
                return jsonify(macro_sync.plan_macro(plan))
            return jsonify(macro_sync.analyse(code))
        except Exception as error:
            return jsonify({"error": f"Macro data unavailable: {error}"}), 502

    @app.route("/api/plans/<code>/fill", methods=["POST"])
    def api_plan_fill(code):
        body = request.get_json(silent=True) or {}
        try:
            trade = trade_tools.fill_plan_entry(code, int(body.get("index", 0)), body.get("price"))
        except Exception as error:
            return jsonify({"error": str(error)}), 400
        try:
            portfolio.build()
        except Exception:
            pass
        return jsonify(trade)

    @app.route("/api/plans/<code>/close", methods=["POST"])
    def api_plan_close(code):
        status = (request.get_json(silent=True) or {}).get("status", "cancelled")
        trade_tools.close_plan(code, "done" if status == "done" else "cancelled")
        return jsonify({"ok": True})

    # ------------------------------------------------------------------ heatmap
    @app.route("/heatmap")
    def heatmap():
        body = """
<div class="filters">
  <label>Stocks<select id="hm-universe"><option value="top">Top 150 by market cap</option>
    <option value="liquid" selected>Liquid (≥ Rp10bn/day)</option><option value="active">Active (≥ Rp1bn/day)</option></select></label>
  <label>Size by<select id="hm-size"><option value="mcap_t">Market cap</option><option value="value_bn">Value traded today</option></select></label>
  <label>Period<select id="hm-period"><option value="change">Today (live)</option><option value="perf_1w">1 week</option>
    <option value="perf_1m">1 month</option><option value="perf_ytd">Year to date</option></select></label>
  <div class="hm-legend"><span>-5%</span><i style="background:#b4233c"></i><i style="background:#7a2a3a"></i><i style="background:#3a4256"></i>
    <i style="background:#1f6b45"></i><i style="background:#16a34a"></i><span>+5%</span></div>
  <span class="muted" id="hm-info"></span>
</div>
<div id="heatmap" class="heatmap"></div><div id="hm-tip" class="hm-tip"></div>
<script src="/static/heatmap.js"></script>"""
        return page("Heatmap", body)

    @app.route("/api/heatmap")
    def api_heatmap():
        data = trade_tools.market_live()
        snapshot = {s["code"]: s for s in (all_stocks.load_summary() or {}).get("stocks", [])}
        rows = []
        for s in data["stocks"]:
            snap = snapshot.get(s["code"], {})
            rows.append(dict(s, perf_1w=snap.get("perf_1w"), perf_1m=snap.get("perf_1m"), perf_ytd=snap.get("perf_ytd")))
        return jsonify({"stocks": rows, "time": data["time"]})

    # ------------------------------------------------------------------ small APIs
    @app.route("/api/sparks")
    def api_sparks():
        codes = request.args.get("codes", "").split(",")
        return jsonify(trade_tools.sparks(codes))

    @app.route("/api/favorites", methods=["GET", "POST"])
    def api_favorites():
        if request.method == "POST":
            code = (request.get_json(silent=True) or {}).get("code", "")
            return jsonify({"on": trade_tools.toggle_favorite(code)})
        return jsonify(trade_tools.load_favorites())

    @app.route("/api/alerts", methods=["GET", "POST", "DELETE"])
    def api_alerts():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            try:
                alert = trade_tools.add_alert(body.get("code", ""), body.get("type", ""), float(body.get("value")), body.get("note", ""))
            except Exception as error:
                return jsonify({"error": str(error)}), 400
            return jsonify(dict(alert, text=trade_tools.describe_alert(alert)))
        if request.method == "DELETE":
            trade_tools.delete_alert(request.args.get("id", ""))
            return jsonify({"ok": True})
        code = request.args.get("code")
        alerts = [dict(a, text=trade_tools.describe_alert(a)) for a in trade_tools.load_alerts()
                  if not code or a["code"] == code.upper()]
        return jsonify(alerts)

    @app.route("/api/news/<code>")
    def api_news(code):
        snap = all_stocks.find(code.upper()) or {}
        return jsonify(trade_tools.stock_news(code, snap.get("name", "")))

    @app.route("/api/dividends/<code>")
    def api_dividends(code):
        try:
            return jsonify(trade_tools.dividend_history(code))
        except Exception as error:
            return jsonify({"error": f"No dividend data: {error}"})

    @app.route("/api/detail/<code>")
    def api_detail(code):
        detail = trade_tools.stock_detail(code)
        return jsonify(detail or {"error": "unknown code"})

    @app.route("/api/trade", methods=["POST"])
    def api_trade():
        body = request.get_json(silent=True) or {}
        try:
            entry = trade_tools.record_trade(body.get("code", ""), body.get("side", ""), float(body.get("price") or 0),
                                             int(float(body.get("lots") or 0)), body.get("date", ""), body.get("note", ""))
        except Exception as error:
            return jsonify({"error": str(error)}), 400
        try:
            portfolio.build()
        except Exception:
            pass
        return jsonify(entry)

    # ------------------------------------------------------------------ stock page
    @app.route("/stock/<code>")
    def stock_view(code):
        code = code.upper().replace(".JK", "").strip()
        if code in ("IHSG", "COMPOSITE"):
            return redirect("/market")
        snapshot = all_stocks.find(code) or {}
        pdata = portfolio.load_portfolio()
        held = next((h for h in pdata.get("holdings", []) if h["code"] == code), None)
        max_loss = float(pdata.get("max_loss_pct", 8))

        def analyse():
            holding = dict(held) if held else {"code": code, "avg": snapshot.get("price") or 0, "hypothetical": True}
            if not holding["avg"]:
                prices, _ = engine.fetch_history(f"{code}.JK", years=1)
                holding["avg"] = float(prices["Close"].iloc[-1])
            row = portfolio.analyse_holding(holding, max_loss)
            row["alerts"] = portfolio.holding_alerts(row) if held else {}
            row["hypothetical"] = not held
            return row

        with ThreadPoolExecutor(max_workers=2) as pool:
            detail_job = pool.submit(trade_tools.stock_detail, code)
            row_job = pool.submit(analyse)
            try:
                detail = detail_job.result() or {}
            except Exception:
                detail = {}
            try:
                row = row_job.result()
            except Exception as error:
                row = None
                row_error = str(error)
        if not detail and row is None:
            return page(code, f"<div class='card'><h2>{esc(code)}</h2><p class='red'>No data for {esc(code)}: {esc(row_error)}</p>"
                              "<p><a href='/all'>← back to the screener</a></p></div>")

        # plan card from the portfolio analysis, without its own chart (the big chart is on top)
        levels = []
        plan_card = "<div class='card muted'>No daily history for a plan.</div>"
        if row is not None:
            summary = {"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "holdings": [row]}
            plan_card = portfolio_page_html(summary).split("<div class=\"two-col\">\n<form", 1)[0]
            found = re.search(r'<div data-chart data-code="[^"]*" data-levels="([^"]*)"></div>', plan_card)
            if found:
                levels = json.loads(found.group(1).replace("&quot;", '"').replace("&#x27;", "'").replace("&amp;", "&"))
                plan_card = plan_card.replace(found.group(0), "")
            if not held:
                plan_card = plan_card.replace(">HOLD within plan (below breakeven)<", ">Plan if you buy at today's price<").replace(
                    ">HOLD in profit: protect breakeven<", ">Plan if you buy at today's price<")

        alerts = [a for a in trade_tools.load_alerts() if a["code"] == code and a["active"]]
        alert_prices = [a["value"] for a in alerts if a["type"] in ("above", "below") and not a.get("plan")]
        plan = trade_tools.get_plan(code)
        if plan:
            # an active queue plan replaces the automatic levels on the chart
            levels = [
                {"price": e["price"], "color": "#22c55e" if e.get("filled") else "#38bdf8",
                 "title": f"Q{i} {e['lots']} lot" + (" ✓" if e.get("filled") else ""), "style": 0, "width": 2}
                for i, e in enumerate(plan["entries"], start=1)
            ]
            levels.append({"price": plan["exit"]["price"], "color": "#f04461", "title": "PLAN EXIT (close)", "style": 0, "width": 2})
            levels += [{"price": t, "color": "#4ade80", "title": f"T{i}"} for i, t in enumerate(plan["targets"], start=1)]
            if plan["breakout"].get("trigger"):
                levels.append({"price": plan["breakout"]["trigger"], "color": "#f5b942", "title": "Breakout (close)"})
        settings = trade_tools.load_settings()
        price = detail.get("close") or (row or {}).get("price")
        limits = detail.get("limits") or {}
        d = detail
        name = d.get("description") or snapshot.get("name", "")

        def stat(label, value, extra=""):
            return f"<div><span>{label}</span><b>{value}</b>{extra}</div>"

        float_pct = (d["float_shares_outstanding"] / d["total_shares_outstanding_fundamental"] * 100
                     if d.get("float_shares_outstanding") and d.get("total_shares_outstanding_fundamental") else None)
        key_stats = "".join([
            stat("Market cap", big_rp(d.get("market_cap_basic"))),
            stat("Enterprise value", big_rp(d.get("enterprise_value_fq"))),
            stat("Shares outstanding", fmt((d.get("total_shares_outstanding_fundamental") or 0) / 1e9, 2, "B") if d.get("total_shares_outstanding_fundamental") else "–"),
            stat("Free float", fmt(float_pct, 1, "%")),
            stat("P/E (TTM)", fmt(d.get("price_earnings_ttm"), 1)),
            stat("P/BV", fmt(d.get("price_book_fq"), 2)),
            stat("P/S", fmt(d.get("price_sales_current"), 2)),
            stat("EPS (TTM)", fmt(d.get("earnings_per_share_basic_ttm"), 1)),
            stat("Book value / share", fmt(d.get("book_value_per_share_fq"), 0)),
            stat("ROE", fmt(d.get("return_on_equity"), 1, "%")),
            stat("ROA", fmt(d.get("return_on_assets"), 1, "%")),
            stat("Net margin", fmt(d.get("net_margin_ttm"), 1, "%")),
            stat("Operating margin", fmt(d.get("operating_margin_ttm"), 1, "%")),
            stat("Revenue (TTM)", big_rp(d.get("total_revenue_ttm"))),
            stat("Net income (TTM)", big_rp(d.get("net_income_ttm"))),
            stat("Revenue growth YoY", fmt(d.get("total_revenue_yoy_growth_ttm"), 1, "%")),
            stat("EPS growth YoY", fmt(d.get("earnings_per_share_diluted_yoy_growth_ttm"), 1, "%")),
            stat("Free cash flow (TTM)", big_rp(d.get("free_cash_flow_ttm"))),
            stat("Debt / equity", fmt(d.get("debt_to_equity_fq"), 2)),
            stat("Current ratio", fmt(d.get("current_ratio_fq"), 2)),
            stat("Total debt", big_rp(d.get("total_debt_fq"))),
            stat("Total assets", big_rp(d.get("total_assets_fq"))),
            stat("Dividend yield", fmt(d.get("dividends_yield_current"), 2, "%")),
            stat("Last ex-dividend", esc(d.get("ex_dividend_date_recent") or "–")),
            stat("Beta (1Y)", fmt(d.get("beta_1_year"), 2)),
            stat("Daily volatility", fmt(d.get("Volatility.D"), 1, "%")),
            stat("52-week range", f"{fmt(d.get('price_52_week_low'))} – {fmt(d.get('price_52_week_high'))}"),
            stat("All-time range", f"{fmt(d.get('Low.All'))} – {fmt(d.get('High.All'))}"),
            stat("Avg volume 10d / 30d", f"{fmt((d.get('average_volume_10d_calc') or 0) / 1e6, 1, 'M')} / {fmt((d.get('average_volume_30d_calc') or 0) / 1e6, 1, 'M')}"),
            stat("Relative volume", fmt(d.get("relative_volume_10d_calc"), 2, "×")),
            stat("Employees", fmt(d.get("number_of_employees"))),
            stat("Earnings", f"last {esc(d.get('earnings_release_date') or '–')}", f"<div class='small'>next {esc(d.get('earnings_release_next_date') or '–')}</div>"),
        ])
        perf = "".join(
            f"<div><span>{label}</span><b class='{'green' if (d.get(key) or 0) >= 0 else 'red'}'>{fmt(d.get(key), 1, '%')}</b></div>"
            for label, key in (("1W", "Perf.W"), ("1M", "Perf.1M"), ("3M", "Perf.3M"), ("6M", "Perf.6M"),
                               ("YTD", "Perf.YTD"), ("1Y", "Perf.Y"), ("5Y", "Perf.5Y"))
        )
        technicals = d.get("technicals") or {}
        tech_rows = ""
        for group, title in (("moving", "Moving averages"), ("oscillators", "Oscillators")):
            rows = (technicals.get(group) or {}).get("rows", [])
            tech_rows += f"<div><h4>{title}</h4><table class='mini'><thead><tr><th>Name</th><th class='num'>Value</th><th>Action</th></tr></thead><tbody>"
            tech_rows += "".join(
                f"<tr><td>{esc(r['name'])}</td><td class='num'>{fmt(r['value'], 2)}</td>"
                f"<td class='{'green' if r['signal'] == 'Buy' else 'red' if r['signal'] == 'Sell' else 'grey'}'>{r['signal']}</td></tr>"
                for r in rows
            ) + "</tbody></table></div>"
        pivots = (technicals.get("pivots") or {})
        pivot_html = " · ".join(f"{k} <b>{fmt(v)}</b>" for k, v in pivots.items() if v)

        stock_data = {
            "code": code,
            "price": price,
            "prev": d.get("prev_close"),
            "ara": limits.get("ara"),
            "arb": limits.get("arb"),
            "exit": plan["exit"]["price"] if plan else (row or {}).get("exit_level"),
            "targets": plan["targets"] if plan and plan["targets"] else [t[0] for t in (row or {}).get("targets", [])],
            "held": {"lots": held.get("lots"), "avg": held.get("avg")} if held else None,
            "settings": settings,
            "technicals": technicals,
            "plan": plan,
            "atr": d.get("ATR"),
        }
        held_badge = (f"<span class='chip-static green'>In portfolio · {held.get('lots') or '?'} lots @ {held['avg']:,.0f}</span>"
                      if held else "")
        body = f"""
{_json_script('STOCK', stock_data)}
<div class="card stock-head">
  <div class="sh-left">
    {logo_html(d.get('logo'), code, 48)}
    <div><div class="sh-title"><h1>{esc(code)}</h1><span class="star big" data-star="{esc(code)}">☆</span>
      <button class="icon-btn" onclick="document.querySelector('[data-tab=alerts]').click();document.getElementById('al-value').focus()" title="Price alert">🔔</button></div>
      <div class="muted">{esc(name)} · {esc(d.get('sector') or snapshot.get('sector', ''))} · {esc(d.get('industry') or snapshot.get('industry', ''))}</div>
      {held_badge}</div>
  </div>
  <div class="sh-price"><span class="hero-price" data-q="{esc(code)}" data-f="price" data-color>{fmt(price)}</span>
    <div><b data-q="{esc(code)}" data-f="chgabs"></b> <b data-q="{esc(code)}" data-f="chg"></b></div></div>
  <div class="sh-stats">
    <div><span>Open</span><b data-q="{esc(code)}" data-f="open">{fmt(d.get('open'))}</b></div>
    <div><span>High</span><b data-q="{esc(code)}" data-f="high">{fmt(d.get('high'))}</b></div>
    <div><span>Low</span><b data-q="{esc(code)}" data-f="low">{fmt(d.get('low'))}</b></div>
    <div><span>Prev close</span><b>{fmt(d.get('prev_close'))}</b></div>
    <div><span>ARA</span><b class="green">{fmt(limits.get('ara'))}</b></div>
    <div><span>ARB</span><b class="red">{fmt(limits.get('arb'))}</b></div>
    <div><span>Volume</span><b data-q="{esc(code)}" data-f="volume">–</b></div>
    <div><span>Value</span><b data-q="{esc(code)}" data-f="value">–</b></div>
    <div><span>Lot (100 sh)</span><b>{big_rp(d.get('lot_value'))}</b></div>
    <div><span>Tick</span><b>{fmt(d.get('tick'))}</b></div>
    <div><span>Mkt cap</span><b>{big_rp(d.get('market_cap_basic'))}</b></div>
    <div><span>P/E · P/BV</span><b>{fmt(d.get('price_earnings_ttm'), 1)} · {fmt(d.get('price_book_fq'), 2)}</b></div>
  </div>
</div>
<div class="trade-grid">
  <div class="card chart-card"><div data-chart data-full data-code="{esc(code)}" data-levels="{esc(json.dumps(levels))}" data-alerts="{esc(json.dumps(alert_prices))}"></div></div>
  <div class="side-col">
    <div class="card ticket" id="ticket"></div>
    <div class="card"><h4 style="margin:0 0 4px">Technical rating (daily)</h4><div class="gauges" id="gauges"></div></div>
  </div>
</div>
<div class="card tabs" id="stock-tabs">
  <div class="tab-bar">
    <button data-tab="qplan" class="{'on' if plan else ''}">📋 Queue plan{' ●' if plan else ''}</button>
    <button data-tab="plan" class="{'' if plan else 'on'}">Plan &amp; levels</button><button data-tab="stats">Key stats</button>
    <button data-tab="tech">Technicals</button><button data-tab="div">Dividends</button><button data-tab="news">News</button>
    <button data-tab="broksum">Broksum</button><button data-tab="alerts">Alerts</button>
  </div>
  <div data-panel="qplan" style="{'' if plan else 'display:none'}"><div id="qplan-box"></div></div>
  <div data-panel="plan" style="{'display:none' if plan else ''}">{plan_card}</div>
  <div data-panel="stats" style="display:none"><div class="stat-grid">{key_stats}</div>
    <h4>Performance</h4><div class="stat-grid perf">{perf}</div>
    <p class="muted">Fundamentals from TradingView (latest reported, Rupiah). Check the company's financial report on idx.co.id before relying on them.</p></div>
  <div data-panel="tech" style="display:none"><div class="two-col">{tech_rows}</div>
    <p class="muted">Monthly classic pivots: {pivot_html or '–'}. Ratings are TradingView-style indicator votes on the daily chart - a summary of momentum, not a forecast.</p></div>
  <div data-panel="div" style="display:none"><div id="div-box" class="muted">Loading dividend history…</div></div>
  <div data-panel="news" style="display:none"><div id="news-box" class="muted">Loading news…</div></div>
  <div data-panel="broksum" style="display:none">
    <form method="post" action="/portfolio/broksum">
      <input type="hidden" name="code" value="{esc(code)}"><input type="hidden" name="next" value="/stock/{esc(code)}">
      <div class="filters" style="box-shadow:none;padding:0 0 8px;border:0;background:none">
        <label>Date<input name="date" type="date" value="{datetime.now():%Y-%m-%d}"></label>
        <label>Net foreign (Rp bn)<input name="net_foreign" size="8"></label>
      </div>
      <textarea name="brokers" rows="7" style="width:100%;font-family:Consolas,monospace" placeholder="One broker per line: CODE  net value  [avg price]&#10;AK 12.5 1300&#10;BK 8.2B 1295&#10;YP -9.4 1310"></textarea>
      <button class="btn">Save &amp; analyse</button>
      <span class="muted">Copy the top net buyers / sellers from your broker app's broker summary. The result shows in Plan &amp; levels.</span>
    </form></div>
  <div data-panel="alerts" style="display:none">
    <div class="filters" style="border:0;background:none;box-shadow:none;padding:0 0 10px">
      <label>When<select id="al-type"><option value="above">Price ≥</option><option value="below">Price ≤</option>
        <option value="change_up">Day change ≥ +%</option><option value="change_down">Day change ≤ −%</option></select></label>
      <label>Value<input id="al-value" size="9"></label><label>Note<input id="al-note" size="24" placeholder="optional"></label>
      <button class="btn" id="al-add">Add alert</button>
    </div><div id="al-list"></div>
    <p class="muted">Alerts are checked every 30 seconds while this app runs during market hours (popup + sound). Prices are ~10 min delayed.</p></div>
</div>
<script src="/static/stock.js"></script><script src="/static/plan.js"></script>"""
        message = request.args.get("msg", "")
        if message:
            body = f"<div class='notice'>{esc(message)}</div>" + body
        return page(code, body)

    # ------------------------------------------------------------------ journal
    @app.route("/journal", methods=["GET", "POST"])
    def journal():
        message = ""
        if request.method == "POST":
            form = request.form
            try:
                if form.get("delete"):
                    trade_tools.delete_transaction(form["delete"])
                    message = "Log line removed (holdings were not changed - edit them on the Portfolio page if needed)."
                else:
                    entry = trade_tools.record_trade(form.get("code", ""), form.get("side", ""), float(form.get("price") or 0),
                                                     int(float(form.get("lots") or 0)), form.get("date", ""), form.get("note", ""))
                    portfolio.build()
                    message = (f"Recorded {entry['side'].upper()} {entry['code']} {entry['lots']} lots @ {entry['price']:,.0f}"
                               + (f" - realized {entry['realized']:+,.0f} ({entry['realized_pct']:+.1f}%)" if entry.get("realized") is not None else ""))
            except Exception as error:
                message = f"Not recorded: {error}"
        stats = trade_tools.journal_stats()
        settings = trade_tools.load_settings()

        def money(value):
            if value is None:
                return "–"
            return f"<span class='{'green' if value >= 0 else 'red'}'>{value:+,.0f}</span>"

        rows = "".join(
            f"<tr><td>{esc(t['date'])}</td><td><a href='/stock/{esc(t['code'])}'><b>{esc(t['code'])}</b></a></td>"
            f"<td><span class='pill-{'green' if t['side'] == 'buy' else 'red'}'>{t['side'].upper()}</span></td>"
            f"<td class='num'>{t['price']:,.0f}</td><td class='num'>{t['lots']:,}</td><td class='num'>{t['gross']:,.0f}</td>"
            f"<td class='num'>{t.get('fee', 0):,.0f}</td><td class='num'>{money(t.get('realized'))}</td>"
            f"<td class='num'>{'' if t.get('realized_pct') is None else money(t['realized_pct']).replace('</span>', '%</span>')}</td>"
            f"<td class='small'>{esc(t.get('note', ''))}</td>"
            f"<td><form method='post' onsubmit=\"return confirm('Remove this log line? Holdings are not changed.')\">"
            f"<button class='link-btn' name='delete' value='{esc(t['id'])}'>✕</button></form></td></tr>"
            for t in stats["transactions"]
        ) or "<tr><td colspan='11' class='muted'>No trades recorded yet.</td></tr>"
        cards = f"""
<div class="kpis">
  <div class="card kpi"><span>Realized P/L</span><b>{money(stats['realized'])}</b></div>
  <div class="card kpi"><span>Closed trades</span><b>{stats['closed']}</b></div>
  <div class="card kpi"><span>Win rate</span><b>{fmt(stats['win_rate'], 0, '%')}</b></div>
  <div class="card kpi"><span>Avg win / avg loss</span><b>{money(stats['avg_win'])} / {money(stats['avg_loss'])}</b></div>
  <div class="card kpi"><span>Profit factor</span><b>{fmt(stats['profit_factor'], 2)}</b></div>
  <div class="card kpi"><span>Fees paid</span><b>{stats['fees']:,.0f}</b></div>
</div>"""
        body = f"""
{f"<div class='notice'>{esc(message)}</div>" if message else ""}
{cards}
<div class="two-col">
  <div class="card"><h2>Realized P/L curve</h2><div id="equity" style="height:260px"></div>
    {"" if stats['equity'] else "<p class='muted'>The curve appears after your first recorded SELL.</p>"}</div>
  <form method="post" class="card"><h2>Record a trade</h2>
    <div class="filters" style="border:0;background:none;box-shadow:none;padding:0">
      <label>Side<select name="side"><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
      <label>Code<input name="code" size="6" required></label><label>Date<input name="date" type="date" value="{datetime.now():%Y-%m-%d}"></label>
      <label>Price<input name="price" size="8" required></label><label>Lots<input name="lots" size="6" required></label>
      <label>Note / reason<input name="note" size="28" placeholder="setup, why, emotion"></label>
    </div>
    <button class="btn">Record</button>
    <p class="muted">Recording updates your Portfolio (average price and lots) and logs fees: buy {settings['fee_buy_pct']}% ·
      sell {settings['fee_sell_pct']}% (change in <a href="/settings">Settings</a>). Realized P/L = (sell − avg) × shares − fees.
      This app never sends orders - trade in your broker app first.</p>
  </form>
</div>
<div class="card"><h2>Transactions</h2><table class="mini"><thead><tr><th>Date</th><th>Code</th><th>Side</th><th class="num">Price</th>
<th class="num">Lots</th><th class="num">Gross</th><th class="num">Fee</th><th class="num">Realized</th><th class="num">%</th><th>Note</th><th></th></tr></thead>
<tbody>{rows}</tbody></table></div>
{_json_script('EQUITY', stats['equity'])}
<script>
document.addEventListener('DOMContentLoaded', () => {{
  if (!window.EQUITY.length) return;
  const chart = LightweightCharts.createChart(document.getElementById('equity'), {{autoSize: true,
    layout: {{background: {{type: 'solid', color: 'transparent'}}, textColor: '#8b9bb4'}},
    grid: {{vertLines: {{color: 'rgba(42,58,92,.45)'}}, horzLines: {{color: 'rgba(42,58,92,.45)'}}}}}});
  const series = chart.addBaselineSeries({{baseValue: {{type: 'price', price: 0}}}});
  const byDay = {{}};
  window.EQUITY.forEach(p => byDay[p.date] = p.value);
  series.setData(Object.entries(byDay).map(([time, value]) => ({{time, value}})));
  chart.timeScale().fitContent();
}});
</script>"""
        return page("Journal", body)

    # ------------------------------------------------------------------ alerts page
    @app.route("/alerts", methods=["GET", "POST"])
    def alerts_page():
        message = ""
        if request.method == "POST":
            try:
                if request.form.get("delete"):
                    trade_tools.delete_alert(request.form["delete"])
                    message = "Alert deleted."
                else:
                    alert = trade_tools.add_alert(request.form.get("code", ""), request.form.get("type", ""),
                                                  float(request.form.get("value") or 0), request.form.get("note", ""))
                    message = f"Alert added: {trade_tools.describe_alert(alert)}"
            except Exception as error:
                message = f"Not saved: {error}"
        alerts = trade_tools.load_alerts()
        active = [a for a in alerts if a["active"]]
        done = sorted([a for a in alerts if not a["active"]], key=lambda a: a["triggered"] or "", reverse=True)

        def delete_button(alert):
            return (f"<form method='post' style='margin:0'><button class='link-btn' name='delete' value='{esc(alert['id'])}'>✕</button></form>")

        active_rows = "".join(
            f"<tr><td><a href='/stock/{esc(a['code'])}'><b>{esc(a['code'])}</b></a></td><td>{esc(trade_tools.describe_alert(a))}</td>"
            f"<td class='num'><b data-q='{esc(a['code'])}' data-f='price'>–</b></td><td class='num' data-q='{esc(a['code'])}' data-f='chg'></td>"
            + (f"<td class='num' data-q='{esc(a['code'])}' data-f='dist' data-level='{a['value']}'>–</td>" if a["type"] in ("above", "below") else "<td></td>")
            + f"<td class='small'>{esc(a['note'])}</td><td class='muted'>{esc(a['created'])}</td><td>{delete_button(a)}</td></tr>"
            for a in active
        ) or "<tr><td colspan='8' class='muted'>No active alerts.</td></tr>"
        done_rows = "".join(
            f"<tr><td><b>{esc(a['code'])}</b></td><td>{esc(trade_tools.describe_alert(a))}</td><td class='num'>{fmt(a.get('trigger_price'))}</td>"
            f"<td class='muted'>{esc(a['triggered'] or '')}</td><td class='small'>{esc(a['note'])}</td><td>{delete_button(a)}</td></tr>"
            for a in done[:40]
        ) or "<tr><td colspan='6' class='muted'>Nothing triggered yet.</td></tr>"
        type_options = "".join(f"<option value='{k}'>{esc(v)}</option>" for k, v in trade_tools.ALERT_TYPES.items())
        body = f"""
{f"<div class='notice'>{esc(message)}</div>" if message else ""}
<form method="post" class="filters">
  <label>Code<input name="code" size="6" required placeholder="TPIA"></label>
  <label>When<select name="type">{type_options}</select></label>
  <label>Value (price or %)<input name="value" size="9" required></label>
  <label>Note<input name="note" size="30" placeholder="e.g. breakout above resistance"></label>
  <button class="btn">Add alert</button>
  <span class="muted">Tip: on any chart click 🔔 Alert, then click a price.</span>
</form>
<div class="card"><h2>Active alerts</h2><table class="mini"><thead><tr><th>Code</th><th>Condition</th><th class="num">Price</th>
<th class="num">Today</th><th class="num">Distance</th><th>Note</th><th>Created</th><th></th></tr></thead><tbody>{active_rows}</tbody></table></div>
<div class="card"><h2>Triggered</h2><table class="mini"><thead><tr><th>Code</th><th>Condition</th><th class="num">At</th><th>When</th>
<th>Note</th><th></th></tr></thead><tbody>{done_rows}</tbody></table>
<p class="muted">The app checks alerts every 30 seconds during IDX hours while it is open (a popup + sound). It cannot check
when the app is closed. Prices are ~10 minutes delayed, so set alerts with some margin.</p></div>"""
        return page("Alerts", body)

    # ------------------------------------------------------------------ settings
    @app.route("/settings", methods=["GET", "POST"])
    def settings_page():
        message = ""
        if request.method == "POST":
            try:
                trade_tools.save_settings(request.form.to_dict())
                message = "Settings saved."
            except Exception as error:
                message = f"Not saved: {error}"
        s = trade_tools.load_settings()
        body = f"""
{f"<div class='notice'>{esc(message)}</div>" if message else ""}
<form method="post" class="card" style="max-width:720px">
  <h2>Trading settings</h2>
  <div class="filters" style="border:0;background:none;box-shadow:none;padding:0">
    <label>Buy fee %<input name="fee_buy_pct" size="6" value="{s['fee_buy_pct']:g}"></label>
    <label>Sell fee % (incl. 0.1% tax)<input name="fee_sell_pct" size="6" value="{s['fee_sell_pct']:g}"></label>
    <label>Trading capital (Rp)<input name="capital" size="14" value="{s['capital']:.0f}"></label>
    <label>Risk per trade (% of capital)<input name="risk_pct" size="5" value="{s['risk_pct']:g}"></label>
  </div>
  <button class="btn">Save</button>
  <p class="muted">Fees differ per broker - check yours (Stockbit, Ajaib, IPOT, Mirae…). Capital and risk % drive the position sizer
  on each stock page: lots = (capital × risk %) ÷ ((entry − stop) × 100). A common rule is risking 0.5–2% per trade.</p>
</form>"""
        return page("Settings", body)

    # ------------------------------------------------------------------ morning / closing reports
    report_jobs = {}

    @app.route("/reports")
    def reports_page():
        import daily_reports
        kind = request.args.get("kind") or ("morning" if datetime.now().hour < 12 else "close")
        info = {}
        for name in ("morning", "close"):
            path = daily_reports.REPORT_DIR / f"latest_{name}.json"
            if path.exists():
                info[name] = json.loads(path.read_text(encoding="utf-8"))
        running = report_jobs.get(kind) == "running"
        tabs = "".join(
            f"<a class='{'on' if name == kind else ''}' href='/reports?kind={name}'>{label}"
            f"<small>{esc(info.get(name, {}).get('generated', 'not built yet'))}</small></a>"
            for name, label in (("morning", "☀ Morning brief"), ("close", "🌙 Closing report"))
        )
        body = f"""
<div class="report-bar">
  <div class="report-tabs">{tabs}</div>
  <form method="post" action="/reports/build/{kind}" style="margin:0">
    <button class="btn" {'disabled' if running else ''}>{'Building… (about 30 s)' if running else '⟳ Build ' + ('morning brief' if kind == 'morning' else 'closing report') + ' now'}</button></form>
  <a class="chip" href="/reports/file/{kind}" target="_blank">Open in browser</a>
</div>
<p class="muted">Built automatically at 08:00 (morning) and 16:20 / 18:00 (close) on trading days for your holdings, queue plans
and the market. A popup shows the key points; the full report is here.</p>
<iframe class="report-frame" src="/reports/file/{kind}?t={int(time.time())}"></iframe>
{'<script>setTimeout(() => location.reload(), 8000)</script>' if running else ''}"""
        return page("Reports", body)

    @app.route("/reports/file/<kind>")
    def report_file(kind):
        import daily_reports
        if kind not in ("morning", "close"):
            return ("", 404)
        path = daily_reports.REPORT_DIR / f"latest_{kind}.html"
        if not path.exists():
            return "<body style='background:#0a1020;color:#8b9bb4;font-family:Segoe UI;padding:30px'>Not built yet - press Build.</body>"
        return path.read_text(encoding="utf-8")

    @app.route("/reports/build/<kind>", methods=["POST"])
    def report_build(kind):
        import threading
        import daily_reports
        if kind in ("morning", "close") and report_jobs.get(kind) != "running":
            report_jobs[kind] = "running"

            def work():
                try:
                    daily_reports.build_morning() if kind == "morning" else daily_reports.build_close()
                finally:
                    report_jobs[kind] = "done"

            threading.Thread(target=work, daemon=True).start()
        return redirect(f"/reports?kind={kind}")

    # fired alerts feed for toasts
    return lambda since=0: [e for e in trade_tools.fired_events if e["time"] > time.time() - 3600]

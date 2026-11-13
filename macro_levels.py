"""
Macro levels for the after-close report and deck: Gold (XAU), Brent crude and the US Dollar Index.

For each asset: last price, 1D / 5D / 1M change, trend vs MA50 / MA200, RSI, nearest
support / resistance, and a plain-language read of what it means for IHSG.
Prices are live futures/index quotes at report time (they keep trading after the IDX close).

Run manually:  python macro_levels.py
"""

import base64
import html
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import big_banks
import bmri_daily_check as core

FOLDER = core.FOLDER
SUMMARY_FILE = FOLDER / "macro_summary.json"
CHART_FILE = FOLDER / "macro_chart.png"

ASSETS = [
    {"code": "XAU", "symbol": "GC=F", "name": "Gold (XAU)", "unit": "USD/oz", "decimals": 0, "color": "#C9A227"},
    {"code": "BRENT", "symbol": "BZ=F", "name": "Brent crude", "unit": "USD/bbl", "decimals": 2, "color": "#1F2A44"},
    {"code": "DXY", "symbol": "DX-Y.NYB", "name": "US Dollar Index", "unit": "index", "decimals": 2, "color": "#2F6FD6"},
]


def fmt(value, decimals):
    return f"{value:,.{decimals}f}"


def ihsg_impact(code: str, close: float, change_5d: float, above_ma50: bool) -> tuple:
    """Return (score for IHSG: +1 supportive / 0 neutral / -1 headwind, explanation)."""
    if code == "BRENT":
        if close >= 100 and change_5d > 0:
            return -1, "Oil > $100 and rising: bigger import/subsidy bill, rupiah pressure; energy stocks benefit", "Headwind: subsidy bill, rupiah (energy stocks gain)"
        if close >= 100:
            return -1, "Oil still > $100: headwind for rupiah and inflation; easing would be relief", "Headwind: rupiah, inflation"
        if change_5d < -3:
            return 1, "Oil falling: relief for rupiah, inflation and BI's room to cut", "Supportive: rupiah, inflation relief"
        return 0, "Oil below $100: manageable for Indonesia's balance of payments", "Neutral"
    if code == "DXY":
        if change_5d > 0.5 or (above_ma50 and change_5d > 0):
            return -1, "Stronger dollar: pressure on rupiah and foreign outflows from IHSG", "Headwind: rupiah, foreign outflows"
        if change_5d < -0.5:
            return 1, "Weaker dollar: supportive for rupiah and foreign inflows", "Supportive: rupiah, foreign inflows"
        return 0, "Dollar steady: neutral for flows", "Neutral"
    if code == "XAU":
        if change_5d > 1:
            return 0, "Gold rising: positive for gold miners (ANTM, MDKA, BRMS); often a risk-off signal", "Gold miners up; risk-off hint"
        if change_5d < -1:
            return 0, "Gold falling: negative for gold miners (ANTM, MDKA, BRMS); limited index impact", "Gold miners weaker; small index impact"
        return 0, "Gold flat: little impact on IHSG", "Neutral"
    return 0, "", ""


def analyse(asset: dict) -> dict:
    prices = core.add_indicators(big_banks.fetch_daily(asset["symbol"]))
    last = prices.iloc[-1]
    close = float(last["Close"])
    decimals = asset["decimals"]

    def change(sessions):
        return (close / float(prices["Close"].iloc[-1 - sessions]) - 1) * 100

    levels = {
        "20-day high": float(prices["High"].iloc[-20:].max()),
        "20-day low": float(prices["Low"].iloc[-20:].min()),
        "60-day high": float(prices["High"].iloc[-60:].max()),
        "60-day low": float(prices["Low"].iloc[-60:].min()),
        "52-week high": float(prices["High"].iloc[-250:].max()),
        "52-week low": float(prices["Low"].iloc[-250:].min()),
        "MA50": float(last["MA50"]),
        "MA200": float(last["MA200"]),
    }
    def distinct(candidates):
        picked = []
        for value, name in candidates:
            if picked and abs(value / picked[-1][0] - 1) < 0.003:
                picked[-1] = (picked[-1][0], picked[-1][1] + " / " + name)
                continue
            picked.append((value, name))
        return picked[:2]

    supports = distinct(sorted([(v, n) for n, v in levels.items() if v < close * 0.998], reverse=True))
    resistances = distinct(sorted([(v, n) for n, v in levels.items() if v > close * 1.002]))

    above_ma50 = close > float(last["MA50"])
    above_ma200 = close > float(last["MA200"])
    if above_ma50 and above_ma200:
        trend = "Uptrend"
    elif not above_ma50 and not above_ma200:
        trend = "Downtrend"
    else:
        trend = "Mixed"
    change_5d = change(5)
    impact_score, impact, impact_short = ihsg_impact(asset["code"], close, change_5d, above_ma50)

    return {
        **{key: asset[key] for key in ("code", "symbol", "name", "unit", "decimals", "color")},
        "date": prices.index[-1].strftime("%Y-%m-%d"),
        "close": close,
        "change_1d": change(1),
        "change_5d": change_5d,
        "change_1m": change(21),
        "ma50": float(last["MA50"]),
        "ma200": float(last["MA200"]),
        "rsi": float(last["RSI14"]),
        "trend": trend,
        "supports": [[round(v, decimals), n] for v, n in supports],
        "resistances": [[round(v, decimals), n] for v, n in resistances],
        "impact_score": impact_score,
        "impact": impact,
        "impact_short": impact_short,
        "_prices": prices,
    }


def draw_chart(results: list, sessions: int = 120) -> None:
    fig, axes = plt.subplots(1, len(results), figsize=(12, 3.0))
    for ax, result in zip(axes, results):
        view = result["_prices"].iloc[-sessions:]
        ax.plot(view.index, view["Close"], color=result["color"], linewidth=1.6)
        ax.plot(view.index, view["MA50"], color="royalblue", linewidth=0.8, alpha=0.8)
        ax.plot(view.index, view["MA200"], color="purple", linewidth=0.8, alpha=0.8)
        for value, _ in result["supports"][:1]:
            ax.axhline(value, color="#0E9F6E", linestyle="--", linewidth=0.8)
        for value, _ in result["resistances"][:1]:
            ax.axhline(value, color="#D64545", linestyle="--", linewidth=0.8)
        ax.set_title(
            f"{result['name']}  {fmt(result['close'], result['decimals'])}  ({result['change_1d']:+.1f}%)",
            fontsize=9,
        )
        ax.tick_params(labelsize=7)
        ax.xaxis.set_major_locator(plt.MaxNLocator(4))
        ax.grid(alpha=0.25)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.text(0.5, 0.01, "MA50 blue · MA200 purple · dashed: nearest support (green) / resistance (red)",
             ha="center", fontsize=7, color="#666666")
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(CHART_FILE, dpi=130)
    plt.close(fig)


def overall_read(results: list) -> str:
    score = sum(r["impact_score"] for r in results)
    if score <= -2:
        return "Macro headwind for IHSG (strong dollar and/or high oil)"
    if score == -1:
        return "Mild macro headwind for IHSG"
    if score >= 1:
        return "Macro supportive for IHSG"
    return "Macro neutral for IHSG"


def run() -> dict:
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(analyse, ASSETS))
    draw_chart(results)
    summary = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "overall": overall_read(results),
        "assets": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results],
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load_summary() -> dict:
    if SUMMARY_FILE.exists():
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    return {}


def level_text(levels: list, decimals: int) -> str:
    return " · ".join(f"{fmt(value, decimals)} ({name})" for value, name in levels) or "–"


def section_html(summary: dict) -> str:
    if not summary:
        return "<p>Macro data unavailable.</p>"
    rows = []
    for asset in summary["assets"]:
        d = asset["decimals"]
        rows.append(
            "<tr>"
            f"<td><b>{html.escape(asset['name'])}</b><br><span class='muted'>{asset['unit']}</span></td>"
            f"<td>{fmt(asset['close'], d)}</td>"
            f"<td class='{core.signed_class(asset['change_1d'])}'>{asset['change_1d']:+.2f}%</td>"
            f"<td class='{core.signed_class(asset['change_5d'])}'>{asset['change_5d']:+.2f}%</td>"
            f"<td class='{core.signed_class(asset['change_1m'])}'>{asset['change_1m']:+.2f}%</td>"
            f"<td>{asset['trend']}<br><span class='muted'>RSI {asset['rsi']:.0f}</span></td>"
            f"<td>{html.escape(level_text(asset['supports'], d))}</td>"
            f"<td>{html.escape(level_text(asset['resistances'], d))}</td>"
            f"<td>{html.escape(asset['impact'])}</td>"
            "</tr>"
        )
    chart = ""
    if CHART_FILE.exists():
        encoded = base64.b64encode(CHART_FILE.read_bytes()).decode()
        chart = f"<img src='data:image/png;base64,{encoded}' alt='macro chart' style='margin-top:10px'>"
    return (
        f"<p class='big'>{html.escape(summary['overall'])}</p>"
        "<table><tr><th>Asset</th><th>Last</th><th>1D</th><th>5D</th><th>1M</th><th>Trend</th>"
        "<th>Support</th><th>Resistance</th><th>IHSG impact</th></tr>"
        + "".join(rows)
        + f"</table><p class='muted'>Live futures/index quotes at {summary['generated']} WIB.</p>"
        + chart
    )


if __name__ == "__main__":
    import sys

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    result = run()
    print(result["overall"])
    for asset in result["assets"]:
        d = asset["decimals"]
        print(
            f"{asset['name']:16s} {fmt(asset['close'], d):>9s}  1D {asset['change_1d']:+.2f}%  "
            f"5D {asset['change_5d']:+.2f}%  {asset['trend']:9s} "
            f"S: {level_text(asset['supports'], d)} | R: {level_text(asset['resistances'], d)}"
        )
        print(f"{'':16s} IHSG: {asset['impact']}")

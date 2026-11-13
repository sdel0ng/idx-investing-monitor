"""
IHSG (Jakarta Composite) — daily technical read and rebound-vs-retrace lean.

Uses the same post-shock framework as the banks (15 Oct pre-shock close, post-shock low,
rebound high, 38.2–50% pullback zone), market-wide net foreign flow you enter in the 18:00
broksum form, and the Big 4 banks' direction (they are the heaviest index weights).

Run manually:  python ihsg_analysis.py
"""

import base64
import html
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

import big_banks
import bmri_daily_check as core

SYMBOL = "^JKSE"
FOLDER = core.FOLDER
SUMMARY_FILE = FOLDER / "ihsg_summary.json"
CHART_FILE = FOLDER / "ihsg_chart.png"
CALL_STATE_FILE = FOLDER / "ihsg_call_state.json"


def load_flow() -> pd.DataFrame:
    if core.IHSG_FLOW_FILE.exists():
        return pd.read_csv(core.IHSG_FLOW_FILE, parse_dates=["Date"]).sort_values("Date")
    return pd.DataFrame(columns=["Date", "NetForeignBn"])


def build_plan(prices: pd.DataFrame) -> dict:
    """Post-shock structure from data up to the previous session (index points, no ticks)."""
    history = prices.iloc[:-1]
    after_shock = history.loc[big_banks.SHOCK_DATE:]
    pre_shock = history.loc[:big_banks.PRE_SHOCK_DATE]
    if len(after_shock) < 2 or pre_shock.empty:
        return {"available": False}
    low_date = after_shock["Low"].idxmin()
    shock_low = float(after_shock["Low"].min())
    rebound_high = float(history.loc[low_date:]["High"].max())
    swing = rebound_high - shock_low
    zone_top = rebound_high - big_banks.PULLBACK_ZONE[0] * swing
    zone_bottom = rebound_high - big_banks.PULLBACK_ZONE[1] * swing
    return {
        "available": True,
        "shock_low": int(round(shock_low)),
        "rebound_high": int(round(rebound_high)),
        "pre_shock_close": int(round(float(pre_shock["Close"].iloc[-1]))),
        "pullback": {"zone": [int(round(zone_bottom)), int(round(zone_top))]},
    }


def draw_chart(prices: pd.DataFrame, plan: dict, sessions: int = 120) -> None:
    view = prices.iloc[-sessions:]
    fig, (ax_price, ax_rsi) = plt.subplots(
        2, 1, figsize=(8, 5.3), sharex=True, gridspec_kw={"height_ratios": [3.2, 1]}
    )
    ax_price.plot(view.index, view["Close"], color="#1F2A44", linewidth=1.8, label="IHSG")
    for column, color in (("MA20", "orange"), ("MA50", "royalblue"), ("MA200", "purple")):
        ax_price.plot(view.index, view[column], color=color, linewidth=0.9, label=column)
    if plan.get("available"):
        zone = plan["pullback"]["zone"]
        ax_price.axhspan(zone[0], zone[1], color="#0E9F6E", alpha=0.12, label="Pullback zone")
        for level, label, color in (
            (plan["pre_shock_close"], "Pre-shock close", "#D64545"),
            (plan["rebound_high"], "Rebound high", "#E09F1F"),
            (plan["shock_low"], "Shock low", "#52606D"),
        ):
            ax_price.axhline(level, color=color, linestyle="--", linewidth=0.9)
            ax_price.text(view.index[1], level, f" {label} {level:,}", fontsize=7, color=color, va="bottom")
    ax_price.axvline(big_banks.SHOCK_DATE, color="#999999", linewidth=0.8, linestyle=":")
    ax_price.set_title(f"IHSG {view['Close'].iloc[-1]:,.0f} · {view.index[-1]:%d %b %Y}", fontsize=10)
    ax_price.legend(loc="upper right", fontsize=7, frameon=False)
    ax_price.grid(alpha=0.25)
    ax_rsi.plot(view.index, view["RSI14"], color="teal", linewidth=1)
    for level, color in ((70, "red"), (50, "gray"), (30, "green")):
        ax_rsi.axhline(level, color=color, linewidth=0.6)
    ax_rsi.set_ylabel("RSI14", fontsize=8)
    ax_rsi.tick_params(labelsize=7)
    ax_price.tick_params(labelsize=7)
    for axis in (ax_price, ax_rsi):
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(CHART_FILE, dpi=130)
    plt.close(fig)


def run(bank_results: list = None) -> dict:
    prices = big_banks.fetch_daily(SYMBOL)
    volume_pending = bool(prices["Volume"].iloc[-1] == 0)
    if volume_pending:
        # Intraday Yahoo bars often report 0 volume for the index: treat as neutral
        prices.iloc[-1, prices.columns.get_loc("Volume")] = prices["Volume"].iloc[-21:-1].mean()
    prices = core.add_indicators(prices)
    ta = core.technical_analysis(prices)
    plan = build_plan(prices)
    last = prices.iloc[-1]
    close = float(last["Close"])

    # Market-wide foreign flow (entered in the 18:00 form)
    flow = load_flow()
    today_flow = None
    flow_5d = None
    if len(flow):
        if flow["Date"].iloc[-1] == prices.index[-1]:
            today_flow = float(flow["NetForeignBn"].iloc[-1])
        flow_5d = float(flow.tail(5)["NetForeignBn"].sum())

    extra = []
    if today_flow is not None:
        extra.append((1 if today_flow > 0 else -1,
                      f"market foreign net {'buy' if today_flow > 0 else 'sell'} Rp{abs(today_flow):,.0f}bn"))
    if bank_results:
        sector = big_banks.sector_outlook(bank_results)
        if sector.get("score", 0) >= 0.5:
            extra.append((1, "Big 4 banks leaning rebound (heaviest index weights)"))
        elif sector.get("score", 0) <= -0.5:
            extra.append((-1, "Big 4 banks leaning retrace (heaviest index weights)"))
    outlook = big_banks.direction_outlook("IHSG", prices, plan, ihsg_5d=0.0, extra_factors=extra)
    if volume_pending:
        outlook["reasons"].append("today's volume not final yet")

    levels = {
        "MA20": float(last["MA20"]),
        "MA50": float(last["MA50"]),
        "MA200": float(last["MA200"]),
        "20-day high": float(prices["High"].iloc[-20:].max()),
        "20-day low": float(prices["Low"].iloc[-20:].min()),
    }
    if plan.get("available"):
        levels["Pre-shock close"] = plan["pre_shock_close"]
        levels["Rebound high"] = plan["rebound_high"]
        levels["Shock low"] = plan["shock_low"]
        levels["Pullback zone top"] = plan["pullback"]["zone"][1]
        levels["Pullback zone bottom"] = plan["pullback"]["zone"][0]
    supports = sorted([(round(v), n) for n, v in levels.items() if v < close], reverse=True)[:3]
    resistances = sorted([(round(v), n) for n, v in levels.items() if v > close])[:3]

    draw_chart(prices, plan)
    summary = {
        "date": prices.index[-1].strftime("%Y-%m-%d"),
        "close": close,
        "previous_close": float(prices["Close"].iloc[-2]),
        "change_1d": big_banks.pct(close, float(prices["Close"].iloc[-2])),
        "change_5d": big_banks.pct(close, float(prices["Close"].iloc[-6])),
        "vs_pre_shock": big_banks.pct(close, plan.get("pre_shock_close")),
        "from_low": big_banks.pct(close, plan.get("shock_low")),
        "rsi": float(last["RSI14"]),
        "bias": ta["bias"],
        "score": ta["score"],
        "trend": ta["trend"],
        "momentum": ta["momentum"],
        "candle": ta["candle"],
        "supports": supports,
        "resistances": resistances,
        "plan": plan,
        "outlook": outlook,
        "net_foreign_today": today_flow,
        "net_foreign_5d": flow_5d,
        "volume_pending": volume_pending,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load_summary() -> dict:
    if SUMMARY_FILE.exists():
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    return {}


# ---------------------------------------------------------------- alerts
def _slug(label: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_")


def call_summary_text(summary: dict) -> str:
    outlook = summary["outlook"]
    plan = summary.get("plan") or {}
    flow = summary.get("net_foreign_today")
    lines = [
        f"IHSG {summary['close']:,.0f} ({big_banks.fmt_pct(summary['change_1d'])} today, "
        f"{big_banks.fmt_pct(summary['vs_pre_shock'])} vs 15 Oct)",
        f"Call: {outlook['label']} (score {outlook['score']:+d}, {outlook['confidence']} confidence)",
        f"Expect: {outlook['expectation']}",
        "Why: " + ("; ".join(outlook["reasons"]) or "mixed signals"),
        "Market foreign today: " + ("not entered" if flow is None else f"Rp{flow:+,.0f}bn"),
    ]
    if plan.get("available"):
        zone = plan["pullback"]["zone"]
        lines.append(
            f"Levels: rebound confirmed above {plan['rebound_high']:,} · pullback zone {zone[0]:,}–{zone[1]:,} · "
            f"shock low {plan['shock_low']:,} · gap fill {plan['pre_shock_close']:,}"
        )
    return "\n".join(lines)


def ihsg_alerts(summary: dict, update_state: bool = True) -> dict:
    """One-shot alerts: the rebound/retrace call changed vs the previous session, or a key level
    was crossed on today's close (previous close taken from the price data)."""
    alerts = {}
    if not summary or "outlook" not in summary:
        return alerts
    outlook = summary["outlook"]
    state = {}
    if CALL_STATE_FILE.exists():
        state = json.loads(CALL_STATE_FILE.read_text(encoding="utf-8"))

    # The call from the last run of the PREVIOUS trading day is the reference
    if state.get("date") == summary["date"]:
        reference_label = state.get("previous_day_label")
    else:
        reference_label = state.get("label")
    if reference_label and reference_label != outlook["label"]:
        alerts[f"ihsg_call_{_slug(outlook['label'])}"] = (
            f"IHSG CALL CHANGED: {reference_label} → {outlook['label']} (score {outlook['score']:+d}).\n"
            + call_summary_text(summary)
        )

    plan = summary.get("plan") or {}
    previous_close = summary.get("previous_close")
    close = summary["close"]
    if plan.get("available") and previous_close is not None:
        zone_bottom = plan["pullback"]["zone"][0]
        crossings = [
            ("ihsg_above_rebound_high", previous_close <= plan["rebound_high"] < close,
             f"IHSG closed {close:,.0f} ABOVE the rebound high {plan['rebound_high']:,}: rebound confirmed."),
            ("ihsg_gap_filled", previous_close <= plan["pre_shock_close"] < close,
             f"IHSG closed {close:,.0f} above the pre-shock close {plan['pre_shock_close']:,}: MSCI-shock gap filled."),
            ("ihsg_lost_pullback_zone", previous_close >= zone_bottom > close,
             f"IHSG closed {close:,.0f} BELOW the pullback zone {zone_bottom:,}: deeper retrace risk toward {plan['shock_low']:,}."),
            ("ihsg_below_shock_low", previous_close >= plan["shock_low"] > close,
             f"IHSG closed {close:,.0f} BELOW the post-shock low {plan['shock_low']:,}: rebound failed."),
        ]
        for key, crossed, message in crossings:
            if crossed:
                alerts[key] = message + "\n" + call_summary_text(summary)

    if update_state:
        if state.get("date") == summary["date"]:
            previous_day_label = state.get("previous_day_label")
        else:
            previous_day_label = state.get("label")
        CALL_STATE_FILE.write_text(
            json.dumps(
                {
                    "date": summary["date"],
                    "label": outlook["label"],
                    "score": outlook["score"],
                    "previous_day_label": previous_day_label,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    return alerts


def section_html(summary: dict) -> str:
    """HTML block for the close report and morning brief."""
    if not summary:
        return "<p>IHSG data unavailable.</p>"
    outlook = summary["outlook"]
    fmt = big_banks.fmt_pct
    foreign_today = summary["net_foreign_today"]
    foreign_5d = summary["net_foreign_5d"]
    foreign_today_text = "not entered" if foreign_today is None else f"Rp{foreign_today:+,.0f}bn"
    foreign_5d_text = "–" if foreign_5d is None else f"Rp{foreign_5d:+,.0f}bn"
    levels = "".join(
        f"<tr><td>Resistance</td><td>{value:,}</td><td>{html.escape(name)}</td></tr>"
        for value, name in reversed(summary["resistances"])
    ) + "".join(
        f"<tr><td>Support</td><td>{value:,}</td><td>{html.escape(name)}</td></tr>"
        for value, name in summary["supports"]
    )
    chart = ""
    if CHART_FILE.exists():
        encoded = base64.b64encode(CHART_FILE.read_bytes()).decode()
        chart = f"<img src='data:image/png;base64,{encoded}' alt='IHSG chart' style='margin-top:10px'>"
    return (
        f"<p class='big'>IHSG: {html.escape(outlook['label'])} "
        f"<span class='muted'>(score {outlook['score']:+d}, {outlook['confidence']} confidence)</span></p>"
        f"<p>{html.escape(outlook['expectation'])}</p>"
        + core.bullet_list(outlook["reasons"] or ["mixed signals"])
        + "<table><tr><th>Close</th><th>1D</th><th>5D</th><th>vs 15 Oct</th><th>From shock low</th>"
        "<th>RSI</th><th>Technical bias</th><th>Foreign today</th><th>Foreign (last 5 entries)</th></tr><tr>"
        f"<td>{summary['close']:,.0f}</td>"
        f"<td class='{core.signed_class(summary['change_1d'])}'>{fmt(summary['change_1d'])}</td>"
        f"<td class='{core.signed_class(summary['change_5d'])}'>{fmt(summary['change_5d'])}</td>"
        f"<td class='{core.signed_class(summary['vs_pre_shock'])}'>{fmt(summary['vs_pre_shock'])}</td>"
        f"<td class='{core.signed_class(summary['from_low'])}'>{fmt(summary['from_low'])}</td>"
        f"<td>{summary['rsi']:.0f}</td><td>{html.escape(summary['bias'])}</td>"
        f"<td class='{core.signed_class(foreign_today)}'>{foreign_today_text}</td>"
        f"<td class='{core.signed_class(foreign_5d)}'>{foreign_5d_text}</td></tr></table>"
        + "<p><b>Key levels</b></p><table><tr><th></th><th>Level</th><th>What</th></tr>" + levels + "</table>"
        + core.bullet_list([f"Trend: {summary['trend']}", f"Momentum: {summary['momentum']}",
                            f"Candle: {summary['candle']}"])
        + chart
    )


if __name__ == "__main__":
    import sys

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    result = run(big_banks.run())
    print(f"IHSG {result['close']:,.0f} ({big_banks.fmt_pct(result['change_1d'])}): "
          f"{result['outlook']['label']} ({result['outlook']['score']:+d}) - {result['outlook']['expectation']}")
    for reason in result["outlook"]["reasons"]:
        print("  -", reason)
    print("Plan:", result["plan"])

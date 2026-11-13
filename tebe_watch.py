"""
TEBE (PT Dana Brata Luhur Tbk) - speculative watch for the after-close report and deck.

Fixed alert levels from the 28 Oct 2026 review:
  1,620  post-shock low (support)      - a close below = breakdown
  2,000  first resistance               - a close above = first recovery sign
  2,330  gap / pre-shock close (15 Oct) - a close above = gap filled
Plus a "held support" alert: dips to ~1,620 and closes above it with a reversal candle on volume.

Run manually:  python tebe_watch.py
"""

import base64
import html
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import big_banks
import bmri_daily_check as core

SYMBOL = "TEBE.JK"
SUPPORT = 1620
RESISTANCE = 2000
GAP = 2330
DEEP_SUPPORT = 1060          # where the Aug-Oct run started
# Your TEBE position comes from positions.json (edit it in the dashboard); None = not held
AVERAGE_PRICE = core.load_positions().get("TEBE", {}).get("average_price")
BREAKEVEN_PRICE = core.breakeven_for(AVERAGE_PRICE) if AVERAGE_PRICE else None
PARTIAL_SELL_ZONE = (2000, 2110)
FOLDER = core.FOLDER
SUMMARY_FILE = FOLDER / "tebe_summary.json"
CHART_FILE = FOLDER / "tebe_chart.png"


def verdict_for(close: float, ma200: float) -> tuple:
    holding = AVERAGE_PRICE is not None
    if close < SUPPORT:
        if holding:
            return "EXIT per plan: closed below 1,620", (
                f"Cut the position; next supports ~{ma200:,.0f} (MA200), then {DEEP_SUPPORT:,}. Don't average down."
            )
        return "AVOID: broke the 1,620 post-shock low", (
            f"Next supports ~{ma200:,.0f} (MA200), then {DEEP_SUPPORT:,}. Don't catch the knife."
        )
    if close < RESISTANCE:
        if holding:
            return "HOLD with stop: testing support", (
                f"Exit on a daily close below {SUPPORT:,}. Sell part at {PARTIAL_SELL_ZONE[0]:,}–{PARTIAL_SELL_ZONE[1]:,}. "
                "No averaging down."
            )
        return "WAIT: testing support, below resistance", (
            f"Only consider a small entry if 1,620 holds with a strong reversal day on volume, "
            f"or after a close above {RESISTANCE:,}."
        )
    if close < GAP:
        if holding:
            return "REDUCE into strength: 2,000–2,330 resistance", (
                f"Sell part here; hold the rest only on a close above {GAP:,}. Breakeven ~{BREAKEVEN_PRICE:,}."
            )
        return "Recovery attempt: above 2,000", f"Needs a close above {GAP:,} (gap) to repair the trend."
    if holding:
        return "Gap filled: hold the rest, trail the stop", f"Trail the stop under {RESISTANCE:,}; position back in profit."
    return "Gap filled: trend repair", "Momentum back; still size small given volatility."


def run() -> dict:
    prices = core.add_indicators(big_banks.fetch_daily(SYMBOL))
    ta = core.technical_analysis(prices)
    last = prices.iloc[-1]
    previous = prices.iloc[-2]
    close = float(last["Close"])
    previous_close = float(previous["Close"])
    ma200 = float(last["MA200"])
    atr = float(last["ATR14"])
    verdict, guidance = verdict_for(close, ma200)

    candle = core.detect_candle(prices)
    reversal = candle.startswith(("Hammer", "Bullish engulfing")) or (
        close > last["Open"] and close >= last["Low"] + 0.6 * (last["High"] - last["Low"])
    )
    heavy_volume = last["Volume"] > last["Vol20"]

    alerts = {}
    if previous_close >= SUPPORT > close:
        position_note = (
            f" You hold at {AVERAGE_PRICE:,}: EXIT per plan ({(close / AVERAGE_PRICE - 1) * 100:+.1f}%)."
            if AVERAGE_PRICE else " Avoid."
        )
        alerts["tebe_broke_support"] = (
            f"TEBE closed {close:,.0f} BELOW the 1,620 post-shock low: breakdown. "
            f"Next ~{ma200:,.0f} (MA200), then {DEEP_SUPPORT:,}." + position_note
        )
    if AVERAGE_PRICE and last["High"] >= PARTIAL_SELL_ZONE[0] and previous["High"] < PARTIAL_SELL_ZONE[0]:
        alerts["tebe_partial_sell_zone"] = (
            f"TEBE reached {PARTIAL_SELL_ZONE[0]:,} (high {last['High']:,.0f}): partial-sell zone "
            f"{PARTIAL_SELL_ZONE[0]:,}–{PARTIAL_SELL_ZONE[1]:,} for your {AVERAGE_PRICE:,} position. "
            f"Hold the rest only on a close above {GAP:,}."
        )
    if previous_close <= RESISTANCE < close:
        alerts["tebe_above_2000"] = (
            f"TEBE closed {close:,.0f} ABOVE 2,000: first recovery sign. Next resistance {GAP:,} (gap)."
        )
    if previous_close <= GAP < close:
        alerts["tebe_gap_filled"] = f"TEBE closed {close:,.0f} ABOVE {GAP:,}: 15 Oct gap filled."
    if last["Low"] <= SUPPORT * 1.025 and close >= SUPPORT and reversal and heavy_volume:
        alerts["tebe_held_support"] = (
            f"TEBE held 1,620 (low {last['Low']:,.0f}, close {close:,.0f}) with a reversal on "
            f"{last['Volume'] / last['Vol20']:.1f}x volume: speculative entry watch, SMALL size, "
            f"stop below ~1,540."
        )

    view = prices.iloc[-120:]
    fig, (ax_price, ax_volume) = plt.subplots(
        2, 1, figsize=(8, 5.3), sharex=True, gridspec_kw={"height_ratios": [3, 1]}
    )
    colors = np.where(view["Close"] >= view["Open"], "#26a69a", "#ef5350")
    ax_price.vlines(view.index, view["Low"], view["High"], color=colors, linewidth=0.8)
    ax_price.bar(
        view.index,
        (view["Close"] - view["Open"]).abs().clip(lower=10),
        bottom=view[["Open", "Close"]].min(axis=1),
        color=colors,
        width=0.7,
    )
    for column, color in (("MA20", "orange"), ("MA50", "royalblue"), ("MA200", "purple")):
        ax_price.plot(view.index, view[column], color=color, linewidth=1.0, label=column)
    for level, label, color in (
        (GAP, f"Gap {GAP:,}", "#D64545"),
        (RESISTANCE, f"Resistance {RESISTANCE:,}", "#D64545"),
        (SUPPORT, f"Support {SUPPORT:,}", "#0E9F6E"),
    ):
        ax_price.axhline(level, color=color, linestyle="--", linewidth=0.9)
        ax_price.text(view.index[1], level + 25, label, fontsize=7, color=color)
    ax_price.set_title(f"TEBE {close:,.0f} ({(close / previous_close - 1) * 100:+.1f}%) · {prices.index[-1]:%d %b %Y}",
                       fontsize=10)
    ax_price.legend(loc="upper left", fontsize=7, frameon=False)
    ax_price.grid(alpha=0.25)
    ax_volume.bar(view.index, view["Volume"] / 1e6, color=colors, width=0.7)
    ax_volume.set_ylabel("Vol (M)", fontsize=8)
    for axis in (ax_price, ax_volume):
        axis.tick_params(labelsize=7)
        for side in ("top", "right"):
            axis.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(CHART_FILE, dpi=130)
    plt.close(fig)

    summary = {
        "date": prices.index[-1].strftime("%Y-%m-%d"),
        "close": close,
        "change_1d": (close / previous_close - 1) * 100,
        "change_5d": (close / float(prices["Close"].iloc[-6]) - 1) * 100,
        "rsi": float(last["RSI14"]),
        "atr": atr,
        "atr_pct": atr / close * 100,
        "ma20": float(last["MA20"]),
        "ma50": float(last["MA50"]),
        "ma200": ma200,
        "bias": ta["bias"],
        "trend": ta["trend"],
        "candle": candle,
        "verdict": verdict,
        "guidance": guidance,
        "average_price": AVERAGE_PRICE,
        "breakeven": BREAKEVEN_PRICE,
        "pnl_pct": None if AVERAGE_PRICE is None else (close / AVERAGE_PRICE - 1) * 100,
        "levels": {"support": SUPPORT, "resistance": RESISTANCE, "gap": GAP, "deep_support": DEEP_SUPPORT},
        "alerts": alerts,
    }
    SUMMARY_FILE.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def load_summary() -> dict:
    if SUMMARY_FILE.exists():
        return json.loads(SUMMARY_FILE.read_text(encoding="utf-8"))
    return {}


def section_html(summary: dict) -> str:
    if not summary:
        return "<p>TEBE data unavailable.</p>"
    chart = ""
    if CHART_FILE.exists():
        encoded = base64.b64encode(CHART_FILE.read_bytes()).decode()
        chart = f"<img src='data:image/png;base64,{encoded}' alt='TEBE chart' style='margin-top:10px'>"
    return (
        f"<p class='big'>TEBE {summary['close']:,.0f} "
        f"<span class='{core.signed_class(summary['change_1d'])}'>{summary['change_1d']:+.1f}%</span> · "
        f"{html.escape(summary['verdict'])}</p>"
        f"<p>{html.escape(summary['guidance'])}</p>"
        + core.bullet_list(([
            f"YOUR POSITION: avg {summary['average_price']:,} → {summary['pnl_pct']:+.1f}% · stop: daily close < {SUPPORT:,} · "
            f"sell part {PARTIAL_SELL_ZONE[0]:,}–{PARTIAL_SELL_ZONE[1]:,} · breakeven ~{summary['breakeven']:,}"
        ] if summary.get("average_price") else []) + [
            f"Levels: support {SUPPORT:,} · resistance {RESISTANCE:,} · gap {GAP:,} · MA200 {summary['ma200']:,.0f}",
            f"Trend: {summary['trend']}",
            f"RSI {summary['rsi']:.0f} · candle: {summary['candle']}",
            f"Volatility: ATR ~Rp{summary['atr']:,.0f} ({summary['atr_pct']:.0f}% per day) - size positions small",
        ] + [f"ALERT: {message}" for message in summary["alerts"].values()])
        + "<p class='muted'>Speculative position: coal-logistics stock after a parabolic run, an IDX suspension and "
        "auto-reject days. Stops can gap on auto-reject days.</p>"
        + chart
    )


if __name__ == "__main__":
    import sys

    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    result = run()
    print(f"TEBE {result['close']:,.0f} ({result['change_1d']:+.1f}%): {result['verdict']}")
    print(" ", result["guidance"])
    print("  alerts:", list(result["alerts"]) or "none")

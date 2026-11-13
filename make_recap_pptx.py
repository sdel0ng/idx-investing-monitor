"""
Build the BMRI recap deck from the latest close data.

Called automatically at the end of every after-close report (bmri_daily_check.py),
or run manually:  python make_recap_pptx.py

Dynamic content (refreshed each run): date, current price, P/L vs average, technical read,
price ladder marker, risk/reward from the current price, latest broksum signal, verdict and
active alerts. Historical facts (MSCI shock, crash low) and the plan levels are fixed.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

import bmri_daily_check as core
import big_banks
import ihsg_analysis
import macro_levels
import tebe_watch

FOLDER = Path(__file__).resolve().parent
OUTPUT = FOLDER.parent / "BMRI_Swing_Recap.pptx"
DECK_ARCHIVE = core.REPORT_DIR / "decks"

NAVY = RGBColor(0x1F, 0x2A, 0x44)
GREEN = RGBColor(0x0E, 0x9F, 0x6E)
RED = RGBColor(0xD6, 0x45, 0x45)
AMBER = RGBColor(0xE0, 0x9F, 0x1F)
BLUE = RGBColor(0x2F, 0x6F, 0xD6)
GRAY = RGBColor(0x52, 0x60, 0x6D)
LIGHT = RGBColor(0xF4, 0xF6, 0xF8)
LINE = RGBColor(0xD9, 0xDE, 0xE4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
PALE = RGBColor(0x9F, 0xB3, 0xC8)
FONT = "Segoe UI"
TOTAL_SLIDES = 10

ALERT_LABELS = {
    "stop_close": "STOP triggered",
    "stop_tested": "stop tested intraday",
    "near_stop": "near stop",
    "breakout_confirmed": "breakout confirmed",
    "breakout_touch": "touched 3,980",
    "target_1": "T1 reached",
    "target_2": "T2 reached",
    "foreign_turned_buy": "foreign turned net buy",
    "foreign_3day_buy": "foreign 3-day buying",
    "accumulation_3day": "3-day accumulation",
    "distribution_3day": "3-day distribution",
}


# ---------------------------------------------------------------- drawing helpers
def add_text(slide, x, y, w, h, text, size=14, color=NAVY, bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.vertical_anchor = anchor
    frame.margin_left = frame.margin_right = Inches(0.05)
    frame.margin_top = frame.margin_bottom = Inches(0.02)
    lines = text if isinstance(text, list) else [text]
    for index, line in enumerate(lines):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.alignment = align
        run = paragraph.add_run()
        run.text = line
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = color
        run.font.name = font
    return box


def add_rect(slide, x, y, w, h, fill, line=None, rounded=False):
    shape_type = MSO_SHAPE.ROUNDED_RECTANGLE if rounded else MSO_SHAPE.RECTANGLE
    shape = slide.shapes.add_shape(shape_type, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(1)
    if rounded:
        shape.adjustments[0] = 0.08
    shape.shadow.inherit = False
    return shape


def add_bullets(slide, x, y, w, h, items, size=14, color=NAVY, bullet_color=GREEN, spacing=12):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    frame = box.text_frame
    frame.word_wrap = True
    frame.margin_left = frame.margin_right = Inches(0.05)
    for index, item in enumerate(items):
        paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
        paragraph.space_after = Pt(spacing)
        strong, rest = item if isinstance(item, tuple) else (None, item)
        dot = paragraph.add_run()
        dot.text = "●  "
        dot.font.size = Pt(size - 4)
        dot.font.color.rgb = bullet_color
        dot.font.name = FONT
        if strong:
            run = paragraph.add_run()
            run.text = strong + " "
            run.font.bold = True
            run.font.size = Pt(size)
            run.font.color.rgb = color
            run.font.name = FONT
        run = paragraph.add_run()
        run.text = rest
        run.font.size = Pt(size)
        run.font.color.rgb = color
        run.font.name = FONT
    return box


def kpi(slide, x, y, w, h, value, label, color=NAVY, note=None):
    add_rect(slide, x, y, w, h, LIGHT, rounded=True)
    add_rect(slide, x, y + 0.18, 0.06, h - 0.36, color)
    add_text(slide, x + 0.2, y + 0.12, w - 0.3, 0.6, value, size=26, bold=True, color=color)
    add_text(slide, x + 0.2, y + 0.72, w - 0.3, 0.35, label, size=12, bold=True)
    if note:
        add_text(slide, x + 0.2, y + 1.02, w - 0.3, h - 1.1, note, size=11, color=GRAY)


def card(slide, x, y, w, h, heading, items, accent=GREEN, size=14):
    add_rect(slide, x, y, w, h, WHITE, line=LINE, rounded=True)
    add_rect(slide, x, y, w, 0.08, accent)
    add_text(slide, x + 0.25, y + 0.2, w - 0.5, 0.45, heading, size=16, bold=True)
    add_bullets(slide, x + 0.25, y + 0.72, w - 0.5, h - 0.85, items, size=size, bullet_color=accent)


def table(slide, x, y, col_widths, rows, row_height=0.46, size=13, first_col_colors=None,
          cell_colors=None):
    shape = slide.shapes.add_table(
        len(rows), len(col_widths), Inches(x), Inches(y),
        Inches(sum(col_widths)), Inches(row_height * len(rows)),
    )
    grid = shape.table
    for col, width in enumerate(col_widths):
        grid.columns[col].width = Inches(width)
    for r, row in enumerate(rows):
        grid.rows[r].height = Inches(row_height)
        for c, value in enumerate(row):
            cell = grid.cell(r, c)
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY if r == 0 else (WHITE if r % 2 else LIGHT)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = cell.margin_right = Inches(0.12)
            cell.text_frame.word_wrap = True
            run = cell.text_frame.paragraphs[0].add_run()
            run.text = value
            run.font.name = FONT
            run.font.size = Pt(size)
            run.font.bold = r == 0 or c == 0
            if r == 0:
                run.font.color.rgb = WHITE
            elif cell_colors and (r, c) in cell_colors:
                run.font.color.rgb = cell_colors[(r, c)]
                run.font.bold = True
            elif c == 0 and first_col_colors:
                run.font.color.rgb = first_col_colors[r - 1]
            else:
                run.font.color.rgb = NAVY


# ---------------------------------------------------------------- data
def load_context() -> dict:
    """Latest close data: prefer the close summary, fall back to the price file."""
    summary = {}
    if core.SUMMARY_FILE.exists():
        summary = json.loads(core.SUMMARY_FILE.read_text(encoding="utf-8"))
    if not summary.get("ta"):
        prices = core.add_indicators(pd.read_csv(core.PRICE_FILE, index_col=0, parse_dates=True))
        last = prices.iloc[-1]
        summary.update(
            {
                "date": prices.index[-1].strftime("%Y-%m-%d"),
                "final": True,
                "close": float(last["Close"]),
                "previous_close": float(prices["Close"].iloc[-2]),
                "ta": core.technical_analysis(prices),
            }
        )
    summary.setdefault("alert_keys", [])
    summary["date_label"] = datetime.strptime(summary["date"], "%Y-%m-%d").strftime("%d %b %Y")
    return summary


def short(text: str, limit: int = 140) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- deck
def build_deck(output: Path = OUTPUT) -> Path:
    data = load_context()
    ta = data["ta"]
    close = data["close"]
    date_label = data["date_label"]
    status = "close" if data.get("final", True) else "intraday"
    pnl = (close / core.AVERAGE_PRICE - 1) * 100
    footer = f"BMRI swing plan · {date_label} {status} · education only, not financial advice"
    banks_summary = big_banks.load_summary()
    banks = banks_summary.get("banks", [])
    entry_rows = big_banks.entry_rows(banks) if banks else []
    ihsg = ihsg_analysis.load_summary()
    macro = macro_levels.load_summary()
    tebe = tebe_watch.load_summary()
    total_slides = (
        TOTAL_SLIDES + (2 if banks else 0) + (1 if entry_rows else 0) + (1 if ihsg else 0) + (1 if macro else 0)
        + (1 if tebe else 0)
    )
    slide_counter = [1]

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]

    def new_slide(title, subtitle, number=None):
        slide_counter[0] += 1
        slide = prs.slides.add_slide(blank)
        add_rect(slide, 0.6, 0.55, 0.09, 0.62, GREEN)
        add_text(slide, 0.8, 0.45, 11.5, 0.6, title, size=28, bold=True)
        if subtitle:
            add_text(slide, 0.8, 1.02, 11.5, 0.4, subtitle, size=14, color=GRAY)
        add_rect(slide, 0.6, 7.0, 12.13, 0.01, LINE)
        add_text(slide, 0.6, 7.05, 10, 0.3, footer, size=10, color=GRAY)
        add_text(slide, 11.7, 7.05, 1.03, 0.3, f"{slide_counter[0]} / {total_slides}", size=10, color=GRAY,
                 align=PP_ALIGN.RIGHT)
        return slide

    # 1. title
    slide = prs.slides.add_slide(blank)
    add_rect(slide, 0, 0, 13.333, 7.5, NAVY)
    add_rect(slide, 0.9, 2.35, 0.12, 1.55, GREEN)
    add_text(slide, 1.2, 2.2, 11, 1.0, "BMRI Swing Trade Recap", size=48, bold=True, color=WHITE)
    add_text(slide, 1.2, 3.2, 11, 0.6, "BMRI plan  ·  IHSG  ·  Big 4 banks  ·  Daily system",
             size=22, color=RGBColor(0xC8, 0xD3, 0xE0))
    add_text(slide, 1.2, 4.6, 11, 0.4,
             f"Bank Mandiri (IDX: BMRI)  |  Updated after the {date_label} {status}  |  Close {close:,.0f}",
             size=16, color=PALE)
    add_text(slide, 1.2, 6.6, 11, 0.4,
             f"Auto-generated {datetime.now():%d %b %Y %H:%M} WIB. For education and planning only. Not financial advice.",
             size=12, color=PALE)

    # 2. what happened
    slide = new_slide("What happened", "A market-wide shock, not a Bank Mandiri problem", 2)
    now_color = GREEN if close >= core.BREAKEVEN_PRICE else RED if close < core.STOP_CLOSE else BLUE
    tiles = [
        ("−14.8%", "16 Oct: MSCI shock", RED, "Auto-reject on ~8× normal volume"),
        ("3,200", "19 Oct: capitulation low", RED, "Lowest price of the year"),
        ("3,920", "Rebound high", AMBER, "Stalled at the gap / MA20"),
        (f"{close:,.0f}", f"Now ({date_label})", now_color,
         f"{pnl:+.1f}% vs your {core.AVERAGE_PRICE:,} average" if core.HOLDING_BMRI
         else f"{pnl:+.1f}% vs your former {core.AVERAGE_PRICE:,} avg (closed)"),
    ]
    for index, (value, label, color, note) in enumerate(tiles):
        kpi(slide, 0.6 + index * 3.07, 1.65, 2.87, 1.65, value, label, color, note)
    card(slide, 0.6, 3.6, 5.95, 3.15, "Cause", [
        ("MSCI consultation", "on downgrading Indonesia from Emerging to Standalone market"),
        ("Foreign selling", "~Rp868bn net sell in BMRI on 16 Oct alone"),
        ("Big banks hit together:", "BBCA, BBRI, BMRI all sold off"),
    ], accent=RED, size=15)
    card(slide, 6.78, 3.6, 5.95, 3.15, "Fundamentals", [
        ("Q3 2026 stable:", "gross NPL 1.02%, loan yields improving"),
        ("Drop is flow-driven,", "not caused by earnings"),
        ("Key risk ahead:", "the final MSCI decision"),
    ], accent=GREEN, size=15)

    # 3. technical picture
    bias_color = GREEN if "bullish" in ta["bias"].lower() else RED if "bearish" in ta["bias"].lower() else AMBER
    slide = new_slide("Technical picture", f"Bias: {ta['bias']} (score {ta['score']:+d}) · {date_label} {status}", 3)
    if core.CHART_FILE.exists():
        slide.shapes.add_picture(str(core.CHART_FILE), Inches(0.6), Inches(1.55), height=Inches(5.3))
    card(slide, 7.05, 1.55, 5.68, 5.3, "Read", [
        ("Trend:", short(ta["trend"], 120)),
        ("Momentum:", short(ta["momentum"], 120)),
        ("Volume:", short(ta["volume"], 90)),
        ("Candle:", short(ta["candle"], 90)),
        ("ATR:", f"~Rp{ta['atr']:,} per day ({ta['atr'] / close * 100:.1f}%)"),
    ], accent=bias_color, size=13)

    # 4. key levels
    slide = new_slide("Key levels", "Where price is likely to react", 4)
    ladder_top, ladder_bottom = 1.65, 6.75
    price_high = max(4500, close + 100)
    price_low = min(3150, close - 100)

    def price_to_y(price):
        return ladder_top + (price_high - price) / (price_high - price_low) * (ladder_bottom - ladder_top)

    add_rect(slide, 2.2, ladder_top, 0.08, ladder_bottom - ladder_top, LINE)
    ladder = [
        (4450, "MA200", "Long-term trend line"),
        (4275, "4,250–4,300", "September range · T3"),
        (4100, "MA50 ~4,100", "T2 target"),
        (3955, "3,930–3,980", "Gap + MA20 · T1 / breakout"),
        (3705, "3,700–3,710", "Recent low"),
        (3625, "3,600–3,650", "Stop zone (daily close)"),
        (3460, "3,460", "20 Oct low"),
        (3200, "3,200", "Crash low"),
    ]
    for price, label, note in ladder:
        color = RED if price > close else GREEN
        y = price_to_y(price)
        add_rect(slide, 1.0, y - 0.015, 2.5, 0.03, color)
        add_text(slide, 3.65, y - 0.2, 2.2, 0.4, label, size=14, bold=True, color=color)
        add_text(slide, 5.85, y - 0.2, 3.2, 0.4, note, size=13, color=GRAY)
    for price, label, color, marker_x in (
        (close, f"Now {close:,.0f}", BLUE, 0.3),
        (core.AVERAGE_PRICE, f"Avg {core.AVERAGE_PRICE:,}", NAVY, 1.6),
    ):
        y = price_to_y(price)
        add_rect(slide, marker_x, y - 0.17, 1.15, 0.32, color, rounded=True)
        add_text(slide, marker_x, y - 0.17, 1.15, 0.32, label, size=11, bold=True,
                 color=WHITE, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    card(slide, 9.3, 1.65, 3.43, 5.1, "How to use", [
        "Red = resistance above the current price, green = support below",
        "The gap zone 3,930–3,980 is the first real test",
        "Only a daily CLOSE below 3,600 breaks the structure",
    ], accent=NAVY, size=15)

    # 5. position plan
    slide = new_slide(
        "Your position plan" if core.HOLDING_BMRI else "Former position plan (closed)",
        f"Average {core.AVERAGE_PRICE:,} · breakeven ~{core.BREAKEVEN_PRICE:,} · now {close:,.0f} ({pnl:+.1f}%)"
        + ("" if core.HOLDING_BMRI else " · position closed, kept for reference"),
        5,
    )
    plan_tiles = [
        (core.STOP_CLOSE, "Stop (daily close)", RED),
        (core.TARGET_1, "T1: sell ~half (gap fill)", GREEN),
        (core.TARGET_2, "T2: sell more (MA50)", GREEN),
        (core.TARGET_3, "T3: breakout only", BLUE),
    ]
    for index, (level, label, color) in enumerate(plan_tiles):
        from_avg = (level / core.AVERAGE_PRICE - 1) * 100
        from_now = level - close
        note = f"{from_avg:+.1f}% from avg · {from_now:+,.0f} from now"
        kpi(slide, 0.6 + index * 3.07, 1.65, 2.87, 1.65, f"{level:,}", label, color, note)
    card(slide, 0.6, 3.6, 7.1, 3.15, "Rules", [
        ("After T1:", f"move the stop on the rest to breakeven ({core.BREAKEVEN_PRICE:,})"),
        ("Hold the rest only if", "price closes above 3,980 on volume > ~220M shares"),
        ("No averaging down", "unless 3,600–3,700 holds with a reversal candle"),
        ("Risk ≤ 1–2% of capital.", f"Loss at stop = lots × 100 × Rp{core.AVERAGE_PRICE - core.STOP_CLOSE}"),
    ], accent=NAVY, size=14)
    risk = close - core.STOP_CLOSE
    if close < core.STOP_CLOSE:
        rr_items = [("Below the stop:", "plan says exit"), ("Verdict:", ta["verdict"])]
    elif close >= core.TARGET_1:
        rr_items = [("T1 zone reached:", "take partial profit"), ("Verdict:", ta["verdict"])]
    else:
        rr_t1 = (core.TARGET_1 - close) / risk
        rr_t2 = (core.TARGET_2 - close) / risk
        rr_items = [
            ("To T1:", f"~{rr_t1:.1f}R (Rp{core.TARGET_1 - close:,.0f} gain vs Rp{risk:,.0f} risk)"),
            ("To T2:", f"~{rr_t2:.1f}R"),
            ("Verdict:", short(ta["verdict"], 90)),
        ]
    card(slide, 7.93, 3.6, 4.8, 3.15, f"Risk / reward from {close:,.0f}", rr_items, accent=AMBER, size=14)

    # 6. setups
    slide = new_slide("Swing setups to watch", "Wait for the market to confirm; don't predict", 6)
    card(slide, 0.6, 1.65, 3.9, 5.1, "1 · Higher-low pullback", [
        ("Preferred setup", ""),
        ("Trigger:", "holds 3,600–3,700 on lower volume + reversal candle"),
        ("Entry", "~3,700 · stop ~3,550"),
        ("Target", "3,960 → ~1.7R (4,100 → ~2.7R)"),
    ], accent=GREEN, size=16)
    card(slide, 4.72, 1.65, 3.9, 5.1, "2 · Breakout", [
        ("Trigger:", "daily close > 3,980 with volume > ~220M shares"),
        ("Entry", "~4,000 · stop ~3,840"),
        ("Target", "4,300 → ~1.9R"),
        ("Note:", "expect a pause at MA50 ~4,100"),
    ], accent=BLUE, size=16)
    card(slide, 8.84, 1.65, 3.89, 5.1, "3 · Stand aside", [
        ("Trigger:", "daily close < 3,600"),
        ("Meaning:", "the rebound failed"),
        ("Next:", "3,460, then 3,200"),
        ("Action:", "exit and don't catch the knife"),
    ], accent=RED, size=16)

    # IHSG market direction
    if ihsg:
        outlook = ihsg["outlook"]
        lean_color = GREEN if outlook["score"] >= 1 else RED if outlook["score"] <= -1 else AMBER
        flow_today = ihsg.get("net_foreign_today")
        flow_text = "not entered" if flow_today is None else f"Rp{flow_today:+,.0f}bn"
        slide = new_slide(
            f"IHSG: {outlook['label']}",
            f"{ihsg['date']} · close {ihsg['close']:,.0f} ({big_banks.fmt_pct(ihsg['change_1d'])}) · "
            f"vs 15 Oct {big_banks.fmt_pct(ihsg['vs_pre_shock'])} · market foreign {flow_text}",
        )
        if ihsg_analysis.CHART_FILE.exists():
            slide.shapes.add_picture(str(ihsg_analysis.CHART_FILE), Inches(0.6), Inches(1.55), height=Inches(5.3))
        reasons = [short(reason, 70) for reason in outlook["reasons"][:5]] or ["Mixed signals"]
        card(slide, 8.85, 1.55, 3.88, 2.7, f"Why ({outlook['score']:+d}, {outlook['confidence']})",
             reasons, accent=lean_color, size=12)
        level_items = [(f"{value:,}", name) for value, name in reversed(ihsg["resistances"][:2])]
        level_items += [(f"{value:,}", name) for value, name in ihsg["supports"][:2]]
        card(slide, 8.85, 4.4, 3.88, 2.45, "Levels (R above / S below)", level_items, accent=NAVY, size=12)

    # Macro: Gold, Brent, DXY
    if macro:
        slide = new_slide("Macro: Gold · Brent · DXY",
                          f"{macro['overall']} · live quotes {macro['generated']} WIB")
        rows = [["Asset", "Last", "1D", "5D", "Trend", "Support", "Resistance", "IHSG impact"]]
        cell_colors = {}
        for row_index, asset in enumerate(macro["assets"], start=1):
            decimals = asset["decimals"]
            rows.append([
                asset["name"],
                macro_levels.fmt(asset["close"], decimals),
                f"{asset['change_1d']:+.1f}%",
                f"{asset['change_5d']:+.1f}%",
                f"{asset['trend']} · RSI {asset['rsi']:.0f}",
                "\n".join(f"{macro_levels.fmt(v, decimals)} {n}" for v, n in asset["supports"]) or "–",
                "\n".join(f"{macro_levels.fmt(v, decimals)} {n}" for v, n in asset["resistances"]) or "–",
                asset.get("impact_short") or short(asset["impact"], 60),
            ])
            for column, key in ((2, "change_1d"), (3, "change_5d")):
                cell_colors[(row_index, column)] = GREEN if asset[key] > 0 else RED
            if asset["impact_score"] < 0:
                cell_colors[(row_index, 7)] = RED
            elif asset["impact_score"] > 0:
                cell_colors[(row_index, 7)] = GREEN
        table(slide, 0.6, 1.5, [1.55, 1.0, 0.8, 0.8, 1.45, 1.95, 1.95, 2.63], rows,
              row_height=0.78, size=11, cell_colors=cell_colors)
        if macro_levels.CHART_FILE.exists():
            slide.shapes.add_picture(str(macro_levels.CHART_FILE), Inches(1.0), Inches(4.75), height=Inches(2.2))

    # Big 4 banks (watchlist)
    if banks:
        ihsg_text = (
            f"IHSG {banks_summary['ihsg_close']:,.0f} ({big_banks.fmt_pct(banks_summary['ihsg_change_1d'])})"
        )
        sector_label = (banks_summary.get("sector") or {}).get("label", "")
        slide = new_slide(f"Big 4 banks: {sector_label.replace('Big 4 ', '')}" if sector_label else "Big 4 banks: overview",
                          f"Direction lean after technicals + flow · {banks_summary['date']} · {ihsg_text}")
        rows = [["Bank", "Close", "1D", "5D", "vs 15 Oct", "From low", "RSI", "Direction lean"]]
        cell_colors = {}
        for row_index, bank in enumerate(banks, start=1):
            values = [bank["code"], f"{bank['close']:,.0f}"]
            for offset, key in enumerate(("change_1d", "change_5d", "vs_pre_shock", "from_low")):
                value = bank[key]
                values.append(big_banks.fmt_pct(value))
                if value is not None:
                    cell_colors[(row_index, 2 + offset)] = GREEN if value > 0 else RED
            values.append(f"{bank['rsi']:.0f}")
            outlook = bank.get("outlook") or {}
            values.append(outlook.get("label", bank["bias"]))
            score = outlook.get("score", 0)
            if score >= 1:
                cell_colors[(row_index, 7)] = GREEN
            elif score <= -1:
                cell_colors[(row_index, 7)] = RED
            rows.append(values)
        bank_colors = [RGBColor.from_string(big_banks.COLORS[b["code"]].lstrip("#")) for b in banks]
        table(slide, 0.6, 1.55, [1.2, 1.2, 1.1, 1.1, 1.3, 1.3, 0.8, 4.13], rows,
              row_height=0.48, size=14, first_col_colors=bank_colors, cell_colors=cell_colors)
        if big_banks.RELATIVE_CHART.exists():
            slide.shapes.add_picture(str(big_banks.RELATIVE_CHART), Inches(0.975), Inches(4.15),
                                     height=Inches(2.75))

        slide = new_slide("Big 4 banks: technical snapshot",
                          "Watchlist view: plan alerts and broksum stay BMRI-only")
        if big_banks.GRID_CHART.exists():
            slide.shapes.add_picture(str(big_banks.GRID_CHART), Inches(0.6), Inches(1.55), height=Inches(5.3))
        for index, bank in enumerate(banks):
            y = 1.55 + index * 1.36
            color = bank_colors[index]
            add_rect(slide, 8.35, y, 4.38, 1.22, LIGHT, rounded=True)
            add_rect(slide, 8.35, y + 0.15, 0.06, 0.92, color)
            add_text(slide, 8.55, y + 0.08, 4.1, 0.35, f"{bank['code']}  ·  {bank['bias']}",
                     size=14, bold=True, color=color)
            signal_text = ", ".join(bank["signals"]) or "No new signal"
            triggered = [key for key, _ in (bank.get("entry") or {}).get("triggers", [])]
            if triggered and bank["code"] in big_banks.ENTRY_ALERT_CODES:
                setups = " + ".join(key.split("_")[1] for key in triggered)
                add_text(slide, 8.55, y + 0.42, 4.1, 0.3, f"ENTRY TRIGGERED: {setups}", size=11,
                         bold=True, color=GREEN)
            else:
                add_text(slide, 8.55, y + 0.42, 4.1, 0.3, f"Signal: {signal_text}", size=11)
            up = bank["resistances"][0] if bank["resistances"] else None
            down = bank["supports"][0] if bank["supports"] else None
            watch = "  ·  ".join(
                part for part in (
                    f"▲ {up[0]:,} ({up[1]})" if up else "",
                    f"▼ {down[0]:,} ({down[1]})" if down else "",
                ) if part
            )
            add_text(slide, 8.55, y + 0.72, 4.1, 0.45, f"Watch: {watch}", size=11, color=GRAY)

    # Entry setups for BBCA / BBRI / BBNI
    if entry_rows:
        slide = new_slide(
            "Entry setups: " + " · ".join(big_banks.ENTRY_ALERT_CODES),
            f"Levels recalculated every close · alert only at ≥ {big_banks.MIN_REWARD_RISK}R to T2 and ≥ 1R to T1",
        )
        rows = [["Bank", "Setup", "Trigger", "Stop", "T1 / T2", "R:R", "Status"]]
        cell_colors = {}
        bank_color = {b["code"]: RGBColor.from_string(big_banks.COLORS[b["code"]].lstrip("#")) for b in banks}
        first_col = []
        for row_index, row in enumerate(entry_rows, start=1):
            setup = (banks[[b["code"] for b in banks].index(row["code"])]["entry"] or {}).get(row["setup"].lower(), {})
            targets = "–"
            if row["target"] is not None:
                t1 = setup.get("target_1", row["target"])
                targets = f"{t1:,} / {row['target']:,}" if t1 != row["target"] else f"{row['target']:,}"
            rows.append([
                row["code"],
                row["setup"],
                row["trigger"],
                "–" if row["stop"] is None else f"{row['stop']:,}",
                targets,
                "–" if row["rr"] is None else f"{row['rr']:.1f}R",
                short(row["status"], 70),
            ])
            first_col.append(bank_color[row["code"]])
            if row["status"].startswith("TRIGGERED"):
                cell_colors[(row_index, 6)] = GREEN
            elif row["status"].startswith(("Approaching", "In the zone")):
                cell_colors[(row_index, 6)] = AMBER
        table(slide, 0.6, 1.55, [0.95, 1.15, 2.75, 0.95, 1.55, 0.8, 3.98], rows,
              row_height=0.6, size=12, first_col_colors=first_col, cell_colors=cell_colors)
        add_text(slide, 0.6, 6.0, 12.13, 0.8, [
            "Pullback: 38.2–50% retracement of the post-shock rebound + reversal candle · stop ½ ATR below the zone · "
            "T1 rebound high (sell ~half), T2 gap fill.",
            "Breakout: close above the pre-shock close / rebound high on above-average volume · stop 0.8 ATR below the level · "
            "T1/T2 next resistances.",
        ], size=11, color=GRAY)

    # TEBE speculative watch
    if tebe:
        verdict_color = RED if tebe["close"] < tebe_watch.SUPPORT else AMBER if tebe["close"] < tebe_watch.GAP else GREEN
        slide = new_slide(
            f"TEBE: {tebe['verdict']}",
            f"{tebe['date']} · close {tebe['close']:,.0f} ({tebe['change_1d']:+.1f}%) · "
            + (f"your avg {tebe['average_price']:,} → {tebe['pnl_pct']:+.1f}% · speculative position"
               if tebe.get("average_price") else "speculative watch"),
        )
        if tebe_watch.CHART_FILE.exists():
            slide.shapes.add_picture(str(tebe_watch.CHART_FILE), Inches(0.6), Inches(1.55), height=Inches(5.3))
        level_items = [
            (f"{tebe_watch.GAP:,}", "gap: hold rest above"),
            (f"{tebe_watch.PARTIAL_SELL_ZONE[0]:,}–{tebe_watch.PARTIAL_SELL_ZONE[1]:,}", "sell part"),
            (f"{tebe_watch.SUPPORT:,}", "STOP (daily close below)"),
            (f"{tebe['ma200']:,.0f}", "MA200"),
        ]
        if tebe.get("average_price"):
            level_items.insert(1, (f"{tebe['average_price']:,}", f"your avg ({tebe['pnl_pct']:+.1f}%)"))
        card(slide, 8.85, 1.55, 3.88, 2.75, "Position levels", level_items, accent=verdict_color, size=12)
        risk_items = [
            ("Plan:", short(tebe["guidance"], 95)),
            ("Volatility:", f"ATR ~{tebe['atr_pct']:.0f}%/day: keep size small"),
        ]
        if tebe["alerts"]:
            risk_items.insert(0, ("ALERT:", short(next(iter(tebe["alerts"].values())), 80)))
        card(slide, 8.85, 4.45, 3.88, 2.4, "What to do", risk_items, accent=NAVY, size=11)

    # 7. daily system
    slide = new_slide("Daily monitoring system", "Runs automatically on your PC, Monday–Friday", 7)
    add_rect(slide, 1.2, 2.55, 10.9, 0.06, LINE)
    steps = [
        ("08:00", "Morning brief", GREEN, [
            "Overnight: EIDO, USD/IDR, Wall St, US 10Y, Nikkei",
            "Opening bias: Positive / Neutral / Negative",
            "News from the last 24 h",
            "Pivots + today's game plan",
        ]),
        ("08:45–09:00", "Pre-opening & open", BLUE, [
            "Check BMRI's indicative opening price (IEP) vs yesterday's close",
            "Follow the if/then rules",
            "Stop is a daily-close rule",
        ]),
        ("16:20", "Close report", AMBER, [
            "Written technical analysis + verdict",
            "Broksum form → accumulation or distribution",
            "Plan alerts as popups",
            "HTML report + this deck refreshed",
        ]),
    ]
    for index, (time_label, title, color, items) in enumerate(steps):
        x = 0.6 + index * 4.1
        dot = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 1.75), Inches(2.33), Inches(0.5), Inches(0.5))
        dot.fill.solid()
        dot.fill.fore_color.rgb = color
        dot.line.fill.background()
        add_text(slide, x, 1.6, 4.0, 0.5, time_label, size=22, bold=True, color=color, align=PP_ALIGN.CENTER)
        card(slide, x, 3.1, 3.9, 3.65, title, items, accent=color, size=14)

    # 8. alerts
    slide = new_slide("Plan alerts", "A popup appears the first time a trigger fires", 8)
    table(
        slide, 0.6, 1.6, [4.6, 7.53],
        [
            ["Trigger", "What to do"],
            ["Close < 3,600", "STOP: exit the position"],
            ["Low touches 3,600, closes above", "Stop held; watch for a reversal candle"],
            ["Close ≤ 3,650", "Caution: close to the stop"],
            ["High ≥ 3,960", "T1: sell ~half, move stop to 3,820"],
            ["Close > 3,980 + volume > average", "Breakout confirmed: hold toward 4,100 / 4,300"],
            ["High ≥ 4,100", "T2: take more profit / tighten the trailing stop"],
            ["Foreign turns net buy / 3 days buying", "Confirmation for holding or the setups"],
            ["3 days accumulation / distribution", "Broksum trend: hold or reduce on strength"],
        ],
        row_height=0.56,
        size=14,
        first_col_colors=[RED, AMBER, AMBER, GREEN, GREEN, GREEN, BLUE, BLUE],
    )

    # 9. broksum
    slide = new_slide("Reading the broker summary (broksum)",
                      "Typed into the 16:20 form from your broker app, ~30 seconds", 9)
    add_rect(slide, 0.6, 1.65, 4.6, 5.1, NAVY, rounded=True)
    add_text(slide, 0.9, 1.85, 4.0, 0.4, "Example input", size=15, bold=True, color=WHITE)
    add_text(slide, 0.9, 2.4, 4.0, 3.2, [
        "Net foreign:  -45.2",
        "",
        "AK   125.3   3790",
        "BK    80",
        "YP  -150     3805",
        "XL   -60",
    ], size=19, color=RGBColor(0xC8, 0xF0, 0xDC), font="Consolas")
    add_text(slide, 0.9, 5.6, 4.0, 1.0, "CODE  value (Rp bn)  [avg price]\nbuyers +, sellers −", size=12, color=PALE)
    card(slide, 5.45, 1.65, 3.55, 5.1, "Broker types", [
        ("Foreign inst.:", "AK BK ZP KZ RX YU CG GW DP"),
        ("Local inst.:", "CC NI SQ OD DX LG KI"),
        ("Retail:", "YP XL XC PD KK EP"),
        ("Editable", "in brokers.json; verify in your app"),
    ], accent=BLUE, size=15)
    latest_signal = data.get("broksum_signal")
    signal_items = [
        ("ACCUMULATION:", "institutions buy, retail sells"),
        ("DISTRIBUTION:", "institutions sell, retail buys"),
        ("Buyers' avg price", "often acts as support"),
    ]
    if latest_signal:
        signal_items.insert(0, (f"Latest ({date_label}):", latest_signal))
    else:
        signal_items.append(("3 days in a row", "triggers an alert"))
    card(slide, 9.23, 1.65, 3.5, 5.1, "Signal", signal_items, accent=GREEN, size=15)

    # 10. risks & next steps
    slide = new_slide("Risks & next steps", None, 10)
    card(slide, 0.6, 1.4, 5.95, 5.35, "Risks", [
        ("MSCI decision:", "headline risk; can gap through the stop like 16 Oct"),
        ("End-of-day check:", "set a stop-loss order in your broker app for intraday protection"),
        ("Data:", "Yahoo prices are unofficial; verify with your broker"),
        ("Broksum:", "entered manually; broker types are approximate"),
    ], accent=RED, size=16)
    def alert_label(key):
        if key in ALERT_LABELS:
            return ALERT_LABELS[key]
        if key.endswith("_entry"):
            return key.replace("_entry", " entry").replace("_", " ")
        if key.startswith("ihsg_"):
            return "IHSG " + key[5:].replace("_", " ")
        if key.startswith("tebe_"):
            return "TEBE " + key[5:].replace("_", " ")
        return key

    active = [alert_label(key) for key in data.get("alert_keys", [])]
    card(slide, 6.78, 1.4, 5.95, 5.35, f"Status after {date_label}", [
        ("Verdict:", short(ta["verdict"], 110)),
        ("Active alerts:", ", ".join(active) if active else "none"),
        ("Next:", "08:00 morning brief, 16:20 close report"),
        ("Review together:", "paste reports or broksum screenshots in chat"),
    ], accent=GREEN, size=16)

    return save_deck(prs, output, data["date"])


def save_deck(prs, output: Path, date_text: str) -> Path:
    """Save the latest deck plus a dated copy; if the deck is open in PowerPoint, save beside it."""
    DECK_ARCHIVE.mkdir(parents=True, exist_ok=True)
    archive_copy = DECK_ARCHIVE / f"BMRI_Recap_{date_text}.pptx"
    prs.save(archive_copy)
    try:
        shutil.copyfile(archive_copy, output)
        return output
    except PermissionError:
        fallback = output.with_name(f"{output.stem}_{datetime.now():%Y%m%d_%H%M}.pptx")
        shutil.copyfile(archive_copy, fallback)
        return fallback


if __name__ == "__main__":
    print(build_deck())

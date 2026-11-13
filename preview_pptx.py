"""Rough layout preview of a .pptx (no PowerPoint needed): draws shapes, text and images."""

import sys
import textwrap
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, Ellipse
from PIL import Image
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

EMU_PER_INCH = 914400


def inches(value):
    return Emu(value).inches


def rgb(color, default="#000000"):
    try:
        return "#" + str(color.rgb)
    except Exception:
        return default


def draw_text_frame(ax, frame, x, y, w, h, scale):
    cursor = y + 0.05
    for paragraph in frame.paragraphs:
        runs = paragraph.runs
        if not runs:
            cursor += 0.2
            continue
        text = "".join(run.text for run in runs)
        size = max((run.font.size.pt if run.font.size else 18) for run in runs)
        bold = any(run.font.bold for run in runs)
        color = rgb(runs[-1].font.color, "#1F2A44")
        char_width = size * 0.0075   # inches per character, approx
        max_chars = max(int(w / char_width), 1)
        lines = textwrap.wrap(text, max_chars) or [""]
        align = paragraph.alignment
        ha = "center" if align == 2 else "right" if align == 3 else "left"
        tx = x + w / 2 if ha == "center" else x + w if ha == "right" else x + 0.05
        for line in lines:
            ax.text(tx, cursor, line, fontsize=size * scale, color=color, ha=ha, va="top",
                    fontweight="bold" if bold else "normal", family="DejaVu Sans")
            cursor += size / 72 * 1.25
        cursor += (paragraph.space_after.pt / 72) if paragraph.space_after else 0
    if cursor > y + h + 0.15:
        ax.add_patch(Rectangle((x, y), w, h, fill=False, edgecolor="magenta", linewidth=2, linestyle="--"))


def render(path: Path, out_dir: Path):
    prs = Presentation(path)
    width, height = inches(prs.slide_width), inches(prs.slide_height)
    out_dir.mkdir(exist_ok=True)
    outputs = []
    for number, slide in enumerate(prs.slides, start=1):
        fig = plt.figure(figsize=(width, height), dpi=80)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, width)
        ax.set_ylim(height, 0)
        ax.axis("off")
        scale = 0.98
        for shape in slide.shapes:
            x, y, w, h = inches(shape.left), inches(shape.top), inches(shape.width), inches(shape.height)
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image = Image.open(BytesIO(shape.image.blob))
                ax.imshow(image, extent=(x, x + w, y + h, y), aspect="auto", zorder=1)
                continue
            if shape.shape_type == MSO_SHAPE_TYPE.AUTO_SHAPE:
                fill = rgb(shape.fill.fore_color, None) if shape.fill.type == 1 else None
                try:
                    edge = rgb(shape.line.color, None) if shape.line.fill.type == 1 else None
                except Exception:
                    edge = None
                name = shape.auto_shape_type
                if "OVAL" in str(name):
                    patch = Ellipse((x + w / 2, y + h / 2), w, h, facecolor=fill or "none", edgecolor=edge or "none")
                elif "ROUNDED" in str(name):
                    patch = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12",
                                           facecolor=fill or "none", edgecolor=edge or "none")
                else:
                    patch = Rectangle((x, y), w, h, facecolor=fill or "none", edgecolor=edge or "none")
                ax.add_patch(patch)
            if shape.has_text_frame and shape.text_frame.text.strip():
                draw_text_frame(ax, shape.text_frame, x, y, w, h, scale)
            if shape.has_table:
                table = shape.table
                cy = y
                for row in table.rows:
                    cx = x
                    rh = inches(row.height)
                    for col_index, cell in enumerate(row.cells):
                        cw = inches(table.columns[col_index].width)
                        fill = rgb(cell.fill.fore_color, "#FFFFFF")
                        ax.add_patch(Rectangle((cx, cy), cw, rh, facecolor=fill, edgecolor="#D9DEE4"))
                        draw_text_frame(ax, cell.text_frame, cx + 0.07, cy + rh / 2 - 0.15, cw - 0.14, rh, scale)
                        cx += cw
                    cy += rh
        output = out_dir / f"slide_{number:02d}.png"
        fig.savefig(output, dpi=80)
        plt.close(fig)
        outputs.append(output)
    return outputs


if __name__ == "__main__":
    deck = Path(sys.argv[1])
    for item in render(deck, Path(sys.argv[2])):
        print(item)

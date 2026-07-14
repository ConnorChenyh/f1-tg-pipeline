from __future__ import annotations

from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont

F1_RED = "#E10600"
F1_DARK = "#15151E"
F1_LIGHT = "#F5F5F5"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    if bold:
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
    else:
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]

    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    if not text:
        return [""]

    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
        current = ""
        for char in paragraph:
            candidate = current + char
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = char
        if current:
            lines.append(current)
    return lines


def _draw_multiline(
    draw: ImageDraw.ImageDraw,
    text: str,
    xy: tuple[int, int],
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_spacing: int = 12,
) -> int:
    x, y = xy
    for line in _wrap_text(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line or "A", font=font)
        y = bbox[3] + line_spacing
    return y


def _measure_lines_height(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.ImageFont,
    line_spacing: int,
) -> int:
    total = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line or "A", font=font)
        total += (bbox[3] - bbox[1]) + line_spacing
    return max(0, total - line_spacing)


def render_item_card(ordinal: str, headline: str, content: str, width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), "#F7F7F4")
    draw = ImageDraw.Draw(img)
    margin_x = 72
    top_y = 70
    max_width = width - margin_x * 2
    draw.rectangle([(0, 0), (width, 22)], fill=F1_RED)
    draw.rectangle([(0, height - 18), (width, height)], fill=F1_DARK)

    label_font = _load_font(28, bold=True)
    ordinal_font = _load_font(62, bold=True)
    title_font = _load_font(46, bold=True)

    draw.text((margin_x, top_y), "F1 24H DIGEST", font=label_font, fill=F1_RED)
    draw.text((margin_x, top_y + 52), f"{ordinal}", font=ordinal_font, fill=F1_RED)
    title_y = top_y + 58
    title_end = _draw_multiline(
        draw,
        headline,
        (margin_x + 96, title_y),
        title_font,
        F1_DARK,
        max_width - 96,
        line_spacing=12,
    )

    divider_y = max(title_end + 42, 230)
    draw.line([(margin_x, divider_y), (width - margin_x, divider_y)], fill="#CBCBCB", width=2)

    text = content.strip()
    body_top = divider_y + 44
    body_bottom = height - 90
    body_height = body_bottom - body_top

    chosen_font: ImageFont.ImageFont | None = None
    chosen_lines: list[str] = []
    chosen_spacing = 14
    for size in range(38, 23, -2):
        font = _load_font(size)
        spacing = max(10, size // 3)
        lines = _wrap_text(draw, text, font, max_width)
        if _measure_lines_height(draw, lines, font, spacing) <= body_height:
            chosen_font = font
            chosen_lines = lines
            chosen_spacing = spacing
            break

    if chosen_font is None:
        chosen_font = _load_font(24)
        chosen_spacing = 8
        chosen_lines = _wrap_text(draw, text, chosen_font, max_width)
        while chosen_lines and _measure_lines_height(draw, chosen_lines + ["…"], chosen_font, chosen_spacing) > body_height:
            chosen_lines.pop()
        if chosen_lines:
            chosen_lines[-1] = chosen_lines[-1].rstrip("。；，,") + "…"

    y = body_top
    for line in chosen_lines:
        draw.text((margin_x, y), line, font=chosen_font, fill="#202026")
        bbox = draw.textbbox((margin_x, y), line or "A", font=chosen_font)
        y = bbox[3] + chosen_spacing

    draw.text((margin_x, height - 58), "围场过去24H新闻", font=_load_font(24), fill="#DADAE0")

    return img


def render_digest_summary_card(items: list[dict[str, Any]], width: int, height: int) -> Image.Image:
    img = Image.new("RGB", (width, height), F1_DARK)
    draw = ImageDraw.Draw(img)
    margin_x = 72
    max_width = width - margin_x * 2

    draw.rectangle([(0, 0), (width, 22)], fill=F1_RED)
    draw.rectangle([(0, height - 18), (width, height)], fill=F1_RED)

    label_font = _load_font(30, bold=True)
    title_font = _load_font(64, bold=True)
    item_font = _load_font(38, bold=True)

    y = 96
    draw.text((margin_x, y), "F1 24H DIGEST", font=label_font, fill=F1_RED)
    y += 72
    y = _draw_multiline(draw, "围场过去24H新闻", (margin_x, y), title_font, F1_LIGHT, max_width, line_spacing=14)
    y += 70

    for item in items[:6]:
        ordinal = item.get("ordinal") or ""
        headline = item.get("headline") or ""
        line = f"{ordinal}、{headline}".strip("、")
        y = _draw_multiline(draw, line, (margin_x, y), item_font, "#F0F0F3", max_width, line_spacing=12)
        y += 30
        if y > height - 180:
            break

    draw.text((margin_x, height - 110), "概要", font=_load_font(32, bold=True), fill=F1_RED)
    return img


def generate_images_for_digest(
    draft: dict[str, Any],
    topics: list[dict[str, Any]],
    draft_dir: Path,
    config: dict[str, Any],
) -> list[str]:
    image_cfg = config.get("images", {})
    width = int(image_cfg.get("width", 1080))
    height = int(image_cfg.get("height", 1440))

    images_dir = draft_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []
    output_root = draft_dir.parent.parent

    items = draft.get("items", [])
    cover = render_digest_summary_card(items, width, height)
    cover_path = images_dir / "cover.png"
    cover.save(cover_path, format="PNG")
    saved_paths.append(str(cover_path.relative_to(output_root)))

    for idx, item in enumerate(items, start=1):
        ordinal = item.get("ordinal", str(idx))
        headline = item.get("headline", "")
        content = item.get("content", "")

        card = render_item_card(ordinal, headline, content, width, height)
        slide_path = images_dir / f"slide_{idx:02d}.png"
        card.save(slide_path, format="PNG")
        saved_paths.append(str(slide_path.relative_to(output_root)))

    return saved_paths

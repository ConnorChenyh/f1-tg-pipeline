from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

F1_RED = "#E10600"
F1_DARK = "#15151E"
F1_LIGHT = "#F5F5F5"
TEXT_WIDTH_RATIO = 0.9

_WRAP_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+(?:[.'’/+&:-][A-Za-z0-9_]+)*|[ \t]+|.")


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
        for token in _WRAP_TOKEN_RE.findall(paragraph):
            if token.isspace() and not current:
                continue

            candidate = current + token
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current.rstrip())
                current = token.lstrip()
        if current:
            lines.append(current.rstrip())
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
    if not lines:
        return 0
    return len(lines) * _line_advance(draw, font, line_spacing) - line_spacing


def _line_advance(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, line_spacing: int) -> int:
    bbox = draw.textbbox((0, 0), "国Ay", font=font)
    return (bbox[3] - bbox[1]) + line_spacing


def _layout_body(
    draw: ImageDraw.ImageDraw,
    text: str,
    text_width: int,
    body_height: int,
) -> tuple[ImageFont.ImageFont, list[str], int, bool]:
    """Pick the largest font whose wrapped text fits. Returns (font, lines, spacing, truncated).

    ``truncated`` means the text could not fit at the smallest size and lines were
    dropped, so the rendered card does not contain the full item content.
    """
    for size in range(34, 21, -2):
        font = _load_font(size)
        spacing = max(10, size // 3)
        lines = _wrap_text(draw, text, font, text_width)
        if _measure_lines_height(draw, lines, font, spacing) <= body_height:
            return font, lines, spacing, False

    font = _load_font(22)
    spacing = 8
    lines = _wrap_text(draw, text, font, text_width)
    truncated = False
    while lines and _measure_lines_height(draw, lines, font, spacing) > body_height:
        lines.pop()
        truncated = True
    if truncated and lines:
        lines[-1] = lines[-1].rstrip("。；，,") + "…"
    return font, lines, spacing, truncated


def render_item_card(
    ordinal: str,
    headline: str,
    content: str,
    width: int,
    height: int,
    *,
    brand_label: str = "F1 24H DIGEST",
    footer_label: str = "围场过去24H新闻",
) -> Image.Image:
    img, _ = render_item_card_with_measure(
        ordinal,
        headline,
        content,
        width,
        height,
        brand_label=brand_label,
        footer_label=footer_label,
    )
    return img


def render_item_card_with_measure(
    ordinal: str,
    headline: str,
    content: str,
    width: int,
    height: int,
    *,
    brand_label: str = "F1 24H DIGEST",
    footer_label: str = "围场过去24H新闻",
) -> tuple[Image.Image, dict[str, Any]]:
    """Render one item card and report how much of the content actually fit."""
    img = Image.new("RGB", (width, height), "#F7F7F4")
    draw = ImageDraw.Draw(img)
    margin_x = 72
    top_y = 70
    max_width = width - margin_x * 2
    text_width = int(max_width * TEXT_WIDTH_RATIO)
    draw.rectangle([(0, 0), (width, 22)], fill=F1_RED)
    draw.rectangle([(0, height - 18), (width, height)], fill=F1_DARK)

    label_font = _load_font(28, bold=True)
    ordinal_font = _load_font(62, bold=True)
    title_font = _load_font(46, bold=True)

    draw.text((margin_x, top_y), brand_label, font=label_font, fill=F1_RED)
    draw.text((margin_x, top_y + 52), f"{ordinal}", font=ordinal_font, fill=F1_RED)
    title_y = top_y + 58
    title_end = _draw_multiline(
        draw,
        headline,
        (margin_x + 96, title_y),
        title_font,
        F1_DARK,
        int((max_width - 96) * TEXT_WIDTH_RATIO),
        line_spacing=12,
    )

    divider_y = max(title_end + 42, 230)
    draw.line([(margin_x, divider_y), (width - margin_x, divider_y)], fill="#CBCBCB", width=2)

    text = content.strip()
    body_top = divider_y + 44
    body_bottom = height - 90
    body_height = body_bottom - body_top

    chosen_font, chosen_lines, chosen_spacing, truncated = _layout_body(
        draw, text, text_width, body_height
    )

    y = body_top
    advance = _line_advance(draw, chosen_font, chosen_spacing)
    drawn_lines: list[str] = []
    for line in chosen_lines:
        if y + advance > body_bottom:
            break
        draw.text((margin_x, y), line, font=chosen_font, fill="#202026")
        drawn_lines.append(line)
        y += advance

    draw.text((margin_x, height - 58), footer_label, font=_load_font(24), fill="#DADAE0")

    # Compare on whitespace-stripped text: wrapping legitimately drops spaces at
    # line breaks, and counting them would make the loss signal noisy. Only the
    # lines actually drawn are counted, so the figure matches the rendered card.
    source_chars = len(re.sub(r"\s+", "", text))
    rendered_chars = len(re.sub(r"\s+", "", "".join(drawn_lines).rstrip("…")))
    dropped_lines = len(chosen_lines) - len(drawn_lines)
    measure = {
        "source_chars": source_chars,
        "rendered_chars": min(rendered_chars, source_chars),
        "truncated": bool(truncated) or dropped_lines > 0,
        "dropped_lines": dropped_lines,
        "font_size": getattr(chosen_font, "size", None),
    }
    return img, measure


def render_digest_summary_card(
    items: list[dict[str, Any]],
    width: int,
    height: int,
    *,
    brand_label: str = "F1 24H DIGEST",
    heading: str = "围场过去24H新闻",
    footer_label: str = "概要",
) -> Image.Image:
    img = Image.new("RGB", (width, height), F1_DARK)
    draw = ImageDraw.Draw(img)
    margin_x = 72
    max_width = width - margin_x * 2
    text_width = int(max_width * TEXT_WIDTH_RATIO)

    draw.rectangle([(0, 0), (width, 22)], fill=F1_RED)
    draw.rectangle([(0, height - 18), (width, height)], fill=F1_RED)

    label_font = _load_font(30, bold=True)
    title_font = _load_font(64, bold=True)
    item_font = _load_font(38, bold=True)

    y = 96
    draw.text((margin_x, y), brand_label, font=label_font, fill=F1_RED)
    y += 72
    y = _draw_multiline(draw, heading, (margin_x, y), title_font, F1_LIGHT, text_width, line_spacing=14)
    y += 70

    for item in items[:6]:
        ordinal = item.get("ordinal") or ""
        headline = item.get("headline") or ""
        line = f"{ordinal}、{headline}".strip("、")
        y = _draw_multiline(draw, line, (margin_x, y), item_font, "#F0F0F3", text_width, line_spacing=12)
        y += 30
        if y > height - 180:
            break

    draw.text((margin_x, height - 110), footer_label, font=_load_font(32, bold=True), fill=F1_RED)
    return img


RENDER_MEASUREMENTS_FILENAME = "render_measurements.json"


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

    measurements: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        ordinal = item.get("ordinal", str(idx))
        headline = item.get("headline", "")
        content = item.get("content", "")

        card, measure = render_item_card_with_measure(ordinal, headline, content, width, height)
        slide_path = images_dir / f"slide_{idx:02d}.png"
        card.save(slide_path, format="PNG")
        saved_paths.append(str(slide_path.relative_to(output_root)))

        measure["slide"] = slide_path.name
        measure["ordinal"] = ordinal
        measurements.append(measure)

    truncated = [item for item in measurements if item.get("truncated")]
    if truncated:
        logger.warning(
            "Image layout truncated %d item(s) that did not fit one card: %s",
            len(truncated),
            ", ".join(
                f"{item['slide']} ({item['rendered_chars']}/{item['source_chars']} chars)"
                for item in truncated
            ),
        )
    (draft_dir / RENDER_MEASUREMENTS_FILENAME).write_text(
        json.dumps(
            {
                "items": measurements,
                "page_width": width,
                "page_height": height,
                "truncated_count": len(truncated),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return saved_paths

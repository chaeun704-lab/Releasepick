"""Render card JPEGs from plan + theme YAML (Pillow). 템플릿2(본문).txt 규격 반영."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
THEMES_DIR = ROOT / "themes"

# 카드뉴스 최종 JPEG 캔버스 (GPT Image 생성 후 리사이즈 기준)
CARD_WIDTH = 1000
CARD_HEIGHT = 1350
CARD_SIZE: tuple[int, int] = (CARD_WIDTH, CARD_HEIGHT)
WINDOWS_MALGUN = Path(r"C:\Windows\Fonts\malgun.ttf")
WINDOWS_MALGUN_BD = Path(r"C:\Windows\Fonts\malgunbd.ttf")


@dataclass
class RenderOptions:
    theme_id: str = "mofe_body"
    font_scale: float = 1.0
    title_color: str | None = None
    body_color: str | None = None
    logo_position: str = "top_right"
    character_png: bytes | None = None
    logo_png: bytes | None = None
    # 템플릿2 §2-2: 녹/파/보/주황 섹션 톤
    section_tone: str = "blue"
    # 템플릿2 §6: simple=여백형, white_card=패턴B 큰 흰 카드
    body_layout: str = "simple"


def _load_theme(theme_id: str) -> dict[str, Any]:
    path = THEMES_DIR / f"{theme_id}.yaml"
    if not path.exists():
        path = THEMES_DIR / "mofe_body.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    sz = max(11, int(size))
    paths = [WINDOWS_MALGUN_BD, WINDOWS_MALGUN] if bold else [WINDOWS_MALGUN, WINDOWS_MALGUN_BD]
    for p in paths:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), sz)
            except OSError:
                continue
    return ImageFont.load_default()


def _scale(n: float, factor: float) -> int:
    return max(10, int(n * factor))


def _line_height(font: ImageFont.ImageFont) -> int:
    if hasattr(font, "getbbox"):
        b = font.getbbox("Ay가")
        return b[3] - b[1] + 6
    return (getattr(font, "size", 14) or 14) + 6


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _tone(theme: dict[str, Any], tone_key: str) -> dict[str, str]:
    tones = theme.get("section_tones") or {}
    t = tones.get(tone_key) or tones.get("neutral") or {
        "stripe": "#4D99D8",
        "title_on_page": "#111111",
        "body_accent": "#2C4A7C",
    }
    stripe = str(t.get("stripe", "#4D99D8"))
    return {
        "stripe": stripe,
        "title_on_page": str(t.get("title_on_page", "#111111")),
        "body_accent": str(t.get("body_accent", stripe)),
    }


def section_tone_palette(theme_id: str, tone_key: str) -> dict[str, str]:
    """섹션 톤별 stripe·제목 강조색 (이미지 프롬프트·Pillow 렌더 공용)."""
    return _tone(_load_theme(theme_id), tone_key)


def get_logo_box(theme_id: str, position: str) -> tuple[int, int, int, int]:
    """로고 합성 영역 (x, y, width, height) — CARD_WIDTH×CARD_HEIGHT 기준."""
    theme = _load_theme(theme_id)
    boxes = theme.get("logo_box") or {}
    key = position if position in boxes else "bottom_center"
    box = boxes.get(key) or boxes.get("bottom_center") or [250, 1253, 500, 97]
    return int(box[0]), int(box[1]), int(box[2]), int(box[3])


def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    *,
    align: str = "left",
) -> None:
    x0, y0, x1, y1 = xy
    w = x1 - x0
    approx = max(6, w // max(10, _line_height(font)))
    lines = textwrap.wrap(text, width=approx) if text else [""]
    y = y0
    lh = _line_height(font)
    for line in lines:
        if y + lh > y1:
            break
        if align == "center":
            tw = _text_width(draw, line, font)
            draw.text((x0 + (w - tw) // 2, y), line, font=font, fill=fill)
        else:
            draw.text((x0, y), line, font=font, fill=fill)
        y += lh


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    *,
    fill: str,
    outline: str | None = None,
    width: int = 1,
) -> None:
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)
    else:
        draw.rectangle(xy, fill=fill, outline=outline, width=width)


def _draw_capsule_label(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
    text_color: str,
    bg: str,
    radius: int,
    pad_x: int = 18,
    pad_y: int = 10,
) -> int:
    """흰 캡슐 라벨 (템플릿2 §4). 반환값: 사용한 높이."""
    if not text.strip():
        return 0
    tw = _text_width(draw, text, font)
    w, h = tw + 2 * pad_x, _line_height(font) + 2 * pad_y - 6
    _rounded_rect(draw, (x, y, x + w, y + h), radius, fill=bg, outline=None)
    draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=text_color)
    return h + 8


def _logo_make_background_transparent(logo: Image.Image, *, threshold: int = 40) -> Image.Image:
    """로고 PNG 검은/짙은 배경을 투명 처리해 카드 배경과 자연스럽게 합성."""
    rgba = logo.convert("RGBA")
    px = rgba.load()
    w, h = rgba.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a < 10:
                continue
            if r <= threshold and g <= threshold and b <= threshold:
                px[x, y] = (r, g, b, 0)
    return rgba


def trim_logo_rgba(logo: Image.Image, *, pad: int = 2, threshold: int = 40) -> Image.Image:
    """투명·검은 여백을 잘라 로고 콘텐츠만 남긴다."""
    rgba = _logo_make_background_transparent(logo, threshold=threshold)
    bbox = rgba.getbbox()
    if not bbox:
        return rgba
    x0, y0, x1, y1 = bbox
    if pad:
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(rgba.width, x1 + pad)
        y1 = min(rgba.height, y1 + pad)
    return rgba.crop((x0, y0, x1, y1))


def _paste_logo(
    base: Image.Image,
    logo_bytes: bytes | None,
    theme: dict[str, Any],
    position: str,
) -> None:
    if not logo_bytes:
        return
    try:
        logo = trim_logo_rgba(Image.open(BytesIO(logo_bytes)))
    except OSError:
        return
    boxes = theme.get("logo_box") or {}
    key = position if position in boxes else "top_right"
    box = boxes.get(key) or boxes.get("top_right") or [588, 40, 160, 56]
    bx0, by0, bw, bh = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    # 한·영 동일: 박스 안에 contain 스케일 후 동일 앵커로 배치
    logo.thumbnail((bw, bh), Image.Resampling.LANCZOS)
    lw, lh = logo.size
    if key == "top_right":
        x, y = bx0 + bw - lw, by0
    elif key == "bottom_center":
        x, y = bx0 + (bw - lw) // 2, by0 + (bh - lh) // 2
    else:
        x, y = bx0, by0
    base.paste(logo, (x, y), logo)


def composite_card_logo(
    base: Image.Image,
    logo_bytes: bytes | None,
    *,
    theme_id: str = "mofe_body",
    logo_position: str = "bottom_center",
    wipe_zone_first: bool = True,
) -> Image.Image:
    """카드 이미지에 로고 합성 (한·영 공통)."""
    return paste_logo_on_image(
        base,
        logo_bytes,
        theme_id=theme_id,
        logo_position=logo_position,
        wipe_zone_first=wipe_zone_first,
    )


def wipe_logo_zone(
    base: Image.Image,
    theme: dict[str, Any],
    position: str,
) -> None:
    """AI가 그린 가짜 로고·워드마크를 지우고 합성 영역을 비운다."""
    boxes = theme.get("logo_box") or {}
    key = position if position in boxes else "top_right"
    box = boxes.get(key) or boxes.get("top_right") or [588, 40, 160, 56]
    bx0, by0, bw, bh = int(box[0]), int(box[1]), int(box[2]), int(box[3])
    w, h = base.size
    pad_x, pad_y = 48, 36
    if key == "bottom_center":
        pad_y = 80
    bx0 = max(0, bx0 - pad_x)
    by0 = max(0, by0 - pad_y)
    bw = min(w - bx0, bw + pad_x * 2)
    bh = min(h - by0, bh + pad_y + (48 if key == "bottom_center" else pad_y))
    pad = 6
    samples: list[tuple[int, int, int]] = []
    if key == "bottom_center":
        sy = max(0, by0 - bh - pad)
        for x in range(max(0, bx0), min(w, bx0 + bw), max(8, bw // 8)):
            samples.append(base.getpixel((x, min(h - 1, sy))))
    elif key == "top_right":
        sx = max(0, bx0 - pad - 24)
        for y in range(by0, min(h, by0 + bh), max(8, bh // 4)):
            samples.append(base.getpixel((sx, y)))
    else:
        sx = min(w - 1, bx0 + bw + pad)
        for y in range(by0, min(h, by0 + bh), max(8, bh // 4)):
            samples.append(base.getpixel((sx, y)))
    if not samples:
        fill = (248, 250, 252)
    else:
        fill = tuple(sum(c[i] for c in samples) // len(samples) for i in range(3))  # type: ignore[assignment]
    draw = ImageDraw.Draw(base)
    draw.rectangle((bx0, by0, bx0 + bw, by0 + bh), fill=fill)


def paste_logo_on_image(
    base: Image.Image,
    logo_bytes: bytes | None,
    *,
    theme_id: str = "mofe_body",
    logo_position: str = "bottom_center",
    wipe_zone_first: bool = False,
) -> Image.Image:
    """생성된 카드 JPEG/RGB 이미지 위에 로고 PNG 합성 (배경 투명)."""
    if not logo_bytes:
        return base
    theme = _load_theme(theme_id)
    out = base.convert("RGBA")
    if wipe_zone_first:
        wipe_logo_zone(out, theme, logo_position)
    _paste_logo(out, logo_bytes, theme, logo_position)
    return out.convert("RGB")


def _paste_character(base: Image.Image, char_bytes: bytes | None, theme: dict[str, Any]) -> None:
    if not char_bytes:
        return
    try:
        ch = Image.open(BytesIO(char_bytes)).convert("RGBA")
    except OSError:
        return
    slot = (theme.get("character_slot") or {}).get("bottom_right") or [480, 620, 280, 340]
    sx, sy, sw, sh = int(slot[0]), int(slot[1]), int(slot[2]), int(slot[3])
    ch.thumbnail((sw, sh), Image.Resampling.LANCZOS)
    cw, ch_ = ch.size
    base.paste(ch, (sx, sy + sh - ch_), ch)


def _paste_slot_illustration(base: Image.Image, png_bytes: bytes | None, theme: dict[str, Any]) -> None:
    """AI 생성 일러스트를 character_slot에 배치 (텍스트는 이후에 그려 덮음)."""
    if not png_bytes:
        return
    try:
        im = Image.open(BytesIO(png_bytes)).convert("RGBA")
    except OSError:
        return
    slot = (theme.get("character_slot") or {}).get("bottom_right") or [480, 620, 280, 340]
    sx, sy, sw, sh = int(slot[0]), int(slot[1]), int(slot[2]), int(slot[3])
    im.thumbnail((sw, sh), Image.Resampling.LANCZOS)
    cw, ch_ = im.size
    base.paste(im, (sx, sy + sh - ch_), im)


def render_slide(
    slide: dict[str, Any],
    *,
    plan: dict[str, Any],
    theme: dict[str, Any],
    options: RenderOptions,
    illustration_png: bytes | None = None,
) -> Image.Image:
    role = slide.get("role", "body")
    variants = theme.get("variants") or {}
    v = variants.get(role) or variants.get("body") or {}
    canvas = theme.get("canvas") or {"width": CARD_WIDTH, "height": CARD_HEIGHT}
    w, h = int(canvas["width"]), int(canvas["height"])
    mg = theme.get("margins") or {"x": 60, "y": 48, "bottom_reserve": 78}
    mx = int(mg.get("x", 60))
    my = int(mg.get("y", 48))
    br = int(mg.get("bottom_reserve", 78))
    pal = theme.get("palette") or {}
    sizes = theme.get("sizes") or {}
    layout = theme.get("layout") or {}
    fs = options.font_scale
    tone = _tone(theme, options.section_tone)
    stripe = tone["stripe"]
    tone_title = options.title_color or tone["title_on_page"]
    body_c = options.body_color or pal.get("body", "#333333")
    page_bg = v.get("background") or pal.get("page_bg", "#F6F8F3")
    img = Image.new("RGB", (w, h), page_bg)
    if illustration_png:
        _paste_slot_illustration(img, illustration_png, theme)
    draw = ImageDraw.Draw(img)
    subtle = pal.get("subtle", "#666666")
    foot_c = pal.get("footnote", "#777777")
    cap_bg = pal.get("capsule_bg", "#FFFFFF")
    cap_tx = pal.get("capsule_text", "#111111")
    on_stripe = pal.get("on_stripe", "#FFFFFF")
    cap_r = int(layout.get("capsule_radius", 22))

    if role == "cover":
        band_h = int(layout.get("cover_band_height", 200))
        draw.rectangle([0, 0, w, band_h], fill=stripe)
        f_ser = _font(_scale(sizes.get("cover_series_on_band", 26), fs), bold=True)
        f_head = _font(_scale(sizes.get("cover_head", 22), fs))
        f_st = _font(_scale(sizes.get("cover_slide_title", 44), fs), bold=True)
        series = plan.get("series_title", "")
        hc = plan.get("head_copy", "")
        _draw_text_block(draw, (mx, 36, w - mx - 100, band_h - 8), series, f_ser, on_stripe)
        _draw_text_block(draw, (mx, band_h + 20, w - mx, band_h + 80), hc, f_head, body_c)
        _draw_text_block(draw, (mx, band_h + 100, w - mx, h - br - 40), slide.get("title", ""), f_st, tone_title, align="center")
    elif role == "closing":
        f_t = _font(_scale(sizes.get("closing_main", 34), fs), bold=True)
        f_f = _font(_scale(sizes.get("footnote", 18), fs))
        title = slide.get("title", "")
        bullets = slide.get("bullets") or []
        block = title
        if bullets:
            block = block + "\n\n" + "\n".join(bullets)
        y1, y2 = my + 80, h - br - 50
        _draw_text_block(draw, (mx + 16, y1, w - mx - 16, y2), block, f_t, tone_title, align="center")
        if slide.get("footnote"):
            _draw_text_block(
                draw,
                (mx, h - br - 40, w - mx, h - 8),
                str(slide["footnote"]),
                f_f,
                foot_c,
                align="center",
            )
    else:
        # 본문 — 템플릿2 §1, §6
        stripe_h = int(layout.get("top_stripe_height", 6))
        draw.rectangle([0, 0, w, stripe_h], fill=stripe)

        f_series = _font(_scale(sizes.get("series_capsule", 23), fs), bold=True)
        f_hc = _font(_scale(sizes.get("head_copy_small", 21), fs))
        f_main = _font(_scale(sizes.get("main_title", 48), fs), bold=True)
        f_bul = _font(_scale(sizes.get("bullet", 31), fs))
        f_fn = _font(_scale(sizes.get("footnote", 20), fs))

        cy = my
        series = plan.get("series_title", "") or "시리즈"
        used = _draw_capsule_label(draw, mx, cy, series, f_series, cap_tx, cap_bg, cap_r)
        cy += used
        hc = plan.get("head_copy", "")
        if hc:
            _draw_text_block(draw, (mx, cy, w - mx, cy + 40), hc, f_hc, subtle)
            cy += 36

        content_top = cy + 12
        if options.body_layout == "white_card":
            cx = int(layout.get("white_card_margin_x", 52))
            cty = int(layout.get("white_card_top", 168))
            cw_ = int(layout.get("white_card_width", 696))
            ch_ = int(layout.get("white_card_height", 720))
            cr = int(layout.get("white_card_radius", 26))
            _rounded_rect(
                draw,
                (cx, cty, cx + cw_, cty + ch_),
                cr,
                fill=pal.get("card_white", "#FFFFFF"),
                outline="#E8EBE4",
                width=1,
            )
            inner_mx, inner_my = cx + 24, cty + 28
            inner_w = cx + cw_ - 24
            _draw_text_block(
                draw,
                (inner_mx, inner_my, inner_w, cty + ch_ - 24),
                slide.get("title", ""),
                f_main,
                tone_title,
            )
            yb = inner_my + _line_height(f_main) * 3
            for b in slide.get("bullets") or []:
                line = f"· {b}"
                _draw_text_block(draw, (inner_mx, yb, inner_w, yb + 120), line, f_bul, body_c)
                yb += min(100, _line_height(f_bul) * 2 + 16)
                if yb > cty + ch_ - 50:
                    break
        else:
            _draw_text_block(draw, (mx, content_top, w - mx, content_top + 140), slide.get("title", ""), f_main, tone_title)
            yb = content_top + 150
            for b in slide.get("bullets") or []:
                line = f"· {b}"
                _draw_text_block(draw, (mx, yb, w - mx, yb + 110), line, f_bul, body_c)
                yb += min(96, _line_height(f_bul) * 2 + 12)
                if yb > h - br - 40:
                    break
            if slide.get("footnote"):
                _draw_text_block(draw, (mx, h - br - 32, w - mx, h - 10), str(slide["footnote"]), f_fn, foot_c)

        if options.body_layout == "white_card" and slide.get("footnote"):
            _draw_text_block(draw, (mx, h - br - 28, w - mx, h - 6), str(slide["footnote"]), f_fn, foot_c)

    _paste_logo(img, options.logo_png, theme, options.logo_position)
    if not illustration_png:
        _paste_character(img, options.character_png, theme)
    return img


def render_plan_to_jpegs(
    plan_dict: dict[str, Any],
    out_dir: Path,
    options: RenderOptions,
    *,
    quality: int = 95,
    progress_callback: Any | None = None,
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    theme = _load_theme(options.theme_id)
    paths: list[Path] = []
    slides = plan_dict.get("slides") or []
    total = len(slides)

    for i, slide in enumerate(slides):
        img = render_slide(
            slide,
            plan=plan_dict,
            theme=theme,
            options=options,
            illustration_png=None,
        )
        role = slide.get("role", "body")
        fname = f"{i + 1:02d}_{role}.jpg"
        p = out_dir / fname
        img.save(p, format="JPEG", quality=quality, optimize=True)
        paths.append(p)
        if progress_callback is not None:
            progress_callback(i + 1, total)
    return paths


def list_theme_ids() -> list[str]:
    if not THEMES_DIR.exists():
        return ["mofe_body"]
    ids = sorted(p.stem for p in THEMES_DIR.glob("*.yaml"))
    if "mofe_body" in ids:
        ids.remove("mofe_body")
        return ["mofe_body"] + ids
    return ids

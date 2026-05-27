"""Date stamp PNG rendering with handwriting-style jitter.

The rendered PNG is a transparent overlay matching the stamp pipeline format,
so it can be composited / dragged / resized just like an image asset.

Design:
  - Use Klee One TTF as base (handwriting-style CJK font; fall back to system
    fonts if missing).
  - Per-character jitter: rotation +/- 2 deg, x/y offset +/- 0.5px,
    scale +/- 5%, alpha 88-100% (ink density variance).
  - These four jitter passes together fake the "drawn by hand" feel.
"""
from __future__ import annotations

import io
import random
from datetime import date
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont

_FONT_DIR = Path(__file__).parent / "fonts"
_KLEE_PATH = _FONT_DIR / "KleeOne-Regular.ttf"
_LXGW_PATH = _FONT_DIR / "LXGWWenKaiTC-Regular.ttf"

# Font fallback chain — system CJK
_SYSTEM_CJK_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",                  # macOS
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/msjh.ttc",                            # Windows
]

# Supported font style ids → bundled path or None for system fallback
_FONT_BUNDLES = {
    "lxgw": _LXGW_PATH,    # default — Lxgw 文楷 TC, brushy handwriting feel
    "klee": _KLEE_PATH,    # Japanese pencil handwriting
}


def _load_font(font_style: str, size_px: int) -> ImageFont.FreeTypeFont:
    """Load font by style preference. Falls back to system CJK if bundle missing."""
    bundled = _FONT_BUNDLES.get(font_style or "lxgw")
    if bundled and bundled.exists():
        try:
            return ImageFont.truetype(str(bundled), size_px)
        except Exception:
            pass
    for p in _SYSTEM_CJK_CANDIDATES:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size_px)
            except Exception:
                continue
    return ImageFont.load_default()


# Weight name -> PIL stroke_width (simulated thickening on top of glyph outline).
# Pillow >= 8.0 supports stroke_width param on ImageDraw.Draw.text().
_WEIGHT_TO_STROKE = {
    "light":   0,      # native outline (often appears thin for handwriting fonts)
    "regular": 0,
    "medium":  1,
    "bold":    2,
    "heavy":   3,
}


def _apply_handwriting_texture(img: Image.Image, level: str = "medium") -> Image.Image:
    """Add handwriting-style edge roughness + ink gaps to a rendered RGBA image.

    Effects layered:
      1. Edge fuzz - alpha randomly reduced on stroke boundary pixels
         (simulates ink bleeding / paper texture absorption)
      2. Interior gaps - sparse tiny holes inside strokes
         (simulates dry pen / pen lifted briefly)
      3. Light Gaussian blur on result (~0.3 px) so edge noise looks soft
         instead of pixelated

    level: 'none' / 'light' / 'medium' / 'heavy'.
    """
    if level == "none" or not level:
        return img
    try:
        import numpy as np
    except Exception:
        return img  # numpy not available -> skip

    if level == "light":
        edge_fade_prob, hole_prob = 0.18, 0.0030
        fade_min = 0.55
        blur_r = 0.25
    elif level == "heavy":
        edge_fade_prob, hole_prob = 0.45, 0.0130
        fade_min = 0.10
        blur_r = 0.40
    else:  # medium (default)
        edge_fade_prob, hole_prob = 0.30, 0.0060
        fade_min = 0.30
        blur_r = 0.30

    arr = np.array(img)
    if arr.shape[-1] != 4:
        return img  # not RGBA
    alpha = arr[:, :, 3].copy()

    # Edge mask: alpha > 50 AND has a transparent (alpha == 0) neighbor in 4-conn
    neighbor_t = np.zeros_like(alpha, dtype=bool)
    neighbor_t[:-1, :] |= (alpha[1:, :] == 0)
    neighbor_t[1:,  :] |= (alpha[:-1, :] == 0)
    neighbor_t[:, :-1] |= (alpha[:, 1:] == 0)
    neighbor_t[:, 1:]  |= (alpha[:, :-1] == 0)
    is_edge = (alpha > 50) & neighbor_t

    rng = np.random.default_rng()
    rand1 = rng.random(alpha.shape)

    # Edge fuzz: drop alpha on selected edge pixels
    fade_mask = is_edge & (rand1 < edge_fade_prob)
    fade_factor = rng.uniform(fade_min, 1.0, alpha.shape)
    alpha_f = alpha.astype(np.float32)
    alpha_f[fade_mask] = alpha_f[fade_mask] * fade_factor[fade_mask]
    alpha = np.clip(alpha_f, 0, 255).astype(np.uint8)

    # Interior holes: tiny gaps inside strokes (simulates dry ink)
    interior = (alpha > 200)
    hole_mask = interior & (rng.random(alpha.shape) < hole_prob)
    alpha[hole_mask] = 0

    arr[:, :, 3] = alpha
    out = Image.fromarray(arr, "RGBA")
    if blur_r > 0:
        out = out.filter(ImageFilter.GaussianBlur(radius=blur_r))
    return out


def format_date(d: date, fmt: str) -> str:
    """Format a date according to UI preset."""
    if fmt == "iso":           # 2026-05-26
        return d.strftime("%Y-%m-%d")
    if fmt == "iso-slash":     # 2026/05/26
        return d.strftime("%Y/%m/%d")
    if fmt == "cjk":           # 2026年05月26日
        return f"{d.year}年{d.month:02d}月{d.day:02d}日"
    if fmt == "roc":           # 民國 115 年 05 月 26 日
        roc_y = d.year - 1911
        return f"民國 {roc_y} 年 {d.month:02d} 月 {d.day:02d} 日"
    return d.strftime("%Y-%m-%d")


def render_date_png(
    text: str,
    *,
    font_style: str = "lxgw",
    weight: str = "regular",
    font_size_px: int = 72,
    color_hex: str = "#1e293b",
    jitter: bool = True,
    texture: str = "medium",
    seed: Optional[int] = None,
) -> tuple[bytes, int, int]:
    """Render a date / short text string as a transparent PNG with optional
    handwriting-style jitter.

    Returns: (png_bytes, width_px, height_px)

    The output PNG has alpha so it composites cleanly over the PDF (or another
    stamp PNG) without showing a white box.

    jitter=True applies 4-layer randomization:
      - rotation +/- 2 deg per char
      - x/y offset +/- 0.5 px per char
      - scale 95-105% per char
      - alpha 88-100% per char (ink density variance)
    """
    if seed is not None:
        random.seed(seed)
    rgb = _hex_to_rgb(color_hex)
    font = _load_font(font_style, font_size_px)
    stroke_w = _WEIGHT_TO_STROKE.get(weight or "regular", 0)

    if not text:
        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        buf = io.BytesIO(); img.save(buf, "PNG")
        return buf.getvalue(), 1, 1

    # Font line metrics — used to align each char by BASELINE (not glyph top).
    # Without this fix, characters with high bbox top (like '-' near cap height)
    # would render above the baseline, looking misaligned vs digits / letters
    # that extend to the bottom of the line.
    ascent, descent = font.getmetrics()
    line_h = ascent + descent

    # Generous padding so jitter rotation / stroke widening doesn't clip
    pad = font_size_px // 3
    # Measure per-char width using base font's advance (consistent spacing)
    char_widths: list[int] = []
    for ch in text:
        bbox = font.getbbox(ch)
        char_widths.append(bbox[2] - bbox[0])
    total_w = sum(char_widths) + pad * 2
    total_h = line_h + pad * 2

    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    baseline_y = pad + ascent  # baseline Y in main canvas

    cursor_x = pad
    for ch, cw in zip(text, char_widths):
        if jitter:
            rot_deg = random.uniform(-2.0, 2.0)
            offset_x = random.uniform(-0.5, 0.5)
            offset_y = random.uniform(-0.5, 0.5)
            scale = random.uniform(0.95, 1.05)
            alpha = random.randint(225, 255)  # 88-100% of 255
        else:
            rot_deg = offset_x = offset_y = 0.0
            scale = 1.0
            alpha = 255

        char_size_px = max(8, int(font_size_px * scale))
        char_font = _load_font(font_style, char_size_px)
        char_ascent, char_descent = char_font.getmetrics()
        char_bbox = char_font.getbbox(ch)
        cw_s = char_bbox[2] - char_bbox[0]
        margin = char_size_px // 2

        # Per-char canvas: width = glyph width + margin; height = full
        # ascent + descent + margin so baseline placement is well-defined.
        ch_canvas_w = cw_s + 2 * margin
        ch_canvas_h = char_ascent + char_descent + 2 * margin
        char_canvas = Image.new("RGBA", (ch_canvas_w, ch_canvas_h), (0, 0, 0, 0))
        char_draw = ImageDraw.Draw(char_canvas)
        # PIL `text((x, y), ...)` interprets y as TOP of the glyph row (i.e.
        # the line's top = baseline - ascent). To place baseline at
        # (margin + char_ascent), pass y = margin and let the font draw down.
        text_x = margin - char_bbox[0]
        text_y = margin
        try:
            char_draw.text((text_x, text_y), ch,
                            font=char_font,
                            fill=(rgb[0], rgb[1], rgb[2], alpha),
                            stroke_width=stroke_w,
                            stroke_fill=(rgb[0], rgb[1], rgb[2], alpha))
        except TypeError:
            char_draw.text((text_x, text_y), ch,
                            font=char_font,
                            fill=(rgb[0], rgb[1], rgb[2], alpha))
        if rot_deg:
            char_canvas = char_canvas.rotate(rot_deg, resample=Image.BICUBIC,
                                              expand=False)

        # Paste so that this char's baseline aligns with main canvas baseline.
        # Main baseline is at `baseline_y`. In char_canvas the baseline is at
        # `margin + char_ascent`. So paste_y = baseline_y - (margin + char_ascent).
        paste_x = int(cursor_x - margin + offset_x)
        paste_y = int(baseline_y - (margin + char_ascent) + offset_y)
        canvas.alpha_composite(char_canvas, (paste_x, paste_y))
        cursor_x += cw

    # Apply handwriting texture (edge fuzz + ink gaps) for organic feel
    if texture and texture != "none":
        canvas = _apply_handwriting_texture(canvas, level=texture)

    # Crop to actual content bbox to minimize file size + match render dim
    cropped = _crop_to_content(canvas)
    buf = io.BytesIO()
    cropped.save(buf, "PNG", optimize=True)
    return buf.getvalue(), cropped.width, cropped.height


def _crop_to_content(img: Image.Image) -> Image.Image:
    """Trim transparent edges so the resulting PNG has tight bbox."""
    bbox = img.getbbox()
    if not bbox:
        return img
    # Pad 4 px so antialiasing edges don't touch the border
    p = 4
    x0, y0, x1, y1 = bbox
    x0 = max(0, x0 - p); y0 = max(0, y0 - p)
    x1 = min(img.width, x1 + p); y1 = min(img.height, y1 + p)
    return img.crop((x0, y0, x1, y1))


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

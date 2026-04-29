"""Apply a PNG watermark to every page of a PDF.

Two layout modes:
  • single  — one watermark at a fixed (x, y) and size
  • tile    — repeat the watermark across the page in a grid

The watermark is rasterised into the page's content stream via
``page.insert_image``, so the result is part of the page graphics — viewers
cannot select-and-delete it like an annotation. Opacity is baked into the
PNG by multiplying its alpha channel before insertion.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Optional

import fitz
from PIL import Image, ImageDraw, ImageFont

from ...core.unit_convert import pt_to_mm, rect_mm_to_pt_topleft


@dataclass
class WatermarkParams:
    mode: str = "tile"           # "tile" | "single"
    opacity: float = 0.25        # 0..1
    rotation_deg: float = 30.0   # clockwise degrees

    # single mode
    x_mm: float = 80.0
    y_mm: float = 130.0
    width_mm: float = 50.0
    height_mm: float = 50.0

    # tile mode
    # ``tile_size_mm`` is the long-edge size; the short edge is derived from
    # the image's natural aspect ratio so logos don't get squashed. The legacy
    # tile_w_mm/tile_h_mm fields are kept as fallbacks for older callers.
    tile_size_mm: float = 60.0
    tile_w_mm: float = 0.0
    tile_h_mm: float = 0.0
    gap_mm: float = 30.0          # horizontal & vertical gap between tiles

    # text source (when watermark is rendered from typed text instead of an
    # asset image). When ``text`` is non-empty the caller passes ``None`` for
    # the PNG path and the service rasterises the string here.
    text: str = ""
    text_color: str = "#cc0000"
    text_size_pt: float = 48.0
    text_font: str = ""           # path to a TTF/OTF; "" → built-in fallback
    text_bold: bool = False
    text_italic: bool = False
    text_underline: bool = False

    pages: Optional[list[int]] = None


# Per-style font search lists. PingFang/微軟正黑/Noto have separate weight
# files; Pillow can't synthesise bold/italic from a single TTC, so we hunt
# for the right physical file.
_CJK_FONT_LISTS = {
    "regular": [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/mingliu.ttc",
        "C:/Windows/Fonts/arialuni.ttf",
    ],
    "bold": [
        "/System/Library/Fonts/PingFang.ttc",  # variant via index handled below
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/msjhbd.ttc",
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    "italic": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "C:/Windows/Fonts/ariali.ttf",
    ],
    "bold_italic": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        "C:/Windows/Fonts/arialbi.ttf",
    ],
}


def _has_cjk(text: str) -> bool:
    """Return True if the string contains any CJK ideograph or kana."""
    for ch in text or "":
        c = ord(ch)
        # CJK Unified, Extension A, Hangul, Kana, fullwidth — covers all East Asian
        if (0x3040 <= c <= 0x30FF or  # Kana
            0x3400 <= c <= 0x4DBF or  # CJK Ext A
            0x4E00 <= c <= 0x9FFF or  # CJK Unified
            0xAC00 <= c <= 0xD7AF or  # Hangul
            0xF900 <= c <= 0xFAFF or  # CJK Compat
            0xFF00 <= c <= 0xFFEF):   # Halfwidth & Fullwidth
            return True
    return False


def _font_covers_cjk(font: ImageFont.FreeTypeFont) -> bool:
    """Test whether the loaded face actually has glyphs for a representative
    CJK char. Pillow happily loads non-CJK fonts (Helvetica, Arial) and renders
    missing chars as the .notdef glyph (a hollow rectangle / tofu).

    We check via the font's underlying TT cmap when possible; fall back to
    measuring `getbbox("中")` width — for missing glyph the width tends to
    be 0 or oddly small."""
    try:
        # Fast path: ask freetype for the glyph index of '中'. 0 = .notdef.
        gid = font.getmask("中").size
        if gid == (0, 0):
            return False
    except Exception:
        pass
    try:
        bbox = font.getbbox("中")
        # Real CJK glyph is roughly square; missing glyph is usually width=0
        # or just a thin rectangle outline.
        return bbox[2] - bbox[0] > 4
    except Exception:
        return True  # don't reject if we can't measure


def _load_font(
    font_path: str, size_px: int, bold: bool = False, italic: bool = False,
    text: str = "",
) -> ImageFont.FreeTypeFont:
    """Load a font. If `text` contains CJK chars, the returned font is
    guaranteed (best-effort) to actually render those — Pillow doesn't
    auto-fallback so we have to walk the candidate list ourselves."""
    needs_cjk = _has_cjk(text)
    # First try the user-specified font. If text is CJK and it lacks CJK
    # glyphs, ignore it and walk the CJK fallback list.
    if font_path:
        try:
            f = ImageFont.truetype(font_path, size_px)
            if not needs_cjk or _font_covers_cjk(f):
                return f
        except Exception:
            pass
    keys: list[str] = []
    if bold and italic: keys.append("bold_italic")
    if bold: keys.append("bold")
    if italic: keys.append("italic")
    keys.append("regular")
    for k in keys:
        for cand in _CJK_FONT_LISTS.get(k, []):
            try:
                f = ImageFont.truetype(cand, size_px)
                if not needs_cjk or _font_covers_cjk(f):
                    return f
            except Exception:
                continue
    # Last-resort: even if it can't render CJK, return something usable.
    if font_path:
        try:
            return ImageFont.truetype(font_path, size_px)
        except Exception:
            pass
    return ImageFont.load_default()


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    h = (hex_color or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r = int(h[0:2], 16); g = int(h[2:4], 16); b = int(h[4:6], 16)
    except Exception:
        r, g, b = 0, 0, 0
    return (r, g, b, alpha)


def _render_text_png(
    text: str, color: str, size_pt: float, font_path: str,
    bold: bool = False, italic: bool = False, underline: bool = False,
) -> tuple[bytes, float, float]:
    """Render ``text`` to a transparent PNG. Returns (png_bytes, w_mm, h_mm)
    where mm uses 96 dpi mapping (matches existing image_natural_size_mm)."""
    # Convert pt → px at a high render DPI for crispness, then scale via mm.
    render_dpi = 300
    size_px = max(8, int(round(size_pt * render_dpi / 72.0)))
    font = _load_font(font_path, size_px, bold=bold, italic=italic, text=text)
    # Measure first.
    tmp = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(tmp)
    bbox = d.textbbox((0, 0), text, font=font)
    tw = max(2, bbox[2] - bbox[0])
    th = max(2, bbox[3] - bbox[1])
    pad = max(4, size_px // 10)
    im = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    rgba = _hex_to_rgba(color, 255)
    # Faux-italic via shear when no italic font is available — rendered
    # font's italic flag isn't reliable, so always shear when requested.
    target_im = im
    if italic:
        target_im = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(target_im)
    d.text((pad - bbox[0], pad - bbox[1]), text, font=font, fill=rgba)
    if underline:
        # Underline thickness scales with the size; draw a simple rectangle.
        thick = max(2, size_px // 18)
        y = pad - bbox[1] + th + max(2, size_px // 12)
        d.rectangle((pad, y, pad + tw, y + thick), fill=rgba)
    if italic:
        # Skew horizontally by ~14° (tan ≈ 0.25). Pillow's affine: (a,b,c,d,e,f)
        # → x' = a*x + b*y + c. Negative b shears top to the right.
        shear = -0.25
        out_w = im.size[0] + int(abs(shear) * im.size[1])
        skewed = target_im.transform(
            (out_w, im.size[1]), Image.AFFINE,
            (1, shear, abs(shear) * im.size[1], 0, 1, 0),
            resample=Image.BICUBIC,
        )
        im = skewed
    buf = BytesIO()
    im.save(buf, format="PNG")
    # Map render-dpi pixels back to mm at 72pt-pt (since the user gave size
    # in pt). 1 pt = 25.4/72 mm. The rendered image's typographic size_pt
    # corresponds to size_px at render_dpi → so 1 px = 25.4 / render_dpi mm.
    mm_per_px = 25.4 / render_dpi
    return buf.getvalue(), im.size[0] * mm_per_px, im.size[1] * mm_per_px


def _prepare_image(png_path: Path, opacity: float, rotation_deg: float) -> bytes:
    """Return PNG bytes with alpha multiplied by opacity and rotated."""
    with Image.open(png_path) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        if 0.0 < opacity < 1.0:
            r, g, b, a = im.split()
            a = a.point(lambda v: int(v * opacity))
            im = Image.merge("RGBA", (r, g, b, a))
        if abs(rotation_deg) > 0.01:
            im = im.rotate(-rotation_deg, resample=Image.BICUBIC, expand=True)
        buf = BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()


def _rotated_size(w_mm: float, h_mm: float, rotation_deg: float) -> tuple[float, float]:
    if abs(rotation_deg) < 0.01:
        return w_mm, h_mm
    theta = math.radians(rotation_deg)
    c = abs(math.cos(theta)); s = abs(math.sin(theta))
    return w_mm * c + h_mm * s, h_mm * c + w_mm * s


def apply_watermark(
    src_pdf: Path,
    dst_pdf: Path,
    watermark_png: Optional[Path],
    params: WatermarkParams,
) -> None:
    """``watermark_png`` may be None when ``params.text`` is set — the source
    image is then rendered from the typed text on the fly."""
    if params.text and params.text.strip():
        text_bytes, text_w_mm, text_h_mm = _render_text_png(
            params.text, params.text_color, params.text_size_pt, params.text_font,
            bold=params.text_bold, italic=params.text_italic,
            underline=params.text_underline,
        )
        # Apply opacity + rotation to the rendered text the same way as for
        # asset images (re-uses the same alpha-multiplied PNG path).
        from io import BytesIO as _BIO
        from PIL import Image as _Img, ImageChops as _IC
        with _Img.open(_BIO(text_bytes)) as im:
            if 0.0 < params.opacity < 1.0:
                r, g, b, a = im.split()
                a = a.point(lambda v: int(v * params.opacity))
                im = _Img.merge("RGBA", (r, g, b, a))
            if abs(params.rotation_deg) > 0.01:
                im = im.rotate(-params.rotation_deg, resample=_Img.BICUBIC, expand=True)
            buf = _BIO(); im.save(buf, format="PNG")
            img_bytes = buf.getvalue()
        # Override the placement footprint to the natural text size when in
        # single mode so we don't stretch the text. In tile mode, override
        # tile_w / tile_h similarly.
        if params.mode == "single":
            params.width_mm = text_w_mm
            params.height_mm = text_h_mm
        else:
            params.tile_w_mm = text_w_mm
            params.tile_h_mm = text_h_mm
    else:
        if watermark_png is None:
            raise ValueError("either text or watermark_png must be provided")
        img_bytes = _prepare_image(watermark_png, params.opacity, params.rotation_deg)
        # Derive tile_w/h from the image's natural aspect ratio so logos
        # aren't stretched. ``tile_size_mm`` is the long edge.
        if params.mode == "tile":
            try:
                with Image.open(watermark_png) as _im:
                    nw, nh = _im.size
                aspect = (nw / nh) if nh else 1.0
            except Exception:
                aspect = 1.0
            long_mm = params.tile_size_mm if params.tile_size_mm > 0 else max(
                params.tile_w_mm or 0, params.tile_h_mm or 0, 60.0
            )
            if aspect >= 1.0:
                params.tile_w_mm = long_mm
                params.tile_h_mm = long_mm / aspect
            else:
                params.tile_h_mm = long_mm
                params.tile_w_mm = long_mm * aspect
        # Same idea for single mode: keep aspect when only a long edge is set.
        if params.mode == "single":
            try:
                with Image.open(watermark_png) as _im:
                    nw, nh = _im.size
                aspect = (nw / nh) if nh else 1.0
            except Exception:
                aspect = 1.0
            # Only auto-fit when the caller hasn't given explicit width/height.
            if (params.width_mm <= 0 or params.height_mm <= 0):
                long_mm = max(params.width_mm, params.height_mm, params.tile_size_mm or 60.0)
                if aspect >= 1.0:
                    params.width_mm = long_mm; params.height_mm = long_mm / aspect
                else:
                    params.height_mm = long_mm; params.width_mm = long_mm * aspect
    if params.mode == "tile":
        # Use the rotated bounding box for placement so tiles don't visually
        # collide when angled.
        tile_w, tile_h = _rotated_size(
            params.tile_w_mm, params.tile_h_mm, params.rotation_deg
        )
    else:
        tile_w = tile_h = 0.0  # unused

    with fitz.open(str(src_pdf)) as doc:
        for i, page in enumerate(doc):
            if params.pages is not None and i not in params.pages:
                continue
            page_w_mm = pt_to_mm(page.rect.width)
            page_h_mm = pt_to_mm(page.rect.height)
            if params.mode == "single":
                w, h = _rotated_size(
                    params.width_mm, params.height_mm, params.rotation_deg
                )
                # Centre the rotated bbox on the user-given (x, y) anchor —
                # which represents the visual centre, not the top-left, so
                # rotation doesn't drift the watermark off the slot.
                cx = params.x_mm + params.width_mm / 2
                cy = params.y_mm + params.height_mm / 2
                x0_mm = cx - w / 2
                y0_mm = cy - h / 2
                _draw(page, img_bytes, x0_mm, y0_mm, w, h, page_h_mm)
            else:
                step_x = max(2.0, tile_w + params.gap_mm)
                step_y = max(2.0, tile_h + params.gap_mm)
                # Stagger every other row by half a step for a denser look.
                row = 0
                y = -tile_h / 2
                while y < page_h_mm + tile_h:
                    x_start = -tile_w / 2 + (step_x / 2 if row % 2 else 0)
                    x = x_start
                    while x < page_w_mm + tile_w:
                        _draw(page, img_bytes, x, y, tile_w, tile_h, page_h_mm)
                        x += step_x
                    y += step_y
                    row += 1
        doc.save(str(dst_pdf), garbage=3, deflate=True)


def _draw(
    page: fitz.Page, img: bytes,
    x_mm: float, y_mm: float, w_mm: float, h_mm: float, page_h_mm: float,
) -> None:
    x0, y0, x1, y1 = rect_mm_to_pt_topleft(x_mm, y_mm, w_mm, h_mm, page_h_mm)
    rect = fitz.Rect(x0, y0, x1, y1)
    page.insert_image(rect, stream=img, overlay=True, keep_proportion=False)

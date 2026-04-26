from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops


def ensure_rgba_png(src: Path, dst: Path) -> None:
    """Normalize an input image into an RGBA PNG written to dst."""
    with Image.open(src) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        im.save(dst, format="PNG")


def make_thumbnail_png(src: Path, dst: Path, max_size: int = 256) -> None:
    with Image.open(src) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        im.thumbnail((max_size, max_size))
        im.save(dst, format="PNG")


def image_natural_size_mm(src: Path, dpi: float = 96.0) -> tuple[float, float]:
    """Best-effort physical size in mm; falls back to 96 dpi when image has no DPI metadata."""
    from .unit_convert import px_to_mm

    with Image.open(src) as im:
        w_px, h_px = im.size
        info_dpi = im.info.get("dpi")
        if info_dpi and info_dpi[0] and info_dpi[1]:
            return px_to_mm(w_px, info_dpi[0]), px_to_mm(h_px, info_dpi[1])
    return px_to_mm(w_px, dpi), px_to_mm(h_px, dpi)


def remove_white_background(
    src: Path,
    dst: Path,
    chroma_low: int = 25,
    chroma_soft: int = 25,
    shadow_tolerance: int = 60,
    dark_softness: int = 50,
) -> None:
    """Map paper-background pixels to transparent, keeping ink (colored or dark).

    Treats a pixel as foreground if EITHER:
      • it has noticeable color (chroma = max(R,G,B) - min(R,G,B) above
        chroma_low), which catches red/blue chops and other colored inks, OR
      • it is significantly darker than the paper (max(R,G,B) drops more than
        shadow_tolerance below the paper's brightness), which catches black
        pen/pencil signatures.
    Both conditions are linearly ramped to preserve antialiased edges. The
    paper brightness reference is auto-sampled from the four corners (median
    per channel) so off-white phone photos work as well as white scans, and
    even-toned shadow gradients on the paper are not mistaken for ink.
    """
    with Image.open(src) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        w, h = im.size
        patch = max(4, min(w, h) // 40)
        rgb = im.convert("RGB")
        samples = []
        for x0, y0 in ((0, 0), (w - patch, 0), (0, h - patch), (w - patch, h - patch)):
            samples.extend(rgb.crop((x0, y0, x0 + patch, y0 + patch)).getdata())
        n = len(samples)
        # Median per channel is robust if 1–2 corners overlap the artwork.
        paper_max = max(sorted(s[c] for s in samples)[n // 2] for c in range(3))

        r, g, b, a = im.split()
        ch_max = ImageChops.lighter(ImageChops.lighter(r, g), b)
        ch_min = ImageChops.darker(ImageChops.darker(r, g), b)
        chroma = ImageChops.subtract(ch_max, ch_min)

        chroma_high = chroma_low + max(1, chroma_soft)

        def chroma_ramp(v: int) -> int:
            if v <= chroma_low:
                return 0
            if v >= chroma_high:
                return 255
            return int(round(255 * (v - chroma_low) / (chroma_high - chroma_low)))

        dark_high = shadow_tolerance + max(1, dark_softness)

        def dark_ramp(v: int) -> int:
            drop = paper_max - v
            if drop <= shadow_tolerance:
                return 0
            if drop >= dark_high:
                return 255
            return int(round(255 * (drop - shadow_tolerance) / (dark_high - shadow_tolerance)))

        color_alpha = chroma.point(chroma_ramp)
        dark_alpha = ch_max.point(dark_ramp)
        # Foreground if either rule fires — take the brighter of the two masks.
        fg_alpha = ImageChops.lighter(color_alpha, dark_alpha)
        new_alpha = ImageChops.darker(fg_alpha, a)
        im.putalpha(new_alpha)
        # Auto-crop the transparent border so signatures and stamps fill the
        # image instead of sitting in a blank canvas. Use a small alpha
        # threshold so anti-aliased edges aren't trimmed.
        bbox = new_alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
        if bbox is not None:
            pad = max(2, min(w, h) // 200)
            x0 = max(0, bbox[0] - pad)
            y0 = max(0, bbox[1] - pad)
            x1 = min(w, bbox[2] + pad)
            y1 = min(h, bbox[3] + pad)
            if (x1 - x0) > 0 and (y1 - y0) > 0 and (x1 - x0, y1 - y0) != (w, h):
                im = im.crop((x0, y0, x1, y1))
        im.save(dst, format="PNG")


def png_bytes(path: Path) -> bytes:
    with Image.open(path) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        buf = BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue()

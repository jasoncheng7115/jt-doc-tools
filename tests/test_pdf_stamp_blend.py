"""Regression tests for the Multiply blend mode applied to pdf-stamp output.

Real ink chops are translucent: stamping over an existing rule or field line
must darken the line (the line shows through), not opaquely cover it with a
hard, unnatural edge. `pdf_utils.stamp_pdf(blend_multiply=True)` (the default)
attaches a `/BM /Multiply` ExtGState to the stamp image so:

  • over white paper   -> ink colour is preserved (blue x white = blue)
  • over a black line  -> the crossing darkens     (blue x black = ~black)

`blend_multiply=False` keeps the legacy opaque behaviour for comparison.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from app.core import pdf_utils as pu

# Stamp ink colour (an opaque solid bar on a transparent background).
INK = (40, 60, 200)


def _make_stamp_png(dst: Path) -> None:
    im = Image.new("RGBA", (200, 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    # A solid horizontal bar spanning the width — this is what crosses the line.
    d.rectangle([0, 50, 199, 70], fill=INK + (255,))
    im.save(dst, format="PNG")


def _make_target_pdf(dst: Path) -> None:
    """A page with one full-width black horizontal rule the stamp will cross."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_line(fitz.Point(0, 100), fitz.Point(300, 100), color=(0, 0, 0), width=3)
    doc.save(str(dst))
    doc.close()


def _render_top_page(pdf: Path) -> Image.Image:
    doc = fitz.open(str(pdf))
    pix = doc[0].get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
    img = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")
    doc.close()
    return img


def _darkest_in_band(img: Image.Image, frac_y: float) -> int:
    """Min luminance across a horizontal band at relative height frac_y."""
    w, h = img.size
    y = int(h * frac_y)
    band = [
        sum(img.getpixel((x, yy))) // 3
        for x in range(w)
        for yy in range(max(0, y - 2), min(h, y + 3))
    ]
    return min(band)


def test_multiply_lets_underlying_line_show_through(tmp_path):
    stamp = tmp_path / "stamp.png"
    src = tmp_path / "src.pdf"
    _make_stamp_png(stamp)
    _make_target_pdf(src)

    # Stamp covers the full width at the vertical centre, so the bar crosses the
    # black rule. Page is 300x200 pt -> mm via pt_to_mm.
    page_w_mm = pu.pt_to_mm(300.0)
    page_h_mm = pu.pt_to_mm(200.0)

    out_mul = tmp_path / "mul.pdf"
    out_opq = tmp_path / "opq.pdf"
    pu.stamp_pdf(src, out_mul, stamp, 0.0, 0.0, page_w_mm, page_h_mm, blend_multiply=True)
    pu.stamp_pdf(src, out_opq, stamp, 0.0, 0.0, page_w_mm, page_h_mm, blend_multiply=False)

    img_mul = _render_top_page(out_mul)
    img_opq = _render_top_page(out_opq)

    # Both files must remain valid single-page PDFs.
    assert fitz.open(str(out_mul)).page_count == 1
    assert fitz.open(str(out_opq)).page_count == 1

    # The bar sits at ~0.5 height (50..70 of 120 in the stamp, scaled), which is
    # exactly where the black rule is. Under Multiply the crossing must be much
    # darker than the opaque draw (the black line bleeds through the ink).
    dark_mul = _darkest_in_band(img_mul, 0.5)
    dark_opq = _darkest_in_band(img_opq, 0.5)
    assert dark_mul < dark_opq - 30, (dark_mul, dark_opq)


def test_multiply_preserves_ink_colour_over_white(tmp_path):
    """Over plain white paper, Multiply must not wash out the ink colour."""
    stamp = tmp_path / "stamp.png"
    src = tmp_path / "src.pdf"
    _make_stamp_png(stamp)
    # Blank white page, no line.
    doc = fitz.open()
    doc.new_page(width=300, height=200)
    doc.save(str(src))
    doc.close()

    out = tmp_path / "mul.pdf"
    pu.stamp_pdf(
        src, out, stamp, 0.0, 0.0, pu.pt_to_mm(300.0), pu.pt_to_mm(200.0),
        blend_multiply=True,
    )
    img = _render_top_page(out)
    # Sample the centre of the bar — should still read as the blue ink, i.e.
    # blue channel clearly dominates and it is not near-white.
    w, h = img.size
    r, g, b = img.getpixel((w // 2, h // 2))
    assert b > 120 and b > r + 40 and b > g + 40, (r, g, b)

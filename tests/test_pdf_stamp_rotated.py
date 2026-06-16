"""Regression: stamp placement must honour page /Rotate (GitHub #28 follow-up).

The editor positions the stamp in the page's DISPLAYED (rotated) coordinates,
but PyMuPDF's page.insert_image works in unrotated page space and ignores
/Rotate. So on a rotated page (e.g. A4 portrait scanned with /Rotate 90, shown
landscape) the exported/composite stamp landed in the wrong place + orientation
while the edit-mode preview showed it correctly — the two diverged. stamp_pdf
now maps the rect through page.derotation_matrix + counter-rotates the image.

We stamp a known DISPLAYED-space position on pages at every rotation and assert
the rendered stamp centroid lands where the editor would show it.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import fitz
import numpy as np
import pytest
from PIL import Image

from app.core import pdf_utils


def _red_png(p: Path):
    Image.new("RGBA", (100, 100), (220, 20, 20, 255)).save(p)


def _stamp_centroid_frac(rotation: int, x_mm: float, y_mm: float,
                         w_mm: float = 40, h_mm: float = 40):
    d = Path(tempfile.mkdtemp())
    sp = d / "stamp.png"; _red_png(sp)
    doc = fitz.open(); doc.new_page(width=595, height=841)  # A4 portrait pt
    if rotation:
        doc[0].set_rotation(rotation)
    src = d / "src.pdf"; doc.save(str(src)); doc.close()
    out = d / "out.pdf"
    pdf_utils.stamp_pdf(src, out, sp, x_mm=x_mm, y_mm=y_mm, w_mm=w_mm, h_mm=h_mm,
                        rotation_deg=0, blend_multiply=False)
    with fitz.open(str(out)) as dd:
        pix = dd[0].get_pixmap(dpi=100)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    arr = np.array(img); H, W, _ = arr.shape
    red = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 90) & (arr[:, :, 2] < 90)
    ys, xs = np.where(red)
    assert len(xs), f"stamp not found on rotation={rotation} (placed off-page?)"
    return xs.mean() / W, ys.mean() / H


# A4 = 210 x 297 mm. Displayed dims depend on rotation.
@pytest.mark.parametrize("rotation,disp_w,disp_h", [
    (0, 210, 297), (90, 297, 210), (180, 210, 297), (270, 297, 210),
])
def test_stamp_lands_at_displayed_position(rotation, disp_w, disp_h):
    # place near the top-left of the DISPLAYED page; stamp center = (50+20, 30+20)
    x_mm, y_mm = 50, 30
    cx, cy = _stamp_centroid_frac(rotation, x_mm, y_mm)
    exp_x = (x_mm + 20) / disp_w
    exp_y = (y_mm + 20) / disp_h
    assert abs(cx - exp_x) < 0.06, f"rot{rotation}: x {cx:.2f} != {exp_x:.2f}"
    assert abs(cy - exp_y) < 0.06, f"rot{rotation}: y {cy:.2f} != {exp_y:.2f}"


def test_rotated_page_not_placed_at_old_buggy_spot():
    # The pre-fix bug put a top-left (100,20) stamp on a /Rotate 90 page at
    # roughly (0.86, 0.57) instead of (0.40, 0.19). Guard against regressing.
    cx, cy = _stamp_centroid_frac(90, 100, 20)
    assert cx < 0.6 and cy < 0.4, f"rotated placement regressed: ({cx:.2f},{cy:.2f})"

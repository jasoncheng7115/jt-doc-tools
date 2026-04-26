from __future__ import annotations

from pathlib import Path

import fitz


def render_page_png(
    pdf_path: Path,
    out_png: Path,
    page_index: int = 0,
    dpi: int = 110,
) -> tuple[int, int]:
    """Render a single PDF page to PNG. Returns (width_px, height_px)."""
    with fitz.open(str(pdf_path)) as doc:
        page = doc[page_index]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(str(out_png))
        return pix.width, pix.height

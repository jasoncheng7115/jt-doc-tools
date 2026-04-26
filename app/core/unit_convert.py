MM_PER_INCH = 25.4
PT_PER_INCH = 72.0


def mm_to_pt(mm: float) -> float:
    return mm * PT_PER_INCH / MM_PER_INCH


def pt_to_mm(pt: float) -> float:
    return pt * MM_PER_INCH / PT_PER_INCH


def mm_to_px(mm: float, dpi: float = 96.0) -> float:
    return mm * dpi / MM_PER_INCH


def px_to_mm(px: float, dpi: float = 96.0) -> float:
    return px * MM_PER_INCH / dpi


def rect_mm_to_pt_topleft(
    x_mm: float,
    y_mm: float,
    w_mm: float,
    h_mm: float,
    page_h_mm: float,  # kept for API stability; unused — PyMuPDF uses top-left origin
) -> tuple[float, float, float, float]:
    """Convert a rect specified with top-left origin in mm to points, keeping
    top-left origin. PyMuPDF's `page.rect` and `fitz.Rect` treat (0,0) as the
    top-left corner with y growing downward, so no Y flip is needed.
    Returns (x0_pt, y0_pt, x1_pt, y1_pt) with y0 < y1."""
    del page_h_mm  # unused
    x0 = mm_to_pt(x_mm)
    y0 = mm_to_pt(y_mm)
    x1 = mm_to_pt(x_mm + w_mm)
    y1 = mm_to_pt(y_mm + h_mm)
    return x0, y0, x1, y1

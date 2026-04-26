"""Geometric layout analysis for PDF form pages.

Extracts horizontal / vertical line segments and (optionally) filled
rectangles from a page, then lets the detector look up "what cell is this
label inside?" by finding the nearest line on each side that brackets the
label's bounding box.

Used by :mod:`pdf_form_detect` to produce a cell rect alongside every
detected field, which the overlay then uses to:
  * clip text to the cell (no overflow),
  * auto-shrink the font size when the value is too long,
  * drop to the next line / below the label when still too long,
  * place check marks at the right x on top of their □ glyph.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import fitz  # PyMuPDF

try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except Exception:  # pragma: no cover — optional dependency
    _HAS_PDFPLUMBER = False


# A horizontal segment is (y, x_start, x_end); vertical is (x, y_start, y_end).
HLine = tuple[float, float, float]
VLine = tuple[float, float, float]
# A table cell: (x0, y0, x1, y1) with y0 = top, y1 = bottom (top-down).
Cell = tuple[float, float, float, float]

_LINE_EPS = 1.5  # px: collapse "nearly horizontal" / "nearly vertical"


def extract_lines(page: fitz.Page) -> tuple[list[HLine], list[VLine]]:
    """Return (horizontal, vertical) line segments on ``page``.

    Also decomposes any stroked rectangle into its four edges — common in
    PDFs where a single cell is drawn as an ``re`` op rather than four
    ``l`` ops, which would otherwise be invisible to us.
    """
    hs: list[HLine] = []
    vs: list[VLine] = []
    for draw in page.get_drawings():
        for item in draw.get("items", []):
            kind = item[0]
            if kind == "l":
                p1, p2 = item[1], item[2]
                x0, y0, x1, y1 = p1.x, p1.y, p2.x, p2.y
                if abs(y1 - y0) < _LINE_EPS and abs(x1 - x0) >= _LINE_EPS:
                    y = (y0 + y1) / 2
                    hs.append((y, min(x0, x1), max(x0, x1)))
                elif abs(x1 - x0) < _LINE_EPS and abs(y1 - y0) >= _LINE_EPS:
                    x = (x0 + x1) / 2
                    vs.append((x, min(y0, y1), max(y0, y1)))
            elif kind == "re":
                r = item[1]
                hs.append((r.y0, r.x0, r.x1))
                hs.append((r.y1, r.x0, r.x1))
                vs.append((r.x0, r.y0, r.y1))
                vs.append((r.x1, r.y0, r.y1))
    return hs, vs


def extract_cells_pdfplumber(pdf_path: Path) -> list[list[Cell]]:
    """Return table cells per page using pdfplumber's table detector.

    pdfplumber's grid extraction handles merged cells, nested rulings, and
    multi-row spans much better than our line-pairing algorithm. We use the
    result as the *primary* source of cells for find_enclosing_cell, falling
    back to the line-based algorithm when pdfplumber isn't available or
    finds no tables.
    """
    if not _HAS_PDFPLUMBER:
        return []
    out: list[list[Cell]] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                cells: list[Cell] = []
                for table in page.find_tables():
                    for row in table.rows:
                        for cell in row.cells:
                            if cell is None:
                                continue
                            x0, y0, x1, y1 = cell
                            # Drop zero-sized cells.
                            if x1 - x0 < 2 or y1 - y0 < 2:
                                continue
                            cells.append((float(x0), float(y0), float(x1), float(y1)))
                out.append(cells)
    except Exception:  # pragma: no cover — don't crash detection on weird PDFs
        return []
    return out


def extract_digit_box_clusters(
    cells: list[Cell], min_count: int = 6, max_count: int = 25
) -> list[list[Cell]]:
    """Return runs of adjacent narrow cells of similar width on the same row.

    Typical use: the "受款帳號" digit grid on Taiwan bank-authorisation
    forms — 10~20 drawn rectangles each holding one digit. Matches cells
    whose widths are within ±20% of each other, touching horizontally
    (gap ≤ 1pt), and whose y-range overlaps ≥80%.
    """
    if not cells:
        return []
    by_row: dict[tuple[int, int], list[Cell]] = {}
    for c in cells:
        key = (round(c[1]), round(c[3]))
        by_row.setdefault(key, []).append(c)
    out: list[list[Cell]] = []
    for row_cells in by_row.values():
        row_cells.sort(key=lambda c: c[0])
        run: list[Cell] = []
        for c in row_cells:
            if not run:
                run = [c]
                continue
            prev = run[-1]
            prev_w = prev[2] - prev[0]
            cur_w = c[2] - c[0]
            if cur_w <= 0 or prev_w <= 0:
                run = [c]
                continue
            w_ratio = min(cur_w, prev_w) / max(cur_w, prev_w)
            gap = c[0] - prev[2]
            if w_ratio >= 0.8 and abs(gap) <= 1.5:
                run.append(c)
            else:
                if min_count <= len(run) <= max_count:
                    out.append(run)
                run = [c]
        if min_count <= len(run) <= max_count:
            out.append(run)
    return out


def find_cell_containing(
    label_bbox: tuple[float, float, float, float], cells: list[Cell]
) -> Optional[Cell]:
    """Return the cell that owns ``label_bbox``.

    First tries strict containment. If no cell strictly contains the bbox
    (common when a printed label overflows its own cell boundary — e.g.
    "公司執照登記字號" running a few pt past its cell edge), falls back to
    the cell whose left edge is closest to the label's left edge, as long
    as the label's y-centre lies in that cell's y-range. This picks the
    label's origin cell and the right-adj cell then gives the proper value
    slot.
    """
    lx0, ly0, lx1, ly1 = label_bbox
    best: Optional[Cell] = None
    best_area = float("inf")
    for cx0, cy0, cx1, cy1 in cells:
        if cx0 - 0.5 <= lx0 and cx1 + 0.5 >= lx1 and cy0 - 0.5 <= ly0 and cy1 + 0.5 >= ly1:
            area = (cx1 - cx0) * (cy1 - cy0)
            if area < best_area:
                best_area = area
                best = (cx0, cy0, cx1, cy1)
    if best is not None:
        return best
    # Tolerant fallback: label overflows its cell to the right. Pick the
    # cell whose left edge matches the label's left edge and whose y-range
    # contains the label's y-centre.
    ly_mid = (ly0 + ly1) / 2
    best_dx = 8.0  # max allowed offset between label.x0 and cell.x0
    for cx0, cy0, cx1, cy1 in cells:
        if not (cy0 - 0.5 <= ly_mid <= cy1 + 0.5):
            continue
        dx = abs(cx0 - lx0)
        if dx > best_dx:
            continue
        # Prefer cells that actually start at or before the label.
        if cx0 > lx0 + 1:
            continue
        best = (cx0, cy0, cx1, cy1)
        best_dx = dx
    return best


def find_cell_right_of(
    cell: Cell, cells: list[Cell], min_width: float = 8.0,
    value_width_hint: float = 40.0,
) -> Optional[Cell]:
    """Pick the next cell to the right of ``cell`` that shares most of its y range.

    Prefers the first cell at least ``value_width_hint`` pt wide (skipping
    narrow marker columns), falling back to any cell ≥ ``min_width``.
    """
    cx0, cy0, cx1, cy1 = cell
    cand: list[Cell] = []
    for oc in cells:
        ox0, oy0, ox1, oy1 = oc
        if ox0 < cx1 - 0.5:
            continue
        overlap = max(0.0, min(cy1, oy1) - max(cy0, oy0))
        if overlap < (cy1 - cy0) * 0.5:
            continue
        cand.append(oc)
    cand.sort(key=lambda c: c[0])
    for c in cand:
        if c[2] - c[0] >= value_width_hint:
            return c
    for c in cand:
        if c[2] - c[0] >= min_width:
            return c
    return None


def find_cell_below_of(
    cell: Cell, cells: list[Cell], min_height: float = 8.0,
) -> Optional[Cell]:
    """Pick the next cell below ``cell`` that shares most of its x range."""
    cx0, cy0, cx1, cy1 = cell
    cand: list[Cell] = []
    for oc in cells:
        ox0, oy0, ox1, oy1 = oc
        if oy0 < cy1 - 0.5:
            continue
        overlap = max(0.0, min(cx1, ox1) - max(cx0, ox0))
        if overlap < (cx1 - cx0) * 0.5:
            continue
        cand.append(oc)
    cand.sort(key=lambda c: c[1])
    for c in cand:
        if c[3] - c[1] >= min_height:
            return c
    return None


def find_enclosing_cell(
    label_bbox: tuple[float, float, float, float],
    h_lines: list[HLine],
    v_lines: list[VLine],
    pad: float = 0.5,
) -> Optional[tuple[float, float, float, float]]:
    """Return the bounding rect of the smallest table cell containing
    ``label_bbox``, or None if the label isn't enclosed on all four sides.

    A side counts as "present" if a line exists on that side of the label
    whose span covers (most of) the perpendicular extent of the label.
    """
    lx0, ly0, lx1, ly1 = label_bbox
    lx0 -= pad
    ly0 -= pad
    lx1 += pad
    ly1 += pad

    # Top: H line with y < label.y0, whose x span brackets the label
    top = _nearest_h(h_lines, lx0, lx1, y_max=ly0, above=True)
    bot = _nearest_h(h_lines, lx0, lx1, y_min=ly1, above=False)
    left = _nearest_v(v_lines, ly0, ly1, x_max=lx0, leftward=True)
    right = _nearest_v(v_lines, ly0, ly1, x_min=lx1, leftward=False)
    if top is None or bot is None or left is None or right is None:
        return None
    return (left, top, right, bot)


def _nearest_h(
    lines: list[HLine],
    x0: float,
    x1: float,
    y_max: Optional[float] = None,
    y_min: Optional[float] = None,
    above: bool = True,
) -> Optional[float]:
    cand = []
    for y, sx, ex in lines:
        if y_max is not None and y >= y_max:
            continue
        if y_min is not None and y <= y_min:
            continue
        if sx <= x0 + 0.5 and ex >= x1 - 0.5:
            cand.append(y)
    if not cand:
        return None
    return max(cand) if above else min(cand)


def _nearest_v(
    lines: list[VLine],
    y0: float,
    y1: float,
    x_max: Optional[float] = None,
    x_min: Optional[float] = None,
    leftward: bool = True,
) -> Optional[float]:
    cand = []
    for x, sy, ey in lines:
        if x_max is not None and x >= x_max:
            continue
        if x_min is not None and x <= x_min:
            continue
        if sy <= y0 + 0.5 and ey >= y1 - 0.5:
            cand.append(x)
    if not cand:
        return None
    return max(cand) if leftward else min(cand)


def find_adjacent_cell_right(
    cell: tuple[float, float, float, float],
    h_lines: list[HLine],
    v_lines: list[VLine],
    min_width: float = 8.0,
    value_width_hint: float = 40.0,
) -> Optional[tuple[float, float, float, float]]:
    """Cell sharing its left edge with ``cell``'s right edge, if one exists.

    Some forms put a narrow "marker" column between the label and the
    actual value column (e.g. "(中) / (英)" row annotators). Preferring the
    first cell that is at least ``value_width_hint`` pt wide lets us skip
    those markers and land on the real value cell. If no cell meets that
    threshold (typical 2-column label/value tables), we fall back to the
    first cell at least ``min_width`` pt wide — which also skips the 1–2pt
    "gap" cells produced by double-stroked borders.
    """
    cx0, cy0, cx1, cy1 = cell
    margin = min(2.0, (cy1 - cy0) / 4)
    cand_xs = sorted(
        x for x, sy, ey in v_lines
        if x > cx1 + 0.5 and sy <= cy0 + margin and ey >= cy1 - margin
    )
    # Build the list of (left, right, width) for each adjacent cell.
    cells: list[tuple[float, float, float]] = []
    prev = cx1
    for x in cand_xs:
        cells.append((prev, x, x - prev))
        prev = x
    # Prefer the first "value-sized" cell.
    for left, right, w in cells:
        if w >= value_width_hint:
            return (left, cy0, right, cy1)
    # Otherwise accept the first non-degenerate cell.
    for left, right, w in cells:
        if w >= min_width:
            return (left, cy0, right, cy1)
    return None


def find_adjacent_cell_below(
    cell: tuple[float, float, float, float],
    h_lines: list[HLine],
    v_lines: list[VLine],
    min_height: float = 8.0,
) -> Optional[tuple[float, float, float, float]]:
    """Cell sharing its top edge with ``cell``'s bottom edge, if one exists."""
    cx0, cy0, cx1, cy1 = cell
    margin = min(2.0, (cx1 - cx0) / 4)
    cand_ys = sorted(
        y for y, sx, ex in h_lines
        if y > cy1 + 0.5 and sx <= cx0 + margin and ex >= cx1 - margin
    )
    for y in cand_ys:
        if y - cy1 >= min_height:
            return (cx0, cy1, cx1, y)
    return None


def compute_value_slot(
    label_bbox: tuple[float, float, float, float],
    cell_rect: Optional[tuple[float, float, float, float]],
    h_lines: list[HLine],
    v_lines: list[VLine],
    min_inline_width: float = 30.0,
    padding: float = 3.0,
) -> tuple[tuple[float, float, float, float], str]:
    """Pick the rectangle where the value text should live.

    Priority:
      1. If the label's own cell has at least ``min_inline_width`` of empty
         space to the right, place the value there ("inline").
      2. Otherwise try the adjacent cell to the right.
      3. Otherwise try the adjacent cell below.
      4. Otherwise fall back to an unbounded region to the right of the label.

    Returns ``(slot_rect, placement)`` where placement is one of
    "inline" | "right-adj" | "below-adj" | "unbounded".
    """
    lx0, ly0, lx1, ly1 = label_bbox
    if cell_rect is not None:
        cx0, cy0, cx1, cy1 = cell_rect
        # Chinese forms typically use a label-column | value-column table
        # grid, so the adjacent right cell is the natural home for the value.
        # Fall back to inline only if there is no adjacent right.
        adj = find_adjacent_cell_right(cell_rect, h_lines, v_lines)
        if adj is not None:
            ax0, ay0, ax1, ay1 = adj
            return (ax0 + padding, ay0, ax1 - padding, ay1), "right-adj"
        remaining = cx1 - lx1 - padding
        if remaining >= min_inline_width:
            return (lx1 + padding, cy0, cx1 - padding, cy1), "inline"
        below = find_adjacent_cell_below(cell_rect, h_lines, v_lines)
        if below is not None:
            bx0, by0, bx1, by1 = below
            return (bx0 + padding, by0, bx1 - padding, by1), "below-adj"
    # No cell info — guess a loose region to the right of the label.
    return (lx1 + padding, ly0 - 1, lx1 + 240, ly1 + 1), "unbounded"


def find_underline_below(
    label_bbox: tuple[float, float, float, float],
    h_lines: list[HLine],
    max_distance: float = 24.0,
) -> Optional[tuple[float, float, float]]:
    """Locate a horizontal underline just beneath ``label_bbox`` even when
    no full cell exists. Useful for "____" style blank-to-fill forms.
    """
    lx0, ly0, lx1, ly1 = label_bbox
    best: Optional[tuple[float, float, float]] = None
    best_dy: float = 1e9
    for y, sx, ex in h_lines:
        if y < ly1 or y > ly1 + max_distance:
            continue
        # Must overlap label's x range meaningfully
        overlap = max(0.0, min(ex, lx1 + 80) - max(sx, lx0))
        if overlap < 8:
            continue
        dy = y - ly1
        if dy < best_dy:
            best = (y, sx, ex)
            best_dy = dy
    return best

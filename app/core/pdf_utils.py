from __future__ import annotations

import math
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

from .unit_convert import mm_to_pt, pt_to_mm, rect_mm_to_pt_topleft


@dataclass
class PageSize:
    width_mm: float
    height_mm: float
    rotation: int  # 0, 90, 180, 270


def get_page_sizes(pdf_path: Path) -> list[PageSize]:
    sizes: list[PageSize] = []
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            r = page.rect
            sizes.append(
                PageSize(
                    width_mm=pt_to_mm(r.width),
                    height_mm=pt_to_mm(r.height),
                    rotation=page.rotation or 0,
                )
            )
    return sizes


def _rotated_stamp(
    stamp_png: Path, rotation_deg: float, w_mm: float, h_mm: float
) -> tuple[bytes, float, float, float, float]:
    """Rotate stamp by rotation_deg (clockwise), return (png_bytes, new_x_offset, new_y_offset,
    new_w_mm, new_h_mm) — offset is the shift from original top-left that keeps the center fixed."""
    if abs(rotation_deg) < 0.01:
        return stamp_png.read_bytes(), 0.0, 0.0, w_mm, h_mm
    theta = math.radians(rotation_deg)
    cos_t = abs(math.cos(theta))
    sin_t = abs(math.sin(theta))
    new_w = w_mm * cos_t + h_mm * sin_t
    new_h = h_mm * cos_t + w_mm * sin_t
    off_x = (w_mm - new_w) / 2.0
    off_y = (h_mm - new_h) / 2.0
    with Image.open(stamp_png) as im:
        if im.mode != "RGBA":
            im = im.convert("RGBA")
        # PIL's rotate angle is counter-clockwise. We want CSS/clockwise semantics,
        # so flip the sign.
        rotated = im.rotate(-rotation_deg, resample=Image.BICUBIC, expand=True)
        buf = BytesIO()
        rotated.save(buf, format="PNG")
    return buf.getvalue(), off_x, off_y, new_w, new_h


def stamp_pdf(
    src_pdf: Path,
    dst_pdf: Path,
    stamp_png: Path,
    x_mm: float,
    y_mm: float,
    w_mm: float,
    h_mm: float,
    pages: list[int] | None = None,
    rotation_deg: float = 0.0,
) -> None:
    """Stamp every (or selected) page of a PDF with a PNG.

    Coordinates are top-left origin, millimetres. pages is 0-indexed; None = all pages.
    rotation_deg rotates clockwise around the stamp's center to simulate hand-stamped tilt.
    """
    stamp_bytes, off_x, off_y, draw_w, draw_h = _rotated_stamp(
        stamp_png, rotation_deg, w_mm, h_mm
    )
    with fitz.open(str(src_pdf)) as doc:
        for i, page in enumerate(doc):
            if pages is not None and i not in pages:
                continue
            page_h_mm = pt_to_mm(page.rect.height)
            x0, y0, x1, y1 = rect_mm_to_pt_topleft(
                x_mm + off_x, y_mm + off_y, draw_w, draw_h, page_h_mm
            )
            rect = fitz.Rect(x0, y0, x1, y1)
            page.insert_image(rect, stream=stamp_bytes, overlay=True, keep_proportion=False)
        doc.save(str(dst_pdf), garbage=3, deflate=True)


def is_encrypted(pdf_path: Path) -> bool:
    with fitz.open(str(pdf_path)) as doc:
        return doc.is_encrypted and doc.needs_pass

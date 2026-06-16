from __future__ import annotations

import math
import re
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


_MULTIPLY_GS_NAME = "JTStMul"


def _apply_multiply_blend(doc: "fitz.Document", page: "fitz.Page", img_xref: int) -> None:
    """Make the just-inserted image (img_xref) composite with the Multiply blend
    mode against whatever is already on the page.

    Real rubber-stamp / ink chops are translucent: the ink darkens the paper and
    any lines/text underneath, it does not opaquely replace them. PyMuPDF's
    ``insert_image`` paints opaquely, so a stamp dropped over an existing rule or
    field line cuts the line with a hard, unnatural edge. We attach an ExtGState
    with ``/BM /Multiply`` to just this one image's draw operator so the strokes
    multiply onto the backdrop (blue × white = blue, blue × black line = darker),
    while the transparent areas (already keyed out) stay untouched. Only this
    image is affected — pre-existing page images (e.g. a scanned form) are not
    darkened.

    Any failure falls back silently to the opaque draw — stamping must never break.
    """
    try:
        page.clean_contents()
        typ, xobj = doc.xref_get_key(page.xref, "Resources/XObject")
        if not xobj:
            return
        xobj_b = xobj.encode("latin-1") if isinstance(xobj, str) else xobj
        m = re.search(rb"/(\w+)\s+%d\s+0\s+R" % img_xref, xobj_b)
        if not m:
            return
        name = m.group(1).decode("latin-1")
        gs_xref = doc.get_new_xref()
        doc.update_object(gs_xref, "<< /Type /ExtGState /BM /Multiply >>")
        doc.xref_set_key(
            page.xref, f"Resources/ExtGState/{_MULTIPLY_GS_NAME}", f"{gs_xref} 0 R"
        )
        contents = page.read_contents()
        needle = ("/%s Do" % name).encode("latin-1")
        # Inject the blend-mode gs inside the image's own q/Q block (right before
        # the Do), so it is scoped to this draw and never leaks to other content.
        patched = contents.replace(
            needle, ("/%s gs " % _MULTIPLY_GS_NAME).encode("latin-1") + needle, 1
        )
        if patched == contents:
            return
        doc.update_stream(page.get_contents()[0], patched)
    except Exception:
        # Fall back to the opaque draw already on the page.
        pass


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
    blend_multiply: bool = True,
) -> None:
    """Stamp every (or selected) page of a PDF with a PNG.

    Coordinates are top-left origin, millimetres. pages is 0-indexed; None = all pages.
    rotation_deg rotates clockwise around the stamp's center to simulate hand-stamped tilt.
    blend_multiply paints the stamp with the Multiply blend mode (translucent ink
    look) so it darkens — rather than opaquely covers — lines/text underneath.
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
            # Coordinates come from the editor, which positions the stamp in the
            # page's DISPLAYED (rotated) coordinates. page.insert_image works in
            # the UNROTATED page space and ignores /Rotate, so on a rotated page
            # (e.g. an A4 portrait scanned with /Rotate 90 → shown landscape) the
            # stamp would land in the wrong place + wrong orientation. Map the
            # rect back through the derotation matrix and counter-rotate the image
            # so it ends up exactly where the user placed it, upright. For an
            # unrotated page the matrix is identity and rotate=0 → no change.
            # (GitHub #28: edit mode vs composite/export mismatch on rotated PDFs.)
            page_rot = page.rotation or 0
            if page_rot:
                rect = rect * page.derotation_matrix
            img_xref = page.insert_image(
                rect, stream=stamp_bytes, overlay=True, keep_proportion=False,
                rotate=page_rot,
            )
            if blend_multiply and isinstance(img_xref, int) and img_xref > 0:
                _apply_multiply_blend(doc, page, img_xref)
        doc.save(str(dst_pdf), garbage=3, deflate=True)


def is_encrypted(pdf_path: Path) -> bool:
    with fitz.open(str(pdf_path)) as doc:
        return doc.is_encrypted and doc.needs_pass

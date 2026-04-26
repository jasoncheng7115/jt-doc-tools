from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...core import pdf_utils


@dataclass
class StampParams:
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    rotation_deg: float = 0.0
    pages: Optional[list[int]] = None  # 0-indexed; None = all


def stamp(src_pdf: Path, dst_pdf: Path, stamp_png: Path, params: StampParams) -> None:
    pdf_utils.stamp_pdf(
        src_pdf=src_pdf,
        dst_pdf=dst_pdf,
        stamp_png=stamp_png,
        x_mm=params.x_mm,
        y_mm=params.y_mm,
        w_mm=params.width_mm,
        h_mm=params.height_mm,
        pages=params.pages,
        rotation_deg=params.rotation_deg,
    )

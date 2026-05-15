"""PDF 真值解析 — pdf_to_office Sprint 1。"""
from .extractor import extract_pdf_truth
from .models import (
    BBox,
    PDFBlock,
    PDFChar,
    PDFDrawing,
    PDFFontInfo,
    PDFImage,
    PDFLine,
    PDFPage,
    PDFTruth,
)

__all__ = [
    "BBox",
    "PDFBlock",
    "PDFChar",
    "PDFDrawing",
    "PDFFontInfo",
    "PDFImage",
    "PDFLine",
    "PDFPage",
    "PDFTruth",
    "extract_pdf_truth",
]

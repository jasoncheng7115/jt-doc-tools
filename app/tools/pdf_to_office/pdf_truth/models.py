"""PDF 真值資料模型 — 從原 PDF 抽出的「真值結構」，給後處理 fixer 比對 docx 用。

座標單位一律 PDF 點 (1 pt = 1/72 in)。bbox = (x0, y0, x1, y1)，左上原點。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Tuple


BBox = Tuple[float, float, float, float]


@dataclass
class PDFChar:
    char: str
    x: float
    y: float
    width: float
    height: float
    font_name: str
    font_size: float
    is_bold: bool = False
    is_italic: bool = False
    color: str = "#000000"  # hex


@dataclass
class PDFLine:
    chars: list[PDFChar]
    bbox: BBox
    text: str
    dominant_font: str
    dominant_size: float


@dataclass
class PDFBlock:
    """PyMuPDF 給的文字區塊；視為「PDF 視角下的段落候選」。"""
    lines: list[PDFLine]
    bbox: BBox
    text: str
    block_type: Literal["text", "image"]
    page_num: int  # 0-based
    dominant_font: str = ""
    dominant_size: float = 0.0


@dataclass
class PDFImage:
    bbox: BBox
    page_num: int
    xref: int
    width: int   # 像素
    height: int
    image_hash: str = ""  # perceptual hash, 對應 docx 圖片用


@dataclass
class PDFDrawing:
    """繪圖物件，用於表格偵測（線條/矩形構成的網格）。"""
    type: Literal["line", "rect", "curve"]
    bbox: BBox
    page_num: int
    stroke_color: str = "#000000"
    stroke_width: float = 0.0


@dataclass
class PDFPage:
    page_num: int  # 0-based
    width: float
    height: float
    margin_top: float
    margin_bottom: float
    margin_left: float
    margin_right: float
    blocks: list[PDFBlock] = field(default_factory=list)
    images: list[PDFImage] = field(default_factory=list)
    drawings: list[PDFDrawing] = field(default_factory=list)
    is_scanned: bool = False
    has_bad_cmap: bool = False


@dataclass
class PDFFontInfo:
    name: str
    is_embedded: bool = False
    has_tounicode: bool = False
    is_cjk: bool = False
    usage_count: int = 0


@dataclass
class PDFTruth:
    pages: list[PDFPage]
    fonts: list[PDFFontInfo]
    total_pages: int
    has_encryption: bool = False
    has_scanned_pages: bool = False
    language_guess: str = "unknown"  # zh-Hant / zh-Hans / en / ja / mixed
    body_font_size: float = 0.0      # 全文出現次數最多的字級（內文基準）
    body_font_name: str = ""

    @property
    def all_blocks(self) -> list[PDFBlock]:
        """所有 text block 的扁平 list（依頁順 + block 順）。給 aligner 用。"""
        out: list[PDFBlock] = []
        for p in self.pages:
            for b in p.blocks:
                if b.block_type == "text":
                    out.append(b)
        return out

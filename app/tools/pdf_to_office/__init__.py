"""PDF 轉文書檔（pdf-to-office）— Sprint 1 MVP。

PDF → docx / odt，引擎 pdf2docx + 後處理層校正常見問題（字型 / 段落 / 雜訊）。
失敗 fallback 到 LibreOffice 直轉。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-to-office",
    name="PDF 轉文書檔",
    description="PDF 轉 Word (.docx) 或 OpenDocument (.odt)，含字型 / 段落 / 雜訊智慧校正。",
    icon="file-text",
    category="檔案編輯",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

"""PDF 文字層補建（pdf-ocr）— 對掃描 PDF / 影像 PDF 補上透明可選取的文字層。

對每頁渲染成影像 → tesseract OCR → 用 PyMuPDF 把識別文字以「invisible
render mode」貼回原位置。輸出 PDF 視覺跟原檔一樣，但文字可選取 / 可搜尋
/ 可被其他文字抽取工具讀到。

LLM 加值（選填）：把 tesseract 結果送 LLM 校正 typo / 還原段落，再寫進
文字層，搜尋與選取品質更好。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-ocr",
    name="OCR 文字辨識",
    description="掃描進來的 PDF 或圖片跑 OCR 後，文字變成可搜尋、可滑鼠選取複製。",
    icon="text",
    category="內容處理",
    version="0.1.0",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

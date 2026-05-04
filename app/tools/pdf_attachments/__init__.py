"""PDF 附件萃取：列出並取出 PDF 內嵌的檔案 (EmbeddedFiles)。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-attachments",
    name="PDF 附件萃取",
    description="列出並取出 PDF 中嵌入的檔案（EmbeddedFiles）。",
    icon="paperclip",
    category="內容處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

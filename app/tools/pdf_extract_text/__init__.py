"""PDF → Text: extract text from PDF with optional paragraph reflow,
exportable as .txt / .md / .docx / .odt."""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-extract-text",
    name="擷取文字",
    description="擷取 PDF 文字，輸出 TXT / Markdown / Word / ODT，可選 LLM 重排段落。",
    icon="paragraph",
    category="內容擷取",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

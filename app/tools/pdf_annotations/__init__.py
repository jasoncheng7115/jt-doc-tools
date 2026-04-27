"""PDF 註解整理:擷取 + 審閱報告 + 待辦清單。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-annotations",
    name="註解整理",
    description="擷取 PDF 註解，輸出完整清單 / 審閱報告 / 待辦清單(CSV / JSON / Markdown)。",
    icon="sticky-note",
    category="內容擷取",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

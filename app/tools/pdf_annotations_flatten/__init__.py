"""PDF 註解平面化:把註解燒進頁面內容，讓收件方看到所有標註且無法移除。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-annotations-flatten",
    name="註解平面化",
    description="把 PDF 註解(螢光筆、文字註解等)燒進頁面內容，使其無法被編輯或移除。",
    icon="layers",
    category="檔案編輯",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

"""PDF 註解清除:刪除註解(全選 / 依作者 / 依類型篩選)。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-annotations-strip",
    name="註解清除",
    description="從 PDF 移除註解，可全選或依作者 / 類型篩選後刪除。",
    icon="trash",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

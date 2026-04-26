"""PDF 差異比對：兩份 PDF 並排顯示文字 / metadata 差異，合約審閱必用。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-diff",
    name="PDF 差異比對",
    description="兩份 PDF 逐頁並排比對，文字差異標紅。",
    icon="diff",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

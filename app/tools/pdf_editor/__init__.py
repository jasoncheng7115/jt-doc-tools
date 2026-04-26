"""PDF Editor — Scribus-inspired frame-based editor."""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-editor",
    name="PDF 編輯器",
    description="疊加文字、圖片、形狀、白底遮罩、標註；可編輯或刪除原 PDF 上的文字與圖片。",
    icon="edit",
    category="檔案編輯",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
    assets_used=["stamp", "signature", "logo"],
)

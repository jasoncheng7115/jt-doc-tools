"""PDF 密碼解除：輸入密碼後存成無密碼的副本。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-decrypt",
    name="PDF 密碼解除",
    description="已知密碼時解除 PDF 的開啟密碼與權限限制。",
    icon="unlock",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

"""PDF → Image: convert each PDF page to a PNG; download single PNG or a ZIP."""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-to-image",
    name="文書轉圖片",
    description="PDF 或 Office 文件每頁轉成 PNG；多頁自動打包 ZIP。",
    icon="image",
    category="格式轉換",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-stamp",
    name="用印與簽名",
    description="上傳 PDF，套用印章 / 簽名 / Logo 圖片並下載；支援批次處理。",
    icon="stamp",
    category="填單用印",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
    assets_used=["stamp"],
)

"""Image → PDF: drag in images, reorder/rotate/delete per page, output one PDF."""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="image-to-pdf",
    name="圖片轉 PDF",
    description="拖入多張圖片，排序 / 旋轉 / 刪除每一頁，輸出單一 PDF；可選頁面大小。",
    icon="image",
    category="格式轉換",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

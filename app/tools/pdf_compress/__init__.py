"""PDF 壓縮 — reduce PDF file size via lossless optimisation,
image downsampling/recompression, font subsetting, and optional
Ghostscript pass."""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-compress",
    name="PDF 壓縮",
    description="減少 PDF 檔案大小；三種預設或進階自訂圖片 DPI、字型子集化等。",
    icon="compress",
    category="檔案編輯",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

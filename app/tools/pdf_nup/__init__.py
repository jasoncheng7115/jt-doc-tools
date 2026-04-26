"""多頁合併 (N-up): impose multiple PDF pages per sheet."""
from pathlib import Path
from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-nup", name="多頁合併",
    description="把 2/4/6/8/9/16 頁 PDF 合併到一張紙；自訂版面、間距、邊框。",
    icon="tile", category="檔案編輯",
)
tool = ToolModule(
    metadata=metadata, router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

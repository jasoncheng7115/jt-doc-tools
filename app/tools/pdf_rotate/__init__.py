from pathlib import Path
from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-rotate", name="頁面轉向",
    description="把 PDF 整份或指定頁面以 90/180/270 度旋轉。",
    icon="rotate-cw", category="檔案編輯",
)
tool = ToolModule(metadata=metadata, router=router,
                  templates_dir=Path(__file__).resolve().parent / "templates")

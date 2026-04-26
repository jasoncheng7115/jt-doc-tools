from pathlib import Path
from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-pages", name="頁面整理",
    description="重新排序、刪除指定頁面，輸出新的 PDF。",
    icon="pages", category="檔案編輯",
)
tool = ToolModule(metadata=metadata, router=router,
                  templates_dir=Path(__file__).resolve().parent / "templates")

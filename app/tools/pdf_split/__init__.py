from pathlib import Path
from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-split", name="頁面分拆",
    description="把 PDF 依頁面範圍切成多份，或一頁一份。",
    icon="split", category="檔案編輯",
)
tool = ToolModule(metadata=metadata, router=router,
                  templates_dir=Path(__file__).resolve().parent / "templates")

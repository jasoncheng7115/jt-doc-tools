from pathlib import Path
from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-merge", name="檔案合併",
    description="把多份 PDF 依上傳順序合併為一份。",
    icon="merge", category="檔案編輯",
)
tool = ToolModule(
    metadata=metadata, router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

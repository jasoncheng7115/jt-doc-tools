from pathlib import Path
from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-pageno", name="插入頁碼",
    description="自動把頁碼印進每一頁；可選位置、字級與起始頁碼。",
    icon="hash", category="檔案編輯",
)
tool = ToolModule(metadata=metadata, router=router,
                  templates_dir=Path(__file__).resolve().parent / "templates")

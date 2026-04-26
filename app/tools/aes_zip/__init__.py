"""AES ZIP 加密：批次把檔案打包成 AES-256 加密 ZIP。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="aes-zip",
    name="AES ZIP 加密",
    description="把多個檔案打包成密碼保護 ZIP（AES-256 加密），適合 email 附件傳送。相容於主流解壓工具（7-Zip / Keka / macOS Archive Utility 10.13+ / WinRAR）。",
    icon="archive",
    category="資安處理",
    enabled=False,  # 暫時下架，程式碼保留
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

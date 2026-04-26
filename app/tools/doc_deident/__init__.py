"""文件去識別化 (De-identification) — detect and redact/mask sensitive data
in PDF and Office documents."""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="doc-deident",
    name="文件去識別化",
    description="偵測文件中的敏感資料（身分證 / 手機 / Email / 統編 …），一鍵編修或資料遮罩。",
    icon="shield",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

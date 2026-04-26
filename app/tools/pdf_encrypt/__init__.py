"""PDF 密碼保護：設開啟密碼 + 權限密碼 + 權限控制。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-encrypt",
    name="PDF 密碼保護",
    description="設 PDF 開啟密碼、權限控制（禁列印 / 複製 / 編輯），AES-256 加密。",
    icon="lock",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

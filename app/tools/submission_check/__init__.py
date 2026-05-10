"""送件前檢核（submission-check）— 對一批文件做 L1 規則 / L2 OCR / L3 LLM 三層檢核。

典型場景：投標 / KYC / HR 到職 / 報帳 / 訴狀附件 / 申請件 / 工程交付 / 保險理賠
等「多檔混格式 + 有特定基準資訊 + 對外送出前」的文件包健檢。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="submission-check",
    name="送件前檢核",
    description="一批文件送出去前的最後一關自查：身分一致性、metadata 殘留、漏改範本、章/簽完整、偽造痕跡，三層檢核（規則 / OCR / LLM）。",
    icon="id-card",
    category="資安處理",
    version="0.1.0",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

"""名片辨識：照片 / 掃描檔裡的名片 → 切卡、辨識、校對 → 聯絡人清單（CSV / XLSX / ODS / VCF / JSON）。

兩種收件模式：每張圖片一張名片（直接辨識）、每張圖片多張名片（自動找出每一張、先確認框位再一次辨識）。
不需要 LLM；OCR 走全站共用的 OCR 設定。規格 `docs-share/business-card/`（內部）。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="business-card-reader",
    name="名片辨識",
    description="擷取名片上的姓名、公司、職稱、電話、Email 等資料，支援一張照片多張名片自動切卡，校對後匯出聯絡人清單。",
    icon="id-card",
    category="內容處理",
    beta=True,
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

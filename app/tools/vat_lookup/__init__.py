"""統編查詢 — 反查公司 / 政府機關 / 學校統一編號的名稱、地址、行業。

資料來源：app.core.vat_db 的 vat_registry SQLite (BGMOPEN 主檔 +
政府機關 / 學校 補充)。資料庫管理在 admin/vat-db。沒匯入資料時告知 admin。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="vat-lookup",
    name="統編查詢",
    description="輸入 8 位統一編號，反查公司 / 政府機關 / 學校的名稱、地址、行業類別。",
    icon="search",
    category="內容處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

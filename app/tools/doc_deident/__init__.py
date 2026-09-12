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
    # **v1.15.32 解除反灰**：加了英美的 SSN / NI / IBAN / 電話 /
    # 郵遞區號 / 美式地址與英文月份生日，而且**樣式依「文件語言」分組**
    # —— 英文模式下台灣專屬的式子會關掉（它們在英文文件上是
    # **抓錯**不是抓不到）。畫面上可選文件語言，預設跟著介面語言。
    # 驗收：英文端到端（打開產出確認撈不回來）＋誤判語料零命中
    # ＋台灣真實樣本零退步。
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

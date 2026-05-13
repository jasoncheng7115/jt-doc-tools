"""電子發票掃描：上傳發票照片 / PDF，掃 QR Code 解出結構化資料。

台灣財政部電子發票（B2C）的左 QR Code 編碼了完整的結構化資料：
發票號碼 / 開立日期 / 隨機碼 / 銷售額 / 總計金額 / 雙方統編 / 加密驗證碼，
解碼後直接得到正確資料，準確率 ~100%（不靠 OCR 也不會有辨識錯誤）。

掃完的發票存到 per-user buffer（JSON 檔），可在 buffer 列表查看 / 刪除 /
匯出（M3 才做）。M1 / M2 階段先把 scan + 列表撐起來。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="einvoice-scan",
    name="電子發票掃描",
    description="掃描電子發票 QR Code，解出發票號碼 / 日期 / 金額 / 雙方統編。支援圖片或 PDF 上傳，結果累積到 buffer 可後續整理匯出。",
    icon="qr",
    category="內容處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

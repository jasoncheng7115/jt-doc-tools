"""清單處理：把每行文字當一筆資料做排序 / 去重 / 篩選。

支援貼上文字或上傳 .txt / .csv / .tsv / .xlsx / .ods / .docx / .odt / .pdf
（萃取後一行一筆）。操作可鏈式組合，結果可一鍵複製或下載成 .txt / .csv /
.xlsx。設計為通用 line-pipeline，未來可繼續加新的操作（grep / awk-like /
頻率統計等）不破壞現有 UI。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="text-list",
    name="清單處理",
    description="貼文字或上傳檔案，每行一筆。可排序 / 去重 / 篩選 / 大小寫 / 取頭尾，操作可組合。",
    icon="list",
    category="內容處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

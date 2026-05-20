"""字數統計：PDF / 純文字 字數、字元類型分布、高頻詞、閱讀時間預估。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-wordcount",
    name="字數統計",
    description="統計 PDF / TXT / MD / CSV 等文件字數、字元、段落、句子；支援多檔批次與跨檔總計。",
    icon="chart-bar",
    category="內容處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

"""PDF 字數統計：頁字數、字元類型分布、高頻詞、閱讀時間預估。"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-wordcount",
    name="字數統計",
    description="統計 PDF 字數 / 字元 / 段落 / 句子，含每頁分布與高頻詞圖表。",
    icon="chart-bar",
    category="內容處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

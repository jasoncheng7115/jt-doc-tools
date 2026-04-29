"""文字差異比對：直接貼兩組文字，立即比對。

不上傳檔案 — 給快速比對 log 片段、code 片段、改稿前後的純文字
情境用，不用先存成檔案再上傳。Diff 邏輯與 doc-diff 共用同一條
SequenceMatcher pipeline 確保結果一致。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="text-diff",
    name="文字差異比對",
    description="直接貼上兩組文字進行比對，不需上傳檔案。給 log / code / 段落改稿快速 diff。",
    icon="diff",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

"""文件差異比對：兩份文件並排顯示文字 / metadata 差異。

接受 PDF 直接比，或 Word / Excel / PowerPoint / ODT / ODS / ODP — 非 PDF 檔
會先用 OxOffice / LibreOffice 轉成 PDF 再比對（合約審閱、版本變更必用）。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="doc-diff",
    name="文件差異比對",
    description="兩份文件逐頁並排比對，文字差異以紅色標示。支援 PDF / Word / Excel / PowerPoint / ODF。",
    icon="diff",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

from pathlib import Path
from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-extract-images", name="擷取圖片",
    description="把 PDF 中所有嵌入的圖片擷取出來，打包成 ZIP 下載。",
    icon="crop", category="內容處理",
)
tool = ToolModule(metadata=metadata, router=router,
                  templates_dir=Path(__file__).resolve().parent / "templates")

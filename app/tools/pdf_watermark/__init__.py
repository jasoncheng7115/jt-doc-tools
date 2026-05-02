from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-watermark",
    name="浮水印",
    description="把浮水印 / Logo 印進 PDF，可調透明度、角度，平鋪或指定位置。",
    icon="watermark",
    category="填單用印",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
    assets_used=["watermark", "logo"],
)

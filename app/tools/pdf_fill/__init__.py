from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="pdf-fill",
    name="表單自動填寫",
    description="上傳廠商資料表 / 申請書（PDF、Word、Excel、ODF），自動辨識欄位後用公司基本資料填好。",
    icon="form",
    category="填單用印",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
    assets_used=[],
)

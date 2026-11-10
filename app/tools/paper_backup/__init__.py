"""加密紙本備份：把重要的秘密加密後印成 QR，金鑰拆成 T / N 份分片交給不同保管人。

建立與還原都在瀏覽器裡做（`static/js/paper_backup/`），主機看不到明文、金鑰與分片；
伺服器只提供頁面，並記錄不含秘密的稽核（備份 ID、門檻、份數）。格式見內部 ADR-001。
預設不在任何一般角色裡（只有管理員能用）；未啟用登入時沒有限制。
"""
from pathlib import Path

from ..base import ToolMetadata, ToolModule
from .router import router

metadata = ToolMetadata(
    id="paper-backup",
    name="加密紙本備份",
    description="把密碼、金鑰加密印成 QR 紙本，金鑰拆給多位保管人，湊滿份數才能還原。",
    icon="paper-backup",
    category="資安處理",
)

tool = ToolModule(
    metadata=metadata,
    router=router,
    templates_dir=Path(__file__).resolve().parent / "templates",
)

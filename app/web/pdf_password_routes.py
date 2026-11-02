"""上傳的 PDF 有沒有開啟密碼 —— 給每一支工具的上傳框共用。

使用者 2026-11-02：拉進一份設了開啟密碼的 PDF，用印頁什麼反應都沒有（預覽端點開不了檔、
畫面只是 `return null`）。不只用印：每一支吃 PDF 的工具都是同一個形狀。

所以檢查收在共用的上傳元件（`static/js/file_upload.js`）裡做一次：
1. 瀏覽器先看檔頭檔尾有沒有 `/Encrypt`（便宜，絕大多數檔案到這裡就結束，不會多傳一趟）；
2. 有的話才把檔案送來這裡問「真的要密碼嗎」—— **只限制列印 / 編輯的 PDF（只有擁有者密碼）
   打得開**，那種不要攔；
3. 要密碼時，畫面讓使用者輸入密碼（送到「PDF 密碼解除」的 `/unlock`，那條路由在工具底下，
   **沒有那支工具權限的人由既有的權限中介層擋掉**），或到該工具去解除。

這一支只回答「要不要密碼」與「你能不能在這裡解」，**不解密、不留檔**（檢查完就刪）。
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

router = APIRouter()

UNLOCK_TOOL = "pdf-decrypt"


def _needs_password(path: Path) -> bool:
    import fitz
    try:
        with fitz.open(str(path)) as doc:
            return bool(doc.needs_pass)
    except Exception:  # noqa: BLE001 —— 毀損的檔不是這裡要回答的事，交給工具自己說
        return False


def _can_unlock(request: Request) -> bool:
    from ..core import upload_owner
    if not upload_owner.auth_enabled():
        return True
    user = getattr(getattr(request, "state", None), "user", None)
    uid = user.get("user_id") if isinstance(user, dict) else None
    if uid is None:
        return False
    from ..core import permissions
    return bool(permissions.user_can_use_tool(int(uid), UNLOCK_TOOL))


@router.post("/pdf-password/check")
async def pdf_password_check(request: Request, file: UploadFile = File(...)):
    """回 `{needs_password, can_unlock, unlock_url, tool_url}`。"""
    head = await file.read(5)
    if head[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    from ..config import settings
    settings.temp_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="pwcheck_", suffix=".pdf", dir=settings.temp_dir,
                                     delete=False) as tmp:
        tmp.write(head)
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            tmp.write(chunk)
        path = Path(tmp.name)
    try:
        needs = await asyncio.to_thread(_needs_password, path)
    finally:
        path.unlink(missing_ok=True)
    return {
        "needs_password": needs,
        "can_unlock": _can_unlock(request) if needs else False,
        "unlock_url": f"/tools/{UNLOCK_TOOL}/unlock",
        "tool_url": f"/tools/{UNLOCK_TOOL}/",
    }

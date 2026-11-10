"""加密紙本備份的伺服器端：只有頁面與不含秘密的稽核。

建立、還原、加解密、分片、PDF 全部在瀏覽器裡（方案 C）—— 這裡刻意沒有任何收明文或分片的端點。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()

_ACTIONS = {"create", "restore"}
_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MODES = {"paper"}                     # 只做紙本（使用者 2026-11-09：不提供存成電子檔）
_DENSITIES = {"low", "medium", "high"}


def _print_context(request: Request) -> dict:
    """浮水印用：誰、從哪裡、在哪個站台產生的（印在每一頁的邊界，方便追查紙本的來源）。"""
    from ...core import client_ip
    from ...core.sessions import user_label
    user = getattr(request.state, "user", None)
    host = (request.headers.get("host") or "").strip()[:100]
    return {"user": user_label(user) if user else "", "ip": client_ip.real_client_ip(request) or "", "host": host}


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "paper_backup.html", {"request": request, "pb_ctx": _print_context(request)})


@router.post("/audit")
async def audit(request: Request):
    """瀏覽器建立 / 還原完成後回報（只收不含秘密的欄位：備份 ID、門檻、份數、方式）。

    標題、檔名、內容、分片一律不收 —— 多送的欄位直接拒絕，避免日後有人順手把秘密送上來。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(400, "invalid body")
    allowed = {"action", "backup_id", "t", "n", "mode", "density", "blocks", "ok"}
    if set(body) - allowed:
        raise HTTPException(400, "unexpected fields")
    action = body.get("action")
    bid = body.get("backup_id")
    t, n = body.get("t"), body.get("n")
    if action not in _ACTIONS or not isinstance(bid, str) or not _ID_RE.match(bid):
        raise HTTPException(400, "invalid action or backup_id")
    if not (isinstance(t, int) and isinstance(n, int) and 2 <= t <= n <= 16):
        raise HTTPException(400, "invalid t/n")
    details: dict = {"backup_id": bid, "t": t, "n": n}
    if action == "create":
        mode, density, blocks = body.get("mode"), body.get("density"), body.get("blocks")
        if mode not in _MODES or density not in _DENSITIES or not (isinstance(blocks, int) and 1 <= blocks <= 2000):
            raise HTTPException(400, "invalid mode/density/blocks")
        details.update(mode=mode, density=density, blocks=blocks)
    else:
        ok = body.get("ok")
        if not isinstance(ok, bool):
            raise HTTPException(400, "invalid ok")
        details["ok"] = ok
    from ...core import audit_db, client_ip
    from ...core.sessions import user_label
    user = getattr(request.state, "user", None)
    audit_db.log_event("paper_backup_" + action, username=user_label(user) if user else "",
                       ip=client_ip.real_client_ip(request), target=bid, details=details)
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})

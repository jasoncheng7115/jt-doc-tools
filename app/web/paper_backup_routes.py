"""加密紙本備份：離線還原包下載。

放在 `/tools/` 之外：要登入（啟用登入時），但**不跟工具權限**（使用者 2026-11-09 決定）——
離線包不含任何秘密，出事時每一位保管人都要拿得到。同一版本、同一語言的位元組固定，
工具頁會顯示 SHA-256 讓人核對。
"""
from __future__ import annotations

import hashlib
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

_CACHE: dict[tuple[str, str], tuple[bytes, str]] = {}
_LOCK = threading.Lock()


def pack(request: Request) -> tuple[bytes, str, str]:
    """回 (內容, SHA-256, 檔名)；同一版本 × 語言只產一次。"""
    from ..core import ui_locale
    from ..main import VERSION
    from ..tools.paper_backup import offline
    lang = ui_locale.resolve(request)
    key = (VERSION, lang)
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is None:
            data = offline.build(request.app.state.templates, request, VERSION, lang)
            hit = (data, hashlib.sha256(data).hexdigest())
            _CACHE[key] = hit
    return hit[0], hit[1], f"jtdt-paper-backup-offline-{VERSION}-{lang}.html"


def build_router() -> APIRouter:
    r = APIRouter()

    @r.get("/paper-backup/offline")
    async def offline_pack(request: Request):
        import asyncio
        from ..core.http_utils import content_disposition
        data, digest, name = await asyncio.to_thread(pack, request)
        return Response(data, media_type="text/html; charset=utf-8", headers={
            "Content-Disposition": content_disposition(name),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "X-JTDT-SHA256": digest,
        })

    @r.get("/paper-backup/offline.json")
    async def offline_info(request: Request):
        import asyncio
        data, digest, name = await asyncio.to_thread(pack, request)
        return JSONResponse({"filename": name, "size": len(data), "sha256": digest}, headers={"Cache-Control": "no-store"})

    return r


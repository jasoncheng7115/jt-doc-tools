"""「用手機接續」的 QR Code：內容是這支工具頁的完整網址，手機掃了直接開同一頁（沒登入會先登入再回來）。

網址的主機以這次請求為準（本機、內網、反向代理都帶得對）。app 關掉了 uvicorn 的 proxy_headers
（見 main.py），所以這裡自己讀反向代理送的 X-Forwarded-Proto / -Host，沒有才用直連的。
QR 只是網址，不帶任何憑證或工作階段：手機要用自己的帳號登入。
"""
from __future__ import annotations

import io
import re

from fastapi import Request
from fastapi.responses import StreamingResponse

_PATH_RE = re.compile(r"^/tools/[a-z0-9-]+/?$")


def public_url(request: Request, path: str) -> str:
    if not _PATH_RE.fullmatch(path):
        raise ValueError("path")
    xfp = (request.headers.get("X-Forwarded-Proto", "") or "").split(",")[0].strip()
    xfh = (request.headers.get("X-Forwarded-Host", "") or "").split(",")[0].strip()
    scheme = xfp if xfp in ("http", "https") else request.url.scheme
    host = xfh or request.headers.get("Host") or request.url.netloc
    return f"{scheme}://{host}{path}"


def qr_response(request: Request, path: str) -> StreamingResponse:
    import qrcode
    target = public_url(request, path)
    img = qrcode.make(target, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png",
                             headers={"X-Handoff-Url": target, "Cache-Control": "no-store"})

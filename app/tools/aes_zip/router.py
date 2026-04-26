"""Endpoints for AES ZIP 加密."""
from __future__ import annotations

import io
import time
import uuid
from pathlib import Path
from typing import List

import pyzipper
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from ...config import settings
from ...core.http_utils import content_disposition


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("aes_zip.html", {"request": request})


@router.post("/submit")
async def submit(
    file: List[UploadFile] = File(...),
    password: str = Form(...),
    zip_name: str = Form(""),
    compression: str = Form("deflate"),   # deflate | store | lzma
):
    if not password:
        raise HTTPException(400, "請輸入密碼")
    files = file or []
    if not files:
        raise HTTPException(400, "沒有檔案")

    # Map compression choice to zipfile constant supported by pyzipper.
    comp_map = {
        "deflate": pyzipper.ZIP_DEFLATED,
        "store":   pyzipper.ZIP_STORED,
        "lzma":    pyzipper.ZIP_LZMA,
    }
    comp = comp_map.get(compression, pyzipper.ZIP_DEFLATED)

    # Read every uploaded file into memory first (async-safe), then hand
    # off encryption to a worker thread so AES + LZMA on big files doesn't
    # block the event loop.
    items: list[tuple[str, bytes]] = []
    total_bytes = 0
    for f in files:
        data = await f.read()
        if data is None:
            continue
        name = Path(f.filename or "file").name or "file"
        items.append((name, data))
        total_bytes += len(data)

    import asyncio as _asyncio

    def _do_encrypt():
        buf = io.BytesIO()
        # AESZipFile with WZ_AES uses WinZip-AES-256 — the de-facto standard
        # that 7-Zip / Keka / modern Archive Utility can unlock with a
        # password prompt.
        with pyzipper.AESZipFile(
            buf, "w",
            compression=comp,
            encryption=pyzipper.WZ_AES,
        ) as zf:
            zf.setpassword(password.encode("utf-8"))
            for name, data in items:
                zf.writestr(name, data)
        return buf.getvalue()

    payload = await _asyncio.to_thread(_do_encrypt)

    zname = (zip_name.strip() or f"archive_{time.strftime('%Y%m%d_%H%M%S')}")
    if not zname.lower().endswith(".zip"):
        zname += ".zip"
    # basic sanitization
    zname = Path(zname).name.replace("/", "_").replace("\\", "_")

    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(zname)},
    )

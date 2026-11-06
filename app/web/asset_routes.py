"""Login-gated read-only asset image endpoints.

Shared branding assets (logos / stamps / signatures / watermarks) live in
「資產管理」(admin uploads them). The picture *files* themselves, however, are
needed by ordinary tool pages — pdf-stamp / pdf-watermark / pdf-editor render
their thumbnails in the asset picker and the editor preview.

The admin router serves these under `/admin/assets/{id}/file|thumb`, but that
whole router is admin-gated. So when authentication is enabled, a non-admin
user (who *can* legitimately use the stamp / watermark tools) gets 403 on
those image URLs → blank pickers and an empty editor preview (GitHub #28).

This router exposes the same two read-only image endpoints under `/assets/...`,
gated only by `require_login` (auth OFF → everyone passes, identical to before).
It exposes **images only** — no list / upload / edit / delete — so non-admins
can view shared assets but not manage them.

**登入不等於看得到每一種資產**（issue #54，2026-10-05）：公司印章與簽名的圖只給
有用印權限的人，浮水印給有浮水印權限的人（規則在 `core.asset_access`）。只要登入
就給的話，沒有用印權限的人拿到印章 PNG 再貼進 PDF 編輯器，權限就形同虛設。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from ..core import asset_access
from ..core.asset_manager import asset_manager
from .deps import require_login

router = APIRouter()


@router.get("/assets/{asset_id}/file")
async def asset_file(asset_id: str, request: Request, _user: dict = Depends(require_login)):
    asset = asset_manager.get(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if not asset_access.can_use_asset(request, asset):
        raise HTTPException(403, asset_access.denied_message(asset))
    path = asset_manager.file_path(asset)
    if not path.exists():
        raise HTTPException(404, "asset file missing")
    return FileResponse(str(path), media_type="image/png")


@router.get("/assets/{asset_id}/thumb")
async def asset_thumb(asset_id: str, request: Request, _user: dict = Depends(require_login)):
    asset = asset_manager.get(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if not asset_access.can_use_asset(request, asset):
        raise HTTPException(403, asset_access.denied_message(asset))
    path = asset_manager.thumb_path(asset)
    if not path.exists():
        # Fall back to the full image if the thumbnail is somehow missing.
        path = asset_manager.file_path(asset)
        if not path.exists():
            raise HTTPException(404, "asset thumb missing")
    return FileResponse(str(path), media_type="image/png")


def build_router() -> APIRouter:
    return router

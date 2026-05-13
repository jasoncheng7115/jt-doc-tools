"""統編查詢 endpoints。

GET  /         — 查詢介面
POST /lookup   — 單筆查詢（接受 form / json，回 JSON）
POST /batch    — 批次查詢（多筆 vat 一次回，CSV 匯出友善）
GET  /db-info  — 統編資料庫狀態（給介面顯示「資料庫含 N 筆」）
公開 API：/api/vat-lookup/{vat} 已存在於 app.main，繼續沿用。
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ...core import vat_db

router = APIRouter()

_VAT_RE = re.compile(r"^\d{8}$")
_BATCH_MAX = 200


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    meta = vat_db.get_meta()
    return templates.TemplateResponse("vat_lookup.html", {
        "request": request,
        "meta": meta,
        "categories": vat_db.get_category_stats(),
    })


@router.get("/db-info")
async def db_info():
    return {
        "meta": vat_db.get_meta(),
        "categories": vat_db.get_category_stats(),
    }


@router.post("/lookup")
async def lookup(payload: dict):
    vat = (payload or {}).get("vat", "").strip()
    if not _VAT_RE.match(vat):
        raise HTTPException(400, "vat 必須是 8 位數字")
    result = vat_db.lookup_vat(vat)
    if not result:
        raise HTTPException(404, f"統編 {vat} 在資料庫中找不到")
    return result


@router.post("/search")
def search(payload: dict):
    """模糊搜尋（同步函數，FastAPI 自動 run in threadpool 避免阻塞 event loop）。"""
    query = (payload or {}).get("query", "")
    field = (payload or {}).get("field", "any")
    limit = (payload or {}).get("limit", 50)
    categories = (payload or {}).get("categories") or None
    if not isinstance(query, str) or len(query.strip()) < 2:
        raise HTTPException(400, "query 至少 2 個字元")
    try:
        results = vat_db.search_companies(
            query.strip(), field=field, limit=limit, categories=categories,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"results": results, "count": len(results), "query": query.strip()}


@router.post("/batch")
async def batch_lookup(payload: dict):
    vats = (payload or {}).get("vats", [])
    if not isinstance(vats, list):
        raise HTTPException(400, "vats 必須是陣列")
    if len(vats) > _BATCH_MAX:
        raise HTTPException(413, f"一次最多查 {_BATCH_MAX} 筆")
    results = []
    for v in vats:
        if not isinstance(v, str):
            results.append({"vat": str(v), "found": False, "error": "格式錯誤"})
            continue
        v = v.strip()
        if not _VAT_RE.match(v):
            results.append({"vat": v, "found": False, "error": "非 8 位數字"})
            continue
        info = vat_db.lookup_vat(v)
        if info:
            results.append({"vat": v, "found": True, **info})
        else:
            results.append({"vat": v, "found": False, "error": "資料庫查無此統編"})
    return {"results": results, "count": len(results),
            "found_count": sum(1 for r in results if r.get("found"))}


# ── 公開 REST API alias（feedback_api_coverage：每個工具都需 callable API）
@router.post("/api/vat-lookup")
async def public_lookup(payload: dict):
    return await lookup(payload)


@router.post("/api/vat-lookup/batch")
async def public_batch(payload: dict):
    return await batch_lookup(payload)

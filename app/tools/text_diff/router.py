"""Endpoints for 文字差異比對 — paste-two-blocks variant of doc-diff."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

# Reuse the proven line + char diff from the document tool so behaviour
# stays identical across the two entry points.
from ..pdf_diff.router import _diff_pages

router = APIRouter()

# Hard upper bound to keep request handling cheap and prevent abuse.
# 1 MiB of pasted text is already ~16k lines — well past anything a human
# would diff in this UI. Beyond it the sensible answer is "use a real
# diff tool offline".
_MAX_BYTES = 1_048_576  # 1 MiB per side


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("text_diff.html", {"request": request})


def _split_lines(s: str) -> list[str]:
    # `splitlines()` is universal-newline aware (handles \r\n / \r / \n)
    # and drops the trailing empty line difflib finds annoying.
    return [ln.rstrip() for ln in (s or "").splitlines()]


@router.post("/compare")
async def compare(request: Request):
    """Accept JSON { text_a, text_b } and return the same shape as
    `/tools/doc-diff/compare` so the UI can reuse the renderer."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    a_raw = body.get("text_a")
    b_raw = body.get("text_b")
    if not isinstance(a_raw, str) or not isinstance(b_raw, str):
        raise HTTPException(400, "text_a / text_b 必須是字串")
    if len(a_raw.encode("utf-8")) > _MAX_BYTES or \
       len(b_raw.encode("utf-8")) > _MAX_BYTES:
        raise HTTPException(413, f"單側文字超過 {_MAX_BYTES // 1024} KiB 上限")

    a_lines = _split_lines(a_raw)
    b_lines = _split_lines(b_raw)
    d = _diff_pages(a_lines, b_lines)
    # Wrap as one-page output so the renderer can stay shared with doc-diff.
    return JSONResponse({
        "page_count_a": 1 if a_lines or a_raw else 0,
        "page_count_b": 1 if b_lines or b_raw else 0,
        "totals": {
            "added":         d["added"],
            "removed":       d["removed"],
            "changed":       d["changed"],
            "chars_added":   d["chars_added"],
            "chars_removed": d["chars_removed"],
            "chars_changed": d["chars_changed"],
            "chars_a":       d["chars_a"],
            "chars_b":       d["chars_b"],
        },
        "metadata_diff": [],
        "pages": [{
            "index": 1,
            "a_exists": True,
            "b_exists": True,
            "diff": d,
        }],
    })


@router.post("/api/text-diff")
async def api_text_diff(request: Request):
    """Public alias — same shape as /compare."""
    return await compare(request)

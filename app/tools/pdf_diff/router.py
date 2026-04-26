"""Endpoints for PDF 差異比對."""
from __future__ import annotations

import difflib
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import settings


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_diff.html", {"request": request})


def _page_lines(doc: "fitz.Document") -> list[list[str]]:
    """Return a list[page][line_text]."""
    pages = []
    for pno in range(doc.page_count):
        text = doc[pno].get_text("text") or ""
        lines = [ln.rstrip() for ln in text.splitlines()]
        pages.append(lines)
    return pages


def _diff_pages(a_lines: list[str], b_lines: list[str]) -> dict:
    """Return a line-level diff structure for two pages:

        {
            "a":   [{text, tag}],   # tag ∈ {"equal","delete","replace"}
            "b":   [{text, tag}],   # tag ∈ {"equal","insert","replace"}
            "added":  int,
            "removed": int,
            "changed": int,
        }

    The ``replace`` tag pairs up across a/b at the same visual row so the
    UI can align them side-by-side.
    """
    sm = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
    a_out: list[dict] = []
    b_out: list[dict] = []
    added = removed = changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                a_out.append({"text": a_lines[k], "tag": "equal"})
                b_out.append({"text": b_lines[j1 + (k - i1)], "tag": "equal"})
        elif tag == "delete":
            for k in range(i1, i2):
                a_out.append({"text": a_lines[k], "tag": "delete"})
                b_out.append({"text": "", "tag": "blank"})
                removed += 1
        elif tag == "insert":
            for k in range(j1, j2):
                a_out.append({"text": "", "tag": "blank"})
                b_out.append({"text": b_lines[k], "tag": "insert"})
                added += 1
        elif tag == "replace":
            la = i2 - i1
            lb = j2 - j1
            # Pair up top rows, fill shorter side with blanks
            rows = max(la, lb)
            for k in range(rows):
                ai = i1 + k if k < la else None
                bi = j1 + k if k < lb else None
                a_out.append({"text": a_lines[ai] if ai is not None else "",
                              "tag": "replace" if ai is not None else "blank"})
                b_out.append({"text": b_lines[bi] if bi is not None else "",
                              "tag": "replace" if bi is not None else "blank"})
                if ai is not None and bi is not None:
                    changed += 1
                elif ai is not None:
                    removed += 1
                else:
                    added += 1
    return {"a": a_out, "b": b_out,
            "added": added, "removed": removed, "changed": changed}


def _metadata_diff(a_meta: dict, b_meta: dict) -> list[dict]:
    keys = sorted(set(a_meta) | set(b_meta))
    rows = []
    for k in keys:
        av = a_meta.get(k) or ""
        bv = b_meta.get(k) or ""
        if av == bv:
            continue
        rows.append({"key": k, "old": str(av), "new": str(bv)})
    return rows


@router.post("/compare")
async def compare(
    file_a: UploadFile = File(...),
    file_b: UploadFile = File(...),
):
    for f in (file_a, file_b):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
    data_a = await file_a.read()
    data_b = await file_b.read()
    if not data_a or not data_b:
        raise HTTPException(400, "empty file")

    uid = uuid.uuid4().hex
    pa = settings.temp_dir / f"diff_{uid}_a.pdf"
    pb = settings.temp_dir / f"diff_{uid}_b.pdf"
    pa.write_bytes(data_a)
    pb.write_bytes(data_b)

    import asyncio as _asyncio
    def _do_diff():
        with fitz.open(str(pa)) as da, fitz.open(str(pb)) as db:
            a_pages = _page_lines(da)
            b_pages = _page_lines(db)
            meta_diff = _metadata_diff(dict(da.metadata or {}),
                                       dict(db.metadata or {}))
            a_page_count = da.page_count
            b_page_count = db.page_count
        page_count = max(a_page_count, b_page_count)
        pages_out = []
        totals = {"added": 0, "removed": 0, "changed": 0}
        for i in range(page_count):
            ap = a_pages[i] if i < a_page_count else []
            bp = b_pages[i] if i < b_page_count else []
            d = _diff_pages(ap, bp)
            totals["added"]   += d["added"]
            totals["removed"] += d["removed"]
            totals["changed"] += d["changed"]
            pages_out.append({
                "index": i + 1,
                "a_exists": i < a_page_count,
                "b_exists": i < b_page_count,
                "diff": d,
            })
        return a_page_count, b_page_count, pages_out, totals, meta_diff
    a_page_count, b_page_count, pages_out, totals, meta_diff = await _asyncio.to_thread(_do_diff)

    return {
        "filename_a": file_a.filename,
        "filename_b": file_b.filename,
        "pages": pages_out,
        "page_count_a": a_page_count,
        "page_count_b": b_page_count,
        "totals": totals,
        "metadata_diff": meta_diff,
    }

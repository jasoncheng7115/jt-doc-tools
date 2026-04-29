"""Endpoints for 文件差異比對 (formerly PDF 差異比對).

Accepts PDF directly, or Word / Excel / PowerPoint / ODF — non-PDF inputs
are first converted to PDF via the shared OxOffice / LibreOffice helper,
then the same line-level diff runs against the rendered text.
"""
from __future__ import annotations

import difflib
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from ...config import settings
from ...core import office_convert


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_diff.html", {"request": request})


def _ensure_pdf(upload: UploadFile, data: bytes, uid: str, slot: str) -> Path:
    """Persist `data` and return a Path to a PDF representation of it.

    PDFs pass through unchanged; Office / ODF inputs are converted via
    soffice. Raises HTTPException(400) for unsupported types and
    HTTPException(500) if soffice itself fails or isn't installed.
    """
    name = (upload.filename or "").lower()
    is_pdf = name.endswith(".pdf")
    is_office = office_convert.is_office_file(name)
    if not (is_pdf or is_office):
        raise HTTPException(
            400,
            f"不支援的檔案類型：{upload.filename}（只接受 PDF / Word / Excel / "
            "PowerPoint / ODT / ODS / ODP）",
        )
    if is_pdf:
        out = settings.temp_dir / f"diff_{uid}_{slot}.pdf"
        out.write_bytes(data)
        return out
    # Office / ODF → write source, convert to PDF.
    suffix = Path(upload.filename or "in.bin").suffix or ".bin"
    src = settings.temp_dir / f"diff_{uid}_{slot}_src{suffix}"
    out = settings.temp_dir / f"diff_{uid}_{slot}.pdf"
    src.write_bytes(data)
    try:
        office_convert.convert_to_pdf(src, out)
    except FileNotFoundError as e:
        raise HTTPException(
            500,
            "找不到 Office 引擎（OxOffice / LibreOffice）— Office / ODF 檔案"
            "需要 soffice 才能轉成 PDF 後比對。",
        ) from e
    except Exception as e:
        raise HTTPException(
            500,
            f"Office 檔轉 PDF 失敗：{upload.filename}（{e}）",
        ) from e
    finally:
        # source bytes no longer needed
        src.unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        raise HTTPException(500, f"Office 檔轉 PDF 後檔案為空：{upload.filename}")
    return out


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
    # Char-level deltas alongside the line-level counts. Useful when the
    # line counts look small but each line has a lot of changed text.
    chars_added = chars_removed = chars_changed = 0
    chars_a = chars_b = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i1, i2):
                a_out.append({"text": a_lines[k], "tag": "equal"})
                b_out.append({"text": b_lines[j1 + (k - i1)], "tag": "equal"})
                chars_a += len(a_lines[k])
                chars_b += len(b_lines[j1 + (k - i1)])
        elif tag == "delete":
            for k in range(i1, i2):
                a_out.append({"text": a_lines[k], "tag": "delete"})
                b_out.append({"text": "", "tag": "blank"})
                removed += 1
                chars_removed += len(a_lines[k])
                chars_a += len(a_lines[k])
        elif tag == "insert":
            for k in range(j1, j2):
                a_out.append({"text": "", "tag": "blank"})
                b_out.append({"text": b_lines[k], "tag": "insert"})
                added += 1
                chars_added += len(b_lines[k])
                chars_b += len(b_lines[k])
        elif tag == "replace":
            la = i2 - i1
            lb = j2 - j1
            # Pair up top rows, fill shorter side with blanks
            rows = max(la, lb)
            for k in range(rows):
                ai = i1 + k if k < la else None
                bi = j1 + k if k < lb else None
                a_text = a_lines[ai] if ai is not None else ""
                b_text = b_lines[bi] if bi is not None else ""
                a_out.append({"text": a_text,
                              "tag": "replace" if ai is not None else "blank"})
                b_out.append({"text": b_text,
                              "tag": "replace" if bi is not None else "blank"})
                chars_a += len(a_text)
                chars_b += len(b_text)
                if ai is not None and bi is not None:
                    changed += 1
                    # On a paired replace, count the per-char edit distance
                    # between the two lines so a 1-char tweak doesn't show
                    # up the same as a fully-rewritten paragraph.
                    s = difflib.SequenceMatcher(None, a_text, b_text,
                                                autojunk=False)
                    for t2, ai2, ai3, bi2, bi3 in s.get_opcodes():
                        if t2 == "equal":
                            continue
                        if t2 == "delete":
                            chars_removed += ai3 - ai2
                        elif t2 == "insert":
                            chars_added += bi3 - bi2
                        elif t2 == "replace":
                            chars_changed += max(ai3 - ai2, bi3 - bi2)
                elif ai is not None:
                    removed += 1
                    chars_removed += len(a_text)
                else:
                    added += 1
                    chars_added += len(b_text)
    return {"a": a_out, "b": b_out,
            "added": added, "removed": removed, "changed": changed,
            "chars_added": chars_added,
            "chars_removed": chars_removed,
            "chars_changed": chars_changed,
            "chars_a": chars_a, "chars_b": chars_b}


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
    data_a = await file_a.read()
    data_b = await file_b.read()
    if not data_a or not data_b:
        raise HTTPException(400, "empty file")

    uid = uuid.uuid4().hex
    pa = _ensure_pdf(file_a, data_a, uid, "a")
    pb = _ensure_pdf(file_b, data_b, uid, "b")

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
        totals = {
            "added": 0, "removed": 0, "changed": 0,
            "chars_added": 0, "chars_removed": 0, "chars_changed": 0,
            "chars_a": 0, "chars_b": 0,
        }
        for i in range(page_count):
            ap = a_pages[i] if i < a_page_count else []
            bp = b_pages[i] if i < b_page_count else []
            d = _diff_pages(ap, bp)
            for k in totals:
                totals[k] += d.get(k, 0)
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

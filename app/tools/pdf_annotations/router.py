"""Endpoints for PDF 註解整理."""
from __future__ import annotations

import csv
import io
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ...config import settings
from ...core.http_utils import content_disposition


router = APIRouter()

# PyMuPDF annotation type id → English name → friendly Chinese label.
# We only surface the types that real reviewers create. Link / Popup /
# Widget (form fields) are filtered out — they'd flood the list.
_TYPE_LABELS = {
    0:  ("Text",            "文字註解"),
    2:  ("FreeText",        "自由文字"),
    3:  ("Line",            "線條"),
    4:  ("Square",          "矩形"),
    5:  ("Circle",          "橢圓"),
    6:  ("Polygon",         "多邊形"),
    7:  ("PolyLine",        "折線"),
    8:  ("Highlight",       "螢光筆"),
    9:  ("Underline",       "底線"),
    10: ("Squiggly",        "波浪線"),
    11: ("StrikeOut",       "刪除線"),
    12: ("Stamp",           "圖章"),
    13: ("Caret",           "插入符"),
    14: ("Ink",             "手繪"),
    16: ("FileAttachment",  "檔案附件"),
}
_USER_TYPE_IDS = set(_TYPE_LABELS.keys())


def _annot_to_dict(idx: int, page_no: int, a: fitz.Annot) -> dict[str, Any]:
    info = a.info or {}
    type_id, type_name = a.type
    rect = a.rect
    en, zh = _TYPE_LABELS.get(type_id, (type_name or "Unknown", type_name or "未知"))
    return {
        "idx":         idx,
        "page":        page_no + 1,
        "type_id":     type_id,
        "type":        en,
        "type_label":  zh,
        "author":      (info.get("title") or "").strip(),
        "subject":     (info.get("subject") or "").strip(),
        "content":     (info.get("content") or "").strip(),
        "created":     info.get("creationDate") or "",
        "modified":    info.get("modDate") or "",
        "rect":        [round(rect.x0, 1), round(rect.y0, 1),
                        round(rect.x1, 1), round(rect.y1, 1)],
    }


def _read_annotations(pdf_path: Path) -> list[dict[str, Any]]:
    """Walk all pages and return user-facing annotations as plain dicts."""
    out: list[dict[str, Any]] = []
    idx = 0
    with fitz.open(str(pdf_path)) as doc:
        if doc.needs_pass:
            raise HTTPException(400, "PDF 已加密,請先解密再分析")
        for pno in range(doc.page_count):
            page = doc[pno]
            for a in page.annots() or []:
                tid = a.type[0]
                if tid not in _USER_TYPE_IDS:
                    continue
                # For highlights/underlines etc. the "content" field is often
                # empty — try to recover the actual highlighted text from
                # the annotation's quad rectangles.
                if tid in (8, 9, 10, 11):
                    info = a.info or {}
                    if not (info.get("content") or "").strip():
                        try:
                            quads = a.vertices
                            if quads:
                                # quads come as list of 4 points per highlight.
                                # Walk in groups of 4 and union into rects.
                                texts = []
                                for i in range(0, len(quads), 4):
                                    pts = quads[i:i+4]
                                    if len(pts) < 4:
                                        continue
                                    xs = [p[0] for p in pts]
                                    ys = [p[1] for p in pts]
                                    r = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
                                    txt = page.get_textbox(r) or ""
                                    if txt.strip():
                                        texts.append(txt.strip())
                                if texts:
                                    a.set_info(content=" ".join(texts))
                        except Exception:
                            pass
                out.append(_annot_to_dict(idx, pno, a))
                idx += 1
    return out


def _summarize(annots: list[dict[str, Any]], page_count: int) -> dict[str, Any]:
    by_type   = Counter(a["type_label"] for a in annots)
    by_author = Counter(a["author"] or "(未署名)" for a in annots)
    pages_with_annot = len({a["page"] for a in annots})
    return {
        "total":            len(annots),
        "page_count":       page_count,
        "pages_with_annot": pages_with_annot,
        "by_type":          [{"label": k, "count": v} for k, v in by_type.most_common()],
        "by_author":        [{"author": k, "count": v} for k, v in by_author.most_common()],
    }


def _filter_annots(annots: list[dict[str, Any]],
                   types: list[str] | None,
                   authors: list[str] | None) -> list[dict[str, Any]]:
    if types:
        wanted = set(types)
        annots = [a for a in annots if a["type"] in wanted]
    if authors:
        wanted_a = set(authors)
        annots = [a for a in annots
                  if (a["author"] or "(未署名)") in wanted_a or a["author"] in wanted_a]
    return annots


def _group(annots: list[dict[str, Any]], by: str) -> list[dict[str, Any]]:
    """Return list of {key, items}. by ∈ page / author / type."""
    keyfn = {
        "page":   lambda a: f"第 {a['page']} 頁",
        "author": lambda a: a["author"] or "(未署名)",
        "type":   lambda a: a["type_label"],
    }.get(by, lambda a: f"第 {a['page']} 頁")
    buckets: dict[str, list] = {}
    for a in annots:
        buckets.setdefault(keyfn(a), []).append(a)
    return [{"key": k, "items": v} for k, v in buckets.items()]


# ---------- format renderers ----------

def _render_csv(annots: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM for Excel
    w = csv.writer(buf)
    w.writerow(["page", "type", "author", "subject", "content",
                "created", "modified", "x0", "y0", "x1", "y1"])
    for a in annots:
        w.writerow([a["page"], a["type_label"], a["author"], a["subject"],
                    a["content"], a["created"], a["modified"],
                    *a["rect"]])
    return buf.getvalue().encode("utf-8")


def _render_review_md(annots: list[dict[str, Any]], group_by: str,
                      filename: str) -> bytes:
    lines = [f"# 註解審閱報告 — {filename}", "",
             f"共 {len(annots)} 條註解。", ""]
    for grp in _group(annots, group_by):
        lines.append(f"## {grp['key']}")
        lines.append("")
        for a in grp["items"]:
            who = a["author"] or "(未署名)"
            content = a["content"] or "(無內容)"
            lines.append(f"- **第 {a['page']} 頁** · {a['type_label']} · {who}")
            for ln in content.splitlines():
                lines.append(f"  > {ln}")
            lines.append("")
    return "\n".join(lines).encode("utf-8")


def _render_todo_md(annots: list[dict[str, Any]], filename: str) -> bytes:
    lines = [f"# 待辦清單 — {filename}", "",
             f"由 {len(annots)} 條註解產生。", ""]
    for a in annots:
        who = a["author"] or "?"
        body = a["content"] or a["subject"] or a["type_label"]
        lines.append(f"- [ ] **第 {a['page']} 頁**: {body} _(來源:{who} · {a['type_label']})_")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _render_todo_csv(annots: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf)
    w.writerow(["status", "page", "todo", "assignee", "priority", "type", "notes"])
    for a in annots:
        body = a["content"] or a["subject"] or a["type_label"]
        w.writerow(["[ ]", a["page"], body, a["author"], "", a["type_label"], ""])
    return buf.getvalue().encode("utf-8")


# ---------- helpers ----------

async def _save_upload(file: UploadFile) -> tuple[Path, str]:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    src = settings.temp_dir / f"annot_{uuid.uuid4().hex}_in.pdf"
    src.write_bytes(data)
    return src, file.filename or "document.pdf"


# ---------- routes ----------

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_annotations.html", {"request": request})


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    src, fname = await _save_upload(file)
    try:
        with fitz.open(str(src)) as doc:
            pc = doc.page_count
        annots = _read_annotations(src)
        return JSONResponse({
            "filename":   fname,
            "page_count": pc,
            "summary":    _summarize(annots, pc),
            "annots":     annots,
        })
    finally:
        src.unlink(missing_ok=True)


@router.post("/api/pdf-annotations")
async def api_annotations(file: UploadFile = File(...)):
    return await analyze(file)


def _parse_csv_list(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


@router.post("/export-csv")
async def export_csv(
    file: UploadFile = File(...),
    types: str = Form(""),
    authors: str = Form(""),
):
    src, fname = await _save_upload(file)
    try:
        annots = _filter_annots(_read_annotations(src),
                                _parse_csv_list(types),
                                _parse_csv_list(authors))
        body = _render_csv(annots)
        base = Path(fname).stem
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 content_disposition(f"{base}_annotations.csv")})
    finally:
        src.unlink(missing_ok=True)


@router.post("/export-review")
async def export_review(
    file: UploadFile = File(...),
    types: str = Form(""),
    authors: str = Form(""),
    group_by: str = Form("page"),
):
    src, fname = await _save_upload(file)
    try:
        annots = _filter_annots(_read_annotations(src),
                                _parse_csv_list(types),
                                _parse_csv_list(authors))
        body = _render_review_md(annots, group_by, fname)
        base = Path(fname).stem
        return Response(body, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition":
                                 content_disposition(f"{base}_review.md")})
    finally:
        src.unlink(missing_ok=True)


@router.post("/export-todo")
async def export_todo(
    file: UploadFile = File(...),
    types: str = Form(""),
    authors: str = Form(""),
    fmt: str = Form("md"),
):
    src, fname = await _save_upload(file)
    try:
        annots = _filter_annots(_read_annotations(src),
                                _parse_csv_list(types),
                                _parse_csv_list(authors))
        base = Path(fname).stem
        if fmt == "csv":
            body = _render_todo_csv(annots)
            return Response(body, media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition":
                                     content_disposition(f"{base}_todo.csv")})
        body = _render_todo_md(annots, fname)
        return Response(body, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition":
                                 content_disposition(f"{base}_todo.md")})
    finally:
        src.unlink(missing_ok=True)


@router.post("/export-json")
async def export_json(
    file: UploadFile = File(...),
    types: str = Form(""),
    authors: str = Form(""),
):
    src, fname = await _save_upload(file)
    try:
        with fitz.open(str(src)) as doc:
            pc = doc.page_count
        annots = _filter_annots(_read_annotations(src),
                                _parse_csv_list(types),
                                _parse_csv_list(authors))
        body = json.dumps({
            "filename":   fname,
            "page_count": pc,
            "summary":    _summarize(annots, pc),
            "annots":     annots,
        }, ensure_ascii=False, indent=2).encode("utf-8")
        base = Path(fname).stem
        return Response(body, media_type="application/json; charset=utf-8",
                        headers={"Content-Disposition":
                                 content_disposition(f"{base}_annotations.json")})
    finally:
        src.unlink(missing_ok=True)

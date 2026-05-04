"""Endpoints for PDF 註解整理."""
from __future__ import annotations

import csv
import io
import json
import re
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ...config import settings
from ...core.http_utils import content_disposition

_UPLOAD_ID_RE = re.compile(r"^[a-f0-9]{32}$")


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
            raise HTTPException(400, "PDF 已加密，請先解密再分析")
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

async def _save_upload(file: UploadFile) -> tuple[Path, str, str]:
    """Save the upload to a uuid-keyed temp path. Returns (path, filename, upload_id)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"annot_{upload_id}_in.pdf"
    src.write_bytes(data)
    return src, file.filename or "document.pdf", upload_id


def _validate_upload_id(upload_id: str) -> None:
    if not _UPLOAD_ID_RE.match(upload_id or ""):
        raise HTTPException(400, "invalid upload_id")


def _cached_paths(upload_id: str) -> tuple[Path, Path]:
    """Return (pdf_path, sidecar_json_path). No existence check."""
    return (
        settings.temp_dir / f"annot_{upload_id}_in.pdf",
        settings.temp_dir / f"annot_{upload_id}_data.json",
    )


def _load_cached(upload_id: str) -> dict[str, Any]:
    """Load analyzed payload from sidecar JSON. Raises 410 if expired."""
    _validate_upload_id(upload_id)
    _, sidecar = _cached_paths(upload_id)
    if not sidecar.exists():
        raise HTTPException(410, "上傳已過期，請重新分析")
    return json.loads(sidecar.read_text(encoding="utf-8"))


# ---------- routes ----------

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    from ...core.llm_settings import llm_settings
    return templates.TemplateResponse("pdf_annotations.html", {
        "request": request,
        "llm_enabled": llm_settings.is_enabled(),
        "llm_model": llm_settings.get_model_for("pdf-annotations") if llm_settings.is_enabled() else "",
    })


async def _llm_group_annots(annots: list[dict[str, Any]]) -> dict[str, Any]:
    """Ask LLM to cluster annotations into themed categories.

    Returns {groups: [{name, summary, indices: [int]}], model: str} or
    {error, model} on failure. Each annotation is identified by its global
    'idx' (assigned during _read_annotations)."""
    from ...core.llm_settings import llm_settings as _llms
    import asyncio as _asyncio
    client = _llms.make_client()
    if client is None:
        return {}
    model = _llms.get_model_for("pdf-annotations")
    if not annots:
        return {}
    items = []
    for a in annots[:200]:
        body = (a.get("content") or a.get("subject") or "").strip()
        if not body:
            body = a.get("type_label") or ""
        body = body[:200]
        items.append({
            "id":   a["idx"],
            "page": a["page"],
            "type": a["type_label"],
            "by":   a["author"] or "(未署名)",
            "text": body,
        })
    payload_json = json.dumps(items, ensure_ascii=False)
    prompt = (
        "你是文件審閱助手。下面是 PDF 上的所有註解 (JSON list)，請依照"
        "「註解的內容主題」自動分組（例：『需修改文字』『格式問題』"
        "『詢問疑點』『已確認』『其他』等，由你判斷）。每組請給簡短"
        "中文名稱與一句話描述，並列出該組成員的 id。\n"
        "**只能回 JSON，不要 markdown / 解釋 / 前綴 / 後綴 / ```json``` 包裝。**\n"
        "格式：{\"groups\": [{\"name\": \"...\", \"summary\": \"...\", \"indices\": [id, id, ...]}, ...]}\n\n"
        f"註解資料：\n{payload_json}"
    )
    def _call():
        return client.text_query(prompt=prompt, model=model,
                                  temperature=0.0, think=False)
    try:
        resp = await _asyncio.to_thread(_call)
    except Exception as e:
        return {"error": str(e), "model": model}
    raw = (resp or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        parsed = json.loads(raw)
        groups = parsed.get("groups") if isinstance(parsed, dict) else None
        if not isinstance(groups, list):
            return {"error": "LLM 回應缺少 groups 欄位", "model": model}
        cleaned = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            ids = [int(x) for x in (g.get("indices") or []) if isinstance(x, (int, float))]
            cleaned.append({
                "name":    str(g.get("name") or "未分類").strip(),
                "summary": str(g.get("summary") or "").strip(),
                "indices": ids,
            })
        return {"groups": cleaned, "model": model}
    except Exception as e:
        return {"error": f"LLM 回應解析失敗：{e}", "model": model,
                "raw": raw[:300]}


@router.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    llm_group: str = Form(""),
):
    """Analyze the upload and persist results.

    The PDF is kept at ``annot_{uid}_in.pdf`` and a JSON sidecar at
    ``annot_{uid}_data.json`` so that subsequent exports + page previews
    can reuse the analysis without re-parsing the PDF (which is slow on
    large files because of highlight-text recovery).

    Both files get cleaned by the data-retention scheduler (2-hour TTL on
    temp uploads).
    """
    src, fname, upload_id = await _save_upload(file)
    with fitz.open(str(src)) as doc:
        pc = doc.page_count
    annots = _read_annotations(src)
    payload = {
        "filename":   fname,
        "upload_id":  upload_id,
        "page_count": pc,
        "summary":    _summarize(annots, pc),
        "annots":     annots,
    }
    if str(llm_group).lower() in ("1", "true", "on", "yes"):
        try:
            llm_extra = await _llm_group_annots(annots)
            if llm_extra:
                payload["llm"] = llm_extra
        except Exception as exc:
            import logging as _lg
            _lg.getLogger(__name__).warning("LLM grouping failed: %s", exc)
            payload["llm"] = {"error": str(exc)}
    # Save sidecar; PDF stays for /preview thumbnails.
    _, sidecar = _cached_paths(upload_id)
    sidecar.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return JSONResponse(payload)


@router.post("/api/pdf-annotations")
async def api_annotations(file: UploadFile = File(...)):
    return await analyze(file)


def _parse_csv_list(s: str | None) -> list[str]:
    if not s:
        return []
    return [t.strip() for t in s.split(",") if t.strip()]


@router.get("/preview/{upload_id}/{page}")
async def preview(upload_id: str, page: int):
    """Render one page (with annotations baked in by the renderer) as a thumbnail PNG."""
    _validate_upload_id(upload_id)
    if page < 1:
        raise HTTPException(400, "invalid page")
    src, _ = _cached_paths(upload_id)
    if not src.exists():
        raise HTTPException(410, "上傳已過期，請重新分析")
    with fitz.open(str(src)) as doc:
        if page > doc.page_count:
            raise HTTPException(404, "page out of range")
        # ~100 DPI thumbnail; PyMuPDF renders annotations onto the pixmap by default.
        mat = fitz.Matrix(1.4, 1.4)
        pix = doc[page - 1].get_pixmap(matrix=mat, alpha=False)
        png = pix.tobytes("png")
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=600"})


@router.post("/export-csv")
async def export_csv(
    upload_id: str = Form(...),
    types: str = Form(""),
    authors: str = Form(""),
):
    cached = _load_cached(upload_id)
    annots = _filter_annots(cached["annots"],
                            _parse_csv_list(types),
                            _parse_csv_list(authors))
    body = _render_csv(annots)
    base = Path(cached["filename"]).stem
    return Response(body, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             content_disposition(f"{base}_annotations.csv")})


@router.post("/export-review")
async def export_review(
    upload_id: str = Form(...),
    types: str = Form(""),
    authors: str = Form(""),
    group_by: str = Form("page"),
):
    cached = _load_cached(upload_id)
    annots = _filter_annots(cached["annots"],
                            _parse_csv_list(types),
                            _parse_csv_list(authors))
    body = _render_review_md(annots, group_by, cached["filename"])
    base = Path(cached["filename"]).stem
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition":
                             content_disposition(f"{base}_review.md")})


@router.post("/export-todo")
async def export_todo(
    upload_id: str = Form(...),
    types: str = Form(""),
    authors: str = Form(""),
    fmt: str = Form("md"),
):
    cached = _load_cached(upload_id)
    annots = _filter_annots(cached["annots"],
                            _parse_csv_list(types),
                            _parse_csv_list(authors))
    base = Path(cached["filename"]).stem
    if fmt == "csv":
        body = _render_todo_csv(annots)
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 content_disposition(f"{base}_todo.csv")})
    body = _render_todo_md(annots, cached["filename"])
    return Response(body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition":
                             content_disposition(f"{base}_todo.md")})


@router.post("/export-json")
async def export_json(
    upload_id: str = Form(...),
    types: str = Form(""),
    authors: str = Form(""),
):
    cached = _load_cached(upload_id)
    annots = _filter_annots(cached["annots"],
                            _parse_csv_list(types),
                            _parse_csv_list(authors))
    body = json.dumps({
        "filename":   cached["filename"],
        "page_count": cached["page_count"],
        "summary":    _summarize(annots, cached["page_count"]),
        "annots":     annots,
    }, ensure_ascii=False, indent=2).encode("utf-8")
    base = Path(cached["filename"]).stem
    return Response(body, media_type="application/json; charset=utf-8",
                    headers={"Content-Disposition":
                             content_disposition(f"{base}_annotations.json")})

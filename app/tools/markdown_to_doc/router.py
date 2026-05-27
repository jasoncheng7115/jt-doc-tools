"""Endpoints for the Markdown → PDF / DOCX / ODT tool.

Pipeline: markdown text → HTML (markdown-it-py) → wrap with theme CSS →
soffice headless → PDF / DOCX / ODT. PDF pages are also rendered to PNG via
PyMuPDF for the preview gallery.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import office_convert as _oc
from ...core import upload_owner as _uo
from ...core.safe_paths import require_uuid_hex
from . import themes

log = logging.getLogger("app.markdown_to_doc")

router = APIRouter()

_MAX_MARKDOWN_BYTES = 5 * 1024 * 1024   # 5 MB raw markdown
_PREVIEW_DPI = 96                        # preview PNG resolution
_PREVIEW_MAX_PAGES = 50                  # cap to avoid huge memory hits


def _work_dir(uid: str) -> Path:
    require_uuid_hex(uid, "upload_id")
    d = settings.temp_dir / f"md2doc_{uid}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_stem(filename: str) -> str:
    stem = Path(filename).stem if filename else "document"
    safe = re.sub(r"[^\w一-鿿\-]+", "_", stem)
    safe = safe.strip("_") or "document"
    return safe[:80]


def _render_md_html(md_text: str, theme_id: str, title: str, font_id: str = "default") -> str:
    """Convert markdown text to a full HTML document with theme CSS applied.

    font_id 為 'default' 時用主題內建字型;其他字型 ID 會 append 覆蓋 body CSS。
    """
    from markdown_it import MarkdownIt
    md = (
        MarkdownIt("commonmark", {"breaks": False, "linkify": True, "html": False})
        .enable("table")
        .enable("strikethrough")
    )
    body_html = md.render(md_text or "")
    theme = themes.get_theme(theme_id)
    font_css = themes.font_css_override(font_id or "default")
    # Light HTML escape on title (used in <title>)
    safe_title = (title or "Document").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>{safe_title}</title>
<style>
{theme["css"]}
{font_css}
</style>
</head>
<body>
{body_html}
</body>
</html>"""


def _render_pdf_previews(pdf_path: Path, out_dir: Path) -> list[Path]:
    """Render each PDF page as a PNG into out_dir. Returns list of PNG paths."""
    import fitz
    out_dir.mkdir(parents=True, exist_ok=True)
    pages: list[Path] = []
    with fitz.open(str(pdf_path)) as doc:
        n = min(doc.page_count, _PREVIEW_MAX_PAGES)
        zoom = _PREVIEW_DPI / 72.0
        mat = fitz.Matrix(zoom, zoom)
        for i in range(n):
            pix = doc[i].get_pixmap(matrix=mat, alpha=False)
            out = out_dir / f"page_{i + 1:03d}.png"
            pix.save(str(out))
            pages.append(out)
    return pages


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("markdown_to_doc.html", {
        "request": request,
        "themes": themes.theme_options(),
        "fonts": themes.font_options(),
    })


@router.post("/convert")
async def convert(
    request: Request,
    text: str = Form(""),
    file: Optional[UploadFile] = File(None),
    theme: str = Form("classic"),
    font: str = Form("default"),
    title: str = Form(""),
):
    """Convert markdown (from text body or uploaded file) to PDF + DOCX + ODT.

    Returns JSON with upload_id, page count, preview URLs, download URLs."""
    md_text = text or ""
    file_stem = "document"
    if file is not None and file.filename:
        data = await file.read()
        if data:
            try:
                md_text = data.decode("utf-8", errors="replace")
            except Exception:
                raise HTTPException(400, "檔案不是 UTF-8 文字")
            file_stem = _safe_stem(file.filename)
    md_bytes = md_text.encode("utf-8")
    if not md_bytes.strip():
        raise HTTPException(400, "請貼上 Markdown 內容或上傳 .md 檔")
    if len(md_bytes) > _MAX_MARKDOWN_BYTES:
        raise HTTPException(400, f"Markdown 超過上限 {_MAX_MARKDOWN_BYTES // 1024 // 1024} MB")
    if theme not in themes.THEMES:
        theme = "classic"
    stem = _safe_stem(title) if title else file_stem
    if not stem:
        stem = "document"

    uid = uuid.uuid4().hex
    _uo.record(uid, request)
    wdir = _work_dir(uid)

    def _do():
        # 1. markdown → HTML with theme
        html = _render_md_html(md_text, theme, stem, font)
        html_path = wdir / f"{stem}.html"
        html_path.write_text(html, encoding="utf-8")
        # 2. HTML → PDF + DOCX + ODT via soffice
        pdf_path = wdir / f"{stem}.pdf"
        docx_path = wdir / f"{stem}.docx"
        odt_path = wdir / f"{stem}.odt"
        errors: dict[str, str] = {}
        try:
            _oc.convert_to_pdf(html_path, pdf_path, timeout=120.0)
        except Exception as e:
            errors["pdf"] = str(e)
            log.exception("md→pdf failed")
        # HTML → ODT 直轉 OK,DOCX 直轉 soffice filter chain 常失敗 →
        # 先 HTML → ODT,再 ODT → DOCX 兩段轉檔保險
        try:
            _oc.convert_to_odt(html_path, odt_path, timeout=120.0)
        except Exception as e:
            errors["odt"] = str(e)
            log.exception("md→odt failed")
        try:
            if odt_path.exists():
                _oc.convert_to_docx(odt_path, docx_path, timeout=120.0)
            else:
                # fallback:沒 ODT 中介,直接從 HTML 試一次
                _oc.convert_to_docx(html_path, docx_path, timeout=120.0)
        except Exception as e:
            errors["docx"] = str(e)
            log.exception("md→docx failed")
        # 3. Render PDF preview pages
        previews: list[Path] = []
        if pdf_path.exists():
            try:
                previews = _render_pdf_previews(pdf_path, wdir / "previews")
            except Exception as e:
                log.exception("preview render failed")
                errors["preview"] = str(e)
        return pdf_path, docx_path, odt_path, previews, errors

    pdf_path, docx_path, odt_path, previews, errors = await asyncio.to_thread(_do)

    if not pdf_path.exists() and "pdf" in errors:
        raise HTTPException(500, f"轉檔失敗:{errors['pdf']}")

    return {
        "ok": True,
        "upload_id": uid,
        "stem": stem,
        "theme": theme,
        "page_count": len(previews),
        "char_count": len(md_text),
        "preview_urls": [
            f"/tools/markdown-to-doc/preview/{uid}/{i + 1}"
            for i in range(len(previews))
        ],
        "downloads": {
            "pdf":  f"/tools/markdown-to-doc/download/{uid}/pdf"  if pdf_path.exists() else None,
            "docx": f"/tools/markdown-to-doc/download/{uid}/docx" if docx_path.exists() else None,
            "odt":  f"/tools/markdown-to-doc/download/{uid}/odt"  if odt_path.exists() else None,
        },
        "errors": errors or None,
    }


@router.get("/preview/{upload_id}/{page}")
async def preview(request: Request, upload_id: str, page: int):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    wdir = _work_dir(upload_id)
    if page < 1 or page > _PREVIEW_MAX_PAGES:
        raise HTTPException(400, "page 超出範圍")
    png = wdir / "previews" / f"page_{page:03d}.png"
    if not png.exists():
        raise HTTPException(404, "預覽圖不存在")
    return FileResponse(str(png), media_type="image/png")


@router.get("/download/{upload_id}/{fmt}")
async def download(request: Request, upload_id: str, fmt: str):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    if fmt not in ("pdf", "docx", "odt"):
        raise HTTPException(400, "format 必須是 pdf / docx / odt")
    wdir = _work_dir(upload_id)
    media = {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "odt":  "application/vnd.oasis.opendocument.text",
    }[fmt]
    candidates = list(wdir.glob(f"*.{fmt}"))
    if not candidates:
        raise HTTPException(404, "輸出檔不存在或已過期")
    out = candidates[0]
    return FileResponse(str(out), media_type=media, filename=out.name)


# ---- public API (single-shot) -----------------------------------------

@router.post("/api/markdown-to-doc", include_in_schema=True)
async def api_markdown_to_doc(
    request: Request,
    text: str = Form(""),
    file: Optional[UploadFile] = File(None),
    theme: str = Form("classic"),
    font: str = Form("default"),
    title: str = Form(""),
    format: str = Form("pdf"),
):
    """Programmatic endpoint. Returns the converted file directly (not JSON)."""
    if format not in ("pdf", "docx", "odt"):
        raise HTTPException(400, "format 必須是 pdf / docx / odt")
    md_text = text or ""
    file_stem = "document"
    if file is not None and file.filename:
        data = await file.read()
        if data:
            md_text = data.decode("utf-8", errors="replace")
            file_stem = _safe_stem(file.filename)
    if not md_text.strip():
        raise HTTPException(400, "請提供 text 或 file")
    if len(md_text.encode("utf-8")) > _MAX_MARKDOWN_BYTES:
        raise HTTPException(400, "Markdown 過大")
    if theme not in themes.THEMES:
        theme = "classic"
    stem = _safe_stem(title) if title else file_stem

    uid = uuid.uuid4().hex
    wdir = _work_dir(uid)

    def _do():
        html = _render_md_html(md_text, theme, stem, font)
        html_path = wdir / f"{stem}.html"
        html_path.write_text(html, encoding="utf-8")
        target = wdir / f"{stem}.{format}"
        if format == "pdf":
            _oc.convert_to_pdf(html_path, target, timeout=120.0)
        elif format == "docx":
            _oc.convert_to_docx(html_path, target, timeout=120.0)
        else:
            _oc.convert_to_odt(html_path, target, timeout=120.0)
        return target

    target = await asyncio.to_thread(_do)
    media = {
        "pdf":  "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "odt":  "application/vnd.oasis.opendocument.text",
    }[format]
    return FileResponse(str(target), media_type=media, filename=target.name)

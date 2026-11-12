from __future__ import annotations
import asyncio as _asyncio
import time
import uuid
import zipfile
from pathlib import Path
from typing import List
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
import fitz
from ...config import settings
from ...core.job_manager import job_manager
from ...core import pdf_preview

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "pdf_pageno.html", {"request": request})


@router.post("/load")
async def load(request: Request, file: UploadFile = File(...)):
    """Stash the upload + return page count and thumbnail URLs (single file)."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data: raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    src = settings.temp_dir / f"pnL_{upload_id}.pdf"
    src.write_bytes(data)
    with fitz.open(str(src)) as doc:
        n = doc.page_count
    return {
        "upload_id": upload_id, "filename": file.filename, "page_count": n,
        "pages": [
            {"page": i + 1, "thumb": f"/tools/pdf-pageno/thumb/{upload_id}/{i + 1}"}
            for i in range(n)
        ],
    }


@router.get("/thumb/{upload_id}/{page}")
async def thumb(upload_id: str, page: int, request: Request, large: bool = False):
    from app.core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = settings.temp_dir / f"pnL_{upload_id}.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found (expired?)")
    suffix = "_large" if large else ""
    out = settings.temp_dir / f"pnL_{upload_id}_thumb{suffix}_{page}.png"
    if not out.exists():
        await pdf_preview.render_page_png_async(src, out, page - 1, dpi=160 if large else 64)
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


def _hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = (hex_color or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return (int(h[0:2], 16) / 255.0,
                int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    except Exception:
        return (0.0, 0.0, 0.0)


import re as _re
# 含 CJK 統一 / 擴展 / 假名 / 全形標點的字元範圍
_CJK_RE = _re.compile(
    r"[　-〿぀-ヿ㐀-䶿一-鿿豈-﫿＀-￯]")


def _pageno_font(text: str):
    """選頁碼字型。含 CJK（如「第 N 頁」）時，PyMuPDF 內建 helv 沒有中文 glyph
    會印成「·」缺字；改用真 CJK 字型。找不到系統 CJK 字型則退用 PyMuPDF 內建
    china-t（繁中，glyph 至少會顯示）。回 (fontname, fontfile|None, fontbuffer|None)。

    `.ttc` 要挑繁中那一套 —— 第 0 套通常是日文，用錯的話「第 N 頁」的「第」「頁」
    會是日文字形。PyMuPDF 沒有索引參數，只能把那一套抽成位元組用 fontbuffer 傳。
    """
    if not _CJK_RE.search(text or ""):
        return "helv", None, None
    try:
        from ...core import font_catalog
        best = font_catalog.best_cjk_path("sans", "traditional")
        if best:
            path, idx = best[0], (best[1] if len(best) > 1 else 0)
            # 只嵌這段文字用到的字 —— 不縮的話一支中文字型十幾 MB 會整份塞進
            # PDF，只為了印「第 1 頁」三個字。
            ff, buf = font_catalog.embeddable_font(path, idx, text=text)
            return "jtcjk", (str(ff) if ff else None), buf
    except Exception:
        pass
    return "china-t", None, None


def _text_width_pt(text: str, font_size: float) -> float:
    """估文字寬（pt）：CJK 全形 ≈ 1.0 em、半形 ≈ 0.55 em。給定位用。"""
    w = 0.0
    for c in text:
        w += 1.0 if _CJK_RE.match(c) else 0.55
    return font_size * w


def _draw_pageno(
    page, *, page_index: int, total: int,
    position: str, fmt: str, start: int,
    font_size: float, margin_mm: float, color_hex: str,
    from_page: int = 1, to_page: int | None = None,
) -> None:
    """Draw a page number on the given page. If the 1-based page number is
    outside [from_page, to_page], no-op (covers/TOC/back cover can be
    skipped this way).

    ``start`` is the number to print on the first *numbered* page; later
    pages increment from there. ``{N}`` in the format template reflects
    the count of *numbered* pages (to_page - from_page + 1), not the
    total page count of the PDF.
    """
    from ...core.unit_convert import mm_to_pt
    page_no = page_index + 1                 # 1-based index on paper
    if to_page is None:
        to_page = total
    if page_no < from_page or page_no > to_page:
        return                                # outside the numbering range
    numbered_total = max(1, to_page - from_page + 1)
    numbered_idx = page_no - from_page        # 0-based within range
    shown = numbered_idx + start
    # `{total}` 是 `{N}` 的別名：API 手冊有一段時間寫的是 `{total}`，照著呼叫的人
    # 拿到的是字面的「{total}」印在每一頁上（2026-09-24 Windows 實機測試抓到）。
    text = (fmt.replace("{n}", str(shown)).replace("{N}", str(numbered_total))
               .replace("{total}", str(numbered_total)))
    m_pt = mm_to_pt(margin_mm)
    # page.rect 含旋轉(visual rect)。insert_text 用內容座標(unrotated)。
    # 對旋轉過的頁(/Rotate 90/180/270),要把計算出的 visual 座標經
    # derotation_matrix 轉回內容座標,並用 rotate= 讓文字方向跟著旋轉
    # 才能讓文字落在 user 視覺認知的位置 + 朝向正確 (issue #21)。
    import fitz
    rot = int(getattr(page, "rotation", 0)) % 360
    r = page.rect                                # visual rect(含旋轉)
    fontname, fontfile, fontbuf = _pageno_font(text)  # 含中文時用 CJK 字型避免缺字
    # 用位元組嵌的字型要先在該頁註冊（insert_text 沒有 fontbuffer 參數）
    if fontbuf is not None:
        page.insert_font(fontname=fontname, fontbuffer=fontbuf)
    tw = _text_width_pt(text, font_size)         # CJK 全形字寬納入定位
    th = font_size * 1.2
    if position == "tl":   x, y = m_pt, m_pt + th
    elif position == "tc": x, y = (r.width - tw) / 2, m_pt + th
    elif position == "tr": x, y = r.width - tw - m_pt, m_pt + th
    elif position == "bl": x, y = m_pt, r.height - m_pt
    elif position == "bc": x, y = (r.width - tw) / 2, r.height - m_pt
    else:                  x, y = r.width - tw - m_pt, r.height - m_pt
    # 把 visual 座標轉回內容座標
    if rot:
        # page.derotation_matrix: 從 visual 轉回內容空間
        cp = fitz.Point(x, y) * page.derotation_matrix
        page.insert_text(
            cp, text,
            fontsize=font_size, fontname=fontname, fontfile=fontfile,
            color=_hex_to_rgb01(color_hex),
            rotate=rot,                          # 文字也跟著轉,才會朝向正確
        )
    else:
        page.insert_text(
            (x, y), text,
            fontsize=font_size, fontname=fontname, fontfile=fontfile,
            color=_hex_to_rgb01(color_hex),
        )


@router.post("/preview-thumb")
async def preview_thumb(
    request: Request,
    upload_id: str = Form(...),
    page: int = Form(...),
    position: str = Form("br"),
    fmt: str = Form("{n} / {N}"),
    start: int = Form(1),
    font_size: float = Form(11.0),
    margin_mm: float = Form(10.0),
    color: str = Form("#000000"),
    from_page: int = Form(1),
    to_page: int = Form(0),  # 0 = until last page
    large: bool = Form(False),  # lightbox 放大檢視用，渲較高 dpi
):
    """Apply the page-number to ONE page in-memory and return a PNG thumb so
    the user sees the *real* rendered output rather than a UI overlay."""
    def _work():
        # 這裡算的是預覽圖（純 CPU）。直接跑在事件迴圈上會讓**全站**
        # 在這段時間都不回應 —— 頁數多或 DPI 高時特別明顯。包成閉包
        # 丟到執行緒之後，慢的只有按下去的那個人自己。
        from app.core.safe_paths import require_uuid_hex
        from ...core import upload_owner as _uo
        require_uuid_hex(upload_id, "upload_id")
        _uo.require(upload_id, request)
        src = settings.temp_dir / f"pnL_{upload_id}.pdf"
        if not src.exists():
            raise HTTPException(404, "upload not found (expired?)")
        with fitz.open(str(src)) as doc:
            if page < 1 or page > doc.page_count:
                raise HTTPException(400, "page out of range")
            tp: int | None = to_page if to_page and to_page > 0 else None
            _draw_pageno(
                doc[page - 1], page_index=page - 1, total=doc.page_count,
                position=position, fmt=fmt, start=start,
                font_size=font_size, margin_mm=margin_mm, color_hex=color,
                from_page=max(1, from_page), to_page=tp,
            )
            pix = doc[page - 1].get_pixmap(dpi=160 if large else 64, alpha=False)
            png = pix.tobytes("png")
        from fastapi.responses import Response as _Resp
        return _Resp(content=png, media_type="image/png",
                     headers={"Cache-Control": "no-store"})

    return await _asyncio.to_thread(_work)


@router.post("/submit")
async def submit(
    request: Request,
    file: List[UploadFile] = File(...),
    position: str = Form("br"),    # tl tr bl br tc bc
    fmt: str = Form("{n} / {N}"),  # template tokens: {n}=current 1-based, {N}=total
    start: int = Form(1),
    font_size: float = Form(11.0),
    margin_mm: float = Form(10.0),
    color: str = Form("#000000"),
    from_page: int = Form(1),
    to_page: int = Form(0),  # 0 = until last page
):
    files = file or []
    if not files: raise HTTPException(400, "沒有檔案")
    bid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(bid, request)
    bdir = settings.temp_dir / f"pn_{bid}"; bdir.mkdir(parents=True, exist_ok=True)
    saved: list[tuple[Path, str]] = []
    for i, f in enumerate(files):
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"只支援 PDF：{f.filename}")
        data = await f.read()
        if not data: raise HTTPException(400, f"空檔：{f.filename}")
        sp = bdir / f"{i:03d}_{Path(f.filename).name}"; sp.write_bytes(data)
        saved.append((sp, f.filename))

    def run(job):
        outs: list[Path] = []
        for fi, (sp, orig) in enumerate(saved):
            job.message = f"處理 {orig}"; job.progress = (fi/len(saved)) * 0.95
            with fitz.open(str(sp)) as doc:
                N = doc.page_count
                tp: int | None = to_page if to_page and to_page > 0 else None
                for i, page in enumerate(doc):
                    _draw_pageno(
                        page, page_index=i, total=N,
                        position=position, fmt=fmt, start=start,
                        font_size=font_size, margin_mm=margin_mm,
                        color_hex=color,
                        from_page=max(1, from_page), to_page=tp,
                    )
                op = bdir / f"{Path(orig).stem}_pageno.pdf"
                doc.save(str(op), garbage=3, deflate=True)
                outs.append(op)
        if len(outs) == 1:
            job.result_path = outs[0]; job.result_filename = outs[0].name
        else:
            zname = f"pageno_{time.strftime('%Y%m%d_%H%M%S')}.zip"
            zp = bdir / zname
            with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in outs: zf.write(p, arcname=p.name)
            job.result_path = zp; job.result_filename = zname
        job.progress = 1.0; job.message = f"完成（{len(outs)} 份）"

    job = job_manager.submit("pdf-pageno", run, meta={"count": len(saved)})
    return {"job_id": job.id}


# ---- 對外 API：單次 upload + 加頁碼 + 直接回 PDF ----
@router.post("/api/pdf-pageno", include_in_schema=True)
async def api_pdf_pageno(
    request: Request,
    file: UploadFile = File(...),
    position: str = Form("br"),
    fmt: str = Form("{n} / {N}"),
    start: int = Form(1),
    font_size: float = Form(11.0),
    margin_mm: float = Form(10.0),
    color: str = Form("#000000"),
    from_page: int = Form(1),
    to_page: int = Form(0),  # 0 = 到最後一頁
):
    """單次上傳 PDF，加頁碼後直接回 PDF。position: tl/tr/bl/br/tc/bc。

    `from_page` / `to_page`（1 起，0 ＝ 到最後一頁）只在這個範圍裡編頁碼（封面、目錄、封底不編）；
    `{N}` 是範圍內的頁數，跟網頁一樣。
    """
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data or data[:4] != b"%PDF":
        raise HTTPException(400, "不是有效的 PDF")
    uid = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(uid, request)
    src = settings.temp_dir / f"pn_api_{uid}_in.pdf"
    out = settings.temp_dir / f"pn_api_{uid}_out.pdf"
    src.write_bytes(data)
    stem = Path(file.filename or "document.pdf").stem
    import asyncio as _asyncio
    def _do():
        with fitz.open(str(src)) as doc:
            N = doc.page_count
            for i, page in enumerate(doc):
                _draw_pageno(
                    page, page_index=i, total=N,
                    position=position, fmt=fmt, start=start,
                    font_size=font_size, margin_mm=margin_mm,
                    color_hex=color,
                    from_page=max(1, from_page), to_page=(to_page if to_page and to_page > 0 else None),
                )
            doc.save(str(out), garbage=3, deflate=True)
    await _asyncio.to_thread(_do)
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"{stem}_pageno.pdf")

"""書籤與目錄 —— 讓合併出來的大部頭文件有導覽。

## 這支工具解決的事

標案、年報、結案報告動輒十幾個檔案合成三百頁。合併工具只負責串接，**產出完全
沒有書籤** —— 收件方只能一直捲。書籤是 PDF 閱讀器唯一的導覽方式。

三種來源，同一個編輯畫面：

* **多檔上傳** → 串接，每一份的檔名成為第一層書籤（子文件自己的書籤降一層掛進去）
* **單檔 + 自動偵測** → 依字級猜標題，產生**草稿**讓使用者改
* **貼上目錄清單** → 「標題 + 頁碼」的文字，縮排決定層級

最後選配在最前面插入**目錄頁**（可點）。

## 為什麼書籤編輯不是前端自己算

`normalize` 會把不合規的層級與頁碼修掉並**逐條回報原因**。前端自己修的話，
規則遲早跟後端不一致；而使用者打的層級被改掉卻沒人講，他只會覺得工具壞了。
"""
from __future__ import annotations

import asyncio as _asyncio
import json
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response

from ...config import settings
from ...core import office_convert, pdf_preview, upload_owner as _uo
from ...core.job_manager import job_manager
from ...core.pdf_guard import ensure_readable_pdf
from ...core.safe_paths import require_uuid_hex
from . import bookmark_core as BC

router = APIRouter()

#: 暫存檔前綴。**不可以再含底線以外的分隔** —— `upload_owner.extract_upload_id`
#: 靠它切出 id，切錯歸屬檢查會整個失效（pdf-watermark 的 `wm_` 踩過，v1.11.80）。
_PREFIX = "bm"


def _src_path(upload_id: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{upload_id}.pdf"


def _out_path(upload_id: str) -> Path:
    return settings.temp_dir / f"{_PREFIX}_{upload_id}_out.pdf"


def _to_pdf(data: bytes, name: str, dst: Path) -> None:
    """PDF 直接寫檔；Office 檔先轉 PDF（跟頁面加框一樣，不逼人先去別的工具轉）。"""
    if data[:5] == b"%PDF-":
        dst.write_bytes(data)
        return
    if not office_convert.is_office_file(name):
        raise HTTPException(400, "只支援 PDF 與文書檔（Word / Excel / PowerPoint / ODF）")
    raw = dst.with_name(dst.stem + "_raw" + Path(name).suffix)
    raw.write_bytes(data)
    try:
        office_convert.convert_to_pdf(raw, dst)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "文書檔轉換失敗，請確認檔案是否完整")
    finally:
        raw.unlink(missing_ok=True)
    if not dst.exists():
        raise HTTPException(400, "文書檔轉換失敗（沒有產出 PDF）")


def _items_from_json(raw: str) -> list[BC.BookmarkItem]:
    try:
        data = json.loads(raw or "[]")
    except (ValueError, TypeError):
        raise HTTPException(400, "書籤資料格式不正確")
    if not isinstance(data, list):
        raise HTTPException(400, "書籤資料格式不正確")
    out: list[BC.BookmarkItem] = []
    for d in data[:2000]:               # 上限：兩千筆已經遠超實用範圍
        if not isinstance(d, dict):
            continue
        # **`int()` 一定要包起來**。上面只擋了「不是合法 JSON」與「不是陣列」，
        # 但 `page` 的值是使用者可控的：`"abc"` / `1e400` / `[1]` / 5000 位的
        # 數字字串各自會丟 ValueError / OverflowError / TypeError，一路冒到
        # 最外層變成 500（v1.14.31 對抗式驗證：5 種畸形值 × 3 個端點全中）。
        # 轉不動就跳過那一筆，不要讓整個請求爆掉。
        try:
            page = int(d.get("page") or 1)
            level = int(d.get("level") or 1)
        except (ValueError, TypeError, OverflowError):
            continue
        out.append(BC.BookmarkItem(
            title=str(d.get("title") or "")[:300],
            page=page, level=level))
    return out


def _payload(upload_id: str, doc: fitz.Document, items, warns) -> dict:
    return {
        "upload_id": upload_id,
        "page_count": doc.page_count,
        "bookmarks": [{"title": i.title, "page": i.page, "level": i.level}
                      for i in items],
        "warnings": warns,
        "pages": [{"page": i + 1,
                   "thumb": f"/tools/pdf-bookmark/thumb/{upload_id}/{i + 1}"}
                  for i in range(min(doc.page_count, 400))],
    }


@router.get("/", response_class=HTMLResponse)
async def page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "pdf_bookmark.html",
        {"request": request, "title": "書籤與目錄"})


@router.post("/load")
async def load(request: Request, files: list[UploadFile] = File(...)):
    """收一或多個檔案。多檔時**串接並以檔名建第一層書籤**（標案最常見的用法）。"""
    if not files:
        raise HTTPException(400, "請選擇檔案")
    if len(files) > 60:
        raise HTTPException(400, "一次最多 60 個檔案")
    upload_id = uuid.uuid4().hex
    _uo.record(upload_id, request)
    dst = _src_path(upload_id)

    if len(files) == 1:
        data = await files[0].read()
        if not data:
            raise HTTPException(400, "檔案是空的")
        _to_pdf(data, files[0].filename or "", dst)
        ensure_readable_pdf(dst)
        with fitz.open(str(dst)) as doc:
            items = BC.read_bookmarks(doc)
            return _payload(upload_id, doc, items, [])

    # 多檔：先各自轉成 PDF，再串接建書籤
    tmp_dir = settings.temp_dir / f"{_PREFIX}src_{upload_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    sources: list[tuple[Path, str]] = []
    try:
        for idx, f in enumerate(files):
            data = await f.read()
            if not data:
                continue
            name = f.filename or f"檔案{idx + 1}"
            part = tmp_dir / f"{idx:03d}.pdf"
            _to_pdf(data, name, part)
            # 書籤標題用**檔名去掉副檔名** —— 帶著 .pdf 在導覽列上很雜
            sources.append((part, Path(name).stem[:120] or f"檔案{idx + 1}"))
        if not sources:
            raise HTTPException(400, "沒有可用的檔案")
        total, items = BC.merge_with_bookmarks(sources, dst)
        if not total:
            raise HTTPException(400, "合併後沒有任何頁面")
    finally:
        for p in tmp_dir.glob("*"):
            p.unlink(missing_ok=True)
        tmp_dir.rmdir()
    with fitz.open(str(dst)) as doc:
        return _payload(upload_id, doc, items, [])


@router.get("/thumb/{upload_id}/{page_no}")
async def thumb(upload_id: str, page_no: int, request: Request,
                large: bool = False):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    suffix = "_large" if large else ""
    out = settings.temp_dir / f"{_PREFIX}_{upload_id}_t{suffix}_{page_no}.png"
    if not out.exists():
        try:
            await pdf_preview.render_page_png_async(src, out, page_no - 1,
                                        dpi=150 if large else 70)
        except ValueError as e:
            raise HTTPException(400, str(e))
    return FileResponse(str(out), media_type="image/png",
                        headers={"Cache-Control": "max-age=300"})


@router.post("/auto-detect")
async def auto_detect(request: Request, upload_id: str = Form(...)):
    """依字級猜標題，回**草稿**。使用者一定要能在畫面上改。"""
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    with fitz.open(str(src)) as doc:
        items = BC.auto_detect(doc)
        warns = ([] if items else
                 ["看不出標題 —— 這份文件的字級沒有明顯差異（簡報、表單、掃描檔"
                  "常是這樣）。請改用「貼上目錄清單」或手動新增。"])
        return _payload(upload_id, doc, items, warns)


@router.post("/parse-list")
async def parse_list(request: Request, upload_id: str = Form(...),
                     text: str = Form("")):
    """把貼上的「標題 + 頁碼」清單解析成書籤。"""
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    items, warns = BC.parse_text_list(text)
    with fitz.open(str(src)) as doc:
        items, more = BC.normalize(items, doc.page_count)
        return _payload(upload_id, doc, items, warns + more)


@router.post("/validate")
async def validate(request: Request, upload_id: str = Form(...),
                   bookmarks: str = Form("[]")):
    """把畫面上的書籤送回來檢查（層級、頁碼），回修正後的結果與說明。"""
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    with fitz.open(str(src)) as doc:
        items, warns = BC.normalize(_items_from_json(bookmarks), doc.page_count)
        return _payload(upload_id, doc, items, warns)


@router.post("/toc-preview")
async def toc_preview(request: Request, upload_id: str = Form(...),
                      bookmarks: str = Form("[]"),
                      toc_title: str = Form("目錄"),
                      toc_max_level: int = Form(3),
                      toc_dots: bool = Form(True),
                      toc_at: int = Form(1),
                      page_no: int = Form(1)):
    """目錄頁**下載前**長什麼樣。

    書籤本身在閱讀器側邊欄，這一頁看不到；目錄頁才是印得出來的那一張，
    所以一定要能先看。走的是與送出**同一支** `build_toc_page`。
    """
    def _work():
        # 這裡算的是預覽圖（純 CPU）。直接跑在事件迴圈上會讓**全站**
        # 在這段時間都不回應 —— 頁數多或 DPI 高時特別明顯。包成閉包
        # 丟到執行緒之後，慢的只有按下去的那個人自己。
        require_uuid_hex(upload_id, "upload_id")
        _uo.require(upload_id, request)
        src = _src_path(upload_id)
        if not src.exists():
            raise HTTPException(404, "檔案不存在（可能已過期）")
        with fitz.open(str(src)) as doc:
            items, _ = BC.normalize(_items_from_json(bookmarks), doc.page_count)
            if not items:
                raise HTTPException(400, "還沒有任何書籤")
            spec = BC.TocPageSpec(title=toc_title or "目錄",
                                  max_level=max(1, min(3, toc_max_level)),
                                  dot_leader=bool(toc_dots))
            inserted = BC.build_toc_page(doc, items, spec, at_page=toc_at)
            if not inserted:
                raise HTTPException(400, "產不出目錄頁")
            at = max(1, min(int(toc_at or 1), doc.page_count))
            idx = at - 1 + (max(1, min(page_no, inserted)) - 1)
            pix = doc[idx].get_pixmap(dpi=88, alpha=False)
            return Response(content=pix.tobytes("png"), media_type="image/png",
                            headers={"Cache-Control": "no-store",
                                     "X-Toc-Pages": str(inserted)})

    return await _asyncio.to_thread(_work)


def _write_result(src: Path, dst: Path, items, *, toc_page: bool,
                  toc_title: str, toc_max_level: int, toc_dots: bool,
                  toc_at: int = 1) -> dict:
    """實際產出。**目錄頁與書籤頁碼的先後順序都在這裡處理** —— 散到呼叫端一定會漏。"""
    with fitz.open(str(src)) as doc:
        inserted = 0
        if toc_page and items:
            spec = BC.TocPageSpec(title=toc_title or "目錄",
                                  max_level=max(1, min(3, toc_max_level)),
                                  dot_leader=bool(toc_dots))
            at = max(1, min(int(toc_at or 1), doc.page_count + 1))
            inserted = BC.build_toc_page(doc, items, spec, at_page=at)
            if inserted:
                # **只有插入點之後的書籤要往後推** —— 目錄可以插在封面後面，
                # 那時指向封面的書籤仍然是第 1 頁，跟著移就會指到目錄自己身上。
                items = BC.shift_pages(items, inserted, from_page=at)
        warns = BC.apply_bookmarks(doc, items)
        doc.save(str(dst), garbage=3, deflate=True)
        return {"page_count": doc.page_count, "toc_pages": inserted,
                "bookmark_count": len(items), "warnings": warns}


@router.post("/submit")
async def submit(request: Request,
                 upload_id: str = Form(...),
                 bookmarks: str = Form("[]"),
                 toc_page: bool = Form(False),
                 toc_title: str = Form("目錄"),
                 toc_max_level: int = Form(3),
                 toc_dots: bool = Form(True),
                 toc_at: int = Form(1),
                 filename: str = Form("bookmarked.pdf")):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _src_path(upload_id)
    if not src.exists():
        raise HTTPException(404, "檔案不存在（可能已過期）")
    items = _items_from_json(bookmarks)
    out = _out_path(upload_id)

    stem = Path(filename).stem or "bookmarked"

    def run(job):
        job.message = "寫入書籤…"
        job.progress = 0.2
        info = _write_result(src, out, items, toc_page=toc_page,
                             toc_title=toc_title, toc_max_level=toc_max_level,
                             toc_dots=toc_dots, toc_at=toc_at)
        # 下載走共用的 `/api/jobs/{id}/download`（JobProgress 自己會接）——
        # 每個工具各寫一條下載路徑，工作區存檔那些共用功能就接不上。
        job.result_path = out
        job.result_filename = f"{stem}_bookmarked.pdf"
        # 摘要放 **meta** 不是 result —— `Job.to_public()` 只送 meta 出去，
        # 放 result 的話前端永遠讀不到（我第一版就放錯，畫面顯示 0 個書籤）。
        job.meta = dict(job.meta or {}, bookmark_result=info)
        job.progress = 1.0
        job.message = ("完成（" + str(info["bookmark_count"]) + " 個書籤"
                       + ("、" + str(info["toc_pages"]) + " 頁目錄"
                          if info["toc_pages"] else "") + "）")

    job = job_manager.submit("pdf-bookmark", run, request=request,
                             meta={"filename": Path(filename).name,
                                   "count": len(items)})
    return {"job_id": job.id}


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    out = _out_path(upload_id)
    if not out.exists():
        raise HTTPException(404, "還沒產生結果")
    return FileResponse(str(out), media_type="application/pdf",
                        filename=f"bookmarked_{upload_id[:8]}.pdf")


# ------------------------------------------------------------ 對外 API --

@router.post("/api/pdf-bookmark")
async def api(request: Request,
              files: list[UploadFile] = File(...),
              bookmarks: str = Form(""),
              toc_page: bool = Form(False),
              toc_title: str = Form("目錄"),
              toc_max_level: int = Form(3),
              auto: bool = Form(False),
              toc_dots: bool = Form(True),
              toc_at: int = Form(1)):
    """單次呼叫：上傳一或多個檔案 → 直接回加好書籤的 PDF。

    `toc_at`：目錄頁插在第幾頁（1 起，預設第 1 頁）；`toc_dots`：標題與頁碼之間畫引導點。

    * 多檔 → 串接並以檔名建第一層書籤
    * `auto=true` → 自動偵測標題
    * `bookmarks` → 自己指定（JSON 陣列，欄位 title / page / level）
    """
    if not files:
        raise HTTPException(400, "請提供檔案")
    uid = uuid.uuid4().hex
    src = _src_path(uid)
    out = _out_path(uid)
    tmp_dir = settings.temp_dir / f"{_PREFIX}api_{uid}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        if len(files) == 1:
            _to_pdf(await files[0].read(), files[0].filename or "", src)
            # **公開 API 也要驗**：網頁的 `/load` 修好之後這條路仍然全數 500
            # —— 它是另一段程式碼（v1.14.31 對抗式驗證實測 3 支 × 3 種輸入）。
            ensure_readable_pdf(src)
            items: list[BC.BookmarkItem] = []
        else:
            sources = []
            for i, f in enumerate(files):
                part = tmp_dir / f"{i:03d}.pdf"
                _to_pdf(await f.read(), f.filename or "", part)
                sources.append((part, Path(f.filename or f"檔案{i+1}").stem))
            _total, items = BC.merge_with_bookmarks(sources, src)
        if bookmarks:
            items = _items_from_json(bookmarks)
        elif auto and not items:
            with fitz.open(str(src)) as doc:
                items = BC.auto_detect(doc)
        _write_result(src, out, items, toc_page=toc_page, toc_title=toc_title,
                      toc_max_level=toc_max_level, toc_dots=toc_dots, toc_at=toc_at)
        return FileResponse(str(out), media_type="application/pdf",
                            filename="bookmarked.pdf")
    finally:
        for p in tmp_dir.glob("*"):
            p.unlink(missing_ok=True)
        tmp_dir.rmdir()
        src.unlink(missing_ok=True)

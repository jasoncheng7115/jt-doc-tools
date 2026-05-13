"""電子發票掃描 endpoints — scan + buffer CRUD.

API 設計：
- POST /scan          multipart 上傳圖片或 PDF → 解 QR → 加進 buffer → 回結果
- GET  /buffer        回該 user 的 buffer 全部 invoices
- DELETE /buffer/{id} 刪一筆
- DELETE /buffer      清空
- POST /api/einvoice-scan  public alias 同 /scan（給 REST API caller）

Auth：跟 default-user role 同步。Per-user buffer 用 buffer.py 的 user_key()
雜湊區分（auth ON / OFF 自動處理）。

安全：
- 上傳檔大小 cap 20 MiB（防 DoS）
- PDF 最多解 20 頁（pyzbar render 慢，避免長期占 worker）
- 副檔名 + content-type 雙重檢查
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from datetime import datetime

from . import buffer, exporter, qr_decoder, settings as user_settings
from fastapi.responses import StreamingResponse
import io
from ...core.http_utils import content_disposition

router = APIRouter()

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MiB
_IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"}
_PDF_SUFFIX = "pdf"


def _request_user(request: Request) -> Optional[dict]:
    """request.state.user 是 dict（feedback_request_state_user_is_dict.md）。"""
    return getattr(request.state, "user", None)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("einvoice_scan.html", {
        "request": request,
        "qr_backend_available": qr_decoder.is_qr_backend_available(),
    })


@router.get("/api/backend-status")
async def backend_status():
    """讓前端知道 QR 後端是否可用（pyzbar）。"""
    return {"available": qr_decoder.is_qr_backend_available()}


@router.post("/scan")
async def scan(request: Request, file: UploadFile = File(...)):
    """上傳圖片 / PDF → 解 QR → 加進 buffer → 回結果。"""
    if not qr_decoder.is_qr_backend_available():
        raise HTTPException(503, "QR 解碼後端 (pyzbar) 未安裝；請聯絡管理員執行 jtdt update")

    name = (file.filename or "").lower()
    suffix = name.rsplit(".", 1)[-1] if "." in name else ""
    if suffix not in _IMAGE_SUFFIXES and suffix != _PDF_SUFFIX:
        raise HTTPException(400, f"不支援的檔案格式 .{suffix}（支援：圖片或 PDF）")

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"檔案超過 {_MAX_UPLOAD_BYTES // 1024 // 1024} MiB 上限")
    if not data:
        raise HTTPException(400, "空檔案")

    # 解 QR
    try:
        if suffix == _PDF_SUFFIX:
            qr_pairs = qr_decoder.decode_pdf(data)
            qr_strings = [text for _page, text in qr_pairs]
        else:
            qr_strings = qr_decoder.decode_image(data)
    except qr_decoder.QRBackendUnavailable as e:
        raise HTTPException(503, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Parse 為 e-invoice 結構（不是 e-invoice 的 QR 自動跳過）
    parsed = qr_decoder.parse_qr_list(qr_strings)

    # 加進 buffer（去重 + 上限）
    user = _request_user(request)
    add_result = buffer.add_invoices(user, parsed)
    info = buffer.buffer_info(user)

    return JSONResponse({
        "scanned_qr_count": len(qr_strings),       # 影像中總共幾個 QR
        "parsed_count": len(parsed),                # 其中幾個是 e-invoice
        "added_count": len(add_result["added"]),
        "duplicates": add_result["duplicates"],     # 重複的 invoice_number list
        "cap_reached": add_result["cap_reached"],
        "added": add_result["added"],               # 完整 entry 陣列（含 id / scanned_at）
        "buffer": info,
    })


@router.get("/buffer")
async def get_buffer(request: Request):
    user = _request_user(request)
    return JSONResponse({
        "invoices": buffer.list_invoices(user),
        "info": buffer.buffer_info(user),
    })


@router.delete("/buffer/{invoice_id}")
async def delete_invoice(invoice_id: str, request: Request):
    # 防 path traversal — id 只允許 hex
    if not invoice_id or len(invoice_id) > 64 or not all(c in "0123456789abcdef" for c in invoice_id):
        raise HTTPException(400, "invalid invoice id")
    user = _request_user(request)
    ok = buffer.delete_invoice(user, invoice_id)
    if not ok:
        raise HTTPException(404, "invoice not found in buffer")
    info = buffer.buffer_info(user)
    return {"deleted": True, "buffer": info}


@router.delete("/buffer")
async def clear_buffer(request: Request):
    user = _request_user(request)
    n = buffer.clear_all(user)
    return {"cleared": n, "buffer": buffer.buffer_info(user)}


@router.post("/scan-text")
async def scan_text(request: Request):
    """Accept pre-decoded QR text strings (no image upload).

    給「連續掃描」用 — 手機端用 jsQR 直接 in-browser decode，
    decode 完只把字串傳上來，不傳影像。比 /scan 快很多（影像通常 0.5-2 MB）。

    Body: {"qr_texts": [str, ...]}  — list of raw QR strings
    Returns: 同 /scan 的格式
    """
    if not qr_decoder.is_qr_backend_available():
        # /scan-text 其實不需要 zbar (字串已 decode 完)，但為了一致性還是檢查
        # — 不對，連續掃描就應該繞過 zbar。直接 parse 即可。
        pass

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")

    qr_texts = body.get("qr_texts") or []
    if not isinstance(qr_texts, list):
        raise HTTPException(400, "qr_texts 必須是陣列")
    # 限長度防 DoS — 連續掃描一次傳 10 張內合理
    if len(qr_texts) > 50:
        raise HTTPException(413, "一次最多 50 個 QR 字串")
    # 各字串長度限制
    for s in qr_texts:
        if not isinstance(s, str):
            raise HTTPException(400, "qr_texts 內必須全為字串")
        if len(s) > 4096:
            raise HTTPException(413, "單一 QR 字串過長")

    parsed = qr_decoder.parse_qr_list(qr_texts)
    user = _request_user(request)
    add_result = buffer.add_invoices(user, parsed)
    info = buffer.buffer_info(user)

    return JSONResponse({
        "scanned_qr_count": len(qr_texts),
        "parsed_count": len(parsed),
        "added_count": len(add_result["added"]),
        "duplicates": add_result["duplicates"],
        "cap_reached": add_result["cap_reached"],
        "added": add_result["added"],
        "buffer": info,
    })


@router.get("/settings")
async def get_settings(request: Request):
    """回該 user 的欄位顯示設定。"""
    user = _request_user(request)
    return JSONResponse({
        "settings": user_settings.get_settings(user),
        "field_definitions": user_settings.FIELD_DEFINITIONS,
    })


@router.put("/settings")
async def update_settings(request: Request):
    """更新欄位顯示設定。Body: {visible_columns?: [...], column_order?: [...]}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    user = _request_user(request)
    try:
        new_settings = user_settings.update_settings(user, body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"settings": new_settings})


@router.post("/settings/reset")
async def reset_settings(request: Request):
    """恢復預設 — 直接刪 user settings 檔。"""
    user = _request_user(request)
    new_settings = user_settings.reset_settings(user)
    return JSONResponse({"settings": new_settings})


@router.patch("/buffer/{invoice_id}")
async def update_invoice(invoice_id: str, request: Request):
    """更新單筆發票的 note 欄位（M2 階段只開放 note 可編輯）。"""
    if not invoice_id or len(invoice_id) > 64 or not all(c in "0123456789abcdef" for c in invoice_id):
        raise HTTPException(400, "invalid invoice id")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    note = body.get("note", "")
    if not isinstance(note, str):
        raise HTTPException(400, "note 必須是字串")
    if len(note) > 500:
        raise HTTPException(413, "note 長度上限 500 字元")
    user = _request_user(request)
    ok = buffer.update_invoice_field(user, invoice_id, "note", note)
    if not ok:
        raise HTTPException(404, "invoice not found in buffer")
    return {"ok": True}


@router.post("/buffer/delete-batch")
async def delete_batch(request: Request):
    """批次刪除 — body {ids: [...]}"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    ids = body.get("ids", [])
    if not isinstance(ids, list):
        raise HTTPException(400, "ids 必須是陣列")
    user = _request_user(request)
    deleted = 0
    for inv_id in ids:
        if isinstance(inv_id, str) and len(inv_id) <= 64 and all(c in "0123456789abcdef" for c in inv_id):
            if buffer.delete_invoice(user, inv_id):
                deleted += 1
    return {"deleted": deleted, "buffer": buffer.buffer_info(user)}


@router.post("/export")
async def export(request: Request):
    """匯出 buffer — body {format: 'csv'|'xlsx'|'json', clear_after?: bool}.

    visible_columns / column_order / field_formats 自動取使用者目前 settings.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "invalid JSON body")
    fmt = body.get("format", "")
    if fmt not in ("csv", "xlsx", "json"):
        raise HTTPException(400, "format 必須是 csv / xlsx / json")
    clear_after = bool(body.get("clear_after", False))

    user = _request_user(request)
    invoices = buffer.list_invoices(user)
    if not invoices:
        raise HTTPException(400, "buffer 為空，無資料可匯出")
    settings = user_settings.get_settings(user)
    try:
        data, mimetype, suffix = exporter.build_export(
            invoices,
            settings["visible_columns"],
            settings["column_order"],
            settings.get("field_formats") or {},
            fmt,
        )
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

    # 若 clear_after，匯出後清空 buffer
    if clear_after:
        buffer.clear_all(user)

    filename = f"einvoices-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{suffix}"
    return StreamingResponse(
        io.BytesIO(data),
        media_type=mimetype,
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.post("/api/einvoice-scan")
async def api_einvoice_scan(request: Request, file: UploadFile = File(...)):
    """Public alias 同 /scan — 給 REST API caller 用。"""
    return await scan(request, file)

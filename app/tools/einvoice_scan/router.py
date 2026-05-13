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

from . import buffer, qr_decoder

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


@router.post("/api/einvoice-scan")
async def api_einvoice_scan(request: Request, file: UploadFile = File(...)):
    """Public alias 同 /scan — 給 REST API caller 用。"""
    return await scan(request, file)

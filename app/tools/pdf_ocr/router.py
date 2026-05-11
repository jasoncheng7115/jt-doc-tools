"""PDF 文字層補建 endpoints."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import job_manager as _jm
from ...core import upload_owner as _uo
from ...core.safe_paths import is_uuid_hex, require_uuid_hex
from . import ocr_core

router = APIRouter()


def _work_dir() -> Path:
    p = settings.temp_dir / "pdf_ocr"
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.get("/", response_class=HTMLResponse)
async def page(request: Request) -> HTMLResponse:
    templates = request.app.state.templates
    # 偵測目前的 OCR engine（admin /admin/ocr-langs 設定）
    from app.core import ocr_engine as _oe
    current_engine = _oe.get_default_engine()
    tess_ok = ocr_core.is_tesseract_available()
    easyocr_ok = _oe.is_easyocr_available()
    langs = ocr_core.get_active_langs() if tess_ok else ""
    installed_langs = ocr_core.get_installed_langs() if tess_ok else []
    # 共用 catalog（admin/ocr-langs 也用同一份）
    from app.core import tessdata_manager as _tm
    cat_with_status = _tm.catalog_with_status() if tess_ok else []
    LANG_CATALOG = [dict(item) for item in cat_with_status] if cat_with_status else [
        dict(item) for item in _tm.LANG_CATALOG
    ]
    installed_set = set(installed_langs)
    # **EasyOCR 主引擎模式**：所有支援的語言都當「已可用」（首次自動下載 model），
    # 不顯示「未安裝」/ fast/best 變體 badge — 這些是 tesseract 概念。
    # 並把語言碼換成 EasyOCR 慣例（chi_tra → ch_tra）供 UI 顯示。
    if current_engine == "easyocr" and easyocr_ok:
        easyocr_supported = set(_oe._TESS_TO_EASYOCR.keys())
        for item in LANG_CATALOG:
            if item["code"] in easyocr_supported:
                item["installed"] = True
                item["display_code"] = _oe._TESS_TO_EASYOCR[item["code"]]
                item["active_variant"] = ""
                item["fast_installed"] = False
                item["best_installed"] = False
            else:
                # 非 EasyOCR 支援的語言 — 顯示原碼但標記未支援
                item["display_code"] = item["code"]
        installed_langs = sorted(easyocr_supported)
    else:
        for item in LANG_CATALOG:
            item["installed"] = item.get("installed", item["code"] in installed_set)
            item["display_code"] = item["code"]
    catalog_codes = {item["code"] for item in LANG_CATALOG}
    extra_installed = [c for c in installed_langs if c not in catalog_codes]
    llm_ok = False
    llm_model = ""
    llm_vision_ok = False
    llm_vision_model = ""
    try:
        from app.core.llm_settings import llm_settings
        llm_ok = llm_settings.is_enabled()
        if llm_ok:
            llm_model = llm_settings.get_model_for("pdf-ocr")
            llm_vision_model = llm_settings.get_model_for("pdf-ocr-vision")
            llm_vision_ok = bool(llm_vision_model)
    except Exception:
        pass
    return templates.TemplateResponse("pdf_ocr.html", {
        "request": request,
        "title": "PDF 文字層補建",
        "tesseract_ok": tess_ok,
        "current_engine": current_engine,
        "easyocr_ok": easyocr_ok,
        "ocr_langs": langs,
        "installed_langs": installed_langs,
        "default_langs": [l for l in (langs or "").split("+") if l],
        "lang_catalog": LANG_CATALOG,
        "extra_installed": extra_installed,
        "llm_ok": llm_ok,
        "llm_model": llm_model,
        "llm_vision_ok": llm_vision_ok,
        "llm_vision_model": llm_vision_model,
    })


_ACCEPTED_EXTS = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")


def _image_to_pdf_bytes(img_bytes: bytes) -> bytes:
    """把單張圖檔包成單頁 PDF。支援 jpg/png/tiff/bmp/webp。
    用 PyMuPDF 建一個跟圖片同尺寸的頁，把圖貼進去。
    """
    import fitz
    img_doc = fitz.open(stream=img_bytes, filetype=None)
    try:
        # 圖片尺寸
        rect = img_doc[0].rect
        # 新 PDF 同尺寸頁
        out_doc = fitz.open()
        page = out_doc.new_page(width=rect.width, height=rect.height)
        page.insert_image(rect, stream=img_bytes)
        return out_doc.tobytes(garbage=3, deflate=True)
    finally:
        img_doc.close()
        try:
            out_doc.close()
        except Exception:
            pass


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空檔案")
    name = (file.filename or "").lower()
    suffix = next((e for e in _ACCEPTED_EXTS if name.endswith(e)), None)
    if not suffix:
        raise HTTPException(400, f"不支援的檔案類型；支援：{', '.join(_ACCEPTED_EXTS)}")
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(400, "檔案超過 200 MB 上限")

    upload_id = uuid.uuid4().hex
    src = _work_dir() / f"po_{upload_id}_src.pdf"
    converted_from = None

    if suffix == ".pdf":
        src.write_bytes(raw)
    else:
        # 圖檔自動包成單頁 PDF 再走 OCR pipeline
        try:
            pdf_bytes = _image_to_pdf_bytes(raw)
            src.write_bytes(pdf_bytes)
            converted_from = suffix
        except Exception as e:
            raise HTTPException(400, f"圖檔轉 PDF 失敗：{e}")

    _uo.record(upload_id, request)
    return {"upload_id": upload_id,
            "filename": file.filename or ("input" + suffix),
            "size": len(raw),
            "converted_from": converted_from}


@router.post("/run/{upload_id}")
async def run_ocr(upload_id: str, request: Request,
                   langs: str = Form(""),
                   dpi: int = Form(300),
                   skip_pages_with_text: bool = Form(True),
                   use_llm: bool = Form(False),
                   use_llm_vision: bool = Form(False)):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _work_dir() / f"po_{upload_id}_src.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    out = _work_dir() / f"po_{upload_id}_out.pdf"
    active_langs = (langs or ocr_core.get_active_langs()).strip() or "eng"
    dpi = max(72, min(dpi, 600))

    # LLM 文字校正 callback
    llm_cb = None
    llm_model_used = ""
    # LLM 視覺校對 callback（吃 png_bytes + raw_text，回 corrected text）
    llm_vision_cb = None
    llm_vision_model_used = ""
    # OCR 用獨立 LLM client，timeout 縮到 120s（避免單頁掛太久使用者沒回饋）
    OCR_LLM_TIMEOUT = 120.0
    try:
        from app.core.llm_settings import llm_settings
        if (use_llm or use_llm_vision) and llm_settings.is_enabled():
            # 重建 client：相同 base_url / api_key，但 timeout 縮到 OCR_LLM_TIMEOUT
            s = llm_settings.get()
            from app.core.llm_client import LLMClient as _LC
            try:
                client = _LC(base_url=s["base_url"],
                             api_key=s.get("api_key") or None,
                             timeout=OCR_LLM_TIMEOUT)
            except Exception:
                client = llm_settings.make_client()
            if use_llm and client:
                llm_model_used = llm_settings.get_model_for("pdf-ocr")
                if llm_model_used:
                    import logging as _lg
                    _ocrlog = _lg.getLogger("app.pdf_ocr.llm")
                    def _llm_cb(raw_text: str) -> str:
                        import time as _t
                        prompt = (
                            "以下是 OCR 軟體（tesseract）對掃描文件的識別結果。\n\n"
                            "## 任務\n"
                            "只修正**明顯**的字符 typo（0/O、1/l、空白多餘、CJK 偏旁誤判）。\n\n"
                            "## 嚴格規則（違反 = 失敗）\n"
                            "1. **不確定的字 → 保留原文**，禁止猜測或用更常見詞替換\n"
                            "2. **公司名 / 人名 / 機關名 / 地名 / 商標 / 編號 / 統編 / 日期 / 金額 / 型號**\n"
                            "   → **完全保留原文**，即使看似錯字也禁止改\n"
                            "3. **保持 word 數量完全一致**\n"
                            "4. **保持原語言**（中翻中、英翻英）\n"
                            "5. **不要新增解釋 / 標點 / 段落**\n\n"
                            "## 輸出\n"
                            f"原文：\n{raw_text}\n\n"
                            "**只輸出修正後文字**，不要任何前綴 / 後綴。"
                        )
                        t0 = _t.time()
                        _ocrlog.info("text LLM call start: model=%s chars=%d", llm_model_used, len(raw_text))
                        try:
                            r = client.text_query(prompt=prompt, model=llm_model_used,
                                                  temperature=0.0, max_tokens=2048,
                                                  think=False) or ""
                            _ocrlog.info("text LLM call done in %.1fs (got %d chars)", _t.time()-t0, len(r))
                            return r
                        except Exception as e:
                            _ocrlog.warning("text LLM call FAILED in %.1fs: %s", _t.time()-t0, e)
                            raise
                    llm_cb = _llm_cb
            if use_llm_vision and client:
                llm_vision_model_used = llm_settings.get_model_for("pdf-ocr-vision")
                if llm_vision_model_used:
                    import logging as _lg
                    _ocrlog2 = _lg.getLogger("app.pdf_ocr.llm")
                    def _llm_vision_cb(png_bytes: bytes, raw_text: str) -> str:
                        import time as _t
                        prompt = (
                            "你會看到一張 PDF 頁的影像，以及 tesseract OCR 對該頁的識別結果。\n\n"
                            "## 任務\n"
                            "請對照影像，修正 OCR 內**明顯**的字元錯誤"
                            "（如 CJK 偏旁誤判：太→大、〇→〇、字符切割錯誤）。\n\n"
                            "## 嚴格規則（違反 = 失敗）\n"
                            "1. **看不清楚的字 → 保留 OCR 原文**，禁止猜測 / 用「常見詞」替換\n"
                            "2. **公司名 / 人名 / 機關名 / 地名 / 商標 / 編號 / 統編 / 日期 / 金額 / 型號**\n"
                            "   → **完全保留 OCR 原文**，即使覺得 OCR 像錯字也禁止改\n"
                            "   （例：OCR=「節省工具箱」→ 不可改成「鼎盛工具箱」即使後者更常見）\n"
                            "3. **不要新增解釋、標點、段落**；**不要重排版**\n"
                            "4. 若整體已正確或不確定就**原樣回傳**\n\n"
                            "## 輸出\n"
                            f"OCR 結果：\n{raw_text}\n\n"
                            "**只輸出修正後的純文字**，不要任何前綴 / 後綴 / 引號 / Markdown / JSON。"
                        )
                        t0 = _t.time()
                        _ocrlog2.info("vision LLM call start: model=%s img=%dB text=%d chars",
                                      llm_vision_model_used, len(png_bytes), len(raw_text))
                        try:
                            # parse_json=False → 直接拿純文字（OCR 校對不需要 JSON wrap）
                            # max_tokens=2048：防 vision 模型不停、無止盡輸出
                            # think=False：對 gemma4 / qwen3 thinking model 抑制
                                # 推理 trace（不然 max_tokens 全花在 <thinking> 上、actual
                                # 答案部分為空，user 會看到「無回傳內容」）
                            r = client.vision_query(png_bytes=png_bytes, prompt=prompt,
                                                    model=llm_vision_model_used, temperature=0.0,
                                                    max_tokens=2048, parse_json=False,
                                                    think=False) or ""
                            r = r.strip()
                            _ocrlog2.info("vision LLM call done in %.1fs (got %d chars)", _t.time()-t0, len(r))
                            return r
                        except Exception as e:
                            _ocrlog2.warning("vision LLM call FAILED in %.1fs: %s", _t.time()-t0, e)
                            # 把例外往外拋；ocr_core 會把錯誤訊息寫進 stage 詳情
                            # 「呼叫失敗：…」比預設「無回傳內容」更有資訊
                            raise
                    llm_vision_cb = _llm_vision_cb
    except Exception:
        pass

    def _run(job: "_jm.Job") -> None:
        def _progress(cur, total, msg):
            job.progress = cur / max(total, 1) * 0.95
            job.message = msg
        try:
            try:
                from app.main import VERSION as _app_version
            except Exception:
                _app_version = ""
            # 拿 vision model 的 preferred image max（profile 偵測）
            vis_img_max = 1568
            if llm_vision_model_used:
                try:
                    from app.core.llm_model_profile import get_profile as _get_prof
                    from app.core.llm_settings import llm_settings as _ls
                    _prof = _get_prof(llm_vision_model_used, _ls.get().get("base_url", ""))
                    vis_img_max = _prof.preferred_image_max
                except Exception:
                    pass
            stats = ocr_core.ocr_pdf_to_searchable(
                src, out,
                langs=active_langs, dpi=dpi,
                skip_pages_with_text=skip_pages_with_text,
                progress_cb=_progress,
                llm_postprocess=llm_cb,
                llm_model_name=llm_model_used,
                llm_vision_postprocess=llm_vision_cb,
                llm_vision_model_name=llm_vision_model_used,
                llm_vision_image_max=vis_img_max,
                app_version=_app_version,
            )
            extra = ""
            if stats.get("llm_vision_used"):
                extra += f"，LLM 視覺校對 ({llm_vision_model_used})"
            if stats.get("llm_used"):
                extra += f"，LLM 文字校正 ({llm_model_used})"
            job.message = (f"完成 — 處理 {stats['pages_ocrd']}/{stats['pages_total']} 頁，"
                           f"插入 {stats['words_inserted']} 字" + extra)
            job.meta = {"upload_id": upload_id, "stats": stats,
                        "langs": active_langs,
                        "llm_model": llm_model_used,
                        "llm_vision_model": llm_vision_model_used,
                        "download_url": f"/tools/pdf-ocr/download/{upload_id}"}
            job.result_path = out
            job.result_filename = (Path(src).stem.replace("_src", "") + "_searchable.pdf")
        except Exception as e:
            job.error = str(e)
            raise

    job = _jm.job_manager.submit("pdf-ocr", _run, meta={"upload_id": upload_id})
    return {"job_id": job.id, "upload_id": upload_id}


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    out = _work_dir() / f"po_{upload_id}_out.pdf"
    if not out.exists():
        raise HTTPException(404, "尚未產生輸出（請先觸發檢核）")
    src = _work_dir() / f"po_{upload_id}_src.pdf"
    name = "searchable.pdf"
    if src.exists():
        name = src.stem.replace("po_", "").replace("_src", "") + "_searchable.pdf"
    from app.core.http_utils import content_disposition
    return FileResponse(out, media_type="application/pdf",
                          headers={"Content-Disposition": content_disposition(name)})


@router.get("/preview/{upload_id}.pdf")
async def preview(upload_id: str, request: Request):
    """Inline PDF stream for PDF.js viewer iframe (no attachment disposition)."""
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    out = _work_dir() / f"po_{upload_id}_out.pdf"
    if not out.exists():
        raise HTTPException(404, "尚未產生輸出")
    return FileResponse(out, media_type="application/pdf",
                          headers={"Content-Disposition": "inline",
                                   "Cache-Control": "private, max-age=0"})

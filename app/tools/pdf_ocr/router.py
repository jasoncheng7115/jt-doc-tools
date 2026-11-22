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


def _result_path(upload_id: str) -> Path:
    return _work_dir() / f"po_{upload_id}_result.json"


def _finish_meta(job, upload_id: str, data: dict) -> None:
    """作業完成：結果寫進 `job.meta`（**更新**，不可以整個換掉 —— 送出時記的檔名與「開啟」的網址會跟著不見），
    另外整份存一個結果檔。統計裡有逐頁全文，存回資料庫時超過上限會被截掉；從「我的作業」按「開啟」回來時讀這份。"""
    import json as _json
    job.meta.update({"upload_id": upload_id, **data})
    try:
        _result_path(upload_id).write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except (OSError, TypeError, ValueError):
        import logging as _lg
        _lg.getLogger(__name__).warning("pdf-ocr 結果檔寫不進去（「開啟」回來會只剩資料庫裡的摘要）", exc_info=True)


def _ocr_engine_note(stats: dict) -> str:
    """完成訊息裡「用了哪個 OCR 引擎、有沒有退回」那一段。

    網頁與 API 兩條路共用 —— 原本只有網頁那條有，API 送出的作業在「我的作業」
    上看不出模型下載失敗、其實是 Tesseract 辨識的（Win10 實機踩到：只寫
    「插入 12 字」，看起來像這份掃描件本來就沒什麼字）。
    """
    extra = ""
    # OCR 引擎使用情況(若有跑到 OCR engine — LLM 完整辨識可能完全跳過 OCR)
    ocr_engine_pages = stats.get("ocr_engine_pages") or {}
    ocr_engine_total_s = stats.get("ocr_engine_total_s", 0)
    ocr_remote_url = stats.get("ocr_remote_url", "")
    ocr_chosen_engine = stats.get("ocr_chosen_engine", "")
    ocr_remote_on = stats.get("ocr_remote_on", False)
    ENG_LABEL = {
        "easyocr-remote": f"遠端 GPU EasyOCR @ {ocr_remote_url}",
        "easyocr": "本機 EasyOCR (CPU)",
        "tesseract": "本機 Tesseract (CPU)",
    }
    def _chosen_label() -> str:
        if ocr_chosen_engine == "easyocr-remote":
            return "遠端 GPU EasyOCR"
        if ocr_chosen_engine == "easyocr":
            return "本機 EasyOCR"
        if ocr_chosen_engine == "tesseract":
            return "本機 Tesseract"
        return ocr_chosen_engine or "?"
    if ocr_engine_pages:
        # 多 engine 顯示細項；單一 engine 簡潔
        if len(ocr_engine_pages) == 1:
            eng = next(iter(ocr_engine_pages))
            label = ENG_LABEL.get(eng, eng)
            # 偵測退回：選用 (含 -remote 意圖) vs 實際 engine_used 不同 → 標示
            if ocr_chosen_engine and eng != ocr_chosen_engine:
                extra += f"，選用 {_chosen_label()} 失敗 → 退回 {label}, 用時 {ocr_engine_total_s}s"
            else:
                extra += f"，{label}, 用時 {ocr_engine_total_s}s"
        else:
            parts = []
            for eng, n in ocr_engine_pages.items():
                parts.append(f"{ENG_LABEL.get(eng, eng)}×{n}")
            extra += f"，OCR 引擎: {' / '.join(parts)} ({ocr_engine_total_s}s)"
    return extra


def _starting_message() -> str:
    """作業剛開始時的狀態文字。**第一次用本機 EasyOCR 要先下載模型**，
    要講出來 —— 只寫「準備中」的話，網路慢時看起來跟當掉一樣。"""
    try:
        from ...core.ocr_engine import easyocr_first_download_pending
        if easyocr_first_download_pending():
            return "第一次使用 OCR：正在下載辨識模型（約 300 MB，只需要一次）…"
    except Exception:                                    # noqa: BLE001
        pass
    return "準備中…（載入引擎 / 連線遠端 OCR Server）"


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
    return templates.TemplateResponse(request, "pdf_ocr.html", {
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


def _clip_repetition(text: str, max_repeats: int = 3) -> str:
    """偵測 LLM 重複生成迴圈，連續相同 line >= max_repeats 次就截斷。
    qwen2.5vl 等 vision LLM 在 temperature=0 + OCR 任務常陷入無限重複(e.g.
    「資安整競爭力之重」repeat 數千次) → 截斷避免污染對齊邏輯與全文展示。
    repeat_penalty=1.15 已大幅減少這種情況，本函式為防呆雙重保險。"""
    lines = text.splitlines()
    out: list[str] = []
    run_text = None
    run_count = 0
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append(ln)
            continue
        if s == run_text:
            run_count += 1
            if run_count > max_repeats:
                out.append("[...截斷:偵測 LLM 重複生成迴圈]")
                break
        else:
            run_text = s
            run_count = 1
        out.append(ln)
    return "\n".join(out)


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


def _name_sidecar(upload_id: str) -> Path:
    return _work_dir() / f"po_{upload_id}_name.txt"


def _remember_name(upload_id: str, filename: str) -> None:
    """把原始檔名記下來（給作業清單顯示）。

    OCR 的來源檔存成 `po_<id>_src.pdf`，原始檔名原本**只出現在上傳的回應裡、
    沒有被保存下來** —— 所以作業清單退而顯示結果檔名 `po_<32位hex>_searchable.pdf`，
    使用者看到的是一串看不出是什麼的內部代號（v1.14.6 使用者回報）。
    """
    try:
        _name_sidecar(upload_id).write_text(filename or "", encoding="utf-8")
    except OSError:
        pass      # 顯示不出名稱是小事，絕不能因此讓 OCR 失敗


def _orig_name(upload_id: str) -> str:
    try:
        return _name_sidecar(upload_id).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


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
    _remember_name(upload_id, file.filename or ("input" + suffix))
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
                   use_llm_vision: bool = Form(False),
                   use_llm_direct: bool = Form(False),
                   use_llm_align: bool = Form(False),
                   use_llm_full: bool = Form(False)):
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
    # LLM 直接辨識 callback（吃 png_bytes，直接回 OCR 文字；失敗會自動退回 EasyOCR）
    llm_direct_cb = None
    llm_direct_model_used = ""
    # LLM 對位辨識 callback（hybrid: 同時跑 EasyOCR 取座標 + LLM 取文字 + 行對齊）
    llm_align_cb = None
    llm_align_model_used = ""
    # LLM 完整辨識 callback（grounded: LLM 同時回文字 + bbox JSON, 不跑 OCR 引擎）
    llm_full_cb = None
    llm_full_model_used = ""
    # 主辨識模式互斥：完整 > 對位 > 直接（三擇一），且都跟校對互斥
    if use_llm_full:
        use_llm = False
        use_llm_vision = False
        use_llm_direct = False
        use_llm_align = False
    elif use_llm_direct:
        use_llm = False
        use_llm_vision = False
        use_llm_align = False
    elif use_llm_align:
        use_llm = False
        use_llm_vision = False
    # OCR 用獨立 LLM client，timeout 縮到 120s（避免單頁掛太久使用者沒回饋）
    OCR_LLM_TIMEOUT = 120.0
    # 文字校正（pdf-ocr）與視覺類（pdf-ocr-vision）是**兩組設定**，管理員可以把它們指到
    # 不同的 LLM 伺服器 —— 各建各的 client。指定的那一台不見了就**明確失敗**（503），
    # 不可以退回全站那一台，也不可以安靜地只跑 OCR（使用者勾了 LLM，會以為校正過了）。
    from app.core.llm_settings import LLMServerUnavailable as _LSU, llm_settings as _lls
    if _lls.is_enabled():
        _want = []
        if use_llm:
            _want.append("pdf-ocr")
        if use_llm_vision or use_llm_direct or use_llm_align or use_llm_full:
            _want.append("pdf-ocr-vision")
        for _tid in _want:
            if _lls.server_problem(_tid):
                raise HTTPException(503, _LSU.MESSAGE)
    try:
        from app.core.llm_settings import llm_settings
        if (use_llm or use_llm_vision or use_llm_direct or use_llm_align or use_llm_full) and llm_settings.is_enabled():
            # 重建 client：同一台伺服器，但 timeout 縮到 OCR_LLM_TIMEOUT
            client = (llm_settings.make_client("pdf-ocr", timeout=OCR_LLM_TIMEOUT)
                      if use_llm else None)
            vclient = (llm_settings.make_client("pdf-ocr-vision", timeout=OCR_LLM_TIMEOUT)
                       if (use_llm_vision or use_llm_direct or use_llm_align or use_llm_full)
                       else None)
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
            if use_llm_vision and vclient:
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
                            "   （例：OCR=「○○工具箱」→ 不可改成「××工具箱」即使後者更常見）\n"
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
                            r = vclient.vision_query(png_bytes=png_bytes, prompt=prompt,
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
            if use_llm_direct and vclient:
                # 直接辨識用 vision 模型（跟視覺校對共用 model 設定 key）
                llm_direct_model_used = llm_settings.get_model_for("pdf-ocr-vision")
                if llm_direct_model_used:
                    import logging as _lg
                    _ocrlog3 = _lg.getLogger("app.pdf_ocr.llm")
                    def _llm_direct_cb(png_bytes: bytes) -> str:
                        import time as _t
                        prompt = (
                            "你是 OCR 引擎。請逐字輸出影像中所有可讀文字，保持原順序與行結構。\n\n"
                            "## 絕對禁止（違反 = 失敗）\n"
                            "❌ 不能腦補：影像中沒寫的字一律不能寫出來\n"
                            "❌ 不能把模糊的字「猜成最像的常見詞」\n"
                            "   例：影像若寫「資安能量登錄」絕不可改成「資安量測」\n"
                            "       影像若寫「吳明徽」絕不可改成「吳明儀」\n"
                            "       影像若寫「中華民國資訊軟體服務商業同業公會」絕不可簡化成「軟體與服務業」\n"
                            "❌ 不能翻譯、整理、重述、解釋\n"
                            "❌ 不能輸出 markdown / JSON / 程式碼框 / 註解 / 前言 / 結語\n\n"
                            "## 必須遵守\n"
                            "✅ 公司名 / 人名 / 機關名 / 路名 / 編號 / 統編 / 日期 / 金額 → 字面照抄，即使覺得是錯字或不通也照抄\n"
                            "✅ 看不清楚 → 用 ? 代替（不要猜）\n"
                            "✅ 維持原行結構 — 影像中一行就輸出一行\n"
                            "✅ 只輸出影像中真實看得到的純文字\n"
                        )
                        t0 = _t.time()
                        _ocrlog3.info("direct LLM call start: model=%s img=%dB",
                                      llm_direct_model_used, len(png_bytes))
                        try:
                            r = vclient.vision_query(png_bytes=png_bytes, prompt=prompt,
                                                    model=llm_direct_model_used,
                                                    temperature=0.0, max_tokens=4096,
                                                    parse_json=False, think=False,
                                                    repeat_penalty=1.15) or ""
                            r = r.strip()
                            r_clipped = _clip_repetition(r)
                            if len(r_clipped) < len(r):
                                _ocrlog3.warning("direct LLM repetition loop detected, clipped %d → %d chars",
                                                  len(r), len(r_clipped))
                            _ocrlog3.info("direct LLM call done in %.1fs (got %d chars)",
                                          _t.time()-t0, len(r_clipped))
                            return r_clipped
                        except Exception as e:
                            _ocrlog3.warning("direct LLM call FAILED in %.1fs: %s", _t.time()-t0, e)
                            raise
                    llm_direct_cb = _llm_direct_cb
            if use_llm_align and vclient:
                # 對位辨識：同個 vision model,同個 prompt(只看圖獨立 OCR),
                # 但回到 ocr_core 後會跟 EasyOCR bbox 對齊。共用 pdf-ocr-vision 設定 key。
                llm_align_model_used = llm_settings.get_model_for("pdf-ocr-vision")
                if llm_align_model_used:
                    import logging as _lg
                    _ocrlog4 = _lg.getLogger("app.pdf_ocr.llm")
                    def _llm_align_cb(png_bytes: bytes) -> str:
                        import time as _t
                        prompt = (
                            "你是 OCR 引擎。請逐字輸出影像中所有可讀文字，保持原順序與行結構。\n\n"
                            "## 絕對禁止（違反 = 失敗）\n"
                            "❌ 不能腦補：影像中沒寫的字一律不能寫出來\n"
                            "❌ 不能把模糊的字「猜成最像的常見詞」\n"
                            "❌ 不能翻譯、整理、重述、解釋\n"
                            "❌ 不能輸出 markdown / JSON / 程式碼框 / 註解 / 前言 / 結語\n\n"
                            "## 必須遵守\n"
                            "✅ 公司名 / 人名 / 機關名 / 路名 / 編號 / 統編 / 日期 / 金額 → 字面照抄\n"
                            "✅ 看不清楚 → 用 ? 代替（不要猜）\n"
                            "✅ 維持原行結構 — 影像中一行就輸出一行\n"
                            "✅ 只輸出影像中真實看得到的純文字\n"
                        )
                        t0 = _t.time()
                        _ocrlog4.info("align LLM call start: model=%s img=%dB",
                                      llm_align_model_used, len(png_bytes))
                        try:
                            r = vclient.vision_query(png_bytes=png_bytes, prompt=prompt,
                                                    model=llm_align_model_used,
                                                    temperature=0.0, max_tokens=4096,
                                                    parse_json=False, think=False,
                                                    repeat_penalty=1.15) or ""
                            r = r.strip()
                            r_clipped = _clip_repetition(r)
                            if len(r_clipped) < len(r):
                                _ocrlog4.warning("align LLM repetition loop detected, clipped %d → %d chars",
                                                  len(r), len(r_clipped))
                            _ocrlog4.info("align LLM call done in %.1fs (got %d chars)",
                                          _t.time()-t0, len(r_clipped))
                            return r_clipped
                        except Exception as e:
                            _ocrlog4.warning("align LLM call FAILED in %.1fs: %s", _t.time()-t0, e)
                            raise
                    llm_align_cb = _llm_align_cb
            if use_llm_full and vclient:
                # 完整辨識：LLM 同時回文字 + bbox JSON,不跑 OCR 引擎。
                # 共用 pdf-ocr-vision 設定 key。
                llm_full_model_used = llm_settings.get_model_for("pdf-ocr-vision")
                if llm_full_model_used:
                    import logging as _lg
                    _ocrlog5 = _lg.getLogger("app.pdf_ocr.llm")
                    def _llm_full_cb(png_bytes: bytes) -> str:
                        import time as _t
                        # 取縮圖實際尺寸，塞進 prompt 讓 LLM 知道座標系統
                        try:
                            from PIL import Image as _PILImg
                            import io as _io
                            _img = _PILImg.open(_io.BytesIO(png_bytes))
                            iw, ih = _img.size
                        except Exception:
                            iw, ih = 0, 0
                        # qwen2.5-VL 對僵硬的 JSON / native token 格式會拒絕輸出座標。
                        # 用自然語言 prompt + plain text bbox 格式，實測穩定。
                        prompt = (
                            "請你識別出影像中的文字，並給出每塊文字所在圖對應的座標 "
                            "(不止 x, y, 要給出左上與右下四個數，即 x1, y1, x2, y2),\n"
                            f"最左上為 (0, 0), 此圖尺寸為 {iw} × {ih} 像素。\n\n"
                            "每塊文字輸出一行，格式:\n"
                            '"文字內容" - (x1, y1, x2, y2)\n\n'
                            "## 規則\n"
                            "- 一行一塊(可以是字 / 詞 / 行 / 短語)\n"
                            "- 不能腦補:影像中沒寫的字一律不能寫\n"
                            "- 看不清楚的字用 ? 代替，不要猜\n"
                            "- 公司名 / 人名 / 機關名 / 編號 / 日期 / 金額照原文寫\n"
                            "- 由上到下、由左到右排序\n"
                        )
                        t0 = _t.time()
                        _ocrlog5.info("full LLM call start: model=%s img=%dB", llm_full_model_used, len(png_bytes))
                        try:
                            r = vclient.vision_query(png_bytes=png_bytes, prompt=prompt,
                                                    model=llm_full_model_used,
                                                    temperature=0.0, max_tokens=8192,
                                                    parse_json=False, think=False,
                                                    repeat_penalty=1.15) or ""
                            r = r.strip()
                            r_clipped = _clip_repetition(r)
                            if len(r_clipped) < len(r):
                                _ocrlog5.warning("full LLM repetition loop detected, clipped %d → %d chars",
                                                  len(r), len(r_clipped))
                            _ocrlog5.info("full LLM call done in %.1fs (got %d chars)", _t.time()-t0, len(r_clipped))
                            return r_clipped
                        except Exception as e:
                            _ocrlog5.warning("full LLM call FAILED in %.1fs: %s", _t.time()-t0, e)
                            raise
                    llm_full_cb = _llm_full_cb
    except Exception:
        pass

    def _run(job: "_jm.Job") -> None:
        # 立刻設一個訊息，避免前端在 progress_cb 第一次呼叫前看到空白卡住
        # (遠端 GPU EasyOCR 首次載 model 可能 5-30 秒，這段時間需明示 user)
        job.message = _starting_message()
        def _progress(cur, total, msg):
            # 使用者按「停止辨識」→ job.cancelled = True → raise 觸發 _run 跳出
            # JobManager._run 偵測 job.cancelled 後會把 status 設成 'cancelled'
            if job.cancelled:
                raise RuntimeError("__cancelled_by_user__")
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
                    _prof = _get_prof(llm_vision_model_used, _ls.base_url_for("pdf-ocr-vision"))
                    vis_img_max = _prof.preferred_image_max
                except Exception:
                    pass
            # 若 llm_direct / llm_align / llm_full 用 vision 模型且未抓到 vis_img_max，retry 一次
            chosen_vision_model = llm_direct_model_used or llm_align_model_used or llm_full_model_used
            if chosen_vision_model and vis_img_max == 1568:
                try:
                    from app.core.llm_model_profile import get_profile as _get_prof
                    from app.core.llm_settings import llm_settings as _ls
                    _prof = _get_prof(chosen_vision_model, _ls.base_url_for("pdf-ocr-vision"))
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
                llm_direct_ocr=llm_direct_cb,
                llm_direct_model_name=llm_direct_model_used,
                llm_align_ocr=llm_align_cb,
                llm_align_model_name=llm_align_model_used,
                llm_full_ocr=llm_full_cb,
                llm_full_model_name=llm_full_model_used,
                app_version=_app_version,
            )
            extra = ""
            extra += _ocr_engine_note(stats)
            if stats.get("llm_full_used"):
                t = stats.get("llm_full_total_s", 0)
                extra += f"，LLM 完整辨識 ({llm_full_model_used}, 用時 {t}s)"
            if stats.get("llm_direct_used"):
                t = stats.get("llm_direct_total_s", 0)
                extra += f"，LLM 直接辨識 ({llm_direct_model_used}, 用時 {t}s)"
            if stats.get("llm_align_used"):
                t = stats.get("llm_align_total_s", 0)
                extra += f"，LLM 對位辨識 ({llm_align_model_used}, 用時 {t}s)"
            if stats.get("llm_vision_used"):
                extra += f"，LLM 視覺校對 ({llm_vision_model_used})"
            if stats.get("llm_used"):
                extra += f"，LLM 文字校正 ({llm_model_used})"
            job.message = (f"完成 — 處理 {stats['pages_ocrd']}/{stats['pages_total']} 頁，"
                           f"插入 {stats['words_inserted']} 字" + extra)
            _finish_meta(job, upload_id, {
                "stats": stats, "langs": active_langs,
                "llm_model": llm_model_used,
                "llm_vision_model": llm_vision_model_used,
                "llm_direct_model": llm_direct_model_used,
                "llm_align_model": llm_align_model_used,
                "llm_full_model": llm_full_model_used,
                "download_url": f"/tools/pdf-ocr/download/{upload_id}"})
            job.result_path = out
            job.result_filename = (Path(src).stem.replace("_src", "") + "_searchable.pdf")
        except Exception as e:
            job.error = str(e)
            raise

    job = _jm.job_manager.submit("pdf-ocr", _run, meta={"upload_id": upload_id,
                                     "filename": _orig_name(upload_id)})
    job.meta["view_url"] = f"/tools/pdf-ocr/?job={job.id}"   # 「我的作業」的「開啟」回到這一頁、接回結果
    return {"job_id": job.id, "upload_id": upload_id}


@router.get("/result/{upload_id}")
async def result(upload_id: str, request: Request):
    """辨識完成後的完整結果（統計、逐頁文字）。從「我的作業」按「開啟」回來時用；擁有者才讀得到。"""
    import json as _json
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    p = _result_path(upload_id)
    if not p.is_file():
        raise HTTPException(404, "找不到這次辨識的結果（可能已過保留期限）")
    return _json.loads(p.read_text(encoding="utf-8"))


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


# ---- 對外 API：單次 upload + 背景 OCR job + 回 job_id ----
@router.post("/api/pdf-ocr", include_in_schema=True)
async def api_pdf_ocr(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("chi_tra+eng"),
    dpi: int = Form(300),
    skip_pages_with_text: bool = Form(True),
):
    """單次上傳 PDF / 圖檔，背景跑 OCR 補文字層。回 job_id 與下載 URL；
    請輪詢 /api/jobs/{job_id} 取得進度，完成後 GET /api/jobs/{job_id}/download。"""
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
    if suffix == ".pdf":
        src.write_bytes(raw)
    else:
        try:
            pdf_bytes = _image_to_pdf_bytes(raw)
            src.write_bytes(pdf_bytes)
        except Exception as e:
            raise HTTPException(400, f"圖檔轉 PDF 失敗：{e}")
    _uo.record(upload_id, request)
    out = _work_dir() / f"po_{upload_id}_out.pdf"
    active_langs = (lang or ocr_core.get_active_langs()).strip() or "eng"
    dpi_val = max(72, min(int(dpi), 600))

    def _run(job: "_jm.Job") -> None:
        job.message = _starting_message()
        def _progress(cur, total, msg):
            if job.cancelled:
                raise RuntimeError("__cancelled_by_user__")
            job.progress = cur / max(total, 1) * 0.95
            job.message = msg
        try:
            stats = ocr_core.ocr_pdf_to_searchable(
                src, out,
                langs=active_langs, dpi=dpi_val,
                skip_pages_with_text=skip_pages_with_text,
                progress_cb=_progress,
            )
            job.message = (f"完成 — 處理 {stats['pages_ocrd']}/{stats['pages_total']} 頁，"
                           f"插入 {stats['words_inserted']} 字" + _ocr_engine_note(stats))
            _finish_meta(job, upload_id, {"stats": stats, "langs": active_langs,
                                          "download_url": f"/api/jobs/{job.id}/download"})
            job.result_path = out
            job.result_filename = Path(src).stem.replace("_src", "") + "_searchable.pdf"
        except Exception as e:
            job.error = str(e)
            raise

    job = _jm.job_manager.submit("pdf-ocr", _run, meta={"upload_id": upload_id,
                                     "filename": _orig_name(upload_id)})
    job.meta["view_url"] = f"/tools/pdf-ocr/?job={job.id}"   # 「我的作業」的「開啟」回到這一頁、接回結果
    return {"job_id": job.id, "upload_id": upload_id,
            "download_url": f"/api/jobs/{job.id}/download"}

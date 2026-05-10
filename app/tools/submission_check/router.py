"""送件前檢核 — Endpoints.

主要 endpoint:
  GET  /                          → 上傳頁
  GET  /cases                     → 案件清單頁
  GET  /case/{case_id}            → 案件詳情頁
  POST /upload                    → 建 case 並上傳一批檔
  POST /run/{case_id}             → 觸發新版本檢核（背景跑）
  GET  /status/{job_id}           → SSE 進度
  GET  /result/{case_id}/{ver}    → JSON 結果
  GET  /file/{case_id}/{file_id}  → 取個別檔（預覽用）
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

from ...config import settings
from ...core import job_manager as _jm
from ...core import upload_owner as _uo
from ...core.safe_paths import is_uuid_hex
from . import case_manager as _cm
from .checks import l1_rules as _l1
from .checks import consistency as _consistency
from .checks import l2_ocr as _l2
from .checks import l3_llm as _l3
from .checks import l4_vision as _l4
from .checks import important as _important
from . import override as _override
from . import chaining as _chaining


def _set_layer_status(job, layer: str, status: str, extra: str = "") -> None:
    """更新 job.meta.layers 給前端 LED 燈號用。"""
    layers = (job.meta or {}).setdefault("layers", {
        "L1": "pending", "L2": "pending", "L3": "pending", "L4": "pending",
    })
    layers[layer] = status
    if extra:
        layers[layer + "_extra"] = extra

router = APIRouter()


# ─── Page renders ────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def page_root(request: Request) -> HTMLResponse:
    """上傳頁 — 拖檔 + ground truth 引導。"""
    templates = request.app.state.templates
    return templates.TemplateResponse("sc_upload.html",
                                       {"request": request, "title": "送件前檢核"})


@router.get("/cases", response_class=HTMLResponse)
async def page_case_list(request: Request, q: str = "", status: str = "") -> HTMLResponse:
    """案件清單頁 — 支援 ?q=主角 / ?status=draft|done|... 篩選。"""
    templates = request.app.state.templates
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    owner_uid = (user or {}).get("user_id") if isinstance(user, dict) else None
    is_admin = _is_admin(user)
    is_auditor = _is_auditor(user)
    # 稽核員 / admin 可看包含已刪除的案件
    cases = _cm.list_cases(owner_uid=owner_uid, admin=is_admin, auditor=is_auditor,
                            include_deleted=(is_admin or is_auditor), limit=200)
    # filter
    q = (q or "").strip().lower()
    if q:
        def _matches(c):
            me = c.get("main_entity") or {}
            blob = " ".join(filter(None, [
                c.get("case_id", ""),
                str(me.get("name", "")),
                str(me.get("identifier", "")),
            ])).lower()
            return q in blob
        cases = [c for c in cases if _matches(c)]
    if status:
        cases = [c for c in cases if c.get("status") == status]
    return templates.TemplateResponse("sc_case_list.html",
                                       {"request": request, "title": "案件清單",
                                        "cases": cases, "q": q, "status_filter": status})


@router.get("/admin-stats", response_class=HTMLResponse)
async def page_admin_stats(request: Request, days: int = 30) -> HTMLResponse:
    """Admin 儀表板 — 跨案件 stats（admin 限定）。"""
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    if not _is_admin(user) and user is not None:
        raise HTTPException(403, "僅 admin 可看此頁")
    from . import stats as _stats
    s = _stats.gather_stats(days=max(1, min(days, 365)))
    templates = request.app.state.templates
    return templates.TemplateResponse("sc_admin_stats.html",
                                       {"request": request, "title": "送件檢核儀表板",
                                        "stats": s, "days": days})


@router.get("/case/{case_id}", response_class=HTMLResponse)
async def page_case_detail(case_id: str, request: Request) -> HTMLResponse:
    """案件詳情頁。"""
    templates = request.app.state.templates
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(404, "case 不存在")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    _check_case_acl(case, request)
    versions_with_reports = []
    for v in case.get("versions", []):
        rep = _cm.load_version_report(case_id, v)
        versions_with_reports.append({"version": v, "report": rep})
    return templates.TemplateResponse("sc_case_detail.html",
                                       {"request": request,
                                        "title": f"案件 {case_id[:8]}",
                                        "case": case,
                                        "versions_with_reports": versions_with_reports})


# ─── API: upload + run ───────────────────────────────────────────────────


@router.post("/upload")
async def upload_files(
    request: Request,
    files: list[UploadFile] = File(...),
    case_id: Optional[str] = Form(None),
    main_entity_name: Optional[str] = Form(None),
    main_entity_identifier: Optional[str] = Form(None),
    counterparty_name: Optional[str] = Form(None),
    case_number: Optional[str] = Form(None),
    deadline: Optional[str] = Form(None),
):
    """建 case（若 case_id=None）+ 上傳多檔 + 設 ground truth。

    回 {"case_id": "...", "files": [...]}.
    """
    if not files:
        raise HTTPException(400, "請至少上傳一個檔案")
    if len(files) > 50:
        raise HTTPException(400, "單次上傳上限 50 檔")

    # 建 / 取 case
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    owner_uid = (user or {}).get("user_id") if isinstance(user, dict) else None
    if case_id:
        if not _cm.CASE_ID_RE.match(case_id):
            raise HTTPException(400, "invalid case_id")
        case = _cm.load_case(case_id)
        if not case:
            raise HTTPException(404, "case 不存在")
        _check_case_acl(case, request)
    else:
        case = _cm.create_case(owner_uid=owner_uid)
        # 同時把 case 寫進 upload_owner 為 owner record（讓 /file/ ACL 過得去）
        _uo.record(case["case_id"], request)

    # 設 ground truth (overwrite)
    gt = case.setdefault("ground_truth", {})
    if main_entity_name:
        gt["main_entity"] = {
            "name": main_entity_name.strip(),
            "type": None,           # 後續 L3 LLM 自動推估
            "identifier": (main_entity_identifier or "").strip() or None,
            "aliases": [],
        }
    if counterparty_name:
        gt["counterparty"] = {"name": counterparty_name.strip(), "type": None}
    if case_number:
        gt["case_number"] = case_number.strip()
    if deadline:
        gt["deadline"] = deadline.strip()

    # 寫檔 + 計 hash
    case_files_dir = _cm.case_dir(case["case_id"]) / "files"
    case_files_dir.mkdir(parents=True, exist_ok=True)
    total_size = 0
    saved = []
    for up in files:
        raw = await up.read()
        size = len(raw)
        total_size += size
        if total_size > 200 * 1024 * 1024:
            raise HTTPException(400, "本批次累計超過 200 MB 上限")
        file_id = uuid.uuid4().hex
        sha256 = hashlib.sha256(raw).hexdigest()
        suffix = Path(up.filename or "").suffix.lower()
        if suffix not in (".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".tiff", ".tif"):
            raise HTTPException(400, f"不支援的檔案類型：{up.filename}")
        # .doc (Word 97-2003 binary OLE) 不是 zip，後續解析會失敗 → 用 soffice 自動轉成 .docx
        store_suffix = suffix
        if suffix == ".doc":
            tmp_doc = case_files_dir / f"{file_id}.doc"
            tmp_doc.write_bytes(raw)
            target_docx = case_files_dir / f"{file_id}.docx"
            try:
                from app.core import office_convert as _oc
                _oc.convert_to_docx(tmp_doc, target_docx, timeout=90.0)
                tmp_doc.unlink(missing_ok=True)
                store_suffix = ".docx"
            except Exception as e:
                tmp_doc.unlink(missing_ok=True)
                raise HTTPException(
                    400,
                    f"無法處理 .doc 檔（{up.filename}）— {str(e)[:120]}。"
                    "請在 Word 內另存為 .docx 後再上傳。"
                )
        else:
            save_path = case_files_dir / f"{file_id}{suffix}"
            save_path.write_bytes(raw)
        _cm.add_file_to_case(case, file_id,
                             original_name=up.filename or file_id,
                             size=size, sha256=sha256,
                             mime=up.content_type or "")
        saved.append({"file_id": file_id, "name": up.filename, "size": size,
                      "auto_converted": (store_suffix != suffix)})

    return {"case_id": case["case_id"], "files": saved}


@router.post("/run/{case_id}")
async def run_check(case_id: str, request: Request):
    """觸發新版本檢核 — 背景跑 L1 (Sprint 1 範圍)，將來會疊加 L2/L3。"""
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(400, "invalid case_id")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    _check_case_acl(case, request)
    if not case.get("files"):
        raise HTTPException(400, "case 內沒有檔案，請先上傳")

    version = _cm.new_version(case)
    case["status"] = "running"
    _cm.save_case(case)

    def _run(job: "_jm.Job") -> None:
        try:
            report = _build_report_l1(case, version, job)
            _cm.save_version_report(case_id, version, report)
            # update case status
            cur = _cm.load_case(case_id)
            if cur:
                cur["status"] = "done"
                _cm.save_case(cur)
            job.message = "完成"
            job.meta = {"case_id": case_id, "version": version,
                        "summary": report.get("summary")}
        except Exception as e:
            job.error = str(e)
            cur = _cm.load_case(case_id)
            if cur:
                cur["status"] = "error"
                _cm.save_case(cur)
            raise

    job = _jm.job_manager.submit("submission-check", _run,
                                  meta={"case_id": case_id, "version": version})
    return {"job_id": job.id, "case_id": case_id, "version": version}


@router.get("/result/{case_id}/{version}")
async def get_result(case_id: str, version: str, request: Request):
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(400, "invalid case_id")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    _check_case_acl(case, request)
    rep = _cm.load_version_report(case_id, version)
    if not rep:
        raise HTTPException(404, "報告未產生")
    return rep


@router.delete("/case/{case_id}")
async def delete_case(case_id: str, request: Request):
    """軟刪除案件 — owner 或 admin 可刪。稽核員仍可從清單看到（標已刪除）。"""
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(400, "invalid case_id")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    is_admin = _is_admin(user)
    # 稽核員不可刪（read-only）
    if _is_auditor(user) and not is_admin:
        raise HTTPException(403, "稽核員為唯讀，無法刪除案件")
    # 一般 user 只能刪自己的
    if user and not is_admin:
        owner_uid = case.get("owner_uid")
        user_uid = user.get("user_id") if isinstance(user, dict) else None
        if owner_uid is not None and owner_uid != user_uid:
            raise HTTPException(403, "您只能刪除自己建立的案件")
    by_user = (user or {}).get("username") if isinstance(user, dict) else "anonymous"
    _cm.soft_delete_case(case_id, by_user=by_user)
    return {"ok": True, "case_id": case_id}


@router.post("/override/{case_id}")
async def post_override(case_id: str, request: Request,
                          finding_key: str = Form(...),
                          verdict: str = Form(...),
                          reason: str = Form("")):
    """加 / 更新 override 註解。"""
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(400, "invalid case_id")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    _check_case_acl(case, request)
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    by_user = (user or {}).get("username") if isinstance(user, dict) else None
    try:
        o = _override.add_override(case_id, finding_key, verdict, reason, by_user)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "override": o}


@router.delete("/override/{case_id}/{finding_key}")
async def delete_override(case_id: str, finding_key: str, request: Request):
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(400, "invalid case_id")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    _check_case_acl(case, request)
    ok = _override.remove_override(case_id, finding_key)
    return {"ok": ok}


@router.get("/page-preview/{case_id}/{file_id}/{page}")
async def get_page_preview(case_id: str, file_id: str, page: int, request: Request):
    """渲染指定 PDF 頁面為 PNG 預覽圖（供 finding 預覽 modal 用）。"""
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(400, "invalid case_id")
    if not re.match(r"^[a-f0-9]{32}$", file_id):
        raise HTTPException(400, "invalid file_id")
    if page < 1 or page > 5000:
        raise HTTPException(400, "page out of range")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    _check_case_acl(case, request)
    fdir = _cm.case_dir(case_id) / "files"
    matches = list(fdir.glob(f"{file_id}.*"))
    if not matches:
        raise HTTPException(404, "檔案不存在")
    path = matches[0]
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            import fitz
            doc = fitz.open(str(path))
            try:
                if page > doc.page_count:
                    raise HTTPException(404, f"page {page} 超過 PDF 總頁數 {doc.page_count}")
                pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72))
                png = pix.tobytes("png")
            finally:
                doc.close()
        elif suffix in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
            png = path.read_bytes()
        else:
            raise HTTPException(400, "此檔案類型不支援頁面預覽")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"渲染失敗：{e}")
    from fastapi.responses import Response
    return Response(content=png, media_type="image/png")


@router.get("/file/{case_id}/{file_id}")
async def get_file(case_id: str, file_id: str, request: Request):
    """取個別檔 — for 前端跳頁預覽。"""
    if not _cm.CASE_ID_RE.match(case_id):
        raise HTTPException(400, "invalid case_id")
    case = _cm.load_case(case_id)
    if not case:
        raise HTTPException(404, "case 不存在")
    _check_case_acl(case, request)
    fdir = _cm.case_dir(case_id) / "files"
    matches = list(fdir.glob(f"{file_id}.*"))
    if not matches:
        raise HTTPException(404, "檔案不存在")
    return FileResponse(matches[0])


# ─── 內部 helpers ──────────────────────────────────────────────────────


def _is_admin(user) -> bool:
    """User 是否 admin —  看 effective_tools 是否含 ALL marker / role 是 admin。"""
    if not user:
        return False
    if isinstance(user, dict):
        et = user.get("effective_tools") or []
        return "*" in et or user.get("role") == "admin"
    return False


def _is_auditor(user) -> bool:
    """User 是否稽核員 — 走既有 perm.is_auditor()。"""
    if not user or not isinstance(user, dict):
        return False
    uid = user.get("user_id")
    if not uid:
        return False
    try:
        from app.core import perm as _perm
        return bool(_perm.is_auditor(int(uid)))
    except Exception:
        return False


def _check_case_acl(case: dict, request: Request) -> None:
    """Case 級 ACL — case owner / admin / 稽核員 可讀（稽核員唯讀，不可改 / 刪）。"""
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    if not user:
        return  # auth OFF
    if _is_admin(user) or _is_auditor(user):
        return
    owner_uid = case.get("owner_uid")
    user_uid = user.get("user_id") if isinstance(user, dict) else None
    if owner_uid is not None and owner_uid != user_uid:
        raise HTTPException(403, "您沒有權限存取此案件")


def _build_report_l1(case: dict, version: str, job: "_jm.Job") -> dict:
    """跑 L1 規則檢查 + 文字層身分一致性彙總成 report.

    Sprint 1：L1 only
    Sprint 2：+ 文字層實體抽取 + 跨檔身分一致性
    """
    case_id = case["case_id"]
    fdir = _cm.case_dir(case_id) / "files"
    files = case.get("files", [])
    n = len(files)

    findings_per_file: dict[str, list[dict]] = {}
    per_file_entities: dict[str, dict] = {}
    per_file_text: dict[str, str] = {}     # 抽出的所有文字（給 amount/date/attachment 用）
    per_file_amounts: dict[str, list] = {}
    per_file_dates: dict[str, list] = {}
    ocr_total_elapsed = 0.0
    ocr_files_processed = 0
    vision_calls = 0

    # 初始化 6 層燈號狀態
    job.meta = job.meta or {}
    job.meta["layers"] = {
        "L1": "running",   # 規則
        "L2": "pending",   # 文字抽取
        "L3": "pending",   # 全頁 OCR
        "L4": "pending",   # 嵌入圖 OCR
        "L5": "pending",   # LLM 文字
        "L6": "pending",   # LLM 視覺
    }

    embed_image_total = 0
    for i, f in enumerate(files):
        job.progress = (i + 0.1) / max(n, 1) * 0.45
        job.message = f"檢查中：{f.get('name', '?')} ({i+1}/{n})"
        # 一旦進入第一檔，標 L2/L3/L4 為 running（並行進行同檔三件事）
        if i == 0:
            _set_layer_status(job, "L2", "running")
            _set_layer_status(job, "L3", "running")
            _set_layer_status(job, "L4", "running")
        file_id = f["file_id"]
        matches = list(fdir.glob(f"{file_id}.*"))
        if not matches:
            findings_per_file[file_id] = [{
                "layer": "L1", "severity": "fail",
                "category": "missing-file",
                "title": "檔案不存在於儲存區",
                "detail": "可能已被清掉或路徑錯亂。",
                "page": None, "evidence": {},
            }]
            continue
        path = matches[0]
        # L1 規則
        findings = _l1.scan_file(path, mime_hint=f.get("mime", ""))
        # 文字層實體 + 抽 text（給 important checks 用）
        try:
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                text_layer = _consistency._extract_pdf_text(path)
            elif suffix in (".docx", ".doc"):
                text_layer = _consistency._extract_docx_text(path)
            else:
                text_layer = ""
            if text_layer:
                per_file_text[file_id] = text_layer
                per_file_amounts[file_id] = _important.extract_amounts(text_layer)
                per_file_dates[file_id] = _important.extract_dates(text_layer)
            ents = _consistency.extract_entities_from_text(text_layer) if text_layer else {}
            if ents:
                per_file_entities[file_id] = ents
        except Exception:
            pass
        # L2 OCR — 對掃描 PDF / 圖片補抽
        try:
            ocr_res = _l2.ocr_file(path)
            ocr_total_elapsed += ocr_res.get("elapsed", 0.0)
            if ocr_res.get("ran"):
                ocr_files_processed += 1
                # 把 OCR 文字餵回 entity extraction
                ocr_text = ocr_res.get("text", "")
                if ocr_text:
                    # 把 OCR 文字 merge 進 per_file_text 給 important checks
                    per_file_text[file_id] = (per_file_text.get(file_id, "") + "\n" + ocr_text).strip()
                    # 抽 amount / date 補進
                    extra_amts = _important.extract_amounts(ocr_text)
                    if extra_amts:
                        per_file_amounts.setdefault(file_id, [])
                        for v in extra_amts:
                            if v not in per_file_amounts[file_id]:
                                per_file_amounts[file_id].append(v)
                    extra_dates = _important.extract_dates(ocr_text)
                    if extra_dates:
                        per_file_dates.setdefault(file_id, [])
                        for d in extra_dates:
                            if d not in per_file_dates[file_id]:
                                per_file_dates[file_id].append(d)
                    extra_ents = _consistency.extract_entities_from_text(ocr_text)
                    if extra_ents:
                        # merge 進該檔的 entities (相加)
                        existing = per_file_entities.get(file_id, {})
                        for kind, counter in extra_ents.items():
                            tgt = existing.setdefault(kind, type(counter)())
                            tgt.update(counter)
                        per_file_entities[file_id] = existing
            # L3 (full-page OCR) findings (truncated / skipped 等資訊)
            findings.extend(_l2.make_findings(file_id, ocr_res, file_name=f.get("name", "")))
        except Exception:
            pass

        # L4: 嵌入圖片 OCR — 即使 PDF 有完整文字層，圖片內字 (章/印/截圖證書) 也要抽
        try:
            if path.suffix.lower() == ".pdf" and _l2.is_tesseract_available():
                img_text, n_imgs = _l2.extract_and_ocr_pdf_images(path)
                if n_imgs > 0:
                    embed_image_total += n_imgs
                    if img_text.strip():
                        per_file_text[file_id] = (per_file_text.get(file_id, "") + "\n" + img_text).strip()
                        # entity 補抽
                        extra_ents = _consistency.extract_entities_from_text(img_text)
                        if extra_ents:
                            existing = per_file_entities.get(file_id, {})
                            for kind, counter in extra_ents.items():
                                tgt = existing.setdefault(kind, type(counter)())
                                tgt.update(counter)
                            per_file_entities[file_id] = existing
                        # amount / date 補抽
                        extra_amts = _important.extract_amounts(img_text)
                        if extra_amts:
                            per_file_amounts.setdefault(file_id, [])
                            for v in extra_amts:
                                if v not in per_file_amounts[file_id]:
                                    per_file_amounts[file_id].append(v)
                        extra_dates = _important.extract_dates(img_text)
                        if extra_dates:
                            per_file_dates.setdefault(file_id, [])
                            for d in extra_dates:
                                if d not in per_file_dates[file_id]:
                                    per_file_dates[file_id].append(d)
        except Exception:
            pass

        findings_per_file[file_id] = findings

    # L1 / L2 / L3 / L4 都跑完
    _set_layer_status(job, "L1", "done")
    # L2 文字抽取一定跑（從 PDF / DOCX 文字層）
    text_extracted = sum(1 for v in per_file_text.values() if v)
    _set_layer_status(job, "L2", "done", f"{text_extracted} 檔抽出文字")
    # L3 全頁 OCR（掃描檔）
    if not _l2.is_tesseract_available():
        _set_layer_status(job, "L3", "skipped", "tesseract 未安裝")
        _set_layer_status(job, "L4", "skipped", "tesseract 未安裝")
    else:
        if ocr_files_processed > 0:
            _set_layer_status(job, "L3", "done", f"{ocr_files_processed} 檔全頁 OCR")
        else:
            _set_layer_status(job, "L3", "skipped", "無掃描檔需要全頁 OCR")
        # L4 嵌入圖 OCR
        if embed_image_total > 0:
            _set_layer_status(job, "L4", "done", f"{embed_image_total} 張圖 OCR")
        else:
            _set_layer_status(job, "L4", "skipped", "PDF 內無嵌入圖片")

    job.progress = 0.55
    job.message = "跨檔身分一致性分析中..."

    # 跨檔 hash 重複
    cross_findings = _l1.cross_file_duplicate_hash(files)

    # 跨檔實體聚合
    aggregated = _consistency.aggregate_across_files(per_file_entities)

    # L5: LLM 文字 — 變體合併
    l3_used = False
    if _l3.llm_available():
        _set_layer_status(job, "L5", "running")
        try:
            job.progress = 0.60
            job.message = "L5 LLM 文字：變體合併中..."
            company_values = [e["value"] for e in aggregated.get("company", [])]
            if len(company_values) >= 2:
                groups = _l3.merge_entity_variants(company_values)
                aggregated = _l3.apply_variant_groups_to_aggregated(aggregated, groups)
                l3_used = True
        except Exception:
            pass

    # 一致性 findings
    consistency_findings = _consistency.detect_consistency_findings(
        aggregated, ground_truth=case.get("ground_truth"), files_meta=files,
    )
    cross_findings.extend(consistency_findings)

    # E 類重要檢查項：金額一致 + 日期合理 + 附件清單
    try:
        cross_findings.extend(_important.detect_amount_inconsistency(per_file_amounts, files))
    except Exception:
        pass
    try:
        deadline_str = (case.get("ground_truth") or {}).get("deadline") or ""
        cross_findings.extend(_important.detect_expired_dates(per_file_dates, files, deadline_str))
    except Exception:
        pass
    try:
        cross_findings.extend(_important.detect_attachment_count_mismatch(per_file_text, files))
    except Exception:
        pass

    # L5: LLM 文字 — 修改範本痕跡推論
    l3_residue_count = 0
    if _l3.llm_available():
        try:
            job.progress = 0.70
            job.message = "L5 LLM 文字：範本痕跡推論中..."
            file_summaries = []
            for f in files:
                # 取該檔的文字（PDF 文字層或 OCR）— 若 per_file_entities 有就找對應；否則重抽前 500 字
                fid = f["file_id"]
                matches = list(fdir.glob(f"{fid}.*"))
                if not matches:
                    continue
                snippet = ""
                try:
                    suffix = matches[0].suffix.lower()
                    if suffix == ".pdf":
                        import fitz
                        doc = fitz.open(str(matches[0]))
                        try:
                            snippet = (doc[0].get_text() if doc.page_count > 0 else "")[:500]
                        finally:
                            doc.close()
                    elif suffix in (".docx",):
                        snippet = _consistency._extract_docx_text(matches[0])[:500]
                except Exception:
                    pass
                if snippet:
                    file_summaries.append({"file_id": fid, "name": f.get("name", "?"),
                                            "snippet": snippet})
            gt_main = ((case.get("ground_truth") or {}).get("main_entity") or {}).get("name") or ""
            l3_findings = _l3.detect_template_residue(file_summaries, ground_truth_main=gt_main)
            # 把 _for_file 的 finding 塞進該檔 findings_per_file
            for fnd in l3_findings:
                tgt = fnd.pop("_for_file", None)
                if tgt and tgt in findings_per_file:
                    findings_per_file[tgt].append(fnd)
                else:
                    cross_findings.append(fnd)
                l3_residue_count += 1
            l3_used = True
        except Exception:
            pass

    if l3_used:
        _set_layer_status(job, "L5", "done", "LLM 文字分析完成")
    else:
        _set_layer_status(job, "L5", "skipped", "LLM 未設定")

    # L6: LLM 視覺 — 對每檔渲染後送 vision LLM
    l4_used = False
    # 重新處理 findings_per_file 中由 L4 vision 加的 findings 標記為 layer="L6"
    if _l4.vision_available():
        _set_layer_status(job, "L6", "running")
        try:
            gt_main = ((case.get("ground_truth") or {}).get("main_entity") or {}).get("name") or ""
            gt_cp = ((case.get("ground_truth") or {}).get("counterparty") or {}).get("name") or ""
            for i, f in enumerate(files):
                fid = f["file_id"]
                fpaths = list(fdir.glob(f"{fid}.*"))
                if not fpaths:
                    continue
                job.progress = 0.78 + (i + 1) / max(n, 1) * 0.18
                job.message = f"L6 LLM 視覺：分析 {f.get('name', '?')} ({i+1}/{n})..."
                try:
                    v_findings = _l4.vision_check_file(fpaths[0], gt_main, gt_cp)
                    # 將 layer 從 L4 改成 L6 (與 6 層架構對齊)
                    for fnd in v_findings:
                        fnd["layer"] = "L6"
                    if v_findings:
                        findings_per_file.setdefault(fid, []).extend(v_findings)
                    vision_calls += 1
                except Exception:
                    pass
            l4_used = True
            _set_layer_status(job, "L6", "done", f"{vision_calls} 檔視覺分析")
        except Exception:
            _set_layer_status(job, "L6", "error", "vision 階段異常")
    else:
        try:
            from app.core.llm_settings import llm_settings
            if not llm_settings.is_enabled():
                _set_layer_status(job, "L6", "skipped", "LLM 未設定")
            else:
                _set_layer_status(job, "L6", "skipped", "目前 LLM 模型非 vision")
        except Exception:
            _set_layer_status(job, "L6", "skipped", "LLM 未設定")

    # 套 user override 註解 + chaining 提示
    fresh_case = _cm.load_case(case_id) or case

    def _enrich(fds):
        fds = _override.apply_overrides_to_findings(fresh_case, fds)
        for fd in fds:
            fd["chaining"] = _chaining.chaining_for(fd.get("category", ""))
            fd["finding_key"] = _override._make_finding_key(fd)
        return fds

    findings_per_file = {fid: _enrich(fds) for fid, fds in findings_per_file.items()}
    cross_findings = _enrich(cross_findings)
    n_overrides_applied = sum(
        1 for fds in findings_per_file.values()
        for fd in fds if fd.get("_override")
    ) + sum(1 for fd in cross_findings if fd.get("_override"))

    # summary
    n_fail = sum(1 for f in files
                 for fd in findings_per_file.get(f["file_id"], [])
                 if fd["severity"] == "fail")
    n_warn = sum(1 for f in files
                 for fd in findings_per_file.get(f["file_id"], [])
                 if fd["severity"] == "warn")
    n_info = sum(1 for f in files
                 for fd in findings_per_file.get(f["file_id"], [])
                 if fd["severity"] == "info")
    n_fail += sum(1 for fd in cross_findings if fd["severity"] == "fail")
    n_warn += sum(1 for fd in cross_findings if fd["severity"] == "warn")
    n_info += sum(1 for fd in cross_findings if fd["severity"] == "info")

    # 簡易就緒度分 — 0 fail = 100, 每 fail -10, 每 warn -3
    score = max(0, 100 - n_fail * 10 - n_warn * 3)

    return {
        "case_id": case_id,
        "version": version,
        "generated_at": time.time(),
        "summary": {
            "files_count": n,
            "score": score,
            "fail": n_fail, "warn": n_warn, "info": n_info,
            "overrides_applied": n_overrides_applied,
            "layers": (job.meta or {}).get("layers", {}),
        },
        "findings_per_file": findings_per_file,
        "cross_findings": cross_findings,
    }

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
from .checks import important as _important
from . import override as _override

router = APIRouter()


# ─── Page renders ────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
async def page_root(request: Request) -> HTMLResponse:
    """上傳頁 — 拖檔 + ground truth 引導。"""
    templates = request.app.state.templates
    return templates.TemplateResponse("sc_upload.html",
                                       {"request": request, "title": "送件前檢核"})


@router.get("/cases", response_class=HTMLResponse)
async def page_case_list(request: Request) -> HTMLResponse:
    """案件清單頁。"""
    templates = request.app.state.templates
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    owner_uid = (user or {}).get("user_id") if isinstance(user, dict) else None
    is_admin = _is_admin(user)
    cases = _cm.list_cases(owner_uid=owner_uid, admin=is_admin, limit=100)
    return templates.TemplateResponse("sc_case_list.html",
                                       {"request": request, "title": "案件清單",
                                        "cases": cases})


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
        # 用 file_id + 原副檔名儲存（避免中文 / 路徑問題）
        suffix = Path(up.filename or "").suffix.lower()
        if suffix not in (".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png", ".tiff", ".tif"):
            raise HTTPException(400, f"不支援的檔案類型：{up.filename}")
        save_path = case_files_dir / f"{file_id}{suffix}"
        save_path.write_bytes(raw)
        _cm.add_file_to_case(case, file_id,
                             original_name=up.filename or file_id,
                             size=size, sha256=sha256,
                             mime=up.content_type or "")
        saved.append({"file_id": file_id, "name": up.filename, "size": size})

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
        # ALL marker convention：包含 "*" 或角色為 admin
        return "*" in et or user.get("role") == "admin"
    return False


def _check_case_acl(case: dict, request: Request) -> None:
    """Case 級 ACL — case owner 或 admin 才可存取（auth ON 才生效）。"""
    user = getattr(request.state, "user", None) if hasattr(request, "state") else None
    if not user:
        return  # auth OFF
    if _is_admin(user):
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

    for i, f in enumerate(files):
        job.progress = (i + 0.1) / max(n, 1) * 0.7
        job.message = f"檢查中：{f.get('name', '?')} ({i+1}/{n})"
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
            # L2 findings (OCR truncated / skipped 等資訊)
            findings.extend(_l2.make_findings(file_id, ocr_res, file_name=f.get("name", "")))
        except Exception:
            pass
        findings_per_file[file_id] = findings

    job.progress = 0.80
    job.message = "跨檔身分一致性分析中..."

    # 跨檔 hash 重複
    cross_findings = _l1.cross_file_duplicate_hash(files)

    # 跨檔實體聚合
    aggregated = _consistency.aggregate_across_files(per_file_entities)

    # L3: LLM 變體合併（如果 LLM enabled）
    l3_used = False
    if _l3.llm_available():
        try:
            job.progress = 0.85
            job.message = "L3 LLM：變體合併中..."
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

    # L3: 修改範本痕跡推論
    l3_residue_count = 0
    if _l3.llm_available():
        try:
            job.progress = 0.92
            job.message = "L3 LLM：範本痕跡推論中..."
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

    # 套 user override 註解（將被 user 標 OK / 誤報的 finding 降級）
    fresh_case = _cm.load_case(case_id) or case
    findings_per_file = {
        fid: _override.apply_overrides_to_findings(fresh_case, fds)
        for fid, fds in findings_per_file.items()
    }
    cross_findings = _override.apply_overrides_to_findings(fresh_case, cross_findings)
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
            "layers": {
                "L1": "done",
                "L2": (f"done ({ocr_files_processed} 檔 OCR, {ocr_total_elapsed:.1f}s)"
                       if ocr_files_processed > 0 else "skipped (無需 OCR 或 tesseract 未裝)"),
                "L3": ("done (LLM)" if l3_used else "skipped (LLM 未設定)"),
            },
        },
        "findings_per_file": findings_per_file,
        "cross_findings": cross_findings,
    }

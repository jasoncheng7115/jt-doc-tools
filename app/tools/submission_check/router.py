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

    for i, f in enumerate(files):
        job.progress = (i + 0.1) / max(n, 1) * 0.8
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
        findings = _l1.scan_file(path, mime_hint=f.get("mime", ""))
        findings_per_file[file_id] = findings
        try:
            ents = _consistency.extract_entities_from_file(path)
            if ents:
                per_file_entities[file_id] = ents
        except Exception:
            pass

    job.progress = 0.85
    job.message = "跨檔身分一致性分析中..."

    # 跨檔 hash 重複
    cross_findings = _l1.cross_file_duplicate_hash(files)

    # Sprint 2: 跨檔實體聚合 + 一致性 findings
    aggregated = _consistency.aggregate_across_files(per_file_entities)
    consistency_findings = _consistency.detect_consistency_findings(
        aggregated, ground_truth=case.get("ground_truth"), files_meta=files,
    )
    cross_findings.extend(consistency_findings)

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
            "layers": {"L1": "done", "L2": "partial (文字層)", "L3": "skipped"},
        },
        "findings_per_file": findings_per_file,
        "cross_findings": cross_findings,
    }

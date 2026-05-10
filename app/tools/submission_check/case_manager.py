"""Case (送件案件) 管理 — CRUD + 檔案儲存 + 元資料持久化。

目錄結構：
  <data>/submission_check/<case_id>/
    case.json              # 案件元資料 + 基準資訊 + 狀態
    files/                 # 原始上傳檔
    versions/              # 多版本快照
      v1/
        report.json        # 該版本檢核結果
        artifacts/         # 截圖等附件
    audit.json             # case 級 audit trail（每次跑 / override）

`case.json` schema 看 _make_blank_case() / load_case()。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from ...config import settings


CASE_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _root() -> Path:
    """Case 資料夾根目錄 — 走既有 data_dir，跟 fill_history 等同階層。"""
    p = settings.data_dir / "submission_check"
    p.mkdir(parents=True, exist_ok=True)
    return p


def case_dir(case_id: str) -> Path:
    """Resolve case 目錄 + 嚴格驗 case_id（防 path traversal）。"""
    if not CASE_ID_RE.match(case_id):
        raise ValueError(f"invalid case_id: {case_id!r}")
    return _root() / case_id


def new_case_id() -> str:
    return uuid.uuid4().hex


def _make_blank_case(case_id: str, owner_uid: Optional[int]) -> dict:
    return {
        "case_id": case_id,
        "owner_uid": owner_uid,
        "status": "draft",                      # draft / running / done / archived
        "created_at": time.time(),
        "updated_at": time.time(),
        # 案件基準資訊 — user 填或系統推估
        "ground_truth": {
            "main_entity": None,                # {name, type, identifier, aliases}
            "counterparty": None,
            "case_number": None,
            "deadline": None,
        },
        # 上傳的檔案 (依 upload 時間序)
        "files": [],                            # [{file_id, name, size, sha256, mime, uploaded_at}]
        # 版本歷史
        "versions": [],                         # ["v1", "v2", ...]
        "current_version": None,
        # User override 註解（per-finding）
        "overrides": [],                        # [{finding_id, verdict, reason, by_user, at}]
    }


def create_case(owner_uid: Optional[int] = None) -> dict:
    """建立空案件，回傳 case dict。"""
    case_id = new_case_id()
    cdir = case_dir(case_id)
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "files").mkdir(exist_ok=True)
    (cdir / "versions").mkdir(exist_ok=True)
    case = _make_blank_case(case_id, owner_uid)
    save_case(case)
    return case


def load_case(case_id: str) -> Optional[dict]:
    cdir = case_dir(case_id)
    f = cdir / "case.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_case(case: dict) -> None:
    case["updated_at"] = time.time()
    cdir = case_dir(case["case_id"])
    cdir.mkdir(parents=True, exist_ok=True)
    f = cdir / "case.json"
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def add_file_to_case(case: dict, file_id: str, original_name: str,
                     size: int, sha256: str, mime: str) -> None:
    case["files"].append({
        "file_id": file_id,
        "name": original_name,
        "size": size,
        "sha256": sha256,
        "mime": mime,
        "uploaded_at": time.time(),
    })
    save_case(case)


def list_cases(owner_uid: Optional[int] = None,
               admin: bool = False, auditor: bool = False,
               include_deleted: bool = False, limit: int = 50) -> list[dict]:
    """列出案件。
    - admin / auditor：看全部
    - 一般 user：只看自己的
    - 預設不含已刪除案件；auditor 預設 include_deleted=True 看歷史
    """
    out: list[dict] = []
    for cdir in sorted(_root().iterdir(), reverse=True):
        if not cdir.is_dir():
            continue
        if not CASE_ID_RE.match(cdir.name):
            continue
        case = load_case(cdir.name)
        if not case:
            continue
        # 權限過濾
        if not (admin or auditor) and case.get("owner_uid") != owner_uid:
            continue
        # 已刪除過濾
        is_deleted = bool(case.get("deleted_at"))
        if is_deleted and not include_deleted:
            continue
        # 人類可讀時間
        from datetime import datetime
        def _fmt(ts):
            if not ts:
                return "—"
            try:
                return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                return "—"
        # 檔名摘要：第一檔 + 「等 N 個」
        files_list = case.get("files", []) or []
        if not files_list:
            files_summary = "—"
        elif len(files_list) == 1:
            files_summary = files_list[0].get("name", "(未命名)")
        else:
            files_summary = f"{files_list[0].get('name', '(未命名)')} 等 {len(files_list)} 個"
        out.append({
            "case_id": case["case_id"],
            "status": case["status"],
            "main_entity": (case.get("ground_truth") or {}).get("main_entity"),
            "files_count": len(files_list),
            "files_summary": files_summary,
            "current_version": case.get("current_version"),
            "created_at": case.get("created_at"),
            "updated_at": case.get("updated_at"),
            "created_at_str": _fmt(case.get("created_at")),
            "updated_at_str": _fmt(case.get("updated_at")),
            "owner_uid": case.get("owner_uid"),
            "deleted_at": case.get("deleted_at"),
            "deleted_by": case.get("deleted_by"),
            "deleted_at_str": _fmt(case.get("deleted_at")),
        })
        if len(out) >= limit:
            break
    return out


def soft_delete_case(case_id: str, by_user: Optional[str] = None) -> bool:
    """軟刪除案件 — 標記 deleted_at + deleted_by，原始檔案保留供稽核。
    回 True 表示成功；False 表示 case 不存在。
    """
    case = load_case(case_id)
    if not case:
        return False
    if case.get("deleted_at"):
        return True  # 已是刪除狀態
    case["deleted_at"] = time.time()
    case["deleted_by"] = by_user or "anonymous"
    case["status"] = "deleted"
    save_case(case)
    return True


def new_version(case: dict) -> str:
    """建立新版本資料夾，回傳版本字串如 'v3'。"""
    next_n = len(case.get("versions", [])) + 1
    vstr = f"v{next_n}"
    vdir = case_dir(case["case_id"]) / "versions" / vstr
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / "artifacts").mkdir(exist_ok=True)
    case.setdefault("versions", []).append(vstr)
    case["current_version"] = vstr
    save_case(case)
    return vstr


def version_dir(case_id: str, version: str) -> Path:
    if not re.match(r"^v\d+$", version):
        raise ValueError(f"invalid version: {version!r}")
    return case_dir(case_id) / "versions" / version


def save_version_report(case_id: str, version: str, report: dict) -> None:
    vdir = version_dir(case_id, version)
    f = vdir / "report.json"
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


def load_version_report(case_id: str, version: str) -> Optional[dict]:
    f = version_dir(case_id, version) / "report.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None

"""User workspace — per-account server-side storage for tool outputs.

Users click 「存至工作區」 on a tool's PDF/PNG output to keep the file on the
server under their own account; 「從工作區載入」 feeds a saved file back into
any tool's upload box. The admin enables / disables the whole feature and sets
a single uniform per-user quota + retention (no per-user overrides). When the
feature is disabled, no UI nor endpoint is exposed.

Storage layout::

    data/workspace/<user_key>/<file_id>/
        ├─ file.pdf | file.png      ← the stored artefact (fixed safe name)
        └─ meta.json                ← {file_id, name, ext, mime, size,
                                        source_tool, saved_at, user_label}

``user_key`` is ``u<user_id>`` when auth is ON, or ``__single__`` when auth is
OFF (a single shared workspace for the one local operator). Cross-user access
is structurally prevented: every read/write resolves under the *requesting*
user's own directory, and ``file_id`` is validated as 32-hex so it can never
escape the directory. Only PDF + PNG are accepted (validated by magic bytes).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import Request

logger = logging.getLogger(__name__)

# mime -> extension. Intentionally tiny: feature is scoped to PDF + PNG.
ALLOWED: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
}

_SINGLE_KEY = "__single__"  # auth-OFF shared workspace


# --------------------------------------------------------------------------- #
# Settings (data/workspace.json)
# --------------------------------------------------------------------------- #

_DEFAULTS: dict[str, Any] = {
    "enabled": True,           # admin master switch — off hides everything
    "per_user_quota_mb": 500,  # 0/-1 = unlimited
    "max_file_mb": 50,         # 0/-1 = unlimited
    "retention_hours": 24,     # -1 = keep forever
    "updated_at": 0.0,
}

_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None


def _settings_path() -> Path:
    from ..config import settings
    return settings.data_dir / "workspace.json"


def get_settings() -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            p = _settings_path()
            merged = json.loads(json.dumps(_DEFAULTS))
            if p.exists():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    merged.update({k: v for k, v in raw.items() if k in _DEFAULTS})
                except Exception:
                    pass
            _CACHE = merged
        return json.loads(json.dumps(_CACHE))


def save_settings(new: dict[str, Any]) -> dict[str, Any]:
    """Merge + persist workspace settings (atomic write, 0600)."""
    global _CACHE
    with _LOCK:
        merged = json.loads(json.dumps(_DEFAULTS))
        cur = _CACHE if _CACHE is not None else None
        if cur:
            merged.update({k: cur[k] for k in _DEFAULTS if k in cur})
        for k in _DEFAULTS:
            if k == "updated_at" or k not in new:
                continue
            v = new[k]
            if k == "enabled":
                merged[k] = bool(v)
            else:
                if not isinstance(v, (int, float)) or isinstance(v, bool):
                    raise ValueError(f"{k} 必須是數字")
                merged[k] = int(v)
        merged["updated_at"] = time.time()
        p = _settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(p)
        _CACHE = merged
        return json.loads(json.dumps(merged))


def is_enabled() -> bool:
    return bool(get_settings().get("enabled"))


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #

class WorkspaceError(Exception):
    """Base for user-facing workspace failures (caller maps to HTTP 4xx)."""


class WorkspaceDisabled(WorkspaceError):
    pass


class QuotaExceeded(WorkspaceError):
    pass


class UnsupportedType(WorkspaceError):
    pass


class NotFound(WorkspaceError):
    pass


# --------------------------------------------------------------------------- #
# User identity → storage key
# --------------------------------------------------------------------------- #

def _auth_enabled() -> bool:
    try:
        from . import auth_settings as _as
        return _as.is_enabled()
    except Exception:
        return False


def _user_id(request: Request) -> Optional[int]:
    user = getattr(getattr(request, "state", None), "user", None)
    if not user:
        return None
    v = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _user_label(request: Request) -> str:
    from . import sessions
    user = getattr(getattr(request, "state", None), "user", None)
    return sessions.user_label(user) if user else ""


def user_key(request: Request) -> str:
    """Storage key for the requesting user. Raises WorkspaceError when auth is
    ON but no user is bound to the request (anonymous → no workspace)."""
    if not _auth_enabled():
        return _SINGLE_KEY
    uid = _user_id(request)
    if uid is None:
        raise WorkspaceError("尚未登入")
    return f"u{uid}"


def _root() -> Path:
    from ..config import settings
    return settings.data_dir / "workspace"


def _user_dir(request: Request, create: bool = False) -> Path:
    d = _root() / user_key(request)
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- #
# Type detection
# --------------------------------------------------------------------------- #

def detect_kind(data: bytes) -> Optional[tuple[str, str]]:
    """Return (mime, ext) for a supported file by magic bytes, else None."""
    if data[:4] == b"%PDF":
        return "application/pdf", ".pdf"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    return None


def _clean_display_name(name: str, ext: str) -> str:
    """A safe, friendly display filename. Stored only in meta.json (never used
    as a filesystem path), so we just strip control chars + cap length and
    ensure the right extension."""
    name = (name or "").strip().replace("\r", " ").replace("\n", " ")
    name = "".join(ch for ch in name if ch.isprintable())
    # drop any directory components a caller might have sent
    name = name.replace("\\", "/").split("/")[-1]
    if not name:
        name = "file" + ext
    if not name.lower().endswith(ext):
        # replace a wrong/absent extension
        stem = name.rsplit(".", 1)[0] if "." in name else name
        name = stem + ext
    return name[:200]


# --------------------------------------------------------------------------- #
# Usage / quota
# --------------------------------------------------------------------------- #

def _dir_size(p: Path) -> int:
    if not p.exists():
        return 0
    total = 0
    for root, _, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def usage(request: Request) -> dict[str, Any]:
    s = get_settings()
    used = _dir_size(_user_dir(request))
    quota_mb = int(s.get("per_user_quota_mb") or 0)
    quota_bytes = quota_mb * 1024 * 1024 if quota_mb > 0 else 0  # 0 = unlimited
    return {
        "used_bytes": used,
        "quota_bytes": quota_bytes,          # 0 = unlimited
        "max_file_bytes": (int(s.get("max_file_mb") or 0) * 1024 * 1024
                           if int(s.get("max_file_mb") or 0) > 0 else 0),
    }


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #

def _meta_path(d: Path) -> Path:
    return d / "meta.json"


def _read_meta(d: Path) -> Optional[dict[str, Any]]:
    mf = _meta_path(d)
    if not mf.exists():
        return None
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_bytes(request: Request, data: bytes, display_name: str,
               source_tool: str = "") -> dict[str, Any]:
    """Persist bytes into the requesting user's workspace. Validates the master
    switch, file type, single-file cap and per-user quota. Returns the meta."""
    if not is_enabled():
        raise WorkspaceDisabled("工作區功能未啟用")
    if not data:
        raise WorkspaceError("檔案為空")
    kind = detect_kind(data)
    if kind is None:
        raise UnsupportedType("工作區只接受 PDF 或 PNG 檔")
    mime, ext = kind
    s = get_settings()
    max_file_mb = int(s.get("max_file_mb") or 0)
    if max_file_mb > 0 and len(data) > max_file_mb * 1024 * 1024:
        raise QuotaExceeded(f"單檔超過上限 {max_file_mb} MB")
    u = usage(request)
    if u["quota_bytes"] and u["used_bytes"] + len(data) > u["quota_bytes"]:
        quota_mb = u["quota_bytes"] // 1024 // 1024
        raise QuotaExceeded(f"工作區容量已滿（額度 {quota_mb} MB），請先刪除舊檔")

    file_id = uuid.uuid4().hex
    d = _user_dir(request, create=True) / file_id
    d.mkdir(parents=True, exist_ok=True)
    (d / f"file{ext}").write_bytes(data)
    meta = {
        "file_id": file_id,
        "name": _clean_display_name(display_name, ext),
        "ext": ext,
        "mime": mime,
        "size": len(data),
        "source_tool": (source_tool or "")[:64],
        "saved_at": time.time(),
        "user_label": _user_label(request),
    }
    _meta_path(d).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return meta


def list_files(request: Request) -> list[dict[str, Any]]:
    if not is_enabled():
        return []
    base = _user_dir(request)
    if not base.exists():
        return []
    out: list[dict[str, Any]] = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        meta = _read_meta(d)
        if meta:
            out.append(meta)
    out.sort(key=lambda m: m.get("saved_at", 0), reverse=True)
    return out


def count_files(request: Request) -> int:
    """Lightweight count of the user's workspace entries (no meta reads)."""
    if not is_enabled():
        return 0
    base = _user_dir(request)
    if not base.exists():
        return 0
    n = 0
    for d in base.iterdir():
        if d.is_dir() and _meta_path(d).exists():
            n += 1
    return n


def _entry_dir(request: Request, file_id: str) -> Path:
    from .safe_paths import is_uuid_hex
    if not is_uuid_hex(file_id):
        raise NotFound("檔案不存在")
    d = _user_dir(request) / file_id
    if not d.is_dir() or _read_meta(d) is None:
        raise NotFound("檔案不存在")
    return d


def get_file(request: Request, file_id: str) -> tuple[Path, dict[str, Any]]:
    """Return (file_path, meta) for one of the requesting user's files. Raises
    NotFound if it doesn't exist / isn't theirs (resolved under their dir)."""
    if not is_enabled():
        raise WorkspaceDisabled("工作區功能未啟用")
    d = _entry_dir(request, file_id)
    meta = _read_meta(d) or {}
    fp = d / f"file{meta.get('ext', '')}"
    if not fp.exists():
        raise NotFound("檔案不存在")
    return fp, meta


def get_thumbnail(request: Request, file_id: str) -> tuple[Path, str]:
    """Return (path, mime) for a preview thumbnail of one of the user's files.
    PNG → the image itself; PDF → first page rendered to a cached thumb.png
    (cached in the entry dir, so it's cleaned with the file). Raises NotFound /
    WorkspaceError on failure (caller serves a placeholder)."""
    if not is_enabled():
        raise WorkspaceDisabled("工作區功能未啟用")
    d = _entry_dir(request, file_id)
    meta = _read_meta(d) or {}
    ext = meta.get("ext", "")
    if ext == ".png":
        fp = d / "file.png"
        if not fp.exists():
            raise NotFound("檔案不存在")
        return fp, "image/png"
    # PDF → render first page (cache thumb.png).
    thumb = d / "thumb.png"
    if thumb.exists():
        return thumb, "image/png"
    src = d / "file.pdf"
    if not src.exists():
        raise NotFound("檔案不存在")
    try:
        import fitz
        with fitz.open(str(src)) as doc:
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.3, 1.3), alpha=False)
            pix.save(str(thumb))
    except Exception as e:  # noqa: BLE001
        raise WorkspaceError(f"無法產生預覽：{e.__class__.__name__}")
    return thumb, "image/png"


def delete_file(request: Request, file_id: str) -> bool:
    if not is_enabled():
        raise WorkspaceDisabled("工作區功能未啟用")
    d = _entry_dir(request, file_id)
    shutil.rmtree(d, ignore_errors=True)
    return True


def rename_file(request: Request, file_id: str, new_name: str) -> dict[str, Any]:
    if not is_enabled():
        raise WorkspaceDisabled("工作區功能未啟用")
    d = _entry_dir(request, file_id)
    meta = _read_meta(d) or {}
    meta["name"] = _clean_display_name(new_name, meta.get("ext", ""))
    _meta_path(d).write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return meta


# --------------------------------------------------------------------------- #
# Retention sweep + admin-wide stats
# --------------------------------------------------------------------------- #

def sweep_older_than(seconds: int) -> int:
    """Delete workspace entries whose saved_at is older than `seconds`.
    seconds <= 0 → no-op (keep forever). Returns count removed."""
    if seconds <= 0:
        return 0
    root = _root()
    if not root.exists():
        return 0
    cutoff = time.time() - seconds
    removed = 0
    for udir in root.iterdir():
        if not udir.is_dir():
            continue
        for d in udir.iterdir():
            if not d.is_dir():
                continue
            meta = _read_meta(d)
            ts = (meta or {}).get("saved_at")
            if ts is None:
                # No meta → fall back to mtime so orphans still expire.
                try:
                    ts = d.stat().st_mtime
                except OSError:
                    continue
            if ts < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


_USER_KEY_RE = re.compile(r"^(u\d+|__single__)$")


def admin_clear_user(user_key: str) -> int:
    """Admin housekeeping: delete ALL of one user's workspace files. Returns
    the number of entries removed. (Admin manages capacity but does not browse
    individual file contents — consistent with the app's no-snoop model.)"""
    if not _USER_KEY_RE.match(user_key or ""):
        return 0
    d = _root() / user_key
    if not d.is_dir():
        return 0
    n = sum(1 for x in d.iterdir() if x.is_dir() and (x / "meta.json").exists())
    shutil.rmtree(d, ignore_errors=True)
    return n


def admin_clear_all() -> int:
    """Admin: clear every user's workspace. Returns total entries removed."""
    root = _root()
    if not root.exists():
        return 0
    total = 0
    for udir in list(root.iterdir()):
        if udir.is_dir():
            total += admin_clear_user(udir.name)
    return total


def collect_stats() -> dict[str, Any]:
    """Admin view: per-user usage + totals."""
    root = _root()
    users: list[dict[str, Any]] = []
    total_bytes = 0
    total_count = 0
    if root.exists():
        for udir in sorted(root.iterdir()):
            if not udir.is_dir():
                continue
            cnt = 0
            label = ""
            for d in udir.iterdir():
                if d.is_dir() and _meta_path(d).exists():
                    cnt += 1
                    if not label:
                        label = (_read_meta(d) or {}).get("user_label", "")
            size = _dir_size(udir)
            total_bytes += size
            total_count += cnt
            users.append({
                "key": udir.name,
                "label": label or (udir.name if udir.name != _SINGLE_KEY else "（單機模式）"),
                "count": cnt,
                "bytes": size,
            })
    users.sort(key=lambda u: u["bytes"], reverse=True)
    return {"users": users, "total_bytes": total_bytes, "total_count": total_count}

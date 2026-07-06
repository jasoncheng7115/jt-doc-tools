"""Scheduled AD / LDAP directory sync into the local cache.

Why: the 群組管理 page used to fire **one live LDAP query per group row** on
every load (to show each group's real member count). With thousands of groups
that is thousands of round-trips → the page "等很久". This module mirrors the
directory groups into the local `groups` table and **caches each group's member
count** there, so the page reads the local DB (milliseconds) and never touches
LDAP on load.

Runs once at startup + every N hours (configurable), and can be triggered
manually from the admin UI ("立即同步"). Only does anything when the auth
backend is `ldap` / `ad`.

Settings live in `data/directory_sync.json`:
    { enabled, interval_hours, name_contains, last_run_at, last_result }
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SCHED_THREAD: Optional[threading.Thread] = None
_SCHED_STOP = threading.Event()
_RUN_LOCK = threading.Lock()          # never run two syncs at once
_running = False

_DEFAULTS = {
    "enabled": True,
    "interval_hours": 6,
    "name_contains": "",              # optional filter to skip system groups
    "sync_users": True,              # also mirror all directory users → local
    "last_run_at": None,              # epoch seconds
    "last_result": None,             # dict from the last run
    "last_error": None,
}


# --------------------------------------------------------------------- settings

def _path():
    from ..config import settings
    return settings.data_dir / "directory_sync.json"


def get_settings() -> dict[str, Any]:
    p = _path()
    data = dict(_DEFAULTS)
    try:
        if p.exists():
            data.update(json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        logger.warning("directory_sync settings unreadable; using defaults")
    # clamp interval to a sane range (1h .. 7d)
    try:
        data["interval_hours"] = max(1, min(168, int(data.get("interval_hours", 6))))
    except Exception:  # noqa: BLE001
        data["interval_hours"] = 6
    return data


def save_settings(*, enabled: Optional[bool] = None,
                  interval_hours: Optional[int] = None,
                  name_contains: Optional[str] = None,
                  sync_users: Optional[bool] = None) -> dict[str, Any]:
    data = get_settings()
    if enabled is not None:
        data["enabled"] = bool(enabled)
    if interval_hours is not None:
        data["interval_hours"] = max(1, min(168, int(interval_hours)))
    if name_contains is not None:
        data["name_contains"] = str(name_contains).strip()[:128]
    if sync_users is not None:
        data["sync_users"] = bool(sync_users)
    _write(data)
    return data


def _write(data: dict[str, Any]) -> None:
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        tmp.replace(p)
    except Exception:  # noqa: BLE001
        logger.exception("failed to persist directory_sync settings")


# ------------------------------------------------------------------- the sync

def is_directory_backend() -> bool:
    try:
        from . import auth_settings
        return (auth_settings.get() or {}).get("backend", "off") in ("ldap", "ad")
    except Exception:  # noqa: BLE001
        return False


def run_sync(name_contains: Optional[str] = None) -> dict[str, Any]:
    """Mirror directory groups + cache each group's member count. Returns a
    report dict. Safe to call from the scheduler or a manual trigger; a second
    concurrent call returns ``{"skipped": "already running"}``."""
    global _running
    if not is_directory_backend():
        return {"skipped": "backend is not ldap/ad"}
    if not _RUN_LOCK.acquire(blocking=False):
        return {"skipped": "already running"}
    _running = True
    started = time.time()
    from . import auth_ldap, auth_db
    settings_now = get_settings()
    if name_contains is None:
        name_contains = settings_now.get("name_contains", "") or ""
    report: dict[str, Any] = {"groups_mirrored": None, "counts_updated": 0,
                              "counts_failed": 0, "users_synced": None,
                              "started_at": started}
    try:
        # 1) mirror the directory group list into the local table
        mirror = auth_ldap.sync_all_groups(name_contains=name_contains)
        report["groups_mirrored"] = mirror
        # 2) cache each ldap/ad group's real member count
        conn = auth_db.conn()
        rows = conn.execute(
            "SELECT id, external_dn FROM groups "
            "WHERE source IN ('ldap','ad') AND external_dn<>''"
        ).fetchall()
        for r in rows:
            dn = (r["external_dn"] or "").strip()
            if not dn:
                continue
            try:
                n = auth_ldap.count_group_members(dn)
                conn.execute(
                    "UPDATE groups SET member_count=?, member_count_synced_at=? "
                    "WHERE id=?", (int(n), time.time(), r["id"]))
                report["counts_updated"] += 1
            except Exception:  # noqa: BLE001
                report["counts_failed"] += 1
        conn.commit()
        # 3) mirror all directory users into the local users table (so 使用者管理
        #    shows everyone + admin can pre-assign roles). Best-effort — a user
        #    sync failure must not lose the group results already committed.
        if settings_now.get("sync_users", True):
            try:
                report["users_synced"] = auth_ldap.sync_all_users()
            except Exception as uexc:  # noqa: BLE001
                report["users_synced"] = {"error": f"{type(uexc).__name__}: {uexc}"}
                logger.exception("user sync failed (group sync kept)")
        report["elapsed_sec"] = round(time.time() - started, 1)
        _stamp(ok=True, result=report)
        logger.info("directory sync done: %s", report)
        return report
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["elapsed_sec"] = round(time.time() - started, 1)
        _stamp(ok=False, result=report, error=report["error"])
        logger.exception("directory sync failed")
        return report
    finally:
        _running = False
        _RUN_LOCK.release()


def _stamp(*, ok: bool, result: dict, error: Optional[str] = None) -> None:
    data = get_settings()
    data["last_run_at"] = time.time()
    data["last_result"] = result
    data["last_error"] = None if ok else error
    _write(data)


def is_running() -> bool:
    return _running


# ------------------------------------------------------------------ scheduler

def start_scheduler() -> None:
    global _SCHED_THREAD
    with _LOCK:
        if _SCHED_THREAD is not None and _SCHED_THREAD.is_alive():
            return
        _SCHED_STOP.clear()
        _SCHED_THREAD = threading.Thread(
            target=_loop, name="directory-sync", daemon=True)
        _SCHED_THREAD.start()


def stop_scheduler() -> None:
    _SCHED_STOP.set()
    if _SCHED_THREAD is not None:
        _SCHED_THREAD.join(timeout=5)


def _loop() -> None:
    # Slight startup delay so we don't pile onto the boot sequence; the pages
    # read whatever is already cached until the first run completes.
    if _SCHED_STOP.wait(30):
        return
    while not _SCHED_STOP.is_set():
        try:
            s = get_settings()
            if s.get("enabled") and is_directory_backend():
                run_sync()
        except Exception:  # noqa: BLE001
            logger.exception("scheduled directory sync failed")
        interval = max(1, int(get_settings().get("interval_hours", 6))) * 3600
        if _SCHED_STOP.wait(interval):
            break

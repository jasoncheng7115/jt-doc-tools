"""排程設定匯出 — 定期把全站設定打包成 zip，並輪替舊檔。

預設**關閉**。啟用後由背景執行緒每小時檢查一次「距離上次匯出是否已達週期」，
到期就呼叫 `settings_export.export_to_zip` 產生一份備份到指定目錄，並依「保留份數」
刪掉最舊的。設定存 `data/scheduled_export.json`（mode 600）。

設定欄位：
  - enabled: bool（預設 False）
  - interval: 'daily' | 'weekly'（預設 daily）
  - target_dir: 匯出目錄（預設 data/settings_backups，隨 data 一起被既有備援帶走）
  - keep: 保留份數（預設 14，超過刪最舊）
  - categories: 要匯出的類別 id 清單（None/空 = 預設全含的類別）
  - last_run: epoch（唯讀，唔手動改）
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..config import settings

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = {"daily": 86400, "weekly": 7 * 86400}
_CHECK_EVERY = 3600  # 每小時檢查一次是否到期

_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None

_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "interval": "daily",
    "target_dir": "",        # 空 = data/settings_backups
    "keep": 14,
    "categories": None,      # None = settings_export 的預設類別
    "last_run": 0.0,
    "last_result": "",
}


def _path() -> Path:
    return settings.data_dir / "scheduled_export.json"


def _default_target() -> Path:
    return settings.data_dir / "settings_backups"


def get_settings() -> dict[str, Any]:
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            p = _path()
            if p.exists():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    merged = json.loads(json.dumps(_DEFAULTS))
                    merged.update({k: raw[k] for k in raw if k in _DEFAULTS})
                    _CACHE = merged
                except Exception:
                    _CACHE = json.loads(json.dumps(_DEFAULTS))
            else:
                _CACHE = json.loads(json.dumps(_DEFAULTS))
        d = json.loads(json.dumps(_CACHE))
    # Present the effective target dir for the UI.
    d["target_dir_effective"] = d.get("target_dir") or str(_default_target())
    return d


def save_settings(new: dict[str, Any]) -> dict[str, Any]:
    """Validate + persist. Preserves last_run/last_result (read-only)."""
    global _CACHE
    interval = (new.get("interval") or "daily").strip()
    if interval not in _INTERVAL_SECONDS:
        raise ValueError("interval 只能是 daily 或 weekly")
    try:
        keep = int(new.get("keep", 14))
    except (TypeError, ValueError):
        raise ValueError("保留份數必須是整數")
    if keep < 1 or keep > 500:
        raise ValueError("保留份數需在 1–500 之間")
    target_dir = (new.get("target_dir") or "").strip()
    cats = new.get("categories")
    if cats is not None and not isinstance(cats, list):
        raise ValueError("categories 必須是清單或 null")
    cur = get_settings()
    merged = {
        "enabled": bool(new.get("enabled")),
        "interval": interval,
        "target_dir": target_dir,
        "keep": keep,
        "categories": list(cats) if cats else None,
        "last_run": cur.get("last_run", 0.0),
        "last_result": cur.get("last_result", ""),
    }
    _write(merged)
    return get_settings()


def _write(d: dict[str, Any]) -> None:
    global _CACHE
    with _LOCK:
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(p)
        _CACHE = d


def _do_export(cfg: dict) -> dict:
    """Run one export to the target dir + rotate old backups."""
    from . import settings_export
    try:
        from ..main import VERSION
    except Exception:
        VERSION = "unknown"
    target = Path(cfg.get("target_dir") or "") if cfg.get("target_dir") else _default_target()
    target.mkdir(parents=True, exist_ok=True)
    name = f"jtdt-settings-{time.strftime('%Y%m%d-%H%M%S')}-v{VERSION}.zip"
    out_path = target / name
    result = settings_export.export_to_zip(
        out_path, cfg.get("categories"), app_version=VERSION)
    _rotate(target, int(cfg.get("keep", 14)))
    return {"file": str(out_path), "file_count": result["file_count"],
            "bytes": result["total_bytes"]}


def _rotate(target: Path, keep: int) -> None:
    """Keep only the newest `keep` jtdt-settings-*.zip in target."""
    try:
        backups = sorted(
            [p for p in target.glob("jtdt-settings-*.zip") if p.is_file()],
            key=lambda p: p.stat().st_mtime, reverse=True)
        for p in backups[keep:]:
            try:
                p.unlink()
            except OSError:
                pass
    except Exception:
        logger.exception("scheduled_export rotate failed")


def run_export_now() -> dict:
    """Force one export immediately (admin 'test' button). Updates last_run."""
    cfg = get_settings()
    res = _do_export(cfg)
    cur = get_settings()
    cur_raw = {k: cur[k] for k in _DEFAULTS}
    cur_raw["last_run"] = time.time()
    cur_raw["last_result"] = f"OK: {res['file_count']} 檔 / {res['bytes']/1024:.0f} KB"
    _write(cur_raw)
    return res


# ---------- background scheduler ----------

_SCHED_THREAD: threading.Thread | None = None
_SCHED_STOP = threading.Event()


def start_scheduler() -> None:
    global _SCHED_THREAD
    with _LOCK:
        if _SCHED_THREAD is not None and _SCHED_THREAD.is_alive():
            return
        _SCHED_STOP.clear()
        _SCHED_THREAD = threading.Thread(
            target=_loop, name="scheduled-export", daemon=True)
        _SCHED_THREAD.start()


def stop_scheduler() -> None:
    _SCHED_STOP.set()
    if _SCHED_THREAD is not None:
        _SCHED_THREAD.join(timeout=5)


def _due(cfg: dict, now: float) -> bool:
    if not cfg.get("enabled"):
        return False
    period = _INTERVAL_SECONDS.get(cfg.get("interval", "daily"), 86400)
    return (now - float(cfg.get("last_run") or 0.0)) >= period


def _loop() -> None:
    # Small initial delay so startup isn't slowed by a first export.
    if _SCHED_STOP.wait(60):
        return
    while not _SCHED_STOP.is_set():
        try:
            cfg = get_settings()
            if _due(cfg, time.time()):
                res = _do_export(cfg)
                cur = {k: cfg[k] for k in _DEFAULTS}
                cur["last_run"] = time.time()
                cur["last_result"] = f"OK: {res['file_count']} 檔"
                _write(cur)
                logger.info("scheduled settings export → %s", res["file"])
        except Exception:
            logger.exception("scheduled export failed")
            try:
                cur = {k: get_settings()[k] for k in _DEFAULTS}
                cur["last_result"] = "FAILED（見 server log）"
                _write(cur)
            except Exception:
                pass
        if _SCHED_STOP.wait(_CHECK_EVERY):
            break

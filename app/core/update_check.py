"""定期檢查新版（預設關閉）。

* 設定與狀態存在 `<資料目錄>/update_check.json`：`enabled`、`last_check_at`、`last_ok`、`last_error`、
  `latest`、`notified_version`。
* 打開之後背景每天檢查一次（啟動後 2 分鐘先看一次是否到期，之後每小時看一次）；只讀 GitHub 上
  `pyproject.toml` 的版本號，送出去的只有 User-Agent 裡的目前版本，不帶主機或使用者資訊。
* 發現新版時，用各管理員自己設定的通知管道（Email / Telegram / Zulip…）通知一次；同一版不重複寄
  （`notified_version`）。連不上 GitHub 只記在狀態裡，不打擾任何人。
* 網頁上的提示（首頁提示條、側欄版本號旁的小標籤）只給管理員（單機模式＝開網頁的人）；
  「這一版不再提醒」記在各管理員自己的偏好裡（`update_dismissed`）。
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

logger = logging.getLogger(__name__)

URL = "https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/pyproject.toml"
CHANGELOG_URL = "https://github.com/jasoncheng7115/jt-doc-tools/blob/main/CHANGELOG{suffix}.md"
INTERVAL = 86400            # 每天一次
_POLL = 3600                # 每小時看一次到期了沒
_FIRST_DELAY = 120          # 啟動後先等 2 分鐘（不拖慢啟動）

_LOCK = threading.RLock()
_CACHE: dict[str, Any] = {"data": None, "mtime": None}
_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()

_DEFAULTS = {"enabled": False, "last_check_at": 0.0, "last_ok": None, "last_error": "", "latest": "",
             "notified_version": ""}


def _path():
    from ..config import settings
    return settings.data_dir / "update_check.json"


def current_version() -> str:
    from ..main import VERSION
    return VERSION


def _vt(v: str) -> tuple:
    try:
        return tuple(int(x) for x in str(v).split(".")[:3])
    except (TypeError, ValueError):
        return ()


def is_newer(latest: str, current: str) -> bool:
    a, b = _vt(latest), _vt(current)
    return bool(a and b and a > b)


def get() -> dict:
    """目前的設定與狀態（讀檔有快取；檔案壞了當成預設值）。"""
    import json
    p = _path()
    with _LOCK:
        try:
            mt = p.stat().st_mtime_ns
        except OSError:
            return dict(_DEFAULTS)
        if _CACHE["mtime"] == mt and _CACHE["data"] is not None:
            return dict(_CACHE["data"])
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(d, dict):
                raise ValueError
        except (OSError, ValueError):
            d = {}
        out = {**_DEFAULTS, **{k: d[k] for k in _DEFAULTS if k in d}}
        out["enabled"] = bool(out["enabled"])
        _CACHE.update(data=out, mtime=mt)
        return dict(out)


def _save(d: dict) -> None:
    from .atomic_json import write_json
    with _LOCK:
        write_json(_path(), {k: d.get(k, v) for k, v in _DEFAULTS.items()})
        _CACHE.update(data=None, mtime=None)


def set_enabled(enabled: bool) -> dict:
    if not isinstance(enabled, bool):
        raise ValueError("enabled 要是 true / false")
    with _LOCK:
        d = get()
        d["enabled"] = enabled
        _save(d)
    if enabled:
        start_scheduler()
    return status()


def fetch_latest(timeout: float = 8) -> str:
    """從 GitHub 讀最新版的版本號；失敗丟 RuntimeError（訊息是給人看的固定句子）。"""
    from . import safe_fetch
    req = urllib.request.Request(URL, headers={"User-Agent": f"jt-doc-tools/{current_version()} (version check)"})
    try:
        with safe_fetch.urlopen(req, timeout=timeout) as resp:
            content = resp.read(200_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub 回應 HTTP {e.code}")
    except Exception:  # noqa: BLE001 —— 網路 / DNS / 逾時，細節進日誌
        logger.info("update check: cannot reach GitHub", exc_info=True)
        raise RuntimeError("無法連到 GitHub（網路 / DNS 問題）")
    m = re.search(r'^\s*version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', content, re.M)
    if not m:
        raise RuntimeError("解析版本失敗（找不到 version 欄位）")
    return m.group(1)


def check_now(*, notify: bool = True) -> dict:
    """立刻檢查一次並記下結果；有新版而且這一版還沒通知過 → 通知管理員（背景執行緒）。"""
    now = time.time()
    try:
        latest = fetch_latest()
        ok, err = True, ""
    except RuntimeError as e:
        latest, ok, err = "", False, str(e)
    with _LOCK:
        d = get()
        d.update(last_check_at=now, last_ok=ok, last_error=err)
        if ok:
            d["latest"] = latest
        send = bool(ok and notify and is_newer(latest, current_version()) and d.get("notified_version") != latest)
        if send:
            d["notified_version"] = latest
        _save(d)
    if send:
        _spawn(_notify_admins, latest)
    return status()


def _spawn(fn, *args) -> None:
    """通知在背景送（寄信可能要好幾秒，不擋住檢查）。"""
    threading.Thread(target=fn, args=args, daemon=True, name="update-notify").start()


def status() -> dict:
    d = get()
    cur = current_version()
    latest = d.get("latest") or ""
    return {"enabled": d["enabled"], "current": cur, "latest": latest,
            "is_outdated": is_newer(latest, cur), "last_check_at": d.get("last_check_at") or 0,
            "last_ok": d.get("last_ok"), "last_error": d.get("last_error") or "",
            "changelog_url": CHANGELOG_URL.format(suffix="")}


# --------------------------------------------------------------------------- 給畫面用

def _user(request) -> Optional[dict]:
    """登入的使用者：頁面上中介層會放在 `request.state.user`；`/api/…` 沒有放，從 session cookie 查。"""
    u = getattr(getattr(request, "state", None), "user", None)
    if u:
        return u
    from . import sessions
    tok = request.cookies.get(sessions.COOKIE_NAME, "")
    return sessions.lookup(tok) if tok else None


def viewer_is_admin(request) -> bool:
    """管理員才看得到 / 關得掉提示；單機模式（沒有啟用登入）＝開網頁的人。"""
    from . import auth_settings, permissions
    if not auth_settings.is_enabled():
        return True
    u = _user(request)
    if not u:
        return False
    if u.get("source") == "off":
        return True
    try:
        return bool(permissions.is_admin(int(u.get("user_id") or 0)))
    except Exception:  # noqa: BLE001
        return False


def _pref_key(request) -> str:
    """這個人的偏好儲存鍵（跟通知偏好、工作區同一把）；單機模式是共用的那一把。"""
    from . import auth_settings, workspace
    u = _user(request) if auth_settings.is_enabled() else None
    uid = u.get("user_id") if u and u.get("source") != "off" else None
    return workspace.key_for_user_id(uid)


def notice_for(request) -> Optional[dict]:
    """這個人要不要看到「有新版」：只給管理員（單機模式＝開網頁的人）、而且確實比目前版本新。
    回 `{"current", "latest", "dismissed", "changelog_url", "update_cmd"}`；不該看到回 None。"""
    try:
        if not viewer_is_admin(request):
            return None
        d = get()
        latest, cur = d.get("latest") or "", current_version()
        if not is_newer(latest, cur):
            return None
        from . import notify_settings
        dismissed = (notify_settings.get_prefs(_pref_key(request)).get("update_dismissed") or "") == latest
        return {"current": cur, "latest": latest, "dismissed": dismissed,
                "changelog_url": changelog_url_for(request), "update_cmd": update_command()}
    except Exception:  # noqa: BLE001 —— 提示是附加的，壞了不可以讓頁面壞
        logger.debug("update notice failed", exc_info=True)
        return None


def dismiss(request, version: str) -> None:
    from . import notify_settings
    version = str(version or "")
    if len(version) > 32 or not re.fullmatch(r"[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}", version):
        raise ValueError("version 格式不正確")
    notify_settings.save_prefs(_pref_key(request), {"update_dismissed": version})


def changelog_url_for(request) -> str:
    try:
        from . import ui_locale
        loc = ui_locale.resolve(request)
    except Exception:  # noqa: BLE001
        loc = "zh-Hant"
    suffix = {"en": "_en", "ja": "_ja"}.get(loc, "")
    return CHANGELOG_URL.format(suffix=suffix)


def update_command() -> dict:
    """這台主機的更新方式（給「怎麼更新」看）。"""
    import platform
    if platform.system() == "Windows":
        return {"os": "windows", "cmd": "jtdt update", "how": "以系統管理員身分開 PowerShell 執行"}
    return {"os": platform.system().lower(), "cmd": "sudo jtdt update", "how": "在主機上執行"}


# --------------------------------------------------------------------------- 通知管理員

def _admin_ids() -> list[int]:
    from . import auth_settings, permissions, user_manager
    if not auth_settings.is_enabled():
        return []
    out = []
    for u in user_manager.list_users():
        try:
            if u.get("enabled", True) and permissions.is_admin(int(u["id"])):
                out.append(int(u["id"]))
        except Exception:  # noqa: BLE001
            continue
    return out


def _notify_admins(latest: str) -> None:
    from . import auth_settings, notify_channels, notify_settings, workspace
    cur = current_version()
    subject = f"jt-doc-tools 有新版 v{latest} 可以更新"
    base = (notify_settings.get().get("site_url") or "").strip().rstrip("/")
    cmd = update_command()
    lines = [subject, f"目前版本：v{cur}", f"更新內容：{CHANGELOG_URL.format(suffix='')}",
             f"更新方式：{cmd['how']} {cmd['cmd']}（更新前先確認沒有作業在跑）"]
    if base:
        lines.append(f"相依套件頁：{base}/admin/sys-deps")
    text = "\n".join(lines)
    keys = ([workspace.key_for_user_id(uid) for uid in _admin_ids()] if auth_settings.is_enabled()
            else [workspace.key_for_user_id(None)])
    for key in keys:
        try:
            channels, merged = notify_settings.resolve_for_user(key)
            if not channels:
                continue
            failed = {c: e for c, e in notify_channels.broadcast(merged, channels, subject, text).items() if e}
            if failed:
                logger.warning("新版通知部分失敗：%s", sorted(failed))
        except Exception:  # noqa: BLE001
            logger.warning("新版通知失敗", exc_info=True)


# --------------------------------------------------------------------------- 背景排程

def _due(now: float) -> bool:
    d = get()
    return bool(d["enabled"]) and (now - float(d.get("last_check_at") or 0)) >= INTERVAL


def _loop() -> None:
    if _STOP.wait(_FIRST_DELAY):
        return
    while not _STOP.is_set():
        try:
            if _due(time.time()):
                check_now()
        except Exception:  # noqa: BLE001
            logger.warning("定期檢查新版失敗", exc_info=True)
        if _STOP.wait(_POLL):
            return


def start_scheduler() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, name="update-check", daemon=True)
        _THREAD.start()


def stop_scheduler() -> None:
    _STOP.set()

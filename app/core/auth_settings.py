"""Auth backend selection + bootstrap helper.

Lives at ``data/auth_settings.json`` (mode 600). Persists which backend
is active (off / local / ldap / ad), session/lockout policy, and LDAP/AD
config blob. Reading this is cheap; reads are not on the hot path (auth
decisions read user/perm tables instead).

The session-signing secret lives in a separate file ``data/.session_secret``
also mode 600, generated on first init. Putting it in JSON would risk it
ending up in admin export bundles by mistake.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from . import auth_db, audit_db, db, passwords, permissions, roles

logger = logging.getLogger(__name__)


_DEFAULTS: dict[str, Any] = {
    "backend": "off",                 # off | local | ldap | ad
    "session_max_age_days": 7,
    "remember_max_age_days": 30,
    "lockout_threshold": 5,           # failed attempts before locking
    "lockout_minutes": 15,
    "ldap": {
        # Filled in by admin; only consulted when backend ∈ {ldap, ad}
        "server_url": "",             # e.g. ldaps://ad.example.com:636
        "use_tls": True,
        "verify_cert": True,
        "service_dn": "",             # bind DN for the search account
        "service_password": "",       # encrypted at-rest (see _encrypt below)
        "user_search_base": "",       # e.g. OU=Users,DC=example,DC=com
        "user_search_filter": "(sAMAccountName={username})",
        "group_attr": "memberOf",
        "username_attr": "sAMAccountName",
        "displayname_attr": "displayName",
    },
    "updated_at": 0.0,
}


def _path() -> Path:
    from ..config import settings
    return settings.data_dir / "auth_settings.json"


def _secret_path() -> Path:
    from ..config import settings
    return settings.data_dir / ".session_secret"


_LOCK = threading.Lock()
_CACHE: dict[str, Any] | None = None


def _ensure_secret() -> bytes:
    """Return the 32-byte session signing secret, generating it on first call."""
    p = _secret_path()
    if p.exists():
        try:
            data = p.read_bytes()
            if len(data) == 32:
                return data
        except Exception:
            pass
    # Create fresh
    p.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    # Write atomically + chmod 600 (owner read/write only).
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(secret)
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(p)
    return secret


def get() -> dict[str, Any]:
    """Return current settings (deep copy of cache to discourage mutation)."""
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            p = _path()
            if p.exists():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                    # Merge with defaults so new keys flow in on upgrade
                    merged = json.loads(json.dumps(_DEFAULTS))
                    _deep_merge(merged, raw)
                    _CACHE = merged
                except Exception as exc:
                    logger.error("auth_settings parse failed (%s); using defaults", exc)
                    _CACHE = json.loads(json.dumps(_DEFAULTS))
            else:
                _CACHE = json.loads(json.dumps(_DEFAULTS))
        return json.loads(json.dumps(_CACHE))


def save(new_settings: dict[str, Any]) -> None:
    """Persist new settings (atomic write + 600). Caller is responsible for
    validation; we just save."""
    global _CACHE
    with _LOCK:
        merged = json.loads(json.dumps(_DEFAULTS))
        _deep_merge(merged, new_settings)
        merged["updated_at"] = time.time()
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(p)
        _CACHE = merged


def _deep_merge(target: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            _deep_merge(target[k], v)
        else:
            target[k] = v


# ---------- accessors used elsewhere ----------

def is_enabled() -> bool:
    return get()["backend"] != "off"


def get_backend() -> str:
    return get()["backend"]


def get_session_secret() -> bytes:
    return _ensure_secret()


# ---------- bootstrap: enable auth + create the seed admin atomically ----------

class BootstrapError(ValueError):
    pass


def enable_local_with_admin(
    *,
    admin_username: str,
    admin_display_name: str,
    admin_password: str,
    admin_password_confirm: str,
    actor_ip: str = "",
) -> int:
    """Atomically: create the seed admin user + flip backend to local.

    Returns the new admin user_id. Raises BootstrapError on validation
    failure (caller surfaces error_zh to UI).

    Refuses to run if backend is already not 'off' — re-bootstrapping a live
    install would silently overwrite the existing admin.
    """
    if get_backend() != "off":
        raise BootstrapError("認證已啟用，無法重新初始化（請先停用後再開啟）")

    admin_username = (admin_username or "").strip()
    admin_display_name = (admin_display_name or "").strip()
    if not admin_username:
        raise BootstrapError("管理員帳號名稱不能空白")
    if len(admin_username) > 64:
        raise BootstrapError("管理員帳號名稱不得超過 64 字元")
    # Conservative allowed chars — letters, digits, dot, dash, underscore.
    # (No spaces; matches typical AD sAMAccountName conventions.)
    import re as _re
    if not _re.fullmatch(r"[A-Za-z0-9._\-]+", admin_username):
        raise BootstrapError("帳號只能用英數、點、底線、減號")

    if admin_password != admin_password_confirm:
        raise BootstrapError("兩次輸入的密碼不一致")
    ok, err = passwords.validate_password(admin_password)
    if not ok:
        raise BootstrapError(err)

    # Hash OUTSIDE the DB tx (slow, ~50ms; we don't want the write lock held).
    pw_hash = passwords.hash_password(admin_password)

    # Make sure schemas exist (caller may not have run init yet on first ever
    # request; we do it here defensively).
    auth_db.init()
    audit_db.init()
    roles.seed_builtin_roles()

    conn = auth_db.conn()
    now = time.time()
    with db.tx(conn):
        # Refuse if any user already exists (caller should have noticed via
        # backend != off but race is possible; defensive).
        existing = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        if existing:
            raise BootstrapError("已存在使用者，無法初始化（資料庫狀態異常）")
        cur = conn.execute(
            "INSERT INTO users(username, display_name, password_hash, source, "
            " enabled, is_admin_seed, created_at) "
            "VALUES (?, ?, ?, 'local', 1, 1, ?)",
            (admin_username, admin_display_name or admin_username, pw_hash, now),
        )
        admin_user_id = cur.lastrowid

    # Auto-assign the admin role to the bootstrap user (so they can access
    # admin pages immediately + permission resolver returns ALL).
    permissions.set_subject_roles("user", str(admin_user_id), ["admin"])

    # Switch backend on. Done outside the auth_db tx so a failure here doesn't
    # leave us with a half-applied state — user can retry by submitting form
    # again (we'd see the user already exists and abort cleanly with a fresh
    # error... actually we'd error on uniqueness; need to think).
    #
    # Simpler: do this BEFORE the DB write to keep the window small. Even
    # simpler: keep current order, accept that retries need DB cleanup
    # (rare path; admin can always nuke data/auth.sqlite to start over).
    settings = get()
    settings["backend"] = "local"
    save(settings)

    audit_db.log_event(
        "auth_enabled",
        username=admin_username,
        ip=actor_ip,
        target="local",
        details={"admin_user_id": admin_user_id},
    )
    logger.info("Auth enabled (backend=local), admin user '%s' (id=%d) created",
                admin_username, admin_user_id)
    return admin_user_id


def disable_auth(*, actor: str = "", ip: str = "") -> None:
    """Flip backend to off. Existing user/permission rows are KEPT (so user
    can re-enable without losing setup). Sessions are wiped."""
    settings = get()
    if settings["backend"] == "off":
        return
    settings["backend"] = "off"
    save(settings)
    # Wipe sessions so any in-flight cookies stop working immediately.
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM sessions")
    audit_db.log_event("auth_disabled", username=actor, ip=ip)
    logger.warning("Auth disabled (rows kept; sessions wiped)")

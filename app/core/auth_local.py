"""Local-mode authentication: username + password against the auth DB.

Lockout strategy:
- Per-username row in `lockouts` (key='user:<username>')
- Per-IP row (key='ip:<addr>')
- Failed attempt increments BOTH counters
- Either reaching threshold locks for `lockout_minutes`
- Successful login clears BOTH

Per-IP lockout protects against credential stuffing across many users from
one source. Per-user lockout protects an individual account from being brute
forced from many distributed sources.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from . import audit_db, auth_db, auth_settings, db, passwords

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Login failure that should be shown to the user verbatim."""


def _check_lockout(conn, key: str, threshold: int) -> Optional[float]:
    row = conn.execute("SELECT failed_count, locked_until FROM lockouts WHERE key=?",
                       (key,)).fetchone()
    if row and row["locked_until"] > time.time():
        return row["locked_until"]
    return None


def _record_fail(conn, key: str, threshold: int, lockout_minutes: int) -> None:
    now = time.time()
    row = conn.execute("SELECT failed_count, locked_until FROM lockouts WHERE key=?",
                       (key,)).fetchone()
    if row:
        new_count = row["failed_count"] + 1
        locked_until = row["locked_until"]
        if new_count >= threshold:
            locked_until = now + lockout_minutes * 60
            new_count = 0   # reset counter once locked
        conn.execute(
            "UPDATE lockouts SET failed_count=?, locked_until=?, last_failed_at=? "
            "WHERE key=?",
            (new_count, locked_until, now, key),
        )
    else:
        new_count = 1
        locked_until = now + lockout_minutes * 60 if new_count >= threshold else 0.0
        conn.execute(
            "INSERT INTO lockouts(key, failed_count, locked_until, last_failed_at) "
            "VALUES (?,?,?,?)",
            (key, 0 if locked_until else new_count, locked_until, now),
        )


def _clear_lockout(conn, keys: list[str]) -> None:
    for k in keys:
        conn.execute("DELETE FROM lockouts WHERE key=?", (k,))


def authenticate(username: str, password: str, *, ip: str = "") -> dict:
    """Verify credentials, return user dict on success.

    Raises ``AuthError`` with a user-facing 中文 message on any failure
    (locked out, wrong password, unknown user, disabled account). The error
    message must be the SAME for every failure mode that an attacker could
    distinguish — only "locked out" is allowed to differ (giving the user
    actionable info).
    """
    s = auth_settings.get()
    threshold = int(s.get("lockout_threshold", 5))
    lockout_minutes = int(s.get("lockout_minutes", 15))

    user_key = f"user:{(username or '').lower().strip()}"
    ip_key = f"ip:{ip or ''}"

    conn = auth_db.conn()

    # ---- pre-flight lockout check (don't even try the password) ----
    locked_user_until = _check_lockout(conn, user_key, threshold)
    locked_ip_until = _check_lockout(conn, ip_key, threshold)
    locked_until = max(filter(None, [locked_user_until, locked_ip_until]),
                       default=None)
    if locked_until:
        secs = int(locked_until - time.time())
        mins = max(1, (secs + 59) // 60)
        audit_db.log_event(
            "login_locked",
            username=username, ip=ip, target=username,
            details={"remaining_seconds": secs},
        )
        raise AuthError(f"嘗試次數過多，請於 {mins} 分鐘後再試")

    # ---- look up user (still in time-uniform path) ----
    row = conn.execute(
        "SELECT id, username, display_name, password_hash, source, enabled "
        "FROM users WHERE username = ? AND source='local'",
        ((username or "").strip(),),
    ).fetchone()

    # Verify password — we still call verify_password even if user is None
    # so timing is uniform between "user not found" and "wrong password".
    pw_hash = row["password_hash"] if row else None
    ok = passwords.verify_password(password or "", pw_hash)

    if not ok or row is None:
        with db.tx(conn):
            _record_fail(conn, user_key, threshold, lockout_minutes)
            _record_fail(conn, ip_key, threshold, lockout_minutes)
        audit_db.log_event(
            "login_fail",
            username=username, ip=ip, target=username,
            details={"reason": "bad_credentials"},
        )
        raise AuthError("帳號或密碼錯誤")

    if not row["enabled"]:
        # Don't increment lockout — this isn't a brute force attempt; it's
        # a legitimately known username with the right password but
        # admin-disabled. Still log it.
        audit_db.log_event(
            "login_fail",
            username=username, ip=ip, target=username,
            details={"reason": "disabled"},
        )
        raise AuthError("帳號已停用，請聯絡管理員")

    # Success path — clear lockouts + bump last_login_at + audit.
    with db.tx(conn):
        _clear_lockout(conn, [user_key, ip_key])
        conn.execute("UPDATE users SET last_login_at=? WHERE id=?",
                     (time.time(), row["id"]))
    audit_db.log_event(
        "login_success",
        username=row["username"], ip=ip, target=row["username"],
        details={"source": row["source"]},
    )
    return {
        "user_id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "source": row["source"],
    }

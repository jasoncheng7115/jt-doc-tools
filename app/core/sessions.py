"""Session token issue / verify / revoke.

Cookie value = 256-bit random token (`secrets.token_urlsafe(32)`). The DB
stores ``sha256(token)`` so a DB breach can't directly resume sessions.

Cookie attributes (set by the route layer, not here):
    HttpOnly = True              prevent JS access
    SameSite = Lax               default; CSRF protection on top-level POSTs
    Secure   = (request scheme == 'https' or X-Forwarded-Proto == 'https')

Lifetime: 7 days default, 30 days when "remember me" checked.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Optional

from . import auth_db, auth_settings, db

logger = logging.getLogger(__name__)


COOKIE_NAME = "jtdt_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(user_id: int, *, remember: bool, ip: str = "", ua: str = "") -> tuple[str, float]:
    """Create a new session row, return (raw_token, expires_at)."""
    s = auth_settings.get()
    days = s["remember_max_age_days"] if remember else s["session_max_age_days"]
    now = time.time()
    expires_at = now + days * 86400
    raw = secrets.token_urlsafe(32)   # 256 bits of entropy
    th = _hash(raw)
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at, "
            "remember, ip, user_agent) VALUES (?,?,?,?,?,?,?)",
            (th, user_id, now, expires_at, 1 if remember else 0,
             (ip or "")[:64], (ua or "")[:256]),
        )
    return raw, expires_at


def lookup(raw_token: str) -> Optional[dict]:
    """Return user dict if session valid, None otherwise. Touches expires_at
    purely on read so we don't extend lifetime sliding-window style — sessions
    have a fixed expiry from issue time (simpler reasoning, easier audit)."""
    if not raw_token:
        return None
    th = _hash(raw_token)
    conn = auth_db.conn()
    row = conn.execute(
        "SELECT s.user_id, s.expires_at, u.username, u.display_name, "
        "       u.source, u.enabled, u.is_admin_seed "
        "FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = ?",
        (th,),
    ).fetchone()
    if row is None:
        return None
    if row["expires_at"] < time.time():
        # Expired — clean up opportunistically.
        revoke(raw_token)
        return None
    if not row["enabled"]:
        # Account disabled while session was alive — drop the session too.
        revoke(raw_token)
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "source": row["source"],
        "is_admin_seed": bool(row["is_admin_seed"]),
    }


def user_label(user: Optional[dict]) -> str:
    """Format a session-user dict as `username@realm` for audit / history /
    UI display. Same name `jason` may exist in both `local` and `ldap`
    realms, so the realm suffix is essential to know who acted.

    Returns "" if user is None / lacks expected fields. Empty source
    falls back to plain username (back-compat for old session shapes /
    callers that pass partial dicts)."""
    if not user:
        return ""
    if isinstance(user, dict):
        username = user.get("username") or ""
        source = user.get("source") or ""
    else:
        username = getattr(user, "username", "") or ""
        source = getattr(user, "source", "") or ""
    if not username:
        return ""
    return f"{username}@{source}" if source else username


def revoke(raw_token: str) -> None:
    """Delete the session row matching this token (idempotent)."""
    if not raw_token:
        return
    th = _hash(raw_token)
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (th,))


def revoke_all_for_user(user_id: int) -> int:
    """Revoke every session belonging to a user (e.g. on password change /
    role change). Returns number of rows removed."""
    conn = auth_db.conn()
    with db.tx(conn):
        cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount


def cleanup_expired() -> int:
    """Drop any session past its expires_at. Called by retention sweep."""
    now = time.time()
    conn = auth_db.conn()
    with db.tx(conn):
        cur = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return cur.rowcount

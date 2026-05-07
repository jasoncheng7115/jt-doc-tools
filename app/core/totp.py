"""TOTP (RFC 6238) 2FA helpers — pyotp + qrcode wrappers.

Used by:
- `/me/2fa` setup / verify pages (self-service)
- Login flow: when user has totp_enabled OR totp_required, ask for 6-digit
  code after password OK.
- Admin force-enroll: setting totp_required=1 forces user to set up on
  next login.

Schema columns added by `_m6_totp_columns` migration:
- users.totp_secret  — 32-char base32 string (pyotp.random_base32()), or NULL
- users.totp_enabled — 0/1, has user completed setup (verified one code)?
- users.totp_required — 0/1, is TOTP mandatory (auditor role default 1)?
"""
from __future__ import annotations

import base64
import io
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def new_secret() -> str:
    """Generate a fresh 32-char base32 secret. Caller stores in
    users.totp_secret BUT keeps totp_enabled=0 until first verify."""
    import pyotp
    return pyotp.random_base32()


def provision_uri(secret: str, username: str, issuer: str) -> str:
    """Build the otpauth:// URI for QR code + manual entry."""
    import pyotp
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify_code(secret: str, code: str, *, window: int = 1) -> bool:
    """Verify a 6-digit code against a secret. window=1 allows ±30s clock
    skew (one step before/after current). Code can include spaces / dashes
    (we strip)."""
    if not secret or not code:
        return False
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != 6:
        return False
    try:
        import pyotp
        return pyotp.TOTP(secret).verify(code, valid_window=window)
    except Exception:
        return False


def qr_png_data_url(uri: str, *, box_size: int = 6, border: int = 2) -> str:
    """Render an otpauth URI to a `data:image/png;base64,...` URL — 直接
    塞到 <img src=...> 不需另外建 endpoint。Pillow 是現成依賴，qrcode
    用它 render PNG。"""
    try:
        import qrcode
        from qrcode.image.pil import PilImage
        img = qrcode.make(uri, image_factory=PilImage,
                          box_size=box_size, border=border)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/png;base64," + b64
    except Exception:
        logger.exception("qr_png_data_url failed")
        return ""


# ---- Per-user state read/write -------------------------------------------

def get_user_totp_state(user_id: int) -> dict:
    """Read TOTP columns for a user. Returns dict with keys
    `enabled` / `required` / `has_secret` (never the secret itself)."""
    from . import auth_db
    row = auth_db.conn().execute(
        "SELECT totp_secret, totp_enabled, totp_required FROM users WHERE id=?",
        (int(user_id),),
    ).fetchone()
    if not row:
        return {"enabled": False, "required": False, "has_secret": False}
    return {
        "enabled": bool(row["totp_enabled"]),
        "required": bool(row["totp_required"]),
        "has_secret": bool(row["totp_secret"]),
    }


def get_secret(user_id: int) -> Optional[str]:
    """Return the user's TOTP secret (for verify). Caller MUST treat as
    sensitive — never log / never expose to frontend."""
    from . import auth_db
    row = auth_db.conn().execute(
        "SELECT totp_secret FROM users WHERE id=?", (int(user_id),),
    ).fetchone()
    if not row:
        return None
    return row["totp_secret"]


def set_secret(user_id: int, secret: str) -> None:
    """Save / replace the TOTP secret. Resets totp_enabled to 0 so user
    must re-verify (avoids changing key without confirming new one works)."""
    from . import auth_db, db
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "UPDATE users SET totp_secret=?, totp_enabled=0 WHERE id=?",
            (secret, int(user_id)),
        )


def mark_enabled(user_id: int) -> None:
    """Flip totp_enabled=1 — only after a successful first-verify. From this
    point onwards, login requires the 6-digit code."""
    from . import auth_db, db
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "UPDATE users SET totp_enabled=1 WHERE id=?", (int(user_id),),
        )


def disable(user_id: int) -> None:
    """Wipe TOTP — user can self-disable IF role doesn't require it.
    Auditor role has totp_required=1 set when account is created; admin
    UI can lift that, this fn just clears the user's secret."""
    from . import auth_db, db
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "UPDATE users SET totp_secret=NULL, totp_enabled=0 WHERE id=?",
            (int(user_id),),
        )


def set_required(user_id: int, required: bool) -> None:
    """Admin-only: force a user to use TOTP. If they don't have a secret
    yet, they'll be redirected to /me/2fa on next login."""
    from . import auth_db, db
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute(
            "UPDATE users SET totp_required=? WHERE id=?",
            (1 if required else 0, int(user_id)),
        )

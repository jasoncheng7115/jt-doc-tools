"""Password hashing using stdlib scrypt — no extra dependency.

Output format: ``scrypt$N=<n>,r=<r>,p=<p>$<salt_b64>$<hash_b64>``

Parameters: N=2^16 (65536), r=8, p=1 — costs ~50ms on modern CPU. Strong
enough against GPU brute force; not so slow that login feels laggy.

When verifying, params come from the stored string so we can rotate cost
later without invalidating existing hashes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets


_DEFAULT_N = 2 ** 16  # 65536
_DEFAULT_R = 8
_DEFAULT_P = 1
_DKLEN = 32
_MAXMEM = 128 * 1024 * 1024   # 128 MB cap (default would be too small for N=2^17 etc.)


def hash_password(password: str,
                  n: int = _DEFAULT_N, r: int = _DEFAULT_R, p: int = _DEFAULT_P) -> str:
    if not isinstance(password, str):
        raise TypeError("password must be str")
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                       n=n, r=r, p=p, maxmem=_MAXMEM, dklen=_DKLEN)
    return (f"scrypt$N={n},r={r},p={p}$"
            f"{base64.b64encode(salt).decode('ascii')}$"
            f"{base64.b64encode(h).decode('ascii')}")


# Pre-computed dummy hash used when the looked-up user doesn't exist, so the
# attacker can't distinguish "user not found" from "wrong password" by timing.
# Generated once on import (the exact value doesn't matter — we just need it
# to take the same time to verify against as a real hash).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time verify ``password`` against the stored hash.

    Pass ``encoded=None`` (i.e. user didn't exist) to still spend roughly
    the same CPU as a real verify — defends against username enumeration
    via login timing.
    """
    if not isinstance(password, str):
        return False
    target = encoded if encoded else _DUMMY_HASH
    try:
        scheme, params, salt_b64, hash_b64 = target.split("$")
        if scheme != "scrypt":
            return False
        kv = dict(part.split("=") for part in params.split(","))
        n, r, p = int(kv["N"]), int(kv["r"]), int(kv["p"])
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                                n=n, r=r, p=p, maxmem=_MAXMEM, dklen=len(expected))
    except Exception:
        return False
    ok = hmac.compare_digest(actual, expected)
    # If we were verifying against the dummy (encoded was None), result is
    # always False — but we already paid the CPU cost.
    return ok and encoded is not None


# ---------- password policy ----------

MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128   # Prevent DoS via huge inputs (scrypt scales with input).


def validate_password(password: str) -> tuple[bool, str]:
    """Return (ok, error_zh). NIST 800-63B style — length-focused, no
    silly complexity rules. UI shows error verbatim."""
    if not isinstance(password, str):
        return False, "密碼必須是文字"
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"密碼長度至少 {MIN_PASSWORD_LEN} 個字元"
    if len(password) > MAX_PASSWORD_LEN:
        return False, f"密碼長度不得超過 {MAX_PASSWORD_LEN} 個字元"
    # Reject all-whitespace.
    if not password.strip():
        return False, "密碼不能全是空白"
    return True, ""

"""Path-safety helpers — reject path traversal in user-supplied filenames.

Used by the dozens of `/preview/{name}` / `/download/{filename}` endpoints
across tool routers. Centralised so the rule is uniform and reviewable.

Rule: filenames must be plain ASCII (alnum + `._-`), no slash / backslash /
NUL / dotdot. Anything else is rejected with HTTP 400.

Why not just `Path(name).name`? That strips path components but allows
unicode normalization tricks, percent-encoding from path params (FastAPI
already decodes), and on Windows things like `CON`/`NUL` reserved names.
A strict allowlist is simpler and we control all the producers — they
all generate names from `uuid4().hex` + a fixed suffix anyway.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

# 32-hex uuid + optional suffix(_p1, _filled, etc.) + extension.
# Allows our internal naming convention; rejects everything else.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]{1,255}$")
# Strict UUID4-hex check (32 lowercase hex). Use to bound upload_id path params.
UUID_HEX_RE = re.compile(r"^[a-f0-9]{32}$")


def is_safe_name(name: str) -> bool:
    """Pure boolean — does name pass our strict allowlist?"""
    if not name or len(name) > 255:
        return False
    if name in (".", ".."):
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    if ".." in name:
        return False
    return bool(_SAFE_NAME_RE.match(name))


def sanitize_filename(name: str) -> str:
    """Return name unchanged if safe, else raise HTTP 400."""
    if not is_safe_name(name):
        raise HTTPException(400, "invalid filename")
    return name


def safe_join(base: Path, name: str) -> Path:
    """Resolve `name` under `base`. Reject if result escapes base or fails
    the strict filename rule. Returns a fully-resolved Path."""
    safe = sanitize_filename(name)
    p = (base / safe).resolve()
    base_resolved = base.resolve()
    # Containment check — covers symlink/escape edge cases too
    try:
        p.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(400, "path escape blocked")
    return p


def is_uuid_hex(s: str) -> bool:
    """Check string is 32-char lowercase hex (our standard upload_id form)."""
    return bool(UUID_HEX_RE.match(s or ""))


def require_uuid_hex(s: str, field: str = "id") -> str:
    """Validate or raise HTTP 400. Returns the validated string."""
    if not is_uuid_hex(s):
        raise HTTPException(400, f"invalid {field}")
    return s

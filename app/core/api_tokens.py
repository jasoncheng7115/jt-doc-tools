"""API token management for external clients.

Stores tokens at ``data/api_tokens.json``. Each token is a random 256-bit
value encoded as base64url (43 chars, no padding) — long enough to resist
brute force without being unwieldy. Tokens include a human-readable label
and optional expiry so admins can rotate.

Security notes:
- Only the token value (full) is ever stored in the JSON; there's no
  hashing since this is a single-user self-hosted app. If concerns about
  disk theft arise, we can switch to HMAC-prefix lookup + hash storage.
- Tokens are matched case-sensitively in constant time via ``hmac.compare_digest``.
- ``check_token()`` always returns bool — never leaks timing.

Integration:
- ``/api/*``, ``/tools/*/submit``, ``/tools/*/preview`` check the
  ``Authorization: Bearer <token>`` header via the ``require_api_token``
  FastAPI dependency (see app.main.install_api_auth).
- Admin UI at /admin/api-tokens lists + generates + revokes.
- **Per user instruction**, one token is auto-generated on first startup
  and shown in the admin page. Also surfaced via env var JTDT_API_TOKEN
  when the admin page is visited (so scripts can pick it up).
"""
from __future__ import annotations

import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from ..config import settings


def generate_token() -> str:
    """Cryptographically random token, base64url, 43 chars."""
    return secrets.token_urlsafe(32)  # 32 bytes → 43 base64url chars


@dataclass
class ApiToken:
    token: str            # full token value (secret)
    label: str            # human-readable name: "built-in", "my-script", ...
    created_at: float
    last_used_at: float = 0.0
    expires_at: float = 0.0   # 0 = never expires
    enabled: bool = True
    # v1.1.0: when auth is on, the token "is" this user — calls are gated
    # by that user's effective perms. owner_user_id=None means legacy /
    # unowned (treated as no permission when auth on, so admin must set it).
    owner_user_id: Optional[int] = None

    def to_public(self) -> dict:
        """Safe-to-display dict — masks the token middle so UI can show a
        preview without leaking the whole thing to screenshots."""
        t = self.token
        masked = f"{t[:6]}…{t[-4:]}" if len(t) > 12 else "***"
        return {
            "label": self.label,
            "token_preview": masked,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
            "expires_at": self.expires_at,
            "enabled": self.enabled,
            "owner_user_id": self.owner_user_id,
        }


class ApiTokenManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._path: Path = settings.data_dir / "api_tokens.json"
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Auto-generate a "built-in" token on first run so scripts can
            # find it via admin UI / env var without manual setup.
            initial = ApiToken(
                token=generate_token(),
                label="built-in",
                created_at=time.time(),
            )
            self._write({
                "tokens": [asdict(initial)],
                "enforce": False,    # grace period: start disabled so existing UI still works
                "updated_at": time.time(),
            })

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"tokens": [], "enforce": False}

    def _write(self, data: dict) -> None:
        data["updated_at"] = time.time()
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---- Public API ----

    def list_tokens(self) -> list[dict]:
        """Safe list for display (masked tokens)."""
        with self._lock:
            return [ApiToken(**t).to_public() for t in self._read().get("tokens", [])]

    def list_full(self) -> list[dict]:
        """Full list including raw tokens — ONLY for admin UI that explicitly
        requests reveal-on-click. Never send to unauthed contexts."""
        with self._lock:
            return list(self._read().get("tokens", []))

    def is_enforced(self) -> bool:
        with self._lock:
            return bool(self._read().get("enforce"))

    def set_enforce(self, enabled: bool) -> None:
        with self._lock:
            data = self._read()
            data["enforce"] = bool(enabled)
            self._write(data)

    def create(self, label: str) -> ApiToken:
        with self._lock:
            data = self._read()
            tok = ApiToken(
                token=generate_token(),
                label=(label or "unnamed").strip()[:40] or "unnamed",
                created_at=time.time(),
            )
            data.setdefault("tokens", []).append(asdict(tok))
            self._write(data)
            return tok

    def revoke(self, token_preview_or_full: str) -> bool:
        """Delete a token. Accepts full token OR a masked preview from UI."""
        if not token_preview_or_full:
            return False
        with self._lock:
            data = self._read()
            before = len(data.get("tokens", []))
            data["tokens"] = [
                t for t in data.get("tokens", [])
                if t.get("token") != token_preview_or_full
                and ApiToken(**t).to_public()["token_preview"] != token_preview_or_full
            ]
            changed = len(data["tokens"]) != before
            if changed:
                self._write(data)
            return changed

    def check(self, presented: Optional[str]) -> bool:
        """Returns True iff ``presented`` matches any enabled non-expired
        token. Convenience wrapper around lookup()."""
        return self.lookup(presented) is not None

    def lookup(self, presented: Optional[str]) -> Optional[dict]:
        """Constant-time token lookup. Returns the matching token dict
        (with owner_user_id populated when set) or None."""
        if not presented:
            return None
        with self._lock:
            data = self._read()
            now = time.time()
            for t in data.get("tokens", []):
                if not t.get("enabled", True):
                    continue
                if t.get("expires_at") and now > t["expires_at"]:
                    continue
                if hmac.compare_digest(str(t.get("token", "")), presented):
                    t["last_used_at"] = now
                    try:
                        self._write(data)
                    except OSError:
                        pass
                    return dict(t)   # copy so caller can't mutate cache
            return None

    def assign_owner(self, token_preview_or_full: str,
                     owner_user_id: Optional[int]) -> bool:
        if not token_preview_or_full:
            return False
        with self._lock:
            data = self._read()
            changed = False
            for t in data.get("tokens", []):
                if (t.get("token") == token_preview_or_full
                        or ApiToken(**{k: v for k, v in t.items()
                                       if k in ApiToken.__dataclass_fields__
                                       }).to_public()["token_preview"] == token_preview_or_full):
                    t["owner_user_id"] = owner_user_id
                    changed = True
            if changed:
                self._write(data)
            return changed

    def first_token(self) -> Optional[str]:
        """Return the first enabled non-expired token (for convenience in
        the admin UI's 'your built-in token is: …' display). Returns None
        if no usable token exists."""
        with self._lock:
            data = self._read()
            now = time.time()
            for t in data.get("tokens", []):
                if not t.get("enabled", True):
                    continue
                if t.get("expires_at") and now > t["expires_at"]:
                    continue
                return str(t.get("token", ""))
            return None


api_tokens = ApiTokenManager()

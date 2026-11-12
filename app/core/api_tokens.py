"""API token management for external clients.

Stores tokens at ``data/api_tokens.json``. Each token is a random 256-bit
value encoded as base64url (43 chars, no padding) — long enough to resist
brute force without being unwieldy. Tokens include a human-readable label
and optional expiry so admins can rotate.

Security notes:
- **The token itself is never stored in clear text** (v1.16.78, CodeQL #208).
  Each entry keeps ``token_sha256`` (used for matching, so a request never
  needs to decrypt anything), ``token_enc`` (Fernet, the same key as the LLM
  and SSO secrets; decrypted only for the admin page's reveal / copy and the
  built-in token hint) and ``preview`` (first 6 + last 4, for listing and
  revoking). Files written by older versions (``"token": "<clear text>"``,
  including old settings backups restored while the service runs) are
  converted the first time they are read.
- If the key changes (``.session_secret`` replaced) tokens keep working —
  matching uses the hash — they just can no longer be revealed.
- Hashes are compared in constant time via ``hmac.compare_digest``.
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

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from . import atomic_json, secret_box
from ..config import settings


def generate_token() -> str:
    """Cryptographically random token, base64url, 43 chars."""
    return secrets.token_urlsafe(32)  # 32 bytes → 43 base64url chars


def _digest(token: str) -> str:
    """比對用的雜湊。Token 本身是 256 位元的亂數（不是人選的密碼），SHA-256 就夠，不必用慢雜湊。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _preview(token: str) -> str:
    return f"{token[:6]}…{token[-4:]}" if len(token) > 12 else "***"


#: 檔案裡每一筆的欄位（不含 Token 本身）
_STORED = ("token_sha256", "token_enc", "preview", "label", "created_at", "last_used_at",
           "expires_at", "enabled", "owner_user_id", "mcp", "stamp")


def _stored(tok: "ApiToken") -> dict:
    """記憶體裡的 ApiToken → 檔案裡的那一筆（雜湊 ＋ 密文 ＋ 遮罩，沒有原文）。"""
    row = {k: v for k, v in asdict(tok).items() if k != "token"}
    row.update(token_sha256=_digest(tok.token), token_enc=secret_box.encrypt(tok.token),
               preview=_preview(tok.token))
    return row


def _convert(data: dict) -> bool:
    """舊版的明文欄位（`"token": "…"`）換成雜湊 ＋ 密文；有換過回 True。"""
    changed = False
    for row in data.get("tokens") or []:
        if not isinstance(row, dict) or "token" not in row:
            continue
        plain = str(row.pop("token") or "")
        row.update(token_sha256=_digest(plain), token_enc=secret_box.encrypt(plain),
                   preview=_preview(plain))
        changed = True
    return changed


def _matches(row: dict, token_or_preview: str) -> bool:
    """完整 Token（比雜湊）或畫面上的遮罩（比 preview）。"""
    return (hmac.compare_digest(str(row.get("token_sha256") or ""), _digest(token_or_preview))
            or row.get("preview") == token_or_preview)


def encrypt_secret(plain: str) -> str:
    """給設定備份換機用（`settings_export._rekey_specs`）：匯入時用這一台的金鑰重新加密。"""
    return secret_box.encrypt(plain)


def decrypt_secret(enc: str) -> str:
    """給設定備份匯出用：解不開回空字串（Token 照樣能用，只是不能顯示原文）。"""
    return secret_box.decrypt(enc, label="API Token")


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
    # v1.17.1：這張 Token 能不能連 MCP（AI 客戶端）。預設不行，管理員在「設定 → MCP」逐張打開
    mcp: bool = False
    # v1.17.38：這張 Token 能不能經 MCP 用印（AI 用印）。預設不行；要先綁使用者
    stamp: bool = False

    def to_public(self) -> dict:
        """Safe-to-display dict — masks the token middle so UI can show a
        preview without leaking the whole thing to screenshots."""
        return _public(dict(asdict(self), preview=_preview(self.token)))


def _public(row: dict) -> dict:
    """檔案裡的一筆 → 可以顯示的欄位（只有遮罩，沒有原文）。"""
    return {
        "label": row.get("label"),
        "token_preview": row.get("preview") or "***",
        "created_at": row.get("created_at"),
        "last_used_at": row.get("last_used_at", 0.0),
        "expires_at": row.get("expires_at", 0.0),
        "enabled": row.get("enabled", True),
        "owner_user_id": row.get("owner_user_id"),
        "mcp": bool(row.get("mcp", False)),
        "stamp": bool(row.get("stamp", False)),
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
                "tokens": [_stored(initial)],
                "enforce": False,    # grace period: start disabled so existing UI still works
                "updated_at": time.time(),
            })
        with self._lock:
            self._read()             # 舊版的明文檔在這裡就轉過來
        self._tighten()

    def _tighten(self) -> None:
        """檔案只給服務帳號讀（0600）：裡面雖然沒有原文了，密文與雜湊也不該給別的帳號讀。

        原本寫檔時沒有指定權限，照 umask 變成 0644，同一台機器的其他帳號讀得到每一張 Token
        （2026-10-09 為合規說明逐項查證時發現）。舊安裝的檔案在這裡收緊一次；Windows 沒有這種權限位元，略過。"""
        if os.name != "posix":
            return
        try:
            if self._path.exists() and self._path.stat().st_mode & 0o077:
                os.chmod(self._path, 0o600)
        except OSError:
            pass

    def _read(self) -> dict:
        """讀檔；遇到舊版的明文欄位就當場轉成雜湊 ＋ 密文寫回去
        （設定備份匯入舊備份時，檔案是在服務跑著的時候被換掉的，所以每次讀都要看）。"""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"tokens": [], "enforce": False}
        if isinstance(data, dict) and _convert(data):
            try:
                self._write(data)
            except OSError:
                pass
        return data

    def _write(self, data: dict) -> None:
        data["updated_at"] = time.time()
        atomic_json.write_json(self._path, data, mode=0o600)

    # ---- Public API ----

    def list_tokens(self) -> list[dict]:
        """Safe list for display (masked tokens)."""
        with self._lock:
            return [_public(t) for t in self._read().get("tokens", [])]

    def find(self, token_or_preview: str) -> Optional[dict]:
        """完整 Token 或遮罩 → 可以顯示的那一筆（找不到回 None）。給管理頁寫稽核用（記名稱，不記 Token）。"""
        if not token_or_preview:
            return None
        with self._lock:
            for t in self._read().get("tokens", []):
                if _matches(t, token_or_preview):
                    return _public(t)
        return None

    def list_full(self) -> list[dict]:
        """Full list with each token decrypted into ``token`` — ONLY for the
        admin UI's reveal-on-click. Never send to unauthed contexts.
        ``token`` is ``""`` when it cannot be decrypted (key changed); the
        token still works, it just cannot be shown."""
        with self._lock:
            out = []
            for t in self._read().get("tokens", []):
                row = {k: t.get(k) for k in _STORED if k not in ("token_sha256", "token_enc")}
                row["token"] = decrypt_secret(str(t.get("token_enc") or ""))
                out.append(row)
            return out

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
            data.setdefault("tokens", []).append(_stored(tok))
            self._write(data)
            return tok

    def revoke(self, token_preview_or_full: str) -> bool:
        """Delete a token. Accepts full token OR a masked preview from UI."""
        if not token_preview_or_full:
            return False
        with self._lock:
            data = self._read()
            before = len(data.get("tokens", []))
            data["tokens"] = [t for t in data.get("tokens", [])
                              if not _matches(t, token_preview_or_full)]
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
        want = _digest(presented)
        with self._lock:
            data = self._read()
            now = time.time()
            for t in data.get("tokens", []):
                if not t.get("enabled", True):
                    continue
                if t.get("expires_at") and now > t["expires_at"]:
                    continue
                if hmac.compare_digest(str(t.get("token_sha256") or ""), want):
                    t["last_used_at"] = now
                    try:
                        self._write(data)
                    except OSError:
                        pass
                    # 呼叫端只需要標籤與擁有者；雜湊與密文不帶出去
                    return {k: v for k, v in t.items() if k not in ("token_sha256", "token_enc")}
            return None

    def assign_owner(self, token_preview_or_full: str,
                     owner_user_id: Optional[int]) -> bool:
        if not token_preview_or_full:
            return False
        with self._lock:
            data = self._read()
            changed = False
            for t in data.get("tokens", []):
                if _matches(t, token_preview_or_full):
                    if t.get("owner_user_id") != owner_user_id:
                        t["stamp"] = False      # 改綁給別人：用印權限不跟著轉過去，要重新允許
                    t["owner_user_id"] = owner_user_id
                    changed = True
            if changed:
                self._write(data)
            return changed

    def set_mcp(self, token_preview_or_full: str, allowed: bool) -> bool:
        """打開 / 關閉這張 Token 連 MCP 的權限（v1.17.1）。"""
        if not token_preview_or_full:
            return False
        with self._lock:
            data = self._read()
            changed = False
            for t in data.get("tokens", []):
                if _matches(t, token_preview_or_full):
                    t["mcp"] = bool(allowed)
                    changed = True
            if changed:
                self._write(data)
            return changed

    def set_stamp(self, token_preview_or_full: str, allowed: bool) -> bool:
        """打開 / 關閉這張 Token 經 MCP 用印（v1.17.38）。打開要先綁使用者（呼叫端檢查）。"""
        if not token_preview_or_full:
            return False
        with self._lock:
            data = self._read()
            changed = False
            for t in data.get("tokens", []):
                if _matches(t, token_preview_or_full):
                    t["stamp"] = bool(allowed)
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
                plain = decrypt_secret(str(t.get("token_enc") or ""))
                if plain:
                    return plain
            return None


api_tokens = ApiTokenManager()

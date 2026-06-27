"""Lightweight persistent store for SSO runtime state (SQLite, no migrations).

Two tables in ``data/sso_store.sqlite``:

* ``saml_replay`` — assertion IDs already consumed, with their NotOnOrAfter.
  python3-saml validates timestamps/signature but does NOT stop an attacker from
  replaying the same valid assertion within its (≈5 min) window; we reject any
  assertion ID seen before. Survives restarts (in-memory wouldn't).

* ``saml_session`` — maps our session token-hash → the IdP NameID + SessionIndex
  captured at login, so SP-initiated Single-Logout can build a proper
  LogoutRequest at logout time (the core sessions table has no slot for these).

Both are best-effort: failures are logged, never block login/logout.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import settings
from ..logging_setup import get_logger

logger = get_logger(__name__)

_LOCK = threading.Lock()
_CONN: Optional[sqlite3.Connection] = None


def _conn() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        p = Path(settings.data_dir) / "sso_store.sqlite"
        p.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(p), check_same_thread=False, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("CREATE TABLE IF NOT EXISTS saml_replay "
                  "(assertion_id TEXT PRIMARY KEY, expires REAL NOT NULL)")
        c.execute("CREATE TABLE IF NOT EXISTS saml_session "
                  "(token_hash TEXT PRIMARY KEY, name_id TEXT, session_index TEXT, "
                  "expires REAL NOT NULL)")
        # OIDC 進行中交易（state/nonce/next）改存伺服器端，cookie 只放隨機 txid →
        # cookie 不含使用者輸入（next），避免「cookie 用 user input」靜態分析告警。
        c.execute("CREATE TABLE IF NOT EXISTS oidc_tx "
                  "(txid TEXT PRIMARY KEY, state TEXT, nonce TEXT, next TEXT, "
                  "expires REAL NOT NULL)")
        _CONN = c
    return _CONN


def assertion_is_replay(assertion_id: str, not_on_or_after: Optional[float]) -> bool:
    """Atomically record the assertion ID; return True if it was ALREADY present
    (i.e. a replay → caller must reject). Empty id → treat as not-replay (the
    signature/timestamp checks remain the primary defence)."""
    if not assertion_id:
        return False
    exp = not_on_or_after or (time.time() + 600)
    try:
        with _LOCK:
            c = _conn()
            c.execute("DELETE FROM saml_replay WHERE expires < ?", (time.time(),))
            try:
                c.execute("INSERT INTO saml_replay(assertion_id, expires) VALUES (?,?)",
                          (assertion_id, exp))
                return False  # inserted = first time seen
            except sqlite3.IntegrityError:
                return True   # PK clash = already consumed = replay
    except Exception as e:
        logger.warning("saml replay store error (%s) — allowing (sig/ts still checked)", e)
        return False


def save_saml_session(token_hash: str, name_id: str, session_index: str,
                      expires: float) -> None:
    if not token_hash:
        return
    try:
        with _LOCK:
            c = _conn()
            c.execute("DELETE FROM saml_session WHERE expires < ?", (time.time(),))
            c.execute("INSERT OR REPLACE INTO saml_session"
                      "(token_hash, name_id, session_index, expires) VALUES (?,?,?,?)",
                      (token_hash, name_id or "", session_index or "", expires))
    except Exception as e:
        logger.warning("saml session store write failed: %s", e)


def pop_saml_session(token_hash: str) -> tuple[str, str]:
    """Return (name_id, session_index) for the token and delete the row."""
    if not token_hash:
        return "", ""
    try:
        with _LOCK:
            c = _conn()
            row = c.execute("SELECT name_id, session_index FROM saml_session "
                            "WHERE token_hash=?", (token_hash,)).fetchone()
            c.execute("DELETE FROM saml_session WHERE token_hash=?", (token_hash,))
            return (row[0], row[1]) if row else ("", "")
    except Exception as e:
        logger.warning("saml session store read failed: %s", e)
        return "", ""


def save_oidc_tx(txid: str, state: str, nonce: str, next_url: str,
                 expires: float) -> None:
    """存 OIDC 進行中交易（IdP 往返期間）。txid 是隨機不可猜的 cookie 值。"""
    if not txid:
        return
    try:
        with _LOCK:
            c = _conn()
            c.execute("DELETE FROM oidc_tx WHERE expires < ?", (time.time(),))
            c.execute("INSERT OR REPLACE INTO oidc_tx"
                      "(txid, state, nonce, next, expires) VALUES (?,?,?,?,?)",
                      (txid, state or "", nonce or "", next_url or "/", expires))
    except Exception as e:
        logger.warning("oidc tx store write failed: %s", e)


def pop_oidc_tx(txid: str) -> dict:
    """取出並刪除 OIDC 交易（一次性，防重放）。回 {state,nonce,next} 或 {}。"""
    if not txid:
        return {}
    try:
        with _LOCK:
            c = _conn()
            row = c.execute("SELECT state, nonce, next, expires FROM oidc_tx "
                            "WHERE txid=?", (txid,)).fetchone()
            c.execute("DELETE FROM oidc_tx WHERE txid=?", (txid,))
            if not row or row[3] < time.time():
                return {}
            return {"state": row[0], "nonce": row[1], "next": row[2]}
    except Exception as e:
        logger.warning("oidc tx store read failed: %s", e)
        return {}


def _reset_for_tests() -> None:
    global _CONN
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN = None

"""Tests for app.core.sessions (issue / lookup / revoke).

Test list:
  - issue creates a row, returns (token, expires_at)
  - lookup(token) returns user dict
  - lookup(unknown_token) returns None
  - lookup of expired session returns None and cleans the row
  - lookup of disabled-user's session returns None
  - revoke deletes the row
  - revoke_all_for_user wipes every session for that user
  - cleanup_expired sweeps old rows
  - DB stores sha256(token), not raw token (security)
"""
from __future__ import annotations

import hashlib
import time

import pytest

from app.core import auth_db, db, sessions


def _seed_user(username="alice"):
    """Insert a minimal local user, return user_id."""
    conn = auth_db.conn()
    with db.tx(conn):
        cur = conn.execute(
            "INSERT INTO users(username, display_name, source, enabled, created_at) "
            "VALUES (?, ?, 'local', 1, ?)",
            (username, username, time.time()),
        )
    return cur.lastrowid


def test_issue_returns_token_and_expiry(auth_off):
    uid = _seed_user()
    token, exp = sessions.issue(uid, remember=False, ip="1.2.3.4", ua="pytest")
    assert isinstance(token, str) and len(token) > 30
    assert exp > time.time() + 6 * 86400   # default 7 days


def test_lookup_returns_user(auth_off):
    uid = _seed_user("bob")
    token, _ = sessions.issue(uid, remember=False)
    cur = sessions.lookup(token)
    assert cur is not None
    assert cur["user_id"] == uid
    assert cur["username"] == "bob"


def test_lookup_unknown_token(auth_off):
    assert sessions.lookup("not-a-real-token") is None
    assert sessions.lookup("") is None


def test_lookup_expired(auth_off):
    uid = _seed_user("eve")
    token, _ = sessions.issue(uid, remember=False)
    # Force expiry
    th = hashlib.sha256(token.encode()).hexdigest()
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?",
                     (time.time() - 1, th))
    assert sessions.lookup(token) is None
    # And was cleaned up
    n = conn.execute("SELECT count(*) FROM sessions WHERE token_hash=?",
                     (th,)).fetchone()[0]
    assert n == 0


def test_lookup_disabled_user(auth_off):
    uid = _seed_user("dave")
    token, _ = sessions.issue(uid, remember=False)
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("UPDATE users SET enabled=0 WHERE id=?", (uid,))
    assert sessions.lookup(token) is None


def test_revoke(auth_off):
    uid = _seed_user("frank")
    token, _ = sessions.issue(uid, remember=False)
    assert sessions.lookup(token) is not None
    sessions.revoke(token)
    assert sessions.lookup(token) is None


def test_revoke_all_for_user(auth_off):
    uid = _seed_user("ginny")
    sessions.issue(uid, remember=False)
    sessions.issue(uid, remember=True)
    sessions.issue(uid, remember=False)
    n = sessions.revoke_all_for_user(uid)
    assert n == 3


def test_cleanup_expired(auth_off):
    uid = _seed_user("hank")
    t1, _ = sessions.issue(uid, remember=False)
    t2, _ = sessions.issue(uid, remember=False)
    # Force one expired
    th1 = hashlib.sha256(t1.encode()).hexdigest()
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("UPDATE sessions SET expires_at=? WHERE token_hash=?",
                     (time.time() - 1, th1))
    n = sessions.cleanup_expired()
    assert n == 1
    # Other still alive
    assert sessions.lookup(t2) is not None


def test_db_stores_hash_not_raw(auth_off):
    """Critical security property: a DB dump must NOT reveal cookie values."""
    uid = _seed_user("ivy")
    token, _ = sessions.issue(uid, remember=False)
    conn = auth_db.conn()
    rows = conn.execute("SELECT token_hash FROM sessions").fetchall()
    th = hashlib.sha256(token.encode()).hexdigest()
    assert any(r["token_hash"] == th for r in rows)
    # raw token must NOT appear anywhere
    for r in rows:
        assert r["token_hash"] != token

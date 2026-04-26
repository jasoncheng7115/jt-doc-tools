"""Tests for app.core.db (SQLite layer).

Test list:
  - get_conn returns a connection with WAL + foreign_keys + busy_timeout
  - Same path returns same connection within a thread (TLS cache)
  - Different threads get different connections
  - migrate() applies pending migrations idempotently
  - migrate() respects user_version (won't re-apply)
  - tx() rolls back on exception
  - tx() commits on clean exit
  - tx() raises if rollback called outside an active tx (sanity)
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from app.core import db as _db


def test_get_conn_pragmas(tmp_path):
    p = tmp_path / "x.sqlite"
    c = _db.get_conn(p)
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_get_conn_caches_per_path(tmp_path):
    p = tmp_path / "x.sqlite"
    c1 = _db.get_conn(p)
    c2 = _db.get_conn(p)
    assert c1 is c2


def test_get_conn_per_thread(tmp_path):
    p = tmp_path / "x.sqlite"
    main_conn = _db.get_conn(p)
    other_conn = []

    def worker():
        other_conn.append(_db.get_conn(p))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert other_conn[0] is not main_conn


def test_migrate_applies_in_order(tmp_path):
    p = tmp_path / "x.sqlite"
    calls: list[str] = []

    def m1(conn):
        conn.execute("CREATE TABLE a (x INTEGER)")
        calls.append("m1")

    def m2(conn):
        conn.execute("CREATE TABLE b (y INTEGER)")
        calls.append("m2")

    final = _db.migrate(p, [m1, m2])
    assert final == 2
    assert calls == ["m1", "m2"]
    c = _db.get_conn(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migrate_idempotent(tmp_path):
    p = tmp_path / "x.sqlite"
    counter = {"n": 0}

    def m1(conn):
        conn.execute("CREATE TABLE a (x INTEGER)")
        counter["n"] += 1

    _db.migrate(p, [m1])
    _db.migrate(p, [m1])  # second call should NOT re-apply
    assert counter["n"] == 1


def test_tx_rolls_back_on_exception(tmp_path):
    p = tmp_path / "x.sqlite"
    c = _db.get_conn(p)
    c.execute("CREATE TABLE t (x INTEGER)")
    with pytest.raises(RuntimeError):
        with _db.tx(c):
            c.execute("INSERT INTO t VALUES (1)")
            raise RuntimeError("oops")
    assert c.execute("SELECT count(*) FROM t").fetchone()[0] == 0


def test_tx_commits_on_clean_exit(tmp_path):
    p = tmp_path / "x.sqlite"
    c = _db.get_conn(p)
    c.execute("CREATE TABLE t (x INTEGER)")
    with _db.tx(c):
        c.execute("INSERT INTO t VALUES (42)")
    assert c.execute("SELECT x FROM t").fetchone()[0] == 42

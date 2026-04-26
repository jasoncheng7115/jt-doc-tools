"""Thin SQLite layer shared by auth & audit modules.

Conventions baked in:

- **WAL mode** + ``synchronous=NORMAL`` — multiple readers + single writer in
  parallel; reads never block on writes (the long-standing "database is
  locked" pain in vanilla SQLite goes away here).
- ``busy_timeout=5000`` — if two threads still race for the write lock,
  retry quietly for 5 s instead of failing immediately.
- ``foreign_keys=ON`` — by default SQLite ignores FK constraints; we want
  cascading deletes to actually work.
- Schema migrations keyed by ``PRAGMA user_version``: a simple version
  counter, each migration bumps it by one. No external migration tool.

Designed so that a future swap to PostgreSQL only needs to replace
``get_conn`` + the migration apply loop — call sites use plain DB-API.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger(__name__)


# ---------- connection management ----------
# A single connection per (db path × thread). SQLite connections are NOT
# safe to share across threads with check_same_thread=True (default), and
# WAL mode wants one connection per thread for best parallelism anyway.
_TLS = threading.local()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=5.0,         # blocked-on-lock retry up to 5s before raising
        isolation_level=None,  # autocommit; we manage tx via BEGIN/COMMIT in tx()
        check_same_thread=True,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn(path: Path) -> sqlite3.Connection:
    """Return a thread-local connection to ``path`` (opens lazily)."""
    cache: dict[str, sqlite3.Connection] = getattr(_TLS, "conns", None) or {}
    if not hasattr(_TLS, "conns"):
        _TLS.conns = cache
    key = str(path.resolve())
    conn = cache.get(key)
    if conn is None:
        conn = _connect(path)
        cache[key] = conn
    return conn


def close_thread_conns() -> None:
    """Close all connections held by the current thread (use during shutdown)."""
    cache = getattr(_TLS, "conns", None) or {}
    for c in cache.values():
        try:
            c.close()
        except Exception:
            pass
    cache.clear()


# ---------- short-tx context manager ----------

@contextmanager
def tx(conn: sqlite3.Connection):
    """Run a short write transaction. Use only around the actual writes —
    NEVER inside this block do IO / Pillow / network / anything slow.

    Usage::

        with tx(conn):
            conn.execute("INSERT ...")
            conn.execute("UPDATE ...")
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------- schema migration ----------

Migration = Callable[[sqlite3.Connection], None]


def migrate(path: Path, migrations: Iterable[Migration]) -> int:
    """Apply migrations in order, tracking progress via PRAGMA user_version.

    Each migration receives the connection and runs whatever DDL/DML it needs.
    Migration N is applied iff current user_version < N. The function then
    bumps user_version to its index.

    Note: we do NOT wrap migrations in our own `tx()` because they typically
    use ``executescript`` for DDL, which implicitly COMMITs whatever tx was
    open. If a migration needs a transaction for data-side work, it should
    use `tx()` internally.

    Returns the final user_version.
    """
    migrations = list(migrations)
    conn = get_conn(path)
    cur_version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i, fn in enumerate(migrations, start=1):
        if cur_version >= i:
            continue
        logger.info("DB %s: applying migration %d (%s)", path.name, i, fn.__name__)
        fn(conn)
        # PRAGMA can't be parameterized; user_version is safe (int constant).
        conn.execute(f"PRAGMA user_version={i}")
        cur_version = i
    return cur_version


# ---------- helpers ----------

def fetchone(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def fetchall(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def execute(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Cursor:
    return conn.execute(sql, params)


def db_size_bytes(path: Path) -> int:
    """File size of the main DB file (ignores WAL/SHM sidecars)."""
    try:
        return path.stat().st_size
    except OSError:
        return 0

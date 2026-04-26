"""Audit database schema + async write queue.

Stored at ``data/audit.sqlite``. Single table ``audit_events``.

**Concurrency model**: every audit write goes through a single background
writer thread that owns the only write connection. Caller does
``log_event(...)`` which puts a record on a queue and returns immediately —
no SQLite contention, no request blocked even under burst load.

Read paths (admin viewer, log forwarder bookmarking) open per-thread read
connections via ``db.get_conn`` — WAL mode lets them read in parallel with
the writer.
"""
from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import db as _db

logger = logging.getLogger(__name__)


# ---------- schema migrations ----------

def _m1_initial(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE audit_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ts              REAL NOT NULL,                  -- epoch seconds
        username        TEXT NOT NULL DEFAULT '',       -- '' for anonymous
        ip              TEXT NOT NULL DEFAULT '',
        event_type      TEXT NOT NULL,                  -- 'login_success' / 'tool_invoke' / ...
        target          TEXT NOT NULL DEFAULT '',       -- e.g. tool_id, user being modified
        details_json    TEXT NOT NULL DEFAULT '{}'      -- arbitrary JSON
    );
    CREATE INDEX idx_audit_ts ON audit_events(ts DESC);
    CREATE INDEX idx_audit_user ON audit_events(username, ts DESC);
    CREATE INDEX idx_audit_event ON audit_events(event_type, ts DESC);

    -- Bookmark for the log forwarder worker (max id already shipped to
    -- external destinations). One row per destination; here we keep a single
    -- row keyed 'forward' for the simple case.
    CREATE TABLE forward_state (
        key             TEXT PRIMARY KEY,
        last_forwarded_id INTEGER NOT NULL DEFAULT 0,
        updated_at      REAL NOT NULL DEFAULT 0
    );
    """)


MIGRATIONS = [_m1_initial]


def audit_db_path() -> Path:
    from ..config import settings
    return settings.data_dir / "audit.sqlite"


def init() -> None:
    path = audit_db_path()
    final = _db.migrate(path, MIGRATIONS)
    logger.info("audit DB ready at %s (schema v%d)", path, final)


def conn():
    return _db.get_conn(audit_db_path())


# ---------- async write queue ----------
# Background thread owns the single write connection. `log_event` is
# fire-and-forget; the queue is unbounded but in practice events are tiny
# (~200 bytes) and the writer can sustain >10k/s on slow disks.

_WRITE_Q: "queue.Queue[Optional[tuple]]" = queue.Queue()
_WRITER_THREAD: Optional[threading.Thread] = None
_WRITER_LOCK = threading.Lock()


def _writer_loop() -> None:
    """Drain `_WRITE_Q`, write to a dedicated connection. Sentinel = None."""
    path = audit_db_path()
    # Open a private connection (NOT via get_conn) — we want it pinned to
    # this thread alone with no thread-local sharing surprises.
    write_conn = sqlite3.connect(
        str(path), timeout=10.0, isolation_level=None, check_same_thread=True,
    )
    write_conn.execute("PRAGMA journal_mode=WAL")
    write_conn.execute("PRAGMA synchronous=NORMAL")
    write_conn.execute("PRAGMA busy_timeout=10000")
    while True:
        item = _WRITE_Q.get()
        if item is None:  # shutdown sentinel
            break
        ts, username, ip, event_type, target, details_json = item
        try:
            write_conn.execute(
                "INSERT INTO audit_events(ts,username,ip,event_type,target,details_json) "
                "VALUES (?,?,?,?,?,?)",
                (ts, username, ip, event_type, target, details_json),
            )
        except Exception as exc:
            # Last resort: log to stderr; we don't have anywhere safer to put
            # an audit-write failure note.
            logger.error("audit write failed: %s — event=%s/%s",
                         exc, event_type, target)
    try:
        write_conn.close()
    except Exception:
        pass


def _ensure_writer() -> None:
    global _WRITER_THREAD
    with _WRITER_LOCK:
        if _WRITER_THREAD is None or not _WRITER_THREAD.is_alive():
            _WRITER_THREAD = threading.Thread(
                target=_writer_loop, name="audit-writer", daemon=True
            )
            _WRITER_THREAD.start()


def log_event(
    event_type: str,
    *,
    username: str = "",
    ip: str = "",
    target: str = "",
    details: Optional[dict[str, Any]] = None,
) -> None:
    """Enqueue an audit event. Returns immediately — actual DB write is async.

    `event_type` is a short snake_case identifier (see CLAUDE.md spec for
    the canonical list). `target` is the natural object of the action
    (tool_id, modified user's username, etc). `details` may carry anything
    JSON-serialisable; sensitive fields (password, full bearer token) MUST
    be omitted by the caller — we don't scrub here.
    """
    _ensure_writer()
    _WRITE_Q.put((
        time.time(),
        username or "",
        ip or "",
        event_type,
        target or "",
        json.dumps(details or {}, ensure_ascii=False),
    ))


def shutdown() -> None:
    """Stop the writer thread gracefully (drains remaining queue first)."""
    _WRITE_Q.put(None)
    if _WRITER_THREAD is not None:
        _WRITER_THREAD.join(timeout=5)

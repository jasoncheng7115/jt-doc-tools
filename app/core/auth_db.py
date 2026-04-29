"""Auth database schema + migrations + low-level access.

Stored at ``data/auth.sqlite``. Contains:

- users / groups / group_members (local mode)
- roles / role_perms (which tools each role can use)
- subject_roles, subject_perms (assign roles or direct tool grants to users
  / groups / OUs)
- sessions (cookie tokens), lockouts (failed login throttle)

External (LDAP/AD) users and groups also live in these tables so the
permission resolver can treat all subjects uniformly. Their `source` field
distinguishes them; `external_dn` carries the AD/LDAP DN.

Higher-level CRUD lives in `auth_manager.py`; this file only owns schema +
helpers shared by every layer.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from . import db as _db

logger = logging.getLogger(__name__)


# ---------- schema migrations ----------

def _m1_initial(conn: sqlite3.Connection) -> None:
    """v1: full v1.1.0 schema in one migration. Future schema changes get
    their own _m2, _m3, ..."""
    conn.executescript("""
    -- ---------- users ----------
    CREATE TABLE users (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        username        TEXT NOT NULL UNIQUE,
        display_name    TEXT NOT NULL DEFAULT '',
        password_hash   TEXT,                  -- NULL for ldap/ad users
        source          TEXT NOT NULL DEFAULT 'local'
                            CHECK (source IN ('local','ldap','ad')),
        external_dn     TEXT,                  -- LDAP/AD DN, NULL for local
        enabled         INTEGER NOT NULL DEFAULT 1,
        is_admin_seed   INTEGER NOT NULL DEFAULT 0,  -- jtdt-admin protection flag
        created_at      REAL NOT NULL,
        last_login_at   REAL DEFAULT 0
    );
    CREATE INDEX idx_users_username ON users(username);

    -- ---------- groups ----------
    CREATE TABLE groups (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        name            TEXT NOT NULL,
        source          TEXT NOT NULL DEFAULT 'local'
                            CHECK (source IN ('local','ldap','ad')),
        external_dn     TEXT,                  -- AD CN=...,OU=... DN
        description     TEXT NOT NULL DEFAULT '',
        created_at      REAL NOT NULL,
        UNIQUE(source, name)
    );

    -- local groups: explicit members.
    -- ldap/ad groups: members come from AD memberOf at login time, NOT here.
    CREATE TABLE group_members (
        group_id        INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
        user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        PRIMARY KEY (group_id, user_id)
    );

    -- ---------- roles ----------
    -- id is a stable text key (e.g. 'admin', 'clerk') so seed roles survive
    -- import/export. is_builtin=1 protects 'admin' and 'default-user' from
    -- destructive UI operations.
    CREATE TABLE roles (
        id              TEXT PRIMARY KEY,
        display_name    TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        is_builtin      INTEGER NOT NULL DEFAULT 0,
        is_protected    INTEGER NOT NULL DEFAULT 0,  -- can edit perms but not rename/delete
        created_at      REAL NOT NULL
    );

    -- which tool ids a role grants. tool_id is the registry id like 'pdf-fill'.
    CREATE TABLE role_perms (
        role_id         TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        tool_id         TEXT NOT NULL,
        PRIMARY KEY (role_id, tool_id)
    );

    -- ---------- subject → role assignments ----------
    -- subject_type is 'user' | 'group' | 'ou'.
    -- subject_key is:
    --   user  -> users.id (as text)
    --   group -> groups.id (as text)
    --   ou    -> the OU DN string (e.g. 'OU=Sales,OU=TW,DC=example,DC=com')
    -- Storing as text makes the schema uniform; we don't FK these (OU has
    -- no row anywhere; group/user FKs are still enforced via app-level checks
    -- on delete).
    CREATE TABLE subject_roles (
        subject_type    TEXT NOT NULL CHECK (subject_type IN ('user','group','ou')),
        subject_key     TEXT NOT NULL,
        role_id         TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
        PRIMARY KEY (subject_type, subject_key, role_id)
    );
    CREATE INDEX idx_subject_roles_subj ON subject_roles(subject_type, subject_key);

    -- direct (subject → tool) grants for special cases. UI hides these
    -- behind an "advanced" toggle.
    CREATE TABLE subject_perms (
        subject_type    TEXT NOT NULL CHECK (subject_type IN ('user','group','ou')),
        subject_key     TEXT NOT NULL,
        tool_id         TEXT NOT NULL,
        PRIMARY KEY (subject_type, subject_key, tool_id)
    );
    CREATE INDEX idx_subject_perms_subj ON subject_perms(subject_type, subject_key);

    -- ---------- sessions ----------
    -- We store sha256(cookie) NOT the raw cookie value: a DB breach then
    -- can't directly resume sessions (attacker still needs the cookie that
    -- only ever lived on the user's browser + briefly in a Set-Cookie).
    -- expires_at is absolute epoch seconds; remember=1 for 30d, 0 for 7d.
    CREATE TABLE sessions (
        token_hash      TEXT PRIMARY KEY,    -- sha256 hex of the cookie value
        user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        created_at      REAL NOT NULL,
        expires_at      REAL NOT NULL,
        remember        INTEGER NOT NULL DEFAULT 0,
        ip              TEXT NOT NULL DEFAULT '',
        user_agent      TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_sessions_user ON sessions(user_id);
    CREATE INDEX idx_sessions_exp ON sessions(expires_at);

    -- ---------- lockouts ----------
    -- key is 'user:<username>' or 'ip:<addr>'. Failed login increments
    -- failed_count; reaching threshold (5) sets locked_until = now + 15min.
    -- Successful login clears the row.
    CREATE TABLE lockouts (
        key             TEXT PRIMARY KEY,
        failed_count    INTEGER NOT NULL DEFAULT 0,
        locked_until    REAL NOT NULL DEFAULT 0,
        last_failed_at  REAL NOT NULL DEFAULT 0
    );
    """)


def _m2_username_source_unique(conn: sqlite3.Connection) -> None:
    """v2: drop UNIQUE(username), add UNIQUE(username, source).

    Rationale: PVE-style multi-realm — same name `jason` may legitimately
    exist as both a `local` account and an `ldap` account; the realm
    dropdown on /login disambiguates at auth time. SQLite can't drop a
    column-level UNIQUE in place, so rebuild the table the standard way.
    """
    conn.executescript("""
    -- Lifted from _m1 with one change: UNIQUE moved off `username` onto
    -- (username, source). Everything else stays bit-for-bit identical so
    -- existing data copies over with INSERT INTO ... SELECT *.
    CREATE TABLE users_new (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        username        TEXT NOT NULL,
        display_name    TEXT NOT NULL DEFAULT '',
        password_hash   TEXT,
        source          TEXT NOT NULL DEFAULT 'local'
                            CHECK (source IN ('local','ldap','ad')),
        external_dn     TEXT,
        enabled         INTEGER NOT NULL DEFAULT 1,
        is_admin_seed   INTEGER NOT NULL DEFAULT 0,
        created_at      REAL NOT NULL,
        last_login_at   REAL DEFAULT 0,
        UNIQUE(username, source)
    );
    INSERT INTO users_new
        (id, username, display_name, password_hash, source, external_dn,
         enabled, is_admin_seed, created_at, last_login_at)
    SELECT id, username, display_name, password_hash, source, external_dn,
           enabled, is_admin_seed, created_at, last_login_at
    FROM users;
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;
    CREATE INDEX idx_users_username ON users(username);
    """)


def _m3_rename_pdf_diff_to_doc_diff(conn: sqlite3.Connection) -> None:
    """v3: rename `pdf-diff` → `doc-diff` in role_perms and subject_perms.

    The tool was renamed (and gained Office / ODF support) in v1.1.61.
    Without this migration, existing installs would keep granting access to
    the now-non-existent `pdf-diff` tool id and would NOT grant access to
    the new `doc-diff` — meaning users would silently lose the tool after
    upgrade.

    `INSERT OR IGNORE` shape avoids dupe-key errors if (somehow) both rows
    already exist for the same role/subject (e.g. admin manually granted
    `doc-diff` first); the old row is then dropped by the DELETE.
    """
    conn.executescript("""
    INSERT OR IGNORE INTO role_perms(role_id, tool_id)
        SELECT role_id, 'doc-diff' FROM role_perms WHERE tool_id = 'pdf-diff';
    DELETE FROM role_perms WHERE tool_id = 'pdf-diff';

    INSERT OR IGNORE INTO subject_perms(subject_type, subject_key, tool_id)
        SELECT subject_type, subject_key, 'doc-diff'
        FROM subject_perms WHERE tool_id = 'pdf-diff';
    DELETE FROM subject_perms WHERE tool_id = 'pdf-diff';
    """)


MIGRATIONS = [_m1_initial, _m2_username_source_unique,
              _m3_rename_pdf_diff_to_doc_diff]


def auth_db_path() -> Path:
    from ..config import settings
    return settings.data_dir / "auth.sqlite"


def init() -> None:
    """Apply all pending migrations. Idempotent — safe to call on every boot."""
    path = auth_db_path()
    final = _db.migrate(path, MIGRATIONS)
    logger.info("auth DB ready at %s (schema v%d)", path, final)


def conn():
    """Shortcut: thread-local connection to the auth DB."""
    return _db.get_conn(auth_db_path())

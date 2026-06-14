"""Regression: auth_db migration v8 (SSO sources) must NOT wipe data.

v8 rebuilds the `users` + `groups` tables to widen the `source` CHECK to allow
'oidc'/'saml'. The rebuild DROPs the tables — with foreign_keys=ON that fires
`group_members`' ON DELETE CASCADE and silently wipes every membership (an
existing LDAP/AD/local-group install would lose group-derived roles on upgrade).
v8 therefore disables foreign_keys for the rebuild. This test simulates a
populated pre-v8 (v7) DB and asserts users / groups / memberships all survive.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import app.core.db as dbm
from app.core import auth_db


def _seed_v7_db() -> tuple[Path, object]:
    p = Path(tempfile.mkdtemp()) / "auth.sqlite"
    dbm.migrate(p, auth_db.MIGRATIONS[:7])  # stop at v7 (pre-SSO)
    c = dbm.get_conn(p)
    c.execute("INSERT INTO users(username,display_name,source,external_dn,enabled,"
              "is_admin_seed,created_at) VALUES('bob','Bob','ldap','CN=bob',1,0,?)",
              (time.time(),))
    uid = c.execute("SELECT id FROM users WHERE username='bob'").fetchone()["id"]
    c.execute("INSERT INTO users(username,display_name,source,enabled,is_admin_seed,"
              "created_at,password_hash) VALUES('admin','A','local',1,1,?, 'x')",
              (time.time(),))
    c.execute("INSERT INTO groups(name,source,external_dn,created_at) "
              "VALUES('Sales','ldap','CN=Sales',?)", (time.time(),))
    gid = c.execute("SELECT id FROM groups WHERE name='Sales'").fetchone()["id"]
    c.execute("INSERT INTO groups(name,source,created_at) VALUES('LocalTeam','local',?)",
              (time.time(),))
    lgid = c.execute("SELECT id FROM groups WHERE name='LocalTeam'").fetchone()["id"]
    c.execute("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (gid, uid))
    c.execute("INSERT INTO group_members(group_id,user_id) VALUES(?,?)", (lgid, uid))
    return p, c


def test_v8_preserves_users_groups_memberships():
    p, c = _seed_v7_db()
    assert c.execute("PRAGMA user_version").fetchone()[0] == 7
    assert c.execute("SELECT COUNT(1) FROM group_members").fetchone()[0] == 2

    dbm.migrate(p, auth_db.MIGRATIONS)  # apply v8

    assert c.execute("PRAGMA user_version").fetchone()[0] == 8
    # users + groups copied over
    assert c.execute("SELECT COUNT(1) FROM users").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(1) FROM groups").fetchone()[0] == 2
    # CRITICAL: memberships survived (the bug wiped these to 0)
    assert c.execute("SELECT COUNT(1) FROM group_members").fetchone()[0] == 2
    # FK enforcement re-enabled + no dangling references
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v8_allows_oidc_saml_sources():
    p, c = _seed_v7_db()
    dbm.migrate(p, auth_db.MIGRATIONS)
    # the whole point: oidc / saml are now valid sources
    for src in ("oidc", "saml"):
        c.execute("INSERT INTO users(username,display_name,source,external_dn,enabled,"
                  "is_admin_seed,created_at) VALUES(?,?,?,?,1,0,?)",
                  (f"u_{src}", src, src, f"ext-{src}", time.time()))
    assert c.execute("SELECT COUNT(1) FROM users WHERE source IN ('oidc','saml')"
                     ).fetchone()[0] == 2


def test_oidc_alg_allowlist_excludes_symmetric():
    from app.core import oidc
    assert "HS256" not in oidc._ALLOWED_ALGS  # alg-confusion guard
    assert all(a[:2] in ("RS", "ES", "PS") for a in oidc._ALLOWED_ALGS)

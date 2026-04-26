"""Unit tests for auth_ldap._sync_user — collision behaviour.

Schema as of v1.1.13 uses UNIQUE(username, source) so PVE-style multi-realm
accounts coexist (`jason@local` vs `jason@ldap`).

Test list:
  1. First-time LDAP user → INSERT new row with source='ldap'
  2. Same DN logging in again → UPDATE existing row (no duplicate)
  3. New LDAP DN with username that exists as local → SUCCESS (separate rows)
  4. New LDAP DN with username that exists as a *different* LDAP DN → AuthError
     (refuse silent identity takeover within the same realm)

These exercise the function directly, no real LDAP server needed. The
LDAP-side bind/search is covered by `test_user_login` integration helpers.
"""
from __future__ import annotations

import pytest

from app.core import auth_db, auth_ldap, db, roles, user_manager


def _setup(auth_off):
    """Initialised auth DB + seeded roles (FK target for default-user)."""
    roles.seed_builtin_roles()
    return auth_db.conn()


def test_sync_user_first_time_inserts(auth_off):
    """Brand-new LDAP DN → row is created with source='ldap'."""
    _setup(auth_off)
    u = auth_ldap._sync_user(
        username="alice", display_name="Alice X",
        dn="uid=alice,ou=Users,dc=example,dc=com", backend="ldap",
    )
    assert u["user_id"]
    assert u["username"] == "alice"
    row = auth_db.conn().execute(
        "SELECT username, source, external_dn FROM users WHERE id=?",
        (u["user_id"],)
    ).fetchone()
    assert row["source"] == "ldap"
    assert row["external_dn"] == "uid=alice,ou=Users,dc=example,dc=com"


def test_sync_user_same_dn_updates(auth_off):
    """Same DN comes back → UPDATE existing row, do not duplicate."""
    _setup(auth_off)
    u1 = auth_ldap._sync_user("bob", "Bob One",
                              "uid=bob,dc=example,dc=com", "ldap")
    u2 = auth_ldap._sync_user("bob", "Bob Two",
                              "uid=bob,dc=example,dc=com", "ldap")
    assert u1["user_id"] == u2["user_id"]
    assert u2["display_name"] == "Bob Two"
    # Only one row exists
    n = auth_db.conn().execute(
        "SELECT COUNT(*) AS c FROM users WHERE username='bob'"
    ).fetchone()["c"]
    assert n == 1


def test_sync_user_coexists_with_local_same_name(auth_off):
    """PVE-style: local `jason` and LDAP `jason` are SEPARATE accounts;
    UNIQUE(username, source) lets them coexist. No error, two rows."""
    _setup(auth_off)
    local_uid = user_manager.create_local(
        username="jason", display_name="Local Jason",
        password="LocalPwd1234", roles=["default-user"],
    )
    ldap_user = auth_ldap._sync_user("jason", "LDAP Jason",
                                     "uid=jason,dc=example,dc=com", "ldap")
    assert ldap_user["user_id"] != local_uid
    rows = auth_db.conn().execute(
        "SELECT source FROM users WHERE username='jason' ORDER BY source"
    ).fetchall()
    assert [r["source"] for r in rows] == ["ldap", "local"]


def test_sync_user_dict_canonical_key_is_user_id(auth_off):
    """Regression for v1.1.13: auth_ldap.authenticate() called
    `user_row["id"]` but _sync_user returned `{"user_id": ...}` — login
    500'd with KeyError. This pins the contract so any future rename
    has to update both producer and consumer together."""
    _setup(auth_off)
    u = auth_ldap._sync_user("ke", "K E",
                             "uid=ke,dc=ex,dc=com", "ldap")
    # Canonical key matches what auth_routes / sessions / permissions all use.
    assert "user_id" in u and isinstance(u["user_id"], int)
    # Don't add a duplicate "id" key — a single source of truth keeps
    # `user_row["id"]` mistakes failing fast in CI rather than at runtime.
    assert "id" not in u


def test_sync_user_username_collides_with_other_ldap_dn(auth_off):
    """Two different DNs claiming the same uid in the SAME backend → refuse
    the second one (would silently take over the first user's identity)."""
    _setup(auth_off)
    auth_ldap._sync_user("dup", "First",
                         "uid=dup,ou=Tw,dc=example,dc=com", "ldap")
    with pytest.raises(auth_ldap.AuthError) as exc:
        auth_ldap._sync_user("dup", "Second",
                             "uid=dup,ou=Us,dc=example,dc=com", "ldap")
    assert "另一個 LDAP DN" in str(exc.value)

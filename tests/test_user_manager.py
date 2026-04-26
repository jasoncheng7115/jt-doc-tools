"""Tests for app.core.user_manager + app.core.group_manager.

Test list:
  - create_local: happy path, default 'default-user' role assigned
  - create_local: rejects bad username / pw
  - create_local: rejects duplicate username
  - reset_password: works for local user, refuses LDAP user, revokes sessions
  - delete: refuses seed admin, refuses last admin
  - update enabled=False: revokes sessions
  - permissions resolver: a user with only default-user role can use pdf-merge
                         but NOT pdf-fill
  - admin role short-circuits to ALL
  - groups: create / delete / set_members
"""
from __future__ import annotations

import pytest

from app.core import (auth_settings, group_manager, permissions, roles,
                      sessions, user_manager)


def test_create_local_happy(auth_off):
    roles.seed_builtin_roles()
    uid = user_manager.create_local("alice", "Alice", "GoodPass1234")
    assert uid > 0
    u = user_manager.get_by_id(uid)
    assert u["username"] == "alice"
    assert "default-user" in u["roles"]


def test_create_local_rejects_bad_username(auth_off):
    roles.seed_builtin_roles()
    with pytest.raises(ValueError):
        user_manager.create_local("bad name", "X", "GoodPass1234")


def test_create_local_rejects_short_password(auth_off):
    roles.seed_builtin_roles()
    with pytest.raises(ValueError):
        user_manager.create_local("alice", "A", "short")


def test_create_local_rejects_duplicate(auth_off):
    roles.seed_builtin_roles()
    user_manager.create_local("alice", "A", "GoodPass1234")
    with pytest.raises(ValueError):
        user_manager.create_local("alice", "B", "OtherPass1234")


def test_reset_password_revokes_sessions(auth_off):
    roles.seed_builtin_roles()
    uid = user_manager.create_local("alice", "A", "GoodPass1234")
    sessions.issue(uid, remember=False)
    sessions.issue(uid, remember=False)
    user_manager.reset_password(uid, "NewGoodPass1234")
    # Sessions wiped after reset
    from app.core import auth_db
    n = auth_db.conn().execute(
        "SELECT count(*) FROM sessions WHERE user_id=?", (uid,)
    ).fetchone()[0]
    assert n == 0


def test_delete_refuses_seed_admin(admin_session):
    # admin_session bootstraps a user with is_admin_seed=1
    from app.core import auth_db
    uid = auth_db.conn().execute(
        "SELECT id FROM users WHERE is_admin_seed=1"
    ).fetchone()["id"]
    with pytest.raises(ValueError) as exc:
        user_manager.delete(uid)
    assert "初始" in str(exc.value)


def test_delete_refuses_last_admin(auth_off):
    """If user holds admin role and is the last admin, can't delete."""
    roles.seed_builtin_roles()
    uid = user_manager.create_local("solo-admin", "Solo", "GoodPass1234",
                                    roles=["admin"])
    with pytest.raises(ValueError) as exc:
        user_manager.delete(uid)
    assert "最後" in str(exc.value)


def test_delete_works_when_not_last_admin(auth_off):
    roles.seed_builtin_roles()
    a = user_manager.create_local("a", "A", "GoodPass1234", roles=["admin"])
    b = user_manager.create_local("b", "B", "GoodPass1234", roles=["admin"])
    user_manager.delete(b)
    assert user_manager.get_by_id(b) is None
    assert user_manager.get_by_id(a) is not None


def test_disable_user_revokes_sessions(auth_off):
    roles.seed_builtin_roles()
    uid = user_manager.create_local("alice", "A", "GoodPass1234")
    sessions.issue(uid, remember=False)
    user_manager.update(uid, enabled=False)
    from app.core import auth_db
    n = auth_db.conn().execute(
        "SELECT count(*) FROM sessions WHERE user_id=?", (uid,)
    ).fetchone()[0]
    assert n == 0


def test_default_user_can_merge_but_not_fill(auth_off):
    roles.seed_builtin_roles()
    uid = user_manager.create_local("alice", "A", "GoodPass1234")
    assert permissions.user_can_use_tool(uid, "pdf-merge") is True
    assert permissions.user_can_use_tool(uid, "pdf-fill") is False
    assert permissions.user_can_use_tool(uid, "pdf-stamp") is False


def test_admin_user_can_use_everything(auth_off):
    roles.seed_builtin_roles()
    uid = user_manager.create_local("god", "God", "GoodPass1234",
                                    roles=["admin"])
    assert permissions.user_can_use_tool(uid, "pdf-fill") is True
    assert permissions.user_can_use_tool(uid, "pdf-stamp") is True
    # Even something that doesn't exist as a tool — admin bypass
    assert permissions.user_can_use_tool(uid, "anything-else") is True


def test_finance_role_has_pdf_fill(auth_off):
    roles.seed_builtin_roles()
    uid = user_manager.create_local("acc", "A", "GoodPass1234",
                                    roles=["finance"])
    assert permissions.user_can_use_tool(uid, "pdf-fill") is True
    assert permissions.user_can_use_tool(uid, "pdf-stamp") is True
    assert permissions.user_can_use_tool(uid, "pdf-encrypt") is True


def test_role_change_invalidates_cache(auth_off):
    roles.seed_builtin_roles()
    uid = user_manager.create_local("alice", "A", "GoodPass1234")
    assert permissions.user_can_use_tool(uid, "pdf-fill") is False
    # Promote to finance
    permissions.set_subject_roles("user", str(uid), ["finance"])
    assert permissions.user_can_use_tool(uid, "pdf-fill") is True


def test_group_role_grants_inherited(auth_off):
    """User in a group with finance role → inherits pdf-fill access."""
    roles.seed_builtin_roles()
    uid = user_manager.create_local("alice", "A", "GoodPass1234")
    gid = group_manager.create_local("Sales Team")
    group_manager.set_members(gid, [uid])
    permissions.set_subject_roles("group", str(gid), ["finance"])
    assert permissions.user_can_use_tool(uid, "pdf-fill") is True


def test_group_create_delete(auth_off):
    gid = group_manager.create_local("Engineers", "Dev team")
    assert group_manager.get(gid)["name"] == "Engineers"
    group_manager.delete(gid)
    assert group_manager.get(gid) is None

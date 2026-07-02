"""Tests for the new-user default role + seed-snapshot behaviour (v1.12.53).

Covers two features added together:

A. Configurable "new-user default role"
   - after seeding, default-user is the default and get_default_role_id() finds it
   - set_default_role_id() moves the flag to a custom role (exactly one flagged)
   - admin / auditor may NOT be set as the new-user default
   - a non-existent role may NOT be set
   - delete() refuses to drop the role currently marked default
   - get_default_role_id() self-heals / falls back if the flagged role vanished

B. seed_builtin_roles() snapshot logic (the "升級又被改回來" bug fix)
   - admin's REMOVAL of a built-in tool survives a re-seed (upgrade)
   - a genuinely NEW tool this release DOES propagate to existing roles
   - bootstrap (existing customer, no snapshot yet) is conservative: it does
     NOT re-add tools the admin removed before the snapshot table existed
"""
from __future__ import annotations

import time

import pytest

from app.core import auth_db, db, roles


def _reset_roles() -> None:
    """Wipe every role-related table so each test starts clean. auth_off only
    wipes users/sessions, not roles / role_perms / role_seed_snapshot (those
    live in a shared DB file across the session)."""
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM subject_roles")
        conn.execute("DELETE FROM role_perms")
        conn.execute("DELETE FROM role_seed_snapshot")
        conn.execute("DELETE FROM roles")


@pytest.fixture(autouse=True)
def _restore_canonical_roles():
    """These tests mutate the shared roles tables (add/remove tools, flip the
    default flag). Restore a clean canonically-seeded state on teardown so we
    don't pollute other test files that assume the stock seed (e.g. test_roles
    :: test_default_user_excludes_sensitive)."""
    yield
    _reset_roles()
    roles.seed_builtin_roles()


# ---------------- A. new-user default role ----------------

def test_default_is_default_user_after_seed(auth_off):
    _reset_roles()
    roles.seed_builtin_roles()
    assert roles.get_default_role_id() == "default-user"
    du = roles.get("default-user")
    assert du["is_default_for_new"] is True
    # exactly one flagged
    flagged = [r for r in roles.list_roles() if r["is_default_for_new"]]
    assert len(flagged) == 1


def test_set_default_to_custom_role(auth_off):
    _reset_roles()
    roles.seed_builtin_roles()
    roles.create("accountant", "會計", tools=["pdf-merge"])
    roles.set_default_role_id("accountant")
    assert roles.get_default_role_id() == "accountant"
    flagged = [r["id"] for r in roles.list_roles() if r["is_default_for_new"]]
    assert flagged == ["accountant"]  # exactly one, and it moved
    assert roles.get("default-user")["is_default_for_new"] is False


def test_set_default_rejects_admin_and_auditor(auth_off):
    _reset_roles()
    roles.seed_builtin_roles()
    with pytest.raises(ValueError):
        roles.set_default_role_id("admin")
    with pytest.raises(ValueError):
        roles.set_default_role_id("auditor")
    # default unchanged
    assert roles.get_default_role_id() == "default-user"


def test_set_default_rejects_missing_role(auth_off):
    _reset_roles()
    roles.seed_builtin_roles()
    with pytest.raises(ValueError):
        roles.set_default_role_id("does-not-exist")


def test_delete_refuses_current_default(auth_off):
    _reset_roles()
    roles.seed_builtin_roles()
    roles.create("accountant", "會計", tools=["pdf-merge"])
    roles.set_default_role_id("accountant")
    with pytest.raises(ValueError):
        roles.delete("accountant")
    # after pointing default elsewhere it can be deleted
    roles.set_default_role_id("default-user")
    roles.delete("accountant")
    assert roles.get("accountant") is None


def test_get_default_self_heals_when_flag_role_vanishes(auth_off):
    _reset_roles()
    roles.seed_builtin_roles()
    roles.create("accountant", "會計", tools=["pdf-merge"])
    roles.set_default_role_id("accountant")
    # Force-delete the flagged role bypassing the guard (simulate corruption).
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM roles WHERE id='accountant'")
    # No role is flagged now → get_default_role_id should self-heal to
    # default-user.
    assert roles.get_default_role_id() == "default-user"
    assert roles.get("default-user")["is_default_for_new"] is True


# ---------------- B. seed snapshot behaviour ----------------

def test_admin_removal_survives_reseed(auth_off):
    """The core bug fix: admin removes a tool from a built-in role, and a later
    upgrade (re-seed) must NOT put it back."""
    _reset_roles()
    roles.seed_builtin_roles()
    du = roles.get("default-user")
    assert "pdf-ocr" in du["tools"]  # precondition — it's in the seed
    # admin unselects pdf-ocr and saves
    new_tools = [t for t in du["tools"] if t != "pdf-ocr"]
    roles.update("default-user", tools=new_tools)
    assert "pdf-ocr" not in roles.get("default-user")["tools"]
    # simulate an upgrade: seed runs again
    roles.seed_builtin_roles()
    assert "pdf-ocr" not in roles.get("default-user")["tools"], \
        "re-seed must not re-add an admin-removed tool"


def test_admin_removal_survives_two_reseeds(auth_off):
    """Removal must persist across MULTIPLE subsequent upgrades, not just one
    (the snapshot is refreshed each run — verify it doesn't regress)."""
    _reset_roles()
    roles.seed_builtin_roles()
    du = roles.get("default-user")
    new_tools = [t for t in du["tools"] if t != "pdf-merge"]
    roles.update("default-user", tools=new_tools)
    roles.seed_builtin_roles()
    roles.seed_builtin_roles()
    assert "pdf-merge" not in roles.get("default-user")["tools"]


def test_admin_addition_survives_reseed(auth_off):
    """Admin ADDS a normally-not-in-default tool (pdf-fill) to default-user —
    re-seed must not strip it."""
    _reset_roles()
    roles.seed_builtin_roles()
    du = roles.get("default-user")
    assert "pdf-fill" not in du["tools"]
    roles.update("default-user", tools=du["tools"] + ["pdf-fill"])
    roles.seed_builtin_roles()
    assert "pdf-fill" in roles.get("default-user")["tools"]


def test_new_tool_this_release_propagates(auth_off):
    """A tool that this release newly added to the seed def (i.e. it's in
    SEED_ROLES but NOT in the role's snapshot) must be granted to existing
    customers' built-in roles on upgrade."""
    _reset_roles()
    roles.seed_builtin_roles()
    conn = auth_db.conn()
    # Simulate "pdf-merge is brand-new this release": remove it from BOTH the
    # snapshot (old seed def didn't list it) and the current grant (old
    # customer never had it).
    with db.tx(conn):
        conn.execute("DELETE FROM role_seed_snapshot "
                     "WHERE role_id='default-user' AND tool_id='pdf-merge'")
        conn.execute("DELETE FROM role_perms "
                     "WHERE role_id='default-user' AND tool_id='pdf-merge'")
    assert "pdf-merge" not in roles.get("default-user")["tools"]
    # Upgrade re-seed → pdf-merge is newly-introduced vs snapshot → added.
    roles.seed_builtin_roles()
    assert "pdf-merge" in roles.get("default-user")["tools"]


def test_bootstrap_preserves_pre_snapshot_removal(auth_off):
    """Existing customer upgrading INTO the snapshot feature: their built-in
    role already lacks a tool the admin removed long ago, and there's no
    snapshot yet. Bootstrap must be conservative and NOT re-add it."""
    _reset_roles()
    conn = auth_db.conn()
    now = time.time()
    # Hand-build a pre-snapshot default-user: has most seed tools EXCEPT
    # pdf-ocr (admin removed it), PLUS pdf-fill (admin added it). No snapshot.
    seed_du = next(r for r in roles.SEED_ROLES if r["id"] == "default-user")
    tools = [t for t in seed_du["tools"] if t != "pdf-ocr"] + ["pdf-fill"]
    with db.tx(conn):
        conn.execute(
            "INSERT INTO roles(id, display_name, description, is_builtin, "
            "is_protected, created_at) VALUES ('default-user','一般使用者','',1,1,?)",
            (now,))
        for t in tools:
            conn.execute("INSERT INTO role_perms(role_id, tool_id) "
                         "VALUES ('default-user', ?)", (t,))
    assert not conn.execute(
        "SELECT 1 FROM role_seed_snapshot WHERE role_id='default-user'"
    ).fetchone()  # precondition: no snapshot
    # First seed run after the feature lands.
    roles.seed_builtin_roles()
    du_tools = roles.get("default-user")["tools"]
    assert "pdf-ocr" not in du_tools, "bootstrap must not re-add pre-existing removal"
    assert "pdf-fill" in du_tools, "admin's own addition must be preserved"
    # And the snapshot is now populated so future upgrades diff correctly.
    assert conn.execute(
        "SELECT 1 FROM role_seed_snapshot WHERE role_id='default-user'"
    ).fetchone()


# ---------------- C. admin HTTP endpoint + end-to-end provisioning ----------

def test_admin_set_default_endpoint(admin_session):
    c, _, _ = admin_session
    roles.create("accountant", "會計", tools=["pdf-merge"])
    r = c.post("/admin/roles/accountant/set-default")
    assert r.status_code == 200, r.text
    assert roles.get_default_role_id() == "accountant"


def test_admin_set_default_rejects_admin_role(admin_session):
    c, _, _ = admin_session
    r = c.post("/admin/roles/admin/set-default")
    assert r.status_code == 400
    assert roles.get_default_role_id() != "admin"


def test_new_local_user_gets_configured_default(admin_session):
    """End-to-end: point the new-user default at a custom role, then create a
    local user with no explicit roles → they land in the custom role."""
    from app.core import user_manager, permissions
    c, _, _ = admin_session
    roles.create("accountant", "會計", tools=["pdf-merge"])
    roles.set_default_role_id("accountant")
    uid = user_manager.create_local("bob-acct", "Bob", "TestPass1234")
    assigned = permissions.list_roles_for_subject("user", str(uid))
    assert "accountant" in assigned
    assert "default-user" not in assigned

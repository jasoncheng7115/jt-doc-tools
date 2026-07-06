"""Scheduled AD/LDAP directory sync + the perf fixes it enables (v1.12.67).

Covers:
  - migration v11 group cache columns exist
  - permissions.list_roles_for_subjects (batch) == per-subject results
  - group_manager.list_groups exposes dir_member_count / parent_dn
  - directory_sync settings defaults + clamp
  - run_sync mirrors + caches member counts (auth_ldap mocked)
  - run_sync no-ops on a non-directory backend
"""
from __future__ import annotations

import pytest

from app.core import auth_db, audit_db, permissions, group_manager, directory_sync


@pytest.fixture(autouse=True)
def _init_db():
    """Startup hook runs lazily under TestClient; init schemas so DB-only
    tests don't hit "no such table"."""
    auth_db.init()
    audit_db.init()
    from app.core import roles
    roles.seed_builtin_roles()          # so 'clerk'/'finance' FK targets exist
    yield


# --------------------------------------------------------------------- helpers

def _mk_group(name: str, source: str = "ldap", dn: str = "") -> int:
    conn = auth_db.conn()
    cur = conn.execute(
        "INSERT INTO groups(name, source, external_dn, created_at) VALUES(?,?,?,?)",
        (name, source, dn, "2026-01-01T00:00:00"))
    conn.commit()
    return cur.lastrowid


def _rm_group(name: str) -> None:
    conn = auth_db.conn()
    conn.execute("DELETE FROM groups WHERE name=?", (name,))
    conn.commit()


# ------------------------------------------------------------------- migration

def test_migration_added_group_cache_columns():
    cols = {r["name"] for r in auth_db.conn().execute("PRAGMA table_info(groups)")}
    assert {"member_count", "member_count_synced_at", "parent_dn"} <= cols


# --------------------------------------------------------------- batch roles

def test_list_roles_for_subjects_matches_per_subject():
    gid = _mk_group("dsync_batch_grp")
    try:
        # assign two roles to the group subject
        conn = auth_db.conn()
        for rid in ("clerk", "finance"):
            conn.execute(
                "INSERT OR IGNORE INTO subject_roles(subject_type, subject_key, role_id) "
                "VALUES('group', ?, ?)", (str(gid), rid))
        conn.commit()
        batch = permissions.list_roles_for_subjects("group", [str(gid), "999999"])
        assert set(batch[str(gid)]) == {"clerk", "finance"}
        assert batch["999999"] == []          # unknown key → empty, still present
        # equivalence with the per-subject helper
        assert set(batch[str(gid)]) == set(
            permissions.list_roles_for_subject("group", str(gid)))
    finally:
        auth_db.conn().execute("DELETE FROM subject_roles WHERE subject_key=?", (str(gid),))
        _rm_group("dsync_batch_grp")


def test_list_roles_for_subjects_empty_input():
    assert permissions.list_roles_for_subjects("user", []) == {}


# --------------------------------------------------- list_groups cached fields

def test_list_groups_exposes_dir_cache_fields():
    gid = _mk_group("dsync_fields_grp", source="ldap", dn="cn=x,dc=t")
    try:
        conn = auth_db.conn()
        conn.execute("UPDATE groups SET member_count=?, member_count_synced_at=? "
                     "WHERE id=?", (55, 1700000000.0, gid))
        conn.commit()
        g = next(x for x in group_manager.list_groups() if x["id"] == gid)
        assert g["dir_member_count"] == 55
        assert g["dir_synced_at"] == 1700000000.0
        assert g["parent_dn"] == ""
    finally:
        _rm_group("dsync_fields_grp")


# ------------------------------------------------------------------ settings

def test_settings_defaults_and_clamp():
    s = directory_sync.save_settings(enabled=True, interval_hours=999, name_contains="  UG_  ")
    assert s["interval_hours"] == 168        # clamped to 7 days
    assert s["name_contains"] == "UG_"
    s2 = directory_sync.save_settings(interval_hours=0)
    assert s2["interval_hours"] == 1          # clamped up


# ------------------------------------------------------------------- run_sync

def test_run_sync_caches_member_counts(monkeypatch):
    g1 = _mk_group("dsync_run_g1", source="ldap", dn="cn=g1,dc=t")
    g2 = _mk_group("dsync_run_g2", source="ldap", dn="cn=g2,dc=t")
    try:
        monkeypatch.setattr(directory_sync, "is_directory_backend", lambda: True)
        import app.core.auth_ldap as al
        monkeypatch.setattr(
            al, "sync_all_groups",
            lambda name_contains="": {"synced": 0, "updated": 0, "total_seen": 2})
        counts = {"cn=g1,dc=t": 42, "cn=g2,dc=t": 7}
        monkeypatch.setattr(al, "count_group_members", lambda dn: counts[dn])
        rep = directory_sync.run_sync()
        assert rep.get("counts_updated") == 2
        assert rep.get("counts_failed") == 0
        conn = auth_db.conn()
        r1 = conn.execute("SELECT member_count, member_count_synced_at FROM groups WHERE id=?",
                          (g1,)).fetchone()
        assert r1["member_count"] == 42 and r1["member_count_synced_at"] is not None
        r2 = conn.execute("SELECT member_count FROM groups WHERE id=?", (g2,)).fetchone()
        assert r2["member_count"] == 7
    finally:
        _rm_group("dsync_run_g1")
        _rm_group("dsync_run_g2")


def test_run_sync_noop_on_non_directory_backend(monkeypatch):
    monkeypatch.setattr(directory_sync, "is_directory_backend", lambda: False)
    rep = directory_sync.run_sync()
    assert "skipped" in rep


# --------------------------------------------------- group hierarchy (tree)

def _g(gid, name, dn="", parent=""):
    return {"id": gid, "name": name, "source": "ldap",
            "external_dn": dn, "parent_dn": parent}


def test_order_groups_as_tree_nesting_and_depth():
    groups = [
        _g(1, "資訊處", "cn=it,dc=t"),
        _g(2, "技術服務部", "cn=svc,dc=t", "cn=it,dc=t"),
        _g(3, "網路組", "cn=net,dc=t", "cn=svc,dc=t"),
        _g(4, "人資處", "cn=hr,dc=t"),
    ]
    out = group_manager.order_groups_as_tree(groups)
    depth = {g["name"]: g["depth"] for g in out}
    assert depth == {"資訊處": 0, "技術服務部": 1, "網路組": 2, "人資處": 0}
    # parent always appears before its child
    order = [g["name"] for g in out]
    assert order.index("資訊處") < order.index("技術服務部") < order.index("網路組")
    assert len(out) == 4                       # every group emitted exactly once


def test_order_groups_as_tree_cycle_safe():
    groups = [
        _g(1, "A", "cn=a,dc=t", "cn=b,dc=t"),
        _g(2, "B", "cn=b,dc=t", "cn=a,dc=t"),
    ]
    out = group_manager.order_groups_as_tree(groups)
    assert {g["name"] for g in out} == {"A", "B"}   # no infinite loop, both once


def test_order_groups_as_tree_local_and_unknown_parent_are_roots():
    groups = [
        _g(1, "本機群組", ""),                      # local → root
        _g(2, "孤兒", "cn=x,dc=t", "cn=missing,dc=t"),  # parent not in set → root
    ]
    out = group_manager.order_groups_as_tree(groups)
    assert all(g["depth"] == 0 for g in out)
    assert len(out) == 2


def test_run_sync_counts_failures(monkeypatch):
    gid = _mk_group("dsync_fail_g", source="ldap", dn="cn=bad,dc=t")
    try:
        monkeypatch.setattr(directory_sync, "is_directory_backend", lambda: True)
        import app.core.auth_ldap as al
        monkeypatch.setattr(al, "sync_all_groups",
                            lambda name_contains="": {"total_seen": 1})

        def _boom(dn):
            raise RuntimeError("ldap down")
        monkeypatch.setattr(al, "count_group_members", _boom)
        rep = directory_sync.run_sync()
        assert rep.get("counts_failed", 0) >= 1
        assert rep.get("counts_updated") == 0
    finally:
        _rm_group("dsync_fail_g")

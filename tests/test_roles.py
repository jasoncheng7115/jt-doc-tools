"""Tests for app.core.roles.

Test list:
  - seed_builtin_roles inserts all 6 default roles
  - seed_builtin_roles is idempotent (no error if already seeded)
  - admin role intentionally has NO tool grants (special-cased)
  - default-user role does NOT include pdf-fill / pdf-stamp
  - finance / sales / legal-sec include pdf-stamp / pdf-fill / etc
  - create() rejects bad ids (uppercase, special chars)
  - create() rejects duplicate id
  - update() can change tools list
  - update() refuses to rename a protected role
  - delete() refuses protected roles
  - delete() works on user-created roles
"""
from __future__ import annotations

import pytest

from app.core import roles


def test_seed_inserts_all_6(auth_off):
    roles.seed_builtin_roles()
    out = {r["id"]: r for r in roles.list_roles()}
    for rid in ("admin", "default-user", "clerk", "finance", "sales", "legal-sec"):
        assert rid in out, f"missing seed role: {rid}"


def test_seed_idempotent(auth_off):
    roles.seed_builtin_roles()
    roles.seed_builtin_roles()
    out = {r["id"]: r for r in roles.list_roles()}
    assert len(out) >= 6


def test_admin_has_no_explicit_tools(auth_off):
    roles.seed_builtin_roles()
    admin = roles.get("admin")
    assert admin is not None
    # admin's tools list is intentionally empty — special-cased in resolver
    assert admin["tools"] == []
    assert admin["is_builtin"] is True
    assert admin["is_protected"] is True


def test_default_user_excludes_sensitive(auth_off):
    roles.seed_builtin_roles()
    du = roles.get("default-user")
    assert "pdf-fill" not in du["tools"]
    assert "pdf-stamp" not in du["tools"]
    # but should have other tools
    assert "pdf-merge" in du["tools"]


def test_finance_role_has_signing_tools(auth_off):
    roles.seed_builtin_roles()
    finance = roles.get("finance")
    assert "pdf-fill" in finance["tools"]
    assert "pdf-stamp" in finance["tools"]
    assert "pdf-encrypt" in finance["tools"]


def test_legal_sec_has_redaction(auth_off):
    roles.seed_builtin_roles()
    ls = roles.get("legal-sec")
    assert "doc-deident" in ls["tools"]
    assert "pdf-hidden-scan" in ls["tools"]
    assert "pdf-decrypt" in ls["tools"]


@pytest.mark.parametrize("bad_id", [
    "Admin",  # uppercase
    "1clerk",  # leading digit
    "a",  # too short
    "with space",
    "with.dot",
    "x" * 40,  # too long
    "",
])
def test_create_rejects_bad_id(auth_off, bad_id):
    with pytest.raises(ValueError):
        roles.create(bad_id, "顯示名")


def test_create_rejects_duplicate(auth_off):
    roles.seed_builtin_roles()
    with pytest.raises(ValueError):
        roles.create("admin", "Another Admin")


def test_update_changes_tools(auth_off):
    roles.seed_builtin_roles()
    roles.update("clerk", tools=["pdf-merge"])
    assert roles.get("clerk")["tools"] == ["pdf-merge"]


def test_update_refuses_rename_protected(auth_off):
    roles.seed_builtin_roles()
    # Should silently keep name (per impl: skip if protected)
    roles.update("admin", display_name="HACKED")
    assert roles.get("admin")["display_name"] == "管理員"


def test_delete_refuses_protected(auth_off):
    roles.seed_builtin_roles()
    with pytest.raises(ValueError):
        roles.delete("admin")
    with pytest.raises(ValueError):
        roles.delete("default-user")


def test_delete_user_role(auth_off):
    roles.create("temp", "Temp Role")
    assert roles.get("temp") is not None
    roles.delete("temp")
    assert roles.get("temp") is None

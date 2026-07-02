"""Category-based settings export / import (v1.12.54).

Covers per-category selection on BOTH export and import, plus the new RBAC
category (roles / perms / new-user default / OU rules) round-trip.
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.core import settings_export


@pytest.fixture(autouse=True)
def _restore_canonical_roles():
    """The RBAC round-trip tests mutate the shared roles tables (create/delete
    roles, move the new-user default). Restore canonical seeded state on
    teardown so we don't pollute later role tests (test_sso / test_user_manager
    / test_v1_4_99). Runs after data_dir's monkeypatch is undone, so it hits
    the real test auth DB."""
    yield
    try:
        from app.core import roles, auth_db, db
        conn = auth_db.conn()
        with db.tx(conn):
            conn.execute("DELETE FROM subject_roles")
            conn.execute("DELETE FROM subject_perms")
            conn.execute("DELETE FROM role_perms")
            conn.execute("DELETE FROM role_seed_snapshot")
            conn.execute("DELETE FROM roles")
        roles.seed_builtin_roles()
    except Exception:
        pass


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point settings.data_dir at a temp dir so file-category tests don't touch
    the real test data dir."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setattr("app.config.settings.data_dir", d)
    return d


def _write(d: Path, name: str, obj) -> None:
    (d / name).write_text(json.dumps(obj), encoding="utf-8")


# ---------------- export selection ----------------

def test_export_only_selected_category(data_dir, tmp_path):
    _write(data_dir, "auth_settings.json", {"backend": "local"})
    _write(data_dir, "llm_settings.json", {"model": "x"})
    out = tmp_path / "exp.zip"
    res = settings_export.export_to_zip(out, ["auth"], app_version="9.9.9")
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert "data/auth_settings.json" in names
    assert "data/llm_settings.json" not in names  # not selected
    assert [c["id"] for c in res["manifest"]["categories"]] == ["auth"]


def test_read_manifest_lists_categories(data_dir, tmp_path):
    _write(data_dir, "auth_settings.json", {"backend": "local"})
    _write(data_dir, "llm_settings.json", {"model": "x"})
    out = tmp_path / "exp.zip"
    settings_export.export_to_zip(out, ["auth", "llm"], app_version="9.9.9")
    manifest = settings_export.read_manifest(out)
    ids = {c["id"] for c in manifest["categories"]}
    assert ids == {"auth", "llm"}


def test_read_manifest_rejects_non_export(tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("hello.txt", "nope")
    with pytest.raises(ValueError):
        settings_export.read_manifest(bad)


# ---------------- import selection ----------------

def test_import_only_selected_category(data_dir, tmp_path):
    _write(data_dir, "auth_settings.json", {"backend": "local"})
    _write(data_dir, "llm_settings.json", {"model": "orig"})
    out = tmp_path / "exp.zip"
    settings_export.export_to_zip(out, ["auth", "llm"], app_version="9.9.9")
    # Mutate both after export.
    _write(data_dir, "auth_settings.json", {"backend": "MUTATED"})
    _write(data_dir, "llm_settings.json", {"model": "MUTATED"})
    # Import only 'auth' → auth restored, llm stays mutated.
    res = settings_export.import_from_zip(out, ["auth"])
    assert json.loads((data_dir / "auth_settings.json").read_text())["backend"] == "local"
    assert json.loads((data_dir / "llm_settings.json").read_text())["model"] == "MUTATED"
    assert "auth" in res["restored_categories"]
    # A .bak of the overwritten auth_settings.json was made.
    assert any("auth_settings.json.bak." in b for b in res["backup_paths"])


def test_import_zip_slip_rejected(data_dir, tmp_path):
    out = tmp_path / "evil.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(settings_export.MANIFEST_NAME, json.dumps({
            "kind": "jtdt-settings-export", "schema_version": 2,
            "categories": [], "entries_by_category": {}}))
        zf.writestr("data/../escape.txt", "pwn")
    with pytest.raises(ValueError):
        settings_export.import_from_zip(out, None)


# ---------------- RBAC round-trip ----------------

def test_rbac_export_import_roundtrip(auth_off, tmp_path):
    """RBAC category: custom role + new-user default survive export→wipe→import."""
    from app.core import roles, auth_db, db
    # clean roles
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM subject_roles")
        conn.execute("DELETE FROM role_perms")
        conn.execute("DELETE FROM role_seed_snapshot")
        conn.execute("DELETE FROM roles")
    roles.seed_builtin_roles()
    roles.create("accountant", "會計", tools=["pdf-merge", "pdf-split"])
    roles.set_default_role_id("accountant")
    # OU rule (portable subject_key)
    from app.core import permissions
    permissions.set_subject_roles("ou", "OU=Sales,DC=x", ["accountant"])

    out = tmp_path / "rbac.zip"
    settings_export.export_to_zip(out, ["rbac"], app_version="9.9.9")
    with zipfile.ZipFile(out) as zf:
        assert settings_export.RBAC_NAME in zf.namelist()

    # Wipe the custom role + move default away.
    roles.set_default_role_id("default-user")
    roles.delete("accountant")
    assert roles.get("accountant") is None

    # Import RBAC back.
    res = settings_export.import_from_zip(out, ["rbac"])
    assert res["rbac"]["roles"] >= 7
    got = roles.get("accountant")
    assert got is not None
    assert set(got["tools"]) == {"pdf-merge", "pdf-split"}
    assert roles.get_default_role_id() == "accountant"
    # OU rule restored
    ou_roles = permissions.list_roles_for_subject("ou", "OU=Sales,DC=x")
    assert "accountant" in ou_roles


def test_rbac_excludes_users(auth_off, tmp_path):
    """The RBAC dump must never contain user rows / password hashes."""
    dump = settings_export._rbac_dump()
    assert set(dump.keys()) == {
        "roles", "role_perms", "role_seed_snapshot",
        "ou_subject_roles", "ou_subject_perms"}
    blob = json.dumps(dump)
    assert "password" not in blob.lower()

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

def test_import_only_selected_category(data_dir, tmp_path, monkeypatch):
    # 這一條驗「只還原勾選的類別」；這台要有本機管理員，認證設定才會套用
    # （沒有的話匯入會擋下來 —— 見 test_settings_export_identity.py）
    monkeypatch.setattr(settings_export, "_has_local_admin", lambda: True)
    _write(data_dir, "auth_settings.json", {"backend": "local"})
    _write(data_dir, "llm_settings.json", {"model": "orig"})
    out = tmp_path / "exp.zip"
    settings_export.export_to_zip(out, ["auth", "llm"], app_version="9.9.9")
    # Mutate both after export.
    _write(data_dir, "auth_settings.json", {"backend": "MUTATED"})
    _write(data_dir, "llm_settings.json", {"model": "MUTATED"})
    # Import only 'auth' → auth restored, llm stays mutated.
    res = settings_export.import_from_zip(out, ["auth"])
    assert json.loads((data_dir / "auth_settings.json").read_text(encoding="utf-8"))["backend"] == "local"
    assert json.loads((data_dir / "llm_settings.json").read_text(encoding="utf-8"))["model"] == "MUTATED"
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


def test_rbac_import_cannot_escalate(auth_off, tmp_path):
    """A crafted backup must NOT be able to: set admin as new-user default,
    grant admin to an OU, or plant an undeletable protected/builtin role."""
    from app.core import roles, auth_db, db
    conn = auth_db.conn()
    with db.tx(conn):
        conn.execute("DELETE FROM subject_roles")
        conn.execute("DELETE FROM subject_perms")
        conn.execute("DELETE FROM role_perms")
        conn.execute("DELETE FROM role_seed_snapshot")
        conn.execute("DELETE FROM roles")
    roles.seed_builtin_roles()

    malicious = {
        "roles": [
            {"id": "admin", "display_name": "管理員", "is_default_for_new": True},
            {"id": "evil", "display_name": "evil", "is_builtin": 1,
             "is_protected": 1, "is_default_for_new": True},
        ],
        "role_perms": [],
        "role_seed_snapshot": [],
        "ou_subject_roles": [["DC=corp,DC=com", "admin"]],
        "ou_subject_perms": [],
    }
    out = tmp_path / "evil.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(settings_export.MANIFEST_NAME, json.dumps({
            "kind": "jtdt-settings-export", "schema_version": 2,
            "categories": [{"id": "rbac"}],
            "entries_by_category": {"rbac": [settings_export.RBAC_NAME]}}))
        zf.writestr(settings_export.RBAC_NAME, json.dumps(malicious))
    settings_export.import_from_zip(out, ["rbac"])

    # admin/auditor never becomes the new-user default
    assert roles.get_default_role_id() not in ("admin", "auditor")
    # the imported "evil" role is created but NOT protected/builtin
    evil = roles.get("evil")
    assert evil is not None
    assert evil["is_protected"] is False and evil["is_builtin"] is False
    # no OU→admin grant landed
    from app.core import permissions
    assert "admin" not in permissions.list_roles_for_subject("ou", "DC=corp,DC=com")


def test_rbac_excludes_users(auth_off, tmp_path):
    """The RBAC dump must never contain user rows / password hashes."""
    dump = settings_export._rbac_dump()
    # v1.16.66 起多帶個人與群組的指派（鍵是編號，匯入時靠 identity.json 換成那一台的編號）
    assert set(dump.keys()) == {
        "roles", "role_perms", "role_seed_snapshot",
        "ou_subject_roles", "ou_subject_perms",
        "user_subject_roles", "group_subject_roles",
        "user_subject_perms", "group_subject_perms"}
    blob = json.dumps(dump)
    assert "password" not in blob.lower()


# ---------------- v1.14.6：補齊缺漏的設定分類 ----------------
# 這批分類是「設定備份」長期漏掉的東西（SSO / 記錄轉送 / 保留策略 / OCR …）。
# 漏掉時不會有任何錯誤訊息，只有客戶搬機還原後才會發現設定不見了 —— 所以這裡
# 逐項釘住，並且**特別測 SSO 的跨機還原**（那項不只是漏，是還原了也不能用）。

_NEW_CATEGORY_ITEMS = {
    "sso": "sso_settings.json",
    "directory": "directory_sync.json",
    "log_forward": "log_forwarders.json",
    "retention": "retention.json",
    "scheduled_export": "scheduled_export.json",
    "ocr": "ocr_settings.json",
    "mfp": "mfp_settings.json",          # v1.16.89：允許使用者自行新增事務機
    "update_check": "update_check.json",  # v1.17.47：每天自動檢查新版
}


@pytest.mark.parametrize("cat_id,filename", sorted(_NEW_CATEGORY_ITEMS.items()))
def test_new_category_exports_its_file(data_dir, tmp_path, cat_id, filename):
    _write(data_dir, filename, {"probe": cat_id})
    out = tmp_path / "out.zip"
    settings_export.export_to_zip(out, selected_ids=[cat_id])
    with zipfile.ZipFile(out) as zf:
        assert f"data/{filename}" in zf.namelist()


def test_auth_category_no_longer_claims_sso():
    """舊版「認證設定」的說明寫著 OIDC / SAML，檔案卻沒包 —— 使用者勾了以為有
    備份，實際沒有。說明與內容必須一致。"""
    auth = next(c for c in settings_export.CATEGORIES if c["id"] == "auth")
    assert "sso_settings.json" not in auth["items"]
    assert "OIDC" not in auth["desc"] and "SAML" not in auth["desc"]
    sso = next(c for c in settings_export.CATEGORIES if c["id"] == "sso")
    assert "OIDC" in sso["desc"] and sso.get("sensitive") is True


def test_ocr_remote_token_category_marked_sensitive():
    """遠端 OCR 設定含 bearer token，UI 要標示為敏感。"""
    ocr = next(c for c in settings_export.CATEGORIES if c["id"] == "ocr")
    assert "ocr_remote.json" in ocr["items"]
    assert ocr.get("sensitive") is True


@pytest.mark.parametrize("cat_id", ["workspace", "scan_buffers",
                                    "history_fill", "history_stamp"])
def test_bulk_user_data_is_opt_in(cat_id):
    """使用者資料量大，預設不勾（搬設定 ≠ 搬全部檔案）。"""
    cat = next(c for c in settings_export.CATEGORIES if c["id"] == cat_id)
    assert cat.get("default") is False


def test_session_secret_never_exported(data_dir, tmp_path):
    """`.session_secret` 是 session 簽章金鑰，進了備份檔等於可偽造登入。
    不管勾選哪些分類都不可以出現。"""
    (data_dir / ".session_secret").write_bytes(b"x" * 32)
    out = tmp_path / "out.zip"
    # rbac 分類讀的是 auth.sqlite，不在這個臨時 data_dir 內 → 只勾檔案 / 目錄類
    settings_export.export_to_zip(
        out, selected_ids=[c["id"] for c in settings_export.CATEGORIES
                           if c["kind"] != "rbac"])
    with zipfile.ZipFile(out) as zf:
        assert not [n for n in zf.namelist() if ".session_secret" in n]


# ---------------- SSO 祕密跨機還原 ----------------

def _fake_sso_file(d: Path, monkeypatch, secret_key: bytes, client_secret: str):
    """用指定的 .session_secret 產生一份「已加密」的 sso_settings.json。"""
    from app.core import sso_settings
    monkeypatch.setattr("app.core.auth_settings._ensure_secret",
                        lambda: secret_key)
    sso_settings._invalidate_cache()
    enc = sso_settings.encrypt_secret(client_secret)
    (d / "sso_settings.json").write_text(json.dumps({
        "oidc": {"enabled": True, "client_id": "abc",
                 "client_secret_enc": enc},
        "saml": {"enabled": False, "sp_private_key_enc": ""},
    }), encoding="utf-8")
    return enc


def test_sso_secret_survives_machine_change(data_dir, tmp_path, monkeypatch):
    """**這是 SSO 分類的重點**：secret 是用本機 .session_secret 加密的，直接複製
    檔案到另一台機器會解不開 —— 設定看起來都在，SSO 卻無聲失效。匯出要解密、
    匯入要用目標機器的金鑰重新加密。"""
    from app.core import sso_settings

    key_a, key_b = b"A" * 32, b"B" * 32
    enc_a = _fake_sso_file(data_dir, monkeypatch, key_a, "s3cr3t-value")

    out = tmp_path / "out.zip"
    settings_export.export_to_zip(out, selected_ids=["sso"])

    # 備份檔內應是明文（與既有的 LDAP 密碼 / API token 同級，分類已標 sensitive）
    with zipfile.ZipFile(out) as zf:
        blob = json.loads(zf.read("data/sso_settings.json").decode())
    assert blob["oidc"]["client_secret_enc"] == "s3cr3t-value"
    assert blob[settings_export._SSO_PLAINTEXT_KEY] is True

    # 換一台機器（不同 .session_secret）還原
    (data_dir / "sso_settings.json").unlink()
    monkeypatch.setattr("app.core.auth_settings._ensure_secret", lambda: key_b)
    sso_settings._invalidate_cache()
    settings_export.import_from_zip(out, selected_ids=["sso"])

    restored = json.loads((data_dir / "sso_settings.json").read_text(encoding="utf-8"))
    assert settings_export._SSO_PLAINTEXT_KEY not in restored, "明文標記要移除"
    ct = restored["oidc"]["client_secret_enc"]
    assert ct != "s3cr3t-value", "落地時必須是密文，不可留明文"
    assert ct != enc_a, "應該用新機器的金鑰重新加密"
    assert sso_settings.decrypt_secret(ct) == "s3cr3t-value"


def test_legacy_sso_backup_without_marker_is_left_alone(data_dir, tmp_path,
                                                        monkeypatch):
    """v1.14.5 以前匯出的備份裡是**別台機器的密文**，本機解不開 —— 原樣保留讓
    管理員重新輸入即可，不可試圖解密（會變成空字串，設定看似存在卻是壞的）。"""
    from app.core import sso_settings

    monkeypatch.setattr("app.core.auth_settings._ensure_secret",
                        lambda: b"B" * 32)
    sso_settings._invalidate_cache()
    legacy = {"oidc": {"client_secret_enc": "gAAAAA-other-machine-ciphertext"}}
    out = tmp_path / "legacy.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("data/sso_settings.json", json.dumps(legacy))
        zf.writestr(settings_export.MANIFEST_NAME, json.dumps({
            "kind": "jtdt-settings-export", "schema_version": 2,
            "categories": [{"id": "sso", "label": "SSO"}],
            "entries_by_category": {"sso": ["data/sso_settings.json"]}}))
    settings_export.import_from_zip(out, selected_ids=["sso"])
    got = json.loads((data_dir / "sso_settings.json").read_text(encoding="utf-8"))
    assert got["oidc"]["client_secret_enc"] == "gAAAAA-other-machine-ciphertext"


# ---------------- 涵蓋度檢查（發版 gate 的單元測試化）----------------

def test_every_settings_file_is_in_some_category():
    """把 tools/check_settings_export_coverage.py 拉進測試 —— 加新設定檔卻忘記
    登記時，pytest 就會紅，不必等到發版前才想起要跑那支腳本。"""
    import sys
    from pathlib import Path as _P
    root = _P(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from tools import check_settings_export_coverage as chk

    cov = chk.declared_coverage()
    missing = [n for n in chk.code_references()
               if n.split("/")[0] not in chk.EXEMPT and n not in chk.EXEMPT
               and not chk.is_covered(n, cov)]
    assert not missing, (
        f"這些設定檔沒被『設定備份 / 匯入』涵蓋：{missing}。"
        "請加進 app/core/settings_export.py 的 CATEGORIES，"
        "或加進 tools/check_settings_export_coverage.py 的 EXEMPT 並寫明理由。")

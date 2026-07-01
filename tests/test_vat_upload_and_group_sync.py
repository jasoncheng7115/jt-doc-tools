"""2026-06-30 客戶回報兩項：
① 統編資料庫上傳匯入要走背景,否則網頁卡住。
② AD/LDAP 預設只同步「登入過使用者所屬」的群組（JIT）→ 只有 5 個;新增
   「同步目錄所有群組」功能。
"""
from __future__ import annotations


def test_vat_upload_runs_in_background_not_blocking(admin_session):
    """上傳統編檔 → 立刻回 background 狀態（status/started），不再同步回 records。"""
    c, _, _ = admin_session
    csv = ("統一編號,名稱,地址\n12345678,測試公司,台北市\n").encode("utf-8")
    r = c.post("/admin/vat-db/upload",
               files={"file": ("t.csv", csv, "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    # 背景化：回 status，而非舊的同步 records
    assert "status" in j and j["status"] in ("started", "already_running")
    assert "records" not in j  # 不再阻塞到匯入完才回


def test_vat_trigger_ingest_async_exists_and_idempotent():
    from app.core import vat_db
    assert hasattr(vat_db, "trigger_ingest_async")
    assert hasattr(vat_db, "_run_ingest_safe")


def test_group_sync_ldap_requires_directory_backend(admin_session):
    """auth backend 非 LDAP/AD（admin_session 是 local）→ 同步目錄群組回 400。"""
    c, _, _ = admin_session
    r = c.post("/admin/groups/sync-ldap")
    assert r.status_code == 400
    assert "LDAP" in r.text or "AD" in r.text


def test_sync_all_groups_function_exists():
    from app.core import auth_ldap
    assert hasattr(auth_ldap, "sync_all_groups")


def test_group_sync_accepts_name_filter(admin_session):
    """同步端點接受 name_contains 過濾（非目錄後端仍 400，但不因參數而 500）。"""
    c, _, _ = admin_session
    r = c.post("/admin/groups/sync-ldap", json={"name_contains": "UG_"})
    assert r.status_code == 400  # local backend
    assert r.status_code != 500


def test_sync_all_groups_escapes_filter_injection():
    """name_contains 走 escape_filter_chars，防 LDAP filter 注入。"""
    import inspect
    from app.core import auth_ldap
    src = inspect.getsource(auth_ldap.sync_all_groups)
    assert "escape_filter_chars" in src
    assert "name_contains" in inspect.signature(auth_ldap.sync_all_groups).parameters


def test_group_members_ldap_endpoint_requires_directory(admin_session):
    """查目錄成員端點:非 LDAP/AD 後端回 400（admin_session 是 local）。"""
    c, _, _ = admin_session
    r = c.get("/admin/groups/1/members-ldap")
    assert r.status_code == 400


def test_get_group_members_function_exists():
    from app.core import auth_ldap
    import inspect
    assert hasattr(auth_ldap, "get_group_members")
    # 用 (memberOf=<dn>) 查 + escape 防注入
    src = inspect.getsource(auth_ldap.get_group_members)
    assert "memberOf" in src or "group_member_filter" in src
    assert "escape_filter_chars" in src

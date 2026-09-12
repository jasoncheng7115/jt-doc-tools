"""管理員的隱私界線要是**一份**政策（F10，v1.15.28）。

外部稽核指出：上傳檔 / 預覽那條路（`upload_owner.check`）管理員**直接放行**，
但背景作業產出那條路（`main._job_access`）**嚴格比對擁有者、管理員也不行**。
同一份文件，走哪條路決定管理員看不看得到 —— 「管理員看不到使用者隱私資料」
這句話因此只在部分範圍成立。

這裡不替產品決定政策，而是把政策**收成一個常數、兩條路共用**，
並要求越權讀取一定留下稽核。要改政策就是改那個常數，
對應的測試會告訴你哪些行為跟著變。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.core import upload_owner as uo


def test_the_policy_is_a_single_named_decision():
    assert isinstance(uo.ADMIN_MAY_READ_USER_FILES, bool), (
        "政策要是一個看得到、改得動的常數")


def test_both_paths_go_through_the_same_helper():
    """判準走 AST：兩條路都要呼叫 `admin_override_allowed`。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    hits = {}
    for rel in ("app/core/upload_owner.py", "app/main.py"):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        hits[rel] = [n for n in ast.walk(tree)
                     if isinstance(n, ast.Call)
                     and (getattr(n.func, "attr", "") == "admin_override_allowed"
                          or getattr(n.func, "id", "") == "admin_override_allowed")]
    for rel, calls in hits.items():
        assert calls, f"{rel} 沒有走共用的管理員政策判斷"


def test_an_override_is_audited(monkeypatch):
    """管理員讀別人的檔案可以放行，但**必須查得到是誰讀了誰的**。"""
    logged = []
    import app.core.audit_db as adb
    monkeypatch.setattr(adb, "log_event",
                        lambda ev, target="", details=None, **kw:
                        logged.append((ev, target, details)))
    uo._OVERRIDE_LOGGED.clear()
    allowed = uo.admin_override_allowed(7, "upload:abc", owner_id=3)
    assert allowed is uo.ADMIN_MAY_READ_USER_FILES
    if allowed:
        assert logged and logged[0][0] == "admin_file_override"
        assert logged[0][2]["owner_id"] == 3
        assert logged[0][2]["admin_user_id"] == 7


def test_repeated_access_to_the_same_resource_is_not_logged_every_time(monkeypatch):
    """一頁縮圖會打幾十個請求 —— 逐個寫稽核等於把稽核洗掉。"""
    logged = []
    import app.core.audit_db as adb
    monkeypatch.setattr(adb, "log_event",
                        lambda ev, target="", details=None, **kw: logged.append(ev))
    uo._OVERRIDE_LOGGED.clear()
    for _ in range(30):
        uo.admin_override_allowed(7, "upload:same", owner_id=3)
    if uo.ADMIN_MAY_READ_USER_FILES:
        assert len(logged) == 1, f"同一個資源被記了 {len(logged)} 筆"
    # 不同資源要各記一筆（不可以整批吞掉）
    uo.admin_override_allowed(7, "upload:other", owner_id=3)
    if uo.ADMIN_MAY_READ_USER_FILES:
        assert len(logged) == 2


def test_turning_the_policy_off_denies_and_does_not_audit(monkeypatch):
    """政策關掉時要真的拒絕（而且沒有東西可稽核）。"""
    logged = []
    import app.core.audit_db as adb
    monkeypatch.setattr(adb, "log_event",
                        lambda *a, **k: logged.append(a))
    monkeypatch.setattr(uo, "ADMIN_MAY_READ_USER_FILES", False)
    assert uo.admin_override_allowed(7, "upload:abc", owner_id=3) is False
    assert not logged


def test_reading_your_own_file_is_not_an_override(monkeypatch, tmp_path):
    """管理員讀自己的上傳不算越權 —— 不可以每次都寫一筆稽核。"""
    import json
    logged = []
    import app.core.audit_db as adb
    monkeypatch.setattr(adb, "log_event", lambda *a, **k: logged.append(a))
    monkeypatch.setattr(uo, "_auth_enabled", lambda: True)
    monkeypatch.setattr(uo, "_is_admin", lambda uid: True)
    monkeypatch.setattr(uo, "_user_id", lambda request: 7)
    owners = tmp_path / "owners"
    owners.mkdir()
    monkeypatch.setattr(uo, "_owners_dir", lambda: owners)
    uid = "a" * 32
    (owners / f"{uid}.json").write_text(json.dumps({"user_id": 7}), encoding="utf-8")

    assert uo.check(uid, None) is True
    assert not logged, "讀自己的檔案被記成越權了"

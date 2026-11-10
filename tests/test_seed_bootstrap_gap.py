"""新工具要真的到得了**既有客戶**，不是只有全新安裝看得到。

## 由來

`seed_builtin_roles()` 的 top-up 靠 `role_seed_snapshot` 當基準線：只補
「這一版 seed 比快照多出來的」工具，這樣 admin 刻意移除的東西才不會被升級補回去。

問題出在**首次 bootstrap**：快照表是 v1.12.53 才落地的。從 v1.12.52 或更早直接
升上來的安裝，快照是空的 → 走保守路徑（**這一輪什麼都不補**，只把當前 seed 定義
寫成基準線）→ 那些客戶錯過的工具，**下一次升級的差集也是空的**，等於永遠不會出現，
而且畫面上沒有任何線索說明原因。

當時 `pdf-to-slides` 補了一條 backfill migration（`_m13`），但 `transit-proof`
（v1.12.74）與 `pdf-border`（v1.14.11）都漏了 —— 盤點時才發現。`_m18` 補上。

## 這一份守什麼

不是釘住那兩個工具的名字（下一支新工具還是會漏），而是**直接模擬那個升級情境**：
角色已存在、快照被清空、新工具不在 role_perms —— 跑完整條 migration 鏈 +
`seed_builtin_roles()` 之後，每一支註冊中的工具都要有人拿得到。
"""
from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def legacy_install(monkeypatch):
    """一份「角色已存在、快照空白」的資料庫 —— 就是舊安裝升級當下的狀態。"""
    monkeypatch.setenv("JTDT_DATA_DIR", tempfile.mkdtemp())
    from app.config import settings
    monkeypatch.setattr(settings, "data_dir",
                        __import__("pathlib").Path(os.environ["JTDT_DATA_DIR"]))
    from app.core import auth_db, db, roles
    auth_db._CONN = None if hasattr(auth_db, "_CONN") else None
    auth_db.init()
    roles.seed_builtin_roles()
    return auth_db, db, roles


def _registered_tool_ids() -> set[str]:
    from app.tool_registry import discover_tools
    return {t.metadata.id for t in discover_tools()}


def _granted_tool_ids(conn) -> set[str]:
    return {r["tool_id"] for r in conn.execute(
        "SELECT DISTINCT tool_id FROM role_perms WHERE role_id <> 'admin'")}


def test_every_registered_tool_reaches_a_fresh_install(legacy_install):
    """全新安裝一定要每支工具都有人拿得到（這是基準線，壞了先看這一條）。"""
    auth_db, _, _ = legacy_install
    from app.core.roles import ADMIN_ONLY_TOOL_IDS        # 刻意只給管理員的（理由寫在 roles.py）
    missing = _registered_tool_ids() - _granted_tool_ids(auth_db.conn()) - set(ADMIN_ONLY_TOOL_IDS)
    assert not missing, f"全新安裝就有工具沒人拿得到：{sorted(missing)}"


def test_every_registered_tool_reaches_a_legacy_install(legacy_install):
    """**這就是原本壞掉的情境。**

    模擬從 v1.12.52 之前直接升上來：快照空白、新工具還不在 role_perms。
    跑完整條 migration 鏈 + top-up 之後，每一支工具都要有人拿得到。

    修之前 `transit-proof` 與 `pdf-border` 會留在缺漏清單裡。
    """
    auth_db, db, roles = legacy_install
    conn = auth_db.conn()
    tools = _registered_tool_ids()

    # 把「這個客戶當年還沒有」的工具與快照一起清掉
    with db.tx(conn):
        conn.execute("DELETE FROM role_seed_snapshot")
        for t in ("transit-proof", "pdf-border", "pdf-to-slides"):
            conn.execute("DELETE FROM role_perms WHERE tool_id=?", (t,))

    # 升級：整條 migration 鏈（backfill 就在裡面）+ 啟動時的 top-up
    for fn in auth_db.MIGRATIONS:
        if fn.__name__.startswith(("_m1_", "_m2_")):
            continue          # 建表 / 重建表的不重跑
        try:
            fn(conn)
        except Exception:     # noqa: BLE001 — 已套用過的 ALTER 會抱怨，略過
            pass
    conn.commit()
    roles.seed_builtin_roles()

    from app.core.roles import ADMIN_ONLY_TOOL_IDS        # 刻意只給管理員的（理由寫在 roles.py）
    missing = tools - _granted_tool_ids(conn) - set(ADMIN_ONLY_TOOL_IDS)
    assert not missing, (
        f"這些工具在既有客戶升級後沒有任何角色拿得到：{sorted(missing)}\n"
        "新工具要嘛在 v1.12.53 之後的 seed 差集裡，要嘛需要一條 backfill "
        "migration（見 _m13 / _m18 的寫法）。")


def test_backfill_is_idempotent(legacy_install):
    """migration 會被重跑（例如手動修復），重跑不可以改變結果。"""
    auth_db, _, _ = legacy_install
    conn = auth_db.conn()
    auth_db._m18_grant_transit_proof_and_border(conn)
    first = _granted_tool_ids(conn)
    auth_db._m18_grant_transit_proof_and_border(conn)
    assert _granted_tool_ids(conn) == first


def test_backfill_does_not_widen_narrowed_roles(legacy_install):
    """補的對象要跟 seed 定義一致，不可以無條件補給所有角色。

    無條件補會把 admin 刻意收窄過的角色一起放寬 —— 那是安全回歸。
    """
    auth_db, db, roles = legacy_install
    from app.core.roles import SEED_ROLES
    conn = auth_db.conn()
    with db.tx(conn):
        for t in ("transit-proof", "pdf-border"):
            conn.execute("DELETE FROM role_perms WHERE tool_id=?", (t,))
    auth_db._m18_grant_transit_proof_and_border(conn)
    conn.commit()
    for tool in ("transit-proof", "pdf-border"):
        got = {r["role_id"] for r in conn.execute(
            "SELECT role_id FROM role_perms WHERE tool_id=?", (tool,))}
        want = {r["id"] for r in SEED_ROLES if tool in r["tools"]} - {"admin"}
        assert got == want, f"{tool} 補到的角色與 seed 定義不符：{got} vs {want}"


def test_legacy_role_description_is_refreshed(legacy_install):
    """舊安裝的內建角色說明要在升級時換成這一版的 —— 但**管理員改過的不動**。

    `seed_builtin_roles()` 對既有角色只補新工具，說明從來沒更新過，於是
    finance / sales / legal-sec 在所有既有安裝上顯示的都還是當初那段
    **描述了它其實沒有的權限**的文字（那正是它被改掉的原因），而且不在語系檔裡、
    英文介面翻不出來（2026-09-06 使用者截圖回報）。
    """
    from app.core import auth_db, roles

    legacy = sorted(roles._LEGACY_DESCRIPTIONS["finance"])[0]
    conn = auth_db.conn()
    # ① 舊出廠值 → 應該被換掉
    conn.execute("UPDATE roles SET description=? WHERE id='finance'", (legacy,))
    # ② 管理員自己寫的 → 一個字都不可以動
    conn.execute("UPDATE roles SET description=? WHERE id='sales'",
                 ("本公司業務部專用，問陳經理",))
    conn.commit()

    roles.seed_builtin_roles()

    got = {r["id"]: r["description"] for r in roles.list_roles()}
    seeded = {r["id"]: r["description"] for r in roles.SEED_ROLES}
    assert got["finance"] == seeded["finance"], "舊出廠說明沒有被更新"
    assert got["sales"] == "本公司業務部專用，問陳經理", "管理員改過的說明被蓋掉了"

"""內建角色（RBAC）的完整性檢查。

## 這份要擋的三類問題

1. **角色說明與實際授權不符**。`legal-sec` 原本寫成
   `_NON_ADMIN_TOOL_IDS + [七個工具]`，而那七個**全部**已經在一般使用者裡 ——
   指派「法務資安」與指派「一般使用者」完全等價（39 個工具）。名稱與說明卻讓人
   以為它是收窄的專用角色，管理員照著指派就會意外給出遠超預期的權限。
   `finance` / `sales` 的說明同樣把「浮水印 / 加密 / 去識別化」寫成特色，那些也
   都在一般使用者裡，真正多的只有表單填寫與用印簽名兩個敏感工具。

2. **新工具沒有傳到既有客戶**。`seed_builtin_roles()` 只在有
   `role_seed_snapshot` 基準線時才補新工具；從 v1.12.52 或更早直接升上來的安裝
   快照是空的 → 走保守 bootstrap → 什麼都不補。所以每次新增工具都要有一條
   backfill migration（m4 / m5 / m13 的作法）。

3. **工具 id 打錯**。角色裡授權一個不存在的 tool_id 不會有任何錯誤，只是那個
   權限永遠沒有作用 —— 從畫面上完全看不出來。
"""
from __future__ import annotations

import pytest

from app.core.roles import SEED_ROLES, _NON_ADMIN_TOOL_IDS


def _known_tool_ids() -> set[str]:
    """實際載入的工具 id（與執行中的服務一致，不是另抄一份清單）。"""
    from app import tool_registry
    return {t.metadata.id for t in tool_registry.discover_tools()}


def _by_id():
    return {r["id"]: r for r in SEED_ROLES}


def _extras(role_id: str) -> set[str]:
    """這個角色比「一般使用者」多授權了哪些工具。"""
    return set(_by_id()[role_id]["tools"]) - set(_NON_ADMIN_TOOL_IDS)


# ---------- 1. 名實相符 ----------

def test_no_role_is_a_silent_clone_of_default_user():
    """除了 default-user 自己，沒有角色可以「剛好等於一般使用者」。

    那種角色指派下去等於沒設定，而管理員會以為自己收窄了權限。
    """
    base = set(_NON_ADMIN_TOOL_IDS)
    for r in SEED_ROLES:
        if r["id"] in ("default-user", "admin", "auditor"):
            continue
        assert set(r["tools"]) != base, (
            f"角色 {r['id']} 的授權與「一般使用者」完全相同 —— "
            f"指派它不會有任何效果，但名稱看起來是專用角色")


def test_roles_claiming_more_than_default_actually_grant_more():
    """說明寫「一般使用者的全部工具，另加 X」的角色，必須真的多授權 X。"""
    for r in SEED_ROLES:
        if "一般使用者的全部工具" not in r["description"]:
            continue
        assert _extras(r["id"]), (
            f"{r['id']} 的說明宣稱是一般使用者的超集另加工具，實際沒有多任何工具")


def test_finance_and_sales_extras_are_only_the_sensitive_ones():
    """財務 / 業務多出來的只有那幾個敏感工具 —— 多出別的要有人明確決定。

    v1.14.20 加入 `pdf-seam-stamp`（騎縫章）：它就是用印的一種（把公司章蓋在
    跨頁上），敏感度與 `pdf-stamp` 相同，所以**跟著 pdf-stamp 一起走**，不放進
    一般使用者。第一版誤放進 `_NON_ADMIN_TOOL_IDS`，變成「一般使用者蓋不了章
    卻蓋得了騎縫章」—— 這條測試當場擋下來。
    """
    for rid in ("finance", "sales"):
        assert _extras(rid) == {"pdf-fill", "pdf-stamp", "pdf-seam-stamp"}, \
            f"{rid} 多出來的是 {_extras(rid)}"


def test_legal_sec_is_actually_narrow():
    """法務資安要是窄角色（否則名稱與說明會誤導管理員）。"""
    tools = set(_by_id()["legal-sec"]["tools"])
    assert len(tools) < len(_NON_ADMIN_TOOL_IDS), "法務資安不該是一般使用者的超集"
    for must in ("doc-deident", "pdf-hidden-scan", "pdf-metadata", "doc-diff",
                 "pdf-encrypt", "pdf-decrypt"):
        assert must in tools, f"法務資安少了說明裡承諾的 {must}"


def test_sensitive_tools_not_in_default_user():
    """表單填寫 / 用印簽名不可以進一般使用者（那是刻意的分界）。"""
    for t in ("pdf-fill", "pdf-stamp"):
        assert t not in _NON_ADMIN_TOOL_IDS, f"{t} 不該授權給所有人"


def test_no_duplicate_tool_ids_in_any_role():
    """重複列出同一個工具不會壞掉，但會讓人誤以為那是該角色的特色。"""
    for r in SEED_ROLES:
        dup = len(r["tools"]) - len(set(r["tools"]))
        assert dup == 0, f"{r['id']} 有 {dup} 個重複的工具 id"


# ---------- 2. 工具 id 必須真的存在 ----------

def test_every_granted_tool_id_exists():
    """打錯的 tool_id 不會報錯，只會靜靜地沒有作用。"""
    known = _known_tool_ids()
    for r in SEED_ROLES:
        for t in r["tools"]:
            assert t in known, f"角色 {r['id']} 授權了不存在的工具 {t}"


from app.core.roles import ADMIN_ONLY_TOOL_IDS as _ADMIN_ONLY_BY_DESIGN  # 刻意只給管理員的工具（理由寫在 roles.py）


def test_every_non_admin_tool_is_granted_to_someone():
    """每個工具至少要在一個非 admin 角色裡，否則只有管理員看得到（刻意的列在 _ADMIN_ONLY_BY_DESIGN）。"""
    granted = set()
    for r in SEED_ROLES:
        if r["id"] == "admin":
            continue
        granted |= set(r["tools"])
    missing = sorted(_known_tool_ids() - granted - set(_ADMIN_ONLY_BY_DESIGN))
    assert not (set(_ADMIN_ONLY_BY_DESIGN) & granted), "列在 _ADMIN_ONLY_BY_DESIGN 的工具不可以出現在一般角色裡"
    # pdf-fill / pdf-stamp 在 finance / sales 裡，所以這裡應為空
    assert not missing, f"這些工具沒有任何非 admin 角色可用：{missing}"


# ---------- 3. 新工具要有 backfill migration ----------

def test_pdf_to_slides_has_backfill_migration():
    """v1.14.0 的新工具要能傳到「快照為空」的舊安裝（見 _m13 的說明）。"""
    from app.core import auth_db
    names = [f.__name__ for f in auth_db.MIGRATIONS]
    assert any("pdf_to_slides" in n for n in names), \
        "缺少 pdf-to-slides 的 backfill migration"


def test_migrations_are_registered_in_order():
    """migration 清單的編號要連續 —— 漏掛一條的後果是那一版的修正永不執行。"""
    import re
    from app.core import auth_db
    nums = [int(re.match(r"_m(\d+)_", f.__name__).group(1))
            for f in auth_db.MIGRATIONS]
    assert nums == sorted(nums), f"順序亂了：{nums}"
    assert nums == list(range(1, len(nums) + 1)), f"編號不連續：{nums}"


def test_backfill_grants_pdf_to_slides_to_upgrading_install(tmp_path):
    """真的模擬「舊安裝升級」：只有 pdf-to-office 的角色升級後要拿到轉簡報檔。

    fresh install 測不到這件事（表本來就是照新 seed 建的），一定要造一個舊狀態。
    """
    import sqlite3
    from app.core import auth_db
    dbp = tmp_path / "auth.sqlite"
    conn = sqlite3.connect(dbp)
    conn.executescript("""
        CREATE TABLE role_perms (role_id TEXT, tool_id TEXT,
                                 PRIMARY KEY(role_id, tool_id));
        CREATE TABLE subject_perms (subject_type TEXT, subject_key TEXT,
                                    tool_id TEXT,
                                    PRIMARY KEY(subject_type, subject_key, tool_id));
        INSERT INTO role_perms VALUES ('default-user', 'pdf-to-office');
        INSERT INTO role_perms VALUES ('clerk', 'pdf-to-office');
        INSERT INTO role_perms VALUES ('narrow-role', 'pdf-merge');
        INSERT INTO subject_perms VALUES ('user', '7', 'pdf-to-office');
    """)
    conn.commit()
    auth_db._m13_grant_pdf_to_slides(conn)
    got = {(r[0], r[1]) for r in conn.execute(
        "SELECT role_id, tool_id FROM role_perms WHERE tool_id='pdf-to-slides'")}
    assert ("default-user", "pdf-to-slides") in got
    assert ("clerk", "pdf-to-slides") in got
    # 刻意收窄過的角色不可以被放寬
    assert ("narrow-role", "pdf-to-slides") not in got
    subj = {(r[0], r[1]) for r in conn.execute(
        "SELECT subject_key, tool_id FROM subject_perms "
        "WHERE tool_id='pdf-to-slides'")}
    assert ("7", "pdf-to-slides") in subj
    # 可重複執行
    auth_db._m13_grant_pdf_to_slides(conn)
    n = conn.execute("SELECT COUNT(*) FROM role_perms "
                     "WHERE tool_id='pdf-to-slides'").fetchone()[0]
    assert n == 2
    conn.close()


# ---------- 4. 保護旗標 ----------

@pytest.mark.parametrize("rid", ["admin", "default-user", "auditor"])
def test_critical_roles_are_protected(rid):
    """這三個被刪掉會造成無法復原的狀態（沒人是管理員 / 新使用者沒角色）。"""
    assert _by_id()[rid]["is_protected"] is True


def test_admin_role_has_no_explicit_tools():
    """admin 走 resolver 短路（空清單 = 全部）—— 列具體工具反而會變成限制。"""
    assert _by_id()["admin"]["tools"] == []


def test_auditor_has_no_tools():
    """稽核員是唯讀角色，不可以有任何工具權限（職責分離）。"""
    assert _by_id()["auditor"]["tools"] == []


def test_legacy_builtin_descriptions_get_refreshed_on_upgrade():
    """升級時要把**以前出廠的**內建角色說明換成這一版的。

    `seed_builtin_roles()` 對既有角色只做「補新工具」，**說明從來沒有被更新**
    —— 於是 finance / sales / legal-sec 三個角色在所有既有安裝上，
    顯示的還是當初那段**描述了它其實沒有的權限**的文字（那正是它被改掉的原因）。
    管理員指派角色時讀到的是錯的，而且那些字串不在語系檔裡，英文介面也翻不出來
    （2026-09-06 使用者截圖回報）。

    **管理員自己改過的說明不可以被蓋掉** —— 只有值還完全等於某個舊出廠值時才換。
    """
    from app.core import roles as _r
    seeded = {x["id"]: x["description"] for x in _r.SEED_ROLES}
    for rid, legacy_set in _r._LEGACY_DESCRIPTIONS.items():
        assert rid in seeded, f"{rid} 不在 SEED_ROLES 裡"
        assert seeded[rid] not in legacy_set, (
            f"{rid} 的舊說明跟現在的種子值一樣，那條 legacy 記錄該移除了")

    # 真的有沒有換掉是行為問題，用一份「舊安裝」的資料庫驗
    # （`tests/test_seed_bootstrap_gap.py::test_legacy_role_description_is_refreshed`）。


def test_role_display_localisation_never_touches_a_renamed_role():
    """翻譯只能套在**還是出廠預設**的名稱上。

    四個內建角色 `is_protected=False`，**管理員可以改名**。照 `is_builtin` 翻的話
    會把管理員取的「會計部」變回「Finance」—— 那是把使用者的資料蓋掉。
    """
    from app.core import roles as _r
    rows = [
        {"id": "finance", "display_name": "財務", "description": "x"},
        {"id": "finance", "display_name": "會計部", "description": "x"},
        {"id": "my-own", "display_name": "財務", "description": "x"},
    ]
    out = _r.localize_for_display(rows, lambda s: "TRANSLATED")
    assert out[0]["display_label"] == "TRANSLATED", "出廠預設名稱要翻"
    assert out[1]["display_label"] == "會計部", "管理員改過的名稱不可以翻"
    assert out[2]["display_label"] == "財務", "自訂角色不可以翻，即使名字剛好一樣"
    # 原值一律不動 —— 編輯表單要用它
    assert [r["display_name"] for r in out] == ["財務", "會計部", "財務"]

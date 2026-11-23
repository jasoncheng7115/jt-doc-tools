"""共用名片簿的成員選單：使用者與群組分兩段、各自有上限（客戶回報 2026-11-20：不能把個人加入名片簿）。

原本合在一起只列前 80 筆、群組排前面；AD 的群組常常上百個（正式機 90 個），使用者一個都列不出來。
這裡把樣板裡真正的 `renderPick` 拿出來，用 Node 跑（假的 DOM），量 90 個群組＋38 個使用者時列出什麼。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TPL = (ROOT / "app/admin/templates/admin_business_cards.html").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _render(users, groups, q="", members=(), ous=None):
    src = re.search(r"(  const PICK_MAX = .*?\n  function renderPick\(\) \{.*?\n  \})\n", TPL, re.S).group(1)
    js = """
const out = {};
function el(tag, cls, text) { return {tag, cls, text: text || '', kids: [], dataset: {}, attrs: {},
  append(...k) { this.kids.push(...k); }, addEventListener() {}, setAttribute(k, v) { this.attrs[k] = v; } }; }
function iconOf(n) { return el('svg', 'icon-' + n); }
function addMember() {}
let firstMatch = null;
const nodes = { bcaSearch: {value: %s}, bcaRole: {value: 'view'}, bcaPick: {replaceChildren(...k) { out.rows = k; }} };
const $ = id => nodes[id];
const tr = s => s;
const subjects = %s;
const members = %s;
function renderMembers() {}
%s
renderPick();
console.log(JSON.stringify(out.rows.map(r => r.tag === 'button'
  ? {t: r.dataset.type, n: r.kids[0].text, add: r.kids.some(k => k.cls === 'bca-pk-add'), label: r.attrs['aria-label']}
  : {cls: r.cls, text: r.text})));
""" % (json.dumps(q), json.dumps({"users": users, "groups": groups, "ous": ous or []}), json.dumps(list(members)), src)
    r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(not NODE, reason="沒有 node")
def test_users_are_listed_even_with_many_groups():
    groups = [{"key": str(i), "name": f"群組{i}", "members": 3} for i in range(90)]
    users = [{"key": str(100 + i), "username": f"user{i}", "source": "ldap", "display_name": f"使用者{i}"} for i in range(38)]
    rows = _render(users, groups)
    assert sum(1 for r in rows if r.get("t") == "user") == 38, "使用者要列得出來（原本被 90 個群組擠掉）"
    assert sum(1 for r in rows if r.get("t") == "group") == 40
    assert any("還有 50 筆" in (r.get("text") or "") for r in rows), "列不完要提示搜尋"
    assert [r["text"] for r in rows if r.get("cls") == "bca-pick-h"] == ["使用者（38）", "群組（90）"]
    # 搜尋顯示名稱也找得到
    rows = _render(users, groups, q="使用者7")
    assert [r["n"] for r in rows if r.get("t") == "user"] == ["user7@ldap"]
    # 已經加進成員的不再列
    rows = _render(users, groups, members=[{"type": "user", "key": "100"}])
    assert "user0@ldap" not in [r.get("n") for r in rows]


OUS = [{"key": "ou=Sales,ou=TW,dc=example,dc=com", "dn": "OU=Sales,OU=TW,DC=example,DC=com", "name": "Sales / TW（example.com）", "users": 12},
       {"key": "ou=TW,dc=example,dc=com", "dn": "OU=TW,DC=example,DC=com", "name": "TW（example.com）", "users": 30}]


@pytest.mark.skipif(not NODE, reason="沒有 node")
def test_organisational_units_can_be_picked_from_the_list():
    """組織單位用選的（客戶 2026-11-23：有設定 LDAP 時能不能用選的，不用自己輸入 DN）。"""
    users = [{"key": "1", "username": "amy", "source": "ad", "display_name": "Amy"}]
    rows = _render(users, [], ous=OUS)
    assert [r["text"] for r in rows if r.get("cls") == "bca-pick-h"] == ["使用者（1）", "群組（0）", "組織單位（2）"]
    assert [r["n"] for r in rows if r.get("t") == "ou"] == ["Sales / TW（example.com）", "TW（example.com）"]
    rows = _render(users, [], q="sales", ous=OUS)                 # 名稱或 DN 都搜得到
    assert [r["n"] for r in rows if r.get("t") == "ou"] == ["Sales / TW（example.com）"]
    rows = _render(users, [], ous=OUS, members=[{"type": "ou", "key": "ou=TW,dc=example,dc=com"}])
    assert [r["n"] for r in rows if r.get("t") == "ou"] == ["Sales / TW（example.com）"], "已經加入的不再列"


@pytest.mark.skipif(not NODE, reason="沒有 node")
def test_no_directory_means_no_ou_section():
    """沒有目錄同步（只有本機或 SAML 帳號，沒有 DN）：組織單位那一段不列，手動輸入 DN 留在下面。"""
    rows = _render([{"key": "1", "username": "amy", "source": "saml"}], [])
    assert [r["text"] for r in rows if r.get("cls") == "bca-pick-h"] == ["使用者（1）", "群組（0）"]


@pytest.mark.skipif(not NODE, reason="沒有 node")
def test_every_row_says_it_adds():
    """每一列都看得出來「點了會加入」（客戶 2026-11-23：搜尋出人名，但沒有加入按鈕）。"""
    rows = _render([{"key": "1", "username": "amy", "source": "ad"}], [{"key": "9", "name": "Sales", "members": 3}], ous=OUS)
    buttons = [r for r in rows if "t" in r]
    assert buttons and all(r["add"] for r in buttons)
    assert all(r["label"].startswith("加入 ") for r in buttons)


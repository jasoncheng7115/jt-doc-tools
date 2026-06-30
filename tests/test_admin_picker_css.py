"""admin 角色/群組 picker 的長名稱不可溢出重疊（2026-06-30 客戶回報）。

根因：`static/css/platform.css` 的 `.form-row label { white-space:nowrap }`
被巢狀在 .form-row 內的 picker `<label class="picker-item">` 繼承 → 長群組名
（AD DN）不換行而溢出欄位互相重疊。修法：每個有 picker 的模板用
`.picker-list .picker-item`（特異性 0,0,2,0）覆蓋成 `white-space:normal; width:auto`。

此測試守住三個模板都有該覆蓋,避免回歸。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATES = [
    "app/admin/templates/admin_users.html",
    "app/admin/templates/admin_groups.html",
    "app/admin/templates/admin_permissions.html",
]


@pytest.mark.parametrize("tpl", _TEMPLATES)
def test_picker_item_overrides_nowrap(tpl):
    s = (_ROOT / tpl).read_text(encoding="utf-8")
    assert ".picker-item" in s, f"{tpl}: 沒有 picker（測試前提不成立）"
    # 必須有高特異性的 .picker-list .picker-item 規則
    m = re.search(r"\.picker-list\s+\.picker-item\s*\{([^}]*)\}", s)
    assert m, f"{tpl}: 缺 `.picker-list .picker-item` 覆蓋 → 長名會繼承 nowrap 溢出"
    body = m.group(1)
    assert "white-space:normal" in body.replace(" ", ""), \
        f"{tpl}: `.picker-list .picker-item` 缺 white-space:normal"
    assert "width:auto" in body.replace(" ", ""), \
        f"{tpl}: `.picker-list .picker-item` 缺 width:auto"

"""`<template>` 裡的東西要從 `.content` 找，不可以 `document.querySelector('#模板id …')`（2026-11-23 客戶回報）。

`<template>` 的內容不在一般的頁面結構裡，`document.querySelector('#bcaIcons [data-icon=x]')` 永遠回 null。
設定 → 名片簿取圖示原本這樣寫：一畫成員清單就丟例外，點了人「沒反應」、共用名片簿建不起來 ——
用 Node 跑的測試把取圖示換成假的，抓不到；真瀏覽器才看得到（`test_business_card_admin_dialog_browser.py`）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _hits() -> list[str]:
    out = []
    for p in sorted((ROOT / "app").rglob("*.html")):
        s = p.read_text(encoding="utf-8")
        for t in set(re.findall(r'<template\s+id="([^"]+)"', s)):
            for m in re.finditer(r"""querySelector(?:All)?\(\s*['"`]#%s[\s>]""" % re.escape(t), s):
                out.append(f"{p.relative_to(ROOT)}:{s.count(chr(10), 0, m.start()) + 1}")
    return out


def test_no_template_is_searched_through_document():
    assert _hits() == [], "改成 document.getElementById('模板id').content.querySelector(…)"


def test_the_scan_catches_the_old_form(tmp_path, monkeypatch):
    """反向對照：原本的寫法要抓得到（不然上面那條永遠綠）。"""
    bad = tmp_path / "app" / "x.html"
    bad.parent.mkdir(parents=True)
    bad.write_text('<template id="ic"><span data-icon="x"></span></template>'
                   "<script>document.querySelector('#ic [data-icon=x]')</script>", encoding="utf-8")
    import sys
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)
    assert _hits() == ["app/x.html:1"]

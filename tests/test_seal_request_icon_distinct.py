"""用印申請與「用印與簽名」工具的圖示要分得開（使用者 2026-11-09：兩個都在側欄，圖示不能一樣）。"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_seal_request_has_its_own_icon_shape():
    icons = _read("app/web/templates/components/icons.html")
    def body(name):
        m = re.search(r"name == '" + re.escape(name) + r"' -%\}(.*?)\{%-", icons, re.S)
        assert m, name
        return re.sub(r"\{#.*?#\}", "", m.group(1), flags=re.S).strip()
    assert body("seal-request") and body("seal-request") != body("stamp")


def test_pdf_stamp_tool_icon_is_not_the_seal_request_icon():
    meta = _read("app/tools/pdf_stamp/__init__.py")
    m = re.search(r'icon="([^"]+)"', meta)
    assert m and m.group(1) != "seal-request"


def test_every_place_that_names_seal_request_uses_the_new_icon():
    base = _read("app/web/templates/base.html")
    link = base[base.index('class="sb-item sb-seal-link"'):]
    link = link[:link.index("</a>")]
    assert "icon('seal-request'" in link and "icon('stamp'" not in link, "側欄的用印申請"
    for rel in ("app/web/templates/seal_request.html", "app/admin/templates/admin_seal_request.html"):
        h1 = re.search(r"<h1>.*?</h1>", _read(rel), re.S).group(0)
        assert "icon('seal-request'" in h1, rel
    main = _read("app/main.py")
    entry = re.search(r'\{"icon": "([^"]+)", "name": "用印申請"', main)
    assert entry and entry.group(1) == "seal-request", "設定選單的用印申請"

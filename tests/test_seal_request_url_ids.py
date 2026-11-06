"""用印申請頁：接進網址的編號一律先經過 `seg()`（驗證＋編碼）。

CodeQL #230（DOM 文字當成網址）、#231（使用者提供的值決定請求網址）：章的編號來自下拉選單、
案件編號可能來自網址的 `#case=`。伺服器端本來就有權限檢查，但前端接網址之前要先驗證。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "app/web/templates/seal_request.html"


def test_every_case_url_segment_goes_through_seg():
    s = PAGE.read_text(encoding="utf-8")
    bad = [s[m.start():m.start() + 60] for m in re.finditer(r"'/cases/' \+ (?!seg\()", s)]
    assert not bad, bad
    bad = [m.group(0) for m in re.finditer(r"'/(attachments|assets)/' \+ (?!seg\()\S+", s)]
    assert not bad, f"{bad} 沒有經過 seg()"


def test_seg_only_accepts_plain_ids():
    s = PAGE.read_text(encoding="utf-8")
    m = re.search(r"function seg\(v\) \{(.*?)\n  \}", s, re.S)
    assert m and "encodeURIComponent" in m.group(1) and "throw" in m.group(1)
    assert "/^[A-Za-z0-9_-]{1,64}$/" in m.group(1)
    assert "/^[A-Za-z0-9_-]{1,64}$/.test(h.slice(5))" in s, "網址 #case= 帶進來的編號要先驗證"

"""名片辨識頁：網址列 `#batch=` 帶進來的批次編號，先驗格式、再編碼，才接進 API 路徑。

CodeQL #234（js/client-side-request-forgery）：「我的作業」的「開啟」會帶 `#batch=<編號>` 回來。伺服器端
本來就有擁有者檢查（別人的批次 404），但前端接網址之前要先驗證並編碼。
"""
from __future__ import annotations

import re
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "app/tools/business_card_reader/templates/business_card_reader.html"


def test_the_batch_id_from_the_url_is_checked_and_encoded():
    s = PAGE.read_text(encoding="utf-8")
    assert "/^#batch=([0-9a-f]{32})$/.exec(location.hash" in s, "#batch= 要先驗成 32 位十六進位"
    assert "openBatch(encodeURIComponent(hb[1]))" in s, "驗過的編號接進 API 路徑前要編碼"
    assert not re.search(r"openBatch\(hb\[1\]\)", s)


def test_no_other_url_values_reach_the_api():
    """網址列只讀 `#books` 與 `#batch=`；其他從網址來的值都不可以接進請求。"""
    s = PAGE.read_text(encoding="utf-8")
    allowed = ("if (location.hash === '#books')", "/^#batch=([0-9a-f]{32})$/.exec(location.hash",
               "history.replaceState(null, '', books ? '#books' : location.pathname + location.search)")
    lines = [ln.strip() for ln in s.splitlines() if re.search(r"location\.(?:hash|search|href)", ln)]
    odd = [ln for ln in lines if not any(a in ln for a in allowed)]
    assert len(lines) == 3 and not odd, odd or lines

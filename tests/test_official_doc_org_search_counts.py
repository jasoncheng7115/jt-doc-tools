"""機關地址簿查詢：講出總筆數、可以「全部顯示」、主機關排在內部單位前面（2026-10-10）。

查「數位」時，臺中市政府數位發展局在正式地址簿裡排第 37 筆，管理頁的試查只列前 20 筆、
只寫「前 20 筆」；公文撰擬的機關清單只列 12 筆、什麼都沒寫 —— **看起來就是地址簿裡沒有**。
查「財政」時臺北市政府財政局排到第 305 筆：前面全是財政部底下的人事處、會計處…（以查詢開頭的優先）。

所以：
* 回應多帶 `total`（符合的總筆數），畫面寫「20 / 165 筆」；列不完時給「全部顯示」，
  一次最多 `ORG_SEARCH_MAX` 筆，再多就講出「只列前 N 筆，多打幾個字」。
* 排序：名稱完全相同 → 主機關（代碼 10 碼）→ 內部單位（主機關代碼後再接 7 碼）；
  同一級裡以查詢開頭的在前。

判準落在畫面上：真的在瀏覽器裡打字、按「全部顯示」，數列出來的筆數。
"""
from __future__ import annotations

import json

import pytest

from app.core import official_doc_sources as ods
from tests.test_official_doc_e2e_kb import _js, _open, _wait, live  # noqa: F401
from tests.test_official_doc_sources import A, clean  # noqa: F401

#: 一個主機關、底下 20 個內部單位（17 碼代碼）、兩個名稱中間才有「財政」的主機關。共 23 筆。
BIG_BOOK = (
    [{"orgId": "Z07000000D", "orgName": "財政部", "statusCode": "T"}]
    + [{"orgId": f"Z07000000DU1{i:05d}", "orgName": f"財政部第{i:02d}處", "statusCode": "T"}
       for i in range(1, 21)]
    + [{"orgId": "Z79030000D", "orgName": "嘉禾市政府財政局", "statusCode": "T"},
       {"orgId": "Z82030000D", "orgName": "東湖縣政府財政處", "statusCode": "T"}]
)
AGENCIES = {"財政部", "嘉禾市政府財政局", "東湖縣政府財政處"}


def _install(book) -> None:
    ods.install_upload(A, json.dumps(book, ensure_ascii=False).encode("utf-8"), "book.json")


# ---------------------------------------------------------------- 核心

def test_the_total_is_reported_even_when_only_a_few_are_listed(clean):  # noqa: F811
    _install(BIG_BOOK)
    page = ods.search_orgs_page("財政", 5)
    assert page["total"] == 23 and len(page["results"]) == 5
    assert ods.search_orgs_page("財政", 999)["total"] == 23
    assert ods.search_orgs_page("不存在", 5) == {"results": [], "total": 0}
    # 舊的介面照舊回清單
    assert [o["orgName"] for o in ods.search_orgs("財政", 3)][0] == "財政部"


def test_main_agencies_come_before_internal_units(clean):  # noqa: F811
    """原本「以查詢開頭」優先：財政部的 20 個內部單位排在前面，名稱中間才有「財政」的
    兩個主機關被擠到第 22、23 筆。"""
    _install(BIG_BOOK)
    names = [o["orgName"] for o in ods.search_orgs("財政", 23)]
    assert set(names[:3]) == AGENCIES, names[:5]
    assert names[0] == "財政部"                      # 同一級裡以查詢開頭的在前
    assert all(n.startswith("財政部第") for n in names[3:])


def test_an_exact_name_still_comes_first(clean):  # noqa: F811
    _install(BIG_BOOK)
    assert ods.search_orgs("嘉禾市政府財政局", 5)[0]["orgName"] == "嘉禾市政府財政局"
    assert ods.search_orgs("財政部第07處", 5)[0]["orgName"] == "財政部第07處"   # 內部單位打全名也在第一


def test_show_all_is_capped(clean, monkeypatch):  # noqa: F811
    _install(BIG_BOOK)
    monkeypatch.setattr(ods, "ORG_SEARCH_MAX", 10)
    page = ods.search_orgs_page("財政", 5000)
    assert len(page["results"]) == 10 and page["total"] == 23


# ---------------------------------------------------------------- 端點

def test_both_endpoints_report_total_and_max(clean):  # noqa: F811
    from fastapi.testclient import TestClient
    from app.main import app
    _install(BIG_BOOK)
    c = TestClient(app)
    j = c.get("/admin/official-doc/search-orgs", params={"q": "財政", "limit": 20}).json()
    assert len(j["results"]) == 20 and j["total"] == 23 and j["max"] == ods.ORG_SEARCH_MAX
    j = c.get("/tools/official-doc/orgs", params={"q": "財政", "limit": 12}).json()
    assert len(j["orgs"]) == 12 and j["total"] == 23 and j["max"] == ods.ORG_SEARCH_MAX
    # 「全部顯示」：原本這支最多 20 筆
    j = c.get("/tools/official-doc/orgs", params={"q": "財政", "limit": 2000}).json()
    assert len(j["orgs"]) == 23


# ---------------------------------------------------------------- 真瀏覽器

def _upload_big_book(port: int) -> None:
    import httpx
    r = httpx.post(f"http://127.0.0.1:{port}/admin/official-doc/sources/{A}/upload",
                   files={"file": ("book.json", json.dumps(BIG_BOOK, ensure_ascii=False).encode("utf-8"),
                                   "application/json")}, timeout=30)
    assert r.status_code == 200 and r.json().get("count") == 23, r.text


def test_admin_trial_search_says_how_many_and_can_show_all(live):  # noqa: F811
    port, send, errs = live
    _upload_big_book(port)
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/admin/official-doc"})
    assert _wait(send, "document.readyState === 'complete' && !!document.getElementById('odOrgQ')")
    _js(send, """(function(){var e=document.getElementById('odOrgQ'); e.value='財政';
                 e.dispatchEvent(new Event('input',{bubbles:true})); return true;})()""")
    assert _wait(send, "document.querySelectorAll('#odOrgs li').length === 20", 10), "沒有列出前 20 筆"
    say = _js(send, "document.getElementById('odOrgSay').textContent")
    assert "20 / 23" in say, say
    first3 = _js(send, "Array.from(document.querySelectorAll('#odOrgs li')).slice(0,3)"
                       ".map(function(li){return li.lastChild.textContent;})")
    assert set(first3) == AGENCIES, first3
    assert _js(send, "!!document.querySelector('#odOrgSay button')"), "列不完要有「全部顯示」"
    _js(send, "document.querySelector('#odOrgSay button').click(), true")
    assert _wait(send, "document.querySelectorAll('#odOrgs li').length === 23", 10), "全部顯示沒有作用"
    assert "23 / 23" in _js(send, "document.getElementById('odOrgSay').textContent")
    assert not _js(send, "!!document.querySelector('#odOrgSay button')"), "全部列出來之後按鈕要收起來"
    assert not errs, errs


def test_picker_says_how_many_and_can_show_all(live):  # noqa: F811
    port, send, errs = live
    _upload_big_book(port)
    _open(send, port)
    _js(send, "document.querySelector('input[name=\"odMode\"][value=\"letter\"]').click(), true")
    _js(send, """(function(){var e=document.getElementById('odReceiver'); e.focus(); e.value='財政';
                 e.setSelectionRange(2,2); e.dispatchEvent(new Event('input',{bubbles:true})); return true;})()""")
    panel = "document.getElementById('odReceiverOrgList')"
    assert _wait(send, f"!{panel}.hidden && {panel}.querySelectorAll('.op-opt').length === 12", 10), \
        "清單沒有列出 12 筆"
    foot = _js(send, f"({panel}.querySelector('.op-more')||{{}}).textContent || ''")
    assert "12 / 23" in foot and _js(send, f"!!{panel}.querySelector('.op-all')"), foot
    # 清單比框高、還沒捲動時，「全部顯示」那一列就看得到（固定在底部）—— 不固定的話要捲到最下面才找得到
    assert _js(send, f"{panel}.scrollHeight > {panel}.clientHeight + 20"), "清單沒有比框高，這條驗不到東西"
    _js(send, f"{panel}.scrollTop = 0, true")
    vis = _js(send, f"""(function(){{var p={panel}.getBoundingClientRect(),
                         b={panel}.querySelector('.op-all').getBoundingClientRect();
                         return b.bottom <= p.bottom + 1 && b.top >= p.top;}})()""")
    assert vis, "「全部顯示」被捲出清單外"
    _js(send, f"{panel}.querySelector('.op-all').click(), true")
    assert _wait(send, f"{panel}.querySelectorAll('.op-opt').length === 23", 10), "全部顯示沒有作用"
    assert "23 / 23" in _js(send, f"{panel}.querySelector('.op-more').textContent")
    assert not _js(send, f"!!{panel}.querySelector('.op-all')")
    assert _js(send, "document.activeElement && document.activeElement.id") == "odReceiver", \
        "按「全部顯示」之後游標要留在輸入框"
    assert not errs, errs

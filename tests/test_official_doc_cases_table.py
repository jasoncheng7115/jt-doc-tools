"""歷史案件表格：主旨整句、舊案件補主旨（2026-10-10 使用者：「案件名稱如果字多 要截斷搭配... 移過去才顯示全文」）。

原本清單顯示的是 `title` —— 那是**檔名**用的，存的時候就切在 20 個字、沒有「…」，
畫面上看起來像整句（「彙整各所屬機關資訊設備現況，請 貴局依附」）。現在：

* 案件多存一個 `subject`（主旨整句，規則同檔名：去掉結語 / 期望語與起頭語，最多 200 字）；
  畫面截斷加「…」、滑鼠移過去看全文（瀏覽器那一側在 `test_official_doc_cases_table_browser.py`）。
* `title` 照舊 20 字（檔名用，不可以變長）。
* 舊案件沒有 `subject`：第一次列出來時從草稿算一次存回去，**不動「最後修改」**。
* 搜尋也比對主旨整句（只比 20 字的話，後半句的字搜不到）。
"""
from __future__ import annotations

import json

from app.core import official_doc_di as di
from tests.test_official_doc_di import _letter
from tests.test_official_doc_tool import BASE, _run, fake_llm  # noqa: F401

LONG = ("彙整各所屬機關資訊設備現況，請　貴局依附件表格填報並於本年10月31日前函復本府，"
        "以利統籌編列下年度汰換預算")


def _long_di() -> bytes:
    text = _letter(content={"subject": LONG, "explanation": ["依本府資訊設備管理要點辦理。"],
                            "measures": []}, closing="請　查照")
    return di.build(text, "letter")[0]


def _cases():
    from app.core import official_doc_cases
    return official_doc_cases


def _row(client, cid):
    return next(x for x in client.get(f"{BASE}/api/cases").json()["cases"] if x["case_id"] == cid)


def test_the_full_subject_is_kept_and_the_title_stays_short(client, auth_off):
    got = client.post(f"{BASE}/cases/import", files=[("files", ("a.di", _long_di()))]).json()
    cid = got["imported"][0]["case_id"]
    row = _row(client, cid)
    assert len(row["title"]) <= 20, row["title"]                     # 檔名用的照舊
    assert row["subject"].startswith("彙整各所屬機關資訊設備現況")
    assert "以利統籌編列下年度汰換預算" in row["subject"], row["subject"]   # 後半句還在
    assert "　貴局" in row["subject"], "挪抬的全形空白要留著"
    assert not row["subject"].endswith("查照"), "期望語不算在主旨裡"
    # 搜尋比對主旨整句：後半句的字也搜得到
    hits = client.get(f"{BASE}/api/cases", params={"q": "汰換預算"}).json()["cases"]
    assert cid in [x["case_id"] for x in hits]


def test_a_generated_draft_stores_its_subject(client, auth_off, fake_llm):  # noqa: F811
    _, cid, res = _run(client)
    meta = _cases().load_meta(cid)
    assert meta.get("subject"), meta
    assert meta["subject"].startswith(meta["title"].rstrip("。，"))


def test_old_cases_get_their_subject_without_touching_last_modified(client, auth_off):
    cid = client.post(f"{BASE}/cases/import",
                      files=[("files", ("a.di", _long_di()))]).json()["imported"][0]["case_id"]
    p = _cases().file(cid, "meta.json")
    meta = json.loads(p.read_text(encoding="utf-8"))
    meta.pop("subject")
    meta["updated_at"] = 1_700_000_000.0
    p.write_text(json.dumps(meta), encoding="utf-8")
    row = _row(client, cid)
    assert "汰換預算" in row["subject"], "舊案件要從草稿補主旨"
    after = json.loads(p.read_text(encoding="utf-8"))
    assert after["subject"] == row["subject"], "補完要存回去，下次不再算"
    assert after["updated_at"] == 1_700_000_000.0, "補一個欄位不可以改「最後修改」"


def test_the_page_carries_full_text_for_hover_and_sort_keys(client, auth_off):
    cid = client.post(f"{BASE}/cases/import",
                      files=[("files", ("a.di", _long_di()))]).json()["imported"][0]["case_id"]
    import re
    html = client.get(f"{BASE}/cases").text
    row = re.search(r'<tr data-case-id="%s".*?</tr>' % cid, html, re.S).group(0)
    a = re.search(r'<a class="odc-name-text"[^>]*>', row).group(0)
    assert re.search(r'title="[^"]*以利統籌編列下年度汰換預算"', a), "滑鼠移過去要看得到全文"
    for k in ("name", "mode", "rev", "issues", "updated"):
        assert f'data-k-{k}="' in row, k
    for col in ("mode", "rev", "issues", "updated"):
        assert f'data-col="{col}"' in row, col

"""公文撰擬 × 政府資料開放的機關範本與地址簿（管理員在「公文撰擬設定」下載的）。

這裡驗的是**工具這一側**：沒下載就不出現、範本只開放填得對的那幾份、
畫面送來的範本代碼只收目前清單上的、範本讀不懂時照樣交檔並講出來。
下載、解析、SSRF 那一側在 `test_official_doc_sources.py`。
"""
from __future__ import annotations

import json

import pytest

from app.core import official_doc_sources as ods

from tests.test_official_doc_tool import (BASE, _run, case_id, fake_llm)  # noqa: F401

SID = "ndc-templates"
TEMPLATES = [
    {"source_id": SID, "category": "一般公文表單", "name": "簽", "path": "一般公文表單/簽.odt"},
    {"source_id": SID, "category": "一般公文表單", "name": "簽(上行簽)",
     "path": "一般公文表單/簽(上行簽).odt"},
    {"source_id": SID, "category": "一般公文表單", "name": "函", "path": "一般公文表單/函.odt"},
    {"source_id": SID, "category": "一般公文表單", "name": "書函", "path": "一般公文表單/書函.odt"},
    {"source_id": SID, "category": "人事表單", "name": "派令", "path": "人事表單/派令.odt"},
]
SIGN_KEY = f"{SID}|一般公文表單/簽.odt"
LETTER_KEY = f"{SID}|一般公文表單/函.odt"


@pytest.fixture
def org_data(monkeypatch):
    state = {"templates": list(TEMPLATES), "orgs": True, "got": []}

    def get_template(sid, name):
        state["got"].append((sid, name))
        return b"TEMPLATE:" + name.encode()
    monkeypatch.setattr(ods, "list_templates", lambda: state["templates"])
    monkeypatch.setattr(ods, "get_template", get_template)
    monkeypatch.setattr(ods, "has_address_book", lambda: state["orgs"])
    monkeypatch.setattr(ods, "attribution",
                        lambda kind=None: [f"國家發展委員會檔案管理局「{kind}」（示範出處）。"])
    # 端點走 `search_orgs_page`（v1.16.74 起多回總筆數 `total`）
    monkeypatch.setattr(ods, "search_orgs_page",
                        lambda q, limit=20: {"results": [{"orgId": "A15000000E", "orgName": "嘉禾市政府",
                                                          "nameMarks": [[0, 2]], "idMarks": []}],
                                             "total": 1})
    return state


@pytest.fixture
def fake_export(monkeypatch):
    from app.core import official_doc_odt as odx
    seen = []

    def export(text, fmt, *, title="", draft_mark=True, template=None, extras=None):
        seen.append({"fmt": fmt, "template": template})
        if template == b"TEMPLATE:BROKEN":
            raise odx.TemplateError("範本讀不進來（不是有效的 ODT）。")
        return b"OUT-" + fmt.encode(), "application/x-test"
    monkeypatch.setattr(odx, "export", export)
    return seen


def _export(client, case_id, **kw):
    body = {"text": "主旨：測試。", "fmt": "odt", "title": "t", "case_id": case_id}
    body.update(kw)
    return client.post(f"{BASE}/export", json=body)


# ------------------------------------------------------------------ 頁面

def test_nothing_shows_when_nothing_was_downloaded(client, auth_off, monkeypatch):
    monkeypatch.setattr(ods, "list_templates", lambda: [])
    monkeypatch.setattr(ods, "has_address_book", lambda: False)
    html = client.get(f"{BASE}/").text
    assert 'id="odTplRow"' not in html and 'id="odOrgList"' not in html


def test_only_templates_that_fill_correctly_are_offered(client, auth_off, org_data):
    html = client.get(f"{BASE}/").text
    i = html.index("data-tpls='") + len("data-tpls='")
    tpls = json.loads(html[i:html.index("'", i)].replace("&#34;", '"').replace("&quot;", '"'))
    assert [t["key"] for t in tpls["sign"]] == [SIGN_KEY]
    assert [t["key"] for t in tpls["letter"]] == [LETTER_KEY]
    assert "endorse" not in tpls, "簽辦意見寫在來文上，沒有範本"
    flat = json.dumps(tpls, ensure_ascii=False)
    assert "上行簽" not in flat and "書函" not in flat and "派令" not in flat


def test_attribution_is_shown_next_to_the_data(client, auth_off, org_data):
    html = client.get(f"{BASE}/").text
    assert "示範出處" in html, "政府資料開放授權條款要求標示出處"
    # 機關名稱改由本站樣式的清單挑（v1.16.68，`static/js/org_picker.js`）；原生 datalist 拿掉了
    assert 'data-org-pick="single"' in html and '/static/js/org_picker.js' in html
    assert 'id="odOrgList"' not in html


def test_a_broken_sources_module_does_not_break_the_page(client, auth_off, monkeypatch):
    def boom(*a, **k):
        raise OSError("index 壞掉")
    monkeypatch.setattr(ods, "list_templates", boom)
    monkeypatch.setattr(ods, "has_address_book", boom)
    r = client.get(f"{BASE}/")
    assert r.status_code == 200 and 'id="odTplRow"' not in r.text


# ------------------------------------------------------------------ 匯出套範本

def test_export_applies_the_chosen_template(client, case_id, org_data, fake_export):
    r = _export(client, case_id, template=SIGN_KEY)
    assert r.status_code == 200, r.text
    assert fake_export[-1]["template"] == "TEMPLATE:一般公文表單/簽.odt".encode()
    assert r.headers["x-jtdt-template"] == "applied"


def test_export_without_a_template_uses_the_built_in_layout(client, case_id, org_data, fake_export):
    r = _export(client, case_id)
    assert r.status_code == 200 and fake_export[-1]["template"] is None
    assert r.headers["x-jtdt-template"] == "none"
    assert org_data["got"] == []


@pytest.mark.parametrize("key", [
    LETTER_KEY,                                   # 另一種文別的範本（這件是簽）
    f"{SID}|一般公文表單/簽(上行簽).odt",          # 下載了、但不開放的那幾份
    f"{SID}|人事表單/派令.odt",
    f"{SID}|../../../etc/passwd",
    "other-source|一般公文表單/簽.odt",
    "x" * 400,
])
def test_template_keys_outside_the_current_list_are_refused(client, case_id, org_data,
                                                            fake_export, key):
    r = _export(client, case_id, template=key)
    assert r.status_code == 400, (key, r.text)
    assert org_data["got"] == [], "清單外的代碼不可以拿去讀範本"


def test_a_template_that_disappeared_is_a_clear_400(client, case_id, org_data, fake_export,
                                                    monkeypatch):
    monkeypatch.setattr(ods, "get_template", lambda sid, name: None)
    r = _export(client, case_id, template=SIGN_KEY)
    assert r.status_code == 400 and "重新整理" in r.json()["detail"]


def test_an_unreadable_template_still_delivers_the_file(client, case_id, org_data, fake_export,
                                                       monkeypatch):
    monkeypatch.setattr(ods, "get_template", lambda sid, name: b"TEMPLATE:BROKEN")
    r = _export(client, case_id, template=SIGN_KEY)
    assert r.status_code == 200 and r.content == b"OUT-odt"
    assert r.headers["x-jtdt-template"] == "fallback", "要講出來沒套到範本"
    assert fake_export[-1]["template"] is None


def test_template_is_ignored_for_text_exports(client, case_id, org_data, fake_export):
    r = _export(client, case_id, fmt="txt", template=SIGN_KEY)
    assert r.status_code == 200 and r.content.decode("utf-8") == "主旨：測試。"
    assert org_data["got"] == []


# ------------------------------------------------------------------ 機關名稱建議

def test_orgs_suggestions(client, auth_off, org_data):
    r = client.get(f"{BASE}/orgs", params={"q": "嘉禾"})
    assert r.status_code == 200
    # `exact`：名稱完全相同而且只有一筆時的代碼（v1.16.68）；這裡查的是名稱的一部分，所以是空的
    # `marks`：符合處的字元位置（畫面標亮用），照伺服器比對的結果原樣帶過去
    # `total`：符合的總筆數、`max`：一次最多列幾筆（v1.16.74：清單列不完時畫面講出「12 / N 筆」）
    assert r.json() == {"orgs": [{"name": "嘉禾市政府", "id": "A15000000E", "marks": [[0, 2]]}],
                        "exact": "", "total": 1, "max": ods.ORG_SEARCH_MAX}


def test_orgs_empty_query_and_too_long(client, auth_off, org_data):
    assert client.get(f"{BASE}/orgs", params={"q": "  "}).json() == {"orgs": [], "exact": "", "total": 0}
    assert client.get(f"{BASE}/orgs", params={"q": "字" * 60}).status_code == 400


def test_orgs_failure_is_just_no_suggestion(client, auth_off, monkeypatch):
    def boom(q, limit=20):
        raise OSError("地址簿壞掉")
    monkeypatch.setattr(ods, "search_orgs_page", boom)
    r = client.get(f"{BASE}/orgs", params={"q": "嘉禾"})
    assert r.status_code == 200 and r.json() == {"orgs": [], "exact": "", "total": 0,
                                                 "max": ods.ORG_SEARCH_MAX}


def test_real_attribution_text_names_the_licence():
    """真的顯名文字要有提供機關、資料名稱與授權條款版本（政府資料開放授權條款第1版）。"""
    src = next(s for s in ods.BUILTIN_SOURCES if s["kind"] == ods.KIND_TEMPLATES)
    t = ods.attribution_text(src)
    assert "國家發展委員會檔案管理局" in t and "政府資料開放授權條款" in t and "data.gov.tw" in t

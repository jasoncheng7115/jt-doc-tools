"""MCP：名片辨識 `business_card_read`（v1.17.41 起；`lang` v1.17.50）經 `jtdt_run` 真的跑一次。

* 回聯絡人（每一欄 key / value / status）與給人校對的 `review_url`；結果沒經過人工校對。
* `lang=ja` 要一路帶到 OCR（日文＋英文）；不認得的語言要擋。
* 預設不開放：名片上是別人的聯絡資料，管理員另外勾。
* 批次歸 Token 的擁有者：另一個人的帳號看不到（站台開了登入時）。
OCR 換成假的；名片內容全部杜撰。
"""
from __future__ import annotations

import pytest

from app.tools.business_card_reader import card_ocr, store
from tests import bc_synth as S
from tests.test_mcp_cross_user import two_users  # noqa: F401
from tests.test_mcp_server import _call, _upload_inline, mcp  # noqa: F401
from tests.test_mcp_tools_batch2 import _run
from tests.test_mcp_tools_batch3 import _err

ROWS = [("株式会社サンプル", 30), ("山田 太郎", 44), ("営業部 部長", 18), ("携帯 090-1234-5678", 14),
        ("taro.yamada@example.jp", 14)]


@pytest.fixture
def fake_ocr(monkeypatch):
    calls = []

    def fake(img, langs=card_ocr.DEFAULT_LANGS, **kw):
        calls.append(langs)
        lines = [card_ocr.Line(text=t, box=[20, 20 + i * 50, 400, 40], conf=95, height=h) for i, (t, h) in enumerate(ROWS)]
        return {"lines": lines, "rotation": 0, "engine": "fake", "image": img, "quality": 99}
    monkeypatch.setattr(card_ocr, "ocr_card", fake)
    monkeypatch.setattr(card_ocr, "lang_available", lambda lang: {"ok": True, "via": "easyocr", "hint": ""})
    return calls


def _enable():
    from app.mcp import settings as ms
    ms.save({"tools": ["business_card_read"]})              # 預設不開放：管理員勾了才看得到


def _fields(res):
    out = {}
    for ct in res["result"]["contacts"]:
        for f in ct["fields"]:
            out.setdefault(f["key"], []).append(f["value"])
    return out


def test_off_by_default():
    from app.mcp import registry
    assert not registry.BY_NAME["business_card_read"].default_on, "名片是別人的聯絡資料：管理員另外勾才開放"


def test_reads_a_card_through_mcp_in_japanese(mcp, fake_ocr):
    c, tok = mcp
    _enable()
    fid = _upload_inline(c, tok, S.jpeg(S.card()), "card.jpg")
    res = _run(c, tok, "business_card_read", [fid], {"lang": "ja", "wait": 60})
    r = res["result"]
    assert r["done"] is True and r["review_url"].startswith("/tools/business-card-reader/#batch="), r
    assert fake_ocr == ["jpn+eng"], "lang=ja 要帶到 OCR"
    got = _fields(res)
    assert got["company"] == ["株式会社サンプル"] and got["name"] == ["山田 太郎"]
    assert got["department"] == ["営業部"] and got["title"] == ["部長"]
    assert got["mobile"] == ["090-1234-5678"] and got["email"] == ["taro.yamada@example.jp"]
    # 預設語言是繁中
    res = _run(c, tok, "business_card_read", [fid], {"wait": 60})
    assert fake_ocr[-1] == "chi_tra+eng"


def test_unknown_language_is_rejected(mcp, fake_ocr):
    c, tok = mcp
    _enable()
    fid = _upload_inline(c, tok, S.jpeg(S.card()), "card.jpg")
    err, msg = _err(c, tok, "business_card_read", [fid], {"lang": "ko"})
    assert err, msg
    assert fake_ocr == [], "擋在辨識之前"


def test_batch_belongs_to_the_token_owner(two_users, fake_ocr):  # noqa: F811
    from app.core import user_manager
    c, alice, bob = two_users
    _enable()
    fid = _upload_inline(c, alice, S.jpeg(S.card()), "card.jpg")
    bid = _run(c, alice, "business_card_read", [fid], {"wait": 60})["result"]["batch_id"]
    ua, ub = user_manager.get_by_username("mcp-alice"), user_manager.get_by_username("mcp-bob")
    assert store.get_batch(f"u{ua['id']}", bid)["id"] == bid, "批次歸 Token 的擁有者"
    for other in (f"u{ub['id']}", "local"):
        with pytest.raises(store.NotFound):
            store.get_batch(other, bid)                       # B 的帳號、開了登入時共用的 local 都看不到

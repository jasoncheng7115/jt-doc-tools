"""名片辨識：日文支援（使用者 2026-11-20）。資料全部杜撰。

* 辨識語言：`zh`（繁中＋英文，預設）/ `ja`（日文＋英文）；建立批次、一次完成 API、MCP 都收 `lang`。
* 一張卡可以單獨改用另一種語言重新辨識（一疊台灣名片裡夾一張日文的），整批也可以改。
* 日文規則：公司（株式会社…）、部門＋職稱、姓名（含假名的名、「々」）、假名讀音那一行不當欄位、
  〒 郵遞區號與地址、日本手機（11 碼、+81）。台灣名片不經過日文規則（真實樣本回歸 0 變化另外驗）。
* 這台辨識不了日文（只有 Tesseract 又沒裝 jpn）時建立批次回 400 並說明。
"""
from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from app import main as app_main
from app.tools.business_card_reader import card_ocr, extract as E, extract_ja as JA, pipeline, store
from tests import bc_synth as S

API = "/tools/business-card-reader/api"


def L(*rows):
    return [{"text": t, "height": h, "conf": 95, "box": [0, i * 30, 400, h]} for i, (t, h) in enumerate(rows)]


def kv(fields):
    out = {}
    for f in fields:
        out.setdefault(f["key"], []).append(f)
    return out


def vals(fs, key):
    return [f["value"] for f in fs.get(key, [])]


# --------------------------------------------------------------------------- 擷取規則

def test_typical_japanese_card():
    fs = kv(E.extract_fields(L(
        ("株式会社サンプル", 30), ("営業本部 第一営業部 部長", 18), ("やまだ たろう", 12), ("山田 太郎", 44), ("Taro Yamada", 18),
        ("〒100-0005", 14), ("東京都千代田区丸の内1-2-3 サンプルビル5F", 14), ("TEL 03-1234-5678  FAX 03-1234-5679", 14),
        ("携帯 090-1234-5678", 14), ("E-mail: taro.yamada@example.co.jp", 14), ("https://www.example.co.jp", 14)), lang="ja"))
    assert vals(fs, "company") == ["株式会社サンプル"]
    assert vals(fs, "department") == ["営業本部 第一営業部"] and vals(fs, "title") == ["部長"]
    assert vals(fs, "name") == ["山田 太郎"] and vals(fs, "name_en") == ["Taro Yamada"]
    assert "やまだ たろう" not in [f["value"] for v in fs.values() for f in v], "假名讀音不當任何欄位"
    assert vals(fs, "address") == ["東京都千代田区丸の内1-2-3 サンプルビル5F"] and fs["address"][0]["zip"] == "100-0005"
    assert vals(fs, "phone") == ["03-1234-5678"] and vals(fs, "fax") == ["03-1234-5679"]
    assert vals(fs, "mobile") == ["090-1234-5678"] and fs["mobile"][0]["status"] == "auto", "日本手機 11 碼是完整的"
    assert vals(fs, "email") == ["taro.yamada@example.co.jp"]
    assert all(f["status"] == "auto" for v in fs.values() for f in v)


@pytest.mark.parametrize("rows,expect", [
    # 名是假名、姓氏有「々」、+81 的手機
    ([("サンプル商事株式会社", 30), ("技術部 課長", 18), ("佐々木 ゆかり", 44), ("〒530-0001 大阪府大阪市北区梅田1-1-1", 14),
      ("Mobile: +81-80-1234-5678", 14)],
     {"company": ["サンプル商事株式会社"], "department": ["技術部"], "title": ["課長"], "name": ["佐々木 ゆかり"],
      "mobile": ["+81-80-1234-5678"], "address": ["大阪府大阪市北区梅田1-1-1"]}),
    # 法人格自己一段、職稱在姓名前面
    ([("一般社団法人 サンプル協会", 26), ("代表理事", 16), ("理事長 鈴木一郎", 40)],
     {"company": ["一般社団法人 サンプル協会"], "title": ["代表理事", "理事長"], "name": ["鈴木一郎"]}),
    # 法人格在後、職稱連著單位、姓名沒有空格
    ([("サンプル 株式会社", 26), ("営業部長", 16), ("田中花子", 40), ("Hanako Tanaka", 16)],
     {"company": ["サンプル 株式会社"], "title": ["営業部長"], "name": ["田中花子"], "name_en": ["Hanako Tanaka"]}),
    # （株）、英文職稱的片假名
    ([("（株）サンプルテック", 26), ("開発本部 エンジニア", 16), ("高橋 健", 40)],
     {"company": ["（株）サンプルテック"], "department": ["開発本部"], "title": ["エンジニア"], "name": ["高橋 健"]}),
    # 日英並列在同一行
    ([("株式会社サンプル Sample Inc.", 26), ("山田 太郎 Taro Yamada", 40), ("営業部 部長 / Sales Manager", 16)],
     {"company": ["株式会社サンプル"], "company_en": ["Sample Inc."], "department": ["営業部"], "title": ["部長"],
      "title_en": ["Sales Manager"], "name": ["山田 太郎"], "name_en": ["Taro Yamada"]}),
])
def test_japanese_card_variants(rows, expect):
    fs = kv(E.extract_fields(L(*rows), lang="ja"))
    for k, v in expect.items():
        assert vals(fs, k) == v, (k, {kk: vals(fs, kk) for kk in fs})


def test_kana_on_the_card_turns_japanese_rules_on_without_choosing_ja():
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("山田 太郎", 44), ("営業部 部長", 16))))
    assert vals(fs, "company") == ["株式会社サンプル"] and vals(fs, "name") == ["山田 太郎"]


def test_taiwan_card_is_not_touched_by_the_japanese_rules():
    rows = [("範例科技股份有限公司", 26), ("王小明", 40), ("業務經理", 18), ("台北市中正區範例路1號", 14),
            ("電話：02-2345-6789", 14), ("手機：0912-345-678", 14)]
    assert not JA.is_japanese_card(L(*rows), "")
    fs = kv(E.extract_fields(L(*rows)))
    assert vals(fs, "company") == ["範例科技股份有限公司"] and vals(fs, "name") == ["王小明"]
    assert vals(fs, "title") == ["業務經理"] and vals(fs, "mobile") == ["0912-345-678"]
    assert fs["mobile"][0]["status"] == "auto"


def test_postal_code_is_not_a_phone_and_japanese_numbers_are_complete():
    assert E.find_phones("〒100-0005 東京都千代田区丸の内1-2-3") == []
    p = E.find_phones("090-1234-5678")[0]
    assert p["kind"] == "mobile" and p["complete"]
    assert E.find_phones("080-1111-2222")[0]["kind"] == "mobile"
    assert E.find_phones("+81-70-1234-5678")[0]["kind"] == "mobile"
    assert E.find_phones("代表 03-1234-5678")[0]["kind"] == "phone"
    assert E.find_phones("0912-345-678")[0]["complete"], "台灣手機照舊"


# --------------------------------------------------------------------------- 語言設定與可用性

def test_lang_codes():
    assert card_ocr.langs_for("ja") == "jpn+eng" and card_ocr.langs_for("zh") == "chi_tra+eng"
    assert card_ocr.langs_for("") == card_ocr.DEFAULT_LANGS and card_ocr.langs_for("jpn+eng") == "jpn+eng"
    assert card_ocr.lang_of("jpn+eng") == "ja" and card_ocr.lang_of("chi_tra+eng") == "zh" and card_ocr.lang_of("") == "zh"


def test_lang_available_follows_the_ocr_engine(monkeypatch):
    from app.core import ocr_engine, ocr_remote_settings, tessdata_manager
    monkeypatch.setattr(ocr_remote_settings, "is_enabled_and_configured", lambda: False)
    monkeypatch.setattr(ocr_engine, "get_default_engine", lambda: "easyocr")
    monkeypatch.setattr(ocr_engine, "is_easyocr_available", lambda: True)
    assert card_ocr.lang_available("ja") == {"ok": True, "via": "easyocr", "hint": ""}
    monkeypatch.setattr(ocr_engine, "get_default_engine", lambda: "tesseract")
    monkeypatch.setattr(ocr_engine, "is_tesseract_available", lambda: True)
    monkeypatch.setattr(tessdata_manager, "get_installed_langs", lambda: {"chi_tra", "eng"})
    st = card_ocr.lang_available("ja")
    assert st["ok"] is False and st["missing"] == ["jpn"] and "OCR" in st["hint"]
    assert card_ocr.lang_available("zh")["ok"] is True
    monkeypatch.setattr(tessdata_manager, "get_installed_langs", lambda: {"chi_tra", "eng", "jpn"})
    assert card_ocr.lang_available("ja") == {"ok": True, "via": "tesseract", "hint": ""}


# --------------------------------------------------------------------------- API

@pytest.fixture
def ocr_calls(monkeypatch):
    """假 OCR：記下每次用的語言；回一張日文名片。"""
    calls = []
    rows = [("株式会社サンプル", 30), ("山田 太郎", 44), ("営業部 部長", 18), ("携帯 090-1234-5678", 14)]

    def fake(img, langs=card_ocr.DEFAULT_LANGS, **kw):
        calls.append(langs)
        lines = [card_ocr.Line(text=t, box=[20, 20 + i * 50, 400, 40], conf=95, height=h) for i, (t, h) in enumerate(rows)]
        return {"lines": lines, "rotation": 0, "engine": "fake", "image": img, "quality": 99}
    monkeypatch.setattr(card_ocr, "ocr_card", fake)
    monkeypatch.setattr(card_ocr, "lang_available", lambda lang: {"ok": True, "via": "easyocr", "hint": ""})
    return calls


@pytest.fixture
def anon(auth_off, ocr_calls):
    with TestClient(app_main.app) as c:
        before = {b["id"] for b in c.get(f"{API}/batches").json().get("items", [])}
        yield c
        # 這裡建的批次刪掉（同一個人最多保留 20 批，留著會讓後面的測試建不了批次）
        pipeline.wait_idle(30)
        for b in c.get(f"{API}/batches").json().get("items", []):
            if b["id"] not in before:
                c.delete(f"{API}/batches/{b['id']}")


def _wait(c, bid, t=60):
    end = time.time() + t
    while time.time() < end:
        pipeline.wait_idle(5)
        b = c.get(f"{API}/batches/{bid}").json()
        if b["counts"]["cards_pending"] == 0 and b["status"] == "review":
            return b
        time.sleep(0.1)
    raise AssertionError("背景辨識沒有完成")


def _files(n=1):
    return [("files", (f"{i}.jpg", S.jpeg(S.scene_single(seed=i + 3)[0]), "image/jpeg")) for i in range(n)]


def test_batch_in_japanese_uses_the_japanese_ocr_and_rules(anon, ocr_calls):
    r = anon.post(f"{API}/batches", files=_files(), data={"mode": "single", "lang": "ja"})
    assert r.status_code == 201, r.text
    b = _wait(anon, r.json()["id"])
    assert b["lang"] == "ja" and ocr_calls == ["jpn+eng"]
    card = b["pages"][0]["cards"][0]
    assert card["lang"] == "ja" and card["lang_used"] == "ja" and card["lang_own"] is False
    f = {x["key"]: x["value"] for x in b["contacts"][0]["fields"]}
    assert f["company"] == "株式会社サンプル" and f["name"] == "山田 太郎" and f["title"] == "部長"


def test_default_is_chinese_and_bad_lang_is_rejected(anon, ocr_calls):
    r = anon.post(f"{API}/batches", files=_files(), data={"mode": "single"})
    b = _wait(anon, r.json()["id"])
    assert b["lang"] == "zh" and ocr_calls == ["chi_tra+eng"]
    r = anon.post(f"{API}/batches", files=_files(), data={"mode": "single", "lang": "kr"})
    assert r.status_code == 400 and "zh" in r.json()["detail"]


def test_unavailable_language_is_a_400_with_a_hint(anon, monkeypatch):
    monkeypatch.setattr(card_ocr, "lang_available", lambda lang: {"ok": lang != "ja", "via": "", "hint": "裝訓練檔"})
    r = anon.post(f"{API}/batches", files=_files(), data={"mode": "single", "lang": "ja"})
    assert r.status_code == 400 and r.json()["detail"] == "裝訓練檔"
    st = anon.get(f"{API}/langs").json()
    assert st["ja"]["ok"] is False and st["zh"]["ok"] is True


def test_one_card_and_the_whole_batch_can_switch_language(anon, ocr_calls):
    b = _wait(anon, anon.post(f"{API}/batches", files=_files(2), data={"mode": "single"}).json()["id"])
    cards = [c for p in b["pages"] for c in p["cards"]]
    ocr_calls.clear()
    assert anon.post(f"{API}/batches/{b['id']}/cards/{cards[0]['id']}/retry", json={"lang": "ja"}).status_code == 200
    b = _wait(anon, b["id"])
    got = {c["id"]: c for p in b["pages"] for c in p["cards"]}
    assert ocr_calls == ["jpn+eng"], "只重新辨識那一張、用日文"
    assert got[cards[0]["id"]]["lang"] == "ja" and got[cards[0]["id"]]["lang_own"] is True
    assert got[cards[1]["id"]]["lang"] == "zh", "另一張照舊"
    assert anon.post(f"{API}/batches/{b['id']}/cards/{cards[0]['id']}/retry", json={"lang": "xx"}).status_code == 400
    # 整批改回繁中：每一張自己選的語言清掉，全部重新辨識
    ocr_calls.clear()
    r = anon.post(f"{API}/batches/{b['id']}/lang", json={"lang": "zh"})
    assert r.status_code == 200 and r.json()["queued"] == 2
    b = _wait(anon, b["id"])
    assert sorted(ocr_calls) == ["chi_tra+eng", "chi_tra+eng"] and b["lang"] == "zh"
    assert all(c["lang"] == "zh" and not c["lang_own"] for p in b["pages"] for c in p["cards"])


def test_one_shot_api_takes_lang(anon, ocr_calls):
    r = anon.post(f"{API}/business-card-reader", files=_files(), data={"mode": "single", "lang": "ja", "wait": 30})
    assert r.status_code == 200, r.text
    assert ocr_calls == ["jpn+eng"]


def test_retry_keeps_manual_edits_when_switching_language(anon, ocr_calls):
    b = _wait(anon, anon.post(f"{API}/batches", files=_files(), data={"mode": "single"}).json()["id"])
    ct = b["contacts"][0]
    fields = [dict(f) for f in ct["fields"]]
    fields.append({"key": "note", "value": "人工補的", "status": "confirmed", "source": {"method": "manual"}})
    assert anon.put(f"{API}/batches/{b['id']}/contacts/{ct['id']}", json={"fields": fields}).status_code == 200
    cid = b["pages"][0]["cards"][0]["id"]
    anon.post(f"{API}/batches/{b['id']}/cards/{cid}/retry", json={"lang": "ja"})
    b = _wait(anon, b["id"])
    assert "人工補的" in [f["value"] for f in b["contacts"][0]["fields"]]


# --------------------------------------------------------------------------- 資料庫升級、畫面、MCP

def test_migration_adds_card_langs_without_touching_existing_rows(tmp_path):
    from app.core import db
    p = tmp_path / "bc.sqlite"
    db.migrate(p, store._MIGRATIONS[:2])
    c = sqlite3.connect(p)
    c.execute("INSERT INTO bc_batches(id, owner, owner_label, created_at, updated_at, mode, status, notes, langs) "
              "VALUES ('b1','u','u',0,0,'single','review','{}','chi_tra+eng')")
    c.execute("INSERT INTO bc_pages(id, batch_id, idx, filename, page_no, width, height, status) "
              "VALUES ('p1','b1',0,'a.jpg',0,100,60,'done')")
    c.execute("INSERT INTO bc_cards(id, batch_id, page_id, idx, quad, status) VALUES ('c1','b1','p1',0,'[]','done')")
    c.commit()
    c.close()
    db.migrate(p, store._MIGRATIONS)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM bc_cards WHERE id='c1'").fetchone()
    assert r["langs"] == "" and r["status"] == "done", "舊的卡照讀，語言空白＝跟著批次"
    assert c.execute("SELECT langs FROM bc_batches WHERE id='b1'").fetchone()[0] == "chi_tra+eng"


def test_page_has_the_language_choice_and_buttons():
    from pathlib import Path
    t = (Path(__file__).resolve().parents[1] / "app/tools/business_card_reader/templates/business_card_reader.html").read_text(encoding="utf-8")
    assert 'name="bcOcrLang" value="zh" checked' in t and 'name="bcOcrLang" value="ja"' in t
    assert "fd.append('lang', ocrLang());" in t
    assert 'id="bcRetryLang"' in t and 'id="bcBatchRelang"' in t
    assert "'/batches/' + S.batch.id + '/lang'" in t


def test_mcp_tool_takes_lang():
    from app.mcp import registry
    tool = next(t for t in registry.TOOLS if t.name == "business_card_read") if hasattr(registry, "TOOLS") else None
    if tool is None:
        tool = next(t for t in registry.all_tools() if t.name == "business_card_read")
    assert tool.params["lang"]["enum"] == ["zh", "ja"] and tool.params["lang"]["default"] == "zh"


def test_real_ocr_misreads_on_a_japanese_card():
    """遠端 GPU EasyOCR 日文模型實測的讀法（杜撰的卡）：〒 認成「テ」、空格不見、.co.jp 的點不見。"""
    rows = [("株式会社サンプル", 30), ("営業本部第一営業部部長", 18), ("やまだたろう", 12), ("山田太郎", 44), ("Taro Yamada", 18),
            ("テ100-0005", 14), ("東京都千代田区丸の内1-2-3サンプルビル5F", 14), ("TEL 03-1234-5678 FAX03-1234-5679", 14),
            ("E-mail:taro.yamada@examplecoyjp", 14)]
    fs = kv(E.extract_fields(L(*rows), lang="ja"))
    assert vals(fs, "phone") == ["03-1234-5678"], "郵遞區號不是電話"
    assert fs["address"][0]["zip"] == "100-0005"
    assert vals(fs, "email") == ["taro.yamada@example.co.jp"] and fs["email"][0]["status"] == "check"
    assert vals(fs, "department") == ["営業本部第一営業部"] and vals(fs, "title") == ["部長"]
    assert vals(fs, "name") == ["山田太郎"]
    # 沒有〒、郵遞區號自己一行
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("山田太郎", 44), ("100-0005", 14), ("東京都千代田区丸の内1-2-3", 14)), lang="ja"))
    assert "phone" not in fs and fs["address"][0]["zip"] == "100-0005"


def test_jp_email_repair_only_touches_japanese_domains():
    assert JA.repair_jp_email("a@examplecojp") == ("a@example.co.jp", True)
    assert JA.repair_jp_email("a@example.ne.jp") == ("a@example.ne.jp", False)
    assert JA.repair_jp_email("a@example.com") == ("a@example.com", False)


# --------------------------------------------------------------------------- 實際 OCR 的讀法（杜撰卡，遠端 GPU EasyOCR 實測的錯法）

def test_english_model_reading_replaces_latin_lines_only():
    ja = [card_ocr.Line(text="伊藤美咲", box=[60, 230, 440, 100], conf=96),
          card_ocr.Line(text="NIisakiIto", box=[72, 340, 144, 32], conf=51),
          card_ocr.Line(text="Ennailmisaki.ito@exanplejp", box=[78, 696, 354, 32], conf=29),
          card_ocr.Line(text="電話 06-5522-9565", box=[77, 616, 435, 39], conf=96)]
    en = [card_ocr.Line(text="fJ @r 31 w", box=[66, 232, 441, 102], conf=6),
          card_ocr.Line(text="Misaki Ito", box=[72, 340, 144, 32], conf=81),
          card_ocr.Line(text="Email misaki ito@example jp", box=[78, 696, 354, 32], conf=53),
          card_ocr.Line(text="1EAG 06-5522-9565", box=[77, 616, 435, 39], conf=48)]
    assert card_ocr.merge_latin(ja, en) == 2
    assert [x.text for x in ja] == ["伊藤美咲", "Misaki Ito", "Email misaki ito@example jp", "電話 06-5522-9565"], \
        "日文的行照舊、英數的行換成英文模型讀的"


@pytest.mark.parametrize("line,want", [
    ("Email misaki ito@example jp", "misaki.ito@example.jp"),
    ("E-mail: misaki kimura@example.or:jp", "misaki.kimura@example.or.jp"),
    ("Email misaki sasaki@examplecojp", "misaki.sasaki@example.co.jp"),
    ("メール: shota kobayashi@example_orjp", "shota.kobayashi@example.or.jp"),
])
def test_email_dots_lost_by_ocr_are_put_back(line, want):
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("山田 太郎", 44), (line, 14)), lang="ja"))
    assert vals(fs, "email") == [want] and fs["email"][0]["status"] == "check"


@pytest.mark.parametrize("rows,want", [
    ([("https: |Iwww example or:jp", 14)], "https://www.example.or.jp"),
    ([("htps:llwwwexample_orjp", 14)], "https://www.example.or.jp"),
    ([("https:/", 14), ("llwwwexample.co jp", 14)], "https://www.example.co.jp"),
])
def test_url_mangled_by_ocr_is_put_back(rows, want):
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("山田 太郎", 44), *rows), lang="ja"))
    assert vals(fs, "url") == [want] and fs["url"][0]["status"] == "check"


@pytest.mark.parametrize("zipline", ["テ530-0001", "7530-0001", "1530-0001", "そ530-0001", "テ53の-0001"])
def test_postal_code_misreads(zipline):
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("山田 太郎", 44), (zipline, 14),
                               ("大阪府大阪市北区梅田1-1-1", 14), ("TEL 06-1234-5678", 14)), lang="ja"))
    assert fs["address"][0]["zip"] == "530-0001" and vals(fs, "phone") == ["06-1234-5678"]


def test_postal_code_line_after_the_address():
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("山田 太郎", 44), ("北海道札幌市中央区北一条西1-1-1", 14),
                               ("テ060-0001", 14)), lang="ja"))
    assert fs["address"][0]["zip"] == "060-0001" and vals(fs, "address") == ["北海道札幌市中央区北一条西1-1-1"]


def test_katakana_titles_and_departments_are_not_mistaken_for_a_reading():
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("マーケティンググループ", 18), ("やまだ まゆみ", 12),
                               ("山田真由美", 44), ("エンジニア", 18)), lang="ja"))
    assert vals(fs, "department") == ["マーケティンググループ"] and vals(fs, "title") == ["エンジニア"]
    assert vals(fs, "name") == ["山田真由美"]


def test_fukuoka_area_code_is_a_landline_on_japanese_cards_only():
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("山田 太郎", 44), ("PHONE092-771-7951 FAX092-771-7952", 14)), lang="ja"))
    assert vals(fs, "phone") == ["092-771-7951"] and "mobile" not in fs
    assert E.find_phones("0912-345-678")[0]["kind"] == "mobile", "台灣名片照舊"


def test_digits_read_as_letters_and_run_together_english():
    fs = kv(E.extract_fields(L(("(株) サンプル Sample TransportCorporation", 30), ("山田 太郎", 44), ("NaokiKobayashi", 18),
                               ("PHONEOI1-808-6310 FAXOI】-808-6311", 14)), lang="ja"))
    assert vals(fs, "phone") == ["011-808-6310"] and vals(fs, "fax") == ["011-808-6311"]
    assert vals(fs, "company_en") == ["Sample Transport Corporation"] and vals(fs, "name_en") == ["Naoki Kobayashi"]


def test_email_account_does_not_hide_the_english_name():
    """OCR 把 Email 帳號的點弄丟（「mayumiyamada@…」）：英文姓名不可以因為出現在 Email 裡被當成品牌字刪掉。"""
    fs = kv(E.extract_fields(L(("(株) サンプル", 30), ("山田真由美", 44), ("Mayumi Yamada", 18),
                               ("メール : mayumiyamada@example.co.jp", 14)), lang="ja"))
    assert vals(fs, "name_en") == ["Mayumi Yamada"]


def test_camel_split_leaves_web_addresses_and_emails_alone():
    assert JA.split_camel("NaokiKobayashi") == "Naoki Kobayashi"
    assert JA.split_camel("www.SampleWeb.example.jp") == "www.SampleWeb.example.jp"
    assert JA.split_camel("TaroYamada@example.jp") == "TaroYamada@example.jp"


def test_lookalike_misreads_on_japanese_cards():
    """OCR 的形近字：片假名中間的「三」是「ミ」、「~般社団法人」的「一」、地址門牌的 7 認成「て」、傳真區碼 06 認成 OG。"""
    fs = kv(E.extract_fields(L(("有限会社ナ三キ物産", 30), ("山田 太郎", 44), ("〒530-0001 大阪府大阪市北区梅田て-7-7", 14),
                               ("電話(06)5032-5016 FAXOG-5032-5017", 14)), lang="ja"))
    assert vals(fs, "company") == ["有限会社ナミキ物産"]
    assert vals(fs, "address") == ["大阪府大阪市北区梅田7-7-7"]
    assert vals(fs, "phone") == ["(06)5032-5016"] and vals(fs, "fax") == ["06-5032-5017"]
    fs = kv(E.extract_fields(L(("~般社団法人 サンプル協会", 30), ("山田 太郎", 44)), lang="ja"))
    assert vals(fs, "company") == ["一般社団法人 サンプル協会"]
    # 漢字本來就該在的地方不動：片假名後面接的是漢字、「て」後面不是門牌
    assert JA.fix_lookalikes("サンプル工業") == "サンプル工業"
    assert JA.fix_lookalikes("東京都港区芝公園にて-") == "東京都港区芝公園にて-"
    assert JA.fix_lookalikes("梅田て-7-7") == "梅田て-7-7", "不是地址的行不換"
    assert E._JP_PHONE_G.sub(E._jp_phone_g, "FAX OG-12") == "FAX OG-12", "數字太少不換"


def test_staff_is_an_english_title_not_a_name():
    """「主任 Senior Staff」：Staff 是職稱用字，不可以被當成英文姓名。"""
    fs = kv(E.extract_fields(L(("株式会社サンプル", 30), ("主任 Senior Staff", 18), ("山田 太郎", 44)), lang="ja"))
    assert vals(fs, "title_en") == ["Senior Staff"] and "name_en" not in fs

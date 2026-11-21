"""名片辨識：客戶回報的幾件事（2026-11-19）。資料全部杜撰。

1. Email 的 @ 被認成 0（O、Q）。
2. 在預先列出的空白欄位輸入後儲存 → 400「欄位狀態不正確」（畫面送了 status=empty）。
3. 名片簿的名片圖不能放大。
4. / 5. 正面中文、背面英文：合併之後英文職稱、部門把中文的蓋掉或不見 → 外文職稱 / 外文部門。
6. 掃描區還沒確認任何一位時匯出按鈕是灰的（範圍預設「已確認」）→ 這時自動用「全部」。
7. 手動加第二支手機、英文職稱（畫面另有真瀏覽器測試）。
8. 一張名片兩個公司名稱：都留、都標需確認、字最大的排第一。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.tools.business_card_reader import exporter as X
from app.tools.business_card_reader import extract as E
from app.tools.business_card_reader import importer as I

ROOT = Path(__file__).resolve().parents[1]
TPL = (ROOT / "app/tools/business_card_reader/templates/business_card_reader.html").read_text(encoding="utf-8")


def L(*rows):
    return [{"text": t, "height": h, "conf": 95, "box": [0, i * 30, 400, h]} for i, (t, h) in enumerate(rows)]


def kv(fields):
    out = {}
    for f in fields:
        out.setdefault(f["key"], []).append(f)
    return out


BASE = (("王小明", 40), ("業務經理", 20), ("範例科技股份有限公司", 26))


# --------------------------------------------------------------------------- 1. @ 被認成 0

@pytest.mark.parametrize("line,want", [
    ("E-mail: kevin.wang0example.com.tw", "kevin.wang@example.com.tw"),
    ("Email kevin0example.com", "kevin@example.com"),
    ("Mail: service0example.net", "service@example.net"),     # 只寫 Mail 也是標籤
    ("kevinwang0freemail.example.org", "kevinwang@freemail.example.org"),    # 常見信箱：沒有標籤也認得
    ("jason01230freemail.example.org", "jason0123@freemail.example.org"),    # 帳號本身有 0：只有一種切法
    ("E-mail: kevinQexample.com", "kevin@example.com"),
])
def test_at_sign_read_as_zero_is_put_back(line, want, monkeypatch):
    # 常見信箱（Gmail、Yahoo…）的判斷用杜撰的網域代替，測試檔不寫真實信箱
    monkeypatch.setattr(E, "_MAIL_HOSTS", E._MAIL_HOSTS + ("freemail.example.org",))
    fs = kv(E.extract_fields(L(*BASE, (line, 16))))
    assert [f["value"] for f in fs["email"]] == [want]
    f = fs["email"][0]
    assert f["status"] == "check" and "@" in f["reason"], "補過的要標需確認"
    assert "url" not in fs


def test_url_that_is_really_an_email_uses_the_cards_own_domain():
    fs = kv(E.extract_fields(L(*BASE, ("sales0example.com.tw", 16), ("www.example.com.tw", 16))))
    assert [f["value"] for f in fs["email"]] == ["sales@example.com.tw"]
    assert fs["email"][0]["raw"] == "sales0example.com.tw" and fs["email"][0]["status"] == "check"
    assert [f["value"] for f in fs["url"]] == ["www.example.com.tw"]


def test_without_evidence_only_warn_not_guess():
    """沒有標籤、名片上也沒有同網域的網址 / Email：不改成 Email（可能真的是網址），但提醒。"""
    fs = kv(E.extract_fields(L(*BASE, ("kevin.wang0example.com.tw", 16))))
    assert "email" not in fs
    assert fs["url"][0]["value"] == "kevin.wang0example.com.tw" and fs["url"][0]["status"] == "check"
    assert "Email" in fs["url"][0]["reason"]


def test_real_urls_and_emails_are_untouched():
    fs = kv(E.extract_fields(L(*BASE, ("www.abc0123.com", 16), ("kevin@example.com", 16),
                               ("Mail: abc@example.org", 16))))
    assert [f["value"] for f in fs["url"]] == ["www.abc0123.com"] and fs["url"][0]["status"] == "auto"
    assert sorted(f["value"] for f in fs["email"]) == ["abc@example.org", "kevin@example.com"]


def test_fix_at_refuses_ambiguous_splits():
    assert E.fix_at("kevin0wang0example.com") is None, "兩種切法都說得通：不猜"
    assert E.fix_at("kevin0wang0example.com", ["example.com"]) == "kevin0wang@example.com"
    assert E.fix_at("abc") is None and E.fix_at("a@example.com") is None


# --------------------------------------------------------------------------- 2. 空白欄位存不進去

def test_template_turns_a_filled_placeholder_into_a_confirmed_field():
    """預先列出的空白格（status=empty）開始填之後要變成 confirmed，不可以把 empty 送給伺服器。"""
    m = re.search(r"if \(placeholder && !cur\.fields\.includes\(f\)\) \{([^}]*)\}", TPL)
    assert m and "f.status = 'confirmed'" in m.group(1)


def test_server_accepts_the_old_pages_empty_status():
    from fastapi import HTTPException
    from app.tools.business_card_reader.router import _clean_fields
    out = _clean_fields([{"key": "mobile", "value": "0911-222-333", "status": "empty", "source": {"method": "manual"}}])
    assert out[0]["status"] == "confirmed"
    with pytest.raises(HTTPException):
        _clean_fields([{"key": "mobile", "value": "1", "status": "bogus"}])


# --------------------------------------------------------------------------- 3. 名片簿的名片圖可以放大

def test_book_card_images_open_in_the_lightbox():
    assert re.search(r'id="bkCards"[^>]*data-lightbox', TPL), "名片簿的名片圖要接放大檢視"
    assert '/static/js/lightbox.js' in TPL
    assert re.search(r"\.bk-card img \{[^}]*cursor: zoom-in", TPL)


# --------------------------------------------------------------------------- 4. / 5. 中英雙面

def test_english_title_and_department_go_to_their_own_fields():
    fs = kv(E.extract_fields(L(("Kevin Wang", 40), ("Sales Manager", 20), ("Sales Department", 18),
                               ("Example Tech Co., Ltd.", 22))))
    assert fs["title_en"][0]["value"] == "Sales Manager" and "title" not in fs
    assert fs["department_en"][0]["value"] == "Sales Department" and "department" not in fs
    # QR 的 vCard 也一樣
    q = dict((k, v) for k, v, _ in E.parse_qr("BEGIN:VCARD\nFN:Kevin Wang\nORG:Example Tech;Sales Dept.\n"
                                                "TITLE:Sales Manager\nEND:VCARD"))
    assert q["title_en"] == "Sales Manager" and q["department_en"] == "Sales Dept."


def _f(key, value):
    return {"id": key + value[:3], "key": key, "value": value, "status": "confirmed", "source": {"method": "manual"}}


def test_book_merge_keeps_both_sides_of_a_bilingual_card():
    """名片簿裡中文那位，再存進同一人的英文面（舊資料英文職稱放在「職稱」）：中文不可以被蓋掉。"""
    from app.tools.business_card_reader.books import _merge_fields
    zh = [_f("name", "王小明"), _f("title", "業務經理"), _f("department", "業務部"), _f("company", "範例科技股份有限公司"),
          _f("mobile", "0912-345-678")]
    en = [_f("name_en", "Kevin Wang"), _f("title", "Sales Manager"), _f("department", "Sales Dept."),
          _f("company", "Example Tech Co., Ltd."), _f("mobile", "0912-345-678")]
    for cur, inc in ((zh, en), (en, zh)):
        out = kv(_merge_fields(cur, inc))
        got = {k: [f["value"] for f in v] for k, v in out.items()}
        assert got["title"] == ["業務經理"] and got["title_en"] == ["Sales Manager"], got
        assert got["department"] == ["業務部"] and got["department_en"] == ["Sales Dept."], got
        assert got["company"] == ["範例科技股份有限公司"] and got["company_en"] == ["Example Tech Co., Ltd."], got
        assert got["name"] == ["王小明"] and got["name_en"] == ["Kevin Wang"]
        assert len(got["mobile"]) == 1, "同一支手機不重複"
    # 兩邊都是中文：照舊用新的（換工作、升職）
    out = kv(_merge_fields([_f("title", "業務經理")], [_f("title", "業務協理")]))
    assert [f["value"] for f in out["title"]] == ["業務協理"] and "title_en" not in out


def test_batch_merge_of_front_and_back_puts_english_into_the_foreign_fields(monkeypatch):
    from app.tools.business_card_reader import store
    keep = {"id": "k", "card_id": "c1", "card_ids": [], "notes": {}, "merged": "[]", "status": "check",
            "fields": [_f("name", "王小明"), _f("title", "業務經理"), _f("department", "業務部")]}
    other = {"id": "o", "card_id": "c2", "card_ids": [], "notes": {}, "status": "check", "idx": 1, "created_at": 0,
             "fields": [_f("name", "Kevin Wang"), _f("title", "Sales Manager"), _f("department", "Sales Dept."),
                        _f("email", "kevin@example.com")]}
    monkeypatch.setattr(store, "get_contact", lambda owner, bid, cid: {"k": keep, "o": other}[cid] if cid in ("k", "o")
                        else saved)
    saved = {}

    class C:
        def execute(self, sql, args=()):
            if sql.startswith("UPDATE"):
                import json
                saved.update(fields=json.loads(args[0]))
            return self

    import contextlib
    monkeypatch.setattr(store, "conn", lambda: C())
    monkeypatch.setattr(store.db, "tx", lambda c: contextlib.nullcontext())
    store.merge_contacts("u", "b", "k", "o")
    got = {}
    for f in saved["fields"]:
        got.setdefault(f["key"], []).append(f["value"])
    assert got["title"] == ["業務經理"] and got["title_en"] == ["Sales Manager"]
    assert got["department"] == ["業務部"] and got["department_en"] == ["Sales Dept."]
    assert got["name"] == ["王小明"] and got["name_en"] == ["Kevin Wang"] and got["email"] == ["kevin@example.com"]


def test_export_and_vcf_round_trip_of_the_foreign_fields():
    c = {"fields": [_f("name_en", "Kevin Wang"), _f("title_en", "Sales Manager"), _f("department_en", "Sales Dept."),
                    _f("company_en", "Example Tech Co., Ltd.")], "notes": {}}
    assert "title_en" in X.COLUMNS and "department_en" in X.COLUMNS
    assert X.LABELS_ZH["title_en"] == "外文職稱" and X.LABELS_ZH["department_en"] == "外文部門"
    v = X.export_vcf([c]).decode("utf-8")
    assert "TITLE:Sales Manager" in v and "X-JTDT-TITLE-EN:Sales Manager" in v, "通訊錄的職稱欄要有值"
    assert "ORG:Example Tech Co.\\, Ltd.;Sales Dept." in v
    back = I._read_vcf(v.encode("utf-8"))[0]["fields"]
    keys = sorted((f["key"], f["value"]) for f in back)
    assert ("title_en", "Sales Manager") in keys and ("title", "Sales Manager") not in keys, "讀回來不要多一份中文欄位"
    assert ("department_en", "Sales Dept.") in keys and ("department", "Sales Dept.") not in keys
    assert ("company_en", "Example Tech Co., Ltd.") in keys and ("company", "Example Tech Co., Ltd.") not in keys


def test_spreadsheet_headers_for_the_foreign_fields_are_recognised():
    m = I.auto_map(["姓名", "英文職稱", "外文部門", "職稱"])
    assert m[1] == "title_en" and m[2] == "department_en" and m[3] == "title"


# --------------------------------------------------------------------------- 6. 匯出範圍

def test_export_scope_defaults_to_all_when_nothing_is_confirmed():
    assert "S.scopeTouched" in TPL and "k.confirmed ? 'confirmed' : (k.contacts ? 'all' : 'confirmed')" in TPL


# --------------------------------------------------------------------------- 7. 手動加多值欄位

def test_multi_value_rows_have_a_plus_button():
    assert "bc-fmore" in TPL and "addFieldAfter(f.key, f)" in TPL
    assert "bk-more" in TPL
    assert "['title_en', tr('外文職稱')]" in TPL and "['department_en', tr('外文部門')]" in TPL


# --------------------------------------------------------------------------- 8. 兩個公司名稱

def test_two_company_names_are_both_kept_flagged_and_the_biggest_comes_first():
    fs = kv(E.extract_fields(L(("王小明 經理", 30), ("範例集團", 18), ("範例科技股份有限公司", 40))))
    assert [f["value"] for f in fs["company"]] == ["範例科技股份有限公司", "範例集團"]
    assert all(f["status"] == "check" and "公司名稱" in f["reason"] for f in fs["company"])


def test_the_same_company_printed_twice_is_one_field():
    fs = kv(E.extract_fields(L(("王小明 經理", 30), ("範例科技股份有限公司", 40), ("範例科技股份有限公司", 18))))
    assert len(fs["company"]) == 1 and fs["company"][0]["status"] == "auto"


# --------------------------------------------------------------------------- issue #59：中英文姓名在同一行

@pytest.mark.parametrize("line,zh,en", [
    ("王小明 Kevin Wang", "王小明", "Kevin Wang"),
    ("王小明Kevin Wang", "王小明", "Kevin Wang"),
    ("Kevin Wang 王小明", "王小明", "Kevin Wang"),
    ("王小明 KEVIN WANG", "王小明", "KEVIN WANG"),
    ("王小明 Kevin", "王小明", "Kevin"),
    ("陳 大 文 David Chen", "陳大文", "David Chen"),
    ("王小明 (Kevin Wang)", "王小明", "Kevin Wang"),
    ("王小明／Kevin Wang", "王小明", "Kevin Wang"),
])
def test_chinese_and_english_name_on_one_line_are_split(line, zh, en):
    """issue #59：名片上中英文姓名在同一行 → 原本整行放進外文姓名、姓名空白。"""
    fs = kv(E.extract_fields(L((line, 40), ("範例科技股份有限公司", 26), ("Tel: 02-2345-6789", 16))))
    assert [f["value"] for f in fs["name"]] == [zh]
    assert [f["value"] for f in fs["name_en"]] == [en]


def test_bilingual_title_and_department_on_one_line_are_split():
    fs = kv(E.extract_fields(L(("王小明", 40), ("業務經理 Sales Manager", 20), ("業務部 Sales Dept.", 18),
                               ("範例科技股份有限公司", 26))))
    assert [f["value"] for f in fs["title"]] == ["業務經理"] and [f["value"] for f in fs["title_en"]] == ["Sales Manager"]
    assert [f["value"] for f in fs["department"]] == ["業務部"] and [f["value"] for f in fs["department_en"]] == ["Sales Dept."]


def test_bilingual_brand_line_or_lowercase_word_is_not_a_name():
    fs = kv(E.extract_fields(L(("範例科技 Example Tech", 40), ("王小明", 30), ("範例科技股份有限公司", 26))))
    assert [f["value"] for f in fs["name"]] == ["王小明"] and "name_en" not in fs
    fs = kv(E.extract_fields(L(("車廂 car", 40), ("範例科技股份有限公司", 26))))
    assert "name_en" not in fs


# --------------------------------------------------------------------------- 直式名片橫放（2026-11-20）

def test_landscape_frame_that_reads_badly_also_tries_90_and_270(monkeypatch):
    """橫的框 0° 與 180° 都讀不太出來 → 再試 90° / 270°（直式名片橫放掃描）；0° 就讀得好就不多花時間。"""
    from PIL import Image
    from app.core import ocr_engine
    from app.tools.business_card_reader import card_ocr as O
    calls = []

    def fake(png, langs, engine=None, allow_local_easyocr=True):
        im = Image.open(__import__("io").BytesIO(png))
        calls.append(im.size)
        good = im.size[0] < im.size[1]                      # 轉成直的才讀得出來
        w = [{"text": "範例科技股份有限公司" * 4 if good else "~", "conf": 95 if good else 20,
              "left": 10, "top": 10, "width": 100, "height": 20}]
        return w, "fake"
    monkeypatch.setattr(ocr_engine, "recognize_image", fake)
    monkeypatch.setattr(O, "_reread_low_lines", lambda *a, **k: None)
    r = O.ocr_card(Image.new("RGB", (900, 540), "white"))
    assert r["rotation"] in (90, 270) and len(calls) == 3, calls
    calls.clear()
    monkeypatch.setattr(ocr_engine, "recognize_image", lambda png, langs, engine=None, allow_local_easyocr=True: (
        calls.append(1) or [{"text": "範例科技股份有限公司" * 4, "conf": 95, "left": 0, "top": 0, "width": 9, "height": 9}], "fake"))
    r = O.ocr_card(Image.new("RGB", (900, 540), "white"))
    assert r["rotation"] == 0 and len(calls) == 1, "0° 就讀得好：只讀一次"

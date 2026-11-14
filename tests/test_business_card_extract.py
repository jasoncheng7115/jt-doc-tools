"""名片辨識：OCR 行 → 欄位（規則擷取）、QR、行的組合。資料全部杜撰。"""
from __future__ import annotations

import pytest

from app.tools.business_card_reader import card_ocr as O
from app.tools.business_card_reader import extract as E


def L(*rows):
    """[(文字, 字高)] → OCR 行。"""
    return [{"text": t, "box": [10, 10 + i * 40, 300, 30], "conf": 92, "height": h} for i, (t, h) in enumerate(rows)]


def kv(fields):
    out = {}
    for f in fields:
        out.setdefault(f["key"], []).append(f)
    return out


def test_typical_taiwanese_card():
    fs = kv(E.extract_fields(L(
        ("範例科技股份有限公司", 32), ("業務部", 20), ("王小明", 44), ("業務副理", 20),
        ("電話：(02)2345-6789 分機 302", 18), ("手機：0912-345-678", 18), ("傳真：02-2345-6790", 18),
        ("Email: kevin.sample@example.com", 18), ("www.example.com.tw", 18),
        ("100 台北市中正區範例路 1 段 2 號 3 樓", 18), ("統一編號：04595257", 18),
    )))
    assert fs["company"][0]["value"] == "範例科技股份有限公司"
    assert fs["department"][0]["value"] == "業務部"
    assert fs["name"][0]["value"] == "王小明"
    assert fs["title"][0]["value"] == "業務副理"
    ph = fs["phone"][0]
    assert ph["norm"] == "0223456789" and ph["ext"] == "302" and ph["status"] == "auto"
    assert fs["mobile"][0]["norm"] == "0912345678"
    assert fs["fax"][0]["norm"] == "0223456790"
    assert fs["email"][0]["value"] == "kevin.sample@example.com"
    assert fs["url"][0]["value"] == "www.example.com.tw"
    assert fs["address"][0]["zip"] == "100" and fs["address"][0]["value"].startswith("台北市")
    assert fs["vat"][0]["value"] == "04595257" and fs["vat"][0]["status"] == "auto", "統編開頭 0 要保留，檢查碼要過"


def test_largest_text_is_not_automatically_the_name():
    """規格：不能把最大字一律當姓名 —— 公司名、品牌字常常最大。"""
    fs = kv(E.extract_fields(L(("範例科技股份有限公司", 80), ("王小明", 40), ("經理", 20))))
    assert fs["name"][0]["value"] == "王小明"
    assert fs["company"][0]["value"] == "範例科技股份有限公司"


@pytest.mark.parametrize("line,kind,norm,ext", [
    ("Tel: +886-2-2345-6789 ext. 12", "phone", "+886223456789", "12"),
    ("TEL:(886-2)2345-6789", "phone", "886223456789", ""),
    ("M: 0912 345 678", "mobile", "0912345678", ""),
    ("Fax: 02-2345-6790", "fax", "0223456790", ""),
    ("免付費服務專線：0800-000-123", "phone", "0800000123", ""),
    ("總機 04-2345-6789 轉 012", "phone", "0423456789", "012"),
    ("Te|: (02)2345-0001", "phone", "0223450001", ""),           # OCR 把 l 認成 |
    ("電話:(02)2345-0002", "phone", "0223450002", ""),           # 括號跟標籤黏在一起
])
def test_phone_kinds_labels_and_extensions(line, kind, norm, ext):
    phones = E.find_phones(line)
    assert phones, line
    p = phones[0]
    assert (p["kind"], p["norm"], p["ext"]) == (kind, norm, ext)


def test_country_code_is_not_added_when_not_printed():
    assert E.normalize_phone("02-2345-6789") == "0223456789", "國碼沒印出來就不加 +886"


def test_masked_or_short_numbers_need_checking():
    fs = kv(E.extract_fields(L(("手機：0933-  -521", 18))))
    m = fs["mobile"][0]
    assert m["status"] == "check" and "位數" in m["reason"]


def test_vat_checksum():
    assert E.vat_checksum_ok("04595257")
    assert E.vat_checksum_ok("13579004")
    assert not E.vat_checksum_ok("12345678")
    fs = kv(E.extract_fields(L(("統編：12345678", 18))))
    assert fs["vat"][0]["status"] == "check"


def test_vat_is_not_mistaken_for_a_phone_and_not_guessed():
    fs = kv(E.extract_fields(L(("範例科技股份有限公司", 30), ("統一編號 04595257", 18))))
    assert "phone" not in fs and fs["vat"][0]["value"] == "04595257"
    fs = kv(E.extract_fields(L(("範例科技股份有限公司", 30))))
    assert "vat" not in fs, "統編只收名片上明列的，不依公司名稱猜"


def test_email_without_at_sign_is_kept_but_flagged():
    fs = kv(E.extract_fields(L(("E-mail: kevinoexample.com", 18))))
    assert fs["email"][0]["status"] == "check"


def test_url_ocr_cleanup():
    assert E._clean_url("http:llwww.example.com.tw") == "http://www.example.com.tw"
    assert E._clean_url("llwww.example.com") == "www.example.com"


def test_english_card_and_two_line_address():
    fs = kv(E.extract_fields(L(
        ("Kevin Sample", 40), ("Sales Manager", 20), ("Purchasing Dept.", 18), ("Example Tech Co., Ltd.", 22),
        ("No. 1, Sec. 2, Example Rd., Zhongzheng Dist.,", 16), ("Taipei City 100, Taiwan (R.O.C.)", 16),
    )))
    assert fs["name_en"][0]["value"] == "Kevin Sample"
    assert fs["title"][0]["value"] == "Sales Manager"
    assert fs["department"][0]["value"] == "Purchasing Dept."
    assert fs["company_en"][0]["value"] == "Example Tech Co., Ltd."
    assert len(fs["address"]) == 1 and "Taipei City" in fs["address"][0]["value"], "英文地址兩行接成一筆"


def test_role_word_before_title_is_not_a_name():
    fs = kv(E.extract_fields(L(("採購副理", 20), ("王小明", 40))))
    assert fs["name"][0]["value"] == "王小明" and fs["title"][0]["value"] == "採購副理"


def test_place_names_are_not_names():
    fs = kv(E.extract_fields(L(("範例東區", 40), ("西門店", 40))))
    assert "name" not in fs


def test_low_confidence_lines_are_flagged():
    lines = L(("電話：02-2345-6789", 18))
    lines[0]["conf"] = 20
    fs = kv(E.extract_fields(lines))
    assert fs["phone"][0]["status"] == "check"


def test_im_accounts():
    fs = kv(E.extract_fields(L(("LINE ID: kevin_example", 18), ("QQ: 1234500678", 18))))
    assert {f["value"] for f in fs["im"]} == {"kevin_example", "1234500678"}
    assert "phone" not in fs, "通訊帳號的數字不可以當電話"


def test_every_field_carries_its_source_box():
    fs = E.extract_fields(L(("電話：02-2345-6789", 18)))
    x, y, w, h = fs[0]["source"]["box"]
    assert fs[0]["source"]["method"] == "ocr"
    assert 10 <= x and x + w <= 310.01 and (y, h) == (10, 30), "框在那一行裡面"
    assert x > 10, "標籤「電話：」不框進去，只框號碼"


# --------------------------------------------------------------------------- 統編被當成電話（使用者 2026-11-13 實測回報）
# OCR 把標籤本身認錯（「一」變破折號 / 注音ㄧ、「統」變形近字、冒號變間隔號），標籤一對不上 8 碼就被當成電話。
# 號碼都是杜撰的（有效檢查碼：04595257、13579004；無效：12345678）。

@pytest.mark.parametrize("text", [
    "統一編號：04595257", "統—編號:04595257", "統-編號 04595257", "統ㄧ編號：04595257", "统一编号 04595257",
    "紒一編號 · 04595257", "統編.04595257", "統 一 編 號 04595257", "VAT No. 04595257", "Tax ID: 04595257",
    "GUI No.04595257", "統一編號04595257",
])
def test_vat_with_misread_label_is_not_a_phone(text):
    fs = kv(E.extract_fields(L((text, 18))))
    assert "phone" not in fs and "mobile" not in fs, f"{text!r} 被當成電話"
    assert fs["vat"][0]["value"] == "04595257"


def test_label_on_the_line_above():
    fs = kv(E.extract_fields(L(("統一編號", 18), ("04595257", 18))))
    assert "phone" not in fs and fs["vat"][0]["value"] == "04595257"


def test_bare_eight_digits_without_a_phone_label_is_a_vat_candidate():
    fs = kv(E.extract_fields(L(("13579004", 18))))
    assert "phone" not in fs and fs["vat"][0]["status"] == "auto"
    fs = kv(E.extract_fields(L(("12345678", 18))))
    assert "phone" not in fs and fs["vat"][0]["status"] == "check", "檢查碼不符：仍列統編但要確認"
    assert "電話" in fs["vat"][0]["reason"]


@pytest.mark.parametrize("text,kind", [
    ("TEL: 13579004", "phone"), ("+886 2 13579004", "phone"), ("(02)13579004", "phone"),
    ("電話 1357-9004", "phone"), ("02-1357-9004", "phone"), ("0912345678", "mobile"), ("傳真 13579004", "fax"),
])
def test_real_phones_stay_phones(text, kind):
    fs = kv(E.extract_fields(L((text, 18))))
    assert kind in fs and "vat" not in fs, f"{text!r} 被當成統編"


def test_valid_checksum_overrides_low_ocr_confidence():
    line = [{"text": "統一編號：04595257", "box": [10, 10, 300, 30], "conf": 30, "height": 18}]
    v = E.extract_fields(line)[0]
    assert v["key"] == "vat" and v["status"] == "auto", "檢查碼過了就不必再因信心低標需確認"
    line[0]["text"] = "統一編號：12345678"
    assert E.extract_fields(line)[0]["status"] == "check"


def test_address_and_vat_on_one_line_are_split_and_boxed_separately():
    line = [{"text": "台北市中正區範例路10號12樓 統一編號04595257", "box": [100, 50, 800, 40], "conf": 95, "height": 30}]
    fs = {f["key"]: f for f in E.extract_fields(line)}
    assert fs["address"]["value"] == "台北市中正區範例路10號12樓"
    assert fs["vat"]["value"] == "04595257"
    ax, _, aw, _ = fs["address"]["source"]["box"]
    vx, _, vw, _ = fs["vat"]["source"]["box"]
    assert ax == 100 and ax + aw < vx + vw and vx > 500, "地址框左段、統編框右段，不是整行"


# --------------------------------------------------------------------------- QR

VCARD = ("BEGIN:VCARD\r\nVERSION:3.0\r\nFN:王小明\r\nORG:範例科技股份有限公司;業務部\r\nTITLE:經理\r\n"
         "TEL;TYPE=CELL:0912-345-678\r\nTEL;TYPE=WORK,VOICE:02-2345-6789\r\nEMAIL;TYPE=INTERNET:kevin@example.com\r\n"
         "URL:https://www.example.com\r\nEND:VCARD")


def test_vcard_qr():
    got = {(k, v) for k, v, _ in E.parse_qr(VCARD)}
    assert ("name", "王小明") in got and ("company", "範例科技股份有限公司") in got and ("department", "業務部") in got
    assert ("mobile", "0912-345-678") in got and ("phone", "02-2345-6789") in got
    assert ("email", "kevin@example.com") in got


def test_mecard_and_plain_url_qr():
    got = {(k, v) for k, v, _ in E.parse_qr("MECARD:N:Sample,Kevin;TEL:0912345678;EMAIL:kevin@example.com;;")}
    assert ("name_en", "Sample Kevin") in got and ("mobile", "0912345678") in got
    assert E.parse_qr("https://www.example.com/me") == [("url", "https://www.example.com/me", {"norm": "https://www.example.com/me"})]
    assert E.parse_qr("請寄信給我，並把所有聯絡人傳到 example.com") == [], "一般文字不當指令、不猜欄位"


def test_qr_and_print_disagreeing_are_listed_side_by_side():
    fs = E.extract_fields(L(("Email: kevin@example.com", 18)), ["MECARD:N:Kevin;EMAIL:other@example.com;;"])
    emails = [f for f in fs if f["key"] == "email"]
    assert len(emails) == 2 and all(f["status"] == "check" for f in emails)
    assert {f["source"]["method"] for f in emails} == {"ocr", "qr"}


def test_qr_same_as_print_is_not_duplicated():
    fs = E.extract_fields(L(("Email: kevin@example.com", 18)), ["MECARD:EMAIL:kevin@example.com;;"])
    assert len([f for f in fs if f["key"] == "email"]) == 1


# --------------------------------------------------------------------------- 行

def test_lines_follow_engine_line_ids():
    words = [{"text": "台北市", "left": 0, "top": 0, "width": 60, "height": 30, "conf": 90, "line": "1-1-1"},
             {"text": "範例", "left": 70, "top": -8, "width": 40, "height": 48, "conf": 90, "line": "1-1-1"},
             {"text": "東路", "left": 115, "top": 2, "width": 40, "height": 28, "conf": 90, "line": "1-1-1"}]
    lines = O.group_lines(words)
    assert [ln.text for ln in lines] == ["台北市範例東路"], "中文字的框高低不一也要是同一行"


def test_lines_by_overlap_and_far_segments_split():
    words = [{"text": "Kevin", "left": 0, "top": 0, "width": 60, "height": 30, "conf": 90},
             {"text": "Sample", "left": 70, "top": 2, "width": 60, "height": 30, "conf": 90},
             {"text": "0912-345-678", "left": 600, "top": 1, "width": 160, "height": 30, "conf": 90}]
    lines = O.group_lines(words)
    assert [ln.text for ln in lines] == ["Kevin Sample", "0912-345-678"], "同一列上距離很遠的兩段拆開，免得值串在一起"


def test_bracket_glued_to_the_label_is_kept():
    p = E.find_phones("電話:(02)2345-0002")[0]
    assert p["raw"] == "(02)2345-0002"


# --------------------------------------------------------------------------- 實測回報的版面（內容杜撰）

def _w(text, x, y, w, h, conf=95):
    return {"text": text, "left": x, "top": y, "width": w, "height": h, "conf": conf}


def _fields_from_words(words):
    return kv(E.extract_fields([ln.to_dict() for ln in O.group_lines(words)]))


def test_small_title_next_to_big_name_on_the_same_row():
    """名片常把小字職稱跟大字姓名排在同一列：不可以黏成一欄（實測：職稱跟英文名字連在一起、中文名字沒抓到）。"""
    fs = _fields_from_words([
        _w("資訊經理", 560, 40, 120, 28), _w("王 小 明", 720, 20, 260, 70),
        _w("Manager", 560, 110, 110, 24), _w("Kevin Sample", 720, 100, 280, 50),
        _w("範例企業股份有限公司", 40, 260, 900, 60),
    ])
    assert fs["name"][0]["value"] == "王小明"
    assert fs["title"][0]["value"] in ("資訊經理",) and "Manager" in [f["value"] for f in fs["title"]]
    assert fs["name_en"][0]["value"] == "Kevin Sample"


@pytest.mark.parametrize("line,title,name", [
    ("Manager Kevin Sample", "Manager", "Kevin Sample"),
    ("Kevin Sample, Sales Manager", "Sales Manager", "Kevin Sample"),
])
def test_english_title_and_name_in_one_phrase_are_split(line, title, name):
    fs = kv(E.extract_fields(L((line, 30))))
    assert fs["title"][0]["value"] == title and fs["name_en"][0]["value"] == name


@pytest.mark.parametrize("line", ["Deputy Program Director", "Community Manager", "Director of Sales"])
def test_multi_word_titles_stay_whole(line):
    fs = kv(E.extract_fields(L((line, 30))))
    assert fs["title"][0]["value"] == line and "name_en" not in fs


def test_two_column_card_phone_left_english_address_right():
    """雙欄：左欄電話、右欄三行英文地址。電話那一行不可以變成地址；英文地址三行接成一筆（實測：電話被算成第二筆地址）。"""
    fs = _fields_from_words([
        _w("515 彰化縣大村鄉範例路三段 1 號", 40, 300, 560, 30),
        _w("No. 1, Sec. 3, Example Rd., Dacun", 700, 300, 560, 30),
        _w("統一編號：04595257", 40, 340, 400, 30),
        _w("Township, Example County 515,", 700, 340, 520, 30),
        _w("電話：+886-4-2345-6789 #606", 40, 380, 460, 30),
        _w("Taiwan (R.O.C.)", 700, 380, 260, 30),
        _w("wWW.example.com.tw", 700, 420, 330, 30),
    ])
    addrs = [f["value"] for f in fs["address"]]
    assert len(addrs) == 2, addrs
    assert any("No. 1, Sec. 3" in a and "Taiwan" in a for a in addrs), "英文地址三行要接成一筆"
    assert not any("886" in a for a in addrs), "電話不可以變成地址"
    assert fs["phone"][0]["ext"] == "606"
    assert fs["url"][0]["value"] == "www.example.com.tw"


def test_brand_words_repeated_in_company_are_not_names():
    fs = kv(E.extract_fields(L(("範例", 60), ("範例企業股份有限公司", 30), ("王小明", 44), ("經理", 20))))
    assert fs["name"][0]["value"] == "王小明"


def test_address_followed_by_vat_on_the_same_line_is_trimmed():
    fs = kv(E.extract_fields(L(("台北市中正區範例路 10 號 12 樓 統編 04595257", 18))))
    assert fs["address"][0]["value"] == "台北市中正區範例路 10 號 12 樓" and fs["vat"][0]["value"] == "04595257"


@pytest.mark.parametrize("text", ["No. 1, Sec. 3, Example Rd., Dacun", "9F, No. 12, Example Rd.", "Example County 515, Taiwan"])
def test_english_address_words_with_periods_match(text):
    """「No.」「Rd.」「Sec.」這類帶句點的縮寫要比對得到（原本接 `\\b` 永遠比對不到）。"""
    assert len(E.EN_ADDR.findall(text)) >= 2


# --------------------------------------------------------------------------- 網址與 Email 的 OCR 壞法（資料杜撰）

@pytest.mark.parametrize("text,want", [
    ("www.example com.tw", "www.example.com.tw"),            # 中間多一個空白
    ("WWW example com tW", "www.example.com.tw"),            # 每一段都被拆開、國別碼大寫
    ("wwwexample.com.tw", "www.example.com.tw"),             # www 後面少了點
    ("Website:wvww.example com.fw", "www.example.com.tw"),   # 標籤黏著、wvww、.tw 認成 .fw
    ("網址 :http:wwwexample.com.tw", "http://www.example.com.tw"),
    ("HOME PAGE: http:llwwwexample.com.tW", "http://www.example.com.tw"),
    ("http:lblog.example.com/kevin", "http://blog.example.com/kevin"),
    ("Www example.com.w新店", "www.example.com.tw"),          # 後面黏著中文、.w
])
def test_broken_urls_are_joined_back(text, want):
    fs = kv(E.extract_fields(L((text, 18))))
    assert [f["value"] for f in fs["url"]] == [want]


@pytest.mark.parametrize("text", ["@example.com.tw", "kevin @gmail.com", "E-mail:kevin Oexample.com", "com.tw"])
def test_email_fragments_are_not_urls(text):
    assert "url" not in kv(E.extract_fields(L((text, 18))))


def test_url_cut_short_is_completed_from_the_email_domain():
    fs = kv(E.extract_fields(L(("www.example", 18), ("kevin@example.com.tw", 18))))
    assert fs["url"][0]["value"] == "www.example.com.tw" and fs["url"][0]["status"] == "check"
    fs = kv(E.extract_fields(L(("www.example", 18))))
    assert fs["url"][0]["status"] == "check" and "不完整" in fs["url"][0]["reason"]
    fs = kv(E.extract_fields(L(("www.sample demo.example.com", 18), ("kevin@sample-demo.example.com", 18))))
    assert fs["url"][0]["value"] == "www.sample-demo.example.com", "連字號依 Email 網域補回"


def test_email_split_over_two_lines_and_glued_label():
    fs = kv(E.extract_fields(L(("E-Mailkevin.sample", 18), ("@example.de", 18))))
    assert fs["email"][0]["value"] == "kevin.sample@example.de"
    fs = kv(E.extract_fields(L(("02-2345-6789", 18), ("@example.com", 18))))
    assert all("02-2345" not in f["value"] for f in fs.get("email", [])), "電話號碼不可以接成 Email"


def test_at_sign_read_as_q_is_restored_from_the_url():
    fs = kv(E.extract_fields(L(("Email kevinQexample.com", 18), ("WWW example com", 18))))
    assert fs["email"][0]["value"] == "kevin@example.com" and fs["email"][0]["status"] == "check"


# --------------------------------------------------------------------------- 英文地址被拆開（版面情況取自真實名片，字是杜撰的）

def _ln(text, x, y, w, h):
    return {"text": text, "box": [x, y, w, h], "conf": 95, "height": h}


def test_english_address_split_into_pieces_on_one_row():
    """同一列被 OCR 拆成三段（門牌｜Sec.｜路名），下一列是城市：要接成一筆、順序照左到右再往下。"""
    lines = [_ln("Kevin Sample", 450, 500, 350, 100),
             _ln("Example Rd.", 940, 650, 280, 60), _ln("4F. No. 9 Lane 12", 460, 653, 350, 44),
             _ln("Sec. 3,", 820, 659, 120, 42), _ln("1", 1210, 679, 29, 24),
             _ln("Taipei, Taiwan, R.O.C.", 457, 697, 412, 54)]
    fs = kv(E.extract_fields(lines))
    assert [f["value"] for f in fs["address"]] == ["4F. No. 9 Lane 12 Sec. 3, Example Rd. Taipei, Taiwan, R.O.C."]


def test_english_address_continues_when_a_tall_box_overlaps_the_next_line():
    lines = [_ln("No.32, Sec.2, Example Rd. Sample Dist., TEL:(02)2345-6789", 90, 733, 1220, 97),
             _ln("New Taipei City, Taiwan R.O.C.", 85, 783, 596, 61)]
    fs = kv(E.extract_fields(lines))
    assert fs["address"][0]["value"] == "No.32, Sec.2, Example Rd. Sample Dist. New Taipei City, Taiwan R.O.C."
    assert fs["phone"][0]["norm"] == "0223456789"


def test_a_fragment_inside_the_next_line_does_not_split_the_address():
    lines = [_ln("No.1-2, Example N. Rd. Sample Dist.,", 238, 578, 832, 73),
             _ln("City", 435, 629, 124, 82), _ln("Sample City 10001, Taiwan (R.O.C.)", 239, 631, 862, 78)]
    fs = kv(E.extract_fields(lines))
    assert [f["value"] for f in fs["address"]] == ["No.1-2, Example N. Rd. Sample Dist. Sample City 10001, Taiwan (R.O.C.)"]


# --------------------------------------------------------------------------- 信心低的行放大再讀一次

def _fake_engine(monkeypatch, reread):
    """第一次（整張卡）回一行信心低的公司名；之後（補讀那一行）回 `reread` 給的結果。"""
    from app.core import ocr_engine
    calls = []

    def fake(png, langs, engine=None, **kw):
        calls.append(len(png))
        if len(calls) == 1:
            return [{"text": "範例堡股份有限公司", "left": 40, "top": 40, "width": 600, "height": 40, "conf": 35},
                    {"text": "王小明", "left": 40, "top": 120, "width": 200, "height": 60, "conf": 95}], "fake"
        return reread, "fake"
    monkeypatch.setattr(ocr_engine, "recognize_image", fake)
    monkeypatch.setattr(ocr_engine, "local_easyocr_safe", lambda: True)
    return calls


def test_low_confidence_line_gets_an_alternative_reading_not_a_silent_change(monkeypatch):
    """補讀的結果只當「另一種讀法」給人選（真實名片實測自動換的話好壞參半），原本的讀法與信心低的標記都留著。"""
    from PIL import Image
    calls = _fake_engine(monkeypatch, [{"text": "範例疊股份有限公司", "left": 20, "top": 70, "width": 900, "height": 80, "conf": 88}])
    res = O.ocr_card(Image.new("RGB", (900, 540), "white"), try_rotations=False)
    low = next(ln for ln in res["lines"] if ln.text == "範例堡股份有限公司")
    assert low.alt == "範例疊股份有限公司" and low.to_dict()["alt"] == "範例疊股份有限公司"
    assert "alt" not in next(ln for ln in res["lines"] if ln.text == "王小明").to_dict()
    assert len(calls) == 2, "只補讀信心低的那一行"
    f = E.extract_fields([ln.to_dict() for ln in res["lines"]])
    assert next(x for x in f if x["key"] == "company")["status"] == "check", "信心低的標記不可以因為有另一種讀法就消失"


@pytest.mark.parametrize("reread", [
    [{"text": "範例堡股份有限公司", "left": 0, "top": 70, "width": 9, "height": 9, "conf": 38}],         # 沒有明顯比較好
    [{"text": "範例疊股份有限公司 電話 02-2345-6789 傳真", "left": 0, "top": 70, "width": 9, "height": 9, "conf": 95}],  # 讀到旁邊的東西
    [{"text": "範例股份有限公司", "left": 0, "top": 70, "width": 9, "height": 9, "conf": 99}],    # 少了一個字（信心反而高）：不換
    [],
])
def test_reread_keeps_the_first_reading_unless_clearly_better(monkeypatch, reread):
    from PIL import Image
    _fake_engine(monkeypatch, reread)
    res = O.ocr_card(Image.new("RGB", (900, 540), "white"), try_rotations=False)
    low = next(ln for ln in res["lines"] if ln.text == "範例堡股份有限公司")
    assert not low.alt, "沒有明顯比較好的讀法就不給"


def test_several_low_lines_are_reread_in_one_ocr_call(monkeypatch):
    """幾行信心低的疊成一張圖只多讀一次，依上下位置分回各行。"""
    from PIL import Image
    from app.core import ocr_engine
    calls = []

    def fake(png, langs, engine=None, **kw):
        calls.append(1)
        if len(calls) == 1:
            return [{"text": "範例堡股份有限公司", "left": 40, "top": 40, "width": 600, "height": 40, "conf": 30},
                    {"text": "業務經哩", "left": 40, "top": 200, "width": 300, "height": 30, "conf": 30}], "fake"
        # 疊起來的圖：第一行在上、第二行在下（第一段約 60～180、第二段在它下面）
        return [{"text": "範例疊股份有限公司", "left": 20, "top": 80, "width": 900, "height": 80, "conf": 90},
                {"text": "業務經理", "left": 20, "top": 260, "width": 500, "height": 80, "conf": 90}], "fake"
    monkeypatch.setattr(ocr_engine, "recognize_image", fake)
    monkeypatch.setattr(ocr_engine, "local_easyocr_safe", lambda: True)
    res = O.ocr_card(Image.new("RGB", (900, 540), "white"), try_rotations=False)
    assert len(calls) == 2, "補讀只多一次"
    assert {ln.alt for ln in res["lines"]} == {"範例疊股份有限公司", "業務經理"}

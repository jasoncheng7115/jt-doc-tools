"""名片辨識：直書（縦書き）的日文名片（2026-11-21）。

一般的 OCR 只讀橫排，直排的欄會被切成「目」「豊」「9」這種碎片（實測：杜撰直式卡欄位正確率 14.7%）。
`vertical.py` 從影像找出直排的欄、等距切字、排成橫的一行再讀；`card_ocr._vertical_pass` 決定要不要換掉原本的讀法。
卡片內容全部杜撰；需要 Noto Sans CJK 字型（沒有就略過）。
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.tools.business_card_reader import card_ocr, extract as E, vertical as V
from tests import bc_synth as S

pytestmark = pytest.mark.skipif(not S.has_cjk_font(), reason="需要 Noto Sans CJK 字型")

SERIF = Path("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc")
FW = str.maketrans("0123456789-", "０１２３４５６７８９︱")

#: 由右到左：（文字、字級）。數字用全形、直書的「-」畫成「︱」
COLUMNS = [("株式会社サンプル", 30), ("営業部", 22), ("部長", 22), ("山田　太郎", 60),
           ("〒100-0005".translate(FW), 19), ("東京都千代田区丸の内1-2-3".translate(FW), 19),
           ("電話　03-1234-5678".translate(FW), 19), ("ＦＡＸ　03-1234-5679".translate(FW), 19)]
EMAIL = "taro.yamada@example.jp"


def _font(size, serif=False):
    return ImageFont.truetype(str(SERIF if serif else S.CJK_FONT), size, index=0)


def _vtext(d, x, y, text, size, serif=False, pitch=1.12):
    f = _font(size, serif)
    for ch in text:
        if ch == "　":
            y += size * pitch
            continue
        b = f.getbbox(ch)
        d.text((x + (size - (b[2] - b[0])) / 2 - b[0], y), ch, font=f, fill="black")
        y += size * pitch


def vertical_card(serif=False, blur=0.0) -> tuple[Image.Image, list[int]]:
    """杜撰的直式名片；回 (圖, 每一欄的 x)。"""
    im = Image.new("RGB", (640, 1060), "white")
    d = ImageDraw.Draw(im)
    xs = [560, 500, 460, 350, 290, 252, 214, 176]           # 每一欄的左緣（欄與欄之間至少隔半個字）
    for x, (text, size) in zip(xs, COLUMNS):
        _vtext(d, x, 160 if size < 30 else 80, text, size, serif)
    d.text((40, 960), "E-mail: " + EMAIL, font=_font(18, serif), fill="black")
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return im, xs


# --------------------------------------------------------------------------- 找欄、切字

@pytest.mark.parametrize("serif", [False, True])
def test_columns_are_found_right_to_left_with_one_cell_per_character(serif):
    if serif and not SERIF.exists():
        pytest.skip("沒有 Noto Serif CJK")
    im, xs = vertical_card(serif=serif)
    cols = V.columns(im)
    assert len(cols) == len(COLUMNS), [c.box for c in cols]
    for c, x, (text, _size) in zip(cols, xs, COLUMNS):
        assert abs(c.box[0] - x) < 15, (c.box, x, text)
        cells = sum(1 for a, b in c.cells if b > a)
        assert cells == len(text.replace("　", "")), (text, cells, c.cells)
    # 全形空白是一個空格子，不是兩欄
    name = cols[3]
    assert len(name.cells) == 5 and sum(1 for a, b in name.cells if b <= a) == 1
    # 底部橫排的 Email：字母上下疊起來不算一欄
    assert all(c.box[1] + c.box[3] < 900 for c in cols)


def test_big_horizontal_lines_stacked_are_not_columns():
    """橫式名片：公司、部門、職稱、姓名幾行靠左疊在一起，「往下黏」會黏成細長的一條 ——
    單看影像會誤判（實測），所以 `_vertical_pass` 要用原本讀得好的橫排字擋掉。這裡先確認影像這一層真的會誤判，
    擋的那一層在下面的整合測試驗。"""
    im = S.card([("有限会社サンプル", 44), ("営業部", 26), ("部長", 26), ("小林 太郎", 80)])
    assert V.find_columns(im), "如果影像這一層已經不會誤判，下面那支整合測試的前提就不成立了"


# --------------------------------------------------------------------------- 「︱」與「1」

def _glyph(ch, size, serif, blur=0.0):
    im = Image.new("L", (size * 2, int(size * 1.4)), 255)
    f = _font(size, serif)
    ImageDraw.Draw(im).text((size // 2, int(size * 0.1)), ch, font=f, fill=0)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(im)


@pytest.mark.parametrize("serif", [False, True])
@pytest.mark.parametrize("size", [14, 19, 24])
@pytest.mark.parametrize("blur", [0.0, 0.6])
def test_vertical_dash_is_turned_but_digit_one_is_not(serif, size, blur):
    """直書的「︱」排成橫的要轉 90°（變回「-」）；全形「１」也是一條直線，但上面有一撇、明體還有襯線，不可以轉
    （實測：轉了之後「011-184-3871」讀成「0ーーIー84-387-」）。"""
    if serif and not SERIF.exists():
        pytest.skip("沒有 Noto Serif CJK")
    col = size * 0.9                     # 欄寬（有「電話」這種標籤時接近字級）
    assert V.is_vertical_bar(_glyph("︱", size, serif, blur), col)
    assert not V.is_vertical_bar(_glyph("１", size, serif, blur), col)
    assert not V.is_vertical_bar(_glyph("一", size, serif, blur), col)


# --------------------------------------------------------------------------- 讀完之後的整理

@pytest.mark.parametrize("raw, want", [
    ("06一4655-6683", "06-4655-6683"),
    ("0 8 0-一9 0 8 1一3 6 66", "080-9081-3666"),
    ("電話 092 | 9 48 | 4043", "電話 092-948-4043"),
    ("神奈川県横浜市西区高島3一3 一3", "神奈川県横浜市西区高島3-3-3"),
    ("０３︱１２３４", "03-1234"),
    ("丁220-0011", "〒220-0011"), ("T10O一0005", "〒100-0005"), ("丁812 Ooll", "〒812-0011"), ("7460-0008", "〒460-0008"),
    ("ドAx 075-753-0332", "FAX 075-753-0332"), ("''EL 092-620-5712", "TEL 092-620-5712"),
    ("株ンサンプル", "（株）サンプル"), ("一株ヘナミキ物産", "（株）ナミキ物産"),
    # 不該動的
    ("株式会社サンプル", "株式会社サンプル"), ("有限会社見本", "有限会社見本"), ("丁目", "丁目"), ("山田 太郎", "山田 太郎"),
])
def test_clean_text(raw, want):
    assert V.clean_text(raw) == want


# --------------------------------------------------------------------------- 整條流程（假 OCR）

def _bands(img: Image.Image) -> list[tuple[int, int]]:
    """疊起來的那張圖：每一條（一欄排成的一行）的上下。"""
    a = np.asarray(img.convert("L")) < 128
    rows = a.any(axis=1)
    out, start = [], None
    for y, v in enumerate(rows):
        if v and start is None:
            start = y
        elif not v and start is not None:
            out.append((start, y))
            start = None
    if start is not None:
        out.append((start, len(rows)))
    return out


#: 假 OCR 讀疊起來那張圖的結果（由右到左那幾欄；帶著 OCR 常見的讀法）
STRIP_READS = ["株式会社サンプル", "営業部", "部長", "山田 太郎", "丁100一0005", "東京都千代田区丸の内1一2一3",
               "電話 0 3一1234一5678", "上Ax 03-1234-5679"]


def _fake_engine(card_size, canvas_calls, horizontal_words=None, strip_reads=None, strip_conf=80):
    def fake(png, langs, **kw):
        img = Image.open(io.BytesIO(png))
        if img.size == card_size:                                   # 整張卡：直排被切成的碎片＋底下的 Email
            if horizontal_words is not None:
                return horizontal_words, "fake"
            ws = [{"text": "目", "left": 553, "top": 75, "width": 49, "height": 313, "conf": 3},
                  {"text": "豊", "left": 492, "top": 134, "width": 28, "height": 56, "conf": 9},
                  {"text": "9", "left": 125, "top": 309, "width": 20, "height": 44, "conf": 32}]
            ws.append({"text": "E-mail: " + EMAIL, "left": 40, "top": 960, "width": 330, "height": 26, "conf": 90})
            return ws, "fake"
        if img.width > img.height and img.size == card_size[::-1]:  # 轉 90° 試讀：讀不出東西
            return [], "fake"
        canvas_calls.append(img.size)
        bands = _bands(img)
        return [{"text": t, "left": 30, "top": a, "width": 300, "height": b - a, "conf": strip_conf}
                for (a, b), t in zip(bands, strip_reads or STRIP_READS)], "fake"
    return fake


def test_vertical_card_is_read_through_the_strips(monkeypatch):
    from app.core import ocr_engine
    im, _xs = vertical_card()
    calls = []
    monkeypatch.setattr(ocr_engine, "recognize_image", _fake_engine(im.size, calls))
    res = card_ocr.ocr_card(im, langs="jpn+eng")
    texts = [ln.text for ln in res["lines"]]
    assert calls, "直書要多讀一次（各欄疊成一張圖）"
    assert texts[:8] == ["株式会社サンプル", "営業部", "部長", "山田 太郎", "〒100-0005", "東京都千代田区丸の内1-2-3",
                         "電話 03-1234-5678", "FAX 03-1234-5679"], texts
    assert "目" not in texts and "豊" not in texts, "直排被切成的碎片要換掉"
    assert any("example.jp" in t for t in texts), "橫排的 Email 留著"
    assert all(ln.vertical for ln in res["lines"][:8])
    fs = E.extract_fields([dict(ln.to_dict(), index=i) for i, ln in enumerate(res["lines"])], lang="ja")
    got = {}
    for f in fs:
        got.setdefault(f["key"], []).append(f["value"])
    assert got["company"] == ["株式会社サンプル"] and got["name"] == ["山田 太郎"]
    assert got["department"] == ["営業部"] and got["title"] == ["部長"]
    assert got["phone"] == ["03-1234-5678"] and got["fax"] == ["03-1234-5679"]
    assert got["address"] == ["東京都千代田区丸の内1-2-3"]
    assert [f for f in fs if f["key"] == "address"][0]["zip"] == "100-0005"


def test_horizontal_card_keeps_its_own_reading(monkeypatch):
    """橫式名片上幾行字疊起來被影像那一層誤判成直欄（實測：公司、部門、職稱、姓名全被換成亂碼）：
    原本讀得好的橫排字（一個字框就有三個以上的字）碰到的欄不算，整張照原本的讀法。"""
    from app.core import ocr_engine
    im = S.card([("有限会社サンプル", 44), ("営業部", 26), ("部長", 26), ("小林 太郎", 80)])
    cols = V.find_columns(im)
    assert cols
    text_rows = ["有限会社サンプル", "営業部", "部長", "小林 太郎"]
    words = _words_by_row(im, text_rows)
    calls = []
    monkeypatch.setattr(ocr_engine, "recognize_image", _fake_engine(im.size, calls, horizontal_words=words))
    res = card_ocr.ocr_card(im, langs="jpn+eng")
    assert [ln.text for ln in res["lines"]] == text_rows
    assert not any(ln.vertical for ln in res["lines"])


def _words_by_row(im, texts, conf=95):
    """照影像上實際的位置，每一行給一個字框（模擬原本讀得好的橫排字）。"""
    m = V.ink_mask(im)
    rows = np.nonzero(m.any(axis=1))[0]
    bands, start, prev = [], None, None
    for y in rows:
        if start is None:
            start = prev = y
        elif y - prev > 3:
            bands.append((start, prev + 1))
            start = y
        prev = y
    bands.append((start, prev + 1))
    out = []
    for (a, b), t in zip(bands, texts):
        xs = np.nonzero(m[a:b].any(axis=0))[0]
        out.append({"text": t, "left": int(xs[0]), "top": int(a), "width": int(xs[-1] - xs[0] + 1),
                    "height": int(b - a), "conf": conf})
    return out


def test_short_horizontal_lines_keep_their_reading_when_the_strips_read_worse(monkeypatch):
    """只有兩個字的短行（「営業」「部長」）擋不到「一個字框三個字以上」那道：直書的讀法要明顯比原本好才換。"""
    from app.core import ocr_engine
    rows = ["営業", "部長", "山田", "太郎"]
    im = S.card([(t, 44 if i < 2 else 80) for i, t in enumerate(rows)])
    assert V.find_columns(im)
    calls = []
    monkeypatch.setattr(ocr_engine, "recognize_image",
                        _fake_engine(im.size, calls, horizontal_words=_words_by_row(im, rows),
                                     strip_reads=["目豊", "雲景", "幕華", "昌溝"], strip_conf=12))
    res = card_ocr.ocr_card(im, langs="jpn+eng")
    assert [ln.text for ln in res["lines"]] == rows


def test_english_only_does_not_run_the_vertical_pass(monkeypatch):
    """直書處理只在會有直書名片的語言跑（日文、繁中）；只有英文時不跑。"""
    from app.core import ocr_engine
    im, _xs = vertical_card()
    calls = []
    monkeypatch.setattr(ocr_engine, "recognize_image", _fake_engine(im.size, calls))
    card_ocr.ocr_card(im, langs="eng")
    assert calls == []


def test_taiwan_vertical_card_in_chinese_mode(monkeypatch):
    """台灣的直式名片（2026-11-22 起繁中模式也跑直書處理）：直欄換成排好的讀法、擷取出欄位。"""
    from app.core import ocr_engine
    im, _xs = vertical_card()
    calls = []
    reads = ["範例科技股份有限公司", "業務部", "經理", "王 小明", "丁100一0005", "臺北市中正區範例路一段1號5樓",
             "電話 ~02_2345一6789", "傳真 02-2345-6790"]
    monkeypatch.setattr(ocr_engine, "recognize_image", _fake_engine(im.size, calls, strip_reads=reads))
    res = card_ocr.ocr_card(im, langs="chi_tra+eng")
    assert calls, "繁中模式也要跑直書處理"
    fs = E.extract_fields([dict(ln.to_dict(), index=i) for i, ln in enumerate(res["lines"])], lang="zh")
    got = {}
    for f in fs:
        got.setdefault(f["key"], []).append(f["value"])
    assert got["company"] == ["範例科技股份有限公司"] and got["title"] == ["經理"]
    assert got["phone"] == ["02-2345-6789"] and got["fax"] == ["02-2345-6790"], got
    assert "臺北市中正區範例路一段1號5樓" in got["address"][0]


def test_thin_texture_strips_are_not_columns():
    """底紋、照片裡的細線也是細長的一條：比卡片短邊 1.5% 還細的不算一欄（台灣真實樣本實測誤觸兩張）。"""
    im = Image.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(im)
    for x in range(100, 1300, 60):                       # 一排 9 px 寬、200 px 高的細條
        d.rectangle([x, 300, x + 9, 500], fill="black")
    assert V.find_columns(im) == []


def test_a_well_read_horizontal_line_is_never_replaced(monkeypatch):
    """跟直欄重疊的原本的行會被換掉（那些是直排被切成的碎片）—— 但讀得好的橫排行（信心 60 以上、三個字以上）
    一律留著。台灣真實樣本實測：一張英文名片的「Dr. …」「Grafen」被底紋誤判的細條蓋掉。"""
    from app.core import ocr_engine
    im, _xs = vertical_card()
    good = {"text": "Grafen", "left": 545, "top": 200, "width": 70, "height": 22, "conf": 90}
    base = _fake_engine(im.size, [])

    def fake(png, langs, **kw):
        words, used = base(png, langs, **kw)
        if Image.open(io.BytesIO(png)).size == im.size:
            words = words + [good]
        return words, used
    monkeypatch.setattr(ocr_engine, "recognize_image", fake)
    texts = [ln.text for ln in card_ocr.ocr_card(im, langs="jpn+eng")["lines"]]
    assert "Grafen" in texts, texts
    assert "株式会社サンプル" in texts, "直欄照樣換上排好的讀法"


def test_texture_read_as_text_is_not_taken():
    """底紋、照片被讀成一串「圖」（台灣真實樣本每一次誤觸都有）：不採用；真的文字照常。"""
    for t in ("回圖圖", "主者主圖圖圖", "圖圖啞", "一嘿圖圖一"):
        assert card_ocr._looks_like_texture(t), t
    for t in ("株式会社サンプル", "設計圖書館", "電話 03-1234-5678", "ABC"):
        assert not card_ocr._looks_like_texture(t), t


def test_strips_touching_the_card_edge_are_not_columns():
    """貼著卡片邊緣（2% 以內）的細長區塊是邊框、照片或裁切帶到的背景；真的字有留白。"""
    im = Image.new("RGB", (655, 1400), "white")
    d = ImageDraw.Draw(im)
    f = _font(30)
    for i, ch in enumerate("株式会社"):                    # 貼著右邊緣的一欄
        d.text((633, 300 + 34 * i), ch, font=f, fill="black")
    for i, ch in enumerate("株式会社"):                    # 離邊緣有留白的一欄
        d.text((400, 300 + 34 * i), ch, font=f, fill="black")
    xs = [c[0] for c in V.find_columns(im)]
    assert len(xs) == 1 and 380 <= xs[0] <= 420, xs

"""英文文件的去識別化（第 2 批，v1.15.32）。

**現況為什麼不能算「支援英文」**（實測一份英文員工資料表）：
Email / 信用卡 / IP / URL 這些「格式」類抓得到，但**姓名、地址、電話、
社會安全號碼這四樣是去識別化的核心，英文全漏**；更糟的是台灣專屬的式子
在英文文件上**抓錯**：市話式子把護照號 `488912345`、IBAN 的 `6016 1331`、
信用卡的 `4111 1111` 都當成電話，航班號抓到郵遞區號 `NW1` 與 IBAN 開頭 `GB29`。

> **誤判比漏抓更危險** —— 畫面會顯示「已處理」，使用者以為遮乾淨了。

所以這一批做兩件事：加英美常見個資的式子（**有檢查碼的一律驗**），
並且**在英文模式下把台灣專屬的那幾條關掉**。
"""
from __future__ import annotations

import pytest

from app.tools.doc_deident import patterns as P


def _hits(text: str, lang: str) -> dict[str, list[str]]:
    """用該語言的樣式集掃一段文字，回 {樣式 id: [命中的值]}。"""
    out: dict[str, list[str]] = {}
    for pat in P.catalog_for(lang):
        for m in pat.regex.finditer(text):
            val = m.group(pat.value_group) if pat.value_group else m.group(0)
            try:
                ok = pat.validate(val)
            except Exception:  # noqa: BLE001
                ok = True
            if ok:
                out.setdefault(pat.id, []).append(val.strip())
    return out


# --------------------------------------------------------------- 檢查碼

@pytest.mark.parametrize("iban,valid", [
    ("GB82 WEST 1234 5698 7654 32", True),      # 英國官方範例
    ("DE89 3704 0044 0532 0130 00", True),      # 德國官方範例
    ("FR14 2004 1010 0505 0001 3M02 606", True),
    ("GB82 WEST 1234 5698 7654 33", False),     # 改一位 → 檢查碼不符
    ("GB29 NWBK 6016 1331 9268 19", True),
    ("AB12 3456 7890 1234", False),             # 隨便編的
])
def test_iban_checksum(iban, valid):
    """**一定要驗 mod-97** —— 不驗的話任何「兩字母+兩數字+一串英數」都會中。"""
    assert P._iban_valid(iban) is valid, iban


@pytest.mark.parametrize("ssn,found", [
    ("SSN 123-45-6789", True),
    ("000-45-6789", False),     # 區碼 000 不發
    ("666-45-6789", False),     # 666 不發
    ("900-45-6789", False),     # 9xx 不發
    ("123-00-6789", False),     # 群碼 00 不發
    ("123-45-0000", False),     # 序號 0000 不發
])
def test_us_ssn_excludes_numbers_that_are_never_issued(ssn, found):
    got = bool(P.RE_US_SSN.search(ssn))
    assert got is found, f"{ssn} → {got}"


@pytest.mark.parametrize("phone,valid", [
    ("+1 (415) 555-0182", True),
    ("415-555-0199", True),
    ("(212) 736-5000", True),
    ("115-555-0199", False),    # 區碼首位 1
    ("415-055-0199", False),    # 交換碼首位 0
])
def test_nanp_phone(phone, valid):
    m = P.RE_NANP_PHONE.search(phone)
    assert bool(m and P._nanp_valid(m.group(0))) is valid, phone


@pytest.mark.parametrize("ni,valid", [
    # 第一、二碼都不可以是 D F I Q U V（第二碼另外不可以是 O）——
    # 我第一版用 `QQ` 當有效範例，那其實是**規格上不會發出的號碼**。
    ("AB 12 34 56 C", True),
    ("JG 12 34 56 D", True),
    ("QQ 12 34 56 C", False),
    ("BG 12 34 56 C", False),   # BG 是保留前綴
    ("ZZ 12 34 56 A", False),
])
def test_uk_ni_number(ni, valid):
    m = P.RE_UK_NI.search(ni)
    assert bool(m and P._uk_ni_valid(m.group(0))) is valid, ni


# --------------------------------------------------------------- 抓得到

@pytest.mark.parametrize("text,pid", [
    ("Date of Birth: January 5, 1985", "dob_en"),
    ("DOB: 5 January 2026", "dob_en"),
    ("Born: Jan 5 1985", "dob_en"),
    ("1842 Maple Street, Apt 4B, Springfield, IL 62704", "us_addr"),
    ("221B Baker Street", "us_addr"),
    ("London NW1 6XE", "uk_postcode"),
    ("Mobile: +1 (415) 555-0182", "nanp_phone"),
    ("IBAN: GB82 WEST 1234 5698 7654 32", "iban"),
    ("SSN: 123-45-6789", "us_ssn"),
])
def test_english_pii_is_detected(text, pid):
    hits = _hits(text, "en")
    assert pid in hits, f"{text!r} 沒抓到 {pid}（抓到 {list(hits)}）"


# ------------------------------------------------- 語系隔離（這批的重點）

_EN_DOC = """CONFIDENTIAL - Employee Record
Name: Michael Thompson
Date of Birth: January 5, 1985
Social Security Number: 123-45-6789
Passport No.: 488912345
Home Address: 1842 Maple Street, Apt 4B, Springfield, IL 62704, USA
UK Office: 221B Baker Street, London NW1 6XE
Mobile: +1 (415) 555-0182
Email: michael.thompson@acme-corp.com
IBAN: GB29 NWBK 6016 1331 9268 19
Credit Card: 4111 1111 1111 1111
"""


def test_taiwan_only_patterns_are_off_for_english_documents():
    """台灣市話 / 車牌 / 統編式子在英文文件上**抓錯**，不是抓不到。"""
    hits = _hits(_EN_DOC, "en")
    for pid in ("landline", "mobile", "plate", "tw_biz", "tw_id", "addr"):
        assert pid not in hits, (
            f"英文文件上還在跑台灣專屬的 {pid}，抓到 {hits.get(pid)}")


def test_english_patterns_are_off_for_chinese_documents():
    zh = "地址：新北市汐止區新台五路一段 88 號　電話：02-2696-1234"
    hits = _hits(zh, "zh-Hant")
    for pid in ("us_ssn", "nanp_phone", "us_addr", "uk_postcode", "iban"):
        assert pid not in hits, f"中文文件上跑了英文樣式 {pid}"


def test_the_format_patterns_work_in_both_languages():
    """Email / 信用卡 / IP 這些與語言無關，兩邊都要抓得到。"""
    for lang in ("en", "zh-Hant"):
        hits = _hits("mail: a.b@example.com 卡號 4111 1111 1111 1111 ip 192.0.2.5",
                     lang)
        assert "email" in hits and "cc" in hits, f"{lang}: {list(hits)}"


def test_the_known_false_positives_are_gone():
    """稽核與實測抓到的那幾個誤判。"""
    hits = _hits(_EN_DOC, "en")
    flat = [v for vals in hits.values() for v in vals]
    # 護照號、IBAN 片段、信用卡片段不可以被當成電話
    for bad in ("488912345", "6016 1331", "4111 1111"):
        assert bad not in hits.get("landline", []), f"{bad} 又被當成市話"
        assert bad not in hits.get("nanp_phone", []), f"{bad} 又被當成電話"
    # 郵遞區號與 IBAN 開頭不可以被當成航班號
    # 航班號的式子已收緊成「至少三位數字」 —— 郵遞區號 `NW1` 與 IBAN 開頭
    # `GB29` 都不再中（代價是 `JL5` 這種一位數航班抓不到，見式子的說明）。
    assert "NW1" not in hits.get("flight", [])
    assert "GB29" not in hits.get("flight", [])


def test_a_document_full_of_lookalike_numbers_produces_no_sensitive_hits():
    """**誤判語料**：只驗「抓得到」的話，把式子放寬到抓一切也會過。"""
    noise = """PURCHASE ORDER
Order No.: 555-0142-7788      Part No.: 123-45-6789-A
Invoice: INV-2026-000123      ISBN 978-0-306-40615-7
Version 12.4.1 build 20260913 Quantity: 1,234,567
Reference: AB12 3456 7890 1234 (internal ledger code)
Tracking: 1Z999AA10123456784
"""
    hits = _hits(noise, "en")
    for pid in ("us_ssn", "us_ssn_label", "iban", "uk_ni", "us_addr"):
        assert pid not in hits, (
            f"誤判：{pid} 在一份沒有個資的採購單上命中 {hits.get(pid)}")


# --------------------------------------------------------------- 結構

def test_every_pattern_declares_its_locale_intent():
    """新加式子要想清楚它是哪一種 —— 忘了標註會讓它在兩邊都跑。"""
    for pat in P.CATALOG:
        assert pat.locales is None or isinstance(pat.locales, tuple), pat.id
        if pat.locales:
            assert set(pat.locales) <= {"zh-Hant", "en"}, (pat.id, pat.locales)


def test_the_doc_language_list_has_no_duplicates():
    codes = [c for c, _ in P.DOC_LANGS]
    assert len(codes) == len(set(codes))
    assert "zh-Hant" in codes and "en" in codes


def test_default_ids_follow_the_document_language():
    en, zh = P.default_ids_for("en"), P.default_ids_for("zh-Hant")
    assert "us_ssn" in en and "us_ssn" not in zh
    assert "tw_id" in zh and "tw_id" not in en
    assert "email" in en and "email" in zh      # 與語言無關

"""英文文件去識別化的端到端（v1.15.32）。

**判準是「打開產出看內容」**（TEST_PLAN §0.5）：端點回 200、偵測到幾筆、
畫面顯示成功，都不算驗收。這支測試做一份有真個資的英文 PDF、跑完整流程、
**再從產出抽文字，確認那幾段撈不回來**。

issue #51 的教訓就是這個：地址式子的泛用分支從上線起沒有成立過，
單元層一眼可見卻活了很多版 —— 因為沒有任何測試是「拿一份真的有地址的檔案
跑一次，看它最後有沒有被遮掉」。
"""
from __future__ import annotations

import fitz
import pytest

from app.tools.doc_deident import patterns as P
from app.tools.doc_deident import redact_core
from app.tools.doc_deident.fake_values import Replacer

_SECRETS = {
    "us_ssn": "123-45-6789",
    "nanp_phone": "415-555-0182",
    "email": "michael.thompson@acme-corp.com",
    "iban": "GB82 WEST 1234 5698 7654 32",
}

_DOC_LINES = [
    "CONFIDENTIAL - Employee Record",
    "Name: Michael Thompson",
    "Date of Birth: January 5, 1985",
    "Social Security Number: 123-45-6789",
    "Home Address: 1842 Maple Street, Springfield, IL 62704",
    "Mobile: 415-555-0182",
    "Email: michael.thompson@acme-corp.com",
    "IBAN: GB82 WEST 1234 5698 7654 32",
    "Credit Card: 4111 1111 1111 1111",
]


def _english_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    y = 80
    for line in _DOC_LINES:
        page.insert_text((56, y), line, fontsize=11)
        y += 26
    out = tmp_path / "employee.pdf"
    doc.save(str(out))
    doc.close()
    return out


def _findings(page, lang: str) -> list[tuple[str, str, fitz.Rect]]:
    """用該語言的樣式集找出命中，並回傳它們在頁面上的位置。"""
    text = page.get_text()
    hits = []
    for pat in P.catalog_for(lang):
        if not pat.default_on:
            continue
        for m in pat.regex.finditer(text):
            val = (m.group(pat.value_group) if pat.value_group else m.group(0)).strip()
            if not val:
                continue
            try:
                if not pat.validate(val):
                    continue
            except Exception:  # noqa: BLE001
                pass
            for rect in page.search_for(val):
                hits.append((pat.id, val, rect))
    return hits


def test_english_pii_is_gone_from_the_output(tmp_path):
    src = _english_pdf(tmp_path)
    doc = fitz.open(str(src))
    page = doc[0]
    hits = _findings(page, "en")
    found_ids = {pid for pid, _v, _r in hits}
    for pid in _SECRETS:
        assert pid in found_ids, f"偵測階段就沒抓到 {pid}（抓到 {sorted(found_ids)}）"

    redact_core.apply_page_redactions(page, [r for _p, _v, r in hits],
                                      fill=(0, 0, 0))
    out = tmp_path / "redacted.pdf"
    doc.save(str(out), garbage=3, deflate=True)
    doc.close()

    # **打開產出看內容** —— 這才是驗收
    got = fitz.open(str(out))
    text = got[0].get_text()
    got.close()
    leaked = [v for v in _SECRETS.values() if v.replace(" ", "") in text.replace(" ", "")]
    assert not leaked, f"產出裡還撈得到：{leaked}"


def test_the_untouched_parts_are_still_readable(tmp_path):
    """只遮該遮的 —— 文件其他內容要留著，不然產出沒有用。"""
    src = _english_pdf(tmp_path)
    doc = fitz.open(str(src))
    page = doc[0]
    hits = _findings(page, "en")
    redact_core.apply_page_redactions(page, [r for _p, _v, r in hits], fill=(0, 0, 0))
    out = tmp_path / "redacted.pdf"
    doc.save(str(out)); doc.close()
    got = fitz.open(str(out)); text = got[0].get_text(); got.close()
    assert "CONFIDENTIAL" in text, "標題被一起遮掉了"
    assert "Employee Record" in text


def test_replace_mode_does_not_put_chinese_into_an_english_document(tmp_path):
    """替換模式的用途是「文件還能當正常文件用」——
    英文合約換出「王大明」就達不到那個目的，而且一眼看得出被動過。"""
    r = Replacer(valid_checksum=False)
    cjk = lambda s: any("㐀" <= c <= "鿿" for c in s)  # noqa: E731
    for pid, val in [("person_name", "Michael Thompson"),
                     ("company", "Acme Manufacturing Corporation"),
                     ("us_addr", "1842 Maple Street, Springfield, IL 62704"),
                     ("us_ssn", "123-45-6789"),
                     ("nanp_phone", "415-555-0182")]:
        fake = r.for_value(pid, val)
        assert fake and not cjk(fake), f"{pid} 的假值有中文：{fake!r}"


def test_the_fake_ssn_and_iban_can_never_collide_with_a_real_person(tmp_path):
    """假值要**刻意不合法** —— 驗得過的假號碼可能真的屬於某個人。"""
    r = Replacer(valid_checksum=False)
    ssn = r.for_value("us_ssn", "123-45-6789")
    assert ssn.startswith("9"), f"假 SSN 沒有用不指派的 9xx 號段：{ssn}"
    assert not P._iban_valid(r.for_value("iban", "GB82 WEST 1234 5698 7654 32"))
    phone = r.for_value("nanp_phone", "415-555-0182")
    assert "555-01" in phone, f"假電話沒有用保留號段：{phone}"


def test_a_chinese_document_still_gets_chinese_fake_values():
    """反向：中文文件不可以換出英文假值。"""
    r = Replacer(valid_checksum=False)
    assert any("㐀" <= c <= "鿿"
               for c in r.for_value("person_name", "王小明"))

"""PDF 密碼解除：對外 API 的高鐵模式（v1.17.37）。

網頁版本來就有「高鐵模式」（台灣高鐵電子車票 PDF 的開啟密碼＝出發日期，在日期範圍內每天試），
對外 API（MCP 也走這支）原本只收手動密碼 —— 透過 AI 用不到。這裡驗：

* API 收 `thsr_mode` / `thsr_date_from` / `thsr_date_to` / `thsr_date_format` / `use_filename_as_password`，
  解開的檔名是那個日期（日期格式裡的 / 換成 _），跟網頁一致。
* 日期範圍裡沒有那一天 → 400 並說試了幾天；範圍超過上限、日期或格式不對 → 400。
* 原本只帶 `password` 的呼叫方式不變。
* 網頁的 `/submit` 改用同一支 helper 之後，高鐵模式照常。
"""
from __future__ import annotations

import time

import fitz
import pytest

from app.tools.pdf_decrypt.router import THSR_MAX_DAYS, thsr_candidates

API = "/tools/pdf-decrypt/api/pdf-decrypt"


def _enc(pw: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "ticket")
    out = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=pw, owner_pw=pw + "-owner")
    doc.close()
    return out


def _post(client, data: bytes, name="ticket.pdf", **form):
    return client.post(API, files={"file": (name, data, "application/pdf")}, data=form)


def _filename(r) -> str:
    cd = r.headers["content-disposition"]
    return cd.split("filename=", 1)[1].strip('"') if "filename*=" not in cd else cd.split("''", 1)[1]


def _opens_without_password(data: bytes) -> bool:
    with fitz.open(stream=data, filetype="pdf") as d:
        return not d.needs_pass and d.page_count == 1


def test_thsr_mode_finds_the_travel_date(client, auth_off):
    r = _post(client, _enc("20261108"), thsr_mode="true", thsr_date_from="2026-10-01", thsr_date_to="2026-11-30")
    assert r.status_code == 200, r.text
    assert _filename(r) == "20261108.pdf"
    assert _opens_without_password(r.content)


def test_thsr_mode_reversed_range_and_custom_format(client, auth_off):
    r = _post(client, _enc("2026/11/08"), thsr_mode="1", thsr_date_from="2026-11-30", thsr_date_to="2026-11-01",
              thsr_date_format="YYYY/MM/DD")
    assert r.status_code == 200, r.text
    assert _filename(r) == "2026_11_08.pdf"


def test_thsr_mode_date_outside_range_says_how_many_days(client, auth_off):
    r = _post(client, _enc("20261108"), thsr_mode="true", thsr_date_from="2026-01-01", thsr_date_to="2026-01-31")
    assert r.status_code == 400
    assert "31" in r.json()["detail"]


@pytest.mark.parametrize("form,msg", [
    ({"thsr_date_from": "2026-01-01", "thsr_date_to": ""}, "起迄"),
    ({"thsr_date_from": "2026/01/01", "thsr_date_to": "2026-01-31"}, "日期格式"),
    ({"thsr_date_from": "2026-01-01", "thsr_date_to": "2026-01-31", "thsr_date_format": "%Y%m%d"}, "Y M D"),
    ({"thsr_date_from": "0001-01-01", "thsr_date_to": "9999-12-31"}, str(THSR_MAX_DAYS)),
])
def test_thsr_mode_rejects_bad_ranges(client, auth_off, form, msg):
    r = _post(client, _enc("20261108"), thsr_mode="true", **form)
    assert r.status_code == 400 and msg in r.json()["detail"], r.text


def test_range_limit_is_inclusive_and_covers_the_page_timeline():
    from datetime import date, timedelta
    start = date(2025, 1, 1)
    end = start + timedelta(days=THSR_MAX_DAYS - 1)
    assert len(thsr_candidates(start.isoformat(), end.isoformat(), "YYYYMMDD")) == THSR_MAX_DAYS
    assert THSR_MAX_DAYS >= 14 * 31                     # 網頁時間軸最多 14 個月，不可以被這個上限擋到


def test_filename_as_password_and_plain_password_still_work(client, auth_off):
    r = _post(client, _enc("abc123"), name="abc123.pdf", use_filename_as_password="true")
    assert r.status_code == 200 and _filename(r) == "abc123_decrypted.pdf"
    r = _post(client, _enc("open-pw"), password="open-pw")
    assert r.status_code == 200 and _filename(r) == "ticket_decrypted.pdf" and _opens_without_password(r.content)
    assert _post(client, _enc("open-pw"), password="wrong").status_code == 400
    # 沒有開啟密碼的 PDF：什麼都不給也能處理（只去掉權限限制）
    doc = fitz.open(); doc.new_page()
    plain = doc.tobytes(); doc.close()
    assert _post(client, plain).status_code == 200


def test_web_submit_thsr_mode_unchanged(client, auth_off):
    from app.core.job_manager import job_manager
    r = client.post("/tools/pdf-decrypt/submit",
                    files=[("file", ("a.pdf", _enc("20261108"), "application/pdf")),
                           ("file", ("b.pdf", _enc("20261109"), "application/pdf"))],
                    data={"thsr_mode": "1", "thsr_date_from": "2026-11-01", "thsr_date_to": "2026-11-30"})
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    for _ in range(200):
        j = job_manager.get(jid)
        if j.status in ("done", "error"):
            break
        time.sleep(0.05)
    assert j.status == "done", j.message
    import zipfile
    with zipfile.ZipFile(j.result_path) as zf:
        assert sorted(zf.namelist()) == ["20261108.pdf", "20261109.pdf"]
    r = client.post("/tools/pdf-decrypt/submit", files=[("file", ("a.pdf", _enc("x"), "application/pdf"))],
                    data={"thsr_mode": "1", "thsr_date_from": "0001-01-01", "thsr_date_to": "9999-12-31"})
    assert r.status_code == 400

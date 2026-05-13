"""Tests for vat_db (M4.a)."""
from __future__ import annotations

import io
import pytest

from app.core import vat_db


@pytest.fixture
def vat_tmp(tmp_path, monkeypatch):
    """Redirect data_dir to tmp_path; clear in-memory cache."""
    monkeypatch.setattr("app.core.vat_db.app_settings",
                        type("S", (), {"data_dir": tmp_path})())
    vat_db._lookup_cache.clear()
    return tmp_path


# ─── CSV parsing ─────────────────────────────────────────────────────

def test_parse_basic_csv():
    csv_text = (
        "統一編號,營業人名稱,營業地址,負責人姓名\n"
        "12345678,測試企業有限公司,台北市信義區,王小明\n"
        "87654321,範例商行,新北市板橋區,陳大同\n"
    )
    records = list(vat_db.parse_csv_to_records(csv_text.encode("utf-8")))
    assert len(records) == 2
    assert records[0]["vat"] == "12345678"
    assert records[0]["name"] == "測試企業有限公司"
    assert records[0]["address"] == "台北市信義區"
    assert records[0]["owner"] == "王小明"


def test_parse_skips_invalid_vat():
    csv_text = (
        "統一編號,營業人名稱\n"
        "abcd1234,XX 公司\n"        # 不是 8 位數字
        "12345,YY 商行\n"            # 太短
        "12345678,正常公司\n"
        ",空白統編公司\n"
        "99999999,\n"               # 空名稱 skip
    )
    records = list(vat_db.parse_csv_to_records(csv_text.encode("utf-8")))
    assert len(records) == 1
    assert records[0]["vat"] == "12345678"


def test_parse_utf8_bom():
    csv_text = "﻿統一編號,營業人名稱\n12345678,測試\n".encode("utf-8")
    records = list(vat_db.parse_csv_to_records(csv_text))
    assert records[0]["vat"] == "12345678"


def test_parse_big5_fallback():
    csv_text_big5 = "統一編號,營業人名稱\n12345678,測試\n".encode("big5")
    records = list(vat_db.parse_csv_to_records(csv_text_big5))
    assert records[0]["name"] == "測試"


def test_parse_english_headers():
    csv_text = (
        "Business_Accounting_NO,Business_Name,Business_Address\n"
        "12345678,Test Co Ltd,Taipei\n"
    )
    records = list(vat_db.parse_csv_to_records(csv_text.encode("utf-8")))
    assert records[0]["vat"] == "12345678"
    assert records[0]["name"] == "Test Co Ltd"


def test_parse_missing_required_columns():
    csv_text = "foo,bar\n1,2\n"
    with pytest.raises(ValueError, match="找不到"):
        list(vat_db.parse_csv_to_records(csv_text.encode("utf-8")))


# ─── Ingest + lookup ─────────────────────────────────────────────────

def test_ingest_and_lookup(vat_tmp):
    csv_text = (
        "統一編號,營業人名稱,營業地址\n"
        "12345678,測試公司,台北\n"
        "87654321,範例商行,新北\n"
    )
    result = vat_db.ingest_csv(csv_text.encode("utf-8"), source="test")
    assert result["records"] == 2
    assert result["source"] == "test"

    r = vat_db.lookup_vat("12345678")
    assert r is not None
    assert r["name"] == "測試公司"
    assert r["address"] == "台北"

    r2 = vat_db.lookup_vat("87654321")
    assert r2["name"] == "範例商行"

    # 不存在 → None
    assert vat_db.lookup_vat("99999999") is None


def test_lookup_invalid_vat_format(vat_tmp):
    assert vat_db.lookup_vat("") is None
    assert vat_db.lookup_vat("abc") is None
    assert vat_db.lookup_vat("123") is None
    assert vat_db.lookup_vat(None) is None


def test_ingest_replaces_old_data(vat_tmp):
    csv1 = "統一編號,營業人名稱\n12345678,舊名稱\n"
    csv2 = "統一編號,營業人名稱\n12345678,新名稱\n55555555,新增公司\n"
    vat_db.ingest_csv(csv1.encode("utf-8"), source="v1")
    assert vat_db.lookup_vat("12345678")["name"] == "舊名稱"

    vat_db.ingest_csv(csv2.encode("utf-8"), source="v2")
    assert vat_db.lookup_vat("12345678")["name"] == "新名稱"
    assert vat_db.lookup_vat("55555555")["name"] == "新增公司"


def test_ingest_empty_raises(vat_tmp):
    csv_text = "統一編號,營業人名稱\n"
    with pytest.raises(ValueError, match="沒有任何有效資料"):
        vat_db.ingest_csv(csv_text.encode("utf-8"))


def test_get_meta(vat_tmp):
    csv_text = "統一編號,營業人名稱\n12345678,X 公司\n"
    vat_db.ingest_csv(csv_text.encode("utf-8"), source="testsrc")
    meta = vat_db.get_meta()
    assert meta["record_count"] == 1
    assert meta["source"] == "testsrc"
    assert meta["last_updated"]


def test_clear_db(vat_tmp):
    csv_text = "統一編號,營業人名稱\n12345678,X\n"
    vat_db.ingest_csv(csv_text.encode("utf-8"))
    assert vat_db.lookup_vat("12345678")
    vat_db.clear_db()
    assert vat_db.lookup_vat("12345678") is None
    assert vat_db.get_meta()["record_count"] == 0


# ─── ZIP + CSV auto-detect ───────────────────────────────────────────

def test_ingest_archive_zip(vat_tmp):
    import zipfile
    csv_text = "統一編號,營業人名稱\n12345678,壓縮內公司\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("BGMOPEN1.csv", csv_text)
    result = vat_db.ingest_archive_or_csv(buf.getvalue(), source="ziptest")
    assert result["records"] == 1
    assert vat_db.lookup_vat("12345678")["name"] == "壓縮內公司"


def test_ingest_archive_plain_csv(vat_tmp):
    csv_text = "統一編號,營業人名稱\n12345678,純 CSV 公司\n"
    result = vat_db.ingest_archive_or_csv(csv_text.encode("utf-8"), source="plain")
    assert result["records"] == 1
    assert vat_db.lookup_vat("12345678")["name"] == "純 CSV 公司"


# ─── HTTP: /api/vat-lookup endpoint ──────────────────────────────────

def test_api_vat_lookup_endpoint():
    """Public endpoint reachable without admin."""
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    # Bad input → 400
    r = client.get("/api/vat-lookup/abc")
    assert r.status_code == 400
    r = client.get("/api/vat-lookup/123")
    assert r.status_code == 400
    # Not found → 404
    r = client.get("/api/vat-lookup/00000000")
    assert r.status_code in (404, 200)  # 200 if real DB has 00000000


def test_ingest_zip_no_csv_inside(vat_tmp):
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("readme.txt", "no csv here")
    with pytest.raises(ValueError, match="找不到"):
        vat_db.ingest_archive_or_csv(buf.getvalue(), source="bad-zip")

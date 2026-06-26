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
        "12345678,測試企業有限公司,台北市○○區,王小明\n"
        "87654321,範例商行,新北市○○區,陳大同\n"
    )
    records = list(vat_db.parse_csv_to_records(csv_text.encode("utf-8")))
    assert len(records) == 2
    assert records[0]["vat"] == "12345678"
    assert records[0]["name"] == "測試企業有限公司"
    assert records[0]["address"] == "台北市○○區"
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


# ---- FTS5 名稱搜尋（逐字 unigram，1 字 / 子字串 / 多關鍵字 AND）----

def _seed_fts(vat_db_mod):
    csv_text = (
        "統一編號,營業人名稱,營業地址\n"
        "11111111,節省工具有限公司,台中市北屯區\n"
        "22222222,節省科技股份有限公司,台北市\n"
        "33333333,工具王企業社,高雄市\n"
        "44444444,大同電器行,台北市大同區\n"
    )
    return vat_db_mod.ingest_csv(csv_text.encode("utf-8"), source="test")


def test_fts_one_char_and_substring(vat_tmp):
    _seed_fts(vat_db)
    names = lambda q: sorted(r["name"] for r in vat_db.search_companies(q))
    assert "工具王企業社" in names("工")          # 1 個字
    assert "節省工具有限公司" in names("工")
    assert names("節省") == ["節省工具有限公司", "節省科技股份有限公司"]  # 子字串


def test_fts_multi_keyword_and(vat_tmp):
    _seed_fts(vat_db)
    r = [x["name"] for x in vat_db.search_companies("節省 工具")]
    assert r == ["節省工具有限公司"]   # 只有同時含「節省」+「工具」


def test_fts_cross_field_and(vat_tmp):
    _seed_fts(vat_db)
    # 地址含「台北」+ 名稱含「科技」→ 跨欄位 AND
    r = [x["name"] for x in vat_db.search_companies("台北 科技")]
    assert r == ["節省科技股份有限公司"]


def test_search_min_one_char(vat_tmp):
    _seed_fts(vat_db)
    assert vat_db.search_companies("") == []
    assert len(vat_db.search_companies("工")) >= 1


def test_like_fallback_when_fts_missing(vat_tmp):
    _seed_fts(vat_db)
    conn = vat_db._connect()
    conn.execute("DROP TABLE IF EXISTS vat_fts")
    conn.commit()
    conn.close()
    r = [x["name"] for x in vat_db.search_companies("節省")]  # 退回 LIKE
    assert "節省工具有限公司" in r


def test_category_stats_cached_after_ingest(vat_tmp):
    _seed_fts(vat_db)
    conn = vat_db._connect()
    row = conn.execute(
        "SELECT value FROM vat_meta WHERE key='category_stats_json'").fetchone()
    conn.close()
    assert row and row[0]   # ingest 後已快取


def test_rebuild_fts_from_existing(vat_tmp):
    # 模擬舊版 DB：有資料但 build_index=False（沒建 FTS）→ 之後 rebuild
    vat_db.ingest_csv(
        "統一編號,營業人名稱,營業地址\n55555555,測試企業社,台南市\n".encode(),
        source="t", build_index=False)
    conn = vat_db._connect()
    ready = vat_db._fts_ready(conn)
    conn.close()
    assert ready is False
    assert vat_db.rebuild_fts() == 1
    assert any(x["name"] == "測試企業社" for x in vat_db.search_companies("測試"))


# ---- 欄位開關 / 下鑽 / 統計（v1.12.18）----

def _seed_full(vat_db_mod):
    vat_db_mod.init_db()
    c = vat_db_mod._connect()
    rows = [
        ("11111111", "節省工具有限公司", "台北市大同區", "王小明", "有限公司", "超級市場", "企業"),
        ("22222222", "大同電器行", "台北市中山區", "張三", "獨資", "家電零售", "企業"),
        ("33333333", "台中科技股份有限公司", "台中市西屯區", "李四", "股份有限公司", "半導體", "企業"),
        ("44444444", "節省超商", "雲林縣斗六市", "陳五", "有限公司", "便利商店 / 超級市場", "企業"),
    ]
    for vat, name, addr, owner, org, ind, cat in rows:
        c.execute("INSERT OR REPLACE INTO vat_registry"
                  "(vat,name,address,owner,org_type,industries,category,status) "
                  "VALUES(?,?,?,?,?,?,?,'營業中')", (vat, name, addr, owner, org, ind, cat))
    c.commit(); vat_db_mod.rebuild_fts(c); c.commit(); c.close()


def test_search_fields_filter(vat_tmp):
    _seed_full(vat_db)
    n = lambda q, **k: sorted(x["name"] for x in vat_db.search_companies(q, **k))
    # 「大同」只搜名稱 → 大同電器行；只搜地址 → 節省工具(地址含大同區)
    assert n("大同", fields=["name"]) == ["大同電器行"]
    assert n("大同", fields=["address"]) == ["節省工具有限公司"]
    # 只搜負責人
    assert n("李四", fields=["owner"]) == ["台中科技股份有限公司"]
    # 全選(None)= 兩者都中
    assert set(n("大同")) == {"大同電器行", "節省工具有限公司"}


def test_search_drill_filters(vat_tmp):
    _seed_full(vat_db)
    n = lambda q, **k: sorted(x["name"] for x in vat_db.search_companies(q, **k))
    assert n("節省", org_type="有限公司") == ["節省工具有限公司", "節省超商"]
    assert n("電器", city="台北市") == ["大同電器行"]
    assert n("節省", industry="便利商店") == ["節省超商"]
    assert n("節省", industry="超級市場") == ["節省工具有限公司", "節省超商"]


def test_search_stats(vat_tmp):
    _seed_full(vat_db)
    s = vat_db.search_stats("節省")   # 節省工具 + 節省超商
    assert s["total"] == 2
    ind = {x["value"]: x["count"] for x in s["industry"]}
    assert ind.get("超級市場") == 2 and ind.get("便利商店") == 1   # 多值「/」切分
    city = {x["value"]: x["count"] for x in s["city"]}
    assert city.get("台北市") == 1 and city.get("雲林縣") == 1
    org = {x["value"]: x["count"] for x in s["org"]}
    assert org.get("有限公司") == 2

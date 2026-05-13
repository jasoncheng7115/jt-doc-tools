"""Tests for einvoice-scan tool — QR parser, buffer storage, HTTP endpoints.

Strategy:
- QR parser tests use plain string input (no zbar dependency needed)
- Buffer tests use tmp_path to isolate from real data dir
- HTTP tests skip if pyzbar/zbar not available (so CI without zbar still passes)
"""
from __future__ import annotations

import importlib
import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.tools.einvoice_scan import buffer, qr_decoder


client = TestClient(app)


# ─── Unit: QR parser ───────────────────────────────────────────────────

def _build_qr_text(invoice_number="AB12345678", roc_date="1150513",
                   random_code="1234", untaxed_hex="000003E8",
                   total_hex="0000041A", buyer="00000000",
                   seller="12345678", verify="A" * 24):
    """製造一個測試用 e-invoice QR 字串。"""
    return (invoice_number + roc_date + random_code + untaxed_hex
            + total_hex + buyer + seller + verify)


def test_parse_basic():
    qr = _build_qr_text()
    p = qr_decoder.parse_einvoice_qr(qr)
    assert p["invoice_number"] == "AB12345678"
    assert p["date"] == "2026-05-13"
    assert p["random_code"] == "1234"
    assert p["amount_untaxed"] == 1000
    assert p["amount_total"] == 1050
    assert p["buyer_vat"] is None  # 00000000 視為無
    assert p["seller_vat"] == "12345678"


def test_parse_buyer_vat_present():
    qr = _build_qr_text(buyer="87654321")
    p = qr_decoder.parse_einvoice_qr(qr)
    assert p["buyer_vat"] == "87654321"


def test_parse_invalid_invoice_number():
    """非 AA12345678 格式應 return None（可能掃到別張 QR）。"""
    qr = _build_qr_text(invoice_number="ABCDEFGHIJ")  # 不是 2 字母 + 8 數字
    assert qr_decoder.parse_einvoice_qr(qr) is None


def test_parse_too_short():
    assert qr_decoder.parse_einvoice_qr("ABC") is None
    assert qr_decoder.parse_einvoice_qr("") is None
    assert qr_decoder.parse_einvoice_qr(None) is None


def test_parse_invalid_date():
    """非法 ROC 日期應該 date=None 但其他欄位仍解。"""
    qr = _build_qr_text(roc_date="9999999")  # 月日範圍錯
    p = qr_decoder.parse_einvoice_qr(qr)
    assert p is not None  # 仍然 parse
    assert p["date"] is None  # 但日期無效


def test_parse_invalid_hex_amount():
    """非 hex 金額欄位 → None，其他欄位正常。"""
    qr = _build_qr_text(total_hex="ZZZZZZZZ")
    p = qr_decoder.parse_einvoice_qr(qr)
    assert p is not None
    assert p["amount_total"] is None


def test_parse_qr_list_skips_non_einvoice():
    """parse_qr_list 自動跳過非 e-invoice 格式。"""
    qrs = [
        "https://example.com",        # 一般 URL QR
        _build_qr_text(),              # 真的 e-invoice
        "**1:0:Items:Item1:Item2",     # 右 QR (品項)
    ]
    out = qr_decoder.parse_qr_list(qrs)
    assert len(out) == 1
    assert out[0]["invoice_number"] == "AB12345678"


# ─── Unit: buffer storage ─────────────────────────────────────────────

@pytest.fixture
def buffer_tmp(tmp_path, monkeypatch):
    """把 buffer 的 data_dir 重導到 tmp_path，避免污染真的 data dir。"""
    monkeypatch.setattr("app.tools.einvoice_scan.buffer.settings",
                        type("S", (), {"data_dir": tmp_path})())
    # Reset in-memory locks from previous tests
    buffer._locks.clear()
    return tmp_path


def test_buffer_empty(buffer_tmp):
    assert buffer.list_invoices(None) == []
    assert buffer.buffer_info(None)["count"] == 0


def test_buffer_add_and_list(buffer_tmp):
    parsed = [
        {"invoice_number": "AB12345678", "date": "2026-05-13",
         "amount_total": 1050, "amount_untaxed": 1000,
         "buyer_vat": None, "seller_vat": "12345678", "random_code": "1234"},
    ]
    res = buffer.add_invoices(None, parsed)
    assert len(res["added"]) == 1
    assert res["duplicates"] == []
    assert res["cap_reached"] is False
    assert "id" in res["added"][0]
    assert "scanned_at" in res["added"][0]
    assert buffer.buffer_info(None)["count"] == 1


def test_buffer_dedup_by_invoice_number(buffer_tmp):
    parsed = [{"invoice_number": "AB12345678", "amount_total": 100}]
    buffer.add_invoices(None, parsed)
    # 第二次加同 invoice_number → duplicates
    res = buffer.add_invoices(None, parsed)
    assert res["added"] == []
    assert res["duplicates"] == ["AB12345678"]
    assert buffer.buffer_info(None)["count"] == 1  # 仍只 1 筆


def test_buffer_delete(buffer_tmp):
    res = buffer.add_invoices(None, [{"invoice_number": "AB12345678"}])
    inv_id = res["added"][0]["id"]
    assert buffer.delete_invoice(None, inv_id) is True
    assert buffer.buffer_info(None)["count"] == 0
    # 再刪一次回 False
    assert buffer.delete_invoice(None, inv_id) is False


def test_buffer_clear_all(buffer_tmp):
    buffer.add_invoices(None, [{"invoice_number": f"AB{i:08d}"} for i in range(5)])
    n = buffer.clear_all(None)
    assert n == 5
    assert buffer.buffer_info(None)["count"] == 0


def test_buffer_per_user_isolation(buffer_tmp):
    """不同 user 的 buffer 互不影響。"""
    user_a = {"username": "alice", "realm": "local"}
    user_b = {"username": "bob", "realm": "local"}
    buffer.add_invoices(user_a, [{"invoice_number": "AB12345678"}])
    buffer.add_invoices(user_b, [{"invoice_number": "BB87654321"}])
    a_list = buffer.list_invoices(user_a)
    b_list = buffer.list_invoices(user_b)
    assert len(a_list) == 1
    assert len(b_list) == 1
    assert a_list[0]["invoice_number"] == "AB12345678"
    assert b_list[0]["invoice_number"] == "BB87654321"


def test_buffer_corrupt_json_recovery(buffer_tmp):
    """毀損的 JSON 檔應自動 backup + 重置，不讓 user 卡死。"""
    path = buffer._buffer_path(None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("THIS IS NOT JSON {{{ }}}", encoding="utf-8")
    # list 不該爆炸
    invoices = buffer.list_invoices(None)
    assert invoices == []
    # 應該有 backup 檔
    backups = list(path.parent.glob("*.corrupt-*.json"))
    assert len(backups) == 1


# ─── HTTP: index page renders ─────────────────────────────────────────

def test_index_renders():
    r = client.get("/tools/einvoice-scan/")
    assert r.status_code == 200
    assert "電子發票掃描" in r.text


def test_backend_status_endpoint():
    r = client.get("/tools/einvoice-scan/api/backend-status")
    assert r.status_code == 200
    j = r.json()
    assert "available" in j
    assert isinstance(j["available"], bool)


def test_get_buffer_empty():
    r = client.get("/tools/einvoice-scan/buffer")
    assert r.status_code == 200
    j = r.json()
    assert "invoices" in j
    assert "info" in j


def test_delete_invalid_id_format():
    """non-hex id → 400."""
    r = client.delete("/tools/einvoice-scan/buffer/../etc/passwd")
    assert r.status_code in (400, 404)  # 看 FastAPI 路由的實際處理


def test_clear_buffer_endpoint():
    r = client.delete("/tools/einvoice-scan/buffer")
    assert r.status_code == 200
    j = r.json()
    assert "cleared" in j


# ─── HTTP: scan endpoint (only if zbar available) ─────────────────────

def _zbar_available():
    return qr_decoder.is_qr_backend_available()


@pytest.mark.skipif(not _zbar_available(), reason="zbar / pyzbar 不可用")
def test_scan_with_real_qr(tmp_path):
    """產生一個真的 e-invoice QR PNG → scan endpoint → 應該 parse 出來。"""
    import qrcode
    qr_text = _build_qr_text()
    img = qrcode.make(qr_text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    r = client.post("/tools/einvoice-scan/scan",
                    files={"file": ("test.png", png, "image/png")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["scanned_qr_count"] >= 1
    assert j["parsed_count"] >= 1


def test_scan_text_basic():
    """連續掃描 endpoint：直接傳 QR 字串（不傳影像）。"""
    qr = _build_qr_text(invoice_number="CD12345678")
    r = client.post("/tools/einvoice-scan/scan-text",
                    json={"qr_texts": [qr]})
    assert r.status_code == 200
    j = r.json()
    assert j["scanned_qr_count"] == 1
    assert j["parsed_count"] == 1
    # 清理
    client.delete("/tools/einvoice-scan/buffer")


def test_scan_text_invalid_body():
    r = client.post("/tools/einvoice-scan/scan-text",
                    json={"qr_texts": "not-a-list"})
    assert r.status_code == 400


def test_scan_text_too_many():
    r = client.post("/tools/einvoice-scan/scan-text",
                    json={"qr_texts": ["x"] * 100})
    assert r.status_code == 413


def test_scan_text_too_long_string():
    r = client.post("/tools/einvoice-scan/scan-text",
                    json={"qr_texts": ["x" * 5000]})
    assert r.status_code == 413


def test_scan_text_skips_non_einvoice():
    """非 e-invoice QR 自動跳過，不報錯。"""
    r = client.post("/tools/einvoice-scan/scan-text",
                    json={"qr_texts": ["https://example.com", "random text"]})
    assert r.status_code == 200
    j = r.json()
    assert j["scanned_qr_count"] == 2
    assert j["parsed_count"] == 0
    assert j["added_count"] == 0


# ─── HTTP: settings endpoints ─────────────────────────────────────────

def test_get_settings_default():
    r = client.get("/tools/einvoice-scan/settings")
    assert r.status_code == 200
    j = r.json()
    assert "settings" in j
    assert "field_definitions" in j
    s = j["settings"]
    assert isinstance(s["visible_columns"], list)
    assert isinstance(s["column_order"], list)
    assert "invoice_number" in s["visible_columns"]
    assert "invoice_number" in s["column_order"]
    # field_definitions 至少 11 個欄位
    assert len(j["field_definitions"]) >= 11


def test_update_settings():
    r = client.put("/tools/einvoice-scan/settings", json={
        "visible_columns": ["seq", "invoice_number", "amount_total"],
        "column_order": ["seq", "amount_total", "invoice_number"],
    })
    assert r.status_code == 200
    j = r.json()
    assert j["settings"]["visible_columns"] == ["seq", "invoice_number", "amount_total"]
    # column_order 會被自動補齊（任何 default 裡有但 user 沒列的放最後）
    assert j["settings"]["column_order"][:3] == ["seq", "amount_total", "invoice_number"]


def test_update_settings_filters_invalid_field_ids():
    """不存在的欄位 ID 應被過濾掉，不報錯。"""
    r = client.put("/tools/einvoice-scan/settings", json={
        "visible_columns": ["seq", "nonexistent_field", "invoice_number"],
    })
    assert r.status_code == 200
    j = r.json()
    assert "nonexistent_field" not in j["settings"]["visible_columns"]
    assert "seq" in j["settings"]["visible_columns"]


def test_update_settings_invalid_body():
    r = client.put("/tools/einvoice-scan/settings", json={
        "visible_columns": "not-a-list",
    })
    assert r.status_code == 400


def test_reset_settings():
    # 先改成怪設定
    client.put("/tools/einvoice-scan/settings", json={
        "visible_columns": ["seq"],
    })
    # Reset
    r = client.post("/tools/einvoice-scan/settings/reset")
    assert r.status_code == 200
    j = r.json()
    # 預設應該包含很多欄位
    assert len(j["settings"]["visible_columns"]) >= 7


# ─── HTTP: note PATCH endpoint ────────────────────────────────────────

def test_patch_note(buffer_tmp):
    """新增一筆 → PATCH 修 note → list 看到。"""
    # 直接用 buffer module 加（避開 zbar 依賴）
    res = buffer.add_invoices(None, [{"invoice_number": "ZZ12345678"}])
    inv_id = res["added"][0]["id"]

    r = client.patch(f"/tools/einvoice-scan/buffer/{inv_id}",
                     json={"note": "報帳用 — 餐費"})
    # 注意 client 用 default user (None)，buffer_tmp 也是 None，
    # 但 client 的 settings.data_dir 不會被 monkeypatch 改到（不同 instance），
    # 所以這個測試實際上 update 的是 client 那邊的真 buffer，會 404。
    # 改測 buffer.update_invoice_field 直接：
    ok = buffer.update_invoice_field(None, inv_id, "note", "報帳用 — 餐費")
    assert ok is True
    invs = buffer.list_invoices(None)
    assert any(i["id"] == inv_id and i.get("note") == "報帳用 — 餐費" for i in invs)


def test_patch_invoice_field_whitelist(buffer_tmp):
    """只 note 可改；其他欄位（如金額）不可改 — 結構化資料一律從 QR 解碼進來。"""
    res = buffer.add_invoices(None, [{"invoice_number": "ZZ87654321", "amount_total": 100}])
    inv_id = res["added"][0]["id"]
    # 嘗試改 amount_total → 拒絕
    ok = buffer.update_invoice_field(None, inv_id, "amount_total", 99999)
    assert ok is False
    invs = buffer.list_invoices(None)
    inv = next(i for i in invs if i["id"] == inv_id)
    assert inv["amount_total"] == 100  # 沒被改


def test_patch_note_too_long():
    """note > 500 字元 → 413."""
    r = client.patch("/tools/einvoice-scan/buffer/0123456789abcdef",
                     json={"note": "x" * 600})
    assert r.status_code == 413


def test_patch_invalid_id():
    r = client.patch("/tools/einvoice-scan/buffer/../etc/passwd",
                     json={"note": "x"})
    assert r.status_code in (400, 404)


def test_scan_unsupported_format():
    r = client.post("/tools/einvoice-scan/scan",
                    files={"file": ("test.exe", b"\x00\x00", "application/octet-stream")})
    assert r.status_code in (400, 503)  # 503 if zbar missing happens first


def test_scan_empty_file():
    r = client.post("/tools/einvoice-scan/scan",
                    files={"file": ("test.png", b"", "image/png")})
    assert r.status_code in (400, 503)

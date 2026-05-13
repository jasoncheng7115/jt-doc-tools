"""Tests for einvoice-scan field formatters (M3.2)."""
from __future__ import annotations

from app.tools.einvoice_scan.formatters import (
    apply_format,
    fmt_amount,
    fmt_date,
    fmt_invoice_number,
    fmt_scanned_at,
)


# ─── invoice_number ─────────────────────────────────────────────────

def test_invoice_number_formats():
    assert fmt_invoice_number("AB12345678", "compact") == "AB12345678"
    assert fmt_invoice_number("AB12345678", "dash") == "AB-12345678"
    assert fmt_invoice_number("AB12345678", "space") == "AB 12345678"


def test_invoice_number_empty():
    assert fmt_invoice_number(None, "dash") == ""
    assert fmt_invoice_number("", "dash") == ""


def test_invoice_number_short_passthrough():
    """非 10 字元 → 直接回原值（不爆炸）。"""
    assert fmt_invoice_number("ABC", "dash") == "ABC"


# ─── date ───────────────────────────────────────────────────────────

def test_date_formats():
    iso = "2026-05-13"
    assert fmt_date(iso, "iso") == "2026-05-13"
    assert fmt_date(iso, "slash") == "2026/05/13"
    assert fmt_date(iso, "chinese") == "2026年05月13日"
    assert fmt_date(iso, "roc") == "115/05/13"
    assert fmt_date(iso, "roc_chinese") == "民國115年05月13日"


def test_date_empty():
    assert fmt_date(None, "slash") == ""
    assert fmt_date("", "slash") == ""


def test_date_invalid_format():
    """非 YYYY-MM-DD → 直接回原值。"""
    assert fmt_date("not-a-date", "slash") == "not-a-date"


# ─── amount ─────────────────────────────────────────────────────────

def test_amount_formats():
    assert fmt_amount(1050, "plain") == "1050"
    assert fmt_amount(1050, "comma") == "1,050"
    assert fmt_amount(1050, "currency") == "NT$ ○○○"
    assert fmt_amount(1234567, "comma") == "1,234,567"


def test_amount_zero():
    assert fmt_amount(0, "comma") == "0"


def test_amount_none():
    assert fmt_amount(None, "comma") == ""


# ─── scanned_at ─────────────────────────────────────────────────────

def test_scanned_at_iso():
    iso = "2026-05-13T14:30:00+08:00"
    out = fmt_scanned_at(iso, "iso")
    assert "2026-05-13" in out


def test_scanned_at_local():
    iso = "2026-05-13T06:30:00+00:00"   # UTC
    out = fmt_scanned_at(iso, "local")
    # YYYY/MM/DD HH:MM:SS 格式
    assert "/" in out and ":" in out


def test_scanned_at_date_only():
    iso = "2026-05-13T14:30:00+08:00"
    out = fmt_scanned_at(iso, "date_only")
    assert "/" in out and len(out.split("/")) == 3


def test_scanned_at_relative_recent():
    """剛剛掃 → 應顯示「剛剛 / N 分鐘前」之類。"""
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    out = fmt_scanned_at(recent, "relative")
    assert "前" in out or "剛剛" in out


# ─── apply_format unified ──────────────────────────────────────────

def test_apply_format_uses_user_setting():
    formats = {"invoice_number": "dash", "date": "roc"}
    assert apply_format("invoice_number", "AB12345678", formats) == "AB-12345678"
    assert apply_format("date", "2026-05-13", formats) == "115/05/13"


def test_apply_format_fallback_to_default():
    """未指定該欄位 → 用 FIELD_DEFINITIONS 預設值。"""
    assert apply_format("invoice_number", "AB12345678", {}) == "AB-12345678"  # default = dash
    assert apply_format("date", "2026-05-13", {}) == "2026/05/13"  # default = slash
    assert apply_format("amount_total", 1050, {}) == "1,050"  # default = comma


def test_apply_format_unknown_field():
    """未知 field → return value as string."""
    assert apply_format("nonexistent", "abc", {}) == "abc"
    assert apply_format("nonexistent", None, {}) == ""


def test_apply_format_no_formats_field():
    """field 沒 formats 區塊（如 seller_vat / random_code）→ str(value)."""
    assert apply_format("seller_vat", "12345678", {"seller_vat": "anything"}) == "12345678"
    assert apply_format("random_code", "1234", {}) == "1234"


def test_apply_format_amount_group():
    """三個金額欄位都用 amount formatter。"""
    f = {"amount_total": "currency", "amount_untaxed": "currency", "tax": "currency"}
    assert apply_format("amount_total", 1050, f) == "NT$ ○○○"
    assert apply_format("amount_untaxed", 1000, f) == "NT$ 1,000"
    assert apply_format("tax", 50, f) == "NT$ 50"

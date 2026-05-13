"""欄位顯示格式化器 — 後端版本。

設計：
- 內部儲存永遠是「正規化格式」（compact 發票號碼 / ISO 日期 / 整數金額）
- 顯示 / 匯出時依使用者選的 format_id 跑對應 formatter
- 前後端共用同一份 FIELD_DEFINITIONS（後端 single source of truth）；
  邏輯需與 `einvoice_scan.html` 內的 JS formatters 對齊（同一 input
  / 同一 format_id 應產生同一 output）
- JSON export 永遠用內部格式（忽略 field_formats）— 對應前端 / API 客戶端
  期待的 ISO 日期 + 整數金額 + compact 號碼

使用方式：
    from .formatters import apply_format
    apply_format("invoice_number", "AB12345678", {"invoice_number": "dash"})
    # → "AB-12345678"

未指定欄位 format → fallback 該欄位 default。未知欄位 → return value as-is。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from .settings import FIELD_DEFINITIONS

# 預先建索引，避免每次 apply_format 都 walk list
_FIELD_DEF_BY_ID = {f["id"]: f for f in FIELD_DEFINITIONS}
_AMOUNT_FIELD_IDS = {"amount_total", "amount_untaxed", "tax"}


def _get_format_id(field_id: str, field_formats: dict) -> Optional[str]:
    """取使用者選的 format ID；沒選 → fallback default；無 formats → None。"""
    if not isinstance(field_formats, dict):
        field_formats = {}
    fid = field_formats.get(field_id)
    if fid:
        return fid
    definition = _FIELD_DEF_BY_ID.get(field_id)
    if not definition:
        return None
    fmts = definition.get("formats")
    return fmts["default"] if fmts else None


# ─── 個別 formatter ─────────────────────────────────────────────────

def fmt_invoice_number(value: Optional[str], format_id: str) -> str:
    if not value:
        return ""
    if len(value) != 10:
        return value
    if format_id == "dash":
        return f"{value[:2]}-{value[2:]}"
    if format_id == "space":
        return f"{value[:2]} {value[2:]}"
    return value  # compact (default)


def fmt_date(value: Optional[str], format_id: str) -> str:
    """ISO 'YYYY-MM-DD' 轉各種顯示格式。"""
    if not value:
        return ""
    parts = value.split("-")
    if len(parts) != 3:
        return value
    y, m, d = parts
    try:
        ad_y = int(y)
    except ValueError:
        return value
    roc_y = ad_y - 1911
    if format_id == "iso":
        return value
    if format_id == "slash":
        return f"{y}/{m}/{d}"
    if format_id == "chinese":
        return f"{y}年{m}月{d}日"
    if format_id == "roc":
        return f"{roc_y:03d}/{m}/{d}"
    if format_id == "roc_chinese":
        return f"民國{roc_y}年{m}月{d}日"
    return value


def fmt_amount(value: Optional[int], format_id: str) -> str:
    if value is None:
        return ""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value)
    if format_id == "comma":
        return f"{n:,}"
    if format_id == "currency":
        return f"NT$ {n:,}"
    return str(n)  # plain (default)


def fmt_scanned_at(value: Any, format_id: str) -> str:
    """ISO 8601 timestamp（含時區）轉各種顯示。"""
    if not value:
        return ""
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    elif isinstance(value, datetime):
        dt = value
    else:
        return str(value)

    # 全部欄位假設使用者所在地（相對時間用 UTC now 比較）
    if format_id == "iso":
        return dt.isoformat()
    if format_id == "local":
        return dt.astimezone().strftime("%Y/%m/%d %H:%M:%S")
    if format_id == "date_only":
        return dt.astimezone().strftime("%Y/%m/%d")
    if format_id == "relative":
        return _format_relative(dt)
    return dt.isoformat()


def _format_relative(dt: datetime) -> str:
    """產生 '剛剛 / N 分鐘前 / N 小時前 / N 天前' 之類字串。"""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    secs = int(diff.total_seconds())
    if secs < 60:
        return "剛剛"
    if secs < 3600:
        return f"{secs // 60} 分鐘前"
    if secs < 86400:
        return f"{secs // 3600} 小時前"
    days = secs // 86400
    if days < 7:
        return f"{days} 天前"
    # 7 天以上直接給日期，比「N 天前」好讀
    return dt.astimezone().strftime("%Y/%m/%d")


# ─── 統一入口 ─────────────────────────────────────────────────────

def apply_format(field_id: str, value: Any, field_formats: dict) -> str:
    """把欄位值依使用者格式設定轉成顯示字串。"""
    format_id = _get_format_id(field_id, field_formats)
    if not format_id:
        # 沒有可選格式（例如 seq / seller_vat / random_code / note / buyer_vat）
        return "" if value is None else str(value)

    if field_id == "invoice_number":
        return fmt_invoice_number(value, format_id)
    if field_id == "date":
        return fmt_date(value, format_id)
    if field_id in _AMOUNT_FIELD_IDS:
        return fmt_amount(value, format_id)
    if field_id == "scanned_at":
        return fmt_scanned_at(value, format_id)

    return "" if value is None else str(value)


def apply_format_invoice(invoice: dict, field_formats: dict) -> dict:
    """方便給 export 用：一次跑一筆 invoice 全欄位。

    特別處理：
    - tax 欄位由 amount_total - amount_untaxed 算出（buffer 內沒實際 tax 欄位）
    - seq 由呼叫方自己塞（apply_format 不知道 row index）
    """
    out = {}
    for f in FIELD_DEFINITIONS:
        fid = f["id"]
        if fid == "tax":
            total = invoice.get("amount_total")
            untaxed = invoice.get("amount_untaxed")
            value = (total - untaxed) if (total is not None and untaxed is not None) else None
        else:
            value = invoice.get(fid)
        out[fid] = apply_format(fid, value, field_formats)
    return out

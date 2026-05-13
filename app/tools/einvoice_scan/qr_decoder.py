"""QR Code 解碼 + 台灣電子發票 (B2C) 結構化資料 parser.

台灣財政部 B2C 電子發票標準（明碼摘要式）QR Code 編碼規範：
左 QR Code 前 77 個字元為定長欄位，之後為變長品項資料。

| Offset | Len | Field            | 說明                                |
|--------|-----|------------------|-------------------------------------|
| 0      | 10  | invoice_number   | 發票字軌號碼 e.g. "AB12345678"      |
| 10     | 7   | date             | 開立日期 民國年 YYYMMDD e.g. "1150513" |
| 17     | 4   | random_code      | 隨機碼                              |
| 21     | 8   | amount_untaxed   | 銷售額 (16 進位 8 字元)             |
| 29     | 8   | amount_total     | 總計金額 (16 進位 8 字元)           |
| 37     | 8   | buyer_vat        | 買方統編 (00000000 = 無)            |
| 45     | 8   | seller_vat       | 賣方統編                            |
| 53     | 24  | encrypt_check    | 加密驗證碼 (本程式不驗證)           |
| 77+    | -   | : 分隔的變長資料 | **編碼方式 / 品項加密 / 品項數 / 品項… |

右 QR Code 通常為 `**` 開頭的品項延續資料（非必要）。

設計：
- pyzbar 是 optional dependency；missing 時 raise QRBackendUnavailable，由
  caller 顯示友善訊息（請使用者手動 install pyzbar）
- decode_image() 接受 bytes（PNG / JPG / WebP），回 list[str] 原始 QR 字串
- parse_einvoice_qr() 拆 left QR fixed fields，回 dict；不是 e-invoice QR
  會 return None（不爆炸，因為使用者可能掃到別張 QR）
"""
from __future__ import annotations

import io
import re
from datetime import datetime
from typing import Optional


class QRBackendUnavailable(Exception):
    """pyzbar / zbar binary 沒裝。"""


def is_qr_backend_available() -> bool:
    """確認 pyzbar 可用 — 檢查 import + zbar shared lib 載入是否成功。"""
    try:
        # 真的 import 才會觸發 zbar shared lib 載入
        from pyzbar import pyzbar  # noqa: F401
        return True
    except Exception:
        return False


def decode_image(data: bytes) -> list[str]:
    """從圖片 bytes 解碼出所有 QR Code 字串（按位置排序：左→右、上→下）。

    Raises QRBackendUnavailable if pyzbar / zbar 不可用。
    Returns [] if 沒有 QR code。
    """
    try:
        from PIL import Image
        from pyzbar import pyzbar
    except ImportError as e:
        raise QRBackendUnavailable(
            f"pyzbar / Pillow 未安裝 ({e})。請執行 jtdt update 或手動安裝："
            f" pip install pyzbar Pillow"
        )

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"無法解析圖片：{e}")

    # 統一轉 RGB（pyzbar 對 RGBA / palette 偶有問題）
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGB")

    results = pyzbar.decode(img)
    if not results:
        return []

    # 按位置排序（top → bottom，再 left → right）讓「左 QR / 右 QR」順序穩定
    def _key(r):
        return (r.rect.top // 50, r.rect.left)

    sorted_results = sorted(results, key=_key)
    out = []
    for r in sorted_results:
        try:
            text = r.data.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        if text:
            out.append(text)
    return out


def decode_pdf(data: bytes, max_pages: int = 20) -> list[tuple[int, str]]:
    """從 PDF bytes 把每頁 render 成 PNG 後解碼 QR。

    Returns list of (page_index_1based, qr_text)。
    PyMuPDF 必裝（既有依賴），但 pyzbar 仍可能 missing → 會 raise。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise QRBackendUnavailable(f"PyMuPDF 未安裝：{e}")

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:
        raise ValueError(f"無法解析 PDF：{e}")

    out: list[tuple[int, str]] = []
    try:
        for i, page in enumerate(doc[:max_pages], start=1):
            # 200 DPI 對 QR 已經夠 — 太高反而慢
            pix = page.get_pixmap(dpi=200)
            png = pix.tobytes("png")
            for qr in decode_image(png):
                out.append((i, qr))
    finally:
        doc.close()
    return out


# ─── Taiwan e-invoice parser ────────────────────────────────────────

_INVOICE_NUMBER_RE = re.compile(r"^[A-Z]{2}\d{8}$")
_VAT_RE = re.compile(r"^\d{8}$")
_DATE_ROC_RE = re.compile(r"^(\d{3,4})(\d{2})(\d{2})$")  # 1150513 / 11150513


def _parse_roc_date(s: str) -> Optional[str]:
    """民國 YYYMMDD → ISO YYYY-MM-DD；失敗回 None。"""
    if not s:
        return None
    m = _DATE_ROC_RE.match(s)
    if not m:
        return None
    yy, mm, dd = m.groups()
    try:
        roc_y = int(yy)
        ad_y = roc_y + 1911
        # 驗 month / day 真的合法
        datetime(ad_y, int(mm), int(dd))
        return f"{ad_y:04d}-{mm}-{dd}"
    except (ValueError, OverflowError):
        return None


def _parse_hex_amount(s: str) -> Optional[int]:
    """16 進位字串 → int；失敗回 None。"""
    if not s:
        return None
    try:
        return int(s, 16)
    except ValueError:
        return None


def parse_einvoice_qr(qr_text: str) -> Optional[dict]:
    """把單一 QR 字串嘗試 parse 成 e-invoice fixed-field dict。

    Returns None 如果不是 e-invoice 格式（長度不足 / 號碼格式錯）。

    回傳 dict 欄位：
        invoice_number: str (10 char)
        date:           str (ISO YYYY-MM-DD) 或 None
        random_code:    str (4 char)
        amount_untaxed: int 或 None
        amount_total:   int 或 None
        buyer_vat:      str (8 digits) 或 None（"00000000" 視為無）
        seller_vat:     str (8 digits) 或 None
    """
    if not qr_text or len(qr_text) < 77:
        return None

    invoice_number = qr_text[0:10]
    if not _INVOICE_NUMBER_RE.match(invoice_number):
        return None  # 不是 e-invoice 格式（可能掃到別張 QR）

    date = _parse_roc_date(qr_text[10:17])
    random_code = qr_text[17:21]
    amount_untaxed = _parse_hex_amount(qr_text[21:29])
    amount_total = _parse_hex_amount(qr_text[29:37])

    buyer_raw = qr_text[37:45]
    seller_raw = qr_text[45:53]
    buyer_vat = buyer_raw if (_VAT_RE.match(buyer_raw) and buyer_raw != "00000000") else None
    seller_vat = seller_raw if _VAT_RE.match(seller_raw) else None

    return {
        "invoice_number": invoice_number,
        "date": date,
        "random_code": random_code,
        "amount_untaxed": amount_untaxed,
        "amount_total": amount_total,
        "buyer_vat": buyer_vat,
        "seller_vat": seller_vat,
    }


def parse_right_qr_items(text: str) -> Optional[list[str]]:
    """從右 QR 字串解析品項清單 (best-effort).

    台灣 B2C 電子發票右 QR 格式（明碼摘要式）：
        **<二維碼種類>:<編碼方式>:<品項加密>:<品項數>:<本張品項數>:<中文編碼>:<品項1>:<品項2>:...

    例：`**1:0:0:5:5:Big5:鉛筆:橡皮擦:文件夾:...`

    本 parser 簡單版：
    - 必須 `**` 開頭
    - 用 `:` 分段
    - 跳過前 6 個 metadata 欄位，剩下視為品項
    - 品項加密 (第 3 欄 = '1') 時無法解（內建沒實作 AES）→ 回 None

    Returns list of strings 或 None（不是右 QR / 加密 / 解析失敗）。
    """
    if not text or not text.startswith("**"):
        return None
    parts = text.split(":")
    if len(parts) < 7:
        return None
    # 第 3 欄 (index 2) = 品項加密旗標 ('0' / '1')
    if parts[2] == "1":
        return None  # 加密，無法解
    # 從 index 6 起為品項
    items = [p.strip() for p in parts[6:] if p and p.strip() and not p.startswith("**")]
    return items if items else None


def parse_qr_list(qr_list: list[str]) -> list[dict]:
    """把多筆 QR 字串 parse；自動跳過非 e-invoice。

    自動配對左 QR + 右 QR（同一影像中常一起出現）：
    - 左 QR 解出來後，看清單中是否有 `**` 開頭的右 QR；如果有，把品項 attach 到 invoice
    - 一張影像有多筆左 QR + 右 QR 時，按出現順序配對（zbar 已 sort by position）
    """
    invoices = []
    items_lists = []  # 同步順序
    for qr in qr_list:
        parsed = parse_einvoice_qr(qr)
        if parsed:
            invoices.append(parsed)
        else:
            items = parse_right_qr_items(qr)
            if items is not None:
                items_lists.append(items)

    # Pair items 1:1（多餘的丟掉）
    for i, items in enumerate(items_lists):
        if i < len(invoices):
            invoices[i]["items"] = items

    return invoices

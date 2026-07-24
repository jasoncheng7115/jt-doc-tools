"""API 文件契約回歸測試。

守住「使用者照 API.md 範例呼叫 → 不該壞」這條線。徹查時抓到的問題：
- pdf-compress 非法 preset → 未接的 ValueError → 500（改優雅 400）；
  文件範例誤用 `preset=ebook`（其實是 Ghostscript gs_preset）。
- pdf-pages 文件寫 mode=keep/delete，但程式只認 reorder/drop（改加別名）。
- pdf-encrypt 文件寫 algorithm=AES-256（大寫），程式只認小寫（改大小寫不敏感）。

這些工具不需 soffice（純 PyMuPDF），可用 TestClient 直接跑。
"""
from __future__ import annotations

from io import BytesIO

import fitz
from fastapi.testclient import TestClient

import app.main as app_main


def _pdf(n: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(n):
        pg = doc.new_page(width=595, height=842)
        pg.insert_text((72, 100), f"Page {i + 1} 測試", fontsize=14)
    buf = BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _c() -> TestClient:
    return TestClient(app_main.app)


# ---------- pdf-compress：非法 preset 不可 500 ----------

def test_compress_invalid_preset_is_400_not_500():
    """任何非法 preset（含文件曾誤寫的 ebook）→ 優雅 400，絕不 500。"""
    c = _c()
    for bad in ("ebook", "screen", "xyz", "PRINTER"):
        r = c.post("/tools/pdf-compress/api/pdf-compress",
                   files={"file": ("d.pdf", _pdf(), "application/pdf")},
                   data={"preset": bad})
        assert r.status_code == 400, f"preset={bad} → {r.status_code}（不該 500/其他）"
        assert r.status_code != 500


def test_compress_valid_presets_ok():
    """文件標示的三個合法 preset 都能成功轉出 PDF。"""
    c = _c()
    for good in ("gentle", "balanced", "aggressive"):
        r = c.post("/tools/pdf-compress/api/pdf-compress",
                   files={"file": ("d.pdf", _pdf(), "application/pdf")},
                   data={"preset": good})
        assert r.status_code == 200, f"preset={good} → {r.status_code}"
        assert r.content[:4] == b"%PDF"


# ---------- pdf-pages：keep / delete 友善別名 ----------

def test_pages_keep_alias_keeps_pages():
    """文件範例 mode=keep 必須可用（別名 → reorder），只留指定頁。"""
    c = _c()
    r = c.post("/tools/pdf-pages/api/pdf-pages",
               files={"file": ("d.pdf", _pdf(3), "application/pdf")},
               data={"mode": "keep", "spec": "1,3"})
    assert r.status_code == 200, r.text
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count == 2  # 只留第 1、3 頁
    out.close()


def test_pages_delete_alias_drops_pages():
    """mode=delete（別名 → drop）刪掉指定頁。"""
    c = _c()
    r = c.post("/tools/pdf-pages/api/pdf-pages",
               files={"file": ("d.pdf", _pdf(3), "application/pdf")},
               data={"mode": "delete", "spec": "2"})
    assert r.status_code == 200, r.text
    out = fitz.open(stream=r.content, filetype="pdf")
    assert out.page_count == 2  # 刪掉第 2 頁
    out.close()


def test_pages_original_modes_still_work():
    """reorder / drop 原生 mode 不可被別名破壞（向後相容）。"""
    c = _c()
    for mode in ("reorder", "drop"):
        r = c.post("/tools/pdf-pages/api/pdf-pages",
                   files={"file": ("d.pdf", _pdf(3), "application/pdf")},
                   data={"mode": mode, "spec": "1,2"})
        assert r.status_code == 200, f"mode={mode} → {r.text}"


def test_pages_bad_mode_is_400():
    c = _c()
    r = c.post("/tools/pdf-pages/api/pdf-pages",
               files={"file": ("d.pdf", _pdf(2), "application/pdf")},
               data={"mode": "nonsense", "spec": "1"})
    assert r.status_code == 400


# ---------- pdf-encrypt：algorithm 大小寫不敏感 ----------

def test_encrypt_uppercase_algorithm_ok():
    """文件範例 algorithm=AES-256（大寫）必須可用。"""
    c = _c()
    for algo in ("AES-256", "aes-256", "AES-128", "RC4-128"):
        r = c.post("/tools/pdf-encrypt/api/pdf-encrypt",
                   files={"file": ("d.pdf", _pdf(1), "application/pdf")},
                   data={"user_pw": "open123", "algorithm": algo})
        assert r.status_code == 200, f"algorithm={algo} → {r.status_code} {r.text[:60]}"
        assert r.content[:4] == b"%PDF"


def test_encrypt_bad_algorithm_is_400():
    c = _c()
    r = c.post("/tools/pdf-encrypt/api/pdf-encrypt",
               files={"file": ("d.pdf", _pdf(1), "application/pdf")},
               data={"user_pw": "x", "algorithm": "twofish"})
    assert r.status_code == 400

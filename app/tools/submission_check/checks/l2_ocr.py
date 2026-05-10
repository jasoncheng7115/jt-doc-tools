"""L2 OCR — 對掃描 PDF / 圖片證書做 OCR，補抓文字層抓不到的內容。

策略：
- PDF 有文字層 → 跳過 OCR (text 已 cover)
- PDF 無文字層 (掃描檔) → 每頁渲染成 PNG → tesseract OCR
- 圖片 (jpg/png/tiff) → 直接 tesseract OCR
- chi_tra 為主、依 admin 設定可加 chi_sim / jpn / kor / eng

技術細節：
- 用既有 sys_deps.configure_pytesseract() 解決 Windows tesseract path
- DPI 200 對掃描品質夠（高 DPI 慢且未必更準）
- 大檔保護：每檔最多 OCR 30 頁 (avoid stuck 50+ page bid PDFs)；超過標 partial
"""
from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Optional


# 預設 OCR 語言 — 跟 admin 設定整合在 v2，目前繁中為主
DEFAULT_LANGS = "chi_tra+chi_sim+eng"
MAX_OCR_PAGES = 30  # 單檔上限


def is_tesseract_available() -> bool:
    """檢查 tesseract binary 是否可用。"""
    import shutil
    try:
        from app.core.sys_deps import configure_pytesseract
        path = configure_pytesseract()
        if path:
            return True
    except Exception:
        pass
    return bool(shutil.which("tesseract"))


def _has_text_layer(pdf_path: Path) -> bool:
    """檢查 PDF 是否有實質文字層（避免對已抽得到字的檔案重複 OCR）。"""
    try:
        import fitz
    except ImportError:
        return False
    try:
        doc = fitz.open(str(pdf_path))
        try:
            # 抽前 3 頁文字，> 100 字元就視為有文字層
            chars = 0
            for i, page in enumerate(doc):
                if i >= 3:
                    break
                chars += len(page.get_text() or "")
            return chars > 100
        finally:
            doc.close()
    except Exception:
        return False


def _ocr_image_bytes(img_bytes: bytes, langs: str = DEFAULT_LANGS) -> str:
    """對圖片 bytes 跑 OCR。"""
    try:
        from app.core.sys_deps import configure_pytesseract
        configure_pytesseract()
    except Exception:
        pass
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        return pytesseract.image_to_string(img, lang=langs) or ""
    except Exception:
        return ""


def _ocr_pdf_pages(pdf_path: Path, langs: str = DEFAULT_LANGS,
                    dpi: int = 200, max_pages: int = MAX_OCR_PAGES) -> tuple[str, int, bool]:
    """渲染 PDF 每頁 → OCR → 串接文字。
    回 (text, pages_processed, truncated)。
    """
    try:
        import fitz
    except ImportError:
        return ("", 0, False)
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        return ("", 0, False)

    texts: list[str] = []
    pages_done = 0
    truncated = False
    try:
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for pno in range(doc.page_count):
            if pno >= max_pages:
                truncated = True
                break
            page = doc[pno]
            try:
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")
                t = _ocr_image_bytes(img_bytes, langs=langs)
                if t.strip():
                    texts.append(f"[Page {pno + 1}]\n{t}")
                pages_done += 1
            except Exception:
                continue
    finally:
        doc.close()

    return ("\n\n".join(texts), pages_done, truncated)


def ocr_file(path: Path, langs: str = DEFAULT_LANGS) -> dict:
    """L2 OCR 主 entry。

    Returns:
        {
          "text": str,           # OCR 出的文字（空字串 = OCR 沒做或失敗）
          "ran": bool,           # 是否真的跑了 OCR
          "skip_reason": str,    # ran=False 時為什麼
          "pages_processed": int,
          "truncated": bool,
          "elapsed": float,      # 秒
        }
    """
    started = time.time()
    suffix = path.suffix.lower()
    out = {
        "text": "", "ran": False, "skip_reason": "",
        "pages_processed": 0, "truncated": False, "elapsed": 0.0,
    }

    # 圖片 → 直接 OCR
    if suffix in (".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"):
        try:
            img_bytes = path.read_bytes()
            text = _ocr_image_bytes(img_bytes, langs=langs)
            out["text"] = text
            out["ran"] = bool(text)
            out["pages_processed"] = 1
            out["elapsed"] = time.time() - started
            if not text:
                out["skip_reason"] = "OCR 結果為空（圖片可能太小 / 模糊 / tesseract 缺對應語言）"
        except Exception as e:
            out["skip_reason"] = f"圖片 OCR 失敗：{e}"
        return out

    # PDF → 一律跑 OCR（即使有文字層 — 掃描檔有時 PDF 編輯器會插假文字層或 garbage 文字，
    # 仍需要 OCR 從圖層真正讀字以正確比對；而且章 / 印 / 圖內字只有 OCR 抓得到）
    if suffix == ".pdf":
        text, pages, truncated = _ocr_pdf_pages(path, langs=langs)
        out["text"] = text
        out["ran"] = pages > 0
        out["pages_processed"] = pages
        out["truncated"] = truncated
        out["elapsed"] = time.time() - started
        if not pages:
            out["skip_reason"] = "PDF 無法渲染 / OCR 失敗"
        return out

    # 其他 (DOCX 等)
    out["skip_reason"] = f"不支援 OCR 此檔案類型：{suffix}"
    out["elapsed"] = time.time() - started
    return out


def make_findings(file_id: str, ocr_result: dict, file_name: str = "") -> list[dict]:
    """根據 OCR 結果產生 L2 層 findings（資訊性質）。"""
    findings: list[dict] = []
    if not ocr_result.get("ran"):
        if ocr_result.get("skip_reason") and "已有文字層" not in ocr_result.get("skip_reason", ""):
            findings.append({
                "layer": "L2",
                "severity": "info",
                "category": "ocr-skipped",
                "title": "OCR 未跑",
                "detail": ocr_result.get("skip_reason", ""),
                "page": None, "evidence": {},
            })
        return findings
    if ocr_result.get("truncated"):
        findings.append({
            "layer": "L2",
            "severity": "info",
            "category": "ocr-truncated",
            "title": f"OCR 只處理前 {ocr_result['pages_processed']} 頁",
            "detail": (f"檔案頁數較多，OCR 僅處理前 {MAX_OCR_PAGES} 頁。"
                       "後續頁面內的文字未進入跨檔一致性比對。"),
            "page": None,
            "evidence": {"pages_processed": ocr_result["pages_processed"]},
        })
    return findings

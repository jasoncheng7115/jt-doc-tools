"""PDF OCR 核心 — 對 PDF 每頁跑 tesseract，回傳 word-level bbox+text，
然後用 PyMuPDF 寫透明文字層回原 PDF。

PDF 「透明文字層」原理：
- PyMuPDF page.insert_text 預設 render_mode=0（fill 可見）
- render_mode=3 = invisible — 文字被「畫」在頁面但 fill / stroke 都關
  → 視覺看不到，但 PDF reader 仍能命中（cmd+F 搜尋、滑鼠選取、文字抽取）
- 同 macOS Preview Live Text、Adobe 「Make Searchable PDF」做的事

OCR 信心 < 30 的 word 跳過（太可能是雜訊）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

import fitz

log = logging.getLogger(__name__)

DEFAULT_LANGS = "chi_tra+chi_sim+eng"
DEFAULT_DPI = 300
MIN_CONF = 30  # tesseract conf 0-100, < 30 視為雜訊跳過


def _tesseract_image_to_data(img_bytes: bytes, langs: str):
    """跑 tesseract 對單張圖回 word-level data。
    回 list of dicts: [{text, conf, left, top, width, height}, ...]
    用 image_to_data 拿到 bbox（image_to_string 沒 bbox）。
    """
    try:
        from app.core.sys_deps import configure_pytesseract
        configure_pytesseract()
    except Exception:
        pass
    try:
        import pytesseract
        from pytesseract import Output
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        data = pytesseract.image_to_data(img, lang=langs, output_type=Output.DICT)
    except Exception as e:
        log.warning("tesseract image_to_data failed: %s", e)
        return []

    n = len(data.get("text", []))
    out = []
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = 0
        if conf < MIN_CONF:
            continue
        out.append({
            "text": text,
            "conf": conf,
            "left": int(data["left"][i]),
            "top": int(data["top"][i]),
            "width": int(data["width"][i]),
            "height": int(data["height"][i]),
        })
    return out


def add_text_layer_to_page(page: "fitz.Page", words: list[dict],
                             dpi: int = DEFAULT_DPI) -> int:
    """把 OCR 出來的 words list 以透明文字寫進 page。
    words 內 bbox 是「影像座標」(px @ dpi)，轉 PDF pt: pt = px * 72 / dpi
    回 inserted word count。
    """
    if not words:
        return 0
    px_to_pt = 72.0 / dpi
    n = 0
    for w in words:
        text = w["text"]
        if not text:
            continue
        x_pt = w["left"] * px_to_pt
        y_top_pt = w["top"] * px_to_pt
        h_pt = w["height"] * px_to_pt
        # PyMuPDF insert_text 用 baseline 為基準，baseline ≈ top + height * 0.85
        baseline_y = y_top_pt + h_pt * 0.85
        font_size = max(4.0, h_pt * 0.9)
        try:
            page.insert_text(
                fitz.Point(x_pt, baseline_y),
                text,
                fontname="china-t",  # 用 PyMuPDF 內建支援 CJK 的字型
                fontsize=font_size,
                color=(0, 0, 0),
                render_mode=3,  # 透明
            )
            n += 1
        except Exception as e:
            # 字型 / glyph 缺；改 fallback helv（ASCII OK，CJK 變 .notdef
            # 但 invisible 看不到，搜尋 / 選取仍 work）
            try:
                page.insert_text(
                    fitz.Point(x_pt, baseline_y),
                    text,
                    fontname="helv",
                    fontsize=font_size,
                    color=(0, 0, 0),
                    render_mode=3,
                )
                n += 1
            except Exception:
                continue
    return n


def page_has_text_layer(page: "fitz.Page") -> bool:
    """檢查頁面是否已有實質文字層（避免重複 OCR）。"""
    try:
        txt = page.get_text() or ""
        return len(txt.strip()) > 30
    except Exception:
        return False


def ocr_pdf_to_searchable(
    src_pdf: Path, dst_pdf: Path, *,
    langs: str = DEFAULT_LANGS,
    dpi: int = DEFAULT_DPI,
    skip_pages_with_text: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    llm_postprocess: Optional[Callable[[str], str]] = None,
) -> dict:
    """主 entry — 開 src_pdf，逐頁 OCR + 加文字層，存到 dst_pdf。
    回 {pages_total, pages_ocrd, pages_skipped, words_inserted, llm_used}。
    """
    doc = fitz.open(str(src_pdf))
    pages_total = doc.page_count
    pages_ocrd = 0
    pages_skipped = 0
    words_inserted = 0
    llm_used = False

    try:
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for pno in range(pages_total):
            page = doc[pno]
            if progress_cb:
                progress_cb(pno + 1, pages_total, f"OCR 頁 {pno+1}/{pages_total}")
            if skip_pages_with_text and page_has_text_layer(page):
                pages_skipped += 1
                continue
            try:
                pix = page.get_pixmap(matrix=mat)
                png = pix.tobytes("png")
            except Exception as e:
                log.warning("render page %d failed: %s", pno, e)
                continue
            words = _tesseract_image_to_data(png, langs)
            if not words:
                continue
            # LLM 後處理 — 把 words 串成文字，送 LLM 校正，再用同樣 bbox
            # 對應回去（粗略對應：以 word 順序 1-to-1，若校正後 word 數變
            # 就退回原文）
            if llm_postprocess:
                try:
                    raw_text = " ".join(w["text"] for w in words)
                    cleaned = llm_postprocess(raw_text)
                    if cleaned and cleaned.strip():
                        cleaned_words = cleaned.split()
                        if len(cleaned_words) == len(words):
                            for i, cw in enumerate(cleaned_words):
                                words[i]["text"] = cw
                            llm_used = True
                except Exception as e:
                    log.warning("LLM postprocess failed for page %d: %s", pno, e)
            n = add_text_layer_to_page(page, words, dpi=dpi)
            words_inserted += n
            pages_ocrd += 1

        dst_pdf.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(dst_pdf), garbage=3, deflate=True)
    finally:
        doc.close()

    return {
        "pages_total": pages_total,
        "pages_ocrd": pages_ocrd,
        "pages_skipped": pages_skipped,
        "words_inserted": words_inserted,
        "llm_used": llm_used,
    }


def is_tesseract_available() -> bool:
    import shutil
    try:
        from app.core.sys_deps import configure_pytesseract
        if configure_pytesseract():
            return True
    except Exception:
        pass
    return bool(shutil.which("tesseract"))


def get_active_langs(wanted: str = DEFAULT_LANGS) -> str:
    """過濾掉沒裝的語言。"""
    try:
        from app.core.sys_deps import configure_pytesseract
        configure_pytesseract()
        import pytesseract
        installed = set(pytesseract.get_languages(config="") or [])
    except Exception:
        return wanted
    parts = [p for p in wanted.split("+") if p in installed]
    return "+".join(parts) if parts else "eng"

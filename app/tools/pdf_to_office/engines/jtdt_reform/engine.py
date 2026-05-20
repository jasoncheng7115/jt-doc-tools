"""jtdt-reform engine 主入口。

流程：
  PDF → PDFTruth (extract) → DocumentModel (group) → docx (build)
       → PyMuPDF.page.get_links() 取超連結
"""
from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

from ...pdf_truth import extract_pdf_truth
from .docx_builder import build_docx
from .odt_builder import build_odt
from .paragraph_grouper import build_document_model

log = logging.getLogger(__name__)


def _extract_links(pdf_path: Path) -> dict[int, list]:
    """page_num → [{uri, bbox}, ...]"""
    out: dict[int, list] = {}
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        log.warning("open pdf for links failed: %s", e)
        return out
    try:
        for pno in range(doc.page_count):
            try:
                page = doc.load_page(pno)
                links = page.get_links() or []
                items: list[dict] = []
                for link in links:
                    uri = link.get("uri") or link.get("file") or ""
                    rect = link.get("from")
                    if not uri or rect is None:
                        continue
                    items.append({
                        "uri": uri,
                        "bbox": (float(rect.x0), float(rect.y0),
                                 float(rect.x1), float(rect.y1)),
                    })
                if items:
                    out[pno] = items
            except Exception as e:
                log.debug("page %d get_links failed: %s", pno, e)
    finally:
        doc.close()
    return out


def convert_via_jtdt_reform(pdf_path: Path, docx_path: Path) -> dict:
    """轉 PDF → docx，用 jtdt-reform engine（不靠 pdf2docx）。

    回 stats dict：
    {
      "ok": bool,
      "pages_converted": int,
      "tables_built": int,
      "free_paragraphs": int,
      "images": int,
      "hyperlinks": int,
      "engine": "jtdt-reform",
      "error": str (only on failure)
    }
    """
    pdf_path = Path(pdf_path)
    docx_path = Path(docx_path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    try:
        truth = extract_pdf_truth(pdf_path)
    except Exception as e:
        log.exception("PDFTruth extraction failed")
        return {"ok": False, "engine": "jtdt-reform",
                "error": f"pdf_truth: {e}", "pages_converted": 0}
    try:
        doc_model = build_document_model(truth)
    except Exception as e:
        log.exception("build_document_model failed")
        return {"ok": False, "engine": "jtdt-reform",
                "error": f"group: {e}", "pages_converted": 0}
    page_links = _extract_links(pdf_path)
    try:
        stats = build_docx(doc_model, pdf_path, docx_path,
                            page_links_by_page=page_links)
    except Exception as e:
        log.exception("build_docx failed")
        return {"ok": False, "engine": "jtdt-reform",
                "error": f"build: {e}", "pages_converted": 0}
    stats["engine"] = "jtdt-reform"
    stats["pages_converted"] = stats.get("pages", 0)
    return stats


def convert_via_jtdt_reform_to_odt(pdf_path: Path, odt_path: Path) -> dict:
    """轉 PDF → ODT，用 jtdt-reform engine。

    ODT 是 LibreOffice / OxOffice native format，渲染 100% 確定，沒 OOXML quirks。
    這是 v1.8.82+ 主路徑，比 convert_via_jtdt_reform (docx-direct) 視覺更穩定。
    """
    pdf_path = Path(pdf_path)
    odt_path = Path(odt_path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    try:
        truth = extract_pdf_truth(pdf_path)
    except Exception as e:
        log.exception("PDFTruth extraction failed")
        return {"ok": False, "engine": "jtdt-reform-odt",
                "error": f"pdf_truth: {e}", "pages_converted": 0}
    try:
        doc_model = build_document_model(truth)
    except Exception as e:
        log.exception("build_document_model failed")
        return {"ok": False, "engine": "jtdt-reform-odt",
                "error": f"group: {e}", "pages_converted": 0}
    page_links = _extract_links(pdf_path)
    try:
        stats = build_odt(doc_model, pdf_path, odt_path,
                            page_links_by_page=page_links)
    except Exception as e:
        log.exception("build_odt failed")
        return {"ok": False, "engine": "jtdt-reform-odt",
                "error": f"build: {e}", "pages_converted": 0}
    stats["engine"] = "jtdt-reform-odt"
    stats["pages_converted"] = stats.get("pages", 0)
    return stats

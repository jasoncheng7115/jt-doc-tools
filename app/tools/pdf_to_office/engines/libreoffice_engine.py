"""LibreOffice / OxOffice 後備引擎 — 當 pdf2docx 失敗時用。

soffice 的 PDF→docx 比 pdf2docx 慢且輸出更糟（會包成文字方塊），但能保住「至少
有東西」的底線。也提供 docx→odt 轉換給 ODT 輸出格式用。
"""
from __future__ import annotations

import logging
from pathlib import Path

from ....core import office_convert

log = logging.getLogger(__name__)


def convert_via_libreoffice(pdf_path: Path, docx_path: Path, timeout: float = 120.0) -> dict:
    """soffice --convert-to docx <pdf> — fallback only。"""
    pdf_path = Path(pdf_path)
    docx_path = Path(docx_path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    docx_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        office_convert.convert_to_docx(pdf_path, docx_path, timeout=timeout)
        return {"ok": True, "pages_converted": 0, "error": ""}  # 頁數 unknown
    except Exception as e:
        log.exception("libreoffice fallback failed")
        return {"ok": False, "pages_converted": 0, "error": str(e)}


def docx_to_odt(docx_path: Path, odt_path: Path, timeout: float = 60.0) -> dict:
    """docx → odt 轉換（用 soffice writer8）。"""
    docx_path = Path(docx_path)
    odt_path = Path(odt_path)
    if not docx_path.exists():
        raise FileNotFoundError(str(docx_path))
    try:
        office_convert.convert_to_odt(docx_path, odt_path, timeout=timeout)
        return {"ok": True, "error": ""}
    except Exception as e:
        log.exception("docx_to_odt failed")
        return {"ok": False, "error": str(e)}

"""pdf2docx engine wrapper — 鎖死 0.5.13。

pdf2docx 上游 (Artifex) 2026 已停止維護，授權轉 MIT。我們鎖版 + 必要時 fork。
此模組職責純粹：餵 PDF → 吐 docx，過程中的 log 收進來，不做後處理（那是 postprocess
的事）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from pdf2docx import Converter

log = logging.getLogger(__name__)


def convert_via_pdf2docx(
    pdf_path: Path,
    docx_path: Path,
    start: int = 0,
    end: int | None = None,
    pages: list[int] | None = None,
) -> dict:
    """轉 PDF → docx。

    Args:
        pdf_path: 來源 PDF
        docx_path: 目標 docx 路徑
        start: 起始頁 (0-based, inclusive)
        end: 結束頁 (0-based, exclusive)，None = 到最後
        pages: 指定頁清單（與 start/end 互斥）

    Returns:
        {"ok": bool, "pages_converted": int, "error": str}

    Raises:
        FileNotFoundError: pdf_path 不存在
    """
    pdf_path = Path(pdf_path)
    docx_path = Path(docx_path)
    if not pdf_path.exists():
        raise FileNotFoundError(str(pdf_path))
    docx_path.parent.mkdir(parents=True, exist_ok=True)

    cv = Converter(str(pdf_path))
    try:
        kwargs: dict = {}
        if pages is not None:
            kwargs["pages"] = pages
        else:
            kwargs["start"] = start
            if end is not None:
                kwargs["end"] = end
        cv.convert(str(docx_path), **kwargs)
        # 估算實際轉換頁數（pdf2docx 沒提供 attr 直接拿，從 fitz doc 拿）
        if pages is not None:
            pages_done = len(pages)
        else:
            try:
                total = cv.fitz_doc.page_count
            except Exception:
                total = 0
            pages_done = max(0, (end or total) - start)
        return {"ok": True, "pages_converted": pages_done, "error": ""}
    except Exception as e:
        log.exception("pdf2docx convert failed")
        return {"ok": False, "pages_converted": 0, "error": str(e)}
    finally:
        try:
            cv.close()
        except Exception:
            pass

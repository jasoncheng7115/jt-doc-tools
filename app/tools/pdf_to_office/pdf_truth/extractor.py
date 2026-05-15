"""PDF 真值抽取器 — 用 PyMuPDF 把原 PDF 解成 PDFTruth 結構。

設計原則：
- 失敗安全：個別 page / block 解析錯誤不影響整體；errors 進 log，回傳 best-effort 結果
- 不修改原 PDF
- 不假設字型可信（CMap 異常會在 has_bad_cmap 標記）
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF

from .font_inspector import inspect_fonts
from .models import (
    BBox,
    PDFBlock,
    PDFChar,
    PDFDrawing,
    PDFImage,
    PDFLine,
    PDFPage,
    PDFTruth,
)

log = logging.getLogger(__name__)

# 掃描頁判定：整頁文字 < 此字數視為掃描檔（沒文字層）
SCANNED_PAGE_TEXT_THRESHOLD = 50
# CMap 異常判定：PUA 字元比例 > 此值視為 ToUnicode 缺失
BAD_CMAP_PUA_RATIO_THRESHOLD = 0.2

_PUA_RE = re.compile(r"[-]")  # Private Use Area
_CJK_RE = re.compile(r"[㐀-鿿가-힯぀-ヿ]")
_HANT_HINT = re.compile(r"[ㄅ-ㄯ]|的|是|不|為|這|個")  # 注音 + 常見繁中字
_HANS_HINT = re.compile(r"[这为么会个时间]")
_JA_HINT = re.compile(r"[぀-ゟ゠-ヿ]")


def _color_int_to_hex(c) -> str:
    """PyMuPDF span color 是 int (0xRRGGBB) — 轉 #RRGGBB hex string。"""
    if c is None:
        return "#000000"
    try:
        v = int(c)
        return "#{:06x}".format(v & 0xFFFFFF)
    except (TypeError, ValueError):
        return "#000000"


def _font_flags_to_bold_italic(flags: int) -> tuple[bool, bool]:
    """PyMuPDF span flags bitfield: bit 4 = bold, bit 1 = italic."""
    return bool(flags & (1 << 4)), bool(flags & (1 << 1))


def _dominant(items: list[tuple[str, float, float]]) -> tuple[str, float]:
    """從 (font_name, size, weight) 清單算最常用的字型 + 字級。weight 用字元數加權。"""
    if not items:
        return "", 0.0
    fc: Counter = Counter()
    sc: Counter = Counter()
    for name, size, w in items:
        fc[name] += w
        sc[round(size, 1)] += w
    return fc.most_common(1)[0][0], float(sc.most_common(1)[0][0])


def _extract_chars_from_span(span: dict, page_num: int) -> list[PDFChar]:
    """span = PyMuPDF dict text 的最小單位。chars 在 span['text'] 裡是整個字串，
    無單字元位置；要 per-char 座標得用 page.get_text('rawdict')。我們先用 span 級
    粗略當每個字元，足夠 fixer 使用。"""
    text = span.get("text") or ""
    bbox = span.get("bbox") or (0, 0, 0, 0)
    font_name = (span.get("font") or "").lstrip("+")
    font_size = float(span.get("size") or 0.0)
    flags = int(span.get("flags") or 0)
    bold, italic = _font_flags_to_bold_italic(flags)
    color = _color_int_to_hex(span.get("color"))
    out: list[PDFChar] = []
    if not text:
        return out
    # 把 span bbox 平均分給每個字元當粗略 x（PDF 沒給 per-char 我們無從精確）
    x0, y0, x1, y1 = bbox
    n = len(text)
    cw = (x1 - x0) / max(1, n)
    h = y1 - y0
    for i, ch in enumerate(text):
        out.append(PDFChar(
            char=ch,
            x=x0 + i * cw,
            y=y0,
            width=cw,
            height=h,
            font_name=font_name,
            font_size=font_size,
            is_bold=bold,
            is_italic=italic,
            color=color,
        ))
    return out


def _dedup_consecutive_lines(lines: list[PDFLine]) -> list[PDFLine]:
    """連續完全相同文字的 PDFLine 去重（PDF 用 stroke+fill 多層渲染粗體效果時，
    PyMuPDF 會抽出重複行，例如「彰化縣\\n彰化縣\\n彰化縣\\n彰化縣」— pdf2docx 把
    重複內容塞進 docx 看起來就是字疊字 / 重複段落）。

    判定：相鄰兩 line 的 text 完全相同 → 後面的合併到前面（保留前者 bbox/font）。
    """
    if not lines:
        return lines
    out: list[PDFLine] = [lines[0]]
    for ln in lines[1:]:
        if ln.text and out[-1].text == ln.text:
            # 跳過重複行（保留前者）
            continue
        out.append(ln)
    return out


def _extract_page(page) -> PDFPage:
    page_num = page.number
    rect = page.rect
    page_width = float(rect.width)
    page_height = float(rect.height)

    blocks_out: list[PDFBlock] = []
    images_out: list[PDFImage] = []
    drawings_out: list[PDFDrawing] = []

    # ------ text blocks -------
    try:
        d = page.get_text("dict")
    except Exception as e:
        log.warning("page %d get_text dict failed: %s", page_num, e)
        d = {"blocks": []}

    total_text_len = 0
    pua_count = 0
    total_char_count = 0
    for blk in d.get("blocks") or []:
        btype = blk.get("type", 0)  # 0=text, 1=image
        bbox = tuple(blk.get("bbox") or (0, 0, 0, 0))
        if btype == 0:
            # text block
            lines_out: list[PDFLine] = []
            block_font_items: list[tuple[str, float, float]] = []
            for line in blk.get("lines") or []:
                chars_in_line: list[PDFChar] = []
                line_text_parts: list[str] = []
                line_bbox = tuple(line.get("bbox") or (0, 0, 0, 0))
                line_font_items: list[tuple[str, float, float]] = []
                for span in line.get("spans") or []:
                    chs = _extract_chars_from_span(span, page_num)
                    chars_in_line.extend(chs)
                    txt = span.get("text") or ""
                    line_text_parts.append(txt)
                    if txt:
                        weight = len(txt)
                        item = (
                            (span.get("font") or "").lstrip("+"),
                            float(span.get("size") or 0.0),
                            weight,
                        )
                        line_font_items.append(item)
                        block_font_items.append(item)
                    pua_count += len(_PUA_RE.findall(txt))
                    total_char_count += len(txt)
                line_text = "".join(line_text_parts)
                if not line_text and not chars_in_line:
                    continue
                dom_font, dom_size = _dominant(line_font_items)
                lines_out.append(PDFLine(
                    chars=chars_in_line,
                    bbox=line_bbox,
                    text=line_text,
                    dominant_font=dom_font,
                    dominant_size=dom_size,
                ))
                total_text_len += len(line_text)
            if lines_out:
                # 重複 line 去重（字疊字粗體效果常見）
                lines_out = _dedup_consecutive_lines(lines_out)
                dom_font, dom_size = _dominant(block_font_items)
                block_text = "\n".join(ln.text for ln in lines_out)
                blocks_out.append(PDFBlock(
                    lines=lines_out,
                    bbox=bbox,
                    text=block_text,
                    block_type="text",
                    page_num=page_num,
                    dominant_font=dom_font,
                    dominant_size=dom_size,
                ))
        elif btype == 1:
            # image block — 詳細圖片資料另外從 get_images() 取（拿 xref）
            blocks_out.append(PDFBlock(
                lines=[], bbox=bbox, text="", block_type="image", page_num=page_num,
            ))

    # ------ images（xref + perceptual hash） -------
    try:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                bbox_list = page.get_image_rects(xref)
                bbox = tuple(bbox_list[0]) if bbox_list else (0, 0, 0, 0)
            except Exception:
                bbox = (0, 0, 0, 0)
            try:
                pix = fitz.Pixmap(page.parent, xref)
                w, h = pix.width, pix.height
                # 用 SHA1 hash bytes 當 image_hash（perceptual hash 是 imagehash 套件
                # 才有，先用 content hash 替代 — fixer 可用 hash 配對相同內容圖片）
                img_hash = hashlib.sha1(pix.tobytes(), usedforsecurity=False).hexdigest()[:16]
                pix = None
            except Exception:
                w, h, img_hash = 0, 0, ""
            images_out.append(PDFImage(
                bbox=bbox,
                page_num=page_num,
                xref=xref,
                width=w,
                height=h,
                image_hash=img_hash,
            ))
    except Exception as e:
        log.warning("page %d get_images failed: %s", page_num, e)

    # ------ drawings（線條 / 矩形 → 表格偵測） -------
    try:
        for drw in page.get_drawings():
            # drw['type'] = 'f' (fill), 's' (stroke), 'fs'。我們只關心 path 種類
            items = drw.get("items") or []
            for it in items:
                kind = it[0] if it else ""
                if kind == "l":  # line: ('l', p0, p1)
                    p0, p1 = it[1], it[2]
                    bbox = (
                        min(p0.x, p1.x), min(p0.y, p1.y),
                        max(p0.x, p1.x), max(p0.y, p1.y),
                    )
                    drawings_out.append(PDFDrawing(
                        type="line", bbox=bbox, page_num=page_num,
                        stroke_width=float(drw.get("width") or 0),
                    ))
                elif kind == "re":  # rect: ('re', rect)
                    r = it[1]
                    bbox = (r.x0, r.y0, r.x1, r.y1)
                    drawings_out.append(PDFDrawing(
                        type="rect", bbox=bbox, page_num=page_num,
                        stroke_width=float(drw.get("width") or 0),
                    ))
                elif kind == "c":  # bezier curve
                    pass  # 曲線通常不是表格線，跳過
    except Exception as e:
        log.debug("page %d get_drawings failed: %s", page_num, e)

    # ------ 邊距估算（依文字 block 集中分佈） -------
    if blocks_out:
        text_bboxes = [b.bbox for b in blocks_out if b.block_type == "text"]
        if text_bboxes:
            min_x = min(bb[0] for bb in text_bboxes)
            min_y = min(bb[1] for bb in text_bboxes)
            max_x = max(bb[2] for bb in text_bboxes)
            max_y = max(bb[3] for bb in text_bboxes)
            margin_left = max(0.0, min_x)
            margin_top = max(0.0, min_y)
            margin_right = max(0.0, page_width - max_x)
            margin_bottom = max(0.0, page_height - max_y)
        else:
            margin_left = margin_top = margin_right = margin_bottom = 0.0
    else:
        margin_left = margin_top = margin_right = margin_bottom = 0.0

    # ------ 掃描頁 / CMap 異常標記 -------
    is_scanned = total_text_len < SCANNED_PAGE_TEXT_THRESHOLD and bool(images_out)
    has_bad_cmap = (
        total_char_count > 0
        and (pua_count / total_char_count) > BAD_CMAP_PUA_RATIO_THRESHOLD
    )

    return PDFPage(
        page_num=page_num,
        width=page_width,
        height=page_height,
        margin_top=margin_top,
        margin_bottom=margin_bottom,
        margin_left=margin_left,
        margin_right=margin_right,
        blocks=blocks_out,
        images=images_out,
        drawings=drawings_out,
        is_scanned=is_scanned,
        has_bad_cmap=has_bad_cmap,
    )


def _guess_language(pages: list[PDFPage]) -> str:
    """從整體字元分佈猜語言。粗略但夠 fixer 用。"""
    sample = "".join(b.text for p in pages for b in p.blocks if b.block_type == "text")[:5000]
    if not sample:
        return "unknown"
    cjk = len(_CJK_RE.findall(sample))
    ja = len(_JA_HINT.findall(sample))
    hant = len(_HANT_HINT.findall(sample))
    hans = len(_HANS_HINT.findall(sample))
    total = max(1, len(sample))
    if ja > total * 0.05 and cjk > total * 0.1:
        return "ja"
    if cjk > total * 0.1:
        if hant >= hans:
            return "zh-Hant"
        return "zh-Hans"
    return "en"


def _body_font_stats(pages: list[PDFPage]) -> tuple[str, float]:
    """全文最常用的字型 + 字級（內文基準）。掃描頁直接跳過。"""
    items: list[tuple[str, float, float]] = []
    for p in pages:
        if p.is_scanned:
            continue
        for b in p.blocks:
            if b.block_type != "text":
                continue
            for ln in b.lines:
                if ln.dominant_font and ln.dominant_size > 0:
                    items.append((ln.dominant_font, ln.dominant_size, len(ln.text) or 1))
    return _dominant(items)


def extract_pdf_truth(pdf_path: Path | str) -> PDFTruth:
    """主入口：解析 PDF → PDFTruth。失敗回傳空 PDFTruth + log warning。"""
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    has_encryption = bool(doc.is_encrypted)
    if has_encryption:
        # 嘗試空密碼解鎖（owner password 通常允許讀取）
        try:
            doc.authenticate("")
        except Exception:
            pass

    pages: list[PDFPage] = []
    for pno in range(doc.page_count):
        try:
            page = doc.load_page(pno)
            pages.append(_extract_page(page))
        except Exception as e:
            log.warning("extract page %d failed: %s", pno, e)
            continue

    fonts = inspect_fonts(doc)
    body_font, body_size = _body_font_stats(pages)
    lang = _guess_language(pages)
    has_scanned = any(p.is_scanned for p in pages)
    total = doc.page_count
    doc.close()

    return PDFTruth(
        pages=pages,
        fonts=fonts,
        total_pages=total,
        has_encryption=has_encryption,
        has_scanned_pages=has_scanned,
        language_guess=lang,
        body_font_size=body_size,
        body_font_name=body_font,
    )

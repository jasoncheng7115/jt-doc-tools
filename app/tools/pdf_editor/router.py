"""PDF Editor router — Phase 1 endpoints.

Routes:
  GET  /                       → editor page
  POST /load                   → upload PDF, return upload_id + pages info
  GET  /preview/{filename}     → serve rendered page PNGs
  GET  /file/{upload_id}       → serve original PDF (for PDF.js if needed)
  POST /save                   → accept JSON model, burn into new PDF
  GET  /download/{upload_id}   → download the saved PDF
"""
from __future__ import annotations

import io
import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ...config import settings
from ...core import pdf_preview
from ...core.asset_manager import asset_manager


router = APIRouter()


def _extract_image_with_alpha(doc: "fitz.Document", xref: int,
                              out_path: Path) -> None:
    """Extract a PDF image to PNG preserving alpha (transparent areas).

    The naive ``fitz.Pixmap(doc, xref).save(...)`` path only grabs the base
    RGB stream — for PDFs that store transparency as a separate SMask
    xref (the common case for embedded PNG-with-alpha) the alpha is lost
    and transparent pixels become black. This helper handles three cases:

      1) extract_image returns PNG bytes already containing alpha → write
         the original bytes directly. Bit-perfect, fastest.
      2) extract_image flags an SMask xref → load both pixmaps and combine
         into an RGBA pixmap, then save as PNG.
      3) Fallback: regular Pixmap(doc, xref) — same as before, may end up
         opaque but won't crash.
    """
    info = doc.extract_image(xref)
    ext = (info.get("ext") or "").lower()
    raw = info.get("image") or b""
    smask_xref = int(info.get("smask") or 0)

    # Case 1: original is PNG and has its own alpha → just save raw bytes.
    if ext == "png" and raw:
        # PNG can be RGB-only or RGBA; either way reusing the original
        # bytes preserves whatever alpha (if any) was there.
        try:
            from PIL import Image as _PILImage
            import io as _io
            with _PILImage.open(_io.BytesIO(raw)) as im:
                # If PNG already has alpha, just write it; otherwise we
                # might still need to merge SMask below.
                if im.mode in ("RGBA", "LA") or "A" in im.getbands():
                    out_path.write_bytes(raw)
                    return
        except Exception:
            pass

    # Case 2: separate SMask → combine base + mask into RGBA pixmap.
    if smask_xref:
        try:
            base = fitz.Pixmap(doc, xref)
            mask = fitz.Pixmap(doc, smask_xref)
            # PyMuPDF: combining base RGB pixmap with a 1-channel mask
            # yields an RGBA pixmap.
            combined = fitz.Pixmap(base, mask)
            combined.save(str(out_path))
            return
        except Exception:
            pass

    # Case 3: fallback. May be opaque but at least non-empty.
    pix = fitz.Pixmap(doc, xref)
    if pix.n > 4:  # CMYK / DeviceN → convert to RGB
        pix = fitz.Pixmap(fitz.csRGB, pix)
    pix.save(str(out_path))


# Western fonts — when a span uses one of these, OCR with eng-only is far
# more accurate than chi_tra+eng (the latter sometimes hallucinates CJK
# strokes into ASCII letters: "Proxmox" → "ProXimoxX").
_WESTERN_FONT_HINTS = (
    "helvetica", "arial", "times", "courier", "verdana", "tahoma",
    "georgia", "calibri", "cambria", "consolas", "roboto", "opensans",
    "open sans", "sourcecode", "source code", "source sans", "sourcesans",
    "lato", "montserrat", "ubuntu", "dejavu", "liberation", "free",
    "garamond", "palatino", "myriad",
)
_CJK_FONT_HINTS = (
    "pingfang", "songti", "heiti", "kaiti", "stsong", "stkaiti",
    "noto sans cjk", "noto serif cjk", "notosanscjk", "notoserifcjk",
    "source han", "sourcehan", "微軟", "細明", "正黑", "明體",
    "ming", "jhenghei", "jheng hei", "yahei", "simsun", "simhei",
    "tc", "tw", "hk", "sc", "cn", "jp", "kr",  # subset markers in subset names
)


def _ocr_lang_for_font(span_font: str) -> str:
    """Pick OCR language(s) based on the span's font name. Western fonts get
    eng-only (much more accurate); CJK fonts get chi_tra+eng (need both)."""
    name = (span_font or "").lower()
    if not name:
        return "chi_tra+eng"
    # CJK markers take priority — many subset fonts have generic-sounding
    # base names but the +TC / +SC suffix gives them away.
    for h in _CJK_FONT_HINTS:
        if h in name:
            return "chi_tra+eng"
    for h in _WESTERN_FONT_HINTS:
        if h in name:
            return "eng"
    return "chi_tra+eng"


def _ocr_bbox(page: "fitz.Page", bbox, lang: str = "chi_tra+eng") -> str:
    """OCR a single bbox region on a PDF page, return cleaned text.

    Used to recover real text from PDFs whose Identity-H subset fonts have
    a missing or identity ToUnicode CMap (so PyMuPDF text extraction returns
    GIDs as Unicode garbage). Renders the region at high DPI and asks
    tesseract to read it.

    Returns empty string when tesseract / pytesseract is unavailable, OCR
    crashes, or the result is obviously empty / noise.
    """
    try:
        import pytesseract
        from PIL import Image
        # 防 user 把 tesseract 裝在預設位置但沒加 PATH（Win11 客戶常見，issue #4）
        from ...core.sys_deps import configure_pytesseract
        configure_pytesseract()
    except ImportError:
        return ""
    try:
        x0, y0, x1, y1 = [float(v) for v in bbox]
        bw = max(x1 - x0, 1.0)
        bh = max(y1 - y0, 1.0)
        # Padding strategy:
        #  - Horizontal: tiny (2pt) — bigger pad caught adjacent spans on the
        #    same line (e.g. "Proxmox VE" + "網路基本設定" sit next to each
        #    other and got merged into "VE 網路基本設定").
        #  - Vertical: small fraction (10% or 2pt min) — just enough for
        #    descenders / accents but not enough to grab the underline below
        #    a section heading (which OCR happily reads as "一").
        pad_x = 2.0
        pad_y = max(bh * 0.10, 2.0)
        page_rect = page.rect
        rect = fitz.Rect(
            max(page_rect.x0, x0 - pad_x),
            max(page_rect.y0, y0 - pad_y),
            min(page_rect.x1, x1 + pad_x),
            min(page_rect.y1, y1 + pad_y),
        )
        # 400 DPI for short titles (more pixels per glyph helps); 300 otherwise.
        dpi = 400 if bh < 40 else 300
        matrix = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=matrix, clip=rect, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        # OCR for short CJK is finicky; right PSM varies by content. Try
        # several modes and pick the longest sane result. PSM 7=single text
        # line, 6=uniform block, 8=single word, 11=sparse text.
        aspect = bh / bw
        psms = ["7", "6", "8", "11"] if aspect <= 0.5 else ["6", "7", "11"]
        text = ""
        for psm in psms:
            try:
                cand = pytesseract.image_to_string(
                    img, lang=lang, config=f"--psm {psm}",
                )
            except Exception:
                continue
            cand = (cand or "").strip()
            if len(cand) > len(text):
                text = cand
        # Strip OCR-flavored line noise: stray standalone punctuation, NBSP,
        # zero-width chars, control chars.
        text = "".join(
            ch for ch in text
            if ord(ch) >= 0x20 or ch in ("\n", "\t")
        )
        text = text.replace(" ", " ").strip()
        return text
    except Exception:
        return ""


# Cache: (doc.id, font_name_or_xref) → bool. PDF page font lookup is not free.
_TOUNICODE_CACHE: dict[tuple[int, str], bool] = {}


def _font_has_tounicode(doc: "fitz.Document", page: "fitz.Page",
                        span_font: str) -> bool:
    """Check whether the font used by ``span`` carries a /ToUnicode CMap.

    PDF fonts that embed a subset (Identity-H without /ToUnicode) render
    correctly visually but text extraction returns raw glyph IDs reinterpreted
    as Unicode codepoints — usually surfacing as obscure CJK characters
    (e.g. 登入系統 → 猞狝狘). Without ToUnicode we cannot recover the true
    text, so callers should refuse the extraction and prompt the user to
    redact + re-type instead.
    """
    if not span_font:
        return True  # nothing to check, be permissive
    cache_key = (id(doc), str(page.number) + ":" + span_font)
    cached = _TOUNICODE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    result = True  # default permissive — only flip to False on confirmed miss
    try:
        for finfo in page.get_fonts(full=True):
            # finfo: (xref, ext, type, basefont, name, encoding[, referencer])
            xref = finfo[0]
            basefont = finfo[3] if len(finfo) > 3 else ""
            name = finfo[4] if len(finfo) > 4 else ""
            # PyMuPDF strips the 6-letter subset prefix ("ABCDEF+") from the
            # span's font name in some versions but keeps it in others. Match
            # by suffix to cover both.
            span_norm = span_font.split("+")[-1]
            cand_norms = [
                str(name).split("+")[-1] if name else "",
                str(basefont).split("+")[-1] if basefont else "",
            ]
            if span_norm not in cand_norms and span_font not in (str(name), str(basefont)):
                continue
            try:
                obj_str = doc.xref_object(xref, compressed=False) or ""
            except Exception:
                obj_str = ""
            result = "/ToUnicode" in obj_str
            break
    except Exception:
        result = True  # on error, don't block the user
    _TOUNICODE_CACHE[cache_key] = result
    return result


# Common Taiwan-繁中 characters — if a CJK string contains zero of these,
# it is almost certainly garbled extraction (real Taiwan PDFs almost always
# include common particles like 的/是/在/了 or function words).
_COMMON_TC = set(
    "的是在了一不和有也與及或但若如那這就因為所以從為對於到把被讓以由"
    "於還只都我你他她我們他們你們什麼怎麼這個那個這些那些"
    "登入系統設定檔案資料管理服務網路連線安裝啟動關閉重設密碼帳號"
    "使用者員工客戶廠商日期時間年月日今明昨前後上下左右中間第頁本"
    "如何說明請按確定取消儲存匯出匯入下載上傳新增修改刪除查詢搜尋"
    "顯示隱藏離開結束完成成功失敗錯誤警告通知訊息提示輸入輸出選擇"
    "選項範圍位置數量大小高度寬度長度公司部門組織單位負責人員會員"
    "權限管理員一般個人團體大型中型小型快速分析統計報告紀錄歷史最新"
    "中文英文程式語言開源企業免費商業軟體網站手機電腦平板問題答案"
    "建議考慮注意應該必須可能繁體簡體台灣中華民國"
    "虛擬化容器實體機器主機網域區網路由備份還原快照映像光碟硬碟"
    "記憶體處理器執行運作功能操作畫面文字段落章節範例例如說明文件"
)


def _looks_garbled(text: str) -> bool:
    """Heuristic: detect text-extraction garbage from PDFs whose Identity-H
    fonts have a broken/identity ToUnicode CMap (so /ToUnicode existence check
    in :func:`_font_has_tounicode` returns True but content is still GIDs).

    Two signals, either suffices:
      a) Contains symbols/scripts that should not appear in normal Taiwan text
         (math operators, technical symbols, lone Hangul jamo, dingbats, etc.).
         A single occurrence in a short string is highly suspicious.
      b) Has multiple CJK characters but ZERO common Taiwan characters — real
         Taiwan PDFs basically always hit at least one of 的/是/在/了/一/...
    """
    if not text:
        return False

    suspicious = 0
    cjk_count = 0
    common_hits = 0
    for ch in text:
        cp = ord(ch)
        if ch in _COMMON_TC:
            common_hits += 1
        if 0x3400 <= cp <= 0x9FFF or 0x20000 <= cp <= 0x2FFFF:
            cjk_count += 1
            # CJK Ext A/B is rare; if a span is mostly Ext A → suspicious
            if 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x2FFFF:
                suspicious += 1
            continue
        # Non-CJK suspicious blocks
        if 0x2200 <= cp <= 0x22FF:        # Mathematical Operators (⊕, ∂, ...)
            suspicious += 1
        elif 0x2300 <= cp <= 0x23FF:      # Miscellaneous Technical
            suspicious += 1
        elif 0x2500 <= cp <= 0x257F:      # Box Drawing
            suspicious += 1
        elif 0x25A0 <= cp <= 0x25FF:      # Geometric Shapes
            suspicious += 1
        elif 0x2600 <= cp <= 0x26FF:      # Misc Symbols
            suspicious += 1
        elif 0x2700 <= cp <= 0x27BF:      # Dingbats
            suspicious += 1
        elif 0x3100 <= cp <= 0x312F:      # Bopomofo (注音) — lone bopomofo in body text is a smell
            suspicious += 1
        elif 0x3130 <= cp <= 0x318F:      # Hangul Compatibility Jamo (ㄱ etc)
            suspicious += 1
        elif 0xE000 <= cp <= 0xF8FF:      # Private Use Area
            suspicious += 1

    # Signal a) suspicious symbols mixed in
    if suspicious >= 1 and (cjk_count + suspicious) <= 12:
        return True
    if suspicious / max(cjk_count + suspicious, 1) >= 0.25:
        return True
    # Signal b) multiple CJK but no common chars → garbled
    if cjk_count >= 2 and common_hits == 0:
        return True
    # Signal c) — long run of identical letters/digits (8+) is essentially
    # never real text. 客戶踩過：TOC 的 leader dots「........」用 Identity-H
    # subset font，glyph index 0x65 對應「.」glyph 但 ToUnicode 把它 map 回
    # codepoint 0x65 = 'e'，extract 變「eeeeeeee...」。閾值 8+ 避免誤判
    # 編號/序號的 5-7 連碼。Real text 連 8 個同字母不存在。
    import re as _re
    if _re.search(r"([A-Za-z0-9])\1{7,}", text):
        return True
    return False


@router.get("/fonts")
async def list_fonts():
    """Return the font catalog for the text-tool dropdown."""
    from ...core import font_catalog
    fonts = font_catalog.list_fonts()
    # Group by category for nicer UI
    groups: dict[str, list] = {}
    for f in fonts:
        groups.setdefault(f["category"], []).append({
            "id": f["id"],
            "label": f["label"],
            "family": f["family"],
            "variant": f.get("variant", ""),
            "cjk": f.get("cjk"),
            "style": f.get("style"),
        })
    group_titles = {
        "custom": "自訂上傳字型",
        "taiwan": "台灣系統字型",
        "free-cjk": "開源 CJK 字型",
        "cjk": "其他 CJK 字型",
        "latin": "西文開源字型",
        "pymupdf": "PyMuPDF 內建（最輕量、相容性最好）",
    }
    ordered = []
    # custom 排第一，讓 admin 上傳的公司字型最顯眼
    for key in ("custom", "taiwan", "free-cjk", "cjk", "latin", "pymupdf"):
        if key in groups:
            ordered.append({"key": key, "title": group_titles[key], "fonts": groups[key]})
    return {"groups": ordered, "total": len(fonts)}


@router.post("/detect-objects")
async def detect_objects(request: Request):
    """Click-hit test on an existing PDF page. Given (upload_id, page, x, y)
    in PDF points, find the topmost text span or image at that location.
    Returns {kind, bbox, ...} or {kind: null} when nothing hit.
    """
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    page_idx = int(body.get("page", 0))
    x = float(body.get("x", 0))
    y = float(body.get("y", 0))
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _work_dir() / f"pe_{upload_id}_src.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")

    with fitz.open(str(src)) as doc:
        if page_idx < 0 or page_idx >= doc.page_count:
            raise HTTPException(400, "page out of range")
        page = doc[page_idx]

        # 1) Text spans (most common hit for form editing)
        td = page.get_text("dict")
        for block in td.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    bx0, by0, bx1, by1 = span.get("bbox", (0, 0, 0, 0))
                    if bx0 <= x <= bx1 and by0 <= y <= by1:
                        col_int = int(span.get("color", 0) or 0)
                        r = (col_int >> 16) & 0xff
                        g = (col_int >> 8) & 0xff
                        b = col_int & 0xff
                        span_text = span.get("text", "")
                        span_font = span.get("font", "")
                        # Detect garbled extraction. Two checks:
                        #  1) Font lacks /ToUnicode CMap (truly broken).
                        #  2) Heuristic on the extracted text itself — catches
                        #     PDFs whose CMap exists but is identity (returns
                        #     GIDs as Unicode); this is the common case where
                        #     登入系統 → 翕⊕ㄱ 戔ㄱ.
                        unreliable = False
                        if span_text:
                            if not _font_has_tounicode(doc, page, span_font):
                                unreliable = True
                            elif _looks_garbled(span_text):
                                unreliable = True
                        # When extraction is unreliable, OCR the bbox region
                        # to recover real text. Only fall back to "ask user
                        # to retype" if OCR is unavailable or returns nothing.
                        ocr_text = ""
                        ocr_used = False
                        if unreliable:
                            ocr_lang = _ocr_lang_for_font(span_font)
                            ocr_text = _ocr_bbox(page, [bx0, by0, bx1, by1], lang=ocr_lang)
                            if ocr_text:
                                ocr_used = True
                        if ocr_used:
                            final_text = ocr_text
                        elif unreliable:
                            final_text = ""
                        else:
                            final_text = span_text
                        return {
                            "kind": "text",
                            "bbox": [bx0, by0, bx1, by1],
                            "text": final_text,
                            "font_size": float(span.get("size", 11)),
                            "color": f"#{r:02x}{g:02x}{b:02x}",
                            "font": span_font,
                            "extracted_text_unreliable": unreliable and not ocr_used,
                            "ocr_used": ocr_used,
                        }

        # 2) Images — get_image_info returns bboxes
        try:
            for info in page.get_image_info(xrefs=True):
                b = info.get("bbox")
                if not b:
                    continue
                bx0, by0, bx1, by1 = b
                if bx0 <= x <= bx1 and by0 <= y <= by1:
                    xref = info.get("xref") or 0
                    thumb_png = None
                    if xref:
                        # Export image as PNG under work_dir for the
                        # frontend to preview. Preserve alpha channel:
                        # PyMuPDF stores transparent PDF images as base
                        # RGB + a separate SMask xref (soft mask = alpha).
                        # Naive `fitz.Pixmap(doc, xref)` only grabs base →
                        # transparent pixels render BLACK. Fix:
                        #   1) prefer extract_image() raw bytes when ext is
                        #      png/jpeg-with-alpha (preserves original
                        #      encoding incl. alpha);
                        #   2) otherwise combine base pixmap + smask pixmap
                        #      into RGBA.
                        img_file = (_work_dir() /
                                    f"pe_{upload_id}_imgx{xref}_p{page_idx}.png")
                        if not img_file.exists():
                            try:
                                _extract_image_with_alpha(doc, xref, img_file)
                            except Exception:
                                img_file = None
                        if img_file and img_file.exists():
                            thumb_png = f"/tools/pdf-editor/preview/{img_file.name}"
                    return {
                        "kind": "image",
                        "bbox": [bx0, by0, bx1, by1],
                        "xref": xref,
                        "thumb_url": thumb_png,
                    }
        except Exception:
            pass

        # 3) AcroForm widgets — form fields (text input, checkbox,
        # combo, signature). Returns the field name + rect so the
        # frontend can mark them for deletion.
        try:
            for w in page.widgets() or []:
                r = w.rect
                if r is None:
                    continue
                bx0, by0, bx1, by1 = r.x0, r.y0, r.x1, r.y1
                if bx0 <= x <= bx1 and by0 <= y <= by1:
                    # Field type: text / check / combo / radio / signature
                    try:
                        field_type = w.field_type_string
                    except Exception:
                        field_type = str(getattr(w, "field_type", ""))
                    return {
                        "kind": "widget",
                        "bbox": [bx0, by0, bx1, by1],
                        "field_name": w.field_name or "",
                        "field_type": field_type,
                    }
        except Exception:
            pass

        # 4) Vector drawings — lines, rects, paths. Returns a small
        # bounding rect around the click so the frontend can show a
        # marker and let the user redact it.
        try:
            drawings = page.get_drawings() or []
            for dw in drawings:
                rect = dw.get("rect")
                if rect is None:
                    continue
                try:
                    bx0, by0, bx1, by1 = rect.x0, rect.y0, rect.x1, rect.y1
                except AttributeError:
                    bx0, by0, bx1, by1 = rect
                if bx0 <= x <= bx1 and by0 <= y <= by1:
                    # Skip huge full-page rectangles (page background).
                    pw, ph = page.rect.width, page.rect.height
                    if (bx1 - bx0) >= pw * 0.9 and (by1 - by0) >= ph * 0.9:
                        continue
                    return {
                        "kind": "drawing",
                        "bbox": [bx0, by0, bx1, by1],
                        "type": dw.get("type", ""),
                    }
        except Exception:
            pass

    return {"kind": None}


@router.get("/assets")
async def list_assets():
    """Return all stamp/signature/logo assets usable in the editor."""
    out = []
    for t in ("stamp", "signature", "logo"):
        for a in asset_manager.list(type=t):
            out.append({
                "id": a.id,
                "name": a.name,
                "type": a.type,
                "thumb_url": f"/admin/assets/{a.id}/thumb",
                "file_url": f"/admin/assets/{a.id}/file",
                "preset": {"width_mm": a.preset.width_mm, "height_mm": a.preset.height_mm},
            })
    return {"assets": out}


def _resolve_fonts_for_pref(
    font_pref: str, has_cjk: bool, bold: bool, italic: bool,
    page, cache: dict, pno: int,
) -> tuple[str, str]:
    """Return (cjk_font, ascii_font) fit for an insert_text call. Mirrors the
    save-path logic so the one-click "換字型" feature stays visually
    consistent with the editor's per-text font selection."""
    custom_font_name: str | None = None
    if font_pref.startswith("system:"):
        from ...core import font_catalog
        entry = font_catalog.resolve_font_id(font_pref)
        if entry and entry.get("path"):
            fkey = (pno, entry["path"], int(entry.get("idx") or 0))
            if fkey in cache:
                custom_font_name = cache[fkey]
            else:
                try:
                    reg_name = f"uf{len(cache)}"
                    page.insert_font(fontname=reg_name, fontfile=entry["path"])
                    cache[fkey] = reg_name
                    custom_font_name = reg_name
                except Exception:
                    custom_font_name = None
    if font_pref.startswith("custom:"):
        from ...core import font_catalog
        entry = font_catalog.resolve_font_id(font_pref)
        if entry and entry.get("path"):
            fkey = (pno, entry["path"], int(entry.get("idx") or 0))
            if fkey in cache:
                custom_font_name = cache[fkey]
            else:
                try:
                    reg_name = f"uc{len(cache)}"
                    page.insert_font(fontname=reg_name, fontfile=entry["path"])
                    cache[fkey] = reg_name
                    custom_font_name = reg_name
                except Exception:
                    custom_font_name = None

    if custom_font_name:
        return (custom_font_name, custom_font_name)
    # 把 china-* 內建升級為實際系統 CJK 字型（PyMuPDF 內建在 Linux render
    # 不出 serif/sans 區別，全部變厚實 sans — v1.4.77 修）
    def _u(builtin: str) -> str:
        return _upgrade_cjk_font(page, builtin, cache, pno)
    if font_pref in ("pymupdf:sans", "sans"):
        return (_u("china-ts") if has_cjk else _style_suffix("helv", bold, italic),
                _style_suffix("helv", bold, italic))
    if font_pref in ("pymupdf:serif", "serif"):
        return (_u("china-t"), _style_suffix("tiro", bold, italic))
    if font_pref in ("pymupdf:simplified", "simplified"):
        return (_u("china-ss"), _style_suffix("helv", bold, italic))
    if font_pref in ("pymupdf:helv", "helv"):
        return (_u("china-t"), _style_suffix("helv", bold, italic))
    if font_pref in ("pymupdf:tiro", "tiro"):
        return (_u("china-t"), _style_suffix("tiro", bold, italic))
    if font_pref in ("pymupdf:cour", "mono"):
        return (_u("china-t"), _style_suffix("cour", bold, italic))
    # "default" / "pymupdf:default"
    return (_u("china-t"), _style_suffix("helv", bold, italic))


def _replace_all_fonts_sync(src, upload_id: str, font_id: str):
    """Heavy CPU work for replace-all-fonts; called via asyncio.to_thread
    so the async event loop stays responsive."""
    replaced = 0
    font_cache: dict = {}
    doc = fitz.open(str(src))
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            spans: list[dict] = []
            for block in page.get_text("dict").get("blocks", []) or []:
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []) or []:
                    for sp in line.get("spans", []) or []:
                        txt = sp.get("text") or ""
                        if not txt.strip():
                            continue
                        # Skip garbled spans — Identity-H subset 字型 ToUnicode
                        # 壞掉時 PyMuPDF extract 會回 raw glyph code（例如
                        # 全是「eeeeeee」對應原本的 leader dots「........」）。
                        # 換字型若把這種 garbage 重新貼回去會看到一行 eeee
                        # 蓋住原本的 dots（v1.4.90 客戶踩到）。
                        font_name = sp.get("font") or ""
                        if not _font_has_tounicode(doc, page, font_name) \
                                or _looks_garbled(txt):
                            continue
                        spans.append(sp)
            if not spans:
                continue
            # Redact all existing text first.
            for sp in spans:
                # fill=None — 不畫白底覆蓋。否則底下原本是有色 cell（表頭灰
                # / 總計橘）會被白方塊蓋掉，視覺上文字變成「浮在白底上」。
                # 不畫覆蓋的話 redact 只會移除文字 content stream item，下方
                # 顏色矩形原樣保留。v1.4.78 修。
                page.add_redact_annot(fitz.Rect(*sp["bbox"]), fill=None)
            try:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except Exception:
                page.apply_redactions()
            # Re-insert with chosen font, preserving size + color + bold/italic
            for sp in spans:
                bx0, by0, bx1, by1 = sp["bbox"]
                text = sp.get("text") or ""
                size = float(sp.get("size", 11) or 11)
                col_int = int(sp.get("color", 0) or 0)
                r_c = ((col_int >> 16) & 0xff) / 255.0
                g_c = ((col_int >> 8) & 0xff) / 255.0
                b_c = (col_int & 0xff) / 255.0
                flags = int(sp.get("flags", 0) or 0)
                bold = bool(flags & 16)
                italic = bool(flags & 2)
                has_cjk = any("一" <= c <= "鿿" for c in text)
                cjk_f, ascii_f = _resolve_fonts_for_pref(
                    font_id, has_cjk, bold, italic, page, font_cache, pno,
                )
                # baseline: roughly bottom minus a small descender
                base_y = by1 - size * 0.18
                _insert_mixed_text(page, bx0, base_y, text,
                                   cjk_font=cjk_f, ascii_font=ascii_f,
                                   font_size=size, color=(r_c, g_c, b_c))
                replaced += 1
        # Save to a temp and atomically replace the pristine src so the
        # editor session now sees the re-fonted PDF as its base.
        tmp = _work_dir() / f"pe_{upload_id}_src_new.pdf"
        doc.save(str(tmp), garbage=3, deflate=True)
    finally:
        doc.close()
    tmp.replace(src)

    # Re-render page PNGs so thumbnails refresh on the frontend.
    pages_info = []
    with fitz.open(str(src)) as d2:
        for i in range(d2.page_count):
            page = d2[i]
            png = _work_dir() / f"pe_{upload_id}_p{i+1}.png"
            pdf_preview.render_page_png(src, png, i, dpi=120)
            pages_info.append({
                "index": i,
                "width_pt": page.rect.width,
                "height_pt": page.rect.height,
                "preview_url": f"/tools/pdf-editor/preview/{png.name}?t={int(time.time())}",
            })

    return replaced, pages_info


@router.post("/replace-all-fonts")
async def replace_all_fonts(request: Request):
    """One-click: replace every existing text span in the PDF with the
    same text rendered in a chosen font. Destructive — overwrites the
    editor session's pristine source with the new PDF.

    在覆寫前先把目前的 src 備份到 `pe_{id}_src_pre_repl.pdf`，這樣使用者
    後悔可以呼叫 /undo-replace-all-fonts 還原。"""
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    font_id = str(body.get("font_id") or "pymupdf:default")
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _work_dir() / f"pe_{upload_id}_src.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired or missing")
    # 備份目前 src（每次換字型只保最新一份，避免無限疊加）
    backup = _work_dir() / f"pe_{upload_id}_src_pre_repl.pdf"
    try:
        shutil.copyfile(str(src), str(backup))
    except Exception:
        pass  # 備份失敗不擋換字型，但 undo 就會 404
    import asyncio as _asyncio
    replaced, pages_info = await _asyncio.to_thread(
        _replace_all_fonts_sync, src, upload_id, font_id
    )
    return {"ok": True, "replaced": replaced, "pages": pages_info,
            "can_undo": backup.exists()}


@router.post("/undo-replace-all-fonts")
async def undo_replace_all_fonts(request: Request):
    """還原最近一次「整份換字型」前的 src.pdf 並重新渲染預覽。"""
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = _work_dir() / f"pe_{upload_id}_src.pdf"
    backup = _work_dir() / f"pe_{upload_id}_src_pre_repl.pdf"
    if not backup.exists():
        raise HTTPException(404, "no backup — already undone or never replaced")
    # restore: backup → src（搬走避免重複按）
    backup.replace(src)
    # 重新渲染所有頁的預覽 PNG
    pages_info = []
    with fitz.open(str(src)) as d2:
        for i in range(d2.page_count):
            page = d2[i]
            png = _work_dir() / f"pe_{upload_id}_p{i+1}.png"
            pdf_preview.render_page_png(src, png, i, dpi=120)
            pages_info.append({
                "index": i,
                "width_pt": page.rect.width,
                "height_pt": page.rect.height,
                "preview_url": f"/tools/pdf-editor/preview/{png.name}?t={int(time.time())}",
            })
    return {"ok": True, "pages": pages_info}


@router.post("/upload-image")
async def upload_image(
    request: Request,
    upload_id: str = Form(...),
    file: UploadFile = File(...),
):
    """Accept an ad-hoc image (PNG/JPEG) from the editor UI. Stash it under
    the tool work_dir with a pe_ prefix so the same save-time flow that
    re-inserts extracted PDF images (via ``existing_src``) can re-use it.
    Returns {url, width, height} so the frontend can place the image on
    the canvas at its natural aspect ratio.
    """
    import uuid as _uuid
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    fname_lower = (file.filename or "").lower()
    if not any(fname_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif")):
        raise HTTPException(400, "只支援 PNG / JPEG / WEBP / GIF")
    # Normalize everything to PNG so PyMuPDF's insert_image + alpha handling
    # is consistent.
    png_bytes: bytes
    try:
        from io import BytesIO
        try:
            from PIL import Image  # type: ignore
            im = Image.open(BytesIO(data))
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.mode else "RGB")
            buf = BytesIO()
            im.save(buf, format="PNG")
            png_bytes = buf.getvalue()
            w, h = im.size
        except Exception:
            # Pillow not available — fall back to raw bytes (PyMuPDF can
            # still consume PNG/JPEG via stream=).
            png_bytes = data
            # Ask PyMuPDF for dimensions instead.
            try:
                pix = fitz.Pixmap(data)
                w, h = pix.width, pix.height
                pix = None
            except Exception:
                w, h = 100, 100
    except Exception as e:
        raise HTTPException(400, f"解碼失敗：{e}")

    fname = f"pe_{upload_id}_userimg_{_uuid.uuid4().hex}.png"
    (_work_dir() / fname).write_bytes(png_bytes)
    return {
        "ok": True,
        "url": f"/tools/pdf-editor/preview/{fname}",
        "width": w,
        "height": h,
    }


# Subfolder under temp_dir for this tool's working files.
def _work_dir() -> Path:
    p = settings.temp_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hex_rgb01(h: str) -> tuple[float, float, float]:
    """#rrggbb (or rrggbb) → (r, g, b) each 0-1 for PyMuPDF colour args."""
    h = (h or "").lstrip("#")
    if len(h) != 6:
        return (0.0, 0.0, 0.0)
    try:
        return (int(h[0:2], 16)/255.0, int(h[2:4], 16)/255.0, int(h[4:6], 16)/255.0)
    except Exception:
        return (0.0, 0.0, 0.0)


def _style_suffix(base: str, bold: bool = False, italic: bool = False) -> str:
    """Map PyMuPDF built-in font base name + bold/italic flags → final
    fontname (e.g. helv + bold → hebo). Module-level so it can be called
    from any helper. Previously a nested fn inside save(), but
    _insert_mixed_text/_resolve_fonts_for_pref also need it (replace-all-fonts
    crashed with NameError before this hoist)."""
    if base in ("helv", "hebo", "heit", "hebi"):
        return {(0, 0): "helv", (1, 0): "hebo",
                (0, 1): "heit", (1, 1): "hebi"}[(int(bold), int(italic))]
    if base in ("tiro", "tibo", "tiit", "tibi"):
        return {(0, 0): "tiro", (1, 0): "tibo",
                (0, 1): "tiit", (1, 1): "tibi"}[(int(bold), int(italic))]
    if base in ("cour", "cobo", "coit", "cobi"):
        return {(0, 0): "cour", (1, 0): "cobo",
                (0, 1): "coit", (1, 1): "cobi"}[(int(bold), int(italic))]
    return base  # china-s/china-t/uf*/uc* etc have no style variants


def _upgrade_cjk_font(page, builtin_name: str, cache: dict, pno: int) -> str:
    """Upgrade a PyMuPDF built-in CJK font name (china-t / china-ts /
    china-s / china-ss) to a registered system CJK font when one is
    available. Falls back to the original built-in if nothing usable.

    根因：PyMuPDF 的 china-* 內建在 Linux 上實際渲染都長一樣（厚實 sans），
    使用者選 PyMuPDF Serif 看不出與 Sans 差別。這裡把 china-t→Noto Serif CJK
    TC、china-ts→Noto Sans CJK TC 等替換掉。Cache key 跟其它 system font
    註冊共用，避免每頁重複 insert_font。"""
    style_cjk_map = {
        "china-t":  ("serif", "traditional"),
        "china-ts": ("sans",  "traditional"),
        "china-s":  ("serif", "simplified"),
        "china-ss": ("sans",  "simplified"),
    }
    pair = style_cjk_map.get(builtin_name)
    if not pair:
        return builtin_name  # not a builtin we know how to upgrade
    style, cjk = pair
    try:
        from ...core import font_catalog
        best = font_catalog.best_cjk_path(style=style, cjk=cjk)
    except Exception:
        best = None
    if not best:
        return builtin_name
    path, idx = best
    fkey = (pno, str(path), idx)
    if fkey in cache:
        return cache[fkey]
    try:
        reg_name = f"cu{len(cache)}"
        page.insert_font(fontname=reg_name, fontfile=str(path))
        cache[fkey] = reg_name
        return reg_name
    except Exception:
        return builtin_name


def _insert_mixed_text(page, x: float, y: float, text: str, *,
                       cjk_font: str, ascii_font: str,
                       font_size: float, color: tuple) -> float:
    """Render a text run by splitting into consecutive CJK / ASCII groups
    and drawing each with the right font. This avoids PyMuPDF's China-
    series fonts rendering ASCII punctuation as wide "full-width" glyphs
    (which makes e.g. "]]]" look like "] ] ]" with big gaps). Returns
    the ending x-coordinate.
    """
    if not text:
        return x
    # Group consecutive chars by script
    runs: list[tuple[bool, str]] = []
    cur = ""
    cur_is_cjk: Optional[bool] = None
    def _is_cjk(ch: str) -> bool:
        # Include CJK Unified + full-width punctuation ranges
        return ("一" <= ch <= "鿿" or
                "　" <= ch <= "〿" or
                "＀" <= ch <= "￯")
    for ch in text:
        is_cjk = _is_cjk(ch)
        if cur_is_cjk is None:
            cur = ch; cur_is_cjk = is_cjk
        elif is_cjk == cur_is_cjk:
            cur += ch
        else:
            runs.append((cur_is_cjk, cur))
            cur = ch; cur_is_cjk = is_cjk
    if cur:
        runs.append((cur_is_cjk, cur))

    cx = x
    for is_cjk, run in runs:
        fn = cjk_font if is_cjk else ascii_font
        # 多層 fallback — CJK 內容若 primary fail，要試其他 CJK font，**不能**
        # 直接掉到 helv（Helvetica 沒 CJK glyphs，會渲成 .notdef tofu 或完全
        # 不顯示，使用者看到的就是「文字消失」#6 慘案根因之一）。
        if is_cjk:
            tried = [fn, "china-ts", "china-t", "china-s", "china-ss"]
        else:
            tried = [fn, _style_suffix("helv") if fn != "helv" else "helv"]
        # Dedupe while preserving order
        seen = set(); fallbacks = []
        for f in tried:
            if f not in seen:
                seen.add(f); fallbacks.append(f)
        ok_font = None
        last_err = None
        for try_fn in fallbacks:
            try:
                page.insert_text(fitz.Point(cx, y), run,
                                 fontname=try_fn, fontsize=font_size, color=color)
                ok_font = try_fn
                break
            except Exception as e:
                last_err = e
                continue
        if ok_font is None:
            # 所有 fallback 都炸 — 印 warning 讓 admin 知道（避免悄悄變空白）
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "insert_text all fallbacks failed for run %r (cjk=%s, tried=%s): %s",
                run[:20], is_cjk, fallbacks, last_err)
            ok_font = fn  # for length calculation
        # Advance x by measured width of that run
        try:
            cx += fitz.get_text_length(run, fontname=ok_font, fontsize=font_size)
        except Exception:
            cx += font_size * len(run) * 0.6  # rough fallback
    return cx


def _sample_path(path_str: str, ox: float, oy: float,
                 sx: float = 1.0, sy: float = 1.0,
                 path_offset_x: float = 0.0, path_offset_y: float = 0.0) -> list:
    """Parse a simplified SVG-ish path string (as emitted by Fabric.Path)
    and return a list of fitz.Point approximating the curve. Supported
    commands: M x y, L x y, Q cx cy x y (sampled linearly).

    Fabric emits commands separated by spaces with numeric args. Points
    are in the path's own coordinate space; we apply offset+scale so the
    resulting points land at the object's canvas position.
    """
    import re as _re
    tokens = [t for t in _re.split(r"\s+", path_str.strip()) if t]
    pts: list = []
    i = 0
    def _xy(n: int) -> tuple[float, float]:
        px = float(tokens[n])
        py = float(tokens[n + 1])
        return (ox + px * sx, oy + py * sy)
    def _adj(px, py):
        # Subtract Fabric's internal pathOffset (centers the path in its
        # local coord system) then scale + place at canvas origin.
        return (ox + (px - path_offset_x) * sx,
                oy + (py - path_offset_y) * sy)
    last = None
    while i < len(tokens):
        cmd = tokens[i].upper()
        if cmd in ("M", "L") and i + 2 < len(tokens):
            try:
                px = float(tokens[i + 1])
                py = float(tokens[i + 2])
                x, y = _adj(px, py)
                pts.append(fitz.Point(x, y))
                last = (x, y)
            except ValueError:
                pass
            i += 3
        elif cmd == "Q" and i + 4 < len(tokens):
            try:
                cx_raw = float(tokens[i + 1]); cy_raw = float(tokens[i + 2])
                ex_raw = float(tokens[i + 3]); ey_raw = float(tokens[i + 4])
                cx, cy = _adj(cx_raw, cy_raw)
                ex, ey = _adj(ex_raw, ey_raw)
                # Sample Bezier Q with ~6 points between last and (ex,ey)
                if last is None:
                    last = (ex, ey)
                lx, ly = last
                for t in (0.2, 0.4, 0.6, 0.8, 1.0):
                    u = 1 - t
                    px = u*u*lx + 2*u*t*cx + t*t*ex
                    py = u*u*ly + 2*u*t*cy + t*t*ey
                    pts.append(fitz.Point(px, py))
                last = (ex, ey)
            except ValueError:
                pass
            i += 5
        elif cmd == "Z":
            i += 1
        else:
            # Unknown token — skip
            i += 1
    return pts


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse("pdf_editor.html", {"request": request})


@router.post("/load")
async def load(request: Request, file: UploadFile = File(...)):
    """Upload a PDF, render each page as a preview PNG, return upload_id +
    per-page info (size in pt + preview URL)."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF is supported in Phase 1")

    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    src = _work_dir() / f"pe_{upload_id}_src.pdf"
    src.write_bytes(data)
    # Stash the original filename so /download can suggest it back with
    # an _edited suffix.
    try:
        (_work_dir() / f"pe_{upload_id}_name.txt").write_text(
            file.filename or "document.pdf", encoding="utf-8")
    except Exception:
        pass

    # Render each page to PNG at a consistent DPI. Editor UI scales the
    # displayed image and keeps pt coords from the PDF so save-back maps
    # back without resolution loss.
    preview_dpi = 120
    import asyncio as _asyncio
    def _do_render():
        out = []
        with fitz.open(str(src)) as doc:
            for i in range(doc.page_count):
                page = doc[i]
                w_pt = page.rect.width
                h_pt = page.rect.height
                png = _work_dir() / f"pe_{upload_id}_p{i+1}.png"
                pdf_preview.render_page_png(src, png, i, dpi=preview_dpi)
                out.append({
                    "index": i,
                    "width_pt": w_pt,
                    "height_pt": h_pt,
                    "preview_url": f"/tools/pdf-editor/preview/{png.name}",
                })
        return out
    pages_info = await _asyncio.to_thread(_do_render)

    return {
        "upload_id": upload_id,
        "filename": file.filename,
        "preview_dpi": preview_dpi,
        "pages": pages_info,
    }


@router.get("/preview/{filename}")
async def preview(filename: str, request: Request):
    # Strict allowlist + path containment + per-upload ACL.
    from ...core.safe_paths import safe_join, is_safe_name
    from ...core import upload_owner
    if not (filename.startswith("pe_") and is_safe_name(filename)):
        raise HTTPException(400, "invalid filename")
    path = safe_join(_work_dir(), filename)
    # extract uid from "pe_<uuid>_p1.png" / "pe_<uuid>_out_p1.png" ...
    rest = filename[3:].split("_", 1)[0]
    if rest:
        upload_owner.require(rest, request)
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(str(path), media_type="image/png")


@router.get("/file/{upload_id}")
async def original_file(upload_id: str, request: Request):
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner
    require_uuid_hex(upload_id, "upload_id")
    upload_owner.require(upload_id, request)
    src = _work_dir() / f"pe_{upload_id}_src.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired or missing")
    return FileResponse(str(src), media_type="application/pdf")


@router.post("/save")
async def save(request: Request):
    """Accept the editor JSON model + upload_id. Burn overlay objects into
    a new PDF and return the download URL.

    Body shape (Phase 1 subset — only 'text' and 'whiteout' handled):
      {
        "upload_id": "<hex>",
        "pages": [
          {"page": 0, "objects": [
            {"id": "...", "type": "text", "x": <pt>, "y": <pt>,
             "w": <pt>, "h": <pt>, "text": "...", "font_size": 12,
             "color": "#000000"},
            {"id": "...", "type": "whiteout", "x": <pt>, "y": <pt>,
             "w": <pt>, "h": <pt>}
          ]}
        ]
      }
    """
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    pages = body.get("pages") or []
    src = _work_dir() / f"pe_{upload_id}_src.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired or missing")

    out = _work_dir() / f"pe_{upload_id}_out.pdf"
    # Always open the pristine original — the client sends the FULL edit
    # model (all origBboxes, all deleted_originals, all added objects) on
    # each save, and we replay them from scratch. This keeps redactions
    # idempotent and avoids content duplication when users move an
    # extracted object multiple times.
    doc = fitz.open(str(src))
    # Per-page cache of system fonts already registered this save
    # {(page_index, font_path, idx): buffername}
    _custom_font_cache: dict = {}
    try:
        # ---- Pass 1: apply redactions (destructive removal of existing
        # PDF content that the user deleted, moved, or resized). All
        # redactions for a page must be applied before we insert new
        # content, otherwise apply_redactions can wipe our new overlays.
        for pg in pages:
            pno = int(pg.get("page", 0))
            if pno < 0 or pno >= doc.page_count:
                continue
            page = doc[pno]
            has_redact = False
            for obj in pg.get("objects", []):
                orig = obj.get("original_bbox")
                if not orig or len(orig) != 4:
                    continue
                # Backend safety net (#6 v1.4.2): text 物件 text 為空但有
                # original_bbox 是 bug state — redact 會清掉原文位置但 Pass 2
                # 又跳過空 text 不寫，結果留下完全空白「文字消失」。直接 skip
                # 此 obj 的 redact，原文保留；client safety net 也已有同樣邏輯
                # 不送上來，這裡是雙保險。
                if obj.get("type") == "text" and not str(obj.get("text") or "").strip():
                    import logging as _lg
                    _lg.getLogger(__name__).warning(
                        "skipping redact for text obj with empty text "
                        "(original_bbox=%s) — would leave blank area", orig)
                    continue
                ox0, oy0, ox1, oy1 = [float(v) for v in orig]
                page.add_redact_annot(
                    fitz.Rect(ox0, oy0, ox1, oy1),
                    fill=(1, 1, 1),
                )
                has_redact = True
            # Explicit "deleted existing" entries (no new content, just redact)
            for dbb in pg.get("deleted_originals", []) or []:
                if not dbb or len(dbb) != 4:
                    continue
                dx0, dy0, dx1, dy1 = [float(v) for v in dbb]
                page.add_redact_annot(
                    fitz.Rect(dx0, dy0, dx1, dy1),
                    fill=(1, 1, 1),
                )
                has_redact = True

            # AcroForm widgets marked for deletion. Match by field_name
            # when available (survives bbox jitter), otherwise by bbox.
            for wd in pg.get("deleted_widgets", []) or []:
                target_name = (wd.get("field_name") or "").strip()
                bb = wd.get("bbox") or []
                try:
                    for widget in list(page.widgets() or []):
                        matched = False
                        if target_name and (widget.field_name or "") == target_name:
                            matched = True
                        elif bb and len(bb) == 4 and widget.rect:
                            # Loose bbox match (within 2pt) as fallback.
                            wr = widget.rect
                            if (abs(wr.x0 - bb[0]) < 2 and abs(wr.y0 - bb[1]) < 2
                                and abs(wr.x1 - bb[2]) < 2 and abs(wr.y1 - bb[3]) < 2):
                                matched = True
                        if not matched:
                            continue
                        # Also paint over the widget area with a redact so
                        # any visible appearance goes away.
                        try:
                            page.add_redact_annot(widget.rect, fill=(1, 1, 1))
                            has_redact = True
                        except Exception:
                            pass
                        try:
                            page.delete_widget(widget)
                        except Exception:
                            try:
                                widget.field_name = ""
                                widget.update()
                            except Exception:
                                pass
                except Exception:
                    pass

            if has_redact:
                # IMAGES=NONE keeps embedded images intact outside redact
                # rects (default behaviour would wipe whole images).
                try:
                    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                except Exception:
                    page.apply_redactions()

        # ---- Pass 2: draw overlays and re-inserts.
        for pg in pages:
            pno = int(pg.get("page", 0))
            if pno < 0 or pno >= doc.page_count:
                continue
            page = doc[pno]
            for obj in pg.get("objects", []):
                ot = obj.get("type")
                x = float(obj.get("x", 0))
                y = float(obj.get("y", 0))
                w = float(obj.get("w", 0))
                h = float(obj.get("h", 0))
                rect = fitz.Rect(x, y, x + w, y + h)
                if ot == "whiteout":
                    # Paint an opaque white rectangle over the target area.
                    # Not a redaction (that removes content); this is a
                    # visual cover. True redact lives in a later phase.
                    page.draw_rect(rect, color=None, fill=(1, 1, 1),
                                   fill_opacity=1.0, overlay=True)
                elif ot == "image":
                    # Source: either an asset (stamp/signature/logo) OR an
                    # extracted existing image (existing_src points to a PNG
                    # we wrote under work_dir during detect-objects).
                    src_file = None
                    asset_id = obj.get("asset_id") or ""
                    if asset_id:
                        a = asset_manager.get(asset_id)
                        if a:
                            cand = settings.assets_files_dir / a.file_key
                            if cand.exists():
                                src_file = cand
                    if src_file is None:
                        # existing_src is a URL like /tools/pdf-editor/preview/pe_<id>_imgxN_pM.png
                        es = obj.get("existing_src") or ""
                        if es:
                            fname = es.rsplit("/", 1)[-1]
                            # Only allow our own pe_ prefix files (no traversal)
                            if (fname.startswith("pe_") and ".." not in fname
                                and "/" not in fname):
                                cand = _work_dir() / fname
                                if cand.exists():
                                    src_file = cand
                    if src_file is None:
                        continue
                    try:
                        # Use stream=bytes (same as pdf-stamp), which
                        # preserves PNG alpha correctly. filename=... +
                        # alpha=... can introduce a gray matte artefact
                        # on some PyMuPDF versions.
                        with open(src_file, "rb") as _f:
                            img_bytes = _f.read()
                        kwargs = dict(stream=img_bytes,
                                      keep_proportion=False, overlay=True)
                        ang = float(obj.get("angle") or 0.0)
                        if ang:
                            kwargs["rotate"] = ang
                        page.insert_image(rect, **kwargs)
                        # Optional opacity wasn't applied here — we omit
                        # it on purpose because `alpha=int` in PyMuPDF
                        # overwrites per-pixel transparency. If future
                        # versions need user-controlled opacity, apply
                        # via a Pillow pre-pass that multiplies the
                        # alpha channel.
                    except Exception:
                        continue
                elif ot == "rect":
                    stroke = str(obj.get("stroke") or "#000000").lstrip("#")
                    fill = obj.get("fill")  # nullable
                    sw = float(obj.get("stroke_width") or 1.0)
                    sr, sg, sb = _hex_rgb01(stroke)
                    fill_rgb = _hex_rgb01(str(fill).lstrip("#")) if fill else None
                    page.draw_rect(
                        rect,
                        color=(sr, sg, sb),
                        fill=fill_rgb,
                        width=sw,
                        overlay=True,
                    )
                elif ot == "line":
                    x2 = float(obj.get("x2", x + w))
                    y2 = float(obj.get("y2", y + h))
                    stroke = str(obj.get("stroke") or "#000000").lstrip("#")
                    sw = float(obj.get("stroke_width") or 1.0)
                    sr, sg, sb = _hex_rgb01(stroke)
                    page.draw_line(
                        fitz.Point(x, y), fitz.Point(x2, y2),
                        color=(sr, sg, sb), width=sw, overlay=True,
                    )
                elif ot == "arrow":
                    # Line + a filled triangular head at (x2, y2).
                    x2 = float(obj.get("x2", x + w))
                    y2 = float(obj.get("y2", y + h))
                    stroke = str(obj.get("stroke") or "#000000").lstrip("#")
                    sw = float(obj.get("stroke_width") or 1.0)
                    col = _hex_rgb01(stroke)
                    import math as _m
                    page.draw_line(fitz.Point(x, y), fitz.Point(x2, y2),
                                   color=col, width=sw, overlay=True)
                    # Arrowhead triangle
                    dx, dy = x2 - x, y2 - y
                    ang = _m.atan2(dy, dx)
                    ah = max(6.0, sw * 4.0)   # arrowhead length
                    aw = ah * 0.6             # half-width
                    p1 = fitz.Point(x2, y2)
                    p2 = fitz.Point(x2 - ah*_m.cos(ang) + aw*_m.sin(ang),
                                    y2 - ah*_m.sin(ang) - aw*_m.cos(ang))
                    p3 = fitz.Point(x2 - ah*_m.cos(ang) - aw*_m.sin(ang),
                                    y2 - ah*_m.sin(ang) + aw*_m.cos(ang))
                    shape = page.new_shape()
                    shape.draw_polyline([p1, p2, p3, p1])
                    shape.finish(color=col, fill=col, width=sw)
                    shape.commit(overlay=True)
                elif ot == "highlight":
                    # Yellow translucent rectangle over the area. Using
                    # draw_rect with fill_opacity gives a predictable
                    # visual result (PDF highlight annotations anchor to
                    # text spans, which we don't track here).
                    color_hex = str(obj.get("color") or "#fde047").lstrip("#")
                    r, g, b = _hex_rgb01(color_hex)
                    page.draw_rect(rect, color=None, fill=(r, g, b),
                                   fill_opacity=0.45, overlay=True)
                elif ot == "sticky":
                    # Real PDF text annotation — clickable in viewers,
                    # shows the note text as a popup bubble.
                    note = str(obj.get("note") or "便箋")
                    try:
                        annot = page.add_text_annot(
                            fitz.Point(x, y), note, icon="Note",
                        )
                        try:
                            annot.set_colors(stroke=(1.0, 0.83, 0.09))
                            annot.update()
                        except Exception:
                            pass
                    except Exception:
                        # Fallback: draw a small yellow square so user
                        # still sees something even if annot API fails
                        page.draw_rect(
                            fitz.Rect(x, y, x + max(w, 18), y + max(h, 18)),
                            color=(0.85, 0.47, 0.04), fill=(0.99, 0.90, 0.52),
                            width=0.8, overlay=True,
                        )
                elif ot == "pencil":
                    # Free-hand path. Frontend has resolved Fabric's
                    # pathOffset/scale/translation and sent a flat list
                    # of absolute (x, y) points in PDF points — all we
                    # have to do is draw the polyline.
                    raw_pts = obj.get("points") or []
                    stroke = str(obj.get("stroke") or "#000000").lstrip("#")
                    sw = float(obj.get("stroke_width") or 1.0)
                    col = _hex_rgb01(stroke)
                    pts = []
                    for p in raw_pts:
                        if isinstance(p, (list, tuple)) and len(p) >= 2:
                            try:
                                pts.append(fitz.Point(float(p[0]), float(p[1])))
                            except (TypeError, ValueError):
                                continue
                    if len(pts) >= 2:
                        shape = page.new_shape()
                        shape.draw_polyline(pts)
                        # closePath=False: do NOT connect last point back
                        # to first. Essential for freehand strokes, which
                        # otherwise form a filled loop.
                        shape.finish(color=col, fill=None, width=sw,
                                     lineCap=1, lineJoin=1, closePath=False)
                        shape.commit(overlay=True)
                elif ot == "ellipse":
                    stroke = str(obj.get("stroke") or "#000000").lstrip("#")
                    fill = obj.get("fill")
                    sw = float(obj.get("stroke_width") or 1.0)
                    sr, sg, sb = _hex_rgb01(stroke)
                    fill_rgb = _hex_rgb01(str(fill).lstrip("#")) if fill else None
                    page.draw_oval(
                        rect, color=(sr, sg, sb), fill=fill_rgb,
                        width=sw, overlay=True,
                    )
                elif ot == "text":
                    text = str(obj.get("text") or "")
                    has_orig = obj.get("original_bbox") is not None
                    if not text:
                        # Empty text — should never reach here because the
                        # frontend safety net + Pass 1 redact safety both
                        # filter empty-text+orig_bbox combinations.
                        # Log if it does so we can find the path.
                        import logging as _lg
                        _lg.getLogger(__name__).warning(
                            "text obj reached Pass 2 with empty text "
                            "(has_orig_bbox=%s, page=%d) — skipping",
                            has_orig, pno)
                        continue
                    # 診斷：印出每個 text obj 的關鍵欄位 + page.rotation（v1.4.13 #6 偵錯用）
                    import logging as _lg
                    _lg.getLogger(__name__).info(
                        "pdf-editor insert text page=%d rect=%s text=%r "
                        "font_pref=%s font_size=%.1f color=%r has_orig_bbox=%s "
                        "page.rotation=%d page.mediabox=%s page.rect=%s",
                        pno, [round(rect.x0,1), round(rect.y0,1),
                              round(rect.x1,1), round(rect.y1,1)],
                        text[:50],
                        obj.get("font") or "default",
                        float(obj.get("font_size") or 11),
                        obj.get("color"),
                        has_orig,
                        page.rotation,
                        [round(v,1) for v in (page.mediabox.x0, page.mediabox.y0,
                                              page.mediabox.x1, page.mediabox.y1)],
                        [round(v,1) for v in (page.rect.x0, page.rect.y0,
                                              page.rect.x1, page.rect.y1)])
                    font_size = float(obj.get("font_size") or 11)
                    col = _hex_rgb01(str(obj.get("color") or "#000000"))
                    bold = bool(obj.get("bold"))
                    italic = bool(obj.get("italic"))
                    underline = bool(obj.get("underline"))
                    font_pref = str(obj.get("font") or "default")
                    has_cjk = any("一" <= c <= "鿿" for c in text)
                    # System / custom font override — user picked a .ttf/.otf/.ttc
                    # from the catalog（`system:<path>` 或 `custom:<filename>`）。
                    # 註冊到 page 後用 reg_name 給 insert_text 用。
                    # NOTE: 之前漏了 `custom:` 分支，導致 admin 上傳的字型 (例如「微軟
                    # 正黑體」) 被 fall through 到 built-in china-t — UI 預覽看起來對
                    # 但 auto-save 後重畫就跑掉。v1.4.74 修。
                    custom_font_name = None
                    if font_pref.startswith("system:") or font_pref.startswith("custom:"):
                        from ...core import font_catalog
                        entry = font_catalog.resolve_font_id(font_pref)
                        if entry and entry.get("path"):
                            fkey = (pno, entry["path"], int(entry.get("idx") or 0))
                            if fkey in _custom_font_cache:
                                custom_font_name = _custom_font_cache[fkey]
                            else:
                                try:
                                    prefix = "uc" if font_pref.startswith("custom:") else "uf"
                                    reg_name = f"{prefix}{len(_custom_font_cache)}"
                                    page.insert_font(
                                        fontname=reg_name,
                                        fontfile=entry["path"],
                                        # TTC subfont index (0 if not TTC)
                                    )
                                    _custom_font_cache[fkey] = reg_name
                                    custom_font_name = reg_name
                                except Exception:
                                    # Registration failed — fall through to built-ins
                                    custom_font_name = None

                    # Pick PyMuPDF fontname based on user preference + content.
                    # PyMuPDF built-in font names:
                    #   helv / hebo / heit / hebi  — Helvetica family
                    #   tiro / tibo / tiit / tibi  — Times family
                    #   cour / cobo / coit / cobi  — Courier family
                    #   china-s / china-ss          — SimSun (simplified CJK)
                    #   china-t / china-ts          — MingLiu (traditional CJK)
                    # _style_suffix() 已 hoist 到 module level —
                    # 內部呼叫補上 bold/italic 參數即可。

                    # PyMuPDF built-in CJK fonts:
                    #   china-t  = Traditional 宋體 (MingLiU)     ← Taiwan default
                    #   china-ts = Traditional 黑體 (DFKai-SB-ish)
                    #   china-s  = Simplified 宋體 (SimSun)
                    #   china-ss = Simplified 黑體 (SimHei)
                    # Use traditional as default — this tool is
                    # Taiwan-focused and 繁中 fonts substitute weirdly when
                    # rendered through Simplified CID tables (銜 → 衛 etc).
                    if custom_font_name:
                        fontname = custom_font_name
                    elif font_pref == "pymupdf:sans" or font_pref == "sans":
                        fontname = "china-ts" if has_cjk else _style_suffix("helv", bold, italic)
                    elif font_pref == "pymupdf:serif" or font_pref == "serif":
                        fontname = "china-t" if has_cjk else _style_suffix("tiro", bold, italic)
                    elif font_pref == "pymupdf:cour" or font_pref == "mono":
                        fontname = "china-t" if has_cjk else _style_suffix("cour", bold, italic)
                    elif font_pref == "pymupdf:helv" or font_pref == "helv":
                        fontname = _style_suffix("helv", bold, italic)
                    elif font_pref == "pymupdf:tiro" or font_pref == "tiro":
                        fontname = _style_suffix("tiro", bold, italic)
                    elif font_pref == "pymupdf:simplified" or font_pref == "simplified":
                        fontname = "china-ss" if has_cjk else _style_suffix("helv", bold, italic)
                    else:  # "default" / "pymupdf:default"
                        fontname = "china-t" if has_cjk else _style_suffix("helv", bold, italic)
                    # Split text into CJK / ASCII runs and render each
                    # with the right font so ASCII punctuation in mixed
                    # content doesn't blow up to full-width. ASCII font
                    # tracks the user's preference (helv/tiro/cour) and
                    # its bold/italic variant.
                    # 把 china-* 內建升級為實際系統 CJK 字型（v1.4.77 修，
                    # PyMuPDF 內建在 Linux 全部 render 成 sans，serif 選項
                    # 沒效果）。Cache 用同一張 _custom_font_cache。
                    def _u(builtin: str) -> str:
                        return _upgrade_cjk_font(page, builtin, _custom_font_cache, pno)
                    if fontname.startswith("uf") or fontname.startswith("uc"):
                        # Custom / system font — user picked it specifically,
                        # use it for all chars without splitting (it's
                        # presumably designed for the content they pasted).
                        ascii_font = fontname
                        cjk_font = fontname
                    elif font_pref in ("pymupdf:serif", "serif"):
                        cjk_font = _u("china-t")
                        ascii_font = _style_suffix("tiro", bold, italic)
                    elif font_pref in ("pymupdf:simplified", "simplified"):
                        cjk_font = _u("china-ss")
                        ascii_font = _style_suffix("helv", bold, italic)
                    elif font_pref in ("pymupdf:helv", "helv"):
                        cjk_font = _u("china-t")  # fallback for any CJK in "ASCII-only" picks
                        ascii_font = _style_suffix("helv", bold, italic)
                    elif font_pref in ("pymupdf:tiro", "tiro"):
                        cjk_font = _u("china-t")
                        ascii_font = _style_suffix("tiro", bold, italic)
                    elif font_pref in ("pymupdf:cour", "mono"):
                        cjk_font = _u("china-t")
                        ascii_font = _style_suffix("cour", bold, italic)
                    else:  # sans / default
                        cjk_font = _u("china-ts")
                        ascii_font = _style_suffix("helv", bold, italic)

                    lines = text.split("\n") if "\n" in text else [text]
                    ybase = rect.y0 + font_size
                    for i, line in enumerate(lines):
                        if not line:
                            continue
                        try:
                            _insert_mixed_text(
                                page, rect.x0, ybase + i * font_size * 1.2,
                                line, cjk_font=cjk_font, ascii_font=ascii_font,
                                font_size=font_size, color=col,
                            )
                        except Exception:
                            try:
                                page.insert_text(
                                    fitz.Point(rect.x0, ybase + i * font_size * 1.2),
                                    line, fontname="helv",
                                    fontsize=font_size, color=col,
                                )
                            except Exception:
                                pass
                    # Underline drawn manually beneath the first line
                    if underline:
                        y_line = rect.y1 - font_size * 0.15
                        page.draw_line(
                            fitz.Point(rect.x0, y_line),
                            fitz.Point(rect.x1, y_line),
                            color=col, width=max(0.5, font_size * 0.05),
                            overlay=True,
                        )
        doc.save(str(out), garbage=4, deflate=True)
    finally:
        doc.close()

    # Re-render previews so the user can see the result
    preview_dpi = 120
    pages_info = []
    with fitz.open(str(out)) as d2:
        for i in range(d2.page_count):
            page = d2[i]
            png = _work_dir() / f"pe_{upload_id}_out_p{i+1}.png"
            pdf_preview.render_page_png(out, png, i, dpi=preview_dpi)
            pages_info.append({
                "index": i,
                "width_pt": page.rect.width,
                "height_pt": page.rect.height,
                "preview_url": f"/tools/pdf-editor/preview/{png.name}",
            })

    return {
        "upload_id": upload_id,
        "download_url": f"/tools/pdf-editor/download/{upload_id}",
        "pages": pages_info,
    }


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    from ...core.safe_paths import require_uuid_hex
    from ...core import upload_owner
    require_uuid_hex(upload_id, "upload_id")
    upload_owner.require(upload_id, request)
    out = _work_dir() / f"pe_{upload_id}_out.pdf"
    if not out.exists():
        raise HTTPException(404, "saved file not found — save first")
    # Recover original filename if stashed, else fallback to a random one.
    name_file = _work_dir() / f"pe_{upload_id}_name.txt"
    orig = "document.pdf"
    try:
        if name_file.exists():
            orig = name_file.read_text(encoding="utf-8").strip() or orig
    except Exception:
        pass
    base = orig.rsplit(".", 1)[0]
    fn = f"{base}_edited.pdf"
    return FileResponse(str(out), media_type="application/pdf", filename=fn)

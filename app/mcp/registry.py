"""MCP 開放的工具登記表 —— **哪些工具、參數、怎麼呼叫原本的 API、結果怎麼處理，只寫在這裡**。

* `name` 是 MCP 上的名稱（英文小寫加底線，三家客戶端都吃得下）；`tool_id` 是本站的工具代號（權限照它）。
* 說明一律英文、短：模型讀英文最一致，也省上下文；畫面上的中文名稱另外有。
* `params` 是 JSON Schema 的子集（type / enum / minimum / maximum / pattern / maxLength / default），
  伺服器端照這份檢查（`validate`），錯了回清楚的錯誤讓模型自己修。
* 沒有列在這裡的工具，MCP 一律叫不到；測試檢查每一支載入的工具都有歸類（`CLASSIFIED`）。
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

#: 輸入檔案的種類 → 允許的副檔名（工作區記錄的 ext）
PDF = (".pdf",)
OFFICE = (".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp", ".odg")
TEXT = (".txt", ".md", ".csv", ".json")
IMAGE = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
MARKDOWN = (".md", ".txt")
AUDIO = (".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".flac", ".mp4", ".mov", ".mkv", ".webm")
KINDS = {"pdf": PDF, "office": OFFICE, "pdf_or_office": PDF + OFFICE, "doc_or_text": PDF + OFFICE + TEXT,
         "image": IMAGE, "markdown": MARKDOWN, "pdf_or_image": PDF + IMAGE, "any_doc": PDF + OFFICE + IMAGE,
         "audio": AUDIO}
KIND_TEXT = {"pdf": "PDF", "office": "Office / ODF document", "pdf_or_office": "PDF or Office / ODF document",
             "doc_or_text": "PDF, Office / ODF document or plain text", "image": "Image (PNG, JPEG, TIFF)",
             "markdown": "Markdown or plain text", "pdf_or_image": "PDF or image (PNG, JPEG, TIFF)",
             "any_doc": "PDF, Office / ODF document or image (PNG, JPEG, TIFF)",
             "audio": "Audio or video recording (m4a, mp3, wav, aac, ogg, opus, flac, mp4, mov, mkv, webm)"}
#: PDF 編輯器經 MCP 只收這些物件；圖片（含資產庫的印章 / 簽名）不收 —— AI 用印另有規則
EDITOR_OBJECT_TYPES = ("text", "rect", "ellipse", "line", "arrow", "highlight", "whiteout", "sticky", "pencil")


#: 工作區收得下的結果檔（ZIP 裡其他種類的檔案略過）
WORKSPACE_KEEP = (".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp", ".odg",
                  ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".txt", ".md", ".json")


@dataclass(frozen=True)
class McpTool:
    name: str
    tool_id: str
    category: str
    title: str                      # 中文名稱（畫面、稽核用）
    summary: str                    # 一行英文說明（目錄用）
    path: str                       # 原本的 API 路徑
    inputs: Optional[str]           # 輸入檔種類（KINDS 的鍵）；None ＝ 不吃檔案
    min_files: int = 0
    max_files: int = 0
    file_field: str = "file"
    params: dict = field(default_factory=dict)
    required: tuple = ()
    body: str = "form"              # form | json
    output: str = "file"            # file | files | json | text | image_two_step
    output_desc: str = ""
    example: dict = field(default_factory=dict)
    default_on: bool = True
    #: B 類：原本的工具有 LLM 選項 —— 這裡送出的表單一律把它關掉（欄位 → 值）
    force: dict = field(default_factory=dict)
    #: 多個檔案、各自一個表單欄位（例如比對的新舊兩份）；空的＝全部放在 `file_field`
    file_fields: tuple = ()
    #: 結果是 ZIP 或多個檔案時要留下的副檔名（工作區只收這些）
    keep: tuple = (".pdf",)
    #: 原本的 API 回的檔名是固定的（例如 resized.pdf）：改用「來源檔名-工具名」
    generic_names: tuple = ()
    #: JSON 結果裡太占空間、AI 用不到的欄位（例如比對的標示框座標），回傳前拿掉
    json_strip: tuple = ()
    #: 原本的 API 要的是陣列 / 物件（JSON body）：MCP 這邊收 JSON 字串，檢查過再轉成原本的型別
    json_fields: tuple = ()
    #: 原本的 API 要的是字串陣列（JSON body）：MCP 這邊收逗號分隔的字串
    csv_fields: tuple = ()
    #: 原本的 API 是 GET（查詢類，例如列出公司）
    method: str = "POST"
    #: 額外的參數檢查（檢查過的參數 → 不合格就丟 ParamError）；例如 PDF 編輯器不收圖片
    check: Any = None
    #: AI 用印（v1.17.38）：不走一般的開放清單，只在「允許 AI 用印」＋ Token 勾「允許用印」時看得到
    stamp: bool = False
    #: 任一支工具有權限就看得到（例如列出可用的章：用印或騎縫章其中一個就可以）
    any_tools: tuple = ()


def _pii_ids() -> str:
    from ..tools.doc_deident import patterns as _p
    return ", ".join(p.id for p in _p.CATALOG)


PII_IDS = _pii_ids()

_PAGES = {"type": "string", "maxLength": 200, "pattern": r"^(all|\d+(-\d+)?(,\d+(-\d+)?)*)$",
          "description": "'all' or 1-based pages/ranges, e.g. '1-3,5'"}

TOOLS: tuple[McpTool, ...] = (
    McpTool(
        "pdf_merge", "pdf-merge", "pdf_edit", "檔案合併",
        "Merge 2-20 PDFs into one PDF, in the given order.",
        "/tools/pdf-merge/api/pdf-merge", "pdf", 2, 20, file_field="files",
        output="file", output_desc="One merged PDF saved to the workspace.",
        example={"tool": "pdf_merge", "files": ["<file_id_1>", "<file_id_2>"]}),
    McpTool(
        "pdf_split", "pdf-split", "pdf_edit", "頁面分拆",
        "Split a PDF into one file per page, or by page ranges.",
        "/tools/pdf-split/api/pdf-split", "pdf", 1, 1,
        params={"mode": {"type": "string", "enum": ["each", "ranges"], "default": "each",
                         "description": "'each' = one PDF per page; 'ranges' = one PDF per range"},
                "ranges": {"type": "string", "maxLength": 200, "pattern": r"^(\d+(-\d+)?(,\d+(-\d+)?)*)?$",
                           "default": "", "description": "With mode=ranges, e.g. '1-3,4-6,7'"}},
        output="files", output_desc="One PDF per part saved to the workspace.",
        example={"tool": "pdf_split", "files": ["<file_id>"], "params": {"mode": "ranges", "ranges": "1-2,3-5"}}),
    McpTool(
        "pdf_compress", "pdf-compress", "pdf_edit", "PDF 壓縮",
        "Reduce PDF file size by recompressing images.",
        "/tools/pdf-compress/api/pdf-compress", "pdf", 1, 1,
        params={"preset": {"type": "string", "enum": ["gentle", "balanced", "aggressive"], "default": "balanced",
                           "description": "gentle = best quality, aggressive = smallest"}},
        output="file", output_desc="The compressed PDF saved to the workspace.",
        example={"tool": "pdf_compress", "files": ["<file_id>"], "params": {"preset": "balanced"}}),
    McpTool(
        "pdf_rotate", "pdf-rotate", "pdf_edit", "頁面轉向",
        "Rotate or mirror all or selected pages of a PDF; each page can also get its own setting.",
        "/tools/pdf-rotate/api/pdf-rotate", "pdf", 1, 1,
        params={"angle": {"type": "integer", "enum": [90, 180, 270], "default": 90,
                          "description": "Clockwise degrees (ignored when mode is given)"},
                "mode": {"type": "string", "enum": ["", "rotate-90", "rotate-180", "rotate-270", "flip-h", "flip-v"],
                         "default": "", "description": "Instead of angle: rotate or mirror (flip-h = left/right, "
                                                       "flip-v = top/bottom)"},
                "pages": dict(_PAGES, default="all"),
                "per_page": {"type": "string", "maxLength": 20000, "default": "", "json_type": "object",
                             "description": "Per-page override as a JSON object, e.g. "
                                            "'{\"2\": \"flip-h\", \"3\": \"rotate-180\", \"5\": \"none\"}' "
                                            "(1-based pages; wins over mode / pages)"}},
        json_fields=("per_page",),
        output="file", output_desc="The rotated PDF saved to the workspace.",
        example={"tool": "pdf_rotate", "files": ["<file_id>"], "params": {"angle": 90, "pages": "2,4"}}),
    McpTool(
        "pdf_page_numbers", "pdf-pageno", "pdf_edit", "插入頁碼",
        "Add page numbers to a PDF, optionally only to a page range (skip the cover, contents, back cover).",
        "/tools/pdf-pageno/api/pdf-pageno", "pdf", 1, 1,
        params={"position": {"type": "string", "enum": ["tl", "tc", "tr", "bl", "bc", "br"], "default": "br",
                             "description": "t/b = top/bottom, l/c/r = left/center/right"},
                "fmt": {"type": "string", "maxLength": 40, "default": "{n} / {N}",
                        "description": "{n} = page number, {N} = total pages"},
                "start": {"type": "integer", "minimum": 1, "maximum": 99999, "default": 1},
                "font_size": {"type": "number", "minimum": 6, "maximum": 48, "default": 11},
                "margin_mm": {"type": "number", "minimum": 0, "maximum": 50, "default": 10},
                "color": {"type": "string", "pattern": r"^#[0-9a-fA-F]{6}$", "default": "#000000"},
                "from_page": {"type": "integer", "minimum": 1, "maximum": 99999, "default": 1,
                              "description": "First page to number (1-based)"},
                "to_page": {"type": "integer", "minimum": 0, "maximum": 99999, "default": 0,
                            "description": "Last page to number; 0 = the last page. {N} counts only this range"}},
        output="file", output_desc="The numbered PDF saved to the workspace.",
        example={"tool": "pdf_page_numbers", "files": ["<file_id>"], "params": {"position": "bc", "fmt": "- {n} -"}}),
    McpTool(
        "office_to_pdf", "office-to-pdf", "convert", "辦公文件轉 PDF",
        "Convert an Office / ODF document (docx, xlsx, pptx, odt, ods, odp...) to PDF.",
        "/api/convert-to-pdf", "office", 1, 1,
        output="file", output_desc="The PDF saved to the workspace.",
        example={"tool": "office_to_pdf", "files": ["<file_id>"]}),
    McpTool(
        "document_to_images", "pdf-to-image", "convert", "辦公文件轉圖片",
        "Render each page of a PDF or Office document as a PNG or JPEG image.",
        "/tools/pdf-to-image/convert", "pdf_or_office", 1, 1,
        params={"format": {"type": "string", "enum": ["png", "jpeg"], "default": "png"},
                "dpi": {"type": "integer", "minimum": 72, "maximum": 600, "default": 200,
                        "description": "Ignored when width is given"},
                "width": {"type": "integer", "minimum": 16, "maximum": 10000,
                          "description": "Optional fixed width in pixels for every page"},
                "quality": {"type": "integer", "minimum": 1, "maximum": 100, "default": 80,
                            "description": "JPEG quality"}},
        output="image_two_step", output_desc="One image per page saved to the workspace.",
        example={"tool": "document_to_images", "files": ["<file_id>"], "params": {"format": "png", "dpi": 150}}),
    McpTool(
        "pdf_extract_text", "pdf-extract-text", "extract", "擷取文字",
        "Extract the text of a PDF page by page (no AI).",
        "/tools/pdf-extract-text/api/pdf-extract-text", "pdf", 1, 1,
        output="text",
        output_desc="The text is saved to the workspace as .txt; the first part is returned, "
                    "read the rest with workspace_read_text.",
        example={"tool": "pdf_extract_text", "files": ["<file_id>"]}),
    McpTool(
        "document_word_count", "pdf-wordcount", "extract", "字數統計",
        "Count pages, characters and words of a PDF, Office document or text file (no AI).",
        "/tools/pdf-wordcount/api/pdf-wordcount", "doc_or_text", 1, 1,
        output="json", output_desc="Statistics as JSON.",
        example={"tool": "document_word_count", "files": ["<file_id>"]}),
    McpTool(
        "vat_lookup", "vat-lookup", "lookup", "統編查詢",
        "Look up a Taiwan company / organisation by its 8-digit unified business number (UBN).",
        "/tools/vat-lookup/api/vat-lookup", None, 0, 0, body="json",
        params={"vat": {"type": "string", "pattern": r"^\d{8}$", "description": "8 digits"}},
        required=("vat",), output="json", output_desc="Company name, address, status, industries...",
        example={"tool": "vat_lookup", "params": {"vat": "12345678"}}),
    # ---- v1.17.4 第二批
    McpTool(
        "image_to_pdf", "image-to-pdf", "convert", "圖片轉 PDF",
        "Combine images (PNG, JPEG, TIFF) into one PDF, one image per page, in the given order.",
        "/tools/image-to-pdf/api/image-to-pdf", "image", 1, 50, file_field="files",
        params={"page_size": {"type": "string", "enum": ["auto", "a3", "a4", "a5", "a6", "letter", "legal", "tabloid", "b5"],
                              "default": "auto", "description": "'auto' = each page is the image's own size"},
                "margin_mm": {"type": "number", "minimum": 0, "maximum": 50, "default": 0},
                "rotations": {"type": "string", "maxLength": 400, "pattern": r"^((0|90|180|270)(,(0|90|180|270))*)?$",
                              "default": "", "description": "Optional clockwise rotation per image, comma-separated, e.g. '0,90,0'"}},
        output="file", output_desc="One PDF saved to the workspace.",
        example={"tool": "image_to_pdf", "files": ["<image_id_1>", "<image_id_2>"], "params": {"page_size": "a4"}}),
    McpTool(
        "pdf_to_markdown", "pdf-to-markdown", "convert", "PDF 轉 Markdown",
        "Convert a PDF to Markdown text (headings, lists, tables; no AI).",
        "/tools/pdf-to-markdown/api/pdf-to-markdown", "pdf", 1, 1,
        params={"page_separator": {"type": "boolean", "default": True, "description": "Insert a separator between pages"}},
        force={"include_images": False},
        output="file", output_desc="One .md file saved to the workspace (read it with workspace_read_text).",
        example={"tool": "pdf_to_markdown", "files": ["<file_id>"]}),
    McpTool(
        "markdown_to_document", "markdown-to-doc", "convert", "Markdown 轉辦公文件",
        "Turn Markdown (a .md/.txt file or the text parameter) into a formatted PDF, DOCX or ODT.",
        "/tools/markdown-to-doc/api/markdown-to-doc", "markdown", 0, 1,
        params={"text": {"type": "string", "maxLength": 200000, "default": "",
                         "description": "Markdown text; used when no file is given"},
                "format": {"type": "string", "enum": ["pdf", "docx", "odt"], "default": "pdf"},
                "theme": {"type": "string", "enum": ["classic", "github", "academic", "book", "report", "mono", "teal",
                                                     "indigo", "forest", "coral", "navy-gold", "magazine"],
                          "default": "classic"},
                "font": {"type": "string", "enum": ["default", "noto-sans", "noto-serif", "kaiti", "mingti", "monospace"],
                         "default": "default"},
                "title": {"type": "string", "maxLength": 120, "default": "", "description": "Output file name stem"}},
        output="file", output_desc="One PDF / DOCX / ODT saved to the workspace.",
        example={"tool": "markdown_to_document", "params": {"text": "# Report\n\n- item", "format": "docx"}}),
    McpTool(
        "office_convert", "office-convert", "convert", "辦公文件格式互轉",
        "Convert an Office / ODF document to another office format (e.g. docx to odt, xlsx to ods).",
        "/tools/office-convert/convert", "office", 1, 1,
        params={"target": {"type": "string", "enum": ["odt", "docx-2007", "docx-365", "ods", "xlsx-2007", "xlsx-ooxml",
                                                      "odp", "pptx-2007", "pptx-ooxml"],
                           "description": "Target format; must be the same family (text, spreadsheet, presentation)"}},
        required=("target",), output="job",
        keep=(".odt", ".docx", ".ods", ".xlsx", ".odp", ".pptx"),
        output_desc="The converted document saved to the workspace.",
        example={"tool": "office_convert", "files": ["<file_id>"], "params": {"target": "odt"}}),
    McpTool(
        "pdf_watermark", "pdf-watermark", "pdf_edit", "浮水印",
        "Add a text watermark to a PDF (tiled or single, opacity and angle; all, first or last page).",
        "/tools/pdf-watermark/api/pdf-watermark", "pdf", 1, 1,
        params={"text": {"type": "string", "maxLength": 200, "description": "Watermark text"},
                "mode": {"type": "string", "enum": ["tile", "single"], "default": "tile"},
                "opacity": {"type": "number", "minimum": 0.05, "maximum": 1, "default": 0.25},
                "rotation_deg": {"type": "number", "minimum": -180, "maximum": 180, "default": 30},
                "text_color": {"type": "string", "pattern": r"^#[0-9a-fA-F]{6}$", "default": "#cc0000"},
                "text_size_pt": {"type": "number", "minimum": 6, "maximum": 200, "default": 48},
                "page_mode": {"type": "string", "enum": ["all", "first", "last"], "default": "all"}},
        required=("text",), output="file", output_desc="The watermarked PDF saved to the workspace.",
        example={"tool": "pdf_watermark", "files": ["<file_id>"], "params": {"text": "CONFIDENTIAL"}}),
    McpTool(
        "pdf_flatten_annotations", "pdf-annotations-flatten", "pdf_edit", "註解平面化",
        "Bake PDF annotations (comments, highlights, stamps) into the page so they can no longer be edited.",
        "/tools/pdf-annotations-flatten/api/pdf-annotations-flatten", "pdf", 1, 1,
        output="file", output_desc="The flattened PDF saved to the workspace.",
        example={"tool": "pdf_flatten_annotations", "files": ["<file_id>"]}),
    McpTool(
        "pdf_remove_annotations", "pdf-annotations-strip", "pdf_edit", "註解清除",
        "Remove annotations from a PDF: all of them, or only some types / authors.",
        "/tools/pdf-annotations-strip/api/pdf-annotations-strip", "pdf", 1, 1,
        params={"mode": {"type": "string", "enum": ["all", "filter"], "default": "all"},
                "types": {"type": "string", "maxLength": 300, "default": "",
                          "description": "With mode=filter: comma-separated types, e.g. 'Highlight,Ink,Text'"},
                "authors": {"type": "string", "maxLength": 300, "default": "",
                            "description": "With mode=filter: comma-separated author names"}},
        output="file", output_desc="The PDF without those annotations saved to the workspace.",
        example={"tool": "pdf_remove_annotations", "files": ["<file_id>"], "params": {"mode": "filter", "types": "Ink"}}),
    McpTool(
        "pdf_encrypt", "pdf-encrypt", "pdf_edit", "PDF 密碼保護",
        "Protect a PDF with an open password and/or permissions password.",
        "/tools/pdf-encrypt/api/pdf-encrypt", "pdf", 1, 1,
        params={"user_pw": {"type": "string", "maxLength": 128, "default": "", "description": "Password to open the file"},
                "owner_pw": {"type": "string", "maxLength": 128, "default": "",
                             "description": "Permissions password; at least one of the two passwords is required"},
                "algorithm": {"type": "string", "enum": ["aes-256", "aes-128", "rc4-128"], "default": "aes-256"},
                "allow_print": {"type": "boolean", "default": True},
                "allow_print_hq": {"type": "boolean", "default": False, "description": "High-quality printing"},
                "allow_copy": {"type": "boolean", "default": False},
                "allow_modify": {"type": "boolean", "default": False},
                "allow_annotate": {"type": "boolean", "default": False},
                "allow_form": {"type": "boolean", "default": False, "description": "Fill in form fields"},
                "allow_assemble": {"type": "boolean", "default": False,
                                   "description": "Insert, delete or rotate pages"},
                "allow_accessibility": {"type": "boolean", "default": True,
                                        "description": "Screen readers may extract text"}},
        output="file", output_desc="The protected PDF saved to the workspace.",
        example={"tool": "pdf_encrypt", "files": ["<file_id>"], "params": {"user_pw": "<password>"}}),
    McpTool(
        "pdf_decrypt", "pdf-decrypt", "pdf_edit", "PDF 密碼解除",
        "Remove a PDF password: given password, file name, or THSR ticket mode (tries each date in a range).",
        "/tools/pdf-decrypt/api/pdf-decrypt", "pdf", 1, 1,
        params={"password": {"type": "string", "maxLength": 128, "default": ""},
                "use_filename_as_password": {"type": "boolean", "default": False,
                                             "description": "Try the file name (without .pdf) as the password"},
                "thsr_mode": {"type": "boolean", "default": False,
                              "description": "Taiwan High Speed Rail (THSR) e-ticket mode: the open password is the "
                                             "travel date, so every date from thsr_date_from to thsr_date_to is tried"},
                "thsr_date_from": {"type": "string", "maxLength": 10, "default": "",
                                   "pattern": r"^(\d{4}-\d{2}-\d{2})?$",
                                   "description": "With thsr_mode: first date to try, YYYY-MM-DD"},
                "thsr_date_to": {"type": "string", "maxLength": 10, "default": "",
                                 "pattern": r"^(\d{4}-\d{2}-\d{2})?$",
                                 "description": "With thsr_mode: last date to try, YYYY-MM-DD (range at most 800 days)"},
                "thsr_date_format": {"type": "string", "maxLength": 20, "default": "YYYYMMDD",
                                     "pattern": r"^[YyMmDd\-/._ ]+$",
                                     "description": "With thsr_mode: how the date is written in the password; "
                                                    "THSR uses YYYYMMDD"}},
        output="file", default_on=False,
        output_desc="The unprotected PDF saved to the workspace; in THSR mode it is named after the travel date "
                    "(for example 20261108.pdf).",
        example={"tool": "pdf_decrypt", "files": ["<file_id>"],
                 "params": {"thsr_mode": True, "thsr_date_from": "2026-08-01", "thsr_date_to": "2026-10-31"}}),
    McpTool(
        "pdf_n_up", "pdf-nup", "pdf_edit", "多頁合併",
        "Put several PDF pages on one sheet (2-up, 4-up, ...).",
        "/tools/pdf-nup/api/pdf-nup", "pdf", 1, 1,
        params={"cols": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2},
                "rows": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
                "paper": {"type": "string", "enum": ["a4", "a3", "a5", "letter", "legal", "b4", "b5", "custom"],
                          "default": "a4", "description": "'custom' = use paper_w_mm / paper_h_mm"},
                "paper_w_mm": {"type": "number", "minimum": 20, "maximum": 2000, "default": 210},
                "paper_h_mm": {"type": "number", "minimum": 20, "maximum": 2000, "default": 297},
                "orientation": {"type": "string", "enum": ["auto", "portrait", "landscape"], "default": "auto"},
                "margin_mm": {"type": "number", "minimum": 0, "maximum": 100, "default": 10},
                "gap_mm": {"type": "number", "minimum": 0, "maximum": 100, "default": 5,
                           "description": "Space between cells"},
                "direction": {"type": "string", "enum": ["ltr", "ttb"], "default": "ltr",
                              "description": "'ltr' = across then down; 'ttb' = down then across"},
                "reverse": {"type": "boolean", "default": False, "description": "Place pages in reverse order"},
                "auto_rotate": {"type": "boolean", "default": True, "description": "Rotate pages to fit each cell"},
                "page_align": {"type": "string", "enum": ["center", "left-top"], "default": "center"},
                "show_border": {"type": "boolean", "default": False},
                "border_width_pt": {"type": "number", "minimum": 0.1, "maximum": 10, "default": 0.5},
                "border_color": {"type": "string", "pattern": r"^#[0-9a-fA-F]{6}$", "default": "#94a3b8"},
                "show_page_num": {"type": "boolean", "default": False,
                                  "description": "Print the original page number in each cell"},
                "page_num_format": {"type": "string", "maxLength": 40, "default": "{n}",
                                    "description": "{n} = original page number, {t} = total pages"},
                "page_num_size_pt": {"type": "number", "minimum": 4, "maximum": 48, "default": 8},
                "pad_blanks": {"type": "boolean", "default": True, "description": "Fill the last sheet with blank cells"},
                "show_crop_marks": {"type": "boolean", "default": False},
                "crop_mark_len_mm": {"type": "number", "minimum": 0, "maximum": 20, "default": 4}},
        output="file", output_desc="The n-up PDF saved to the workspace.",
        example={"tool": "pdf_n_up", "files": ["<file_id>"], "params": {"cols": 2, "rows": 2}}),
    McpTool(
        "pdf_clean_metadata", "pdf-metadata", "pdf_edit", "中繼資料清除",
        "Remove metadata (document info, XMP; optionally bookmarks, annotations, form fields) from a PDF.",
        "/tools/pdf-metadata/api/pdf-metadata", "pdf", 1, 1,
        params={"clear_info": {"type": "boolean", "default": True}, "clear_xmp": {"type": "boolean", "default": True},
                "clear_toc": {"type": "boolean", "default": False}, "clear_annots": {"type": "boolean", "default": False},
                "clear_forms": {"type": "boolean", "default": False}},
        output="file", output_desc="The cleaned PDF saved to the workspace.",
        example={"tool": "pdf_clean_metadata", "files": ["<file_id>"]}),
    McpTool(
        "pdf_reorder_pages", "pdf-pages", "pdf_edit", "頁面整理",
        "Reorder, pick or delete PDF pages.",
        "/tools/pdf-pages/api/pdf-pages", "pdf", 1, 1,
        params={"mode": {"type": "string", "enum": ["reorder", "drop"], "default": "reorder",
                         "description": "'reorder' = keep these pages in this order; 'drop' = delete these pages"},
                "spec": {"type": "string", "maxLength": 400, "pattern": r"^[0-9,;\- ]*$", "default": "",
                         "description": "Pages and ranges, e.g. '3,1-2,5-4'"}},
        output="file", output_desc="The rearranged PDF saved to the workspace.",
        example={"tool": "pdf_reorder_pages", "files": ["<file_id>"], "params": {"mode": "drop", "spec": "2"}}),
    McpTool(
        "pdf_page_size", "pdf-page-size", "pdf_edit", "頁面尺寸統一",
        "Change every page of a PDF to a paper size (A4, A3, Letter...), scaling or centering the content.",
        "/tools/pdf-page-size/api/pdf-page-size", "pdf_or_office", 1, 1,
        params={"paper": {"type": "string", "enum": ["a3", "a4", "a5", "b4", "b5", "letter", "legal", "tabloid", "custom"],
                          "default": "a4", "description": "'custom' = use custom_w_mm / custom_h_mm"},
                "custom_w_mm": {"type": "number", "minimum": 20, "maximum": 2000, "default": 210},
                "custom_h_mm": {"type": "number", "minimum": 20, "maximum": 2000, "default": 297},
                "orientation": {"type": "string", "enum": ["auto", "portrait", "landscape"], "default": "auto"},
                "fit": {"type": "string", "enum": ["scale", "center", "crop"], "default": "scale"},
                "align": {"type": "string", "enum": ["center", "top-left"], "default": "center",
                          "description": "Where the content sits on the new page"},
                "keep_same": {"type": "boolean", "default": True,
                              "description": "Leave pages that already have the target size untouched"}},
        output="file", generic_names=("resized.pdf",), output_desc="The resized PDF saved to the workspace.",
        example={"tool": "pdf_page_size", "files": ["<file_id>"], "params": {"paper": "a4"}}),
    McpTool(
        "pdf_bookmarks", "pdf-bookmark", "pdf_edit", "書籤與目錄",
        "Add bookmarks to a PDF: your own list, detected headings, or one per file when merging several files.",
        "/tools/pdf-bookmark/api/pdf-bookmark", "pdf_or_office", 1, 20, file_field="files",
        params={"bookmarks": {"type": "string", "maxLength": 100000, "default": "", "json_type": "array",
                              "description": "Your own bookmarks as a JSON array of {\"title\", \"page\" (1-based), "
                                             "\"level\" (1-3)}; replaces the automatic ones"},
                "auto": {"type": "boolean", "default": False, "description": "Detect headings by font size (one file only)"},
                "toc_page": {"type": "boolean", "default": False, "description": "Insert a clickable table of contents as page 1"},
                "toc_title": {"type": "string", "maxLength": 100, "default": "目錄",
                              "description": "Title of the table of contents page"},
                "toc_at": {"type": "integer", "minimum": 1, "maximum": 99999, "default": 1,
                           "description": "Insert the contents page before this page (1-based)"},
                "toc_dots": {"type": "boolean", "default": True, "description": "Dot leaders between titles and pages"},
                "toc_max_level": {"type": "integer", "minimum": 1, "maximum": 3, "default": 3}},
        json_fields=("bookmarks",),
        output="file", generic_names=("bookmarked.pdf",), output_desc="The bookmarked PDF saved to the workspace.",
        example={"tool": "pdf_bookmarks", "files": ["<file_id>"], "params": {"auto": True}}),
    McpTool(
        "pdf_extract_images", "pdf-extract-images", "extract", "擷取圖片",
        "Extract the embedded images of a PDF as separate image files.",
        "/tools/pdf-extract-images/api/pdf-extract-images", "pdf", 1, 1,
        output="files", keep=(".png", ".jpg", ".jpeg", ".tif", ".tiff"),
        output_desc="One image file per embedded image saved to the workspace.",
        example={"tool": "pdf_extract_images", "files": ["<file_id>"]}),
    McpTool(
        "pdf_ocr", "pdf-ocr", "extract", "OCR 文字辨識",
        "Make a scanned PDF or image searchable by adding an OCR text layer (no AI; Tesseract / EasyOCR).",
        "/tools/pdf-ocr/api/pdf-ocr", "pdf_or_image", 1, 1,
        params={"lang": {"type": "string", "pattern": r"^[a-z_]{3,10}(\+[a-z_]{3,10}){0,4}$", "default": "chi_tra+eng",
                         "description": "Tesseract language codes joined with '+', e.g. 'chi_tra+eng', 'jpn', 'eng'"},
                "dpi": {"type": "integer", "minimum": 72, "maximum": 600, "default": 300},
                "skip_pages_with_text": {"type": "boolean", "default": True}},
        output="job", output_desc="The searchable PDF saved to the workspace (OCR can take a while per page).",
        example={"tool": "pdf_ocr", "files": ["<file_id>"], "params": {"lang": "chi_tra+eng"}}),
    McpTool(
        "text_diff", "text-diff", "compare", "文字差異比對",
        "Compare two texts line by line and return what was added, removed and changed (no AI).",
        "/tools/text-diff/api/text-diff", None, 0, 0, body="json",
        params={"text_a": {"type": "string", "maxLength": 1000000, "description": "Old text"},
                "text_b": {"type": "string", "maxLength": 1000000, "description": "New text"}},
        required=("text_a", "text_b"), output="json",
        output_desc="Totals and the line-by-line differences as JSON.",
        example={"tool": "text_diff", "params": {"text_a": "old line", "text_b": "new line"}}),
    McpTool(
        "document_diff", "doc-diff", "compare", "文件差異比對",
        "Compare two documents (PDF or Office / ODF) page by page and return the differences (no AI).",
        "/tools/doc-diff/api/doc-diff", "pdf_or_office", 2, 2, file_fields=("file_a", "file_b"),
        output="json", json_strip=("marks", "size"),
        output_desc="Totals, metadata changes and the per-page differences as JSON (first file = old, second = new).",
        example={"tool": "document_diff", "files": ["<old_file_id>", "<new_file_id>"]}),
    # ---- v1.17.37 第三批：原本「之後開放」的 9 支＋有 LLM 選項的 5 支（LLM 一律不用）
    McpTool(
        "pdf_to_office", "pdf-to-office", "convert", "PDF 轉文書檔",
        "Convert a PDF to an editable Word (.docx) or OpenDocument (.odt) file (no AI).",
        "/tools/pdf-to-office/convert", "pdf", 1, 1,
        params={"output_format": {"type": "string", "enum": ["docx", "odt"], "default": "docx"},
                "engine": {"type": "string", "enum": ["pdf2docx-refine", "jtdt-reform", "jtdt-layout"],
                           "default": "pdf2docx-refine",
                           "description": "'pdf2docx-refine' = flowing text (default); 'jtdt-reform' = own layout rebuild; "
                                          "'jtdt-layout' = near 1:1 layout with page-anchored text boxes (forms, multi-column)"},
                "enable_postprocess": {"type": "boolean", "default": False,
                                       "description": "Only with pdf2docx-refine: apply the extra clean-up pass"}},
        output="job", keep=(".docx", ".odt"),
        output_desc="The Word / ODT document saved to the workspace (conversion can take a while).",
        example={"tool": "pdf_to_office", "files": ["<file_id>"], "params": {"output_format": "docx"}}),
    McpTool(
        "pdf_to_slides", "pdf-to-slides", "convert", "PDF 轉簡報",
        "Convert a PDF to a presentation, one page per slide (PowerPoint .pptx or OpenDocument .odp).",
        "/tools/pdf-to-slides/convert", "pdf", 1, 1,
        params={"output_format": {"type": "string", "enum": ["pptx", "odp"], "default": "pptx"}},
        output="job", keep=(".pptx", ".odp"),
        output_desc="The presentation saved to the workspace (conversion can take a while).",
        example={"tool": "pdf_to_slides", "files": ["<file_id>"], "params": {"output_format": "pptx"}}),
    McpTool(
        "document_straighten", "doc-straighten", "pdf_edit", "掃描修正",
        "Straighten scans and phone photos of documents: deskew, perspective-correct and even out shadows.",
        "/tools/doc-straighten/api/doc-straighten", "any_doc", 1, 1,
        params={"dpi": {"type": "integer", "enum": [150, 200, 300], "default": 200},
                "detect_quad": {"type": "boolean", "default": True,
                                "description": "Find the paper edges and correct the perspective"},
                "enhance": {"type": "boolean", "default": True, "description": "Flatten uneven background and shadows"},
                "binarize": {"type": "boolean", "default": False,
                             "description": "Black and white only (smaller file; thin strokes may be lost)"},
                "size_w": {"type": "string", "maxLength": 10, "pattern": r"^(\d{1,5}(\.\d{1,2})?)?$", "default": "",
                           "description": "Output page width (with size_h and size_unit); empty = as straightened"},
                "size_h": {"type": "string", "maxLength": 10, "pattern": r"^(\d{1,5}(\.\d{1,2})?)?$", "default": ""},
                "size_unit": {"type": "string", "enum": ["px", "mm"], "default": "px"}},
        output="file", output_desc="The straightened PDF saved to the workspace.",
        example={"tool": "document_straighten", "files": ["<file_id>"]}),
    McpTool(
        "pdf_add_border", "pdf-border", "pdf_edit", "頁面加框",
        "Draw a border around the pages of a PDF or office document (line style, colour, rounded, double, shadow).",
        "/tools/pdf-border/api/pdf-border", "pdf_or_office", 1, 1,
        params={"mode": {"type": "string", "enum": ["page", "content"], "default": "page",
                         "description": "'page' = inset from the page edge; 'content' = hug the page content"},
                "margin_mm": {"type": "number", "minimum": 0, "maximum": 50, "default": 5},
                "width_pt": {"type": "number", "minimum": 0.1, "maximum": 20, "default": 1.5},
                "color": {"type": "string", "pattern": r"^#[0-9A-Fa-f]{6}$", "default": "#333333"},
                "style": {"type": "string", "enum": ["solid", "dashed", "dotted"], "default": "solid"},
                "radius_mm": {"type": "number", "minimum": 0, "maximum": 50, "default": 0},
                "opacity": {"type": "number", "minimum": 0, "maximum": 1, "default": 1},
                "double": {"type": "boolean", "default": False, "description": "Inner and outer lines (certificate style)"},
                "double_gap_mm": {"type": "number", "minimum": 0, "maximum": 20, "default": 1.5},
                "shadow": {"type": "boolean", "default": False},
                "shadow_color": {"type": "string", "pattern": r"^#[0-9A-Fa-f]{6}$", "default": "#000000"},
                "shadow_blur_mm": {"type": "number", "minimum": 0, "maximum": 10, "default": 1.2},
                "shadow_offset_mm": {"type": "number", "minimum": 0, "maximum": 10, "default": 0.6},
                "shadow_opacity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.25},
                "pages": {"type": "string", "maxLength": 200, "pattern": r"^(\d+(-\d+)?(,\d+(-\d+)?)*)?$",
                          "default": "", "description": "1-based pages/ranges, e.g. '1,3,5-8'; empty = all pages"},
                "skip_first": {"type": "boolean", "default": False, "description": "No border on the first page"}},
        output="file", output_desc="The PDF with borders saved to the workspace.",
        example={"tool": "pdf_add_border", "files": ["<file_id>"], "params": {"style": "dashed", "radius_mm": 3}}),
    McpTool(
        "pdf_extract_attachments", "pdf-attachments", "extract", "PDF 附件萃取",
        "Extract the files embedded in a PDF (attachments).",
        "/tools/pdf-attachments/api/pdf-attachments", "pdf", 1, 1,
        output="files", keep=WORKSPACE_KEEP,
        output_desc="Each attachment saved to the workspace; types the workspace does not accept are skipped. "
                    "Fails with NOT_FOUND when the PDF has no attachments.",
        example={"tool": "pdf_extract_attachments", "files": ["<file_id>"]}),
    McpTool(
        "pdf_hidden_content_scan", "pdf-hidden-scan", "extract", "隱藏內容掃描",
        "Scan a PDF for hidden or active content: JavaScript, embedded files, links, hidden text, 3D.",
        "/tools/pdf-hidden-scan/api/pdf-hidden-scan", "pdf", 1, 1,
        output="json", output_desc="Findings per category and totals as JSON.",
        example={"tool": "pdf_hidden_content_scan", "files": ["<file_id>"]}),
    McpTool(
        "scan_merge", "scan-merge", "pdf_edit", "掃描拼合",
        "Combine several scanned pieces (receipts, cards, both sides of an ID) onto one A4 page.",
        "/tools/scan-merge/api/scan-merge", "pdf_or_image", 1, 20, file_field="files",
        params={"layout": {"type": "string", "enum": ["original", "auto"], "default": "original",
                           "description": "'original' = keep each piece's position (may overlap); "
                                          "'auto' = arrange in upload order without overlap"},
                "whiten": {"type": "boolean", "default": True, "description": "Whiten the grey scan background"}},
        output="file", generic_names=("scan-merge.pdf",),
        output_desc="One A4 PDF saved to the workspace.",
        example={"tool": "scan_merge", "files": ["<file_id_1>", "<file_id_2>"], "params": {"layout": "auto"}}),
    McpTool(
        "text_list_process", "text-list", "extract", "清單處理",
        "Process a one-item-per-line list: trim, de-duplicate, sort, filter, change case, head / tail, wrap.",
        "/tools/text-list/api/text-list", None, 0, 0, body="json",
        params={"text": {"type": "string", "maxLength": 2000000, "description": "One item per line"},
                "ops": {"type": "string", "maxLength": 20000, "default": "", "json_type": "array",
                        "description": "JSON array of steps applied in order, e.g. "
                                       "'[{\"op\":\"trim\"},{\"op\":\"dedup\"},{\"op\":\"sort\",\"natural\":true}]'. "
                                       "Ops: trim, drop_empty, dedup (keep: first|last, case_insensitive), "
                                       "sort (order: asc|desc, natural, case_insensitive), reverse, shuffle (seed), "
                                       "case (mode: lower|upper|title), filter (mode: include|exclude, pattern, regex, "
                                       "case_insensitive), head (n), tail (n), wrap (prefix, suffix)"}},
        required=("text",), json_fields=("ops",), output="json",
        output_desc="The resulting lines, counts and the steps applied as JSON.",
        example={"tool": "text_list_process",
                 "params": {"text": "b\na\nb", "ops": '[{"op":"dedup"},{"op":"sort"}]'}}),
    McpTool(
        "transit_proof_parse", "transit-proof", "extract", "乘車證明整理",
        "Read Taiwan Railway, Taiwan High Speed Rail and Uber ride receipts (PDF) into date, route and fare.",
        "/tools/transit-proof/api/transit-proof", "pdf", 1, 50, file_field="files",
        output="json", output_desc="One entry per receipt (date, transport, from / to, fare...) and the files "
                                   "that could not be read, as JSON.",
        example={"tool": "transit_proof_parse", "files": ["<file_id_1>", "<file_id_2>"]}),
    McpTool(
        "pdf_list_annotations", "pdf-annotations", "extract", "註解整理",
        "List the annotations of a PDF (comments, highlights, ink...) with page, author, time and text (no AI).",
        "/tools/pdf-annotations/api/pdf-annotations", "pdf", 1, 1,
        output="json", json_strip=("upload_id",), output_desc="A summary and every annotation as JSON.",
        example={"tool": "pdf_list_annotations", "files": ["<file_id>"]}),
    McpTool(
        "einvoice_scan", "einvoice-scan", "extract", "電子發票處理",
        "Read Taiwan e-invoice QR codes from an image or PDF: invoice number, date, amount, seller UBN, items (no AI).",
        "/tools/einvoice-scan/api/einvoice-scan", "pdf_or_image", 1, 1,
        output="json", json_strip=("buffer",),
        output_desc="The invoices found as JSON; they are also added to the user's e-invoice list on the tool page.",
        example={"tool": "einvoice_scan", "files": ["<file_id>"]}),
    McpTool(
        "document_deidentify", "doc-deident", "pdf_edit", "文件去識別化",
        "Mask, black out or fake-replace personal data (IDs, phones, e-mail...) in a PDF or office file (no AI).",
        "/tools/doc-deident/api/doc-deident", "pdf_or_office", 1, 1,
        params={"mode": {"type": "string", "enum": ["mask", "redact", "replace"], "default": "mask",
                         "description": "'mask' = same-length asterisks; 'redact' = black boxes, text removed; "
                                        "'replace' = consistent fake values"},
                "doc_lang": {"type": "string", "enum": ["", "zh-Hant", "en", "ja"], "default": "",
                             "description": "Language of the document (selects the rule set); empty = default"},
                "types": {"type": "string", "maxLength": 1000, "pattern": r"^([a-z0-9_]+(,[a-z0-9_]+)*)?$",
                          "default": "", "description": "Comma-separated detector ids; empty = the defaults for "
                                                        "doc_lang. Ids: " + PII_IDS},
                "replacements": {"type": "string", "maxLength": 20000, "default": "",
                                 "description": "Only with mode=replace: JSON object {\"original\": \"new value\"}; "
                                                "others get generated fake values"},
                "valid_checksum": {"type": "boolean", "default": False,
                                   "description": "Only with mode=replace: fake IDs / UBNs pass their check digit"},
                "custom": {"type": "string", "maxLength": 2000, "default": "",
                           "description": "Extra patterns to detect, one per line as 'label | regex'"}},
        output="file", output_desc="The de-identified PDF saved to the workspace.",
        example={"tool": "document_deidentify", "files": ["<file_id>"], "params": {"mode": "redact"}}),
    McpTool(
        "text_deidentify", "text-deident", "extract", "文字去識別化",
        "Find and mask personal data in plain text (ID numbers, phones, e-mail, addresses...; rules only, no AI).",
        "/tools/text-deident/api/text-deident", None, 0, 0, body="json",
        params={"text": {"type": "string", "maxLength": 1000000},
                "mode": {"type": "string", "enum": ["mask", "redact", "fake"], "default": "mask",
                         "description": "'mask' = keep the edges, hide the rest; 'redact' = black blocks; "
                                        "'fake' = consistent fake values"},
                "doc_lang": {"type": "string", "enum": ["", "zh-Hant", "en", "ja"], "default": ""},
                "types": {"type": "string", "maxLength": 1000, "pattern": r"^([a-z0-9_]+(,[a-z0-9_]+)*)?$",
                          "default": "", "description": "Comma-separated detector ids; empty = the defaults for "
                                                        "doc_lang. Ids: " + PII_IDS},
                "custom_regex": {"type": "string", "maxLength": 2000, "default": "",
                                 "description": "Extra patterns to detect, one per line as 'label | regex'"}},
        required=("text",), csv_fields=("types",), output="json",
        output_desc="The processed text and every finding as JSON.",
        example={"tool": "text_deidentify", "params": {"text": "Call 0912-345-678", "mode": "mask"}}),
    McpTool(
        "submission_check", "submission-check", "compare", "送件前檢核",
        "Check documents before submitting: file problems and inconsistent names, numbers, dates (no AI).",
        "/tools/submission-check/api/submission-check", "any_doc", 1, 50, file_field="files",
        params={"case_name": {"type": "string", "maxLength": 200, "default": ""},
                "main_entity_name": {"type": "string", "maxLength": 200, "default": "",
                                     "description": "The company / person submitting (checked against every file)"},
                "main_entity_identifier": {"type": "string", "maxLength": 50, "default": "",
                                           "description": "Its UBN or ID number"},
                "counterparty_name": {"type": "string", "maxLength": 200, "default": "",
                                      "description": "Who the documents are submitted to"},
                "case_number": {"type": "string", "maxLength": 100, "default": ""},
                "deadline": {"type": "string", "maxLength": 10, "pattern": r"^(\d{4}-\d{2}-\d{2})?$",
                             "default": "", "description": "YYYY-MM-DD"}},
        force={"allow_llm": False}, output="job_json",
        output_desc="The check report as JSON (per-file and cross-file findings, layer status); the case also "
                    "appears on the tool page.",
        example={"tool": "submission_check", "files": ["<file_id_1>", "<file_id_2>"],
                 "params": {"main_entity_name": "Example Co., Ltd."}}),
    # ---- v1.17.38 AI 用印：只用資產庫裡經 AI 授權的章；條件與確認流程在 core.ai_stamp
    McpTool(
        "stamp_list", "pdf-stamp", "stamp", "用印與簽名",
        "List the seals and signatures you may use through AI (name, default position; no images).",
        "/api/ai-stamp/seals", None, 0, 0, method="GET", stamp=True, default_on=False, any_tools=("pdf-stamp", "pdf-seam-stamp"),
        params={"tool": {"type": "string", "enum": ["pdf-stamp", "pdf-seam-stamp"], "default": "pdf-stamp",
                         "description": "pdf-stamp = seals and signatures; pdf-seam-stamp = seals for seam stamps"}},
        output="json", output_desc="Seals with id, name, type, default_position_mm, needs_confirmation and "
                                   "remaining_today (null = no daily limit).",
        example={"tool": "stamp_list"}),
    McpTool(
        "pdf_stamp", "pdf-stamp", "stamp", "用印與簽名",
        "Stamp approved seals or signatures onto a PDF; some need the owner's confirmation on the web first.",
        "/tools/pdf-stamp/api/ai-stamp", "pdf", 1, 1, stamp=True, default_on=False,
        params={"placements_json": {"type": "string", "maxLength": 50000, "json_type": "array",
                                    "description": "JSON array; each item {\"asset_id\" (from stamp_list), \"page\" "
                                                   "(0-based), optional \"x_mm\", \"y_mm\", \"width_mm\", "
                                                   "\"height_mm\", \"rotation_deg\"}; missing position = the "
                                                   "seal's default. Only seals from stamp_list; no uploaded images"}},
        required=("placements_json",), json_fields=("placements_json",), output="stamp",
        output_desc="The stamped PDF saved to the workspace, or status 'pending' with a request_id when the "
                    "owner must approve it on the web page (then check with stamp_request_status).",
        example={"tool": "pdf_stamp", "files": ["<file_id>"],
                 "params": {"placements_json": '[{"asset_id": "<id from stamp_list>", "page": 0}]'}}),
    McpTool(
        "pdf_seam_stamp", "pdf-seam-stamp", "stamp", "騎縫章",
        "Add a seam seal across page edges with an approved seal; may need the owner's confirmation first.",
        "/tools/pdf-seam-stamp/api/ai-seam-stamp", "pdf_or_office", 1, 1, stamp=True, default_on=False,
        params={"asset_id": {"type": "string", "pattern": r"^[0-9a-f]{32}$",
                             "description": "Seal id from stamp_list with tool=pdf-seam-stamp"},
                "mode": {"type": "string", "enum": ["side", "spread"], "default": "side",
                         "description": "'side' = across the right / left edge of stacked pages; 'spread' = across facing pages"},
                "group": {"type": "integer", "minimum": 0, "maximum": 9999, "default": 2,
                          "description": "Pages per seal; 0 = one seal across the whole document"},
                "edge": {"type": "string", "enum": ["right", "left"], "default": "right"},
                "size_mm": {"type": "number", "minimum": 8, "maximum": 120, "default": 40},
                "offset_mm": {"type": "number", "minimum": 0, "maximum": 60, "default": 3},
                "pos_mm": {"type": "number", "minimum": -200, "maximum": 200, "default": 0,
                           "description": "Vertical position; 0 = centred, positive = lower"},
                "angle_deg": {"type": "number", "minimum": -45, "maximum": 45, "default": 0},
                "opacity": {"type": "number", "minimum": 0.05, "maximum": 1, "default": 1},
                "jitter_pos": {"type": "boolean", "default": False},
                "jitter_angle": {"type": "boolean", "default": False},
                "seed": {"type": "integer", "minimum": 0, "maximum": 2147483647, "default": 0}},
        required=("asset_id",), output="stamp",
        output_desc="The seam-stamped PDF saved to the workspace, or status 'pending' with a request_id.",
        example={"tool": "pdf_seam_stamp", "files": ["<file_id>"], "params": {"asset_id": "<id from stamp_list>"}}),
    McpTool(
        "stamp_request_status", "pdf-stamp", "stamp", "用印與簽名",
        "Check an AI stamping request waiting for confirmation; once approved it returns the workspace file.",
        "/api/ai-stamp/requests/{request_id}", None, 0, 0, method="GET", stamp=True, default_on=False,
        any_tools=("pdf-stamp", "pdf-seam-stamp"),
        params={"request_id": {"type": "string", "pattern": r"^[0-9a-f]{32}$"}},
        required=("request_id",), output="json",
        output_desc="status: pending / approved / rejected / expired / void (conditions changed before approval); when approved, file.file_id is in the workspace.",
        example={"tool": "stamp_request_status", "params": {"request_id": "<request_id>"}}),
    # ---- v1.17.37 第四批：原本「第二期」的表單自動填寫、會議錄音轉逐字稿，與 PDF 編輯器（不含圖片）
    McpTool(
        "pdf_fill_companies", "pdf-fill", "lookup", "表單自動填寫：公司清單",
        "List the company profiles that pdf_fill_form can use (id and name only).",
        "/tools/pdf-fill/api/companies", None, 0, 0, method="GET", default_on=False,
        output="json", output_desc="[{id, name, is_active}]; is_active is used when company_id is empty.",
        example={"tool": "pdf_fill_companies"}),
    McpTool(
        "pdf_fill_form", "pdf-fill", "pdf_edit", "表單自動填寫",
        "Fill in a supplier / vendor form (PDF or office file) with a saved company profile (rules only, no AI).",
        "/tools/pdf-fill/api/pdf-fill", "pdf_or_office", 1, 1, default_on=False,
        params={"company_id": {"type": "string", "maxLength": 64, "pattern": r"^[A-Za-z0-9_-]*$", "default": "",
                               "description": "Company profile id from pdf_fill_companies; empty = the active one"},
                "font_id": {"type": "string", "maxLength": 64, "pattern": r"^[A-Za-z0-9_.-]+$", "default": "auto",
                            "description": "Font for the filled text; 'auto' picks a CJK font"}},
        output="file", output_desc="The filled PDF saved to the workspace. It contains the company's data "
                                   "(addresses, bank account...).",
        example={"tool": "pdf_fill_form", "files": ["<file_id>"], "params": {"company_id": "<id>"}}),
    McpTool(
        "pdf_edit", "pdf-editor", "pdf_edit", "PDF 編輯器",
        "Draw text, boxes, lines, arrows, highlights, white-out and notes onto PDF pages (no images or stamps).",
        "/tools/pdf-editor/api/pdf-editor", "pdf", 1, 1,
        params={"model": {"type": "string", "maxLength": 200000, "json_type": "object",
                          "description": "JSON object {\"pages\": [{\"page\": 0-based index, \"objects\": [...]}]}. "
                                         "Units are pt from the top-left corner. Every object has type and x, y, w, h. "
                                         "text: text, font_size, color (#RRGGBB), bold, italic, underline; "
                                         "rect / ellipse: stroke, fill, stroke_width; line / arrow: x, y, w, h as the "
                                         "two end points' box, stroke, stroke_width; highlight: color; whiteout: covers "
                                         "the area in white; sticky: note; pencil: points [[x, y], ...]. Optional angle "
                                         "(clockwise degrees). Types: " + ", ".join(EDITOR_OBJECT_TYPES)}},
        required=("model",), json_fields=("model",), check=lambda p: _check_editor_model(p),
        output="file", output_desc="The edited PDF saved to the workspace.",
        example={"tool": "pdf_edit", "files": ["<file_id>"], "params": {"model": '{"pages": [{"page": 0, "objects": '
                 '[{"type": "text", "x": 72, "y": 72, "w": 200, "h": 20, "text": "Reviewed", "font_size": 14, '
                 '"color": "#cc0000"}]}]}'}}),
    McpTool(
        "meeting_transcribe", "meeting-transcribe", "extract", "會議錄音轉逐字稿",
        "Transcribe a meeting recording with speaker labels (speech service; long recordings take minutes).",
        "/tools/meeting-transcribe/api/meeting-transcribe", "audio", 1, 1, default_on=False,
        params={"language": {"type": "string", "maxLength": 16, "pattern": r"^[A-Za-z_-]+$", "default": "auto",
                             "description": "'auto' or a BCP-47 code such as zh-Hant, en, ja"},
                "num_speakers": {"type": "integer", "minimum": 0, "maximum": 30, "default": 0,
                                 "description": "0 = let the service decide (recommended)"},
                "terms": {"type": "string", "maxLength": 20000, "default": "",
                          "description": "Names and terms, one per line; 'misheard -> correct' lines fix known "
                                         "mishearings; other lines are meeting context"}},
        output="transcript",
        output_desc="A readable transcript (.txt) and the full result (.json) saved to the workspace; "
                    "the first part of the text is returned (read the rest with workspace_read_text).",
        example={"tool": "meeting_transcribe", "files": ["<audio_id>"], "params": {"language": "zh-Hant"}}),
)

NAMES = tuple(t.name for t in TOOLS)
BY_NAME = {t.name: t for t in TOOLS}
CATEGORIES = {"convert": "Format conversion", "pdf_edit": "PDF editing", "extract": "Text and statistics",
              "compare": "Comparison", "lookup": "Lookups", "stamp": "Stamping (AI stamping)"}

#: 每一支本站工具在 MCP 的歸類。新工具一定要在這裡決定（測試會檢查），不可以預設開放。
#: open＝已在 TOOLS；later＝不用 LLM、之後開放；llm_optional＝有 LLM 選項，開放時要強制關掉；
#: excluded＝不開放（LLM 是核心、操作設備、互動式、管理功能）；sensitive＝有法律效果，另有規則（用印）。
CLASSIFIED: dict[str, str] = {
    # 不用 LLM 的工具全部已開放（在 TOOLS 裡）；有 LLM 選項的也已開放，LLM 一律關掉（見各自的 force / 說明）
    "official-doc": "excluded", "translate-doc": "excluded", "doc-translate": "excluded",
    "meeting-summary": "excluded", "mfp-fetch": "excluded",
    # 加密紙本備份處理的就是秘密，而且建立 / 還原都只在瀏覽器裡（主機不碰秘密）—— 永遠不經 AI
    "paper-backup": "excluded",
    "pdf-stamp": "sensitive", "pdf-seam-stamp": "sensitive",
    # 已開放的放最後：蓋過上面「之後開放」的歸類（AI 用印那幾支維持「另有規則」）
    **{t.tool_id: "open" for t in TOOLS if not t.stamp},
}


# --------------------------------------------------------------------------- 參數檢查

class ParamError(ValueError):
    pass


def validate(tool: McpTool, params: Any) -> dict:
    """照登記表檢查參數，補上預設值；不認得的參數一律拒絕。回乾淨的參數（只有登記過的欄位）。"""
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ParamError("params must be an object")
    unknown = sorted(set(params) - set(tool.params))
    if unknown:
        allowed = ", ".join(sorted(tool.params)) or "(none)"
        raise ParamError(f"unknown parameter(s): {', '.join(unknown)}; allowed: {allowed}")
    out: dict = {}
    for key, spec in tool.params.items():
        if key not in params or params[key] is None:
            if key in tool.required:
                raise ParamError(f"missing required parameter: {key}")
            if "default" in spec:
                out[key] = spec["default"]
            continue
        out[key] = _check(key, spec, params[key])
        if key in tool.json_fields and out[key]:
            try:
                parsed = json.loads(out[key])
            except ValueError:
                raise ParamError(f"{key} must be valid JSON ({spec.get('description', '')})") from None
            want = spec.get("json_type", "array")
            if (want == "array") != isinstance(parsed, list) or (want == "object") != isinstance(parsed, dict):
                raise ParamError(f"{key} must be a JSON {want}")
    if tool.check:
        tool.check(out)
    return out


def _check_editor_model(params: dict) -> None:
    model = json.loads(params.get("model") or "{}")
    pages = model.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ParamError("model needs a non-empty 'pages' array")
    for pg in pages:
        if not isinstance(pg, dict) or not isinstance(pg.get("page"), int) or pg["page"] < 0:
            raise ParamError("every page needs 'page' (0-based integer) and 'objects'")
        objs = pg.get("objects")
        if not isinstance(objs, list):
            raise ParamError("every page needs an 'objects' array")
        for o in objs:
            if not isinstance(o, dict) or o.get("type") not in EDITOR_OBJECT_TYPES:
                raise ParamError("object type must be one of: " + ", ".join(EDITOR_OBJECT_TYPES)
                                 + " (images, stamps and signatures are not available through MCP)")
            if o.get("asset_id") or o.get("existing_src") or o.get("original_bbox"):
                raise ParamError("asset_id / existing_src / original_bbox are not available through MCP")


def to_api(tool: McpTool, params: dict) -> dict:
    """檢查過的參數 → 原本的 API 要的型別（JSON 字串轉回陣列 / 物件、逗號字串轉成清單）。"""
    out = dict(params)
    for k in tool.json_fields:
        if k in out:
            out[k] = json.loads(out[k]) if out[k] else None
            if out[k] is None:
                del out[k]
    for k in tool.csv_fields:
        if k in out:
            items = [x.strip() for x in str(out[k] or "").split(",") if x.strip()]
            if items:
                out[k] = items
            else:
                del out[k]
    return out


def _check(key: str, spec: dict, v: Any) -> Any:
    t = spec.get("type")
    if t == "integer":
        if isinstance(v, bool) or not isinstance(v, int):
            if isinstance(v, str) and re.fullmatch(r"-?\d{1,9}", v.strip()):
                v = int(v)
            elif isinstance(v, float) and v.is_integer():
                v = int(v)
            else:
                raise ParamError(f"{key} must be an integer")
    elif t == "number":
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            try:
                v = float(str(v))
            except ValueError:
                raise ParamError(f"{key} must be a number") from None
        # NaN 跟任何數比較都是 False，下面的上下限檢查擋不住；無限大也不是合理的參數
        if not math.isfinite(v):
            raise ParamError(f"{key} must be a finite number")
    elif t == "boolean":
        if isinstance(v, str) and v.strip().lower() in ("true", "false"):
            v = v.strip().lower() == "true"
        elif not isinstance(v, bool):
            raise ParamError(f"{key} must be true or false")
    elif t == "string":
        if not isinstance(v, str):
            raise ParamError(f"{key} must be a string")
        if len(v) > spec.get("maxLength", 1000):
            raise ParamError(f"{key} is longer than {spec.get('maxLength', 1000)} characters")
        if "\x00" in v:
            raise ParamError(f"{key} contains a NUL character")
        if spec.get("pattern") and not re.fullmatch(spec["pattern"], v):
            raise ParamError(f"{key} has an invalid format ({spec.get('description', spec['pattern'])})")
    if "enum" in spec and v not in spec["enum"]:
        raise ParamError(f"{key} must be one of: {', '.join(map(str, spec['enum']))}")
    if "minimum" in spec and v < spec["minimum"]:
        raise ParamError(f"{key} must be >= {spec['minimum']}")
    if "maximum" in spec and v > spec["maximum"]:
        raise ParamError(f"{key} must be <= {spec['maximum']}")
    return v


def describe(tool: McpTool) -> dict:
    """`jtdt_describe` 回的完整說明（只在模型需要時才給，不放進 tools/list）。"""
    files = None
    if tool.inputs:
        n = (f"exactly {tool.min_files}" if tool.min_files == tool.max_files
             else f"{tool.min_files} to {tool.max_files}")
        files = {"count": n, "kind": KIND_TEXT[tool.inputs], "extensions": list(KINDS[tool.inputs]),
                 "how": "Workspace file ids (from workspace_list, workspace_upload_url or a previous result)."}
    return {
        "tool": tool.name, "title": tool.title, "category": tool.category, "summary": tool.summary,
        "files": files,
        "params": {"type": "object", "properties": tool.params, "required": list(tool.required),
                   "additionalProperties": False},
        "output": tool.output_desc, "example": tool.example,
    }

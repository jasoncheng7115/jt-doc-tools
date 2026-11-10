"""MCP 開放的工具登記表 —— **哪些工具、參數、怎麼呼叫原本的 API、結果怎麼處理，只寫在這裡**。

* `name` 是 MCP 上的名稱（英文小寫加底線，三家客戶端都吃得下）；`tool_id` 是本站的工具代號（權限照它）。
* 說明一律英文、短：模型讀英文最一致，也省上下文；畫面上的中文名稱另外有。
* `params` 是 JSON Schema 的子集（type / enum / minimum / maximum / pattern / maxLength / default），
  伺服器端照這份檢查（`validate`），錯了回清楚的錯誤讓模型自己修。
* 沒有列在這裡的工具，MCP 一律叫不到；測試檢查每一支載入的工具都有歸類（`CLASSIFIED`）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

#: 輸入檔案的種類 → 允許的副檔名（工作區記錄的 ext）
PDF = (".pdf",)
OFFICE = (".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".pptx", ".ppt", ".odp", ".odg")
TEXT = (".txt", ".md", ".csv", ".json")
IMAGE = (".png", ".jpg", ".jpeg", ".tif", ".tiff")
MARKDOWN = (".md", ".txt")
KINDS = {"pdf": PDF, "office": OFFICE, "pdf_or_office": PDF + OFFICE, "doc_or_text": PDF + OFFICE + TEXT,
         "image": IMAGE, "markdown": MARKDOWN, "pdf_or_image": PDF + IMAGE}
KIND_TEXT = {"pdf": "PDF", "office": "Office / ODF document", "pdf_or_office": "PDF or Office / ODF document",
             "doc_or_text": "PDF, Office / ODF document or plain text", "image": "Image (PNG, JPEG, TIFF)",
             "markdown": "Markdown or plain text", "pdf_or_image": "PDF or image (PNG, JPEG, TIFF)"}


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
        "Rotate all or selected pages of a PDF clockwise.",
        "/tools/pdf-rotate/api/pdf-rotate", "pdf", 1, 1,
        params={"angle": {"type": "integer", "enum": [90, 180, 270], "default": 90,
                          "description": "Clockwise degrees"},
                "pages": dict(_PAGES, default="all")},
        output="file", output_desc="The rotated PDF saved to the workspace.",
        example={"tool": "pdf_rotate", "files": ["<file_id>"], "params": {"angle": 90, "pages": "2,4"}}),
    McpTool(
        "pdf_page_numbers", "pdf-pageno", "pdf_edit", "插入頁碼",
        "Add page numbers to every page of a PDF.",
        "/tools/pdf-pageno/api/pdf-pageno", "pdf", 1, 1,
        params={"position": {"type": "string", "enum": ["tl", "tc", "tr", "bl", "bc", "br"], "default": "br",
                             "description": "t/b = top/bottom, l/c/r = left/center/right"},
                "fmt": {"type": "string", "maxLength": 40, "default": "{n} / {N}",
                        "description": "{n} = page number, {N} = total pages"},
                "start": {"type": "integer", "minimum": 1, "maximum": 99999, "default": 1},
                "font_size": {"type": "number", "minimum": 6, "maximum": 48, "default": 11},
                "margin_mm": {"type": "number", "minimum": 0, "maximum": 50, "default": 10},
                "color": {"type": "string", "pattern": r"^#[0-9a-fA-F]{6}$", "default": "#000000"}},
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
        "Add a text watermark to every page of a PDF (tiled or single, with opacity and angle).",
        "/tools/pdf-watermark/api/pdf-watermark", "pdf", 1, 1,
        params={"text": {"type": "string", "maxLength": 200, "description": "Watermark text"},
                "mode": {"type": "string", "enum": ["tile", "single"], "default": "tile"},
                "opacity": {"type": "number", "minimum": 0.05, "maximum": 1, "default": 0.25},
                "rotation_deg": {"type": "number", "minimum": -180, "maximum": 180, "default": 30},
                "text_color": {"type": "string", "pattern": r"^#[0-9a-fA-F]{6}$", "default": "#cc0000"},
                "text_size_pt": {"type": "number", "minimum": 6, "maximum": 200, "default": 48}},
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
                "allow_copy": {"type": "boolean", "default": False}},
        output="file", output_desc="The protected PDF saved to the workspace.",
        example={"tool": "pdf_encrypt", "files": ["<file_id>"], "params": {"user_pw": "<password>"}}),
    McpTool(
        "pdf_decrypt", "pdf-decrypt", "pdf_edit", "PDF 密碼解除",
        "Remove the password from a PDF, given the password.",
        "/tools/pdf-decrypt/api/pdf-decrypt", "pdf", 1, 1,
        params={"password": {"type": "string", "maxLength": 128, "default": ""}},
        output="file", output_desc="The unprotected PDF saved to the workspace.", default_on=False,
        example={"tool": "pdf_decrypt", "files": ["<file_id>"], "params": {"password": "<password>"}}),
    McpTool(
        "pdf_n_up", "pdf-nup", "pdf_edit", "多頁合併",
        "Put several PDF pages on one sheet (2-up, 4-up, ...).",
        "/tools/pdf-nup/api/pdf-nup", "pdf", 1, 1,
        params={"cols": {"type": "integer", "minimum": 1, "maximum": 8, "default": 2},
                "rows": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
                "paper": {"type": "string", "enum": ["a4", "a3", "a5", "letter", "legal", "b4", "b5"], "default": "a4"},
                "orientation": {"type": "string", "enum": ["auto", "portrait", "landscape"], "default": "auto"}},
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
        "/tools/pdf-page-size/api/pdf-page-size", "pdf", 1, 1,
        params={"paper": {"type": "string", "enum": ["a3", "a4", "a5", "b4", "b5", "letter", "legal", "tabloid"], "default": "a4"},
                "orientation": {"type": "string", "enum": ["auto", "portrait", "landscape"], "default": "auto"},
                "fit": {"type": "string", "enum": ["scale", "center", "crop"], "default": "scale"}},
        output="file", generic_names=("resized.pdf",), output_desc="The resized PDF saved to the workspace.",
        example={"tool": "pdf_page_size", "files": ["<file_id>"], "params": {"paper": "a4"}}),
    McpTool(
        "pdf_bookmarks", "pdf-bookmark", "pdf_edit", "書籤與目錄",
        "Add bookmarks to a PDF: detect headings automatically, or merge several PDFs with one bookmark per file.",
        "/tools/pdf-bookmark/api/pdf-bookmark", "pdf", 1, 20, file_field="files",
        params={"auto": {"type": "boolean", "default": False, "description": "Detect headings by font size (one file only)"},
                "toc_page": {"type": "boolean", "default": False, "description": "Insert a clickable table of contents as page 1"},
                "toc_max_level": {"type": "integer", "minimum": 1, "maximum": 3, "default": 3}},
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
)

NAMES = tuple(t.name for t in TOOLS)
BY_NAME = {t.name: t for t in TOOLS}
CATEGORIES = {"convert": "Format conversion", "pdf_edit": "PDF editing", "extract": "Text and statistics",
              "compare": "Comparison", "lookup": "Lookups"}

#: 每一支本站工具在 MCP 的歸類。新工具一定要在這裡決定（測試會檢查），不可以預設開放。
#: open＝已在 TOOLS；later＝不用 LLM、之後開放；llm_optional＝有 LLM 選項，開放時要強制關掉；
#: excluded＝不開放（LLM 是核心、操作設備、互動式、管理功能）；sensitive＝有法律效果，另有規則（用印）。
CLASSIFIED: dict[str, str] = {
    "office-convert": "later", "image-to-pdf": "later", "pdf-to-office": "later", "pdf-to-markdown": "later",
    "markdown-to-doc": "later", "pdf-to-slides": "later", "pdf-pages": "later", "pdf-border": "later",
    "pdf-bookmark": "later", "pdf-nup": "later", "pdf-page-size": "later", "pdf-watermark": "later",
    "doc-straighten": "later", "scan-merge": "later", "pdf-extract-images": "later", "pdf-attachments": "later",
    "pdf-metadata": "later", "pdf-hidden-scan": "later", "pdf-annotations-strip": "later",
    "pdf-annotations-flatten": "later", "pdf-encrypt": "later", "pdf-decrypt": "later", "text-diff": "later",
    "text-list": "later", "transit-proof": "later",
    "pdf-ocr": "llm_optional", "doc-diff": "llm_optional", "text-deident": "llm_optional",
    "doc-deident": "llm_optional", "pdf-annotations": "llm_optional", "submission-check": "llm_optional",
    "einvoice-scan": "llm_optional",
    "official-doc": "excluded", "translate-doc": "excluded", "doc-translate": "excluded",
    "meeting-summary": "excluded", "meeting-transcribe": "excluded", "mfp-fetch": "excluded",
    "pdf-editor": "excluded", "pdf-fill": "excluded",
    # 加密紙本備份處理的就是秘密，而且建立 / 還原都只在瀏覽器裡（主機不碰秘密）—— 永遠不經 AI
    "paper-backup": "excluded",
    "pdf-stamp": "sensitive", "pdf-seam-stamp": "sensitive",
    # 已開放的放最後：蓋過上面「之後開放」的歸類
    **{t.tool_id: "open" for t in TOOLS},
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

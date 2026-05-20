"""DocumentModel → ODT (ODF text) builder。

ODT-first 路線（v1.8.82+）— 取代直寫 OOXML 的 docx_builder。
LibreOffice / OxOffice 對 ODT 是 native，渲染 100% 確定，沒 OOXML quirks。

對應關係（PLAN_PDF_TO_OFFICE_ODT.md §3）:
- PageModel (size, margins) → <style:page-layout>
- TableModel               → <table:table> + <table:table-column> + <table:table-row>
- cell_blocks / cell_lines → <text:p> + <text:span>
- FreeBlock                → <text:p text:style-name="...">
- banner_rects             → <draw:polygon> native (phase 4)
- Cell merge               → table:number-rows-spanned / table:number-columns-spanned
- Cell shading             → <style:table-cell-properties fo:background-color="...">
- Cell borders             → fo:border-top 等

不靠 OOXML schema 順序、tblPr 順序、tcW vs gridSpan 矛盾、sibling-table align 等 bug
— 全部 sidestep。

外部入口：
- build_odt(doc_model, pdf_path, output_path, page_links_by_page=None) -> dict
"""
from __future__ import annotations

import logging
import re
from io import BytesIO
from pathlib import Path

from odf.opendocument import OpenDocumentText
from odf.text import P, Span, H, A, LineBreak
from odf.table import Table, TableColumn, TableRow, TableCell, CoveredTableCell
from odf.style import (
    Style, PageLayout, PageLayoutProperties, MasterPage,
    TextProperties, ParagraphProperties,
    TableProperties, TableCellProperties,
    TableColumnProperties, TableRowProperties,
    GraphicProperties,
)
from odf.draw import Frame, Image as ODFImage, Polygon, Rect as DrawRect, TextBox

log = logging.getLogger(__name__)

# --- 字型 fallback（同 docx_builder._normalize_font_name 邏輯）---
DEFAULT_WESTERN_FONT = "Times New Roman"
DEFAULT_CJK_FONT = "Noto Sans CJK TC"
DEFAULT_CJK_SERIF_FONT = "Noto Serif CJK TC"
DEFAULT_WESTERN_SERIF_FONT = "Times New Roman"
DEFAULT_WESTERN_SANS_FONT = "Arial"

# v1.9.93：字型 serif/sans 對照（per-PDF，build_odt 啟動時從 font descriptor
# /Flags bit 2 (serif=0x2) 建立）。台灣表單多用明體(serif)/標楷體，全對應
# Noto Sans 失去 serif 感。key = basefont name（去 subset prefix）→ True=serif。
_FONT_SERIF_MAP: dict[str, bool] = {}


def _build_font_serif_map(pdf_path) -> None:
    """掃 PDF 全部字型 descriptor /Flags，建立 name→is_serif 對照。
    /Flags bit 2 (value 2) = Serif；bit 1 (value 1) = FixedPitch。"""
    global _FONT_SERIF_MAP
    _FONT_SERIF_MAP = {}
    try:
        import fitz, re as _re
        doc = fitz.open(str(pdf_path))
        for pno in range(doc.page_count):
            try:
                for finfo in doc.get_page_fonts(pno):
                    xref = finfo[0]
                    basefont = finfo[3] if len(finfo) > 3 else ""
                    nm = (basefont or "").lstrip("+").strip()
                    if not nm or nm in _FONT_SERIF_MAP:
                        continue
                    # 名稱直接含 serif/明/宋/song/ming/serif hint
                    nl = nm.lower()
                    if any(k in nl for k in ("ming", "song", "sung", "serif",
                                              "mincho", "宋", "明", "kai", "楷")):
                        _FONT_SERIF_MAP[nm] = True
                        continue
                    if any(k in nl for k in ("hei", "gothic", "sans", "yahei",
                                              "jhenghei", "黑", "ping")):
                        _FONT_SERIF_MAP[nm] = False
                        continue
                    # 匿名 subset → 讀 descriptor /Flags
                    is_serif = None
                    try:
                        obj = doc.xref_object(xref) or ""
                        m = _re.search(r"/FontDescriptor\s+(\d+)\s+0\s+R", obj)
                        if m:
                            desc = doc.xref_object(int(m.group(1))) or ""
                            fm = _re.search(r"/Flags\s+(\d+)", desc)
                            if fm:
                                flags = int(fm.group(1))
                                is_serif = bool(flags & 0x2)
                    except Exception:
                        pass
                    if is_serif is not None:
                        _FONT_SERIF_MAP[nm] = is_serif
            except Exception:
                continue
        doc.close()
    except Exception:
        pass


def _is_pdf_embedded_font(name: str) -> bool:
    f = (name or "").lstrip("+").strip()
    if not f:
        return True
    fl = f.lower()
    return bool(
        (f.startswith("TT") and any(c.isdigit() for c in f))
        or "o00" in fl
        or (len(f) <= 12 and f[:1].isupper()
            and not any(k in fl for k in ("arial", "times", "courier", "noto",
                                            "ming", "song", "kai", "hei", "yi",
                                            "yuan", "fang", "jheng", "ping",
                                            "helvet", "verd", "georg")))
    )


def _strip_subset_prefix(name: str) -> str:
    """去掉 PDF subset prefix（6 大寫字母 + '+'，如 BCDEEE+DFKaiShu → DFKaiShu）。"""
    import re as _re
    n = (name or "").strip()
    n = _re.sub(r"^[A-Z]{6}\+", "", n)
    return n.lstrip("+").strip()


def _resolved_font(name: str, for_cjk: bool = True) -> str:
    """PDF font → 有效系統字型。v1.9.93：
    1. strip subset prefix（BCDEEE+DFKaiShu → DFKaiShu）
    2. 已知 CJK 字型名（明體/楷體/宋→Noto Serif CJK TC；黑體/gothic→Noto Sans）
    3. 已知西文（Times/Georgia/serif→Times New Roman；Arial/Helvetica→Arial）
    4. 匿名 subset → 用 _FONT_SERIF_MAP 判 serif
    避免把「BCDEEE+DFKaiShu-SB」這種非系統字型名原樣輸出 → soffice 找不到亂掉。
    """
    raw = (name or "").strip()
    nm = _strip_subset_prefix(raw)
    nl = nm.lower()
    # 西文 serif / sans 明確名稱
    if any(k in nl for k in ("times", "georgia", "garamond", "minion",
                              "cambria", "book antiqua")):
        return DEFAULT_CJK_SERIF_FONT if for_cjk else DEFAULT_WESTERN_SERIF_FONT
    if any(k in nl for k in ("arial", "helvet", "verdana", "tahoma",
                              "segoe", "calibri", "roboto")):
        return DEFAULT_CJK_FONT if for_cjk else DEFAULT_WESTERN_SANS_FONT
    if "courier" in nl or "mono" in nl:
        return "Courier New"
    # CJK 明體 / 宋 / 楷 = serif-like → Noto Serif CJK TC
    if any(k in nl for k in ("ming", "song", "sung", "mincho", "kai",
                              "serif", "宋", "明", "楷")):
        return DEFAULT_CJK_SERIF_FONT if for_cjk else DEFAULT_WESTERN_SERIF_FONT
    # CJK 黑體 / gothic = sans → Noto Sans CJK TC
    if any(k in nl for k in ("hei", "gothic", "jhenghei", "yahei", "黑",
                              "yuan", "圓", "ping")):
        return DEFAULT_CJK_FONT if for_cjk else DEFAULT_WESTERN_SANS_FONT
    # 匿名 subset / 不認得 → 用 serif map
    if _is_pdf_embedded_font(nm) or not nm:
        is_serif = _FONT_SERIF_MAP.get(raw)
        if is_serif is None:
            is_serif = _FONT_SERIF_MAP.get(nm)
        if is_serif is None:
            vals = list(_FONT_SERIF_MAP.values())
            if vals and sum(vals) > len(vals) / 2:
                is_serif = True
        if for_cjk:
            return DEFAULT_CJK_SERIF_FONT if is_serif else DEFAULT_CJK_FONT
        return DEFAULT_WESTERN_SERIF_FONT if is_serif else DEFAULT_WESTERN_SANS_FONT
    # 認得的真實字型名（已 strip prefix）→ 原樣用
    return nm


# --- 顏色 utility ---
def _normalize_color(hex_str: str, default: str = "") -> str:
    """'#RRGGBB' / 'RRGGBB' → '#RRGGBB' (ODF 用)；非合法 hex 回 default。"""
    h = (hex_str or "").lstrip("#").lower()
    if len(h) == 6 and all(c in "0123456789abcdef" for c in h):
        return f"#{h}"
    if len(h) == 3 and all(c in "0123456789abcdef" for c in h):
        return f"#{h[0]*2}{h[1]*2}{h[2]*2}"
    return default


# --- 主入口 ---
def build_odt(doc_model, pdf_path: Path, output_path: Path,
                page_links_by_page: dict | None = None) -> dict:
    """從 DocumentModel 寫出 .odt 檔。

    args:
        doc_model: DocumentModel (from paragraph_grouper.build_document_model)
        pdf_path:  原 PDF 路徑（給 image 提取用，暫未實作 phase 4 後加）
        output_path: 輸出 .odt 路徑
        page_links_by_page: dict[page_num] -> list[link dict]（暫未用）

    回 stats dict {ok, pages, tables, free_paragraphs, images, engine}
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # v1.9.93：建立本 PDF 字型 serif/sans 對照（給 _resolved_font 用）
    try:
        _build_font_serif_map(pdf_path)
    except Exception:
        pass

    odt = OpenDocumentText()

    # === Step 1: page layout (size + margins) ===
    if doc_model.pages:
        first = doc_model.pages[0]
        # v1.8.97 margin 自動收緊：scan 所有 page 內容 bbox，margin 不可大於
        # min_element_y / page_h - max_element_y - 5pt buffer，避免「PDF 內容貼邊」
        # 案例下 row padding 把流推到下一頁
        min_y = first.height; max_y = 0.0
        for pg in doc_model.pages:
            for fb in (pg.free_blocks or []):
                if fb.block.bbox:
                    min_y = min(min_y, float(fb.block.bbox[1]))
                    max_y = max(max_y, float(fb.block.bbox[3]))
            for tm in (pg.tables or []):
                if tm.region.bbox:
                    min_y = min(min_y, float(tm.region.bbox[1]))
                    max_y = max(max_y, float(tm.region.bbox[3]))
            for br in (pg.banner_rects or []):
                min_y = min(min_y, float(br[1]))
                max_y = max(max_y, float(br[3]))
        # v1.9.49：BUFFER 1.0 → 0.0、min_bottom 5.0 → 2.0
        # v1.9.54：tight page（content_max_y > page_h × 0.93）時 eff_margin_bottom
        # 額外壓縮 15pt，留給 soffice flow 計算 ENDPAD + row 內部 padding overhead。
        # 廠商基本資料表 等 1->2 page case：row heights 累積 + spacer 加總比實際 render
        # 高 ~16pt → soffice flow 看到溢出強換頁；多 15pt 邊距 budget 吸收掉。
        BUFFER = 0.0
        eff_margin_top = max(0.0, min(float(first.margin_top or 0),
                                        max(0.0, min_y - BUFFER)))
        # v1.9.60 雙模 margin_bottom：
        #  - 緊頁面 (ratio > 0.93)：用 15pt relief (page_h-max_y-15)
        #  - 寬鬆頁面：cap 20pt (v1.9.59 30pt → v1.9.60 20pt) — multi-page docs
        #    每頁邊緣個別溢出（ODF Web page 29 max_y=798 → 842-30=812 OK 但 soffice
        #    flow 多算了一些）；降到 20pt 給每頁 10pt 額外 budget。20pt = ~一行
        #    高度，仍夠視覺呼吸。
        page_h_f = float(first.height)
        _ratio = (max_y / page_h_f) if page_h_f else 0
        if _ratio > 0.93:
            eff_margin_bottom = max(2.0, page_h_f - max_y - BUFFER - 15.0)
        else:
            eff_margin_bottom = min(20.0, max(2.0,
                page_h_f - max_y - BUFFER))
        page_layout = PageLayout(name="MainPage")
        page_layout.addElement(PageLayoutProperties(
            pagewidth=f"{first.width:.1f}pt",
            pageheight=f"{first.height:.1f}pt",
            marginleft=f"{first.margin_left:.1f}pt",
            marginright=f"{first.margin_right:.1f}pt",
            margintop=f"{eff_margin_top:.1f}pt",
            marginbottom=f"{eff_margin_bottom:.1f}pt",
            printorientation="portrait",
        ))
        odt.automaticstyles.addElement(page_layout)
        master = MasterPage(name="Standard", pagelayoutname="MainPage")
        odt.masterstyles.addElement(master)

    # v1.8.98 「TINY」字型 style：給 page-anchor frame wrapper 內的 ZWSP 用，
    # 把 wrapper paragraph 在流中的高度壓到接近 0（lineheight 在 OxOffice 對含
    # frame 的 paragraph 是 soft cap，font-size 才是 enforced 底線）
    tiny_text_style = Style(name="TINYZW", family="text")
    tiny_text_style.addElement(TextProperties(
        fontsize="0.05pt", fontsizeasian="0.05pt", fontsizecomplex="0.05pt",
    ))
    odt.automaticstyles.addElement(tiny_text_style)

    # === Step 2: 共用 style cache ===
    style_cache: dict = {}

    def _get_text_style(font_size_pt: float, font_name: str,
                         bold: bool, italic: bool, color_hex: str,
                         alignment: str = "") -> str:
        """產生（或重用）text style，回 style name。"""
        key = (round(font_size_pt, 1), font_name, bold, italic,
               color_hex, alignment)
        if key in style_cache:
            return style_cache[key]
        sname = f"T{len(style_cache)}"
        s = Style(name=sname, family="text")
        tp_kwargs = {}
        if font_size_pt > 0:
            tp_kwargs["fontsize"] = f"{font_size_pt:.1f}pt"
            tp_kwargs["fontsizeasian"] = f"{font_size_pt:.1f}pt"
            tp_kwargs["fontsizecomplex"] = f"{font_size_pt:.1f}pt"
        ascii_f = _resolved_font(font_name, for_cjk=False)
        cjk_f = _resolved_font(font_name, for_cjk=True)
        if ascii_f:
            tp_kwargs["fontname"] = ascii_f
        if cjk_f:
            tp_kwargs["fontnameasian"] = cjk_f
            tp_kwargs["fontnamecomplex"] = cjk_f
        if bold:
            tp_kwargs["fontweight"] = "bold"
            tp_kwargs["fontweightasian"] = "bold"
            tp_kwargs["fontweightcomplex"] = "bold"
        if italic:
            tp_kwargs["fontstyle"] = "italic"
            tp_kwargs["fontstyleasian"] = "italic"
        c = _normalize_color(color_hex)
        if c:
            tp_kwargs["color"] = c
        s.addElement(TextProperties(**tp_kwargs))
        odt.automaticstyles.addElement(s)
        style_cache[key] = sname
        return sname

    def _get_para_style(alignment: str = "", spacing_after_pt: float = 2.0,
                          parent: str = "", vertical: bool = False) -> str:
        """產生（或重用）paragraph style，回 style name。

        spacing_after_pt 預設 2pt — 避免所有 free paragraphs 緊貼擠成一坨。
        對 banner 上方的多個 free paragraphs (公司資訊 / <樣本 A>號 / 地址) 拆段視覺。
        vertical=True 套 tb-rl writing-mode（vertical text，如「申請人基本資料」垂直 banner）。
        """
        key = ("P", alignment, round(spacing_after_pt, 1), parent, vertical)
        if key in style_cache:
            return style_cache[key]
        sname = f"P{len(style_cache)}"
        s_kwargs = {"name": sname, "family": "paragraph"}
        if parent:
            s_kwargs["parentstylename"] = parent
        s = Style(**s_kwargs)
        pp_kwargs = {}
        if alignment == "justify":
            # 分散對齊（CJK 字均勻撐滿 cell 寬，邊到邊）— text-align-last=justify
            # 讓單行也分散（上稿申請表「申 請 單 位」label 案）。
            pp_kwargs["textalign"] = "justify"
            pp_kwargs["textalignlast"] = "justify"
        elif alignment in ("left", "center", "right"):
            pp_kwargs["textalign"] = {
                "left": "start", "center": "center", "right": "end"
            }[alignment]
        if spacing_after_pt > 0:
            pp_kwargs["marginbottom"] = f"{spacing_after_pt:.1f}pt"
        # v1.9.62/63：lineheight 100% → 90% → 80%。OxOffice 預設用 font natural
        # leading (~1.2x font-size)，PDF bbox 是視覺尺寸 (~font-size)，ODT 每行
        # 多 0.2 × font-size 累積。Release Notes 25→37 (v1.9.61) → 25→31 (v1.9.62
        # 90%) → 再試 80% 看能否到 25→27 範圍。
        # v1.9.78：確認 85% 為最佳平衡 — 90% Release Notes 25→27 regress；
        # 100% Release Notes 25→31 大 regress。75% 雖修 multi-page 但 form cell
        # 視覺受影響。85% 平衡 multi-page page count + form 內小字渲染。
        pp_kwargs["lineheight"] = "85%"
        # NOTE: 不再用 writing-mode tb-rl — OxOffice 對單 paragraph writing-mode 會
        # propagate 整 cell render（v1.8.84 bug）。改用 per-char LineBreak 模擬 vertical
        s.addElement(ParagraphProperties(**pp_kwargs))
        odt.automaticstyles.addElement(s)
        style_cache[key] = sname
        return sname

    def _get_cell_style(width_pt: float, bg_color: str,
                          border_color: str, border_width_pt: float,
                          has_borders: bool, vAlign: str = "middle",
                          border_sides: tuple = (True, True, True, True),
                          outer_sides: tuple = (False, False, False, False),
                          outer_width_pt: float = 0.0) -> str:
        """產生 cell style，回 style name。

        border_sides = (top, right, bottom, left)；False 那邊 emit 'none'。
        outer_sides = 哪些邊是「表格外框」(該 emit 較粗線)；outer_width_pt 為
        外框粗度。v1.9.89：表單外框粗 / 內格線細的區分。
        """
        sides = tuple(bool(x) for x in border_sides)
        osides = tuple(bool(x) for x in outer_sides)
        key = ("TC", round(width_pt, 1), bg_color, border_color,
               round(border_width_pt, 2), has_borders, vAlign, sides,
               osides, round(outer_width_pt, 2))
        if key in style_cache:
            return style_cache[key]
        sname = f"TC{len(style_cache)}"
        s = Style(name=sname, family="table-cell")
        tcp_kwargs = {"verticalalign": vAlign}
        bg = _normalize_color(bg_color)
        if bg:
            tcp_kwargs["backgroundcolor"] = bg
        # borders — per-side based on `sides` + 外框用 outer_width
        if has_borders:
            bc = _normalize_color(border_color, default="#999999")
            bw_pt = border_width_pt if border_width_pt > 0 else 0.3
            ow_pt = outer_width_pt if outer_width_pt > 0 else bw_pt
            inner_def = f"{bw_pt:.2f}pt solid {bc}"
            outer_def = f"{ow_pt:.2f}pt solid {bc}"
            t_top, t_right, t_bot, t_left = sides
            o_top, o_right, o_bot, o_left = osides
            def _side(on, is_outer):
                if not on:
                    return "none"
                return outer_def if is_outer else inner_def
            # 若全 inner 且都開 → 用單一 border (省 style)
            if all(sides) and not any(osides):
                tcp_kwargs["border"] = inner_def
            else:
                tcp_kwargs["bordertop"] = _side(t_top, o_top)
                tcp_kwargs["borderright"] = _side(t_right, o_right)
                tcp_kwargs["borderbottom"] = _side(t_bot, o_bot)
                tcp_kwargs["borderleft"] = _side(t_left, o_left)
        else:
            tcp_kwargs["border"] = "none"
        # padding（v1.8.91 改 0.02cm 更緊湊，配合 fixed rowheight 才不會 cell 漲）
        # v1.9.46 revert：0pt 沒解 page-doubling 反而讓文字貼緊邊框，保留 0.02cm
        tcp_kwargs["padding"] = "0.02cm"
        s.addElement(TableCellProperties(**tcp_kwargs))
        odt.automaticstyles.addElement(s)
        style_cache[key] = sname
        return sname

    def _get_table_style(width_pt: float, align: str = "left",
                          margintop_pt: float = 0.0,
                          marginleft_pt: float = 0.0) -> str:
        key = ("TBL", round(width_pt, 1), align, round(margintop_pt, 1),
                round(marginleft_pt, 1))
        if key in style_cache:
            return style_cache[key]
        sname = f"TBL{len(style_cache)}"
        s = Style(name=sname, family="table")
        kw = {"width": f"{width_pt:.1f}pt", "align": align,
              "marginbottom": "0pt"}
        if margintop_pt > 0:
            kw["margintop"] = f"{margintop_pt:.1f}pt"
        else:
            kw["margintop"] = "0pt"
        # v1.9.31：marginleft 用來把「PDF 右側的 sub-table」對齊回去右側
        # （否則 flow emit 都從 left margin 開始 → 報價單 未連稅/營業稅/總計
        # mini-table 應在右側但跑左邊）
        if marginleft_pt > 0:
            kw["marginleft"] = f"{marginleft_pt:.1f}pt"
            # align 改 "from-left" 而非 "margins"，否則 marginleft 被忽略
            kw["align"] = "left"
        s.addElement(TableProperties(**kw))
        odt.automaticstyles.addElement(s)
        style_cache[key] = sname
        return sname

    def _get_column_style(width_pt: float) -> str:
        key = ("COL", round(width_pt, 1))
        if key in style_cache:
            return style_cache[key]
        sname = f"COL{len(style_cache)}"
        s = Style(name=sname, family="table-column")
        s.addElement(TableColumnProperties(columnwidth=f"{width_pt:.1f}pt"))
        odt.automaticstyles.addElement(s)
        style_cache[key] = sname
        return sname

    def _get_row_style(min_height_pt: float) -> str:
        key = ("ROW", round(min_height_pt, 1))
        if key in style_cache:
            return style_cache[key]
        sname = f"ROW{len(style_cache)}"
        s = Style(name=sname, family="table-row")
        # v1.8.91：用 rowheight (preferred fixed) + useoptimalrowheight=false 強制
        # 高度跟 PDF 一致；不然 OxOffice 對 minrowheight 會自動依 cell content 漲高 →
        # 14 row 主表渲到比 PDF 多 ~100pt，把後續內容推到下一頁
        s.addElement(TableRowProperties(
            rowheight=f"{min_height_pt:.1f}pt",
            useoptimalrowheight="false",
        ))
        odt.automaticstyles.addElement(s)
        style_cache[key] = sname
        return sname

    # === Step 3: Heading2 style for 標題 ===
    h2_style = Style(name="Heading2", family="paragraph",
                      parentstylename="Standard", displayname="Heading 2")
    h2_style.addElement(TextProperties(
        fontsize="16pt",
        fontsizeasian="16pt",
        fontweight="bold",
        fontweightasian="bold",
        color="#1f4e79",
    ))
    h2_style.addElement(ParagraphProperties(
        marginbottom="0.2cm", margintop="0.2cm",
    ))
    odt.styles.addElement(h2_style)

    # === Step 4: 對每 page 產生內容（按 Y 序混排 free / table / image）===
    stats = {"tables": 0, "free_paragraphs": 0, "images": 0, "banner_polygons": 0}
    # v1.9.5：過濾完全空白的 page（無 free_blocks / tables / images / banners）—
    # 避免 PDF 內 trailing blank page 被當成額外頁產生 extra page break
    _eff_pages = [pg for pg in doc_model.pages
                    if (pg.free_blocks or pg.tables or pg.images
                          or pg.banner_rects)]
    for pi, page in enumerate(_eff_pages):
        # v1.9.26 C：直書 / 田字格 練習卷 fallback —
        # v1.9.86：raster fallback **只保留 bad-CMap**（文字 decode 成 garbage，
        # 抽出來無意義，raster 至少保視覺）。**移除 vertical_text raster** —
        # 直書頁（如 田字格練習卷）文字其實是有效 unicode，raster 轉圖讓文字
        # 不可選取編輯，違背「PDF → 可編輯 Office」目的（使用者明確反對）。
        # 直書頁改走正常 structural rebuild，文字照樣抽出（排版可能不完美但
        # 可編輯），跟 pdf2docx 行為一致。
        if _page_is_bad_cmap(page, pdf_path):
            try:
                pw = float(page.width or 595)
                ph = float(page.height or 842)
                f = _build_free_drawing_frame(
                    odt, (0, 0, pw, ph), page.page_num, pdf_path,
                    cluster_id=f"vt_p{pi}")
                if f is not None:
                    # v1.9.66：bad-cmap / vertical raster：wrap_p 本身設
                    # breakafter="page" 直接觸發下一頁分頁，避免 wrap_p + break
                    # 段落各佔一頁的雙佔位現象（v1.9.65 → 23 頁 / v1.9.66 → 接近 15）。
                    is_last_vt = (pi >= len(_eff_pages) - 1)
                    full_p_style = Style(name=f"VTWRAP{pi}", family="paragraph")
                    pp_attrs = dict(margintop="0pt", marginbottom="0pt",
                                    lineheight="0.05pt")
                    if not is_last_vt:
                        pp_attrs["breakafter"] = "page"
                    full_p_style.addElement(ParagraphProperties(**pp_attrs))
                    odt.automaticstyles.addElement(full_p_style)
                    wrap_p = P(stylename=full_p_style)
                    wrap_p.addElement(f)
                    wrap_p.addElement(Span(stylename="TINYZW", text="​"))
                    odt.text.addElement(wrap_p)
                    stats.setdefault("vertical_pages", 0)
                    stats["vertical_pages"] += 1
                    continue  # skip structural rebuild for this page
            except Exception as e:
                log.debug("vertical page raster failed: %s", e)
                # fallback 失敗就照舊路徑跑（不會更糟）

        # v1.9.87：直書頁 reflow — 偵測 column-major 直書文字（田字格練習卷 /
        # 直書範本）→ 按「右欄→左欄、欄內上→下」reading order 重組成橫向可讀
        # 段落。比 structural rebuild 的散落定位好（可編輯 + 連貫），也避免
        # raster 失去文字。空白 田字格 不產生文字。
        if _page_is_vertical_text(page):
            try:
                if _emit_vertical_text_reflow(
                        odt, page, _get_text_style, _get_para_style):
                    stats.setdefault("vertical_pages", 0)
                    stats["vertical_pages"] += 1
                    # 多頁之間加 page break
                    if pi < len(_eff_pages) - 1:
                        vpb = Style(name=f"VRPB{pi}", family="paragraph")
                        vpb.addElement(ParagraphProperties(
                            breakbefore="page", margintop="0pt",
                            marginbottom="0pt", lineheight="0.05pt"))
                        vpb.addElement(TextProperties(
                            fontsize="0.05pt", fontsizeasian="0.05pt"))
                        odt.automaticstyles.addElement(vpb)
                        vpb_p = P(stylename=vpb)
                        vpb_p.addElement(Span(stylename="TINYZW", text="​"))
                        odt.text.addElement(vpb_p)
                    continue  # skip structural rebuild
            except Exception as e:
                log.debug("vertical text reflow failed: %s", e)

        # v1.8.98 改：所有 banner / image / polygon 的 page-anchor frame 全部塞進
        # **單一**「header_p」wrapper paragraph（不再每個 frame 一個 wrap_p）— 因
        # OxOffice 對含 frame 的 paragraph 至少給一行 min height（即使 lineheight
        # 設 0.05pt + fontsize 0.05pt）。多個 wrap_p 累積 4×~14pt = 56pt 流高度，
        # 把後續 spacer 推到 PDF 真實 Y 之下；合一個只佔 1×~14pt。
        header_p_style = Style(name="HDRWRAP", family="paragraph")
        header_p_style.addElement(ParagraphProperties(
            margintop="0pt", marginbottom="0pt", lineheight="0.05pt",
        ))
        odt.automaticstyles.addElement(header_p_style)
        header_p = P(stylename=header_p_style)
        header_frame_count = [0]  # 用 list 包裝以方便 closure 改

        def _add_header_frame(frame):
            header_p.addElement(frame)
            header_frame_count[0] += 1

        grouped, ungrouped = _group_banners_by_container(page.banner_rects or [])
        for group in grouped:
            try:
                f = _build_banner_group_frame(odt, group)
                if f is not None:
                    _add_header_frame(f)
                    stats["banner_polygons"] += 1
            except Exception as e:
                log.debug("build banner group failed: %s", e)
        for banner in ungrouped:
            try:
                f = _build_banner_polygon_frame(odt, banner,
                                                  pdf_path=pdf_path,
                                                  page_num=pi)
                if f is not None:
                    _add_header_frame(f)
                    stats["banner_polygons"] += 1
            except Exception as e:
                log.debug("build banner failed: %s", e)
        for img in (page.images or []):
            try:
                f = _build_pdf_image_frame(odt, img, pdf_path)
                if f is not None:
                    _add_header_frame(f)
                    stats["images"] += 1
            except Exception as e:
                log.debug("build image failed: %s", e)
        # v1.9.25：emit free-drawing-cluster raster frames（不在 table 內、不在
        # outer-container 內的 free drawings — 箭頭、dimension line、vector 形）
        # 用 PyMuPDF clip 區域 raster 成 page-anchor PNG（PDF / ODT 原理）
        emitted_clusters_for_dedupe: list = []
        for fdc in (getattr(page, "free_drawing_clusters", []) or []):
            try:
                f = _build_free_drawing_frame(
                    odt, fdc, page.page_num, pdf_path,
                    cluster_id=f"{pi}_{int(fdc[0])}_{int(fdc[1])}")
                if f is not None:
                    _add_header_frame(f)
                    stats.setdefault("free_drawings", 0)
                    stats["free_drawings"] += 1
                    emitted_clusters_for_dedupe.append(fdc)
            except Exception as e:
                log.debug("build free drawing frame failed: %s", e)
        # 至少塞一個 ZWSP，避免 empty wrap_p 被 OxOffice collapse
        header_p.addElement(Span(stylename="TINYZW", text="​"))
        if header_frame_count[0] > 0:
            odt.text.addElement(header_p)
        # 其他 elements 按 Y 序排
        # images / banners 已 emit-first（上方），剩 free_blocks 與 tables 按 Y 序排
        # v1.9.32：cluster raster 已含內部文字 → 把「中心在 cluster bbox 內」
        # 的 free_block 丟掉，避免雙重渲染（user：「核准字號 字疊兩次」）
        def _bbox_in_cluster(bb) -> bool:
            cx = (bb[0] + bb[2]) / 2.0
            cy = (bb[1] + bb[3]) / 2.0
            for cb in emitted_clusters_for_dedupe:
                if cb[0] <= cx <= cb[2] and cb[1] <= cy <= cb[3]:
                    return True
            return False

        elements: list[tuple[float, str, object]] = []
        for fb in (page.free_blocks or []):
            if _bbox_in_cluster(fb.block.bbox):
                continue
            # v1.9.69：跳過 0-line 空 block (Release Notes cover page 上常見
            # 「整頁邊框 box」block，0 lines 但 bbox 涵蓋整頁 → 之前處理時把
            # current_y_pt 推到 page bottom，使後續真實 title 文字被擠到下頁)
            if not (fb.block.lines or []):
                bbox = fb.block.bbox
                if bbox and (float(bbox[3]) - float(bbox[1])) > float(page.height or 842) * 0.5:
                    continue
            elements.append((float(fb.block.bbox[1]), "free", fb))
        for tm in (page.tables or []):
            elements.append((float(tm.region.bbox[1]), "table", tm))
        elements.sort(key=lambda x: x[0])

        # banner_y_max 判定 free_block 是否屬「banner 浮動區」：取最大 banner.y1
        # 改 v1.8.91：只取「page 上方 25% 內」的 banner 才算頂部 banner 區（避免
        # 頁中下段的印章 / seal polygon 把 banner_y_max 推到頁底，誤把所有 free
        # block 都歸 banner 區）
        banner_y_max = 110.0
        page_h_local = float(page.height or 842)
        for br in (page.banner_rects or []):
            try:
                if float(br[3]) <= page_h_local * 0.25:
                    banner_y_max = max(banner_y_max, float(br[3]))
            except Exception:
                pass

        # 額外：「outer container box」偵測（大型 banner_rect 含 path_points=0 +
        # 寬度 > page_w × 0.5）— 內部 elements 走 page-anchor frame 不走 flow，
        # 整塊圖樣才不會推 page 1 內容溢到 page 2。
        # （是 PDF / ODT 原理：page-anchor frame 不佔 flow 高度）
        outer_containers: list[tuple[float, float, float, float]] = []
        page_w_local = float(page.width or 595)
        for br in (page.banner_rects or []):
            try:
                bx0, by0, bx1, by1 = float(br[0]), float(br[1]), float(br[2]), float(br[3])
                bw = bx1 - bx0
                bh = by1 - by0
                # 大型容器：寬 > 50% 頁寬 + 高 > 100pt + 位於頁中下半（y_top > 25%）
                if (bw > page_w_local * 0.5 and bh > 100
                        and by0 > page_h_local * 0.25
                        and len(br[5]) == 0):  # 無 path_points = 純矩形 frame
                    outer_containers.append((bx0, by0, bx1, by1))
            except Exception:
                pass

        def _bbox_in_container(bbox: tuple) -> bool:
            """判斷 bbox 中心點是否在任何 outer container 內。"""
            if not outer_containers or not bbox:
                return False
            cx = (float(bbox[0]) + float(bbox[2])) / 2.0
            cy = (float(bbox[1]) + float(bbox[3])) / 2.0
            for cx0, cy0, cx1, cy1 in outer_containers:
                if cx0 <= cx <= cx1 and cy0 <= cy <= cy1:
                    return True
            return False

        # v1.9.91：收集本頁填寫底線 rects（給 _emit_table cell 行內底線 + 自由區
        # draw:line 共用）。每條 = (x0, ymid, x1)。
        # v1.9.95：排除表格水平格線 — 細矩形 (h<2pt) 若 y 落在任何 table row_y
        # 邊界 ±2.5pt 即為 cell 邊框線（非填寫底線），否則 checkbox row 的上下
        # 格線會被誤插成行內底線（「□平時考題 ＿＿＿」印製申請表 case）。填寫底線
        # 在 cell 中間，距 row 邊界 > 2.5pt。
        _row_boundary_ys: list[float] = []
        for _tm in (page.tables or []):
            try:
                _row_boundary_ys.extend(float(_y) for _y in _tm.region.row_ys)
            except Exception:
                pass
        _page_underlines: list = []
        _pw_u = float(page.width or 595)
        for _dr in (getattr(page, "raw_drawings", None) or []):
            if getattr(_dr, "type", "") != "rect":
                continue
            _b = _dr.bbox
            _w = float(_b[2]) - float(_b[0])
            _h = float(_b[3]) - float(_b[1])
            if _h >= 2.0 or _w < 15.0 or _w > _pw_u * 0.5:
                continue
            _ymid = (float(_b[1]) + float(_b[3])) / 2.0
            if any(abs(_ymid - _ry) <= 2.5 for _ry in _row_boundary_ys):
                continue
            _page_underlines.append((float(_b[0]), _ymid, float(_b[2])))

        # === pre-pass v1.8.91 ===
        # 所有「將以 page-anchor 渲」的元素（in outer container 內或頁尾 Y > 85%）
        # 必須在 flow 開頭就 emit 完，否則 wrapper paragraph 落在 mid-flow，若
        # flow 已溢到 page 2，frame 的 anchortype="page" 就 anchor 到 page 2 而非
        # PDF 原本 Y 對應的 page 1。
        # （PDF/ODT 原理：anchortype="page" 的 frame 跟著 wrapper paragraph 所在 page）
        pre_anchored: set = set()  # 已 emit 的 id(payload)
        page_h_e = float(page.height or 842)
        # 找最後一個 element 的 Y_bottom（用 LAST 元素位置判定）
        last_y_bot = 0.0
        for _, _, p in elements:
            try:
                if hasattr(p, 'block') and p.block.bbox:
                    last_y_bot = max(last_y_bot, float(p.block.bbox[3]))
                elif hasattr(p, 'region') and p.region.bbox:
                    last_y_bot = max(last_y_bot, float(p.region.bbox[3]))
            except Exception:
                pass

        for _, kind, payload in elements:
            try:
                if kind == "free":
                    bbox = payload.block.bbox
                    if not bbox:
                        continue
                    target_y = float(bbox[1])
                    in_c = _bbox_in_container(bbox)
                    # 頁尾 Y > 75% 走 anchor。若此 element 是 last in elements 且
                    # 內容貼近 page 底（last_y_bot > 80% page_h），更積極 anchor
                    is_last_near_bottom = (
                        float(bbox[3]) >= last_y_bot - 1
                        and last_y_bot > page_h_e * 0.60)
                    if in_c or target_y > page_h_e * 0.75 or is_last_near_bottom:
                        _emit_free_block_as_frames(odt, payload, page,
                                                     _get_text_style, _get_para_style)
                        pre_anchored.add(id(payload))
                        stats["free_paragraphs"] += 1
                elif kind == "table":
                    target_y = float(payload.region.bbox[1])
                    in_c = _bbox_in_container(payload.region.bbox)
                    # v1.9.32：「頁尾 anchor」threshold 從 75% 拉到 90%，因為
                    # 65-90% 區可能是 signature row（核准/審核/製表）等仍需
                    # 框線的結構 — 走 normal flow + outline 比較對
                    if in_c or target_y > page_h_e * 0.90:
                        _emit_table_as_frame(odt, payload, page,
                                               _get_text_style, _get_para_style,
                                               _get_table_style, _get_column_style,
                                               _get_row_style, _get_cell_style)
                        pre_anchored.add(id(payload))
                        stats["tables"] += 1
            except Exception as e:
                log.debug("pre-anchor emit %s failed: %s", kind, e)

        # paragraph 流目前累積位置（Y in pt）— 開頭從 page top margin 算
        # v1.8.98 校正：page-anchor frame（top banner / image）即使 wrap=none +
        # runthrough=background，OxOffice 仍會在流中把 wrap_p 占據到 frame
        # 最大 bottom Y 的位置 → spacer 從那邊起算才不會多推一段。
        last_kind: str = ""
        current_y_pt = float(page.margin_top or 30)
        # v1.9.13：頁緊度（content 底 vs page height）— 緊頁面（剩 < 100pt buffer）
        # 不允許擴張 row 高度，避免推爆 page bottom
        _max_bottom = 0.0
        for _, _, _p in elements:
            try:
                if hasattr(_p, "block"):
                    _max_bottom = max(_max_bottom, float(_p.block.bbox[3]))
                elif hasattr(_p, "region"):
                    _max_bottom = max(_max_bottom, float(_p.region.bbox[3]))
            except Exception:
                pass
        # v1.9.16：多頁 PDF (≥ 4 pages) 一律禁 row expand —— 多頁文件累積 row 高
        # 度差會推爆下一頁邊界（2025 簡報 / 作業要點文件 / PVE 報告 case）；
        # 單頁 / 少頁表單才允許擴張。
        _is_multipage = len(doc_model.pages) >= 4
        page_has_room = (not _is_multipage
                         and (not page.height
                              or _max_bottom < float(page.height) - 100))
        # v1.9.20：tight page = content max_y > 93% page height
        # → free block 內 line spacing_after 全部設 0 避免累積推爆 page bottom
        # v1.9.61：threshold 從 0.97 → 0.93。ODF Web 等 multi-page docs 每頁
        # max_y = 785/842 (93.2%) 落在 0.93~0.97 之間 — line spacing 累積 ~50pt
        # 把第 12 個 block 推到下頁。改 0.93 觸發 tight 強制 spacing=0。
        _tight_page = bool(page.height
                       and _max_bottom > float(page.height) * 0.93)
        header_frame_max_y = 0.0
        for br in (page.banner_rects or []):
            try:
                # 只算頁頂 banner（在 banner_y_max 區內，避免印章 polygon 干擾）
                if float(br[3]) <= banner_y_max + 10:
                    header_frame_max_y = max(header_frame_max_y, float(br[3]))
            except Exception:
                pass
        for im in (page.images or []):
            try:
                if im.bbox and float(im.bbox[3]) <= banner_y_max + 10:
                    header_frame_max_y = max(header_frame_max_y, float(im.bbox[3]))
            except Exception:
                pass
        # v1.9.98：不再把 current_y_pt 推進到 header_frame_max_y。v1.9.73 起所有
        # 頁頂 banner / logo / 地址都是 wrap="run-through" 的 page-anchor frame，
        # 實測 OxOffice 不會把 flow 佔據到 frame 底（與舊 v1.8.98 假設相反）。
        # 保留推進會讓「頁頂 header 下方的第一個 flow 元素」(報價單 bill-to 公司
        # 區塊) spacer 算太短 → 跑到頁頂蓋住 logo。改讓 spacer 從 margin_top 推到
        # 該元素真實 Y。header_frame_max_y 仍保留供未來判斷用，但不改 current_y_pt。
        _ = header_frame_max_y

        def _emit_spacer(target_y: float):
            nonlocal current_y_pt
            spacer_pt = target_y - current_y_pt
            if spacer_pt > 0.5:
                spacer_style = Style(name=f"SP{id(target_y) & 0xFFFF}_{int(target_y*10)}",
                                      family="paragraph")
                spacer_style.addElement(ParagraphProperties(
                    margintop=f"{spacer_pt:.1f}pt",
                    marginbottom="0pt",
                    lineheight="0.05pt",
                ))
                odt.automaticstyles.addElement(spacer_style)
                # v1.9.14：加入 tiny font-size span (0.05pt) 強制 paragraph 高度
                # 接近 0，避免 OxOffice 用 default 12pt 撐高
                tiny_text_style = Style(
                    name=f"SPT{id(target_y) & 0xFFFF}_{int(target_y*10)}",
                    family="text")
                tiny_text_style.addElement(TextProperties(fontsize="0.05pt"))
                odt.automaticstyles.addElement(tiny_text_style)
                sp_p = P(stylename=spacer_style)
                tiny_sp = Span(stylename=tiny_text_style)
                tiny_sp.addText(" ")
                sp_p.addElement(tiny_sp)
                odt.text.addElement(sp_p)
                current_y_pt = target_y

        for _, kind, payload in elements:
            # 已在 pre-pass anchored 的略過
            if id(payload) in pre_anchored:
                continue
            try:
                if kind == "free":
                    fb_y_top = float(payload.block.bbox[1]) if payload.block.bbox else 0
                    fb_y_bot = float(payload.block.bbox[3]) if payload.block.bbox else 0
                    # Banner Y 區（頁頂 banner 內）→ frame 浮動（不影響流）
                    if fb_y_bot <= banner_y_max + 10:
                        _emit_free_block_as_frames(odt, payload, page,
                                                     _get_text_style, _get_para_style)
                    elif (fb_y_top - current_y_pt) > 250:
                        # v1.9.19：spacer 要推 > 250pt 時 OxOffice 會把過大的
                        # margin clamp 觸發 page break — 改用 page-anchor
                        # frame 直接定位該 block（通告文件 資安通告 / 簡報類
                        # 內 free block 位置跳很遠 case）
                        _emit_free_block_as_frames(odt, payload, page,
                                                     _get_text_style, _get_para_style)
                    else:
                        # 流 paragraph + spacer push 到 fb_y_top
                        _emit_spacer(fb_y_top)
                        _emit_free_block_in_flow(odt, payload,
                                                   _get_text_style, _get_para_style,
                                                   tight_page=bool(_tight_page),
                                                   page_w=float(page.width or 0))
                        current_y_pt = fb_y_bot
                    stats["free_paragraphs"] += 1
                elif kind == "table":
                    target_y = float(payload.region.bbox[1])
                    # v1.9.84：頁首 / 頁尾 furniture table（薄 1-row、貼頁緣、
                    # 全寬）→ page-anchor frame 不佔 flow 高度。大型 web 文檔 等 web 文檔
                    # 每頁有 print header (timestamp + 標題) + footer (URL + 頁碼)
                    # 1-row table，原本在 flow 內各佔 ~10pt + spacer，多頁累積把
                    # content 推溢出多分一頁（大型 web 文檔 96→97 連鎖偏移）。
                    _treg = payload.region
                    _tb = _treg.bbox
                    _trows = len(_treg.row_ys) - 1
                    _th = float(_tb[3]) - float(_tb[1])
                    _pw = float(page.width or 595)
                    _ph2 = float(page.height or 842)
                    _twidth = float(_tb[2]) - float(_tb[0])
                    _is_furniture = (
                        _trows <= 1 and _th < 18
                        and _twidth > _pw * 0.55
                        and (float(_tb[1]) < 32 or float(_tb[3]) > _ph2 - 42)
                    )
                    if _is_furniture:
                        try:
                            _emit_table_as_frame(odt, payload, page,
                                                   _get_text_style,
                                                   _get_para_style,
                                                   _get_table_style,
                                                   _get_column_style,
                                                   _get_row_style,
                                                   _get_cell_style)
                            stats["tables"] += 1
                            last_kind = "table_frame"
                            continue
                        except Exception as e:
                            log.debug("furniture table frame failed: %s", e)
                    gap_to_prev = target_y - current_y_pt
                    # v1.9.35：原 threshold 30pt 太大導致「台灣→報價日期」
                    # 24pt gap 沒 spacer，視覺貼死。改 5pt（一行高度的 1/3）
                    if gap_to_prev > 5:
                        _emit_spacer(target_y)
                    elif last_kind == "table":
                        # v1.9.56：兩個 table 之間 gap ≤ 5pt → 無 spacer，但 ODF
                        # 規範禁止 table 直接連 table；LibreOffice 自動補 12pt
                        # 段落往往把第 2 個 table 推下頁。改自己加 0.05pt 段落吸收
                        # （廠商基本資料表-V2024 / 廠商-c 案 1->2 修法）。
                        inter_style = Style(name=f"INTER_T{stats['tables']}",
                                            family="paragraph")
                        inter_style.addElement(ParagraphProperties(
                            margintop="0pt", marginbottom="0pt",
                            lineheight="0.05pt",
                        ))
                        inter_style.addElement(TextProperties(
                            fontsize="0.05pt", fontsizeasian="0.05pt",
                            fontsizecomplex="0.05pt",
                        ))
                        odt.automaticstyles.addElement(inter_style)
                        inter_p = P(stylename=inter_style)
                        inter_p.addElement(Span(stylename="TINYZW", text="​"))
                        odt.text.addElement(inter_p)
                    # v1.9.31：sub-table 在 PDF 右側時用 marginleft 對齊
                    # （報價單 未連稅/營業稅/總計 mini-table 在 x≈303 右側）
                    # v1.9.40：table 在 PDF 內水平置中時（left/right margin
                    # 相近）用 align="center"，否則 left + marginleft
                    target_x = float(payload.region.bbox[0])
                    table_x1 = float(payload.region.bbox[2])
                    page_w = float(page.width or 595)
                    page_ml = float(page.margin_left or 0)
                    page_mr = float(page.margin_right or 0)
                    left_gap = target_x - page_ml
                    right_gap = (page_w - page_mr) - table_x1
                    table_w = table_x1 - target_x
                    sub_ml = 0.0
                    table_align = "left"
                    # 置中判定：left_gap 與 right_gap 大致相等（差 < 表寬 10%）
                    # 且兩邊都 > 10pt（不是貼邊）
                    if (abs(left_gap - right_gap) < table_w * 0.10
                            and left_gap > 10 and right_gap > 10):
                        table_align = "center"
                    else:
                        # 右側 sub-table 用 marginleft
                        sub_ml = max(0.0, left_gap)
                        if sub_ml < page_w * 0.15:
                            sub_ml = 0.0
                    _emit_table(odt, payload,
                                  _get_text_style, _get_para_style,
                                  _get_table_style, _get_column_style,
                                  _get_row_style, _get_cell_style,
                                  allow_row_expand=page_has_room,
                                  marginleft_pt=sub_ml,
                                  align=table_align,
                                  underline_rects=_page_underlines)
                    current_y_pt = float(payload.region.bbox[3])
                    stats["tables"] += 1
                elif kind == "image":
                    _emit_pdf_image(odt, payload, pdf_path)
                    stats["images"] += 1
                last_kind = kind
            except Exception as e:
                log.debug("emit %s failed: %s", kind, e)

        # v1.9.88：填寫底線還原 — 把「非表格格線的短橫線」(年__月__日 填寫空格、
        # 申請人簽名：____ 等) emit 成 page-anchored draw:Line。表單最常見元素，
        # 之前 in-cell 底線完全遺失（使用者反映「年月日中間底線都不見了」）。
        try:
            _row_ys_all = []
            _col_xs_all = []
            for _tm in (page.tables or []):
                _row_ys_all.extend(_tm.region.row_ys or [])
                _col_xs_all.extend(_tm.region.col_xs or [])
            _n = _emit_fillin_underlines(odt, page, _row_ys_all, _col_xs_all)
            if _n:
                stats.setdefault("underlines", 0)
                stats["underlines"] += _n
        except Exception as e:
            log.debug("fillin underlines failed: %s", e)

        # 多 page 之間加 soft page break — 0pt margin 避免下頁開頭額外推
        # v1.9.59：加 lineheight=0.05pt + font-size=0.05pt 讓 break para 接近 0pt
        # 高度。原本 default 12pt + 100% lineheight = 14pt 累積在每頁起點 → 多頁文
        # 件 (29 pages doc) 共多 14 × 28 = 392pt = ~10 額外頁。
        if pi < len(_eff_pages) - 1:
            pb_style = Style(name=f"PBR{pi}", family="paragraph")
            pb_style.addElement(ParagraphProperties(
                breakbefore="page", margintop="0pt", marginbottom="0pt",
                lineheight="0.05pt",
            ))
            pb_style.addElement(TextProperties(
                fontsize="0.05pt", fontsizeasian="0.05pt", fontsizecomplex="0.05pt",
            ))
            odt.automaticstyles.addElement(pb_style)
            pb_p = P(stylename=pb_style.getAttribute("name"))
            pb_p.addElement(Span(stylename="TINYZW", text="​"))
            odt.text.addElement(pb_p)

    # v1.9.51：body 結尾若是 table，soffice / LibreOffice 自動補一個預設 12pt
    # 段落，常因 page 緊湊推到下一頁形成「empty page 2」。改自己加一個 0.05pt
    # 段落吃掉。但若結尾本身是 page-anchor frame wrapper（vt 全頁影像）就不加，
    # 不然反而引入額外 page (帳號申請表 申請單案：v1.9.50/.51 1->2 regression)。
    # v1.9.67 試只對單頁加 ENDPAD，但發現多頁 doc 的 trailing 空白頁不論
    # 加 / 不加 都會出現（soffice 對 trailing table 強制補一頁），revert 改回
    # 一律加 ENDPAD。
    children = list(odt.text.childNodes)
    last_is_table = bool(children) and getattr(
        children[-1], "qname", (None, None))[1] == "table"
    if last_is_table:
        end_pad = Style(name="ENDPAD", family="paragraph")
        # v1.9.71：移除 keepwithnext="always" — soffice 可能把它解讀為「需要 next
        # paragraph」自動補 12pt 段落 → 創造空頁。改純 0.05pt 無 keep flag。
        end_pad.addElement(ParagraphProperties(
            margintop="0pt", marginbottom="0pt", lineheight="0.05pt",
        ))
        end_pad.addElement(TextProperties(
            fontsize="0.05pt", fontsizeasian="0.05pt", fontsizecomplex="0.05pt",
        ))
        odt.automaticstyles.addElement(end_pad)
        end_p = P(stylename="ENDPAD")
        end_p.addElement(Span(stylename="TINYZW", text="​"))
        odt.text.addElement(end_p)

    # === Step 5: 寫出 .odt 檔 ===
    odt.save(str(output_path))

    return {
        "ok": True,
        "engine": "jtdt-reform-odt",
        "pages": len(doc_model.pages),
        "tables": stats["tables"],
        "free_paragraphs": stats["free_paragraphs"],
        "images": stats["images"],
        "banner_polygons": stats["banner_polygons"],
        "free_drawings": stats.get("free_drawings", 0),
    }


# --- helper: emit free block (paragraph or heading) ---
def _emit_free_block_paragraph_flow(odt, fb, page, get_text_style, get_para_style) -> None:
    """legacy: paragraph flow + alignment heuristic (留作 fallback)"""
    block = fb.block
    text = (block.text or "").strip()
    if not text:
        return
    lines = list(block.lines) if block.lines else []

    def _line_align(ln) -> str:
        if page.width <= 0 or not ln.bbox:
            return "left"
        cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
        line_w = ln.bbox[2] - ln.bbox[0]
        page_cx = page.width / 2.0
        ratio = (cx - page_cx) / page.width
        if line_w < page.width * 0.7:
            if ratio > 0.10:
                return "right"
            elif ratio < -0.10:
                return "left"
            elif abs(ratio) < 0.05:
                return "center"
        return "left"

    if fb.is_heading and lines:
        block_align = _line_align(lines[0])
        para_style = get_para_style(alignment=block_align, parent="Heading2")
        h = H(outlinelevel=2, stylename=para_style)
        h.addText(text)
        odt.text.addElement(h)
        return

    if lines:
        # 把同 alignment 連續 lines 合到 single paragraph (用 LineBreak 分行)
        # 避免單 line 各自 paragraph 累積過多 text:p 影響 banner frame 渲染
        cur_align = None
        cur_p = None
        for ln in lines:
            ln_text = (ln.text or "").strip()
            if not ln_text:
                continue
            align = _line_align(ln)
            size = float(ln.dominant_size or block.dominant_size or 0)
            font = (ln.dominant_font or block.dominant_font or "").lstrip("+")
            ts = get_text_style(size, font, False, False, "", align)
            if align != cur_align or cur_p is None:
                # alignment 改變 → 開新 paragraph
                if cur_p is not None:
                    odt.text.addElement(cur_p)
                para_style = get_para_style(alignment=align)
                cur_p = P(stylename=para_style)
                cur_align = align
            else:
                cur_p.addElement(LineBreak())
            sp = Span(stylename=ts)
            sp.addText(ln_text)
            cur_p.addElement(sp)
        if cur_p is not None:
            odt.text.addElement(cur_p)
    else:
        size = float(block.dominant_size or 0)
        font = (block.dominant_font or "").lstrip("+")
        for line_text in text.split("\n"):
            line_text = line_text.strip()
            if not line_text:
                continue
            para_style = get_para_style(alignment="left")
            p = P(stylename=para_style)
            ts = get_text_style(size, font, False, False, "", "left")
            sp = Span(stylename=ts)
            sp.addText(line_text)
            p.addElement(sp)
            odt.text.addElement(p)


def _emit_free_block_as_frames(odt, fb, page, get_text_style, get_para_style) -> None:
    """把 FreeBlock 內每 line 用浮動 text-frame 絕對定位（用於 banner Y 區）。"""
    block = fb.block
    text = (block.text or "").strip()
    if not text:
        return
    lines = list(block.lines) if block.lines else []
    if not lines:
        return

    page_w = float(page.width or 0)
    # wrap_p 內只放 page-anchor frames（自身不應佔流高度）。明確給 0 margin +
    # 1pt line-height，否則 OxOffice 預設 ~14pt 會在流中堆出空白行，使後續
    # spacer 把後續內容推到比 PDF 真實 Y 更下面（v1.8.90 fix）
    flat_p_style = Style(name=f"FLAT{id(fb) & 0xFFFFFF}", family="paragraph")
    flat_p_style.addElement(ParagraphProperties(
        margintop="0pt", marginbottom="0pt", lineheight="0.05pt",
    ))
    odt.automaticstyles.addElement(flat_p_style)
    wrap_p = P(stylename=flat_p_style)
    for ln in lines:
        ln_text = (ln.text or "").strip()
        if not ln_text or not ln.bbox:
            continue
        x0, y0, x1, y1 = ln.bbox
        size = float(ln.dominant_size or block.dominant_size or 12)
        # frame 寬：line bbox 寬 + padding；高：字級 × 1.5
        line_w = x1 - x0
        # +20pt buffer 避免 OxOffice 字型 metric 略寬於 PDF 抽出值 → 末字 wrap
        frame_w = max(20.0, line_w + 20.0)
        frame_h = max(size * 1.5, y1 - y0 + 4.0)
        if page_w > 0 and x0 + frame_w > page_w:
            frame_w = page_w - x0
        safe_x = max(0.0, x0)
        safe_y = max(0.0, y0)
        font = (ln.dominant_font or block.dominant_font or "").lstrip("+")
        bold = fb.is_heading or _line_is_bold(ln)
        italic = _line_is_italic(ln)
        color = _line_color(ln) or ("#1f4e79" if fb.is_heading else "")
        gname = f"FFR{id(ln) & 0xFFFFFF}"
        gstyle = Style(name=gname, family="graphic")
        gstyle.addElement(GraphicProperties(
            fill="none", stroke="none",
            wrap="run-through", runthrough="foreground",
            verticalpos="from-top", verticalrel="page",
            horizontalpos="from-left", horizontalrel="page",
            paddingleft="0pt", paddingright="0pt",
            paddingtop="0pt", paddingbottom="0pt",
        ))
        odt.automaticstyles.addElement(gstyle)
        frame = Frame(
            stylename=gstyle,
            width=f"{frame_w:.1f}pt",
            height=f"{frame_h:.1f}pt",
            x=f"{safe_x:.1f}pt",
            y=f"{safe_y:.1f}pt",
            anchortype="page",
            zindex="2",
        )
        tb = TextBox()
        ps = get_para_style(alignment="left", spacing_after_pt=0)
        ts = get_text_style(size, font, bold, italic, color, "left")
        p = P(stylename=ps)
        sp = Span(stylename=ts)
        sp.addText(ln_text)
        p.addElement(sp)
        tb.addElement(p)
        frame.addElement(tb)
        wrap_p.addElement(frame)
    # ZWSP 防 empty paragraph collapse — wrap_p 才會渲 frames
    wrap_p.addElement(Span(stylename="TINYZW", text="​"))
    odt.text.addElement(wrap_p)


def _line_align_from_bbox(ln, page_w: float) -> str:
    """依 line bbox X 推 alignment。"""
    if page_w <= 0 or not ln.bbox:
        return "left"
    cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
    line_w = ln.bbox[2] - ln.bbox[0]
    page_cx = page_w / 2.0
    ratio = (cx - page_cx) / page_w
    if line_w < page_w * 0.7:
        if ratio > 0.10:
            return "right"
        elif ratio < -0.10:
            return "left"
        elif abs(ratio) < 0.05:
            return "center"
    return "left"


def _emit_free_block_in_flow(odt, fb, get_text_style, get_para_style,
                                tight_page: bool = False,
                                page_w: float = 0.0) -> None:
    """把 FreeBlock 內每 line 寫成 paragraph 流 內的 text:p（用於 banner Y 區以下）。
    各 line per-line alignment（left/center/right）依 line bbox X 推。
    spacer paragraph 在 build_odt loop 內處理（push Y）。
    v1.9.37：每 line 用 PDF 真實對齊（之前 hardcode left → user：「中華電信
    hiBox 團隊敬上 / www.hibox.hinet.net 等該靠右 / 置中」沒生效）。
    v1.9.37：每 line 用實際 Y gap 算 spacing_after — 而非統計法估值。
    tight_page=True 時 spacing_after=0 — 用於 content 接近頁面容納極限的
    密集 PDF（PVE / 多頁 report case）。
    """
    block = fb.block
    text = (block.text or "").strip()
    if not text:
        return
    lines = list(block.lines) if block.lines else []
    if not lines:
        para_style = get_para_style(alignment="left")
        p = P(stylename=para_style)
        size = float(block.dominant_size or 0)
        font = (block.dominant_font or "").lstrip("+")
        ts = get_text_style(size, font, False, False, "", "left")
        sp = Span(stylename=ts)
        sp.addText(text)
        p.addElement(sp)
        odt.text.addElement(p)
        return
    for i, ln in enumerate(lines):
        ln_text = (ln.text or "").strip()
        if not ln_text:
            continue
        size = float(ln.dominant_size or block.dominant_size or 0)
        font = (ln.dominant_font or block.dominant_font or "").lstrip("+")
        bold = fb.is_heading or _line_is_bold(ln)
        italic = _line_is_italic(ln)
        color = _line_color(ln) or ("#1f4e79" if fb.is_heading else "")
        # v1.9.37：每 line alignment 從 bbox X 推
        align = _line_align_from_bbox(ln, page_w)
        # v1.9.37：spacing_after_pt 用實際 PDF Y gap（line height 基準扣除
        # natural line height）。Tight page 仍強制 0 避免推爆 page bottom。
        spacing = 0.0
        if not tight_page and ln.bbox:
            cur_bot = float(ln.bbox[3])
            if i + 1 < len(lines):
                # 與下一 line 之 Y gap
                next_ln = lines[i + 1]
                if next_ln.bbox:
                    next_top = float(next_ln.bbox[1])
                    gap = next_top - cur_bot
                    # 扣除 natural line spacing（typically ~size × 0.2）
                    natural = max(2.0, size * 0.2)
                    spacing = max(0.0, gap - natural)
                    # 不要過頭 — cap 30pt
                    spacing = min(spacing, 30.0)
        ps = get_para_style(alignment=align, spacing_after_pt=spacing)
        ts = get_text_style(size, font, bold, italic, color, align)
        p = P(stylename=ps)
        sp = Span(stylename=ts)
        sp.addText(ln_text)
        p.addElement(sp)
        odt.text.addElement(p)


def _emit_table_as_frame(odt, tm, page,
                           get_text_style, get_para_style,
                           get_table_style, get_column_style,
                           get_row_style, get_cell_style) -> None:
    """把 table 內每個非空 cell 個別包成 page-anchor frame（footer 區用）。

    v1.9.2 改：之前把整 row 文字 concat 成單字串 → 失去 cell X 位置（頁:1/1 不靠右）。
    現在每 cell 一個獨立 text-frame，按其 cell bbox X/Y 絕對定位 → 各自對齊 PDF 原位。
    """
    region = tm.region
    n_rows = max(0, len(region.row_ys) - 1)
    n_cols = max(0, len(region.col_xs) - 1)
    for r in range(n_rows):
        cy0 = region.row_ys[r]
        cy1 = region.row_ys[r + 1]
        for c in range(n_cols):
            try:
                blocks = tm.cell_blocks[r][c]
            except Exception:
                blocks = []
            if not blocks:
                continue
            # cell 內 line 真實 bbox（不用 col_xs 的 cell boundary，避免 cell 過寬
            # 文字實際在 cell 內某一側時還對齊到 cell 邊）
            lines = []
            for b in blocks:
                lines.extend(b.lines or [])
            if not lines:
                continue
            tx0 = min(ln.bbox[0] for ln in lines)
            ty0 = min(ln.bbox[1] for ln in lines)
            tx1 = max(ln.bbox[2] for ln in lines)
            ty1 = max(ln.bbox[3] for ln in lines)
            cell_text = " ".join((ln.text or "").strip()
                                  for ln in lines).strip()
            if not cell_text:
                continue
            size = float(lines[0].dominant_size or 10)
            font = (lines[0].dominant_font or "").lstrip("+")
            color = _line_color(lines[0]) or ""
            w_pt = max(20.0, tx1 - tx0 + 6.0)
            h_pt = max(12.0, ty1 - ty0 + 4.0)
            safe_x = max(0.0, tx0 - 1.0)
            safe_y = max(0.0, ty0 - 1.0)
            gname = f"FTBLC{id((r,c)) & 0xFFFFFF}_{r}_{c}"
            gstyle = Style(name=gname, family="graphic")
            gstyle.addElement(GraphicProperties(
                fill="none", stroke="none",
                wrap="run-through", runthrough="foreground",
                verticalpos="from-top", verticalrel="page",
                horizontalpos="from-left", horizontalrel="page",
                paddingleft="0pt", paddingright="0pt",
                paddingtop="0pt", paddingbottom="0pt",
            ))
            odt.automaticstyles.addElement(gstyle)
            frame = Frame(
                stylename=gstyle,
                width=f"{w_pt:.1f}pt",
                height=f"{h_pt:.1f}pt",
                x=f"{safe_x:.1f}pt",
                y=f"{safe_y:.1f}pt",
                anchortype="page",
                zindex="2",
            )
            tb = TextBox()
            ps = get_para_style(alignment="left", spacing_after_pt=0)
            ts = get_text_style(size, font, False, False, color, "left")
            p = P(stylename=ps)
            sp = Span(stylename=ts)
            sp.addText(cell_text)
            p.addElement(sp)
            tb.addElement(p)
            frame.addElement(tb)
            wrap_p_style = Style(name=f"FTBLP{id(frame) & 0xFFFFFF}",
                                   family="paragraph")
            wrap_p_style.addElement(ParagraphProperties(
                margintop="0pt", marginbottom="0pt", lineheight="0.05pt",
            ))
            odt.automaticstyles.addElement(wrap_p_style)
            wp = P(stylename=wrap_p_style)
            wp.addElement(frame)
            wp.addElement(Span(stylename="TINYZW", text="​"))
            odt.text.addElement(wp)


def _emit_free_block(odt, fb, page, get_text_style, get_para_style,
                       banner_y_max: float = 110.0) -> None:
    """主 dispatcher：若 free block 落在 banner Y 區（top ~110pt）→ frame 浮動；
    否則 → paragraph 流（搭配 build_odt 內 spacer push 控制 Y）。
    """
    if fb.block.bbox and float(fb.block.bbox[3]) <= banner_y_max + 10:
        _emit_free_block_as_frames(odt, fb, page, get_text_style, get_para_style)
    else:
        _emit_free_block_in_flow(odt, fb, get_text_style, get_para_style)


def _insert_underscores(text: str, n_blanks: int) -> str:
    """把 text 內最長的 n_blanks 個空格 run 換成等長底線字元（全形底線 ＿）。
    用於 cell 內填寫底線（年__月__日）— 行內底線隨文字移動不脫節。
    label 用的短空格（收　件 2-3 格）不會被選中（只取最長 N 個）。
    """
    import re as _re
    if n_blanks <= 0 or "  " not in text:
        return text
    runs = [(m.start(), m.end(), m.end() - m.start())
            for m in _re.finditer(r" {2,}", text)]
    if not runs:
        return text
    # 取最長的 n_blanks 個 run
    runs_by_len = sorted(runs, key=lambda r: -r[2])[:n_blanks]
    targets = sorted(runs_by_len, key=lambda r: r[0])
    out = []
    prev = 0
    for s, e, ln in targets:
        out.append(text[prev:s])
        # 全形底線數 ≈ run 長度 / 2（全形底線較寬），至少 3
        out.append("＿" * max(3, ln // 2))
        prev = e
    out.append(text[prev:])
    return "".join(out)


# --- helper: emit table ---
def _emit_table(odt, tm,
                  get_text_style, get_para_style,
                  get_table_style, get_column_style,
                  get_row_style, get_cell_style,
                  margintop_pt: float = 0.0,
                  allow_row_expand: bool = True,
                  marginleft_pt: float = 0.0,
                  align: str = "left",
                  underline_rects: list | None = None) -> None:
    """把 TableModel 寫成 <table:table>。margintop_pt 為 table 上方額外間距 (給
    flow 中對齊 PDF Y 用)。marginleft_pt 為 table 左方額外間距（把 PDF 右側
    sub-table 還原回右側）。align="center" 時表格水平置中（印製申請表印製
    申請單 case）。allow_row_expand=True 時若 cell 內容溢出嚴重會擴張 row
    height（page 寬裕情況使用）；緊頁面傳 False。"""
    region = tm.region
    n_rows = max(0, len(region.row_ys) - 1)
    n_cols = max(0, len(region.col_xs) - 1)
    if n_rows == 0 or n_cols == 0:
        return

    col_widths_pt = [region.col_xs[i + 1] - region.col_xs[i]
                       for i in range(n_cols)]
    total_w_pt = sum(col_widths_pt)
    table_style = get_table_style(total_w_pt, align=align,
                                    margintop_pt=margintop_pt,
                                    marginleft_pt=marginleft_pt)
    tbl = Table(stylename=table_style)

    # columns
    for cw in col_widths_pt:
        col_style = get_column_style(cw)
        tbl.addElement(TableColumn(stylename=col_style))

    # rows + cells
    hmerge = getattr(region, "hmerge", None) or [[1] * n_cols for _ in range(n_rows)]
    vmerge = getattr(region, "vmerge", None) or [[""] * n_cols for _ in range(n_rows)]
    # v1.9.34：sparse row（無 inner v-line 證據 & 只 col 0 有內容）→ 強制
    # hmerge 整列合一格。範例：供應商基本資料表 備註 row 只有「備註」一格
    # 該整列只有 outer-frame v-lines，hmerge 抽不到 → 預設 [1,1,1,1,1,1]
    # 結果整列 split 成 6 個獨立 cell，但只 col 0 有內容，右邊框就掉了
    hmerge = [list(row) for row in hmerge]
    raw_v_for_sparse = getattr(region, "raw_v_lines", []) or []
    for r in range(n_rows):
        cy_mid = (region.row_ys[r] + region.row_ys[r + 1]) / 2.0
        rh = region.row_ys[r + 1] - region.row_ys[r]
        # 檢查內部 col 邊界（col_xs[1..n_cols-1]）是否有 v-line 跨此 row
        has_inner_vline = False
        for ci in range(1, n_cols):
            cx = region.col_xs[ci]
            for vl in raw_v_for_sparse:
                if abs(vl[0] - cx) > 3:
                    continue
                vy0, vy1 = vl[1], vl[2]
                # v-line 要覆蓋 row 高度大部分才算
                oy = max(0.0, min(vy1, region.row_ys[r + 1]) - max(vy0, region.row_ys[r]))
                if oy >= rh * 0.5:
                    has_inner_vline = True
                    break
            if has_inner_vline:
                break
        # 計 col 0 是否唯一非空格
        try:
            cell_content_counts = [bool(tm.cell_blocks[r][c]) for c in range(n_cols)]
        except Exception:
            cell_content_counts = [False] * n_cols
        if (not has_inner_vline and cell_content_counts[0]
                and not any(cell_content_counts[1:])):
            # 整列合一格
            hmerge[r][0] = n_cols
            for ci in range(1, n_cols):
                hmerge[r][ci] = 0
    # v1.9.98：寬內容跨空 cell 自動 hmerge。某 cell 的內容行右緣明顯超出本欄
    # 右邊界、右側相鄰 cell 為空、且其間無 inner v-line 阻隔 → 把該 cell 橫向
    # 延伸跨越這些空 cell（申請項目 / 印製方式 / 答案卷 選項橫跨整列 case）。
    # 否則寬內容 (390pt) 塞單欄 (148pt) 會觸發 extra_shrink 縮到 55% 變超小字。
    # 通用：用內容右緣 vs 欄邊界相對比較 + 空 cell 判定，不寫死座標。
    def _vline_blocks(cx: float, r: int) -> bool:
        rh_ = region.row_ys[r + 1] - region.row_ys[r]
        for vl in raw_v_for_sparse:
            if abs(vl[0] - cx) > 3:
                continue
            oy = max(0.0, min(vl[2], region.row_ys[r + 1])
                     - max(vl[1], region.row_ys[r]))
            if oy >= rh_ * 0.5:
                return True
        return False

    for r in range(n_rows):
        for c in range(n_cols):
            if hmerge[r][c] != 1:
                continue  # 已被合併 / 被覆蓋的不處理
            try:
                blks = tm.cell_blocks[r][c]
            except Exception:
                blks = None
            if not blks:
                continue
            content_right = 0.0
            for blk in blks:
                for ln in (blk.lines or []):
                    content_right = max(content_right, float(ln.bbox[2]))
            if content_right <= region.col_xs[c + 1] + 3:
                continue  # 內容在本 cell 內，不需延伸
            span = 1
            cc = c + 1
            while cc < n_cols:
                if _vline_blocks(region.col_xs[cc], r):
                    break  # 有 v-line 阻隔
                try:
                    occupied = bool(tm.cell_blocks[r][cc])
                except Exception:
                    occupied = False
                if occupied:
                    break  # 右側 cell 有內容，不能吃
                span += 1
                if content_right <= region.col_xs[cc + 1] + 3:
                    break  # 內容右緣已涵蓋
                cc += 1
            if span > 1:
                hmerge[r][c] = span
                for k in range(c + 1, c + span):
                    hmerge[r][k] = 0
    # v1.9.14：偵測 vmerge group 內 "continue" rows 是否有自身 content（vertical
    # label cell 同時各 row 還有水平 label 之 case，例：申請表範例 col 0 vertical
    # "申請人基本資料" + 各 row 「公司聯絡電話 / 公司代表人」label）→ 整個
    # vmerge group 改成獨立 cell，內容才不會被 CoveredTableCell 吃掉。
    # 深拷貝 vmerge / cell_blocks 避免污染 region 物件（其他工具會用）
    vmerge = [list(row) for row in vmerge]
    cb = [list(row) for row in tm.cell_blocks] if hasattr(tm, "cell_blocks") else None
    if cb:
        for r in range(n_rows):
            for c in range(n_cols):
                if vmerge[r][c] != "restart":
                    continue
                # 找此 group 跨幾 row
                rr_end = r + 1
                while rr_end < n_rows and vmerge[rr_end][c] == "continue":
                    rr_end += 1
                # 檢查 continue rows 是否有 content
                continue_content: list = []
                for rr in range(r + 1, rr_end):
                    try:
                        for _blk in (cb[rr][c] or []):
                            if (_blk.text or "").strip():
                                continue_content.append(_blk)
                    except Exception:
                        pass
                if continue_content:
                    # 把 continue 行 content 合進 restart cell（保持 vmerge 結構
                    # 讓 vertical label cell 跨 row，又不丟 horizontal label）
                    if r < len(cb) and c < len(cb[r]):
                        cb[r][c] = list(cb[r][c] or []) + continue_content
                    # continue 行清空
                    for rr in range(r + 1, rr_end):
                        if rr < len(cb) and c < len(cb[rr]):
                            cb[rr][c] = []
        # 用修改後的 cb 取代 tm.cell_blocks 給後續 emit 用
        # （但只在此 _emit_table call 內生效，因 cb 是 deep copy）
        class _TmShim:
            pass
        _tm = _TmShim()
        for attr in ("region", "cell_fill"):
            try:
                setattr(_tm, attr, getattr(tm, attr))
            except Exception:
                pass
        _tm.cell_blocks = cb
        tm = _tm  # noqa: F841 — 在此 fn 內覆蓋
    # v1.8.99 改：default 從淺灰 0.5pt 改成接近原 PDF 的深色細邊框 (~#333 / 0.75pt)
    border_color = getattr(region, "border_color_hex", "") or "#222222"
    if border_color and not border_color.startswith("#"):
        border_color = f"#{border_color}"
    bw = getattr(region, "border_width_pt", 0) or 0
    # v1.9.29：原本 min=0.75pt 比 PDF 原邊框（典型 0.3-0.5pt）粗很多
    # 使用者比對發現「轉來 .odt 怎麼變那麼粗」
    # 設 range [0.3, 0.6]：太細看不見，太粗變黑塊
    # 部份 PDF 的 stroke_width 抽出來 1.5pt 是 form-field underline width，不是
    # 真正 cell border width，所以 cap 0.6pt 避免被誤帶過粗
    border_width = max(min(bw or 0.3, 0.6), 0.3)

    # v1.8.99：依 PDF 真實 h_lines / v_lines 算每邊是否該畫
    raw_h = getattr(region, "raw_h_lines", []) or []
    raw_v = getattr(region, "raw_v_lines", []) or []
    HTOL = 3.0  # 線與 cell 邊 ±3pt 視為同位置
    XOL = 5.0  # X overlap 5pt 才算覆蓋

    def _has_h_line_at(y: float, cx0: float, cx1: float) -> bool:
        for lx0, ly, lx1 in raw_h:
            if abs(ly - y) > HTOL:
                continue
            # 線必須覆蓋 cell 大部分 X 範圍
            ox = max(0.0, min(lx1, cx1) - max(lx0, cx0))
            if ox >= (cx1 - cx0) * 0.5 or ox >= XOL:
                return True
        return False

    def _has_v_line_at(x: float, cy0: float, cy1: float) -> bool:
        for lx, ly0, ly1 in raw_v:
            if abs(lx - x) > HTOL:
                continue
            oy = max(0.0, min(ly1, cy1) - max(ly0, cy0))
            if oy >= (cy1 - cy0) * 0.5 or oy >= XOL:
                return True
        return False

    has_raw_lines = bool(raw_h) and bool(raw_v)
    # virtual table = PDF 視覺上沒有 lines、靠 text 對齊推出。
    # v1.9.32：分三種處理：
    # - 1-2 row × 多 col：label / value 對（如「報價日期 到期日 銷售人員」）
    #   → 全無框
    # - 多 row：可能是 mini-table 或 structural section
    #   → 只畫外框 outline（top + bottom + left + right），內部無線
    #   兼顧「報價單未連稅 3-row mini-table」與「廠商基本資料表 底部
    #   注意事項 + 廠商簽章 box」（user 圖 #74 點到下方少框）
    _is_virtual = bool(getattr(region, "is_virtual", False))
    # v1.9.101：virtual table 若有 cell 底色（交替灰白等）→ 純底色表，底色本身
    # 即提供視覺結構，不畫外框（報價單未連稅金額 summary：orig 無框只有灰底，
    # 之前 v1.9.32 outline 在純底色 summary 上多畫一圈黑框 = user 指的「週圍框
    # 線不對」）。無底色的 virtual table 才靠 outline 當視覺線索。
    _has_any_fill = False
    try:
        for _fr in (tm.cell_fill or []):
            for _fv in _fr:
                if _fv:
                    _has_any_fill = True
                    break
            if _has_any_fill:
                break
    except Exception:
        pass
    is_fill_only = (_is_virtual and _has_any_fill)
    is_virtual_label_row = (_is_virtual and not _has_any_fill and n_rows <= 2)
    is_virtual_outline_only = (_is_virtual and not _has_any_fill and n_rows >= 3)
    # v1.9.23：回到 per-cell 邊框偵測。取消 row/col propagation 與 form-grid
    # 補齊 — 報價單因 v-line 涵蓋全 col 但視覺實際無邊框（PDFTruth 抽到
    # 隱形 grid line）會被誤套滿框；申請表上半 PDFTruth 漏抽 inner v-line
    # 也無解。等 PDFTruth 改善後再開自動補齊。
    is_form_grid = False
    row_top_line = [False] * (n_rows + 1)
    col_left_line = [False] * (n_cols + 1)

    for r in range(n_rows):
        row_h = (region.row_ys[r + 1] - region.row_ys[r])
        # v1.9.13：偵測單一 cell 內含 > 可容納 line 數 → 拉高 row 避免內容被截
        # (廠商基本資料表 row[0] "統一編號\n廠商代號\n(身份證號)" 3 line 在 31pt
        # cell 內第 3 行被截掉案)
        max_lines_in_row = 0
        max_size_in_row = 12.0
        try:
            for c in range(n_cols):
                blks = tm.cell_blocks[r][c]
                for blk in blks or []:
                    lns = list(blk.lines) if blk.lines else []
                    # v1.9.98：算「邏輯行數」= 不同 Y band 數，不是原始 line 片段
                    # 數。橫向分開的標籤（「申 請 單 位」4 個同 Y 片段）原本被當
                    # 4 行 → row 被擴張成 4 行高 → 過高（上稿申請表案）。同 Y (±2pt)
                    # 視為同一視覺行。
                    ybands: list[float] = []
                    for ln in lns:
                        ly = float(ln.bbox[1])
                        if not any(abs(ly - yb) <= 2.0 for yb in ybands):
                            ybands.append(ly)
                    n_logical = len(ybands)
                    if n_logical > max_lines_in_row:
                        max_lines_in_row = n_logical
                    for ln in lns:
                        sz = float(ln.dominant_size or 0)
                        if sz > max_size_in_row:
                            max_size_in_row = sz
        except Exception:
            pass
        # 估算需要的高度（每行 size × 1.15 + padding 2pt）
        needed = max_lines_in_row * (max_size_in_row * 1.15) + 2.0
        row_font_shrink = 1.0
        if needed > row_h * 1.0:
            if allow_row_expand:
                row_h = needed
            else:
                # v1.9.94：tight 單頁不可撐高 row（會溢頁 — 402386974 / 6-2 /
                # scan_114 從 1 頁變多頁 regression）。改用縮字但 floor 提高到
                # 0.72（原 0.5 太小可讀性輸 pdf2docx；0.72 兼顧 1 頁不溢 + 字
                # 不會超小）。多頁文件才走上面 allow_row_expand 撐高路徑。
                row_font_shrink = max(0.72, (row_h - 2.0) / max(1.0,
                                       max_lines_in_row * (max_size_in_row * 1.15)))
        row_style = get_row_style(row_h)
        tr = TableRow(stylename=row_style)

        for c in range(n_cols):
            h_span = hmerge[r][c] if r < len(hmerge) and c < len(hmerge[r]) else 1
            v_state = vmerge[r][c] if r < len(vmerge) and c < len(vmerge[r]) else ""

            if h_span == 0:
                # 被左側 cell hmerge 吃掉 — emit CoveredTableCell
                tr.addElement(CoveredTableCell())
                continue
            if v_state == "continue":
                # 被上方 cell vmerge 吃掉 — emit CoveredTableCell
                tr.addElement(CoveredTableCell())
                continue

            # 一般 cell（或 hmerge/vmerge first cell）
            fill_hex = ""
            try:
                fill_hex = tm.cell_fill[r][c]
            except Exception:
                pass
            # 邊框 sides：依 PDF 真值決定該畫哪邊
            cell_has_borders = True
            if is_fill_only:
                # v1.9.101：純底色表（報價單 summary）— 無框，靠 cell 底色呈現
                cell_has_borders = False
                border_sides = (False, False, False, False)
            elif is_virtual_label_row:
                # 1-2 row virtual table（label / value 對）視為無線
                cell_has_borders = False
                border_sides = (False, False, False, False)
            elif is_virtual_outline_only:
                # v1.9.32：3+ row virtual table → 只畫 outer outline，內部無線
                # （兼顧報價單 mini-table 3-row 與廠商基本資料表 底部 box）
                end_c = min(c + h_span, n_cols)
                side_top = (r == 0)
                side_bot = (r == n_rows - 1)
                side_left = (c == 0)
                side_right = (end_c == n_cols)
                border_sides = (side_top, side_right, side_bot, side_left)
                cell_has_borders = any(border_sides)
            elif has_raw_lines:
                end_c = min(c + h_span, n_cols)
                if is_form_grid:
                    # v1.9.22：form-grid table → 所有 cell 4 邊全 border
                    # （PDFTruth 沒抽全 vertical line 也補齊；申請表 case）
                    border_sides = (True, True, True, True)
                else:
                    # partial-line：先用 row/col 過半偵測，再 fallback per-cell
                    side_top = row_top_line[r] or _has_h_line_at(
                        region.row_ys[r], region.col_xs[c], region.col_xs[end_c])
                    side_bot = row_top_line[r + 1] or _has_h_line_at(
                        region.row_ys[r + 1], region.col_xs[c], region.col_xs[end_c])
                    side_left = col_left_line[c] or _has_v_line_at(
                        region.col_xs[c], region.row_ys[r], region.row_ys[r + 1])
                    side_right = col_left_line[end_c] or _has_v_line_at(
                        region.col_xs[end_c], region.row_ys[r], region.row_ys[r + 1])
                    # v1.9.34：outer-frame 永遠保證（最外圈 cell 不論 per-cell
                    # 偵測結果如何，都畫 outer 邊）— 供應商基本資料表 備註 row
                    # 因 inner h-line 部分缺，左右邊框沒抽到 → outer-frame
                    # guarantee 補齊
                    if r == 0:
                        side_top = True
                    if r == n_rows - 1:
                        side_bot = True
                    if c == 0:
                        side_left = True
                    if end_c == n_cols:
                        side_right = True
                    border_sides = (side_top, side_right, side_bot, side_left)
            else:
                border_sides = (True, True, True, True)
            # v1.9.95：cell 垂直對齊依「內容邏輯行數」決定，不再單看 row 高。
            # 表單高 row 內若只放 1-2 行（印製方式 / 印製原稿 / 答案卷 等）orig
            # 與 pdf2docx 都是垂直置中；舊版 (>40pt → top) 讓這些行貼頂、下方
            # 留大片空白，與 orig 不符。只有 3+ 行的多行段落（上稿申請表需求說明 /
            # 環評申請項目）才靠上、依閱讀順序由上往下排。
            _rh_cell = region.row_ys[r + 1] - region.row_ys[r]
            _cell_logical_lines = 0
            try:
                _ybands: list[float] = []
                for _blk in (tm.cell_blocks[r][c] or []):
                    for _ln in (_blk.lines or []):
                        _ly = _ln.bbox[1]
                        if not any(abs(_ly - _yb) <= 2.0 for _yb in _ybands):
                            _ybands.append(_ly)
                _cell_logical_lines = len(_ybands)
            except Exception:
                _cell_logical_lines = 0
            _cell_valign = "top" if (_rh_cell > 40 and _cell_logical_lines >= 3) else "middle"
            # v1.9.89：表格外框邊（perimeter）用較粗線，內格線細 — 還原表單
            # 「粗外框 + 細內線」視覺。outer 邊 = cell 在表格邊界。
            _outer_top = (r == 0)
            _outer_left = (c == 0)
            _outer_right = (c + h_span >= n_cols)
            _outer_bot = (r + 1 >= n_rows)
            outer_sides = (_outer_top, _outer_right, _outer_bot, _outer_left)
            outer_w = max(1.0, border_width * 2.2)
            cell_style = get_cell_style(
                col_widths_pt[c], fill_hex,
                border_color, border_width,
                has_borders=cell_has_borders, vAlign=_cell_valign,
                border_sides=border_sides,
                outer_sides=outer_sides, outer_width_pt=outer_w,
            )
            cell_kwargs = {"stylename": cell_style}
            if h_span > 1:
                cell_kwargs["numbercolumnsspanned"] = str(h_span)
            if v_state == "restart":
                # 計算 vmerge 跨幾 row
                v_span = 1
                for rr in range(r + 1, n_rows):
                    s = vmerge[rr][c] if rr < len(vmerge) and c < len(vmerge[rr]) else ""
                    if s == "continue":
                        v_span += 1
                    else:
                        break
                if v_span > 1:
                    cell_kwargs["numberrowsspanned"] = str(v_span)

            tc = TableCell(**cell_kwargs)

            # 填 cell 內容
            blocks = []
            try:
                blocks = tm.cell_blocks[r][c]
            except Exception:
                pass
            if blocks:
                for blk in blocks:
                    lines = list(blk.lines) if blk.lines else []
                    if not lines:
                        txt = (blk.text or "").strip()
                        if txt:
                            ps = get_para_style(alignment="left")
                            ts = get_text_style(
                                float(blk.dominant_size or 0),
                                (blk.dominant_font or "").lstrip("+"),
                                False, False, "", "left")
                            p = P(stylename=ps)
                            sp = Span(stylename=ts)
                            sp.addText(txt)
                            p.addElement(sp)
                            tc.addElement(p)
                        continue
                    # v1.9.3：同 Y_top (±2pt) 的多 line（merged cell 內多列 label/value
                        # 對) 合併成 1 paragraph 不上下堆，避免 fixed rowheight 把第 2+
                        # 行截掉導致內容消失（如「資本額:X 員工人數:Y」同 Y 不同 X case）
                    def _group_same_y_lines(lns):
                        groups: list[list] = []
                        for ln in lns:
                            placed = False
                            for g in groups:
                                if abs(g[0].bbox[1] - ln.bbox[1]) <= 2.0:
                                    g.append(ln); placed = True; break
                            if not placed:
                                groups.append([ln])
                        return groups
                    line_groups = _group_same_y_lines(lines)
                    for grp in line_groups:
                        # 多 line 同 Y → 按 X 排序串接（中間用 4 個空格）
                        grp_sorted = sorted(grp, key=lambda l: l.bbox[0])
                        # 群組整體右緣（多項合併時用全寬做縮字判定，避免只用首項
                        # 寬度誤判 → 合併行實際更寬卻不縮 → soffice 換行）
                        grp_x1 = max(float(g.bbox[2]) for g in grp_sorted)
                        if len(grp_sorted) > 1:
                            ln_text = "    ".join((g.text or "").strip()
                                                   for g in grp_sorted)
                            ln = grp_sorted[0]
                        else:
                            ln = grp_sorted[0]
                            ln_text = (ln.text or "").strip()
                        if not ln_text:
                            continue
                        # v1.9.91：cell 內填寫底線 → 行內底線字元（隨文字移動，
                        # 不脫節）。找此 line y-band 範圍內的底線 rect，數 N 條，
                        # 把 ln_text 內最長的 N 個空格 run 換成等長底線字元。
                        if underline_rects:
                            _lx0b, _ly0b, _lx1b, _ly1b = ln.bbox
                            _uls = [u for u in underline_rects
                                    if _ly0b - 3 <= u[1] <= _ly1b + 8
                                    and u[0] < _lx1b + 4 and u[2] > _lx0b - 4]
                            if _uls:
                                ln_text = _insert_underscores(
                                    ln_text, len(_uls))
                        # vertical text 偵測（h > w × 3 視為 vertical banner cell）
                        lx0, ly0, lx1, ly1 = ln.bbox
                        ln_w = lx1 - lx0
                        ln_h = ly1 - ly0
                        is_vertical = ln_w > 0 and ln_h > ln_w * 3
                        # 推 cell 內 paragraph alignment
                        cell_bbox = None
                        try:
                            cell_bbox = region.cells[r][c]
                            # v1.9.98：cell 若 hmerge 跨欄，右緣延伸到合併後欄邊界，
                            # 讓 alignment / extra_shrink 用實際可用寬度（否則寬選項
                            # 行仍以單欄寬判定 → 過度縮字）。
                            if h_span > 1 and cell_bbox:
                                _merged_x1 = region.col_xs[min(c + h_span,
                                                              n_cols)]
                                cell_bbox = (cell_bbox[0], cell_bbox[1],
                                             max(cell_bbox[2], _merged_x1),
                                             cell_bbox[3])
                        except Exception:
                            pass
                        align = "left"
                        if cell_bbox and not is_vertical:
                            cx0, _, cx1, _ = cell_bbox
                            cell_w = cx1 - cx0
                            line_w = lx1 - lx0
                            if cell_w > 0 and line_w < cell_w * 0.9:
                                # v1.9.40：先看 line 左邊離 cell 左多近；< cell_w
                                # 5% 視為「貼左 = left aligned」（之前只用 center
                                # 對稱判定，導致長 text 兩端 padding 相近時誤
                                # 判 center — 帳號申請表「可列多組計畫案...」case）
                                left_pad = lx0 - cx0
                                right_pad = cx1 - lx1
                                if left_pad < cell_w * 0.05:
                                    align = "left"
                                elif right_pad < cell_w * 0.05:
                                    align = "right"
                                else:
                                    # v1.9.90：兩端都有 padding（非貼邊）→ 用對稱
                                    # 度判定。表單 label cell（申請人姓名 / 身分
                                    # 確認 等）PDF 常置中，兩 pad 相近 → center。
                                    line_cx = (lx0 + lx1) / 2.0
                                    cell_cx = (cx0 + cx1) / 2.0
                                    offset = (line_cx - cell_cx) / cell_w
                                    if offset > 0.15:
                                        align = "right"
                                    elif offset < -0.15:
                                        align = "left"
                                    else:
                                        # 兩端 padding 大致對稱（差 < 35%）→ center
                                        align = "center"
                        if is_vertical:
                            align = "center"
                        # v1.9.99：分散對齊 label — PDF 常用字間空格排出「申 請 單
                        # 位」分散效果（字均勻撐滿 cell 邊到邊）。偵測短 CJK label
                        # （2~8 字）且字間有空格 → 去掉內部空格改 justify（含
                        # text-align-last=justify 讓單行也分散），與 orig 一致。
                        # 排除含數字 / 冒號 / 底線的（那是填寫欄非純 label）。
                        if (not is_vertical and align != "justify"):
                            _cjk_n = sum(1 for ch in ln_text
                                         if "一" <= ch <= "鿿")
                            if (2 <= _cjk_n <= 8
                                    and re.search(
                                        r"[一-鿿]\s+[一-鿿]",
                                        ln_text)
                                    and not any(c.isdigit() for c in ln_text)
                                    and "：" not in ln_text and ":" not in ln_text
                                    and "_" not in ln_text
                                    and "＿" not in ln_text):
                                ln_text = re.sub(r"\s+", "", ln_text)
                                align = "justify"
                        # 不用 writing-mode (OxOffice 對單 paragraph writing-mode tb-rl
                        # 會 propagate 影響整 cell render)；用「每字 LineBreak」模擬 vertical
                        ps = get_para_style(alignment=align)
                        base_size = float(ln.dominant_size or 0)
                        size = base_size * row_font_shrink
                        # v1.9.14：偵測 line 內容寬度遠大於 cell 寬度（PDF 原文跨
                        # 多 col 但被誤分配到單 cell case，例：申請表範例 row 9
                        # col 2 「共設置幅...羅馬旗桿...」實際 PDF 寬 370pt 但
                        # cell 只 144pt）→ 額外 shrink 字級避免末段被截
                        if cell_bbox and not is_vertical and base_size > 0:
                            cx0, _, cx1, _ = cell_bbox
                            cell_w = cx1 - cx0
                            # v1.9.98：用群組全寬（首項左→末項右）判定，多項合併行
                            # 才不會只看首項寬度而誤判。
                            line_w = max(lx1, grp_x1) - lx0
                            # v1.9.99：內容接近 / 超出 cell 寬就溫和縮字到剛好一行。
                            # 門檻 0.92×（soffice 對 CJK 渲染比 PDF 量測寬 ~8%，留
                            # 安全邊距避免換行 — 申請項目 / 印製方式 選項案）；目標
                            # 0.88× cell 寬；floor 0.55 保留極端超寬案不被截。
                            if cell_w > 0 and line_w > cell_w * 0.92:
                                extra_shrink = max(0.55, cell_w * 0.88 / line_w)
                                size = base_size * row_font_shrink * extra_shrink
                        font = (ln.dominant_font or "").lstrip("+")
                        bold = _line_is_bold(ln)
                        italic = _line_is_italic(ln)
                        color = _line_color(ln)
                        ts = get_text_style(size, font, bold, italic,
                                              color, align)
                        p = P(stylename=ps)
                        if is_vertical:
                            chars_only = [ch for ch in ln_text if ch.strip()]
                            for ci, ch in enumerate(chars_only):
                                if ci > 0:
                                    p.addElement(LineBreak())
                                sp = Span(stylename=ts)
                                sp.addText(ch)
                                p.addElement(sp)
                        else:
                            sp = Span(stylename=ts)
                            sp.addText(ln_text)
                            p.addElement(sp)
                        tc.addElement(p)
            else:
                # 空 cell 也要塞一個 <text:p/> 否則 ODF 不合規
                tc.addElement(P())
            tr.addElement(tc)

        tbl.addElement(tr)

    odt.text.addElement(tbl)


def _line_is_bold(line) -> bool:
    if not line or not line.chars:
        return False
    bold_chars = sum(1 for ch in line.chars if getattr(ch, "is_bold", False))
    return bold_chars > len(line.chars) * 0.5


def _line_is_italic(line) -> bool:
    if not line or not line.chars:
        return False
    it_chars = sum(1 for ch in line.chars if getattr(ch, "is_italic", False))
    return it_chars > len(line.chars) * 0.5


def _line_color(line) -> str:
    if not line or not line.chars:
        return ""
    from collections import Counter
    cnt = Counter((ch.color or "") for ch in line.chars if ch.color)
    if not cnt:
        return ""
    most = cnt.most_common(1)[0][0]
    return most


def _page_is_bad_cmap(page, pdf_path=None) -> bool:
    """偵測 PDF 字型 ToUnicode 表壞掉 → text decode 出大量控制字元 / 非可印
    字元 / 不規則 PUA。常見於 CRS / 國際法務表單用自訂 subset font 沒嵌入正
    確 mapping 的情況。

    v1.9.48 加 font BaseFont 啟發式判斷：page 字型 BaseFont 名稱為「CIDFont+F\\d+」
    類匿名 subset（缺真實字型名）→ 視為 bad ToUnicode（CRS 類 PDF 全部 trigger）

    判定：把所有 free_block + table cell text 串起來，控制字元 (\\x00-\\x1F)
    + 私用區字符 + 非 ASCII / CJK / 標點以外的怪 Unicode 比例 > 0.30 → bad
    """
    # v1.9.48：font BaseFont 啟發式判斷
    # v1.9.79：font 啟發**單獨不可作為 trigger** — 帳號申請表 等合法 PDF 也常用
    # CIDFont+F\d+ 匿名 subset 但 ToUnicode 完全 OK。改為 SOFT signal：font 全
    # 匿名 + 文字 decode 出來明顯壞掉，才視為 bad-cmap。
    anon_cidfont_signal = False
    if pdf_path is not None:
        try:
            import fitz, re as _re
            doc = fitz.open(str(pdf_path))
            if 0 <= page.page_num < doc.page_count:
                fonts = doc.load_page(page.page_num).get_fonts()
                anon_cidfont = sum(1 for f in fonts
                                     if _re.fullmatch(r"CIDFont\+F\d+", f[3] or ""))
                if fonts and anon_cidfont == len(fonts):
                    anon_cidfont_signal = True
            doc.close()
        except Exception:
            pass
    try:
        # v1.9.79：odt_builder 呼叫此函式時傳的是 PageModel (含 free_blocks +
        # tables.cell_blocks)，不是 PDFPage (含 blocks)。兩者都要支援，否則
        # all_text 抽不到內容會 fall-through 到 anon_cidfont 啟發，誤判 (帳號申請表
        # 案：fonts 全匿名 CIDFont+F\d+ 但 ToUnicode 完全正常)。
        all_text = ""
        # PageModel 路徑：free_blocks (FreeBlock 含 .block.lines) + table cells
        fbs = getattr(page, "free_blocks", None)
        if fbs:
            for fb in fbs:
                blk = getattr(fb, "block", None)
                if blk:
                    for ln in (getattr(blk, "lines", None) or []):
                        all_text += (getattr(ln, "text", "") or "")
            for tm in (getattr(page, "tables", None) or []):
                for row in (getattr(tm, "cell_blocks", None) or []):
                    for blks in row:
                        for blk in (blks or []):
                            for ln in (getattr(blk, "lines", None) or []):
                                all_text += (getattr(ln, "text", "") or "")
        else:
            # PDFPage 路徑：直接 page.blocks
            for blk in (getattr(page, "blocks", None) or []):
                for ln in (getattr(blk, "lines", None) or []):
                    all_text += (getattr(ln, "text", "") or "")
        if len(all_text) < 100:
            # 文字太少無法判定；若 anon_cidfont_signal 強 → 視為 bad
            return anon_cidfont_signal
        ctrl = 0   # 控制字元 (PDF should not have)
        latin_ext_unusual = 0  # Latin Extended (CRS-style ToUnicode shift)
        pua = 0
        printable_ascii = 0
        cjk = 0
        space_etc = 0
        for ch in all_text:
            code = ord(ch)
            if code < 0x20:
                if ch in ("\t", "\n", "\r"):
                    space_etc += 1
                else:
                    ctrl += 1
            elif 0x20 <= code <= 0x7E:
                printable_ascii += 1
            elif 0x0100 <= code <= 0x024F:
                latin_ext_unusual += 1
            elif 0xE000 <= code <= 0xF8FF:
                pua += 1
            elif (0x3400 <= code <= 0x9FFF
                    or 0xAC00 <= code <= 0xD7AF):
                cjk += 1
            else:
                space_etc += 1
        total = len(all_text)
        # signal 1: > 5% 控制字元（合法 PDF 幾乎不會有）
        if ctrl / total > 0.05:
            return True
        # signal 2: 字幾乎全 Latin Extended（PDF 偽 ToUnicode 把字 shift 到
        # Latin Extended block）而 ASCII / CJK 極少
        if (latin_ext_unusual / total > 0.30
                and printable_ascii / total < 0.30
                and cjk / total < 0.05):
            return True
        # signal 3: 大量 PUA 字元（FontAwesome icon font 也走這裡，但通常 < 30%）
        if pua / total > 0.50:
            return True
        # v1.9.79：font 全匿名 CIDFont (anon_cidfont_signal) 配合「文字看起來
        # 壞」才視為 bad-cmap。帳號申請表 等合法 PDF 字型全匿名 subset 但 ToUnicode
        # 正常，文字 decode 出來 ASCII + CJK 都正常，這時不可 raster。
        # 觸發條件：font 全匿名 + (printable_ascii + cjk + space) / total < 0.7
        # （正常文字 ASCII+CJK+空格應 > 80%，偽 mapping 後常落在 Latin Ext / PUA）
        if anon_cidfont_signal:
            good_chars = printable_ascii + cjk + space_etc
            if good_chars / total < 0.7:
                return True
        return False
    except Exception:
        return False


def _page_is_vertical_text(page) -> bool:
    """偵測 page 是否為「直書（top-to-bottom）」類型 — 例如直書練習卷、
    田字格作業簿、漢字書寫範本。

    判定條件（保守 / 避免誤判）：
    - 至少 30 個 free_blocks 是「單字行」（text 長度 1）
    - 那些單字行群聚成至少 2 個垂直 column（同 X ±5pt + 字數 ≥ 5）

    匹配時，page 結構整個 raster 成 PNG fallback —「真的沒辦法處理的 再用
    文字方塊」(user)。
    """
    try:
        blocks = page.free_blocks or []
        single_char_lines: list[tuple] = []  # (x_center, y_top, text)
        # 1) free_blocks 內的單字行
        for fb in blocks:
            blk = fb.block
            for ln in (blk.lines or []):
                txt = (ln.text or "").strip()
                if len(txt) == 1:
                    cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
                    single_char_lines.append((cx, ln.bbox[1], txt))
        # 2) tables 內的單字行（田字格 cell 內每格一字 — table_detector 把
        #    田字格抓成超多小表，內部 lines 是單字）
        for tm in (page.tables or []):
            for row in (tm.cell_blocks or []):
                for blks in row:
                    for blk in (blks or []):
                        for ln in (blk.lines or []):
                            txt = (ln.text or "").strip()
                            if len(txt) == 1:
                                cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
                                single_char_lines.append((cx, ln.bbox[1], txt))
        if len(single_char_lines) < 20:
            return False
        # cluster by x (tol 5pt)
        from collections import defaultdict
        col_groups: dict = defaultdict(list)
        for cx, y, t in single_char_lines:
            key = round(cx / 5.0) * 5.0
            col_groups[key].append((y, t))
        # 兩種觸發條件：
        #   1) ≥ 2 條 column 各含 ≥ 5 字（典型 田字格 / 多欄直書）
        #   2) ≥ 1 條 column 含 ≥ 20 字（單欄長直書段落，如練習卷 page 2）
        valid_cols = sum(1 for ys in col_groups.values() if len(ys) >= 5)
        if valid_cols >= 2:
            return True
        long_col = any(len(ys) >= 20 for ys in col_groups.values())
        return long_col
    except Exception:
        return False


def _emit_fillin_underlines(odt, page, row_ys, col_xs) -> int:
    """填寫底線還原：把 page.drawings 內「非表格格線的短橫線」emit 成
    page-anchored draw:Line（年__月__日 / 簽名：__ 等填寫空格）。

    判定填寫底線（vs 表格外框 / 內格線）：
    - 水平 hairline（height < 2pt）
    - 寬度 15 ~ page_w × 0.5（填寫空格長度範圍；全寬線是表格外框）
    - Y 不貼近任何表格 row_y（± 3pt）— 表格 row 邊線由 cell border 處理
    回 emit 的線條數。
    """
    from odf.draw import Line as DrawLine
    drawings = (getattr(page, "raw_drawings", None)
                or getattr(page, "drawings", None) or [])
    if not drawings:
        return 0
    page_w = float(page.width or 595)
    row_set = [float(y) for y in (row_ys or [])]

    def _near_row(y: float) -> bool:
        return any(abs(y - ry) <= 3.0 for ry in row_set)

    # v1.9.91：表格 bbox 內的底線改由 cell text 行內底線字元處理（隨文字移動），
    # 這裡只畫「表格外」(自由區) 的填寫底線 — 避免 page-anchored 線與 reflow 後
    # 的 cell 文字脫節（收件__年__月__日 位置不對 case）。
    table_bboxes = []
    for _tm in (getattr(page, "tables", None) or []):
        try:
            table_bboxes.append([float(v) for v in _tm.region.bbox])
        except Exception:
            pass

    def _in_table(x_mid, y_mid) -> bool:
        for tb in table_bboxes:
            if (tb[0] - 2 <= x_mid <= tb[2] + 2
                    and tb[1] - 2 <= y_mid <= tb[3] + 2):
                return True
        return False

    # 先收集通過基本 filter 的候選段
    cands: list[tuple[float, float, float, object]] = []  # (x0, ymid, x1, dr)
    for dr in drawings:
        if getattr(dr, "type", "") != "rect":
            continue
        bb = dr.bbox
        x0, y0, x1, y1 = float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])
        w = x1 - x0
        h = y1 - y0
        if h >= 2.0 or w < 15.0:
            continue
        if w > page_w * 0.5:
            continue
        ymid = (y0 + y1) / 2.0
        if _near_row(ymid):
            continue
        if _in_table((x0 + x1) / 2.0, ymid):
            continue
        cands.append((x0, ymid, x1, dr))

    # v1.9.100：排除「同 Y 連續多段」= 表格底線 / 裝飾規則線（非填寫底線）。
    # 報價單 line-item 表底線、summary 框下緣是逐欄繪製的多段橫線，page-anchor
    # 後與 reflow 內容脫節 → 穿過文字（像刪除線）。真填寫底線是單一孤立段。
    # 同 Y (±2pt) 分組，組內依 x 排序找相接 run（gap ≤ 4pt）；屬 ≥2 段 run 的
    # 段一律排除，只留孤立單段。
    excluded: set = set()
    _ygroups: dict = {}
    for idx, (x0, ymid, x1, dr) in enumerate(cands):
        placed = False
        for yk in _ygroups:
            if abs(yk - ymid) <= 2.0:
                _ygroups[yk].append(idx)
                placed = True
                break
        if not placed:
            _ygroups[ymid] = [idx]
    for yk, idxs in _ygroups.items():
        ordered = sorted(idxs, key=lambda i: cands[i][0])
        run = [ordered[0]]
        for j in ordered[1:]:
            if cands[j][0] <= cands[run[-1]][2] + 4.0:  # 相接（gap ≤ 4pt）
                run.append(j)
            else:
                if len(run) >= 2:
                    excluded.update(run)
                run = [j]
        if len(run) >= 2:
            excluded.update(run)

    emitted = 0
    seen: set = set()
    for idx, (x0, ymid, x1, dr) in enumerate(cands):
        if idx in excluded:
            continue
        # 去重（同位置多條疊放）
        key = (round(x0), round(ymid), round(x1))
        if key in seen:
            continue
        seen.add(key)
        # 顏色
        col = _normalize_color(getattr(dr, "stroke_color", None)
                               or getattr(dr, "fill_color", None)
                               or "#000000", default="#000000")
        lname = f"ULN{len(seen)}_{round(x0)}_{round(ymid)}"
        lstyle = Style(name=lname, family="graphic")
        lstyle.addElement(GraphicProperties(
            stroke="solid", strokewidth="0.5pt", strokecolor=col,
            wrap="run-through", runthrough="foreground",
            verticalpos="from-top", verticalrel="page",
            horizontalpos="from-left", horizontalrel="page",
        ))
        odt.automaticstyles.addElement(lstyle)
        line = DrawLine(stylename=lstyle,
                         x1=f"{x0:.1f}pt", y1=f"{ymid:.1f}pt",
                         x2=f"{x1:.1f}pt", y2=f"{ymid:.1f}pt",
                         anchortype="page")
        wrap_style = Style(name=f"ULNP{len(seen)}", family="paragraph")
        wrap_style.addElement(ParagraphProperties(
            margintop="0pt", marginbottom="0pt", lineheight="0.05pt"))
        odt.automaticstyles.addElement(wrap_style)
        wp = P(stylename=wrap_style)
        wp.addElement(line)
        wp.addElement(Span(stylename="TINYZW", text="​"))
        odt.text.addElement(wp)
        emitted += 1
    return emitted


# 直書呈現形對照：橫書全形 / 半形標點 → 直書專用 presentation form
# (Unicode U+FE10–FE1F、U+FE30–FE4F；Noto CJK 等字型皆支援)。直書時括號 / 引號 /
# 逗號 / 破折號等若不轉向，會維持橫向擺放與直書不符（直書練習卷案）。
_VERT_PUNCT = {
    "（": "︵", "）": "︶", "(": "︵", ")": "︶",
    "「": "﹁", "」": "﹂", "『": "﹃", "』": "﹄",
    "〔": "﹝", "〕": "﹞", "［": "﹇", "］": "﹈",
    "[": "﹇", "]": "﹈", "｛": "︷", "｝": "︸", "{": "︷", "}": "︸",
    "【": "︻", "】": "︼", "《": "︽", "》": "︾", "〈": "︿", "〉": "﹀",
    "、": "︑", "。": "︒", "，": "︐", "：": "︓", "；": "︔",
    "！": "︕", "？": "︖", "—": "︱", "…": "︙", "‥": "︰",
    "－": "︱", "_": "︳", "＿": "︳",
}


def _to_vertical_punct(text: str) -> str:
    """把字串內的橫書標點換成直書呈現形（供直書逐字堆疊用）。"""
    return "".join(_VERT_PUNCT.get(ch, ch) for ch in text)


def _emit_vertical_text_reflow(odt, page, get_text_style,
                                 get_para_style) -> bool:
    """直書頁 reflow：收集所有單字 line，按傳統直書 reading order（欄由右至
    左、欄內由上至下）重組成橫向可讀文字，emit 成正常段落（可編輯）。

    回 True 表示成功 emit。空白 田字格（無字 cell）不產生內容。

    column 分群：用 x_center cluster（tol = 字寬一半）。同欄內 y 由小到大
    （top→bottom）。欄序由大到小（right→left）。欄與欄之間視 y 起點差異
    決定是否換段（新欄 y_top 明顯高於前欄末字 → 視為新段/新行）。
    """
    chars: list[tuple] = []  # (x_center, y_top, font_size, text)
    single_n = 0
    multi_n = 0
    for fb in (page.free_blocks or []):
        for ln in (fb.block.lines or []):
            t = (ln.text or "").strip()
            if t:
                cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
                sz = float(ln.dominant_size or fb.block.dominant_size or 12)
                chars.append((cx, ln.bbox[1], sz, t))
                if len(t) == 1:
                    single_n += 1
                else:
                    multi_n += 1
    for tm in (page.tables or []):
        for row in (tm.cell_blocks or []):
            for blks in row:
                for blk in (blks or []):
                    for ln in (blk.lines or []):
                        t = (ln.text or "").strip()
                        if t:
                            cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
                            sz = float(ln.dominant_size or 12)
                            chars.append((cx, ln.bbox[1], sz, t))
                            if len(t) == 1:
                                single_n += 1
                            else:
                                multi_n += 1
    if len(chars) < 5:
        return False
    # v1.9.87：只對「真直書」(single-char line 比例 > 90%) reflow。橫書 scan
    # (如 scan_114 有大量 multi-char OCR line，single_ratio 71%) 誤觸發
    # vertical_text 時不可 column-major 重排（會打亂橫書閱讀順序）→ 回 False
    # 走正常 structural rebuild。
    total_lines = single_n + multi_n
    if total_lines == 0 or single_n / total_lines < 0.90:
        return False

    # 估字寬（用中位數 font size）做 column tolerance
    sizes = sorted(c[2] for c in chars)
    med_size = sizes[len(sizes) // 2] or 12.0
    col_tol = max(6.0, med_size * 0.6)

    # column cluster by x_center
    chars_by_x = sorted(chars, key=lambda c: -c[0])  # right first
    columns: list[list[tuple]] = []
    for c in chars_by_x:
        placed = False
        for col in columns:
            # col representative x = average
            colx = sum(m[0] for m in col) / len(col)
            if abs(colx - c[0]) <= col_tol:
                col.append(c)
                placed = True
                break
        if not placed:
            columns.append([c])
    # 欄序：x 大→小（右→左）
    columns.sort(key=lambda col: -sum(m[0] for m in col) / len(col))
    # 欄內：y 小→大（上→下）
    for col in columns:
        col.sort(key=lambda m: m[1])

    # v1.9.88：保留原本直書排版 — 每欄 emit 成 page-anchored 文字框，定位在
    # 該欄原始 (x, y)，欄內字垂直堆疊（per-char LineBreak）。版面精準對齊原
    # PDF + 文字可編輯（取代 reflow 變橫 / raster 圖）。
    text_style = get_text_style(med_size, "", False, False, "", "center")
    emitted = 0
    for col in columns:
        col_chars = [m for m in col if m[3].strip()]
        if not col_chars:
            continue
        col_x = sum(m[0] for m in col_chars) / len(col_chars)
        col_y0 = min(m[1] for m in col_chars)
        col_y1 = max(m[1] for m in col_chars)
        col_size = max(m[2] for m in col_chars)
        w_pt = max(col_size * 1.4, 12.0)
        h_pt = max((col_y1 - col_y0) + col_size * 1.6, col_size * 2)
        safe_x = max(0.0, col_x - w_pt / 2.0)
        safe_y = max(0.0, col_y0 - 1.0)
        gname = f"VCOL{id(col) & 0xFFFFFF}"
        gstyle = Style(name=gname, family="graphic")
        gstyle.addElement(GraphicProperties(
            fill="none", stroke="none",
            wrap="run-through", runthrough="foreground",
            verticalpos="from-top", verticalrel="page",
            horizontalpos="from-left", horizontalrel="page",
            paddingleft="0pt", paddingright="0pt",
            paddingtop="0pt", paddingbottom="0pt",
        ))
        odt.automaticstyles.addElement(gstyle)
        frame = Frame(stylename=gstyle, width=f"{w_pt:.1f}pt",
                       height=f"{h_pt:.1f}pt", x=f"{safe_x:.1f}pt",
                       y=f"{safe_y:.1f}pt", anchortype="page", zindex="2")
        tb = TextBox()
        vps = get_para_style(alignment="center", spacing_after_pt=0)
        p = P(stylename=vps)
        sp = Span(stylename=text_style)
        # 欄內每字一行（垂直堆疊）— 用 LineBreak 分隔；標點轉直書呈現形
        for idx, m in enumerate(col_chars):
            if idx > 0:
                sp.addElement(LineBreak())
            sp.addText(_to_vertical_punct(m[3]))
        p.addElement(sp)
        tb.addElement(p)
        frame.addElement(tb)
        wrap_p_style = Style(name=f"VCOLP{id(frame) & 0xFFFFFF}",
                               family="paragraph")
        wrap_p_style.addElement(ParagraphProperties(
            margintop="0pt", marginbottom="0pt", lineheight="0.05pt"))
        odt.automaticstyles.addElement(wrap_p_style)
        wp = P(stylename=wrap_p_style)
        wp.addElement(frame)
        wp.addElement(Span(stylename="TINYZW", text="​"))
        odt.text.addElement(wp)
        emitted += 1
    return emitted > 0


def _build_free_drawing_frame(odt, bbox, page_num: int, pdf_path,
                                 cluster_id: str = ""):
    """把 free drawing cluster 的區域用 PyMuPDF clip 渲染成 page-anchor PNG。

    PDF / ODT 原理：
    - PDF 內有許多 vector 圖（箭頭、dimension line、自由形狀、starburst 等）
    - 我們的 table / paragraph emitter 無法還原這些 vector → 視覺缺失
    - 用 page.get_pixmap(clip=bbox) 把這塊區域整個 raster 成 PNG，
      包含 text + vector 一起渲；以 page-anchor frame 浮在原位置
    - 對「真的沒辦法 structurally rebuild 的」vector，這是 fallback 路徑
    """
    try:
        import fitz
    except Exception:
        return None
    if not bbox or not pdf_path:
        return None
    x0, y0, x1, y1 = bbox
    w_pt = max(1.0, x1 - x0)
    h_pt = max(1.0, y1 - y0)
    try:
        doc = fitz.open(str(pdf_path))
        if 0 <= page_num < doc.page_count:
            page = doc.load_page(page_num)
            clip = fitz.Rect(x0, y0, x1, y1)
            pix = page.get_pixmap(clip=clip, dpi=200, alpha=True)
            img_bytes = pix.tobytes("png")
            doc.close()
        else:
            doc.close()
            return None
        if not img_bytes:
            return None
    except Exception as e:
        log.debug("free drawing raster failed: %s", e)
        return None
    mediatype = "image/png"
    cid = cluster_id or f"p{page_num}_{int(x0)}_{int(y0)}"
    fname = f"Pictures/freedraw_{cid}.png"
    try:
        href = odt.addPicture(fname, mediatype, img_bytes)
    except Exception as e:
        log.debug("addPicture (free drawing) failed: %s", e)
        return None
    gname = f"FDR_{cid}"
    gstyle = Style(name=gname, family="graphic")
    # v1.9.73：wrap="none" → "run-through" 跟 _build_pdf_image_frame 一致。
    # wrap="none" 雖宣告不影響 flow，實際在 soffice 中大型 frame 會把 wrap_p
    # 後續內容推下頁 (PVE / 簡報 case)。run-through + background 內容穿過 frame。
    gstyle.addElement(GraphicProperties(
        wrap="run-through", runthrough="background",
        verticalpos="from-top", verticalrel="page",
        horizontalpos="from-left", horizontalrel="page",
    ))
    odt.automaticstyles.addElement(gstyle)
    frame = Frame(
        stylename=gstyle,
        width=f"{w_pt:.1f}pt", height=f"{h_pt:.1f}pt",
        x=f"{x0:.1f}pt", y=f"{y0:.1f}pt",
        anchortype="page", zindex="1",
    )
    frame.addElement(ODFImage(href=href, type="simple", show="embed",
                                 actuate="onLoad"))
    return frame


def _build_pdf_image_frame(odt, img, pdf_path):
    """產 PDF image 的 page-anchor Frame element 並把 PNG 加進 ODT；回 Frame 或 None。"""
    try:
        import fitz
    except Exception:
        return None
    if not img or not getattr(img, "xref", None):
        return None
    x0, y0, x1, y1 = img.bbox
    w_pt = max(1.0, x1 - x0)
    h_pt = max(1.0, y1 - y0)
    safe_x = max(0.0, x0)
    safe_y = max(0.0, y0)
    try:
        doc = fitz.open(str(pdf_path))
        # v1.9.44：先用 doc.extract_image() 取原始嵌入 PNG / JPEG（不含
        # 同位置疊放的文字 overlay），避免 cover 類圖片把上方文字 baked in
        # 造成「圖片 + 文字 emit」雙重顯示（ODF Web 雲端應用 case）。
        # extract_image 失敗或回不到 PNG/JPEG 才 fallback 用 get_pixmap clip。
        img_bytes = None; ext = "png"
        try:
            pix_data = doc.extract_image(img.xref)
            _b = pix_data.get("image") if pix_data else None
            _ext = pix_data.get("ext") if pix_data else None
            # v1.9.96：有 SMask（透明遮罩）的圖（如 logo）不可直接用 extract_image
            # 的 base RGB — 透明區會變黑底（報價單 logo 案）。改走 get_pixmap
            # (alpha=True) 把 base + SMask 合成，保留透明。
            _has_smask = bool(pix_data.get("smask")) if pix_data else False
            if _b and _ext in ("png", "jpeg", "jpg") and not _has_smask:
                img_bytes = _b
                ext = _ext
        except Exception:
            pass
        if img_bytes is None and img.page_num < doc.page_count:
            page = doc.load_page(img.page_num)
            clip = fitz.Rect(*img.bbox)
            try:
                pix = page.get_pixmap(clip=clip, dpi=200, alpha=True)
                img_bytes = pix.tobytes("png")
                ext = "png"
            except Exception:
                pass
        doc.close()
        if not img_bytes:
            return None
    except Exception as e:
        log.debug("extract_image xref=%s failed: %s", img.xref, e)
        return None
    mediatype = f"image/{ext}"
    fname = f"Pictures/pdf_img_{img.xref}.{ext}"
    try:
        href = odt.addPicture(fname, mediatype, img_bytes)
    except Exception as e:
        log.debug("addPicture failed: %s", e)
        return None
    gname = f"IMG{img.xref}"
    gstyle = Style(name=gname, family="graphic")
    # v1.9.72：wrap="none" 在 soffice 中可能讓大型 cover image 把 wrap_p 後續
    # 內容推到下頁 (Release Notes / MSE / PVE 等 cover page 案 p0 空白 + 內容
    # 推到 p1)。改 wrap="run-through" + runthrough="background" 讓內容穿過 frame。
    gstyle.addElement(GraphicProperties(
        wrap="run-through", runthrough="background",
        verticalpos="from-top", verticalrel="page",
        horizontalpos="from-left", horizontalrel="page",
    ))
    odt.automaticstyles.addElement(gstyle)
    frame = Frame(
        stylename=gstyle,
        width=f"{w_pt:.1f}pt", height=f"{h_pt:.1f}pt",
        x=f"{safe_x:.1f}pt", y=f"{safe_y:.1f}pt",
        anchortype="page", zindex="3",
    )
    frame.addElement(ODFImage(href=href, type="simple", show="embed",
                                 actuate="onLoad"))
    return frame


def _emit_pdf_image(odt, img, pdf_path) -> None:
    """從 PDF 抽 PDFImage 對應的真實 image bytes，作浮動 frame insert 到 ODT。

    PDFImage 有 xref（PDF 內部 reference），用 fitz `extract_image(xref)` 取
    image_bytes + ext。然後 addPicture + Frame 放在 PDF 真實 bbox 位置。
    """
    try:
        import fitz
    except Exception:
        return
    if not img or not getattr(img, "xref", None):
        return
    x0, y0, x1, y1 = img.bbox
    w_pt = max(1.0, x1 - x0)
    h_pt = max(1.0, y1 - y0)
    safe_x = max(0.0, x0)
    safe_y = max(0.0, y0)
    try:
        doc = fitz.open(str(pdf_path))
        # 對 PDF 內有 SMask 的透明 PNG（如 logo），用 page.get_pixmap clip 渲染整塊
        # bbox 區域得到含 alpha 的 PNG，避免直接 extract_image 只拿 base RGB 結果黑底。
        if img.page_num < doc.page_count:
            page = doc.load_page(img.page_num)
            clip = fitz.Rect(*img.bbox)
            try:
                pix = page.get_pixmap(clip=clip, dpi=200, alpha=True)
                img_bytes = pix.tobytes("png")
                ext = "png"
            except Exception:
                pix_data = doc.extract_image(img.xref)
                img_bytes = pix_data.get("image")
                ext = pix_data.get("ext", "png")
        else:
            pix_data = doc.extract_image(img.xref)
            img_bytes = pix_data.get("image")
            ext = pix_data.get("ext", "png")
        doc.close()
        if not img_bytes:
            return
    except Exception as e:
        log.debug("extract_image xref=%s failed: %s", img.xref, e)
        return
    mediatype = f"image/{ext}"
    fname = f"Pictures/pdf_img_{img.xref}.{ext}"
    try:
        href = odt.addPicture(fname, mediatype, img_bytes)
    except Exception as e:
        log.debug("addPicture failed: %s", e)
        return
    # v1.9.18：判斷是否為「全頁背景圖」（如簡報 PDF 的背景影像）— 若 image
    # bbox 涵蓋 page > 80%，runthrough=background 讓文字浮在其上；否則
    # runthrough=foreground 保持原行為（標題 logo 等）。背景圖如果用 foreground，
    # 整頁變不可放任何 flow 元素 → 頁面倍增。
    try:
        from .paragraph_grouper import PageModel as _PM  # type: ignore  # noqa: F401
    except Exception:
        pass
    is_full_page_bg = False
    try:
        # 推估 page 大小：用 image bbox 對照 typical A4/landscape — 寬度 > 700
        # 且高度 > 400（或反過來），且起點 < 30 → 視為全頁背景
        if (x1 - x0) > 700 and (y1 - y0) > 400 and x0 < 30 and y0 < 30:
            is_full_page_bg = True
    except Exception:
        pass
    gname = f"IMG{img.xref}"
    gstyle = Style(name=gname, family="graphic")
    # v1.9.73：wrap="none" → "run-through" 跟其他 frame builder 一致
    gstyle.addElement(GraphicProperties(
        wrap="run-through",
        runthrough="background" if is_full_page_bg else "foreground",
        verticalpos="from-top",
        verticalrel="page",
        horizontalpos="from-left",
        horizontalrel="page",
    ))
    odt.automaticstyles.addElement(gstyle)
    # 流中 wrapper paragraph 0 行高（v1.8.90）— 否則預設 14pt 行高會把後續 spacer 推
    flat_style = Style(name=f"IMGP{img.xref}", family="paragraph")
    flat_style.addElement(ParagraphProperties(
        margintop="0pt", marginbottom="0pt", lineheight="0.05pt",
    ))
    odt.automaticstyles.addElement(flat_style)
    p = P(stylename=flat_style)
    frame = Frame(
        stylename=gstyle,
        width=f"{w_pt:.1f}pt",
        height=f"{h_pt:.1f}pt",
        x=f"{safe_x:.1f}pt",
        y=f"{safe_y:.1f}pt",
        anchortype="page",
        zindex="3",
    )
    frame.addElement(ODFImage(href=href, type="simple", show="embed",
                                 actuate="onLoad"))
    p.addElement(frame)
    # ZWSP 防 empty-paragraph collapse
    p.addElement(Span(stylename="TINYZW", text="​"))
    odt.text.addElement(p)


def _group_banners_by_container(banners: list) -> tuple[list[list], list]:
    """把 banner_rects 分組：outer container + 內部 polygon → 一組；其他 → 單獨。

    判斷 outer container：寬高 ≥ 80pt + 無 path_points（純矩形 frame）+ 寬 / 高
    都比其他 banner 大很多。

    回 (grouped: list of [container, *inner_polygons], ungrouped: list of single banners)
    """
    if not banners:
        return [], []
    # 找 container candidates：no path + size > 80
    containers = []
    others = []
    for b in banners:
        x0, y0, x1, y1, _, pts = b[:6]
        w, h = x1 - x0, y1 - y0
        if w > 80 and h > 80 and not pts:
            containers.append(b)
        else:
            others.append(b)
    # 每個 container 找包含的 inner polygons
    grouped: list[list] = []
    consumed: set = set()
    for c in containers:
        cx0, cy0, cx1, cy1 = c[0], c[1], c[2], c[3]
        inner = []
        for i, b in enumerate(others):
            if i in consumed:
                continue
            bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]
            bcx = (bx0 + bx1) / 2.0
            bcy = (by0 + by1) / 2.0
            if cx0 <= bcx <= cx1 and cy0 <= bcy <= cy1:
                inner.append(b)
                consumed.add(i)
        if inner:
            grouped.append([c] + inner)
        else:
            # container 自己沒 inner polygon → 單獨 emit (細邊框)
            grouped.append([c])
    ungrouped = [b for i, b in enumerate(others) if i not in consumed]
    return grouped, ungrouped


def _build_banner_group_frame(odt, group: list):
    """產 group（container + inner polygons）合一張 PNG 的 page-anchor Frame；回 Frame 或 None。"""
    if not group:
        return None
    container = group[0]
    inners = group[1:] if len(group) > 1 else []
    x0, y0, x1, y1, fill_hex, path_points = container[:6]
    w_pt = max(0.1, x1 - x0)
    h_pt = max(0.1, y1 - y0)
    try:
        from PIL import Image, ImageDraw
        SCALE = 4
        cw = max(8, int(w_pt * SCALE))
        ch = max(8, int(h_pt * SCALE))
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        cfill = _normalize_color(fill_hex, default="")
        if cfill and cfill.lower() not in ("#ffffff", "#fff"):
            cf = cfill.lstrip("#")
            r = int(cf[0:2], 16); g = int(cf[2:4], 16); b = int(cf[4:6], 16)
            d.rectangle([(0, 0), (cw - 1, ch - 1)], fill=(r, g, b, 255))
        d.rectangle([(1, 1), (cw - 2, ch - 2)],
                      outline=(0, 0, 0, 255), width=max(2, SCALE // 2))
        for inner in inners:
            ix0, iy0, ix1, iy1, ifill, ipath = inner[:6]
            i_fill_c = _normalize_color(ifill, default="")
            i_is_white = (not i_fill_c) or i_fill_c.lower() in ("#ffffff", "#fff")
            if ipath and len(ipath) >= 3:
                pts = [(int((p[0] - x0) * SCALE), int((p[1] - y0) * SCALE))
                       for p in ipath]
                if i_is_white:
                    d.line(pts + [pts[0]], fill=(0, 0, 0, 255), width=max(2, SCALE))
                else:
                    cf2 = i_fill_c.lstrip("#")
                    r = int(cf2[0:2], 16); g = int(cf2[2:4], 16); b = int(cf2[4:6], 16)
                    d.polygon(pts, fill=(r, g, b, 255))
            else:
                rx0 = int((ix0 - x0) * SCALE); ry0 = int((iy0 - y0) * SCALE)
                rx1 = int((ix1 - x0) * SCALE); ry1 = int((iy1 - y0) * SCALE)
                if i_is_white:
                    d.rectangle([(rx0, ry0), (rx1 - 1, ry1 - 1)],
                                  outline=(0, 0, 0, 255), width=2)
                else:
                    cf2 = i_fill_c.lstrip("#")
                    r = int(cf2[0:2], 16); g = int(cf2[2:4], 16); b = int(cf2[4:6], 16)
                    d.rectangle([(rx0, ry0), (rx1 - 1, ry1 - 1)],
                                  fill=(r, g, b, 255))
        png_io = BytesIO()
        img.save(png_io, format="PNG")
        png_data = png_io.getvalue()
    except Exception as e:
        log.debug("group PNG render failed: %s", e)
        return None
    href = odt.addPicture(
        f"Pictures/banner_group_{id(group) & 0xFFFFFF}.png",
        "image/png", png_data,
    )
    gname = f"BGRP{id(group) & 0xFFFF}"
    gstyle = Style(name=gname, family="graphic")
    gstyle.addElement(GraphicProperties(
        wrap="run-through", runthrough="background",
        verticalpos="from-top", verticalrel="page",
        horizontalpos="from-left", horizontalrel="page",
    ))
    odt.automaticstyles.addElement(gstyle)
    safe_x = max(0.0, x0)
    safe_y = max(0.0, y0)
    frame = Frame(
        stylename=gstyle,
        width=f"{w_pt:.1f}pt", height=f"{h_pt:.1f}pt",
        x=f"{safe_x:.1f}pt", y=f"{safe_y:.1f}pt",
        anchortype="page", zindex="0",
    )
    frame.addElement(ODFImage(href=href, type="simple", show="embed",
                                 actuate="onLoad"))
    return frame


def _emit_banner_group(odt, group: list) -> None:
    """把一組 banner（container + 多個 inner polygon）合成單一 PNG 渲為 anchor frame。

    PNG 內：
      - container 細黑邊框（若 container 有 fill 也填）
      - 各 inner polygon outline（在 container 內按相對座標位置畫）
    """
    if not group:
        return
    container = group[0]
    inners = group[1:] if len(group) > 1 else []
    x0, y0, x1, y1, fill_hex, path_points = container[:6]
    w_pt = max(0.1, x1 - x0)
    h_pt = max(0.1, y1 - y0)
    try:
        from PIL import Image, ImageDraw
        SCALE = 4
        cw = max(8, int(w_pt * SCALE))
        ch = max(8, int(h_pt * SCALE))
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # container：fill (非白) + 細邊框
        cfill = _normalize_color(fill_hex, default="")
        if cfill and cfill.lower() not in ("#ffffff", "#fff"):
            cf = cfill.lstrip("#")
            r = int(cf[0:2], 16); g = int(cf[2:4], 16); b = int(cf[4:6], 16)
            d.rectangle([(0, 0), (cw - 1, ch - 1)], fill=(r, g, b, 255))
        # container outline (thin border)
        d.rectangle([(1, 1), (cw - 2, ch - 2)],
                      outline=(0, 0, 0, 255), width=max(2, SCALE // 2))
        # inner polygons
        for inner in inners:
            ix0, iy0, ix1, iy1, ifill, ipath = inner[:6]
            i_fill_c = _normalize_color(ifill, default="")
            i_is_white = (not i_fill_c) or i_fill_c.lower() in ("#ffffff", "#fff")
            if ipath and len(ipath) >= 3:
                # 轉成 container 座標系（再 × SCALE）
                pts = [(int((p[0] - x0) * SCALE), int((p[1] - y0) * SCALE))
                       for p in ipath]
                if i_is_white:
                    d.line(pts + [pts[0]], fill=(0, 0, 0, 255),
                             width=max(2, SCALE))
                else:
                    cf2 = i_fill_c.lstrip("#")
                    r = int(cf2[0:2], 16); g = int(cf2[2:4], 16); b = int(cf2[4:6], 16)
                    d.polygon(pts, fill=(r, g, b, 255))
            else:
                # 沒 path → 內部 rect (應該不常見)
                rx0 = int((ix0 - x0) * SCALE)
                ry0 = int((iy0 - y0) * SCALE)
                rx1 = int((ix1 - x0) * SCALE)
                ry1 = int((iy1 - y0) * SCALE)
                if i_is_white:
                    d.rectangle([(rx0, ry0), (rx1 - 1, ry1 - 1)],
                                  outline=(0, 0, 0, 255), width=2)
                else:
                    cf2 = i_fill_c.lstrip("#")
                    r = int(cf2[0:2], 16); g = int(cf2[2:4], 16); b = int(cf2[4:6], 16)
                    d.rectangle([(rx0, ry0), (rx1 - 1, ry1 - 1)],
                                  fill=(r, g, b, 255))
        png_io = BytesIO()
        img.save(png_io, format="PNG")
        png_data = png_io.getvalue()
    except Exception as e:
        log.debug("group PNG render failed: %s", e)
        return
    # 加入 ODT
    href = odt.addPicture(
        f"Pictures/banner_group_{id(group) & 0xFFFFFF}.png",
        "image/png", png_data,
    )
    gname = f"BGRP{id(group) & 0xFFFF}"
    gstyle = Style(name=gname, family="graphic")
    gstyle.addElement(GraphicProperties(
        wrap="run-through", runthrough="background",
        verticalpos="from-top", verticalrel="page",
        horizontalpos="from-left", horizontalrel="page",
    ))
    odt.automaticstyles.addElement(gstyle)
    safe_x = max(0.0, x0)
    safe_y = max(0.0, y0)
    flat_style = Style(name=f"BGP{id(group) & 0xFFFFFF}", family="paragraph")
    flat_style.addElement(ParagraphProperties(
        margintop="0pt", marginbottom="0pt", lineheight="0.05pt",
    ))
    odt.automaticstyles.addElement(flat_style)
    p = P(stylename=flat_style)
    frame = Frame(
        stylename=gstyle,
        width=f"{w_pt:.1f}pt",
        height=f"{h_pt:.1f}pt",
        x=f"{safe_x:.1f}pt",
        y=f"{safe_y:.1f}pt",
        anchortype="page",
        zindex="0",
    )
    frame.addElement(ODFImage(href=href, type="simple", show="embed",
                                 actuate="onLoad"))
    p.addElement(frame)
    p.addElement(Span(stylename="TINYZW", text="​"))
    odt.text.addElement(p)


def _build_banner_polygon_frame(odt, banner, pdf_path: Path | None = None,
                                  page_num: int = 0):
    """產 banner polygon 的 page-anchor Frame element 並把 PNG 加進 ODT；回 Frame 或 None。

    v1.9.23：outer container（白底大框）+ 提供 pdf_path → 改 raster orig PDF
    clip 區域，保留 vector 內容（箭頭 / 尺規線 / 自由繪圖元素），例：
    申請表「廣告物材質」box 內 150cm / 60cm 標註與箭頭。
    """
    if len(banner) >= 6:
        x0, y0, x1, y1, fill_hex, path_points = banner[:6]
    elif len(banner) >= 5:
        x0, y0, x1, y1, fill_hex = banner[:5]
        path_points = []
    else:
        return None
    fill_c = _normalize_color(fill_hex, default="")
    is_white_fill = (not fill_c) or fill_c.lower() in ("#ffffff", "#fff")
    has_path = bool(path_points and len(path_points) >= 3)
    page_w_loc = float(x1) - float(x0)
    page_h_loc = float(y1) - float(y0)
    is_outer_container = (is_white_fill and not has_path
                           and page_w_loc > 80 and page_h_loc > 80)
    if is_white_fill and not has_path and not is_outer_container:
        return None
    w_pt = max(0.1, x1 - x0)
    h_pt = max(0.1, y1 - y0)
    # v1.9.23：outer container 改 raster orig PDF region 保留 vector
    rasterized_data = None
    if is_outer_container and pdf_path:
        try:
            import fitz as _fitz
            _doc = _fitz.open(str(pdf_path))
            if 0 <= page_num < _doc.page_count:
                _page = _doc.load_page(page_num)
                clip = _fitz.Rect(x0, y0, x1, y1)
                _pix = _page.get_pixmap(clip=clip, dpi=200, alpha=True)
                rasterized_data = _pix.tobytes("png")
            _doc.close()
        except Exception as e:
            log.debug("raster outer container failed: %s", e)
    try:
        from PIL import Image, ImageDraw
        SCALE = 4
        cw = max(8, int(w_pt * SCALE))
        ch = max(8, int(h_pt * SCALE))
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if rasterized_data is not None:
            png_data = rasterized_data
        elif is_outer_container:
            d.rectangle([(1, 1), (cw - 2, ch - 2)],
                          outline=(0, 0, 0, 255), width=max(2, SCALE // 2))
        elif has_path:
            pts = [(int((p[0] - x0) * SCALE), int((p[1] - y0) * SCALE)) for p in path_points]
            if is_white_fill:
                d.line(pts + [pts[0]], fill=(0, 0, 0, 255), width=max(2, SCALE))
            else:
                c_hex = fill_c.lstrip("#")
                r = int(c_hex[0:2], 16); g = int(c_hex[2:4], 16); b = int(c_hex[4:6], 16)
                d.polygon(pts, fill=(r, g, b, 255))
        else:
            c_hex = fill_c.lstrip("#")
            r = int(c_hex[0:2], 16); g = int(c_hex[2:4], 16); b = int(c_hex[4:6], 16)
            d.rectangle([(0, 0), (cw - 1, ch - 1)], fill=(r, g, b, 255))
        if rasterized_data is None:
            png_io = BytesIO()
            img.save(png_io, format="PNG")
            png_data = png_io.getvalue()
    except Exception as e:
        log.debug("polygon PNG render failed: %s", e)
        return None
    href = odt.addPicture(
        f"Pictures/banner_{id(banner) & 0xFFFFFF}.png",
        "image/png", png_data,
    )
    gname = f"BANNER{id(banner) & 0xFFFF}"
    gstyle = Style(name=gname, family="graphic")
    gstyle.addElement(GraphicProperties(
        wrap="run-through", runthrough="background",
        verticalpos="from-top", verticalrel="page",
        horizontalpos="from-left", horizontalrel="page",
    ))
    odt.automaticstyles.addElement(gstyle)
    safe_x = max(0.0, x0)
    safe_y = max(0.0, y0)
    frame = Frame(
        stylename=gstyle,
        width=f"{w_pt:.1f}pt", height=f"{h_pt:.1f}pt",
        x=f"{safe_x:.1f}pt", y=f"{safe_y:.1f}pt",
        anchortype="page", zindex="0",
    )
    frame.addElement(ODFImage(href=href, type="simple", show="embed",
                                 actuate="onLoad"))
    return frame


def _emit_banner_polygon(odt, banner) -> None:
    """把 banner（含 path_points）渲成 PNG 加入 ODT 作為 floating image。

    本來想用 <draw:polygon> ODF 原生 shape，但 OxOffice 對 polygon 屬性
    （anchor-type / viewbox / position）解讀 brittle，實測渲不出。改用 PIL
    把 polygon vertices 渲成 transparent PNG → <draw:frame> + <draw:image>
    floating（與 docx_builder 同邏輯）。檔案大一點點，但視覺穩定可靠。
    """
    if len(banner) >= 6:
        x0, y0, x1, y1, fill_hex, path_points = banner[:6]
    elif len(banner) >= 5:
        x0, y0, x1, y1, fill_hex = banner[:5]
        path_points = []
    else:
        return
    fill_c = _normalize_color(fill_hex, default="")
    is_white_fill = (not fill_c) or fill_c.lower() in ("#ffffff", "#fff")
    has_path = bool(path_points and len(path_points) >= 3)
    # 白色 fill 在白頁不可見：若有 path_points (印章 / seal 風格 polygon) 改畫黑色
    # 描邊；否則若是「大型矩形容器」(outer container box for 設置圖樣 區) 改畫
    # 黑色細線框（v1.8.91）；其餘的純白 fill 才 skip。
    page_w_loc = float(x1) - float(x0)
    page_h_loc = float(y1) - float(y0)
    is_outer_container = (is_white_fill and not has_path
                           and page_w_loc > 80 and page_h_loc > 80)
    if is_white_fill and not has_path and not is_outer_container:
        return
    w_pt = max(0.1, x1 - x0)
    h_pt = max(0.1, y1 - y0)
    try:
        from PIL import Image, ImageDraw
        SCALE = 4
        cw = max(8, int(w_pt * SCALE))
        ch = max(8, int(h_pt * SCALE))
        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if is_outer_container:
            # 外框：細黑邊框矩形（v1.8.91，覆蓋「三、設置圖樣」的 outer container）
            d.rectangle([(1, 1), (cw - 2, ch - 2)],
                          outline=(0, 0, 0, 255), width=max(2, SCALE // 2))
        elif has_path:
            pts = [(int((p[0] - x0) * SCALE), int((p[1] - y0) * SCALE)) for p in path_points]
            if is_white_fill:
                # 印章描邊 only — 黑色 stroke + 不填色（保留頁面背景）
                # PIL polygon outline 預設 1px，SCALE=4 太細看不到；先 fill 後再 line
                # 用 ImageDraw.line(pts+[pts[0]], width=N) 才能控厚度
                d.line(pts + [pts[0]], fill=(0, 0, 0, 255), width=max(2, SCALE))
            else:
                c_hex = fill_c.lstrip("#")
                r = int(c_hex[0:2], 16); g = int(c_hex[2:4], 16); b = int(c_hex[4:6], 16)
                d.polygon(pts, fill=(r, g, b, 255))
        else:
            c_hex = fill_c.lstrip("#")
            r = int(c_hex[0:2], 16); g = int(c_hex[2:4], 16); b = int(c_hex[4:6], 16)
            d.rectangle([(0, 0), (cw - 1, ch - 1)], fill=(r, g, b, 255))
        png_io = BytesIO()
        img.save(png_io, format="PNG")
        png_data = png_io.getvalue()
    except Exception as e:
        log.debug("polygon PNG render failed: %s", e)
        return

    # 把 PNG 加入 ODT package 內 Pictures/ 資料夾。
    # odfpy _savePictures: zip path = arcname, manifest fullpath = folder + arcname。
    # filename 帶 "Pictures/" 前綴讓兩者都對 Pictures/banner_xxx.png。
    href = odt.addPicture(
        f"Pictures/banner_{id(banner) & 0xFFFFFF}.png",
        "image/png", png_data,
    )
    # v1.9.73: wrap="none" → "run-through" 跟其他 frame builder 統一
    gname = f"BANNER{id(banner) & 0xFFFF}"
    gstyle = Style(name=gname, family="graphic")
    gstyle.addElement(GraphicProperties(
        wrap="run-through",
        runthrough="background",
        verticalpos="from-top",
        verticalrel="page",
        horizontalpos="from-left",
        horizontalrel="page",
    ))
    odt.automaticstyles.addElement(gstyle)
    # banner y/x clamp 到 0 以上 — OxOffice 對負 y page-anchor 不渲
    safe_x = max(0.0, x0)
    safe_y = max(0.0, y0)
    # 流中 wrapper paragraph 須給 0pt 行高 + 0pt 邊距，否則 OxOffice 預設行高
    # ~14pt 會在 flow 內堆出空白，後面 spacer 就把後續內容推到更下面（v1.8.90 fix）
    flat_style = Style(name=f"BANP{id(banner) & 0xFFFFFF}", family="paragraph")
    flat_style.addElement(ParagraphProperties(
        margintop="0pt", marginbottom="0pt", lineheight="0.05pt",
    ))
    odt.automaticstyles.addElement(flat_style)
    p = P(stylename=flat_style)
    frame = Frame(
        stylename=gstyle,
        width=f"{w_pt:.1f}pt",
        height=f"{h_pt:.1f}pt",
        x=f"{safe_x:.1f}pt",
        y=f"{safe_y:.1f}pt",
        anchortype="page",
        zindex="0",
    )
    frame.addElement(ODFImage(href=href, type="simple", show="embed",
                                 actuate="onLoad"))
    p.addElement(frame)
    # **重要**：在 paragraph 內加 zero-width space，避免 OxOffice 把 empty
    # paragraph collapse 成 0 高度導致 frame 不渲（v83 bisect 確認的 OxOffice bug）
    p.addElement(Span(stylename="TINYZW", text="​"))
    odt.text.addElement(p)

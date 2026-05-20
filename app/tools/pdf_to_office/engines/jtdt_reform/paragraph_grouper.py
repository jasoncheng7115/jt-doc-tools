"""把 PDFTruth blocks 分配到 table cells 或 free paragraphs（B3 v2 line-level）。

v1 缺陷：以「block 中心點」配 cell，導致一個 multi-line block（如「報價日期\\n2023年05月07日」）
整段塞進單一 cell，下方的 value cell 變空，視覺亂掉。

v2 改用 **line-level**：對每個 PDFTruth.line 分別找對應 cell（用 line 中心點），
這樣同 block 不同 line 可以分到不同 cells（label 在一格、value 在另一格）。
非 table 內的 lines 用 Y 距群聚回 free paragraph。

PageModel 內變動：
- TableModel.cell_lines[r][c] = list[PDFLine]（取代 cell_blocks）
- 仍保留 cell_blocks（每 cell 對應 PDFLine 之 dummy block wrapper）給 docx_builder 用
- free_blocks 用「相鄰 line 群聚」重建（同 Y 範圍 + 字級相近視為一段）

「標題 / 頁尾」標籤基於 line 群聚後的 block 字級 / Y 位置判定。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ...pdf_truth.models import PDFBlock, PDFLine
from .table_detector import TableRegion, block_in_table, detect_tables
from .virtual_table_detector import detect_virtual_tables


HEADING_FONT_RATIO = 1.2
FOOTER_Y_RATIO = 0.92
LINE_GROUP_Y_GAP_RATIO = 0.8  # line Y 距 > dominant_size × 此 → 拆段（v1.8.83 從 1.6 降，避免標題 + 申請日期 + section heading 合段）


@dataclass
class FreeBlock:
    """非表格區段落。"""
    block: object
    is_heading: bool
    is_footer: bool


@dataclass
class TableModel:
    region: TableRegion
    cell_blocks: list[list[list]] = field(default_factory=list)
    # cell_lines[r][c] = list[PDFLine]
    cell_lines: list[list[list]] = field(default_factory=list)
    # cell_fill[r][c] = '#RRGGBB' 或 '' — 從 PDF rect fill drawings 推
    cell_fill: list[list[str]] = field(default_factory=list)


@dataclass
class PageModel:
    page_num: int
    width: float
    height: float
    margin_left: float = 0.0
    margin_top: float = 0.0
    margin_right: float = 0.0
    margin_bottom: float = 0.0
    tables: list[TableModel] = field(default_factory=list)
    free_blocks: list[FreeBlock] = field(default_factory=list)
    images: list = field(default_factory=list)
    # banner_rects: [(x0, y0, x1, y1, fill_hex)] — 非 table 內、頁寬 > 60% 的大色塊
    banner_rects: list = field(default_factory=list)
    # free_drawing_clusters: [(x0, y0, x1, y1)] — 不在 table 內、不在 outer
    # container 內的 free drawings（箭頭、dimension 線、vector 圖形）依鄰近度
    # 聚簇後的 bbox。給 odt_builder 用 PyMuPDF clip 區域 raster 成 page-anchor
    # PNG。對「真的沒辦法處理的」vector 用 raster fallback。
    free_drawing_clusters: list = field(default_factory=list)
    # v1.9.88：原 PDF 全部 drawings（給 odt_builder 還原填寫底線 — 年__月__日
    # 等 in-cell 短橫線）。直接帶 PDFPage.drawings reference。
    raw_drawings: list = field(default_factory=list)


@dataclass
class DocumentModel:
    pages: list[PageModel] = field(default_factory=list)
    body_font_size: float = 0.0
    body_font_name: str = ""
    language: str = "unknown"


def _line_y_top(line) -> float:
    return float(line.bbox[1])


def _line_y_center(line) -> float:
    return float((line.bbox[1] + line.bbox[3]) / 2.0)


def _line_in_table_pos(line_bbox: tuple, table: TableRegion,
                        margin: float = 1.0):
    return block_in_table(line_bbox, table, margin=margin)


def _wrap_line_to_block(lines: list, page_num: int) -> PDFBlock:
    """把多個 PDFLine 包成 dummy PDFBlock。"""
    if not lines:
        return None
    x0 = min(ln.bbox[0] for ln in lines)
    y0 = min(ln.bbox[1] for ln in lines)
    x1 = max(ln.bbox[2] for ln in lines)
    y1 = max(ln.bbox[3] for ln in lines)
    text = "\n".join(ln.text for ln in lines if ln.text)
    # dominant font / size from line 1 (簡化)
    df = lines[0].dominant_font
    ds = lines[0].dominant_size
    return PDFBlock(lines=lines, bbox=(x0, y0, x1, y1), text=text,
                    block_type="text", page_num=page_num,
                    dominant_font=df, dominant_size=ds)


def _group_lines_to_blocks(lines: list, page_num: int) -> list:
    """把已排序 (by y_top) 的 lines 依「Y 距 > size × ratio」拆群成 blocks。

    額外 split 條件（v1.8.85）：
    - 用 min(prev, cur) size 算 threshold，避免大字標題之後跟小字內文被合段（<樣本 A>
      「<報價單號>」size 26 + 公司資訊 size 12 案例）
    - X 中心點跨頁面寬度 30% 以上（左 vs 右）強制 split：「<報價單號>」X=417 靠右、
      「<公司名稱>」X=26 靠左 — 邏輯不同段落
    """
    if not lines:
        return []
    groups: list[list] = [[lines[0]]]
    for prev, cur in zip(lines, lines[1:]):
        gap = float(cur.bbox[1]) - float(prev.bbox[3])
        size = min(prev.dominant_size or 12.0, cur.dominant_size or 12.0)
        # X 中心跳變判斷
        prev_cx = (prev.bbox[0] + prev.bbox[2]) / 2.0
        cur_cx = (cur.bbox[0] + cur.bbox[2]) / 2.0
        x_jump = abs(cur_cx - prev_cx)
        if gap > size * LINE_GROUP_Y_GAP_RATIO or x_jump > 150:
            groups.append([cur])
        else:
            groups[-1].append(cur)
    return [_wrap_line_to_block(g, page_num) for g in groups if g]


def _classify_block(block, body_font_size: float, page_height: float) -> tuple[bool, bool]:
    is_heading = False
    is_footer = False
    if body_font_size > 0 and block.dominant_size > 0:
        if block.dominant_size >= body_font_size * HEADING_FONT_RATIO:
            is_heading = True
    if page_height > 0 and float(block.bbox[1]) > page_height * FOOTER_Y_RATIO:
        is_footer = True
    return is_heading, is_footer


def _fix_line_bbox_from_normal_chars(lines: list) -> list:
    """修正 PyMuPDF 抽出 line.bbox 被 outlier chars (e.g. trailing space height=119pt
    模擬 underline placeholder) 拉到不合理範圍的 bug。

    通用規則：以 line 內 non-whitespace char 的 height median 為基準，把
    height > median × 3 的 char 視為 outlier 從 bbox 計算中剔除（保留在 chars 內
    但不影響 bbox / 後續 row 分配）。

    <表單樣本> (案例)：「公司（申請人）名稱」line 11 chars，前 9 個 h=12，後 2 個 trailing
    space h=119 → line.bbox.y1 從應該的 98 變成 215（跨整個 banner 區），導致 row
    分派錯誤。修後 bbox 回到正常 12pt 字高。
    """
    out: list = []
    for ln in lines:
        chars = ln.chars or []
        if not chars:
            out.append(ln)
            continue
        non_ws_heights = [ch.height for ch in chars
                           if ch.char and not ch.char.isspace() and ch.height > 0]
        if not non_ws_heights:
            out.append(ln)
            continue
        non_ws_heights.sort()
        median_h = non_ws_heights[len(non_ws_heights) // 2]
        if median_h <= 0:
            out.append(ln)
            continue
        # 過濾 outlier chars
        normal_chars = [ch for ch in chars
                         if ch.height <= median_h * 3.0]
        if not normal_chars:
            out.append(ln)
            continue
        # 用 normal chars 重算 bbox
        nx0 = min(ch.x for ch in normal_chars)
        nx1 = max(ch.x + ch.width for ch in normal_chars)
        ny0 = min(ch.y for ch in normal_chars)
        ny1 = max(ch.y + ch.height for ch in normal_chars)
        new_bbox = (nx0, ny0, nx1, ny1)
        # 若 bbox 顯著變小（Y 範圍縮 > 20%）→ 替換 line（chars 也用 normal_chars
        # 取代，否則後續 merge 用全 chars 重算 bbox 會抵消這個 fix）
        old_h = ln.bbox[3] - ln.bbox[1]
        new_h = ny1 - ny0
        if old_h > 0 and new_h < old_h * 0.8:
            from ...pdf_truth.models import PDFLine as _PL
            out.append(_PL(
                chars=normal_chars,
                bbox=new_bbox,
                text=ln.text,
                dominant_font=ln.dominant_font,
                dominant_size=ln.dominant_size,
            ))
        else:
            out.append(ln)
    return out


def _dedup_overlapping_lines(lines: list) -> list:
    """去重「同位置同文字」的 line — PDF 描邊渲染 (stroke + fill) 會抽出 2-3 次
    重複 line（PyMuPDF 把每層各算一次）。通用規則：bbox 中心點與文字 normalized
    後相同的後續 line 視為重複。"""
    import re
    seen: set = set()
    out: list = []
    for ln in lines:
        if not ln.text:
            continue
        cx = round((ln.bbox[0] + ln.bbox[2]) / 2.0, 1)
        cy = round((ln.bbox[1] + ln.bbox[3]) / 2.0, 1)
        norm_txt = re.sub(r"\s+", "", ln.text)
        key = (cx, cy, norm_txt)
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out


def _merge_same_y_overlapping_lines(lines: list, y_tol: float = 2.0,
                                      max_x_gap: float = -3.0) -> list:
    """合併同 Y 範圍內 X 重疊 / 部分重疊的 lines (通用規則)。

    PDF 內標題類文字常用「多次重疊渲染」做加粗 + underline 效果，PyMuPDF dict
    把同一行抽成多條 lines（partial substring + X 部分重疊）。例：
        - "<縣市>"                  (X 61-109)
        - "<縣市> 鄉(鎮、市) <表單標題>"  (X 61-469)
        - "公所懸掛...<表單樣本>"        (X 277-469) ← 完全被前一條 contain
        - "公所懸掛...<表單樣本>（範例"   (X 277-517) ← 部分超出
        - "範例)"                  (X 485-537) ← 末端超出

    合一行的文字「<縣市> 鄉(鎮、市) <表單標題>（範例)」(X 61-537)。

    策略：按 Y center 分群（差 ≤ y_tol），每群按 X 起點排序，從左到右掃，
    用 PDFChar.x 判斷哪些 char 在「累積已涵蓋區」內。已涵蓋 → skip；新區 → append。

    後續 _line_in_table_pos 用合一後 line 的 bbox，所有 table 分派邏輯不變。
    """
    if not lines:
        return lines

    # 按 Y center 分群
    sorted_lines = sorted(lines, key=lambda L: (L.bbox[1] + L.bbox[3]) / 2.0)
    groups: list[list] = []
    for ln in sorted_lines:
        if not groups:
            groups.append([ln])
            continue
        last_grp = groups[-1]
        last_cy = sum((g.bbox[1] + g.bbox[3]) / 2.0 for g in last_grp) / len(last_grp)
        cy = (ln.bbox[1] + ln.bbox[3]) / 2.0
        if abs(cy - last_cy) <= y_tol:
            last_grp.append(ln)
        else:
            groups.append([ln])

    def _make_merged(chars_list):
        x0 = min(ch.x for ch in chars_list)
        x1 = max(ch.x + ch.width for ch in chars_list)
        y0 = min(ch.y for ch in chars_list)
        y1 = max(ch.y + ch.height for ch in chars_list)
        text = "".join(ch.char for ch in chars_list)
        # v1.9.42：char.char 是 raw PUA codepoint，需 replace 成標準 Unicode
        # 否則合併後 line.text 還是 （402386974 「機密等級」row case）
        from ...pdf_truth._pua_map import replace_pua_chars
        text = replace_pua_chars(text)
        return PDFLine(
            chars=chars_list,
            bbox=(x0, y0, x1, y1), text=text,
            dominant_font=grp[0].dominant_font,
            dominant_size=grp[0].dominant_size,
        )

    out: list = []
    for grp in groups:
        if len(grp) == 1:
            out.append(grp[0])
            continue
        # 若 group 內任何 line 沒 chars 資料（例：unit test fixture / chars 抽取失敗）
        # → 退回保留原 lines（不做 char-level merge）
        if any(not (ln.chars or []) for ln in grp):
            out.extend(grp)
            continue
        # 按 X 起點排序，逐 line 累積；當下一 line.x0 與當前 acc_x1 距離 > max_x_gap
        # → 不同 cell / 不同段，flush 當前 + 開新 segment（不誤合）
        grp.sort(key=lambda L: L.bbox[0])
        acc_chars: list = []
        acc_x1 = -1e9
        for ln in grp:
            line_chars = ln.chars or []
            non_ws = [ch for ch in line_chars if ch.char and not ch.char.isspace()]
            if not non_ws:
                continue
            ln_start = min(ch.x for ch in non_ws)
            # gap > max_x_gap 且不重疊 → flush 前面，開新 segment
            if acc_chars and ln_start > acc_x1 + max_x_gap:
                out.append(_make_merged(acc_chars))
                acc_chars = []
                acc_x1 = -1e9
            for ch in line_chars:
                if ch.x + ch.width * 0.5 > acc_x1:
                    acc_chars.append(ch)
            if acc_chars:
                last_ch = acc_chars[-1]
                acc_x1 = max(acc_x1, last_ch.x + last_ch.width)
        if acc_chars:
            out.append(_make_merged(acc_chars))
    return out


def build_document_model(pdf_truth) -> DocumentModel:
    doc_model = DocumentModel(
        body_font_size=pdf_truth.body_font_size,
        body_font_name=pdf_truth.body_font_name,
        language=pdf_truth.language_guess,
    )
    all_tables = detect_tables(pdf_truth)

    for pg in pdf_truth.pages:
        pm = PageModel(page_num=pg.page_num, width=pg.width, height=pg.height,
                       margin_left=pg.margin_left, margin_top=pg.margin_top,
                       margin_right=pg.margin_right, margin_bottom=pg.margin_bottom,
                       raw_drawings=list(pg.drawings or []))
        page_tables = [t for t in all_tables if t.page_num == pg.page_num]
        # 收集本頁 fill rect drawings（給 cell shading 用）
        fill_rects = []
        for d in pg.drawings:
            if d.type == "rect" and d.fill_color:
                fill_rects.append(d)
        for tr in page_tables:
            n_rows = max(0, len(tr.row_ys) - 1)
            n_cols = max(0, len(tr.col_xs) - 1)
            cell_lines: list = [[[] for _ in range(n_cols)] for _ in range(n_rows)]
            cell_fill: list = [["" for _ in range(n_cols)] for _ in range(n_rows)]
            # 對每 cell bbox 找有沒有 fill rect 涵蓋它（中心點在 fill rect 內）
            for r in range(n_rows):
                for c in range(n_cols):
                    cx = (tr.col_xs[c] + tr.col_xs[c + 1]) / 2.0
                    cy = (tr.row_ys[r] + tr.row_ys[r + 1]) / 2.0
                    for fr in fill_rects:
                        fx0, fy0, fx1, fy1 = fr.bbox
                        if fx0 <= cx <= fx1 and fy0 <= cy <= fy1:
                            cell_fill[r][c] = fr.fill_color
                            break
            pm.tables.append(TableModel(region=tr, cell_blocks=[],
                                          cell_lines=cell_lines,
                                          cell_fill=cell_fill))

        # banner 偵測：非 table 內 + 頁寬 ≥ 60% + 高 ≥ 15pt 的 fill rect
        page_w = float(pg.width or 0)
        page_h = float(pg.height or 0)
        for fr in fill_rects:
            fx0, fy0, fx1, fy1 = fr.bbox
            fw = fx1 - fx0
            fh = fy1 - fy0
            # 條件 1：直接全寬 banner（page width 60%+ 與高 ≥ 15pt）
            # 條件 2：小 polygon 但 path_points 不為空（curve 切角等 sub-polygon）且
            #         位於 page 上方 25%（fix v85 bug: 'page_h' in dir() 永遠 False）
            # 條件 3（v1.8.89）：印章 / seal 風格 polygon — 多邊（≥ 12 邊）+ 中等大小
            #         (≥ 50pt 且 ≤ 250pt 寬高 ≤ 50%) 視為裝飾性 shape 渲 PNG
            is_big_banner = (page_w > 0 and fw >= page_w * 0.6 and fh >= 15)
            has_path = bool(getattr(fr, "path_points", None))
            path_pt_n = len(getattr(fr, "path_points", None) or [])
            is_sub_polygon = (has_path and page_h > 0 and fy1 < page_h * 0.25)
            is_seal_polygon = (
                has_path and path_pt_n >= 12
                and 30 <= fw <= page_w * 0.5
                and 30 <= fh <= page_h * 0.5
            )
            # v1.9.2：thin horizontal rule (頁分隔線) — 0.5 ≤ fh ≤ 3, width ≥ 60% page
            is_thin_rule = (page_w > 0 and fw >= page_w * 0.6
                              and 0.3 <= fh <= 3.0)
            if (not is_big_banner and not is_sub_polygon
                    and not is_seal_polygon and not is_thin_rule):
                continue
            # 看是否落在任一 real table region 內（中心點）
            fcx = (fx0 + fx1) / 2.0
            fcy = (fy0 + fy1) / 2.0
            in_table = False
            for tr in page_tables:
                tx0, ty0, tx1, ty1 = tr.bbox
                if tx0 <= fcx <= tx1 and ty0 <= fcy <= ty1:
                    in_table = True
                    break
            if not in_table:
                # 帶 path_points 給 docx_builder 用 PIL 渲真實 polygon 形狀
                pm.banner_rects.append((
                    fx0, fy0, fx1, fy1, fr.fill_color,
                    list(getattr(fr, "path_points", None) or []),
                ))

        # 先收集本頁所有 line，做「描邊渲染重複」去重
        page_lines: list = []
        for blk in pg.blocks:
            if blk.block_type != "text":
                continue
            for ln in blk.lines:
                if (ln.text or "").strip():
                    page_lines.append(ln)
        # 先修 line bbox（PyMuPDF outlier char height bug） — 避免 row 分配錯位
        page_lines = _fix_line_bbox_from_normal_chars(page_lines)
        page_lines = _dedup_overlapping_lines(page_lines)
        # 合併同 Y range overlap lines（標題類用多次重疊渲染做加粗的常見 case）
        page_lines = _merge_same_y_overlapping_lines(page_lines)

        # **先 detect hMerge**（用 page_lines）— line assign 才能依正確 hMerge
        # state 處理。vMerge **不在 line assign 階段 redirect** — 因為 vMerge banner
        # 通常只佔 col 內一小段 X 範圍（例：「申請人基本資料」垂直 banner X 62-75
        # 在 col 0 X 54-197 內），同 col 其他 row 的水平 label 不該被 redirect。
        # vMerge 只在 docx_builder 階段用來合儲存格邊框 (gridSpan)，不影響內容歸屬。
        from .table_detector import detect_hmerge_from_lines, detect_vmerge_from_lines
        for tm in pm.tables:
            tm.region.hmerge = detect_hmerge_from_lines(tm.region, page_lines)
            tm.region.vmerge = detect_vmerge_from_lines(tm.region, page_lines)

        # 走 line-level 分配（real table 優先）
        # vMerge 重導向：若 cell (r,c) 是 "continue"，line 應導到上方 "restart" row 的 cell
        free_lines: list = []
        for ln in page_lines:
            assigned = False
            for tm in pm.tables:
                pos = _line_in_table_pos(ln.bbox, tm.region)
                if pos is not None:
                    r, c = pos
                    if 0 <= r < len(tm.cell_lines) and 0 <= c < len(tm.cell_lines[r]):
                        # vMerge **不**做 line redirect — line 留在原 row 寫入
                        # （若 col 有 vMerge banner，docx_builder 在 first row 設
                        # gridSpan，但內容仍依 PDF Y 原始位置分配，避免「col 內所有
                        # row 內容被 vMerge 全部吸進 first row」的副作用）
                        # 例外（v1.8.89 表單 vmerge fix）：「垂直文字 line」(h > w × 3)
                        # 落在 vmerge="continue" cell 時必須 redirect 到「restart」
                        # row；否則佔多 row 的 vertical banner label 會被
                        # _emit_table 當 CoveredTableCell 略過 → 完全消失。
                        # hMerge: 找該 row 內最近左側的「gridSpan > 0」col
                        hmerge = getattr(tm.region, "hmerge", None) or []
                        if hmerge and r < len(hmerge) and c < len(hmerge[r]):
                            if hmerge[r][c] == 0:
                                cc = c - 1
                                while cc >= 0 and hmerge[r][cc] == 0:
                                    cc -= 1
                                if cc >= 0:
                                    c = cc
                        # vMerge: 垂直 line 走 restart redirect
                        lx0, ly0, lx1, ly1 = ln.bbox
                        ln_w = lx1 - lx0
                        ln_h = ly1 - ly0
                        is_vertical_line = ln_w > 0 and ln_h > ln_w * 3
                        if is_vertical_line:
                            vmerge = getattr(tm.region, "vmerge", None) or []
                            if vmerge and r < len(vmerge) and c < len(vmerge[r]):
                                if vmerge[r][c] == "continue":
                                    rr = r - 1
                                    while rr >= 0 and (
                                        vmerge[rr][c] if rr < len(vmerge)
                                        and c < len(vmerge[rr]) else ""
                                    ) == "continue":
                                        rr -= 1
                                    if rr >= 0 and (
                                        vmerge[rr][c] if rr < len(vmerge)
                                        and c < len(vmerge[rr]) else ""
                                    ) == "restart":
                                        r = rr
                        tm.cell_lines[r][c].append(ln)
                        assigned = True
                        break
            if not assigned:
                free_lines.append(ln)

        # 健全性過濾：1-row × N-col table 若 ≥ 1 個 col 完全沒 line → 視為「裝飾
        # 性 banner」（PDF 上的色塊 / 標題帶 — drawings 形成 grid 但 col 1 是純色塊）
        # → 把該 table 內全部 lines 倒回 free_lines，移除該 table 物件
        valid_tables = []
        for tm in pm.tables:
            n_rows = len(tm.cell_lines)
            n_cols = len(tm.cell_lines[0]) if n_rows > 0 else 0
            is_banner = False
            if n_rows == 1 and n_cols >= 1:
                empty_cols = sum(1 for c in range(n_cols)
                                  if not tm.cell_lines[0][c])
                if empty_cols >= 1:
                    is_banner = True
            if is_banner:
                # 把 cell 內 lines 倒回 free
                for r in range(n_rows):
                    for c in range(n_cols):
                        free_lines.extend(tm.cell_lines[r][c])
            else:
                valid_tables.append(tm)
        pm.tables = valid_tables

        # 同 cell 內 lines 依 Y 序
        for tm in pm.tables:
            for r in range(len(tm.cell_lines)):
                tm.cell_lines[r] = [sorted(cell, key=_line_y_top)
                                     for cell in tm.cell_lines[r]]
            # 為相容 docx_builder，把 cell_lines 包成 cell_blocks
            tm.cell_blocks = []
            for r in range(len(tm.cell_lines)):
                row_blocks = []
                for c in range(len(tm.cell_lines[r])):
                    cell_lns = tm.cell_lines[r][c]
                    if cell_lns:
                        # 每 cell 內可能多 line，全包成單一 block（cell 內顯示為多段）
                        row_blocks.append([_wrap_line_to_block(cell_lns, pg.page_num)])
                    else:
                        row_blocks.append([])
                tm.cell_blocks.append(row_blocks)

        # free lines → 先偵測 virtual tables（無框線多欄 row），剩下的成 free blocks
        free_lines.sort(key=_line_y_top)
        virtual_regions, remaining_free = detect_virtual_tables(free_lines, pg.page_num)
        for vreg in virtual_regions:
            # v1.9.28：virtual table 也算 cell_fill — 報價單 data row / 未連稅 /
            # 總計 等 cell shading 用 fill rect 表達；line-based 表偵測完整時
            # paragraph_grouper line 335 已算過，但 virtual table 之前漏算
            n_rows_v = max(0, len(vreg.row_ys) - 1)
            n_cols_v = max(0, len(vreg.col_xs) - 1)
            cell_fill_v: list = [["" for _ in range(n_cols_v)] for _ in range(n_rows_v)]
            for rr in range(n_rows_v):
                for cc in range(n_cols_v):
                    cx = (vreg.col_xs[cc] + vreg.col_xs[cc + 1]) / 2.0
                    cy = (vreg.row_ys[rr] + vreg.row_ys[rr + 1]) / 2.0
                    for fr in fill_rects:
                        fx0, fy0, fx1, fy1 = fr.bbox
                        if fx0 <= cx <= fx1 and fy0 <= cy <= fy1:
                            cell_fill_v[rr][cc] = fr.fill_color
                            break
            tm = TableModel(region=vreg, cell_blocks=[],
                              cell_lines=vreg.cell_lines,
                              cell_fill=cell_fill_v)
            # 為 docx_builder 相容，把 cell_lines 包成 cell_blocks
            for r in range(len(vreg.cell_lines)):
                row_blocks = []
                for c in range(len(vreg.cell_lines[r])):
                    cell_lns = vreg.cell_lines[r][c]
                    if cell_lns:
                        row_blocks.append([_wrap_line_to_block(cell_lns, pg.page_num)])
                    else:
                        row_blocks.append([])
                tm.cell_blocks.append(row_blocks)
            pm.tables.append(tm)

        free_blocks_grouped = _group_lines_to_blocks(remaining_free, pg.page_num)
        # v1.9.43：virtual left column 擴展 — 偵測 「table 左側有 free_blocks
        # 與 table 的 row 對齊」case（上稿申請表：申請單位 / 申請人 /
        # 需求說明 / 上稿日期 / 承辦人 等 label 是 free_block 位於 table 左邊）
        # → 把這些 label 併進對應 table 作為虛擬左欄
        consumed_blocks = _attach_virtual_left_column(
            free_blocks_grouped, pm.tables)
        for blk in free_blocks_grouped:
            if blk is None or id(blk) in consumed_blocks:
                continue
            is_h, is_f = _classify_block(blk, pdf_truth.body_font_size, pg.height)
            pm.free_blocks.append(FreeBlock(block=blk, is_heading=is_h, is_footer=is_f))

        pm.images = list(pg.images or [])
        pm.free_drawing_clusters = _compute_free_drawing_clusters(
            pg, pm, page_tables)
        doc_model.pages.append(pm)

    return doc_model


def _attach_virtual_left_column(free_blocks: list, tables: list) -> set:
    """偵測 table 左側形成「虛擬左欄」的 free_blocks，併入 table。

    某些 PDF 表單只有右側欄位有實際 v-line，左側 label 是純文字 free_block
    位於 table.bbox.x0 左邊（上稿申請表 case）。本 fn 把這些 label
    當虛擬左欄附加進 table（不改 col_xs，只把 cell_blocks 內 row r 的 col 0
    內容用 label 取代並擴張 row_h），並回傳已被吃進去的 free_block id 集合。

    通用判定：
    - free_block.bbox.x1 < table.bbox.x0 + 5pt（在 table 左外側）
    - free_block.bbox 中心 Y 落在 table 的某 row 範圍內
    - 該 row 的 col 0 cell 原本是空（沒衝突）
    - 至少 2 個 free_block 對齊 → 才認為是「左欄」（避免單一字落入）
    """
    consumed: set = set()
    if not free_blocks or not tables:
        return consumed
    for tm in tables:
        region = tm.region
        if not region.row_ys or len(region.row_ys) < 2:
            continue
        tx0 = region.bbox[0]
        ty0 = region.bbox[1]
        ty1 = region.bbox[3]
        # 收集左側可能候選
        candidates: list[tuple[int, object]] = []  # (row_idx, block)
        for blk in free_blocks:
            if blk is None:
                continue
            bx0, by0, bx1, by1 = blk.bbox
            # 在 table 左側（x1 < tx0 + 5）且 Y 落在 table 範圍
            if bx1 >= tx0 + 5 or bx1 < tx0 - 200:
                continue
            bcy = (by0 + by1) / 2.0
            if bcy < ty0 - 2 or bcy > ty1 + 2:
                continue
            # 找對應 row
            row_idx = None
            for r in range(len(region.row_ys) - 1):
                if region.row_ys[r] - 2 <= bcy <= region.row_ys[r + 1] + 2:
                    row_idx = r
                    break
            if row_idx is None:
                continue
            # row col 0 原本空才能塞
            try:
                if tm.cell_blocks[row_idx][0]:
                    continue
            except Exception:
                continue
            candidates.append((row_idx, blk))
        # 至少 2 個對齊才認證為左欄（單個可能誤判）
        if len(candidates) < 2:
            continue
        # 計算這些 label 共同 X 範圍
        min_x = min(blk.bbox[0] for _, blk in candidates)
        # 把 col_xs 左邊延伸到 min_x（保留 col 0 內容，新欄變 col 0，原 col 0 → col 1）
        # 為了不打亂 detect_hmerge/vmerge（已算過），改成簡單做法：直接把
        # label 內容塞進原 cell_blocks[row][0]，並把 region.bbox / col_xs 往左展。
        new_left = max(0.0, min_x - 2.0)
        if new_left >= region.col_xs[0] - 1:
            continue  # 沒擴展空間
        # prepend 新 col：col_xs 多一個 new_left，原 col_xs 不動
        region.col_xs = [new_left] + list(region.col_xs)
        region.bbox = (new_left, region.bbox[1], region.bbox[2], region.bbox[3])
        n_rows = len(tm.cell_blocks)
        # 每 row prepend 空 col 0
        for r in range(n_rows):
            tm.cell_blocks[r] = [[] ] + list(tm.cell_blocks[r])
        # hmerge / vmerge prepend 1 col (全部 1)
        if hasattr(region, 'hmerge') and region.hmerge:
            for r in range(len(region.hmerge)):
                region.hmerge[r] = [1] + list(region.hmerge[r])
        if hasattr(region, 'vmerge') and region.vmerge:
            for r in range(len(region.vmerge)):
                region.vmerge[r] = [""] + list(region.vmerge[r])
        # 賽 cell_lines 同步 prepend
        if hasattr(region, 'cell_lines') and region.cell_lines:
            for r in range(len(region.cell_lines)):
                region.cell_lines[r] = [[]] + list(region.cell_lines[r])
        # cell_fill prepend
        if tm.cell_fill:
            for r in range(len(tm.cell_fill)):
                tm.cell_fill[r] = [""] + list(tm.cell_fill[r])
        # 將候選 block 塞進 col 0
        for row_idx, blk in candidates:
            if row_idx < n_rows:
                tm.cell_blocks[row_idx][0] = [blk]
                consumed.add(id(blk))
    return consumed


def _compute_free_drawing_clusters(pg, pm, page_tables) -> list:
    """偵測本頁「不在 table / outer-container / cell-fill 範圍」的 free drawings
    並依鄰近度聚簇。

    PDF / ODT 原理：
    - PDF 把 vector 圖（箭頭、dimension line、自由形狀）存成 drawings 路徑
    - 我們的表格 / 文字 emitter 不會還原這些 vector → 視覺缺失
    - 解：對「不在已處理範圍內」的 drawings 找 bbox cluster，給 odt_builder
      用 PyMuPDF clip raster 成 page-anchor PNG（保 visual，犧牲可編輯性）
    - 「真的沒辦法處理的 再用文字方塊」(user) — raster 就是 fallback

    回 [(x0, y0, x1, y1)] cluster bbox 列表。
    """
    if not pg.drawings:
        return []
    # 收集所有 line / curve / 顯著 path drawings — 排除 fill rect 與表格線
    cand: list = []
    for d in pg.drawings:
        if d.type == "rect" and d.fill_color:
            continue  # cell shading 已被表格處理
        if d.type == "rect" and not d.stroke_color:
            continue  # 無 stroke 也無 fill 的空 rect
        # 過濾被 table 邊框消耗的 drawing
        # v1.9.30：tolerance 從 2pt 放寬到 10pt — 因為 table 的 OUTER frame
        # 線段是在 table bbox 邊緣（甚至略外），原本 2pt 容差會把外框線歸成
        # free drawing → 整頁邊緣產生 page-anchor frame 把 flow 推到下一頁
        consumed = False
        for tm in page_tables:
            tr = tm
            bx0, by0, bx1, by1 = tr.bbox
            dx0, dy0, dx1, dy1 = d.bbox
            # drawing bbox 完全在 table bbox 內視為被消耗（容差 10pt）
            if (dx0 >= bx0 - 10 and dx1 <= bx1 + 10
                    and dy0 >= by0 - 10 and dy1 <= by1 + 10):
                consumed = True
                break
        if consumed:
            continue
        # 過濾極小 drawing（< 2pt 寬高）
        dx0, dy0, dx1, dy1 = d.bbox
        if (dx1 - dx0) < 2 and (dy1 - dy0) < 2:
            continue
        cand.append(tuple(d.bbox))

    if not cand:
        return []

    # 簡單鄰近度聚簇：bbox 中心距離 < cluster_gap 視為同簇
    cluster_gap = 30.0  # pt — 30pt 內視為同一個視覺群
    clusters: list[list[tuple]] = []
    for bb in cand:
        cx = (bb[0] + bb[2]) / 2.0
        cy = (bb[1] + bb[3]) / 2.0
        merged_to = -1
        for ci, cl in enumerate(clusters):
            for cbb in cl:
                ccx = (cbb[0] + cbb[2]) / 2.0
                ccy = (cbb[1] + cbb[3]) / 2.0
                if abs(cx - ccx) < cluster_gap and abs(cy - ccy) < cluster_gap:
                    merged_to = ci
                    break
            if merged_to >= 0:
                break
        if merged_to >= 0:
            clusters[merged_to].append(bb)
        else:
            clusters.append([bb])

    # 每簇取 union bbox + 適當 padding
    raw: list = []
    pad = 4.0
    for cl in clusters:
        x0 = min(bb[0] for bb in cl) - pad
        y0 = min(bb[1] for bb in cl) - pad
        x1 = max(bb[2] for bb in cl) + pad
        y1 = max(bb[3] for bb in cl) + pad
        # 過小簇跳過（< 10pt² 視為裝飾雜訊）
        if (x1 - x0) < 10 and (y1 - y0) < 10:
            continue
        # 過大簇跳過（> 70% page area 視為非 free drawings，可能整頁裝飾）
        if (x1 - x0) * (y1 - y0) > pg.width * pg.height * 0.7:
            continue
        # v1.9.30：「page 邊框」型 cluster（一邊 < 15pt + 另一邊 > 40% page dim）
        # 視為頁邊裝飾線跳過 — 否則它變成 page-anchor frame 把 flow 推到下一頁
        cw = x1 - x0; ch = y1 - y0
        if (cw < 15 and ch > pg.height * 0.4) or (ch < 15 and cw > pg.width * 0.4):
            continue
        raw.append((x0, y0, x1, y1))

    # dedup：若 cluster A 完全在 cluster B 內 → 丟掉 A
    # （第一輪 center-distance 聚簇允許 bbox 大幅重疊；這步收尾）
    raw.sort(key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)
    keep: list = []
    for bb in raw:
        contained = False
        for kb in keep:
            if (bb[0] >= kb[0] - 1 and bb[1] >= kb[1] - 1
                    and bb[2] <= kb[2] + 1 and bb[3] <= kb[3] + 1):
                contained = True
                break
        if not contained:
            keep.append(bb)
    return keep

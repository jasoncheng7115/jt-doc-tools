"""從 PDFTruth.drawings 偵測表格 grid（B2）。

演算法：
1) 對單頁的 drawings 抽出**水平線段** (h_lines) 與**垂直線段** (v_lines)
   - line drawings：直接用 (x0,y0,x1,y1)
   - rect drawings：拆成 4 邊
2) 對 h_lines / v_lines 各自做 **Y/X coordinate clustering**（容忍 ± 2pt 視為同
   一條）— 得到 row_ys (水平線位置序) 與 col_xs (垂直線位置序)
3) 一個有效表格 region 需要：
   - row_ys ≥ 2（至少一行 — 上下界）
   - col_xs ≥ 2（至少一欄 — 左右界）
4) 同頁可能有**多個分開的 grid**（多張表）— 按 bbox 連通性切組

輸出 list[TableRegion]：
- page_num
- row_ys (sorted list of Y values)
- col_xs (sorted list of X values)
- cells: 2D list of (x0,y0,x1,y1) tuples (logical row × col grid)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


LINE_CLUSTER_TOLERANCE = 2.0   # 兩線 X/Y 差 ≤ 此 pt 視為同一條
MIN_LINE_LENGTH = 20.0         # 太短的「點」不算 line
MIN_TABLE_WIDTH = 60.0         # 表寬太小視為雜訊
MIN_TABLE_HEIGHT = 30.0


@dataclass
class TableRegion:
    page_num: int
    row_ys: list[float]              # 含上界與下界
    col_xs: list[float]              # 含左界與右界
    bbox: tuple[float, float, float, float]
    # cells[r][c] = (x0, y0, x1, y1)（row r 跟 col c 的單一格 bbox）
    cells: list[list[tuple[float, float, float, float]]] = field(default_factory=list)
    # 邊框 dominant color hex (從該 region 內 drawings 多數投票)；空表示用預設灰
    border_color_hex: str = ""
    # 邊框 dominant stroke width (pt)；0 表示用預設 0.5pt
    border_width_pt: float = 0.0
    # vMerge mapping[r][c] = "restart" / "continue" / "" (無 merge)
    vmerge: list[list[str]] = field(default_factory=list)
    # hMerge[r][c] = gridSpan int (cell 橫向跨幾欄；1 = 正常單欄)
    hmerge: list[list[int]] = field(default_factory=list)
    # v1.8.99：原 PDF 內 region 區的 h_lines / v_lines（每條 = (x0, y_mid, x1) /
    # (x_mid, y0, y1)）— 用來判定每個 cell 的個別 border 該不該畫
    raw_h_lines: list[tuple[float, float, float]] = field(default_factory=list)
    raw_v_lines: list[tuple[float, float, float]] = field(default_factory=list)


def _classify_lines(drawings) -> tuple[list, list]:
    """從 PDFTruth.drawings 抽水平與垂直線。
    回 (h_lines, v_lines)，每條 = (x0,y0,x1,y1)。"""
    h: list[tuple] = []
    v: list[tuple] = []
    for d in drawings or []:
        if d.type == "line":
            x0, y0, x1, y1 = d.bbox
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)
            if dy < 1.5 and dx >= MIN_LINE_LENGTH:
                h.append((min(x0, x1), (y0 + y1) / 2.0,
                          max(x0, x1), (y0 + y1) / 2.0))
            elif dx < 1.5 and dy >= MIN_LINE_LENGTH:
                v.append(((x0 + x1) / 2.0, min(y0, y1),
                          (x0 + x1) / 2.0, max(y0, y1)))
        elif d.type == "rect":
            x0, y0, x1, y1 = d.bbox
            w = abs(x1 - x0)
            ht = abs(y1 - y0)
            # **排除 fill rect**（banner / 色塊）— 它們是視覺背景不是表格邊框
            # v1.9.26：fill rect 分類：
            #   - 形狀像線（任一邊 < 2pt）→ 當邊框保留（PDF 常把 thin border 存
            #     成 thin fill rect，如報價單 0.7pt 高的灰色 h-border、申請表
            #     1.4pt 寬的黑色 v-border）
            #   - 否則大塊 fill rect → 視覺背景，跳過（不當邊框）
            if d.fill_color and (not d.stroke_color
                                   or float(d.stroke_width or 0) <= 0):
                # 「形狀像線」= 任一邊 < 2pt（典型 PDF stroke 寬度範圍）
                # v1.9.85：加高 aspect-ratio 判定 — 細長 fill rect（如 2.16pt 寬
                # × 194pt 高的外框邊線）ratio 90:1 明顯是線，不是背景塊。上稿申請表
                # 上稿申請單外框邊 w=2.16 剛好卡 2.0 閾值被誤刪 → 表格中段斷開
                # 切成 2 張 table。改用 ratio > 8 也視為線。
                short = max(0.1, min(w, ht))
                long = max(w, ht)
                is_line_shaped = (w < 2.0 or ht < 2.0
                                  or (long / short) > 8.0)
                if not is_line_shaped:
                    continue
            # 拆 4 邊
            if w >= MIN_LINE_LENGTH:
                h.append((x0, y0, x1, y0))   # top
                h.append((x0, y1, x1, y1))   # bottom
            if ht >= MIN_LINE_LENGTH:
                v.append((x0, y0, x0, y1))   # left
                v.append((x1, y0, x1, y1))   # right
    return h, v


def _cluster_coords(values: list[float], tol: float = LINE_CLUSTER_TOLERANCE) -> list[float]:
    """1D 群聚（容忍 tol）— 排序後合併相鄰差 ≤ tol 的座標為中位數。"""
    if not values:
        return []
    sv = sorted(values)
    out: list[float] = []
    cluster: list[float] = [sv[0]]
    for v in sv[1:]:
        if v - cluster[-1] <= tol:
            cluster.append(v)
        else:
            out.append(sum(cluster) / len(cluster))
            cluster = [v]
    out.append(sum(cluster) / len(cluster))
    return out


def _segments_overlap(a0: float, a1: float, b0: float, b1: float) -> bool:
    return not (a1 < b0 or a0 > b1)


def _split_lines_into_table_groups(h_lines: list, v_lines: list,
                                     y_gap_abs_threshold: float = 80.0,
                                     y_gap_rel_factor: float = 3.0) -> list[tuple]:
    """把 h_lines 按 Y 值大跳分組（同 group 視為同一張 table）— v_lines 跟著
    Y 範圍走（v_line 的 Y 區間 overlap 該 group Y 範圍才歸入）。

    分組條件（任一觸發）：
      - gap > y_gap_abs_threshold (絕對閾值 80 pt — 比典型行高 20-30 大很多)
      - gap > median_gap × y_gap_rel_factor (相對閾值 — 同表內 row 間距相近)

    回 list of (group_h_lines, group_v_lines)。
    """
    if not h_lines:
        return []
    sorted_h = sorted(h_lines, key=lambda L: (L[1] + L[3]) / 2.0)
    if len(sorted_h) < 2:
        groups_h = [[sorted_h[0]]] if sorted_h else []
    else:
        # 計算 median gap — **先用 cluster 去除同 row 重複線**，再算間距
        ys_clustered = _cluster_coords([(L[1] + L[3]) / 2.0 for L in sorted_h])
        cluster_gaps = [ys_clustered[i + 1] - ys_clustered[i]
                         for i in range(len(ys_clustered) - 1)]
        if cluster_gaps:
            sg = sorted(cluster_gaps)
            median_gap = sg[len(sg) // 2]
        else:
            median_gap = 0
        groups_h = [[sorted_h[0]]]
        for prev, cur in zip(sorted_h, sorted_h[1:]):
            gap = (cur[1] + cur[3]) / 2.0 - (prev[1] + prev[3]) / 2.0
            is_split = gap > y_gap_abs_threshold or (
                median_gap > 0 and gap > median_gap * y_gap_rel_factor and gap > 20.0
            )
            # v1.9.33：若 split 條件成立但「OUTER frame v-line」橫跨 gap
            # → 仍歸同一 group（供應商基本資料表 存摺影本/備註/請提供 區只
            # 有 outer-frame 的 v-line 連續，沒內部 h-line → 不應切表）
            # 限制：gap 必須 ≤ 250pt（大於則視為「不同 layout 區段」雖然外框
            # 包住但結構不同，如 申請表 上半表 + 下半廣告物 starbursts）
            if is_split and gap <= 250.0:
                prev_y = (prev[1] + prev[3]) / 2.0
                cur_y = (cur[1] + cur[3]) / 2.0
                # prev group 涵蓋 X 範圍
                prev_x_min = min(min(L[0], L[2]) for L in groups_h[-1])
                prev_x_max = max(max(L[0], L[2]) for L in groups_h[-1])
                bridged = False
                for vl in v_lines:
                    vx = (vl[0] + vl[2]) / 2.0
                    vy0, vy1 = min(vl[1], vl[3]), max(vl[1], vl[3])
                    # 必須是 outer-frame：v-line x 接近 prev group 邊緣
                    is_outer = abs(vx - prev_x_min) < 10 or abs(vx - prev_x_max) < 10
                    if is_outer and vy0 <= prev_y + 2 and vy1 >= cur_y - 2:
                        bridged = True
                        break
                if bridged:
                    is_split = False
            if is_split:
                groups_h.append([cur])
            else:
                groups_h[-1].append(cur)
    # 對每組 h 抓對應的 v 線（v_line Y 範圍與此 group 的 Y 範圍 overlap）
    out = []
    for gh in groups_h:
        y_min = min((L[1] + L[3]) / 2.0 for L in gh)
        y_max = max((L[1] + L[3]) / 2.0 for L in gh)
        gv = []
        for vl in v_lines:
            vy0, vy1 = min(vl[1], vl[3]), max(vl[1], vl[3])
            if not (vy1 < y_min - 2 or vy0 > y_max + 2):
                gv.append(vl)
        out.append((gh, gv))
    return out


def _merge_collinear_segments(lines: list, axis: str,
                                gap_tol: float = 3.0,
                                coord_tol: float = 1.0) -> list:
    """合併同 X (v) 或同 Y (h) 連續 / 接近的線段成單一邏輯線段。

    申請表類 PDF 把 grid 拆成 per-cell 短 thin rect → 各自 ht 才 21pt < filter
    門檻被丟掉。先合併共線段 → 從 21pt 段變成 380pt 邏輯線 → 通過後續過濾。

    axis = "v" (vertical, 主軸是 y) 或 "h" (horizontal, 主軸是 x)
    gap_tol = 段與段之間允許的 gap（容許 3pt 接縫）
    coord_tol = 主軸座標 cluster tolerance
    """
    if not lines:
        return lines
    if axis == "v":
        # line = (x, y0, x, y1)；按 x 分群，每群按 y 序合併
        by_coord: dict = {}
        for L in lines:
            x_key = round(L[0] / coord_tol) * coord_tol
            by_coord.setdefault(x_key, []).append(L)
        merged: list = []
        for x_key, segs in by_coord.items():
            segs.sort(key=lambda L: L[1])
            cur = list(segs[0])
            for s in segs[1:]:
                # 若 s 的 y0 ≤ cur y1 + gap_tol → 合併
                if s[1] <= cur[3] + gap_tol:
                    cur[3] = max(cur[3], s[3])
                else:
                    merged.append(tuple(cur))
                    cur = list(s)
            merged.append(tuple(cur))
        return merged
    elif axis == "h":
        # line = (x0, y, x1, y)
        by_coord = {}
        for L in lines:
            y_key = round(L[1] / coord_tol) * coord_tol
            by_coord.setdefault(y_key, []).append(L)
        merged = []
        for y_key, segs in by_coord.items():
            segs.sort(key=lambda L: L[0])
            cur = list(segs[0])
            for s in segs[1:]:
                if s[0] <= cur[2] + gap_tol:
                    cur[2] = max(cur[2], s[2])
                else:
                    merged.append(tuple(cur))
                    cur = list(s)
            merged.append(tuple(cur))
        return merged
    return lines


def _filter_separator_lines(h_lines: list, v_lines: list,
                              h_long_ratio: float = 0.20,
                              v_long_ratio: float = 0.15) -> tuple[list, list]:
    """過濾「短到不可能是表格 row/col separator」的線。

    通用規則 (first-principles)：
    - 真正的 row separator 線通常**橫跨表寬大部分** (≥ h_long_ratio × group_width)
    - 真正的 col separator 線通常**縱跨表高大部分** (≥ v_long_ratio × group_height)
    - 短於此的線通常是 form-field underline / ID 格 / 裝飾線，不應觸發 row/col

    h_long_ratio = 0.20 / v_long_ratio = 0.15：
    - row separator 通常跨表寬主體（容忍 sub-row 跨 ~20%）
    - col separator 可能只在 sub-region 出現，門檻放寬到 15%
    - 過濾「24pt 寬填空線」「14 格 ID 小 cell vertical strokes」等內部裝飾

    對「outer frame」(全寬最長線) 永遠保留 — 不論長度比例。
    """
    if not h_lines or not v_lines:
        return h_lines, v_lines
    # group bbox（先用全部線估）
    all_x0 = min(min(L[0], L[2]) for L in h_lines + v_lines)
    all_x1 = max(max(L[0], L[2]) for L in h_lines + v_lines)
    all_y0 = min(min(L[1], L[3]) for L in h_lines + v_lines)
    all_y1 = max(max(L[1], L[3]) for L in h_lines + v_lines)
    gw = all_x1 - all_x0
    gh = all_y1 - all_y0
    if gw <= 0 or gh <= 0:
        return h_lines, v_lines
    # v1.9.28：採「相對 OR 絕對門檻 取較小者」
    # - 相對：原 0.15-0.20 × group dim，大表能濾掉小裝飾
    # - 絕對：≥ 18pt（v）/ 25pt（h），確保「跨一個 row 的 inner divider」不會被
    #   超大表 group_height 推高的相對門檻砍掉（申請表 地址 row 鎮/鄉/路/段 等
    #   inner v-divider 40pt < 0.15×697=104pt 之前被砍）
    h_thresh = min(gw * h_long_ratio, 25.0)
    v_thresh = min(gh * v_long_ratio, 18.0)
    # 保留：outer frame (頂底 + 左右) + 長度 ≥ threshold
    h_keep = []
    for L in h_lines:
        length = abs(L[2] - L[0])
        if length >= h_thresh:
            h_keep.append(L)
    v_keep = []
    for L in v_lines:
        length = abs(L[3] - L[1])
        if length >= v_thresh:
            v_keep.append(L)
    # 若過濾後沒線（極端情況：無真表格），fallback 用原 lines
    if not h_keep or not v_keep:
        return h_lines, v_lines
    return h_keep, v_keep


def _build_region_from_lines(page_num: int, h_lines: list,
                              v_lines: list) -> TableRegion | None:
    if not h_lines or not v_lines:
        return None
    # v1.9.25：先做 collinear-merge，否則申請表 / 供應商基本資料表類型 PDF 把
    # 整條 row separator 拆成 per-cell 21pt 短 thin rect，個別小於 _filter
    # _separator_lines 門檻被丟掉 → col_xs / row_ys cluster 缺欄缺列。
    # merge 只串接同 x / 同 y 連續段，不會憑空產生 false line。
    h_lines_orig = h_lines
    v_lines_orig = v_lines
    h_lines_for_cluster = _merge_collinear_segments(h_lines, axis="h")
    v_lines_for_cluster = _merge_collinear_segments(v_lines, axis="v")
    h_lines, v_lines = _filter_separator_lines(
        h_lines_for_cluster, v_lines_for_cluster)
    h_y = [(L[1] + L[3]) / 2.0 for L in h_lines]
    v_x = [(L[0] + L[2]) / 2.0 for L in v_lines]
    # row Y cluster：tolerance 緊 (2pt)，因 row 之間真實間距 >> 容差
    row_ys = _cluster_coords(h_y, tol=LINE_CLUSTER_TOLERANCE)
    # col X cluster：tolerance 放寬到 6pt — 一般表格欄寬 ≥ 20pt，6pt 內的多條垂直線
    # 多是「同 col 邊界內的雙描邊 / 印刷錯位 / form-field underline 端點」
    col_xs = _cluster_coords(v_x, tol=6.0)
    # row_ys 後處理：gap < 10pt 視為「邊框雙線 / 印刷錯位 / 細裝飾線」，
    # 合進相鄰 row（最終 cluster 再做一輪，tol=10）— 避免 row over-segment。
    # 10pt 折衷值：保留 sub-row >= 12pt（checkbox 行 14pt OK）、合掉 8-9pt false separator
    if len(row_ys) > 2:
        row_ys = _cluster_coords(row_ys, tol=10.0)
    if len(row_ys) < 2 or len(col_xs) < 2:
        return None
    bbox = (min(col_xs), min(row_ys), max(col_xs), max(row_ys))
    if bbox[2] - bbox[0] < MIN_TABLE_WIDTH or bbox[3] - bbox[1] < MIN_TABLE_HEIGHT:
        return None
    cells: list[list[tuple]] = []
    for r in range(len(row_ys) - 1):
        row: list[tuple] = []
        for c in range(len(col_xs) - 1):
            row.append((col_xs[c], row_ys[r], col_xs[c + 1], row_ys[r + 1]))
        cells.append(row)
    # v1.9.24：raw_h_lines / raw_v_lines 給 odt_builder 用 — 用「合併共線
    # 短段」後再過濾，讓申請表類 grid（每 row 用 21pt thin rect 拆）的短 v-line
    # 通過 filter（合併後變 380pt 邏輯線）。col_xs / row_ys 已經算過不受影響。
    h_lines_merged = _merge_collinear_segments(h_lines_orig, axis="h")
    v_lines_merged = _merge_collinear_segments(v_lines_orig, axis="v")
    h_lines_merged, v_lines_merged = _filter_separator_lines(
        h_lines_merged, v_lines_merged)
    raw_h = [(min(L[0], L[2]), (L[1] + L[3]) / 2.0, max(L[0], L[2]))
              for L in h_lines_merged]
    raw_v = [((L[0] + L[2]) / 2.0, min(L[1], L[3]), max(L[1], L[3]))
              for L in v_lines_merged]
    return TableRegion(page_num=page_num, row_ys=row_ys, col_xs=col_xs,
                        bbox=bbox, cells=cells,
                        raw_h_lines=raw_h, raw_v_lines=raw_v)


def _dominant_stroke_color_width(drawings, bbox: tuple) -> tuple[str, float]:
    """從落在 bbox 範圍內的 drawings 取多數 stroke_color hex + 中位 stroke_width。
    回 (color_hex 或 '', width_pt 或 0)。"""
    from collections import Counter
    color_cnt: Counter = Counter()
    widths: list[float] = []
    x0, y0, x1, y1 = bbox
    for d in drawings or []:
        if d.type not in ("line", "rect"):
            continue
        dx0, dy0, dx1, dy1 = d.bbox
        if dx1 < x0 - 1 or dx0 > x1 + 1 or dy1 < y0 - 1 or dy0 > y1 + 1:
            continue
        # 過濾 fill rect（type='rect' 但 stroke_width=0 通常是色塊，不算邊框）
        sw = float(d.stroke_width or 0)
        if d.type == "rect" and sw < 0.01:
            continue
        c = (d.stroke_color or "").lstrip("#")
        if c:
            color_cnt[c.lower()] += 1
        if sw > 0:
            widths.append(sw)
    color_hex = ""
    if color_cnt:
        most = color_cnt.most_common(1)[0][0]
        if most not in ("000000", "000"):
            color_hex = most
    width_pt = 0.0
    if widths:
        sw_sorted = sorted(widths)
        width_pt = sw_sorted[len(sw_sorted) // 2]  # 中位
    return color_hex, width_pt


def _detect_vmerge(region: TableRegion, drawings: list) -> list[list[str]]:
    """偵測 cell 縱向合併 (vMerge)。
    對每個 row boundary Y + col X 範圍：若該位置**沒有水平線跨越** (大部分寬度)
    → 視為「row N 與 row N+1 的 cell 在此 col 縱向合併」。
    回 vmerge[r][c] = "restart" (最上面那格) / "continue" (被合進去) / "" (獨立)。

    用於 PDF 內常見「縱向 label cell (基本資料 / 聯絡資訊)」— PDF 上左欄縱跨多
    row 為單一 cell 內含縱排文字，但 table grid 內部仍有 row 分隔線（其他 col
    各 row 有 cell），只是該縱向 col 在 row 邊界沒線。
    """
    n_rows = max(0, len(region.row_ys) - 1)
    n_cols = max(0, len(region.col_xs) - 1)
    if n_rows < 2 or n_cols == 0:
        return [["" for _ in range(n_cols)] for _ in range(n_rows)]
    vmerge = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    # 先抽水平線 (Y 接近常數的 line drawings)
    h_lines = []
    for d in drawings or []:
        if d.type != "line":
            continue
        x0, y0, x1, y1 = d.bbox
        if abs(y1 - y0) > 1.5:
            continue
        h_lines.append((min(x0, x1), (y0 + y1) / 2.0, max(x0, x1)))
    # 安全上限：vMerge 縱跨不超過 rows 的 50%
    MAX_VMERGE_RUN = max(2, n_rows // 2)
    # 追蹤 restart 後已 continue 多少 row（key = (start_row, col)）
    run_len: dict = {}
    for r in range(n_rows - 1):
        boundary_y = region.row_ys[r + 1]
        for c in range(n_cols):
            col_x0 = region.col_xs[c]
            col_x1 = region.col_xs[c + 1]
            col_w = col_x1 - col_x0
            has_line = False
            for lx0, ly, lx1 in h_lines:
                if abs(ly - boundary_y) > 2.0:
                    continue
                overlap = min(lx1, col_x1) - max(lx0, col_x0)
                if overlap > col_w * 0.5:
                    has_line = True
                    break
            if not has_line:
                # 找此 col 內目前 restart 起點
                start_row = r
                while start_row > 0 and vmerge[start_row][c] == "continue":
                    start_row -= 1
                cur_run = run_len.get((start_row, c), 0)
                if cur_run >= MAX_VMERGE_RUN:
                    # 達上限 → 不再 continue，把此 boundary 視為「有線」
                    continue
                if vmerge[r][c] == "":
                    vmerge[r][c] = "restart"
                vmerge[r + 1][c] = "continue"
                run_len[(start_row, c)] = cur_run + 1
    return vmerge


def detect_vmerge_from_lines(region: TableRegion, lines: list) -> list[list[str]]:
    """從 PDFLine 真值偵測縱向合併 (vMerge)。
    對每條 line：
      - line bbox 中心 X → col
      - line bbox Y 範圍跨 row_ys[r0..r1] → vMerge restart 在 (r0,col)，r0+1..r1 設 continue
    回 vmerge[r][c] = "restart" / "continue" / "" (無 merge)。
    """
    n_rows = max(0, len(region.row_ys) - 1)
    n_cols = max(0, len(region.col_xs) - 1)
    vmerge = [["" for _ in range(n_cols)] for _ in range(n_rows)]
    if n_rows < 2 or n_cols == 0 or not lines:
        return vmerge
    for ln in lines:
        if not ln.text or not ln.text.strip():
            continue
        lx0, ly0, lx1, ly1 = ln.bbox
        line_w = lx1 - lx0
        line_h = ly1 - ly0
        # **vertical text gate**：只有「明顯垂直」的 line 才視為 vMerge banner cell
        # 候選（高度遠 > 寬度，例 vertical banner label）。
        # 否則水平 text line 即使因 char bbox 異常跨多 row，不該觸發整 col vMerge
        # (<表單樣本> form 案例：col 0 多 row 各自獨立 label，不應因任一 line bbox 異常
        # 而被誤判 vMerge 整 col)
        is_vertical = line_h > line_w * 1.5
        if not is_vertical:
            continue
        lcx = (lx0 + lx1) / 2.0
        # 找 col
        c = None
        for cc in range(n_cols):
            if region.col_xs[cc] - 1 <= lcx <= region.col_xs[cc + 1] + 1:
                c = cc
                break
        if c is None:
            continue
        # 找 line Y 起訖 row
        r0 = None
        r1 = None
        for rr in range(n_rows):
            if region.row_ys[rr] - 1 <= ly0 <= region.row_ys[rr + 1] + 1:
                r0 = rr
                break
        for rr in range(n_rows):
            if region.row_ys[rr] - 1 <= ly1 <= region.row_ys[rr + 1] + 1:
                r1 = rr
                break
        if r0 is None or r1 is None or r1 <= r0:
            continue
        # line 跨 r0 到 r1 row 在 col c → vMerge
        if vmerge[r0][c] == "":
            vmerge[r0][c] = "restart"
        for rr in range(r0 + 1, r1 + 1):
            vmerge[rr][c] = "continue"
    return vmerge


def detect_hmerge_from_lines(region: TableRegion, lines: list) -> list[list[int]]:
    """從 PDFTruth lines 真值偵測橫向合併。
    對每條 line：
      - line bbox 中心 Y → row
      - line bbox X 範圍橫跨 col_xs[c0..c1] → 該 line 屬於 col c0 但跨到 c1
      - 把 (row, c0..c1) cells 設為 gridSpan
    遇衝突（同 row 多個 lines 不同 span）取最大 span。
    回 hmerge[r][c] = gridSpan int（被合併的右側 col 設 0）。
    """
    n_rows = max(0, len(region.row_ys) - 1)
    n_cols = max(0, len(region.col_xs) - 1)
    hmerge = [[1] * n_cols for _ in range(n_rows)]
    if n_rows == 0 or n_cols < 2 or not lines:
        return hmerge
    # 對每 line 看 start_col / end_col — 用 non-whitespace chars 真實 X 範圍
    # （PDFLine.bbox 含 leading/trailing whitespace，常導致 X 範圍跨多 col 誤判跨欄）
    for ln in lines:
        if not ln.text or not ln.text.strip():
            continue
        # 找 non-whitespace chars 的真實 X 範圍
        non_ws_chars = [ch for ch in (ln.chars or [])
                         if ch.char and not ch.char.isspace()]
        if non_ws_chars:
            lx0 = min(ch.x for ch in non_ws_chars)
            lx1 = max(ch.x + ch.width for ch in non_ws_chars)
        else:
            lx0, _, lx1, _ = ln.bbox  # fallback
        _, ly0, _, ly1 = ln.bbox
        lcy = (ly0 + ly1) / 2.0
        # 找 row
        r = None
        for rr in range(n_rows):
            if region.row_ys[rr] - 1 <= lcy <= region.row_ys[rr + 1] + 1:
                r = rr
                break
        if r is None:
            continue
        # 找 start_col (line 左邊在哪 col 內)
        c0 = None
        c1 = None
        for cc in range(n_cols):
            if region.col_xs[cc] - 1 <= lx0 <= region.col_xs[cc + 1] + 1:
                c0 = cc
                break
        for cc in range(n_cols):
            if region.col_xs[cc] - 1 <= lx1 <= region.col_xs[cc + 1] + 1:
                c1 = cc
                break
        if c0 is None or c1 is None or c1 <= c0:
            continue
        # **coverage 判斷**：line 跨 col boundary 但大部分（≥ 70%）在某一 col 內，
        # 視為「邊界附近的水平 entry text」不該觸發 hmerge。常見 case：
        # form 內 cell 的 text 起點稍微超出 col 邊界但實際內容主體在 right col
        line_len = max(1e-3, lx1 - lx0)
        # c0 內覆蓋
        c0_cov = max(0.0, min(region.col_xs[c0 + 1], lx1) - max(region.col_xs[c0], lx0))
        c1_cov = max(0.0, min(region.col_xs[c1 + 1], lx1) - max(region.col_xs[c1], lx0))
        if c0_cov / line_len < 0.30:
            # 起點 in c0 但 ≤ 30% 線段在 c0 → 視為 c0+1 起，重新算
            c0 = c0 + 1
            if c0 > c1:
                continue
        elif c1_cov / line_len < 0.30:
            # 終點 in c1 但 ≤ 30% 線段在 c1 → 視為 c1-1 結束
            c1 = c1 - 1
            if c1 <= c0:
                continue
        if c1 <= c0:
            continue
        # line 跨 c0 到 c1：cells [c0..c1] 應該 merge
        span = c1 - c0 + 1
        # 只有當「目前該 col 沒被合併進左邊」+「目標 span > 1」才動
        if hmerge[r][c0] == 0:
            continue  # 已被合進更左 col 不再覆寫
        # 設 c0 gridSpan = max(現值, span)，右側設 0
        cur = hmerge[r][c0]
        if span > cur:
            # 還原舊 span 內被設 0 的右側
            for x in range(c0 + 1, min(c0 + cur, n_cols)):
                hmerge[r][x] = 1
            hmerge[r][c0] = span
            for x in range(c0 + 1, min(c0 + span, n_cols)):
                hmerge[r][x] = 0
    # v1.8.99 補強：geometry-based hmerge — 對 row r，若 col boundary X 在 PDF
    # 內無對應 v_line（同 row Y 範圍內 overlap >= 50%）→ (r, c)(r, c+1) 應合併。
    # 處理「值欄空白」的情境（前面 text-based 因 cell 內無 text 漏判）。
    raw_v = region.raw_v_lines or []
    if raw_v and n_cols >= 2:
        for r in range(n_rows):
            cy0 = region.row_ys[r]
            cy1 = region.row_ys[r + 1]
            row_h = max(1e-3, cy1 - cy0)
            # 從左到右掃 col boundary
            for c in range(n_cols - 1):
                if hmerge[r][c] == 0:
                    continue
                # 右邊 col 已被合進左 col 時 skip
                bx = region.col_xs[c + 1]
                has_vsep = False
                for lx, ly0, ly1 in raw_v:
                    if abs(lx - bx) > 3.0:
                        continue
                    oy = max(0.0, min(ly1, cy1) - max(ly0, cy0))
                    if oy / row_h >= 0.5:
                        has_vsep = True
                        break
                if not has_vsep:
                    # merge c into next: extend gridspan
                    # 注意：若 c+1 已有 hmerge > 1（被 text-based merge 設定），
                    # 也吸進來
                    add_span = hmerge[r][c + 1]
                    if add_span == 0:
                        continue  # 已被前面 merge 吃掉
                    hmerge[r][c] = hmerge[r][c] + add_span
                    hmerge[r][c + 1] = 0
                    # 後續被原 c+1 吃掉的 col 也轉到 c
                    for x in range(c + 2, n_cols):
                        if hmerge[r][x] == 0 and any(
                            hmerge[r][cc] > 0 and cc + hmerge[r][cc] > x
                            for cc in range(c + 1, x)
                        ):
                            # 仍屬被合範圍
                            pass
    return hmerge


def _detect_hmerge(region: TableRegion, drawings: list) -> list[list[int]]:
    """偵測橫向合併 (hMerge → docx w:gridSpan)。對每個 col boundary X + row Y
    範圍：若該位置無垂直線跨越 (overlap > row 高 50%) → cell 橫向合併右側。

    回 hmerge[r][c] = gridSpan int。預設 1 (單欄)。被合併的右側 col 設 0 表示
    docx 不獨立 cell（會被 gridSpan 吃掉）。
    """
    n_rows = max(0, len(region.row_ys) - 1)
    n_cols = max(0, len(region.col_xs) - 1)
    if n_rows == 0 or n_cols < 2:
        return [[1] * n_cols for _ in range(n_rows)]
    hmerge = [[1] * n_cols for _ in range(n_rows)]
    # 抽垂直 line
    v_lines = []
    for d in drawings or []:
        if d.type != "line":
            continue
        x0, y0, x1, y1 = d.bbox
        if abs(x1 - x0) > 1.5:
            continue
        v_lines.append(((x0 + x1) / 2.0, min(y0, y1), max(y0, y1)))
    # 安全上限：單個 cell 橫向 merge 不可超過 cols 的 50%
    MAX_HMERGE_SPAN = max(2, n_cols // 2)
    for r in range(n_rows):
        row_y0 = region.row_ys[r]
        row_y1 = region.row_ys[r + 1]
        row_h = row_y1 - row_y0
        c = 0
        while c < n_cols - 1:
            next_c = c + hmerge[r][c]
            if next_c >= n_cols:
                break
            boundary_x = region.col_xs[next_c]
            has_line = False
            for lx, ly0, ly1 in v_lines:
                if abs(lx - boundary_x) > 2.0:
                    continue
                overlap = min(ly1, row_y1) - max(ly0, row_y0)
                if overlap > row_h * 0.5:
                    has_line = True
                    break
            # 已合併到上限 → 強制不再 merge
            if hmerge[r][c] >= MAX_HMERGE_SPAN:
                c = next_c
                continue
            if not has_line:
                add = hmerge[r][next_c] if hmerge[r][next_c] > 0 else 1
                hmerge[r][c] += add
                hmerge[r][next_c] = 0
            else:
                c = next_c
    return hmerge


def _collect_text_lines_bbox(page) -> list[tuple]:
    """從 PDFPage.blocks 內所有 text line 收集 (x0, y0, x1, y1)。
    給 underline detection 用。"""
    out: list[tuple] = []
    for blk in (page.blocks or []):
        if getattr(blk, "block_type", "") != "text":
            continue
        for ln in (blk.lines or []):
            try:
                out.append(tuple(ln.bbox))
            except Exception:
                continue
    return out


def _filter_underline_strokes(h_lines: list, v_lines: list,
                                text_bboxes: list[tuple],
                                expand: float = 2.0) -> tuple[list, list]:
    """剔除「形似 underline / strike-through」的 strokes（pdf2docx 風格 semantic 過濾）。

    通用規則：一條 horizontal stroke 若被附近 horizontal text line 在 X 範圍內**完全包含**
    （stroke X 不超出 line X ±1pt），且 Y 距離 line 不超過 `expand`pt → 視為 underline，
    不算 table border。

    同理 vertical stroke 對 vertical text line（罕見但保留對稱）。

    這比單純「長度過濾」精準很多 — 短 stroke 不一定是 underline (可能是 sub-cell border)，
    長 stroke 也可能是 underline (例：整行底線)。語意判斷才正確。
    """
    if not text_bboxes:
        return h_lines, v_lines

    def _stroke_is_underline_h(stroke):
        """h_line = (x0, y0, x1, y1) — horizontal stroke"""
        sx0, sy = min(stroke[0], stroke[2]), (stroke[1] + stroke[3]) / 2.0
        sx1 = max(stroke[0], stroke[2])
        for lx0, ly0, lx1, ly1 in text_bboxes:
            # Y 距離（stroke 在 line 上方或下方均可）
            if not (ly0 - expand <= sy <= ly1 + expand + 4):
                continue
            # stroke X 範圍必須被 text line X 範圍包含（含 1pt 容差）
            if sx0 >= lx0 - 1.0 and sx1 <= lx1 + 1.0:
                # 額外確認 line 是 horizontal（寬 ≥ 高）
                if (lx1 - lx0) >= (ly1 - ly0):
                    return True
        return False

    h_keep = [s for s in h_lines if not _stroke_is_underline_h(s)]
    # 垂直 underline 罕見，先保守不過濾（不過濾誤殺率比過濾漏殺率低）
    v_keep = v_lines
    if not h_keep:
        return h_lines, v_lines
    return h_keep, v_keep


def _find_table_regions_one_page(page) -> list[TableRegion]:
    """單頁 grid 偵測。先把 h_lines 按 Y 大跳分組（同頁分離 table），
    每組獨立建 region。"""
    h, v = _classify_lines(page.drawings)
    if not h or not v:
        return []
    # 先剔 underline (semantic) — 比長度過濾精準
    text_bboxes = _collect_text_lines_bbox(page)
    h, v = _filter_underline_strokes(h, v, text_bboxes)
    groups = _split_lines_into_table_groups(h, v)
    out: list[TableRegion] = []
    for gh, gv in groups:
        region = _build_region_from_lines(page.page_num, gh, gv)
        if region is not None:
            color_hex, width_pt = _dominant_stroke_color_width(page.drawings,
                                                                  region.bbox)
            region.border_color_hex = color_hex
            region.border_width_pt = width_pt
            # 註：region.vmerge 在 paragraph_grouper 用 detect_vmerge_from_lines
            # （PDFLine 真值）覆寫，避免 drawings-only 啟發式把整 row 都吸進 row 0
            # 這裡先設空，待 grouper 階段填
            n_rows_ = max(0, len(region.row_ys) - 1)
            n_cols_ = max(0, len(region.col_xs) - 1)
            region.vmerge = [["" for _ in range(n_cols_)] for _ in range(n_rows_)]
            # hmerge 暫 disable — 啟發式對 invoice 類 table 過度激進，待重設計
            # region.hmerge = _detect_hmerge(region, page.drawings)
            n_rows = max(0, len(region.row_ys) - 1)
            n_cols = max(0, len(region.col_xs) - 1)
            region.hmerge = [[1] * n_cols for _ in range(n_rows)]
            out.append(region)
    return out


def detect_tables(pdf_truth) -> list[TableRegion]:
    """掃整份 PDFTruth 找表格。回 list[TableRegion]。"""
    out: list[TableRegion] = []
    for page in pdf_truth.pages:
        out.extend(_find_table_regions_one_page(page))
    return out


def block_in_table(block_bbox: tuple, table: TableRegion,
                    margin: float = 1.0) -> Optional[tuple[int, int]]:
    """判斷一個 block 是否落在某 table 內。回 (row_idx, col_idx) 或 None。
    用 block 中心點是否在 table 的某 cell bbox 內判定。"""
    bx0, by0, bx1, by1 = block_bbox
    bcx = (bx0 + bx1) / 2.0
    bcy = (by0 + by1) / 2.0
    # 不在 table bbox 外
    tx0, ty0, tx1, ty1 = table.bbox
    if not (tx0 - margin <= bcx <= tx1 + margin and
            ty0 - margin <= bcy <= ty1 + margin):
        return None
    # 找對應 row
    row_idx = None
    for r in range(len(table.row_ys) - 1):
        y_lo, y_hi = table.row_ys[r], table.row_ys[r + 1]
        if y_lo - margin <= bcy <= y_hi + margin:
            row_idx = r
            break
    col_idx = None
    for c in range(len(table.col_xs) - 1):
        x_lo, x_hi = table.col_xs[c], table.col_xs[c + 1]
        if x_lo - margin <= bcx <= x_hi + margin:
            col_idx = c
            break
    if row_idx is None or col_idx is None:
        return None
    return (row_idx, col_idx)

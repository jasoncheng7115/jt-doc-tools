"""無框線 virtual table 偵測（B8）。

PDF 內常有「視覺上是 table 但沒畫框線」的版面（典型：報價日期/到期日/銷售人員
這類 invoice header row、編號/說明/數量/單價/稅項/金額 column header）。
table_detector 靠 drawings 找 grid，這類沒線的 row 抓不到。

本模組對「已從 real table 排除的 free lines」做：

1) **Y 群聚**：相鄰 line Y 距 ≤ tol → 同一 row 群（同視覺橫向 row）
2) **多欄判定**：Y-group 含 N ≥ 2 line 且 X 方向有間隔（X gap > MIN_X_GAP）→ virtual row
3) **連續 virtual row 同 column 結構 → 同 virtual table**
   - 兩 virtual rows 列數一致（± 1 容忍）+ 各 col X 中心對齊 (± tol)
4) **單一 virtual row 不成表**（避免把孤立兩個短 line 誤判為表）

輸出 VirtualTableRegion list — 跟 real TableRegion 同介面，後續 builder 共用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .table_detector import TableRegion


Y_GROUP_TOLERANCE = 3.0          # 同 row 群 Y 容忍 (line center Y 差 ≤ 此)
MIN_X_GAP = 15.0                 # 同 row 內兩 line 至少 15pt 間隔才算多欄
MIN_LINES_PER_VROW = 2           # virtual row 至少 2 line
COL_ALIGN_TOLERANCE = 25.0       # 兩 row 同 col X 中心差 ≤ 此 → 對齊
# single-row virtual table（無連續 row backup）只接受 N ≥ 此（多欄結構明顯）
MIN_LINES_FOR_SINGLE_ROW_VTABLE = 3


def _line_y_center(line) -> float:
    return float((line.bbox[1] + line.bbox[3]) / 2.0)


def _line_x_center(line) -> float:
    return float((line.bbox[0] + line.bbox[2]) / 2.0)


def _group_lines_by_y(lines: list, tol: float = Y_GROUP_TOLERANCE) -> list[list]:
    """把 lines 依 Y 中心點群聚（相鄰差 ≤ tol 視為同 group）。"""
    if not lines:
        return []
    sl = sorted(lines, key=_line_y_center)
    out: list[list] = [[sl[0]]]
    for prev, cur in zip(sl, sl[1:]):
        if _line_y_center(cur) - _line_y_center(prev) <= tol:
            out[-1].append(cur)
        else:
            out.append([cur])
    return out


def _is_multi_col_row(group: list) -> bool:
    """Y-group 是否成多欄 row（N ≥ 2 + 同 row 內至少 1 對 line 有 X gap）"""
    if len(group) < MIN_LINES_PER_VROW:
        return False
    sg = sorted(group, key=_line_x_center)
    for prev, cur in zip(sg, sg[1:]):
        gap = float(cur.bbox[0]) - float(prev.bbox[2])
        if gap >= MIN_X_GAP:
            return True
    return False


def _rows_compatible(row_a: list, row_b: list) -> bool:
    """兩 row 是否屬於同一 virtual table — N 一致 ± 1 + X 對齊。"""
    if abs(len(row_a) - len(row_b)) > 1:
        return False
    n = min(len(row_a), len(row_b))
    sa = sorted(row_a, key=_line_x_center)[:n]
    sb = sorted(row_b, key=_line_x_center)[:n]
    for la, lb in zip(sa, sb):
        if abs(_line_x_center(la) - _line_x_center(lb)) > COL_ALIGN_TOLERANCE:
            return False
    return True


@dataclass
class VirtualTableRegion:
    """跟 TableRegion 同介面（給 builder 共用）。"""
    page_num: int
    row_ys: list[float]
    col_xs: list[float]
    bbox: tuple[float, float, float, float]
    cells: list[list[tuple]] = field(default_factory=list)
    # cell_lines[r][c] = list[PDFLine]，B 已直接配好
    cell_lines: list[list[list]] = field(default_factory=list)
    is_virtual: bool = True


def _build_virtual_region(page_num: int, rows: list[list]) -> VirtualTableRegion | None:
    """從多 row（每 row = list[PDFLine]）建 VirtualTableRegion。
    用每 row 的 line X 中心、Y 中心構造 row_ys / col_xs。"""
    if not rows or all(len(r) == 0 for r in rows):
        return None
    # col_xs: 取 row 內 line X 邊界（左右）— 對齊用每 row 的 union
    # 簡化：用 max(N) 那 row 的 X 邊界作 col 切分
    max_row = max(rows, key=len)
    sm = sorted(max_row, key=_line_x_center)
    # col 邊界：cols + 1 個分界
    if len(sm) == 1:
        col_xs = [float(sm[0].bbox[0]), float(sm[0].bbox[2])]
    else:
        col_xs = [float(sm[0].bbox[0])]
        for prev, cur in zip(sm, sm[1:]):
            mid = (float(prev.bbox[2]) + float(cur.bbox[0])) / 2.0
            col_xs.append(mid)
        col_xs.append(float(sm[-1].bbox[2]))
    # row_ys: 每 row 的中位 Y + 上下界
    y_centers = [sum(_line_y_center(l) for l in r) / len(r) for r in rows]
    # 上下界
    y_min = min(float(l.bbox[1]) for r in rows for l in r)
    y_max = max(float(l.bbox[3]) for r in rows for l in r)
    if len(rows) == 1:
        row_ys = [y_min, y_max]
    else:
        row_ys = [y_min]
        for prev_y, cur_y in zip(y_centers, y_centers[1:]):
            row_ys.append((prev_y + cur_y) / 2.0)
        row_ys.append(y_max)
    n_rows = len(rows)
    n_cols = len(col_xs) - 1
    # cells bbox grid
    cells: list[list[tuple]] = []
    for r in range(n_rows):
        row_cells: list[tuple] = []
        for c in range(n_cols):
            row_cells.append((col_xs[c], row_ys[r], col_xs[c + 1], row_ys[r + 1]))
        cells.append(row_cells)
    # cell_lines: 每 row 內 line 依 X 序填 cell
    cell_lines: list[list[list]] = [[[] for _ in range(n_cols)]
                                      for _ in range(n_rows)]
    for r_idx, r_lines in enumerate(rows):
        sr = sorted(r_lines, key=_line_x_center)
        for ln in sr:
            lcx = _line_x_center(ln)
            # 找 col
            for c in range(n_cols):
                if col_xs[c] - 1 <= lcx <= col_xs[c + 1] + 1:
                    cell_lines[r_idx][c].append(ln)
                    break
            else:
                # 落最後一格
                cell_lines[r_idx][-1].append(ln)
    bbox = (col_xs[0], y_min, col_xs[-1], y_max)
    return VirtualTableRegion(
        page_num=page_num, row_ys=row_ys, col_xs=col_xs, bbox=bbox,
        cells=cells, cell_lines=cell_lines,
    )


def detect_virtual_tables(free_lines: list, page_num: int) -> tuple[list, list]:
    """從 free_lines 內偵測 virtual tables。
    回 (virtual_table_regions, remaining_free_lines)。"""
    groups = _group_lines_by_y(free_lines)
    if not groups:
        return [], free_lines

    # 標記每 group 是否為「多欄 row」
    flags = [_is_multi_col_row(g) for g in groups]

    # 連續 True flag 視為一張 virtual table
    virtual_regions: list = []
    consumed_line_ids: set = set()

    i = 0
    while i < len(groups):
        if not flags[i]:
            i += 1
            continue
        rows = [groups[i]]
        j = i + 1
        while j < len(groups) and flags[j] and _rows_compatible(rows[-1], groups[j]):
            rows.append(groups[j])
            j += 1
        # 接受規則：
        # - multi-row 連續 ≥ 2 永遠接受
        # - single-row 且 N ≥ MIN_LINES_FOR_SINGLE_ROW_VTABLE (3) 接受
        # - single-row 且 N=2 + 兩 line X 中心距 > 200pt（頁底 footer：左下文字
        #   + 右下頁碼 並排）接受
        accept = False
        if len(rows) >= 2:
            accept = True
        elif len(rows) == 1:
            r0 = rows[0]
            if len(r0) >= MIN_LINES_FOR_SINGLE_ROW_VTABLE:
                accept = True
            elif len(r0) == 2:
                xs = sorted(_line_x_center(l) for l in r0)
                if xs[1] - xs[0] > 200.0:
                    accept = True
        if accept:
            reg = _build_virtual_region(page_num, rows)
            if reg is not None:
                virtual_regions.append(reg)
                for r in rows:
                    for ln in r:
                        consumed_line_ids.add(id(ln))
        i = j

    remaining = [ln for ln in free_lines if id(ln) not in consumed_line_ids]
    return virtual_regions, remaining

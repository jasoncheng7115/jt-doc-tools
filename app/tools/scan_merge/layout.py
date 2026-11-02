"""掃描拼合的「自動排列」：把各塊內容排到 A4 上，互不重疊。

「依原始位置」照掃描時的位置擺，兩份掃描的證件常常落在同一個角落而疊在一起。
自動排列改成：

* **依傳入順序**由上而下、由左而右一列一列排（上傳時先放正面、再放反面，結果就是正面在上）；
* 每一列水平置中，整塊內容垂直置中；
* 原本的大小放得下就**不放大、不縮小**；放不下時全部**等比例**一起縮小，直到放得下為止。

畫面（`POST /tools/scan-merge/layout`）與對外一次性 API（`layout=auto`）都走這一支，
不在前端另寫一份 —— 兩份各自演化，總有一天排出來的位置不一樣。

座標一律是 A4 的 0..1 比例：`w` 是佔 A4 寬度的比例、`h` 是佔 A4 高度的比例。
"""
from __future__ import annotations

import math

A4_W_PT = 595.276
A4_H_PT = 841.890

#: 紙張四周留白（點）。約 12 mm，一般印表機的不可列印邊界都在這之內。
MARGIN_PT = 34.0
#: 各塊之間的間距（點），約 6 mm。
GAP_PT = 17.0
#: 最多幾塊（前端一次上傳最多 30 塊，多檔累加；超過這個數字就是異常請求）。
MAX_ITEMS = 300
#: 縮到這個比例還放不下就不再縮（實際上不會發生：300 塊也放得下）。
_MIN_SCALE = 0.02


class LayoutError(ValueError):
    """輸入不合法（缺欄位、非數字、超過上限）。"""


def _clean(items: list[dict]) -> list[tuple[str, float, float]]:
    if not isinstance(items, list):
        raise LayoutError("items 必須是清單")
    if len(items) > MAX_ITEMS:
        raise LayoutError(f"最多 {MAX_ITEMS} 塊")
    out: list[tuple[str, float, float]] = []
    for it in items:
        if not isinstance(it, dict):
            raise LayoutError("items 的每一項都要是物件")
        try:
            w = float(it.get("w"))
            h = float(it.get("h"))
        except (TypeError, ValueError):
            raise LayoutError("w / h 必須是數字")
        if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
            raise LayoutError("w / h 必須大於 0")
        out.append((str(it.get("id", "")), min(w, 1.0), min(h, 1.0)))
    return out


def _rows(sizes: list[tuple[float, float]], avail_w: float) -> list[list[int]]:
    """一列一列放（第一塊放不下就換列）；回傳每列的索引。"""
    rows: list[list[int]] = []
    cur: list[int] = []
    cur_w = 0.0
    for i, (w, _h) in enumerate(sizes):
        need = w if not cur else cur_w + GAP_PT + w
        if cur and need > avail_w + 1e-6:
            rows.append(cur)
            cur, cur_w = [i], w
        else:
            cur.append(i)
            cur_w = need
    if cur:
        rows.append(cur)
    return rows


def _fits(sizes, avail_w, avail_h):
    if any(w > avail_w + 1e-6 for w, _ in sizes):
        return None
    rows = _rows(sizes, avail_w)
    total_h = sum(max(sizes[i][1] for i in r) for r in rows) + GAP_PT * (len(rows) - 1)
    return rows if total_h <= avail_h + 1e-6 else None


def auto_layout(items: list[dict]) -> list[dict]:
    """把 `[{id, w, h}]` 排到 A4 上，回傳 `[{id, x, y, w, h}]`（順序與輸入相同）。"""
    clean = _clean(items)
    if not clean:
        return []
    base = [(w * A4_W_PT, h * A4_H_PT) for _id, w, h in clean]
    avail_w = A4_W_PT - 2 * MARGIN_PT
    avail_h = A4_H_PT - 2 * MARGIN_PT

    scale = 1.0
    while True:
        sizes = [(w * scale, h * scale) for w, h in base]
        rows = _fits(sizes, avail_w, avail_h)
        if rows is not None or scale <= _MIN_SCALE:
            break
        scale *= 0.97
    if rows is None:            # 理論上到不了；保險起見每塊各自一列
        rows = [[i] for i in range(len(sizes))]

    row_h = [max(sizes[i][1] for i in r) for r in rows]
    total_h = sum(row_h) + GAP_PT * (len(rows) - 1)
    y = MARGIN_PT + max(0.0, (avail_h - total_h) / 2)
    pos: dict[int, tuple[float, float]] = {}
    for r, rh in zip(rows, row_h):
        row_w = sum(sizes[i][0] for i in r) + GAP_PT * (len(r) - 1)
        x = MARGIN_PT + max(0.0, (avail_w - row_w) / 2)
        for i in r:
            w, h = sizes[i]
            pos[i] = (x, y + (rh - h) / 2)     # 同一列裡較矮的垂直置中
            x += w + GAP_PT
        y += rh + GAP_PT

    out = []
    for i, (cid, _w, _h) in enumerate(clean):
        w, h = sizes[i]
        x, yy = pos[i]
        out.append({
            "id": cid,
            "x": round(x / A4_W_PT, 5),
            "y": round(yy / A4_H_PT, 5),
            "w": round(w / A4_W_PT, 5),
            "h": round(h / A4_H_PT, 5),
        })
    return out

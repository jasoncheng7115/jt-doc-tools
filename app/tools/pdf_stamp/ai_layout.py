"""AI 用印的位置計算（v1.17.54）：把 AI 送來的「要蓋在哪裡」換成實際的頁與座標。

AI 不擅長量座標，所以除了原本的 `x_mm` / `y_mm`，另外接受：

* `page`：整數＝第幾頁（**0 起算**，跟原本一樣）；字串＝`all` / `first` / `last` / `3` / `2-5` / `1,3,5-6`（**1 起算**，
  跟人講話的習慣一樣）。
* `anchor`：頁面角落或邊的中間（`top-left` … `bottom-right`、`center`），離邊 `margin_mm`（預設 15），再加 `offset_x_mm` /
  `offset_y_mm`（往右、往下為正）。
* `anchor_text`：找頁面上的文字（例如「負責人」「立書人」），蓋在它的右邊（預設）/ 左邊 / 上面 / 下面 / 正上方，
  間隔 `gap_mm`（預設 2）；`anchor_match` 選第幾個（`first` 預設 / `last` / 1 起算的數字）。頁數是好幾頁時，
  找不到那段文字的頁跳過；每一頁都找不到才算錯（掃描檔沒有文字層就找不到，要改用座標或角落）。
* 大小：只給 `width_mm`（或只給 `height_mm`）時照章的比例算另一邊；都沒給就用章在資產庫設好的大小。
* `opacity`：0.05～1。

算出來的位置一律夾在頁面裡面（不會蓋到頁外）；被夾過的標 `clamped`。座標是**顯示的頁面**（旋轉過的頁照轉過的樣子）、
左上角為原點、單位 mm，跟網頁用印工具、`pdf_utils.stamp_pdf` 一致。

這裡只算位置，不碰權限與用量 —— 那些在 `core.ai_stamp`（每一處都還是要過同樣的檢查）。
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

#: 一個請求最多幾項（展開前）、展開後最多幾處（「all」碰到很長的文件時擋下來）
MAX_ITEMS = 50
MAX_EXPANDED = 200
#: 位置與大小上限（mm）；A0 長邊 1189 mm，再大就不是正常的頁面
MAX_MM = 2000.0
ANCHORS = ("top-left", "top-center", "top-right", "center-left", "center", "center-right",
           "bottom-left", "bottom-center", "bottom-right")
SIDES = ("right", "left", "above", "below", "over")
KEYS = {"asset_id", "page", "x_mm", "y_mm", "width_mm", "height_mm", "rotation_deg", "opacity",
        "anchor", "margin_mm", "offset_x_mm", "offset_y_mm", "anchor_text", "anchor_match", "anchor_side", "gap_mm"}
_PT_PER_MM = 72.0 / 25.4
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_RANGE_RE = re.compile(r"^\s*(\d{1,5})\s*(?:-\s*(\d{1,5}))?\s*$")


class LayoutError(ValueError):
    """位置寫錯（給 AI 看的訊息，講清楚哪一項哪個欄位）。"""

    def __init__(self, message: str, code: str = "BAD_PLACEMENT"):
        super().__init__(message)
        self.message, self.code = message, code


def _num(it: dict, k: str, i: int) -> Optional[float]:
    if k not in it or it[k] is None:
        return None
    v = it[k]
    if isinstance(v, bool):
        raise LayoutError(f"placements[{i}].{k} 要是數字")
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        raise LayoutError(f"placements[{i}].{k} 要是數字")
    if not math.isfinite(f):                        # NaN 跟任何數比都是 False，上下限擋不住
        raise LayoutError(f"placements[{i}].{k} 不可以是 NaN 或無限大")
    return f


def validate(items: Any) -> list[dict]:
    """檢查格式（還沒看文件）。回乾淨的項目（只留認得的欄位）。"""
    if not isinstance(items, list) or not items:
        raise LayoutError("placements 要是非空的陣列")
    if len(items) > MAX_ITEMS:
        raise LayoutError(f"一次最多 {MAX_ITEMS} 項")
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            raise LayoutError(f"placements[{i}] 必須是物件")
        extra = set(it) - KEYS
        if extra:
            raise LayoutError(f"placements[{i}] 不收這些欄位：{sorted(extra)}")
        aid = it.get("asset_id")
        if not isinstance(aid, str) or not _ID_RE.fullmatch(aid):
            raise LayoutError(f"placements[{i}].asset_id 要是 stamp_list 列出的章編號", "NO_ASSET")
        c: dict[str, Any] = {"asset_id": aid}
        page = it.get("page", 0)
        if isinstance(page, bool) or isinstance(page, float) and not page.is_integer():
            raise LayoutError(f"placements[{i}].page 要是整數或像 \"all\"、\"2-5\" 的字串")
        if isinstance(page, (int, float)):
            if page < 0:
                raise LayoutError(f"placements[{i}].page 不可為負")
            c["page"] = int(page)
        elif isinstance(page, str) and 0 < len(page) <= 100:
            c["page"] = page.strip().lower()
            parse_pages(c["page"], 10 ** 6, i)          # 先驗格式（頁數之後才知道）
        else:
            raise LayoutError(f"placements[{i}].page 要是整數或像 \"all\"、\"2-5\" 的字串")
        for k in ("x_mm", "y_mm", "width_mm", "height_mm", "rotation_deg", "opacity", "margin_mm",
                  "offset_x_mm", "offset_y_mm", "gap_mm"):
            v = _num(it, k, i)
            if v is not None:
                c[k] = v
        for k in ("width_mm", "height_mm"):
            if k in c and not (0 < c[k] <= MAX_MM):
                raise LayoutError(f"placements[{i}].{k} 要在 0 到 {MAX_MM:g} 之間")
        for k in ("x_mm", "y_mm", "offset_x_mm", "offset_y_mm"):
            if k in c and abs(c[k]) > MAX_MM:
                raise LayoutError(f"placements[{i}].{k} 超出範圍")
        for k in ("margin_mm", "gap_mm"):
            if k in c and not (0 <= c[k] <= 500):
                raise LayoutError(f"placements[{i}].{k} 要在 0 到 500 之間")
        if "rotation_deg" in c and abs(c["rotation_deg"]) > 360:
            raise LayoutError(f"placements[{i}].rotation_deg 要在 -360 到 360 之間")
        if "opacity" in c and not (0.05 <= c["opacity"] <= 1):
            raise LayoutError(f"placements[{i}].opacity 要在 0.05 到 1 之間")
        anchor, text = it.get("anchor"), it.get("anchor_text")
        has_xy = "x_mm" in c or "y_mm" in c
        if sum(bool(x) for x in (has_xy, anchor, text)) > 1:
            raise LayoutError(f"placements[{i}]：x_mm / y_mm、anchor、anchor_text 只能擇一")
        if anchor is not None:
            if anchor not in ANCHORS:
                raise LayoutError(f"placements[{i}].anchor 只能是 {list(ANCHORS)}")
            c["anchor"] = anchor
        if text is not None:
            if not isinstance(text, str) or not text.strip() or len(text) > 50:
                raise LayoutError(f"placements[{i}].anchor_text 要是 1～50 字")
            c["anchor_text"] = text.strip()
            side = it.get("anchor_side", "right")
            if side not in SIDES:
                raise LayoutError(f"placements[{i}].anchor_side 只能是 {list(SIDES)}")
            c["anchor_side"] = side
            m = it.get("anchor_match", "first")
            if m in ("first", "last"):
                c["anchor_match"] = m
            elif isinstance(m, int) and not isinstance(m, bool) and 1 <= m <= 999:
                c["anchor_match"] = m
            else:
                raise LayoutError(f"placements[{i}].anchor_match 只能是 \"first\"、\"last\" 或 1 起算的數字")
        elif "anchor_side" in it or "anchor_match" in it or "gap_mm" in it:
            raise LayoutError(f"placements[{i}]：anchor_side / anchor_match / gap_mm 要搭配 anchor_text")
        if ("margin_mm" in c) and not anchor:
            raise LayoutError(f"placements[{i}].margin_mm 要搭配 anchor")
        if ("offset_x_mm" in c or "offset_y_mm" in c) and not (anchor or text):
            raise LayoutError(f"placements[{i}]：offset_x_mm / offset_y_mm 要搭配 anchor 或 anchor_text")
        out.append(c)
    return out


def parse_pages(spec: Any, n: int, i: int = 0) -> list[int]:
    """`page` → 0 起算的頁碼清單。整數是 0 起算；字串是 1 起算。超出頁數就錯（不默默略過）。"""
    if isinstance(spec, int):
        if spec >= n:
            raise LayoutError(f"第 {spec + 1} 頁不存在（這份只有 {n} 頁）", "NO_PAGE")
        return [spec]
    s = str(spec).strip().lower()
    if s == "all":
        return list(range(n))
    if s == "first":
        return [0]
    if s == "last":
        return [n - 1]
    pages: list[int] = []
    for part in s.split(","):
        m = _RANGE_RE.match(part)
        if not m:
            raise LayoutError(f"placements[{i}].page 看不懂：{str(spec)[:30]}（例：\"all\"、\"last\"、\"2-5\"、\"1,3\"）")
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if a < 1 or b < a:
            raise LayoutError(f"placements[{i}].page 的範圍不對：{part.strip()}")
        if b > n:
            raise LayoutError(f"第 {b} 頁不存在（這份只有 {n} 頁）", "NO_PAGE")
        for p in range(a - 1, b):
            if p not in pages:
                pages.append(p)
    return pages


def _size(c: dict, preset: dict) -> tuple[float, float]:
    pw, ph = float(preset.get("width_mm") or 30), float(preset.get("height_mm") or 30)
    ratio = ph / pw if pw > 0 and ph > 0 else 1.0
    w, h = c.get("width_mm"), c.get("height_mm")
    if w is not None and h is None:
        h = w * ratio
    elif h is not None and w is None:
        w = h / ratio
    elif w is None:
        w, h = pw, ph
    return float(w), float(h)


def _find_text(page, text: str) -> list:
    """頁面上那段文字的位置（顯示座標、pt）。找不到時把空白拿掉再找一次（「負 責 人」）。"""
    hits = page.search_for(text)
    if not hits and " " in text:
        hits = page.search_for(text.replace(" ", ""))
    rm = page.rotation_matrix
    out = [r * rm for r in hits]
    out = [r for r in out if r.is_valid and not r.is_empty]
    out.sort(key=lambda r: (round(r.y0, 1), r.x0))       # 由上而下、由左而右
    return out


def resolve(doc, items: list[dict], presets: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """實際位置。`doc` 是開好的 fitz 文件；`presets` 是 {asset_id: 章在資產庫的預設位置}。
    回 (placements, skipped)：placements 每一處 `{asset_id, page, x_mm, y_mm, width_mm, height_mm, rotation_deg,
    opacity, how, clamped}`；skipped 是 anchor_text 在那一頁找不到而跳過的 `{item, page}`。"""
    n = doc.page_count
    out: list[dict] = []
    skipped: list[dict] = []
    for i, c in enumerate(items):
        pr = presets.get(c["asset_id"]) or {}
        w, h = _size(c, pr)
        rot = float(c.get("rotation_deg", pr.get("rotation_deg") or 0))
        op = float(c.get("opacity", 1.0))
        found_any = False
        for p in parse_pages(c["page"], n, i):
            page = doc[p]
            W, H = page.rect.width / _PT_PER_MM, page.rect.height / _PT_PER_MM
            if c.get("anchor_text"):
                hits = _find_text(page, c["anchor_text"])
                if not hits:
                    skipped.append({"item": i, "page": p})
                    continue
                m = c.get("anchor_match", "first")
                if isinstance(m, int):
                    if m > len(hits):
                        skipped.append({"item": i, "page": p})
                        continue
                    r = hits[m - 1]
                else:
                    r = hits[-1] if m == "last" else hits[0]
                found_any = True
                x0, y0, x1, y1 = (v / _PT_PER_MM for v in (r.x0, r.y0, r.x1, r.y1))
                cx, cy, g = (x0 + x1) / 2, (y0 + y1) / 2, float(c.get("gap_mm", 2))
                side = c.get("anchor_side", "right")
                x, y = {"right": (x1 + g, cy - h / 2), "left": (x0 - g - w, cy - h / 2),
                        "above": (cx - w / 2, y0 - g - h), "below": (cx - w / 2, y1 + g),
                        "over": (cx - w / 2, cy - h / 2)}[side]
                how = f"anchor_text:{side}"
            elif c.get("anchor"):
                mg = float(c.get("margin_mm", 15))
                vert, _, horiz = c["anchor"].partition("-") if "-" in c["anchor"] else ("center", "", "center")
                x = {"left": mg, "center": (W - w) / 2, "right": W - mg - w}[horiz]
                y = {"top": mg, "center": (H - h) / 2, "bottom": H - mg - h}[vert]
                how = f"anchor:{c['anchor']}"
            else:
                x = c.get("x_mm", pr.get("x_mm", 105))
                y = c.get("y_mm", pr.get("y_mm", 250))
                how = "xy" if ("x_mm" in c or "y_mm" in c) else "default"
            x += float(c.get("offset_x_mm", 0))
            y += float(c.get("offset_y_mm", 0))
            cx_, cy_ = min(max(x, 0.0), max(0.0, W - w)), min(max(y, 0.0), max(0.0, H - h))
            clamped = abs(cx_ - x) > 0.01 or abs(cy_ - y) > 0.01
            out.append({"asset_id": c["asset_id"], "page": p, "x_mm": round(cx_, 2), "y_mm": round(cy_, 2),
                        "width_mm": round(w, 2), "height_mm": round(h, 2), "rotation_deg": rot,
                        "opacity": op, "how": how, "clamped": clamped})
            if len(out) > MAX_EXPANDED:
                raise LayoutError(f"展開後超過 {MAX_EXPANDED} 處（\"all\" 碰到很長的文件時請改成範圍）")
        if c.get("anchor_text") and not found_any:
            raise LayoutError(f"placements[{i}]：頁面上找不到「{c['anchor_text']}」"
                              "（掃描檔沒有文字層時找不到，請改用 anchor 或 x_mm / y_mm）", "ANCHOR_NOT_FOUND")
    return out, skipped


def public(placements: list[dict], names: dict[str, str]) -> list[dict]:
    """回給 AI 的落點（頁碼 0 起算＋1 起算都給，下次可以直接用同一組設定）。"""
    return [{"asset_id": p["asset_id"], "name": names.get(p["asset_id"], ""), "page": p["page"],
             "page_number": p["page"] + 1, "x_mm": p["x_mm"], "y_mm": p["y_mm"], "width_mm": p["width_mm"],
             "height_mm": p["height_mm"], "rotation_deg": p["rotation_deg"], "opacity": p["opacity"],
             "how": p["how"], "clamped": p["clamped"]} for p in placements]


def preview_png(src_path, placements: list[dict], names: dict[str, str], *, dpi: int = 100) -> tuple[bytes, int]:
    """預覽：第一處所在的那一頁畫成 PNG，章的位置只畫**框線與名稱**（不放章圖 —— 還沒確認的章不可以有蓋好的樣子）。
    回 (png, 頁碼 0 起算)。"""
    import fitz
    if not placements:
        raise LayoutError("沒有任何一處可以預覽")
    p0 = placements[0]["page"]
    with fitz.open(str(src_path)) as doc:
        page = doc[p0]
        dm = page.derotation_matrix
        for p in (x for x in placements if x["page"] == p0):
            r = fitz.Rect(p["x_mm"] * _PT_PER_MM, p["y_mm"] * _PT_PER_MM,
                          (p["x_mm"] + p["width_mm"]) * _PT_PER_MM, (p["y_mm"] + p["height_mm"]) * _PT_PER_MM)
            ur = r * dm
            page.draw_rect(ur, color=(0.86, 0.15, 0.15), fill=(0.86, 0.15, 0.15), fill_opacity=0.12,
                           width=1.2, dashes="[4 3] 0", overlay=True)
            label = (names.get(p["asset_id"]) or "")[:20]
            if label:
                try:
                    page.insert_textbox(ur, label, fontsize=max(6, min(11, r.height / 4)), fontname="china-t",
                                        color=(0.75, 0.1, 0.1), align=1, rotate=page.rotation, overlay=True)
                except Exception:                                   # noqa: BLE001 —— 字放不進去就只留框
                    pass
        pix = page.get_pixmap(dpi=dpi)
        return pix.tobytes("png"), p0

"""掃描拼合 — 內容偵測 + 背景淨白（純函式，可測）。

核心原則：
  - 灰階 / 飽和度遮罩「只用來找出內容在哪」（定位），
    實際裁出與輸出的永遠是**原始彩色像素**，絕不二值化 / 去彩。
  - 背景淨白是保守做法：只把「夠亮且接近中性灰」的掃描底色提亮成純白，
    任何有彩度的內容（照片、印刷色、印章）都不動，確保不會讓彩色變白。

不依賴 scipy —— 連通元件用 numpy 自寫 BFS（在縮圖上跑，便宜）。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass
class Region:
    """偵測到的一塊內容。

    像素座標相對於 source 影像；fractional 是相對於整張 source page（0..1），
    供前端把它擺到 A4 同樣的相對位置用。
    """
    x0: int
    y0: int
    x1: int
    y1: int
    fx: float  # 左上 x / 頁寬
    fy: float  # 左上 y / 頁高
    fw: float  # 寬 / 頁寬
    fh: float  # 高 / 頁高

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


def to_rgb(img: Image.Image) -> Image.Image:
    """把任意模式（含 RGBA / palette / CMYK / L）正規化成 RGB，
    透明處以白底合成（掃描合併的底色就是白）。"""
    if img.mode == "RGB":
        return img
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[3])
        return bg
    if img.mode == "P":
        conv = img.convert("RGBA")
        bg = Image.new("RGB", conv.size, "white")
        bg.paste(conv, mask=conv.split()[3])
        return bg
    return img.convert("RGB")


def _luminance(arr: np.ndarray) -> np.ndarray:
    """arr: HxWx3 uint8 → HxW float（0..255）。"""
    r = arr[:, :, 0].astype(np.float32)
    g = arr[:, :, 1].astype(np.float32)
    b = arr[:, :, 2].astype(np.float32)
    return 0.299 * r + 0.587 * g + 0.114 * b


def _saturation(arr: np.ndarray) -> np.ndarray:
    """HSV 的 S 通道（0..1）。中性灰 / 黑 / 白 → 接近 0；彩色 → 較大。"""
    mx = arr.max(axis=2).astype(np.float32)
    mn = arr.min(axis=2).astype(np.float32)
    out = np.zeros_like(mx)
    nz = mx > 0
    out[nz] = (mx[nz] - mn[nz]) / mx[nz]
    return out


def _content_mask(arr: np.ndarray) -> np.ndarray:
    """回傳 bool 遮罩：True = 有內容（非背景）。

    內容 = 比頁面背景明顯暗（文字 / 邊框 / 深色照片） **或** 有彩度
    （亮色但有顏色，例如白底上的紅印章）。背景亮度由「邊框像素中位數」
    估計，對微黃 / 灰底掃描穩健。
    """
    lum = _luminance(arr)
    sat = _saturation(arr)
    h, w = lum.shape
    # 邊框取樣（上下各 3% 列 + 左右各 3% 行）估背景亮度
    bh = max(1, int(h * 0.03))
    bw = max(1, int(w * 0.03))
    border = np.concatenate([
        lum[:bh, :].ravel(), lum[-bh:, :].ravel(),
        lum[:, :bw].ravel(), lum[:, -bw:].ravel(),
    ])
    bg = float(np.median(border)) if border.size else 255.0
    delta = max(18.0, bg * 0.06)
    dark = lum < (bg - delta)
    colored = sat > 0.18
    return dark | colored


def _dilate(mask: np.ndarray, iters: int = 2) -> np.ndarray:
    """3x3 二值膨脹（numpy roll，無 scipy）。把相鄰內容黏成一塊，
    讓同一張卡片的文字行 / 照片合成單一連通區。"""
    m = mask.copy()
    for _ in range(max(0, iters)):
        acc = m.copy()
        acc[:-1, :] |= m[1:, :]
        acc[1:, :] |= m[:-1, :]
        acc[:, :-1] |= m[:, 1:]
        acc[:, 1:] |= m[:, :-1]
        m = acc
    return m


def _merge_aligned_components(
    comps: list[tuple[int, int, int, int, int]],
    *,
    y_overlap_frac: float = 0.5,
    gap_frac_of_height: float = 0.6,
) -> list[tuple[int, int, int, int, int]]:
    """合併「Y 範圍重疊大、X 間距小」的元件 — 同一張卡片內的文字 / 照片可能被
    細白縫切成兩塊（例：健保卡的卡身 + 大頭照），這層補回成一塊。

    條件：兩塊 Y 重疊 >= y_overlap_frac × 較矮的那塊；X 間距
    <= gap_frac_of_height × 較矮的那塊。重複到不再合併為止。

    比起無腦放大 dilation kernel 安全：真正分開的兩張卡片（左右並排）X 間距通常
    遠大於卡片高度的 60%，不會誤合。
    """
    items = list(comps)
    changed = True
    while changed:
        changed = False
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a_area, ax0, ay0, ax1, ay1 = items[i]
                b_area, bx0, by0, bx1, by1 = items[j]
                # Y 重疊（區段相交長度）
                yov = max(0, min(ay1, by1) - max(ay0, by0) + 1)
                yhmin = min(ay1 - ay0 + 1, by1 - by0 + 1)
                if yov < y_overlap_frac * yhmin:
                    continue
                # X 間距（負值代表已重疊）
                if ax1 < bx0:
                    gap = bx0 - ax1 - 1
                elif bx1 < ax0:
                    gap = ax0 - bx1 - 1
                else:
                    gap = -1
                if gap > gap_frac_of_height * yhmin:
                    continue
                # 合併
                merged = (a_area + b_area,
                          min(ax0, bx0), min(ay0, by0),
                          max(ax1, bx1), max(ay1, by1))
                items = [c for k, c in enumerate(items) if k != i and k != j] + [merged]
                changed = True
                break
            if changed:
                break
    return items


def _label_components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """4-連通元件標記（BFS）。回傳每塊 (area, x0, y0, x1, y1)（含界）。"""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out: list[tuple[int, int, int, int, int]] = []
    for sy in range(h):
        row = mask[sy]
        for sx in range(w):
            if not row[sx] or seen[sy, sx]:
                continue
            area = 0
            x0 = x1 = sx
            y0 = y1 = sy
            q: deque[tuple[int, int]] = deque()
            q.append((sy, sx))
            seen[sy, sx] = True
            while q:
                cy, cx = q.popleft()
                area += 1
                if cx < x0:
                    x0 = cx
                if cx > x1:
                    x1 = cx
                if cy < y0:
                    y0 = cy
                if cy > y1:
                    y1 = cy
                if cy > 0 and mask[cy - 1, cx] and not seen[cy - 1, cx]:
                    seen[cy - 1, cx] = True
                    q.append((cy - 1, cx))
                if cy + 1 < h and mask[cy + 1, cx] and not seen[cy + 1, cx]:
                    seen[cy + 1, cx] = True
                    q.append((cy + 1, cx))
                if cx > 0 and mask[cy, cx - 1] and not seen[cy, cx - 1]:
                    seen[cy, cx - 1] = True
                    q.append((cy, cx - 1))
                if cx + 1 < w and mask[cy, cx + 1] and not seen[cy, cx + 1]:
                    seen[cy, cx + 1] = True
                    q.append((cy, cx + 1))
            out.append((area, x0, y0, x1, y1))
    return out


def detect_regions(
    img: Image.Image,
    *,
    min_area_frac: float = 0.006,
    pad_frac: float = 0.0,
    label_max_edge: int = 240,
) -> list[Region]:
    """偵測 source 影像中所有「有內容」的區塊。

    在縮到 ~240px 的遮罩上做膨脹 + 連通元件（便宜），再把外框映射回原尺寸。
    回傳依「由上而下、由左而右」排序的 Region 清單。空白頁回 []。
    """
    rgb = np.asarray(to_rgb(img), dtype=np.uint8)
    H, W = rgb.shape[:2]
    if H == 0 or W == 0:
        return []
    full_mask = _content_mask(rgb)

    # 縮小遮罩做標記（用 PIL BOX 重採樣後 >0）
    long_edge = max(H, W)
    scale = label_max_edge / long_edge if long_edge > label_max_edge else 1.0
    if scale < 1.0:
        sw = max(1, int(round(W * scale)))
        sh = max(1, int(round(H * scale)))
        small_img = Image.fromarray((full_mask * 255).astype(np.uint8)).resize(
            (sw, sh), Image.BOX
        )
        small = np.asarray(small_img) > 0
    else:
        sw, sh = W, H
        small = full_mask

    small = _dilate(small, iters=2)
    comps = _label_components(small)
    # 同一張卡片內常有細白縫（卡身 + 大頭照、姓名 + 條碼分塊等）會被切成多個
    # connected component，這層把 Y 排成同高 + X 間距很近的合起來。
    comps = _merge_aligned_components(comps)
    small_area = sw * sh
    pad_px = pad_frac * long_edge

    regions: list[Region] = []
    inv = 1.0 / scale
    for area, sx0, sy0, sx1, sy1 in comps:
        if area < min_area_frac * small_area:
            continue
        # 先把（膨脹後的）小圖外框映射回原尺寸，外擴一點吸收下採樣誤差
        wx0 = max(0, int(sx0 * inv) - 4)
        wy0 = max(0, int(sy0 * inv) - 4)
        wx1 = min(W, int((sx1 + 1) * inv) + 4)
        wy1 = min(H, int((sy1 + 1) * inv) + 4)
        # 在此視窗內用「未膨脹」的原遮罩求緊貼外框（不含膨脹墊出來的空隙）
        sub = full_mask[wy0:wy1, wx0:wx1]
        rows = np.where(sub.any(axis=1))[0]
        cols = np.where(sub.any(axis=0))[0]
        if rows.size == 0 or cols.size == 0:
            continue
        tx0 = wx0 + int(cols[0])
        ty0 = wy0 + int(rows[0])
        tx1 = wx0 + int(cols[-1]) + 1
        ty1 = wy0 + int(rows[-1]) + 1
        # 可選 padding（預設 0 → 紅框緊貼內容）
        x0 = max(0, int(tx0 - pad_px))
        y0 = max(0, int(ty0 - pad_px))
        x1 = min(W, int(tx1 + pad_px))
        y1 = min(H, int(ty1 + pad_px))
        if x1 <= x0 or y1 <= y0:
            continue
        regions.append(Region(
            x0=x0, y0=y0, x1=x1, y1=y1,
            fx=x0 / W, fy=y0 / H, fw=(x1 - x0) / W, fh=(y1 - y0) / H,
        ))

    regions.sort(key=lambda r: (r.y0, r.x0))
    return regions


def crop_region(img: Image.Image, region: Region) -> Image.Image:
    """裁出原彩色 crop（不做任何色彩變動）。"""
    return to_rgb(img).crop((region.x0, region.y0, region.x1, region.y1))


def crop_card(
    img: Image.Image,
    region: Region,
    *,
    whiten: bool = False,
    edge_inset: int = 2,
    val_thresh: float = 0.86,
    sat_thresh: float = 0.10,
) -> Image.Image:
    """裁出一塊內容並回 **RGBA**：實際卡片矩形不透明，外圍 padding / 背景透明。

    為什麼要透明：crop 是「內容外框 + padding」的矩形，若 padding 區是白色不透明，
    兩塊內容重疊時會用白邊蓋掉鄰塊。改成透明後，只有真正的卡片矩形會遮擋，
    padding 空隙看得到底下（白底 A4 或鄰塊）。

    whiten：在不透明的卡片區內，把「夠亮且接近中性灰」的掃描底色提亮成純白
    （保守，不去彩）。透明與否不受 whiten 影響——padding 永遠透明。
    """
    crop = to_rgb(img).crop((region.x0, region.y0, region.x1, region.y1))
    arr = np.asarray(crop, dtype=np.uint8)
    h, w = arr.shape[:2]
    mask = _content_mask(arr)
    alpha = np.zeros((h, w), dtype=np.uint8)
    ys, xs = np.where(mask)
    if xs.size == 0:
        alpha[:] = 255  # 保底：偵測不到內容就整塊不透明
        rgba = np.dstack([arr, alpha])
        return Image.fromarray(rgba, mode="RGBA")
    cx0 = max(0, int(xs.min()) - edge_inset)
    cy0 = max(0, int(ys.min()) - edge_inset)
    cx1 = min(w, int(xs.max()) + 1 + edge_inset)
    cy1 = min(h, int(ys.max()) + 1 + edge_inset)
    alpha[cy0:cy1, cx0:cx1] = 255  # 卡片矩形不透明，padding ring 透明
    rgba = np.dstack([arr, alpha])
    if whiten:
        sub = rgba[cy0:cy1, cx0:cx1, :3]
        lum = _luminance(sub) / 255.0
        sat = _saturation(sub)
        bg = (lum > val_thresh) & (sat < sat_thresh)
        sub[bg] = (255, 255, 255)
    return Image.fromarray(rgba, mode="RGBA")


def whiten_background(
    crop: Image.Image,
    *,
    val_thresh: float = 0.86,
    sat_thresh: float = 0.10,
) -> Image.Image:
    """把 crop 內「夠亮且接近中性灰」的掃描底色提亮成純白。

    條件：亮度 > val_thresh **且** 飽和度 < sat_thresh。
    → 只清掉淡灰 / 微黃的掃描背景；任何有彩度的內容都保留，絕不去彩。
    白色卡面（飽和度≈0、亮度≈1）本就會被設成白（無視覺變化）。
    """
    rgb = np.asarray(to_rgb(crop), dtype=np.uint8).copy()
    lum = _luminance(rgb) / 255.0
    sat = _saturation(rgb)
    bg = (lum > val_thresh) & (sat < sat_thresh)
    rgb[bg] = (255, 255, 255)
    return Image.fromarray(rgb, mode="RGB")

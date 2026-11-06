"""騎縫章的印章來源：資產庫 / 上傳 / 系統產生。

三種來源最後都收斂成一份 PNG 位元組，交給 `seam_core` 去切 —— 切圖那一段不需要
知道章是哪來的。

系統產生的部分沿用「個資限用章」的作法（`pdf_stamp/restrict_render`）：
字型走 `font_catalog`（管理員上傳的字型會自動出現，`.ttc` 也會挑到繁中子字型），
畫在透明底的 PNG 上。
"""
from __future__ import annotations

import io
import math
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

#: 產生的章畫多大（像素）。畫大一點再縮到 PDF 上，邊緣才不會糊 ——
#: 騎縫章會被切成很細的片，解析度不夠一眼就看得出來。
_CANVAS = 720


def load_asset(asset_id: str) -> bytes:
    """從資產庫取印章圖。

    走 `asset_manager` 直接讀檔，不繞 HTTP —— 這是伺服器內部呼叫。
    （前端要顯示縮圖時才走 login-gated 的 `/assets/{id}/file`。）
    """
    from ...core.asset_manager import asset_manager
    a = asset_manager.get(asset_id)
    # 只收印章（issue #54）：畫面上只列印章，而簽名要有「用印與簽名」權限才能用
    # （`asset_access`）—— 只有騎縫章權限的人送簽名的編號進來，等於繞過那條規則。
    if not a or a.type != "stamp":
        raise ValueError("找不到這個印章資產")
    from ...core import asset_access
    if asset_access.is_controlled_asset(a):
        # 用印申請：僅限核准案件使用的章，騎縫章（含 API）不能直接用
        raise ValueError(asset_access.CONTROLLED_MESSAGE)
    path = asset_manager.file_path(a)
    if not path or not path.exists():
        raise ValueError("印章資產的檔案不存在")
    return path.read_bytes()


#: 印章圖的處理上限。章蓋出來最大也就幾公分，2000 px 已經遠超過 300dpi 所需，
#: 再大只是讓伺服器多做白工。
_MAX_STAMP_PX = 2000

#: 解壓後的像素上限。PIL 預設的 178M 只會發**警告**不會擋，程式照跑。
_MAX_DECODED_PIXELS = 40_000_000


def normalize_upload(data: bytes) -> bytes:
    """上傳的圖統一成 RGBA PNG。

    **白底要去掉** —— 使用者常常是拍照或掃描的章，帶著白底貼上去會蓋掉內文。
    這裡只做保守的處理：接近純白的像素轉成透明。

    **兩件事必須擋住，否則一個小檔案就能把整台伺服器停掉**（v1.14.31 對抗式
    驗證實測）：第一版用純 Python 逐像素迴圈又沒有尺寸上限，一張 116 KB 的
    6000×6000 全白 PNG 要跑 **27 秒**，而端點是 `async def` —— 這 27 秒卡在
    事件迴圈執行緒上，期間**全站**每個請求都在等（實測 `/healthz` 從 74 ms
    變成 19.6 秒）。壓縮率讓成本極度不對稱：上傳成本 116 KB，伺服器成本 27 秒，
    反覆送幾個就是零成本的服務阻斷。

    所以：①先擋掉解析度離譜的 ②縮到 `_MAX_STAMP_PX`（章不需要更大）
    ③白底判定改用 numpy 向量運算，不逐像素跑 Python 迴圈。
    """
    img = Image.open(io.BytesIO(data))
    w, h = img.size
    if w * h > _MAX_DECODED_PIXELS:
        raise ValueError("印章圖的解析度太高")
    img = img.convert("RGBA")
    if max(img.size) > _MAX_STAMP_PX:
        img.thumbnail((_MAX_STAMP_PX, _MAX_STAMP_PX), Image.LANCZOS)

    import numpy as np

    arr = np.array(img)
    # 只吃「很白」的（>= 240），淺灰的印泥不能碰
    white = ((arr[:, :, 0] >= 240) & (arr[:, :, 1] >= 240)
             & (arr[:, :, 2] >= 240))
    arr[:, :, 3] = np.where(white, 0, arr[:, :, 3])
    buf = io.BytesIO()
    Image.fromarray(arr, "RGBA").save(buf, format="PNG")
    return buf.getvalue()


def _font(style: str, size_px: int, bold: bool = False):
    """走 `font_catalog` 拿字型（`.ttc` 會挑繁中子字型）。"""
    try:
        from ...core import font_catalog
        best = font_catalog.best_cjk_path(
            style="sans" if style == "hei" else "serif", cjk="traditional")
        if best:
            path, idx = best[0], (best[1] if len(best) > 1 else 0)
            return ImageFont.truetype(str(path), size_px, index=idx)
    except Exception:  # noqa: BLE001
        pass
    try:
        return ImageFont.load_default(size=size_px)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _fit_text(draw, text: str, style: str, box: int, bold: bool) -> ImageFont:
    """把字級縮到塞得進 box。"""
    size = box
    while size > 8:
        f = _font(style, size, bold)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if (r - l) <= box and (b - t) <= box:
            return f
        size = int(size * 0.9)
    return _font(style, 8, bold)


#: 一個字在章面上佔多大（px）。**這是固定的** —— 字多了要把章加寬，
#: 不是把字縮小；縮到看不清楚的章等於沒蓋。
_CELL = 150
_MAX_CHARS = 30


def generate(text: str, *, shape: str = "circle", color: str = "#c81414",
             style: str = "hei", border_px: int = 14,
             double_ring: bool = True) -> bytes:
    """系統產生印章。

    `shape`：`circle`（圓章，公司章的樣子）/ `square`（方章）/ `rect`（長方，橫式）。

    **字級固定，版面隨字數長**：實務上很多人蓋的是公司全名，
    硬塞進固定畫布只能一直縮字，最後糊成一團。所以
    長方章**加寬**、圓 / 方章**分行**（直行、由右至左，傳統印章的讀序）。
    """
    txt = (text or "騎縫章").strip()[:_MAX_CHARS] or "騎縫章"
    if shape == "rect":
        return _gen_rect(txt, _hex(color), style, border_px, double_ring)
    return _gen_boxed(txt, _hex(color), style, border_px, double_ring, shape)


def _gen_rect(txt: str, rgb, style: str, border_px: int,
              double_ring: bool) -> bytes:
    """長方橫式：字級固定，**寬度隨字數成長**。"""
    f = _font(style, _CELL, True)
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    l, t, r, b = probe.textbbox((0, 0), txt, font=f)
    tw, th = r - l, b - t
    padx = int(_CELL * 0.42) + border_px
    pady = int(_CELL * 0.34) + border_px
    w = tw + padx * 2
    h = th + pady * 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle((border_px // 2, border_px // 2, w - border_px // 2 - 1,
                 h - border_px // 2 - 1), outline=rgb + (255,), width=border_px)
    if double_ring:
        g = border_px * 2
        d.rectangle((g, g, w - g - 1, h - g - 1), outline=rgb + (255,),
                    width=max(2, border_px // 3))
    d.text(((w - tw) / 2 - l, (h - th) / 2 - t), txt, font=f, fill=rgb + (255,))
    return _png(img)


def _gen_boxed(txt: str, rgb, style: str, border_px: int, double_ring: bool,
               shape: str) -> bytes:
    """圓章 / 方章：外形是等比的，所以**字多時分行**而不是縮字。

    傳統印章的讀序是**直行、由右至左**（右上 → 右下 → 左上 → 左下），
    字多時就是多幾行。
    """
    n_ch = len(txt)
    # 讓行列盡量接近正方形；圓章可用面積較小，同字數要多一行才擠得下
    cols = max(1, math.ceil(math.sqrt(n_ch)))
    rows = max(1, math.ceil(n_ch / cols))
    fill_ratio = 0.60 if shape == "circle" else 0.74
    inner_w, inner_h = cols * _CELL, rows * _CELL
    n = int(max(inner_w, inner_h) / fill_ratio)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = border_px
    if shape == "circle":
        d.ellipse((pad, pad, n - pad, n - pad), outline=rgb + (255,), width=border_px)
        if double_ring:
            g = border_px * 3
            d.ellipse((g, g, n - g, n - g), outline=rgb + (255,),
                      width=max(2, border_px // 3))
    else:
        d.rectangle((pad, pad, n - pad, n - pad), outline=rgb + (255,), width=border_px)
        if double_ring:
            g = border_px * 3
            d.rectangle((g, g, n - g, n - g), outline=rgb + (255,),
                        width=max(2, border_px // 3))
    f = _font(style, int(_CELL * 0.86), True)
    x0 = (n - inner_w) / 2
    y0 = (n - inner_h) / 2
    for i, ch in enumerate(txt):
        col = cols - 1 - (i // rows)          # 由右至左
        row = i % rows                        # 每行由上至下
        cl, ct, cr, cb = d.textbbox((0, 0), ch, font=f)
        px = x0 + col * _CELL + (_CELL - (cr - cl)) / 2 - cl
        py = y0 + row * _CELL + (_CELL - (cb - ct)) / 2 - ct
        d.text((px, py), ch, font=f, fill=rgb + (255,))
    return _png(img)


def _png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_grid(d, txt: str, rgb, inner: int, n: int, style: str) -> None:
    """2~4 字排成田字（傳統印章的排法）。"""
    k = 2 if len(txt) > 1 else 1
    cell = inner // k
    f = _font(style, int(cell * 0.86), True)
    x0 = (n - inner) / 2
    y0 = (n - inner) / 2
    # 印章的讀序是**右上 → 右下 → 左上 → 左下**（直行、由右至左）
    order = [(1, 0), (1, 1), (0, 0), (0, 1)] if k == 2 else [(0, 0)]
    for ch, (cx, cy) in zip(txt, order):
        l, t, r, b = d.textbbox((0, 0), ch, font=f)
        px = x0 + cx * cell + (cell - (r - l)) / 2 - l
        py = y0 + cy * cell + (cell - (b - t)) / 2 - t
        d.text((px, py), ch, font=f, fill=rgb + (255,))


def _hex(c: str) -> tuple[int, int, int]:
    c = (c or "").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return (200, 20, 20)
    try:
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except ValueError:
        return (200, 20, 20)


def apply_opacity(png: bytes, opacity: float) -> bytes:
    """整體透明度（蓋在內文上時不要完全擋住字）。"""
    if opacity >= 0.999:
        return png
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    a = img.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, opacity))))
    img.putalpha(a)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

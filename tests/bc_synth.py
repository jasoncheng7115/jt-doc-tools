"""名片辨識測試用的合成資料：畫名片、擺到桌面背景（旋轉、透視、一頁多張），回傳標準答案的四角。

**資料全部是杜撰的**（姓名、公司、電話、Email 用範例網域），不可以換成真實名片的內容。
真實樣本（維基共享資源）只放在開發機的 `temp_pdfs/business_cards/`，不進 git。
"""
from __future__ import annotations

import io
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

CJK_FONT = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
LATIN_FONT = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")

CARD_W, CARD_H = 900, 540          # 90×54 mm 的比例


def _font(size: int, cjk: bool):
    try:
        if cjk and CJK_FONT.exists():
            return ImageFont.truetype(str(CJK_FONT), size, index=3)       # Noto Sans CJK TC
        if LATIN_FONT.exists():
            return ImageFont.truetype(str(LATIN_FONT), size)
    except OSError:
        pass
    return ImageFont.load_default()


def has_cjk_font() -> bool:
    return CJK_FONT.exists()


def card(lines: list[tuple[str, int]] | None = None, *, bg=(250, 250, 248), fg=(30, 30, 30), accent=(37, 99, 235),
         portrait: bool = False) -> Image.Image:
    """一張名片：上方色帶＋幾行字。`lines`＝[(文字, 字級)]。"""
    w, h = (CARD_H, CARD_W) if portrait else (CARD_W, CARD_H)
    im = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w, int(h * 0.08)], fill=accent)
    lines = lines or [("Kevin Sample", 56), ("Sales Manager", 30), ("Example Tech Co., Ltd.", 30),
                      ("Tel: 02-2345-6789 ext. 302", 26), ("Mobile: 0912-345-678", 26), ("kevin@example.com", 26)]
    y = int(h * 0.15)
    for text, size in lines:
        cjk = any("一" <= ch <= "鿿" for ch in text)
        d.text((int(w * 0.07), y), text, fill=fg, font=_font(size, cjk))
        y += int(size * 1.45)
    return im


def background(w: int, h: int, *, color=(70, 60, 50), noise: int = 12, seed: int = 1) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = np.ones((h, w, 3), np.float32) * np.array(color, np.float32)
    base += rng.normal(0, noise, (h, w, 1)).astype(np.float32)
    # 一點木紋 / 光線變化
    yy = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    base *= (0.9 + 0.2 * yy)
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def _perspective_coeffs(src, dst):
    """PIL `Image.transform(PERSPECTIVE)` 要的係數（把輸出座標對回輸入）。"""
    a = []
    for (x, y), (u, v) in zip(dst, src):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    A = np.array(a, np.float64)
    B = np.array(src, np.float64).reshape(8)
    return np.linalg.solve(A, B).tolist()


def place(scene: Image.Image, cardimg: Image.Image, quad: list[tuple[float, float]], shadow: bool = True) -> None:
    """把卡貼到場景的四角位置（左上、右上、右下、左下），可以是任意四邊形（透視）。"""
    w, h = cardimg.size
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    coeffs = _perspective_coeffs(src, quad)
    warped = cardimg.convert("RGBA").transform(scene.size, Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    mask = Image.new("L", (w, h), 255).transform(scene.size, Image.PERSPECTIVE, coeffs, Image.BILINEAR)
    if shadow:
        sh = Image.new("RGBA", scene.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).polygon([(x + 6, y + 8) for x, y in quad], fill=(0, 0, 0, 90))
        sh = sh.filter(ImageFilter.GaussianBlur(6))
        scene.paste(sh, (0, 0), sh)
    scene.paste(warped, (0, 0), mask)


def rect_quad(cx: float, cy: float, w: float, h: float, deg: float = 0.0, persp: float = 0.0):
    """中心、寬高、旋轉角（度）、透視量（0～0.1：上緣縮短的比例）→ 四角。"""
    hw, hh = w / 2, h / 2
    pts = [(-hw * (1 - persp), -hh), (hw * (1 - persp), -hh), (hw, hh), (-hw, hh)]
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in pts]


def scene_multi(n: int = 6, *, portrait_idx=(1, 4), seed: int = 3, size=(3000, 2200), touching: bool = False):
    """一頁 n 張（預設兩張直式），回（影像, 標準答案四角清單：左上起、上到下左到右）。"""
    rng = random.Random(seed)
    W, H = size
    scene = background(W, H, seed=seed)
    cols = 3
    rows = math.ceil(n / cols)
    cw, ch = W / cols, H / rows
    truth = []
    for i in range(n):
        r, c = divmod(i, cols)
        portrait = i in portrait_idx
        cwid, chei = (540, 900) if portrait else (900, 540)
        if touching:
            cx = W / 2 - cwid / 2 + c * cwid if n == 2 else (c + 0.5) * cw
        else:
            cx = (c + 0.5) * cw + rng.uniform(-40, 40)
        cy = (r + 0.5) * ch + rng.uniform(-30, 30)
        deg = 0 if touching else rng.uniform(-8, 8)
        q = rect_quad(cx, cy, cwid, chei, deg)
        place(scene, card(portrait=portrait, accent=[(37, 99, 235), (16, 185, 129), (220, 38, 38)][i % 3]), q,
              shadow=not touching)
        truth.append(q)
    return scene, truth


def scene_touching_pair(seed: int = 5):
    """兩張卡左右緊貼（中間只有一條細縫），回（影像, 兩個四角）。"""
    W, H = 2600, 1400
    scene = background(W, H, seed=seed)
    left = rect_quad(W / 2 - 451, H / 2, 900, 540)
    right = rect_quad(W / 2 + 451, H / 2, 900, 540)
    place(scene, card(accent=(220, 38, 38)), left, shadow=False)
    place(scene, card(accent=(37, 99, 235)), right, shadow=False)
    # 兩張卡之間那條縫（實拍是陰影 / 紙邊）
    ImageDraw.Draw(scene).line([(W / 2, H / 2 - 270), (W / 2, H / 2 + 270)], fill=(90, 90, 90), width=2)
    return scene, [left, right]


def scene_single(deg: float = 7.0, persp: float = 0.06, seed: int = 7, white_on_white: bool = False):
    W, H = 2000, 1500
    if white_on_white:
        scene = background(W, H, color=(236, 236, 232), noise=4, seed=seed)
    else:
        scene = background(W, H, seed=seed)
    q = rect_quad(W / 2, H / 2, 1300, 780, deg, persp)
    place(scene, card(), q, shadow=True)
    return scene, q


def jpeg(im: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def corner_error(found, truth) -> float:
    """找到的四角跟標準答案的最大距離，除以卡片短邊（0.03＝差 3%）。"""
    f = np.asarray(found, np.float64)
    t = np.asarray(truth, np.float64)
    # 角的順序：兩邊都排成左上起；為了不受起點影響，取四種旋轉中最小的
    best = min(np.max(np.linalg.norm(np.roll(f, k, axis=0) - t, axis=1)) for k in range(4))
    tw = np.linalg.norm(t[1] - t[0])
    th = np.linalg.norm(t[3] - t[0])
    return float(best / min(tw, th))

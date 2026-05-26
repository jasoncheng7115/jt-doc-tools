"""掃描拼合 — 內容偵測 + 背景淨白 單元測試。

合成「白底 A4 + 不同位置的彩色方塊」當測試掃描，驗證：
  - 偵測到的區塊位置 / 數量正確
  - 裁出的 crop 保留原彩色（飽和度不掉）
  - 背景淨白只清淡灰底、不動有彩度的內容
  - 空白頁回 []
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from app.tools.scan_merge.detector import (
    Region,
    crop_card,
    crop_region,
    detect_regions,
    whiten_background,
)


def _blank(w=600, h=850, color=(255, 255, 255)) -> Image.Image:
    return Image.new("RGB", (w, h), color)


def _paste_block(img: Image.Image, box, color) -> None:
    x0, y0, x1, y1 = box
    block = Image.new("RGB", (x1 - x0, y1 - y0), color)
    img.paste(block, (x0, y0))


def test_detect_single_block_top_left():
    img = _blank()
    _paste_block(img, (50, 40, 250, 180), (200, 30, 30))  # red card top-left
    regions = detect_regions(img)
    assert len(regions) == 1
    r = regions[0]
    # bbox 應大致涵蓋方塊（含 padding，所以略大但中心對）
    cx = (r.x0 + r.x1) / 2
    cy = (r.y0 + r.y1) / 2
    assert 130 < cx < 170
    assert 90 < cy < 130
    # fractional 在合理範圍
    assert 0.0 <= r.fx < 0.2
    assert 0.0 <= r.fy < 0.2


def test_detect_two_blocks_different_positions():
    img = _blank()
    _paste_block(img, (40, 40, 240, 180), (20, 20, 200))     # blue top-left
    _paste_block(img, (340, 600, 560, 780), (20, 160, 40))   # green bottom-right
    regions = detect_regions(img)
    assert len(regions) == 2
    # 排序後第一塊在上方
    assert regions[0].y0 < regions[1].y0
    # 第二塊偏右下
    assert regions[1].fx > 0.4
    assert regions[1].fy > 0.5


def test_blank_page_yields_no_regions():
    assert detect_regions(_blank()) == []
    # 極淡灰底（掃描雜訊級）也不該被當內容
    assert detect_regions(_blank(color=(250, 250, 250))) == []


def test_crop_preserves_color_not_black_and_white():
    img = _blank()
    _paste_block(img, (100, 100, 300, 260), (210, 40, 40))  # saturated red
    regions = detect_regions(img)
    assert regions
    crop = crop_region(img, regions[0])
    arr = np.asarray(crop, dtype=np.uint8)
    # crop 內必須仍有高飽和度的紅（沒被轉灰階 / 黑白）
    mx = arr.max(axis=2).astype(float)
    mn = arr.min(axis=2).astype(float)
    sat = np.where(mx > 0, (mx - mn) / mx, 0)
    assert sat.max() > 0.5, "crop 應保留彩色，飽和度不可掉到接近 0"
    # 確認紅通道明顯大於綠藍（仍是紅色）
    red_pixels = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 90) & (arr[:, :, 2] < 90)
    assert red_pixels.sum() > 1000


def test_whiten_clears_gray_background_but_keeps_color():
    # crop：淡灰底 + 中央一塊彩色
    crop = Image.new("RGB", (200, 150), (244, 244, 244))  # 掃描淡灰底
    _paste_block(crop, (60, 40, 140, 110), (30, 120, 220))  # 藍色內容
    out = whiten_background(crop)
    arr = np.asarray(out, dtype=np.uint8)
    # 四角（原淡灰底）應被提亮成純白
    for (x, y) in [(2, 2), (197, 2), (2, 147), (197, 147)]:
        assert tuple(arr[y, x]) == (255, 255, 255), f"角落 ({x},{y}) 應淨白"
    # 中央藍色內容必須原樣保留（不被去彩）
    cx, cy = 100, 75
    px = arr[cy, cx]
    assert px[2] > 150 and px[0] < 90, "藍色內容不可被淨白洗掉"


def test_crop_card_is_rgba_with_transparent_padding():
    # 卡片不貼齊頁緣 → 外圍應有透明區，卡片本身不透明且保留彩色
    img = _blank(600, 850)
    _paste_block(img, (120, 140, 360, 320), (40, 90, 200))  # blue card
    regions = detect_regions(img)
    assert regions
    rgba = crop_card(img, regions[0], whiten=False)
    assert rgba.mode == "RGBA"
    arr = np.asarray(rgba, dtype=np.uint8)
    alpha = arr[:, :, 3]
    # 卡片區（中心）不透明
    h, w = alpha.shape
    assert alpha[h // 2, w // 2] == 255
    # 至少有一些不透明像素（卡片）
    assert (alpha == 255).sum() > 1000
    # 中心仍是藍色（彩色保留）
    cx = arr[h // 2, w // 2]
    assert cx[2] > 150 and cx[0] < 90


def test_crop_card_whiten_keeps_card_opaque_color():
    img = _blank(600, 850)
    # 卡片含淡灰底 + 彩色塊
    _paste_block(img, (100, 120, 380, 300), (245, 245, 245))  # 卡片淡灰底
    _paste_block(img, (140, 160, 300, 260), (200, 40, 40))    # 紅色內容
    regions = detect_regions(img)
    assert regions
    rgba = crop_card(img, regions[0], whiten=True)
    arr = np.asarray(rgba, dtype=np.uint8)
    # 紅色內容仍在（不去彩）
    red = (arr[:, :, 0] > 150) & (arr[:, :, 1] < 90) & (arr[:, :, 2] < 90) & (arr[:, :, 3] == 255)
    assert red.sum() > 500


def test_card_body_and_photo_merge_into_one_region():
    """健保卡有卡身（文字 / logo）+ 右側大頭照，中間留白縫。應視為「一張卡」
    而非兩塊。健保卡實掃案例 v1.11.12 修。"""
    img = _blank(700, 1000)
    # 模擬卡身（左、稍寬）
    _paste_block(img, (80, 320, 320, 480), (40, 110, 50))   # 綠色卡身
    # 模擬右側大頭照（Y 範圍與卡身大致重疊，X 間距小）
    _paste_block(img, (350, 330, 470, 470), (200, 150, 120))  # 棕色照片
    regions = detect_regions(img)
    assert len(regions) == 1, f"應合成一張，實得 {len(regions)} 塊"
    r = regions[0]
    # 合併後外框涵蓋兩塊
    assert r.x0 <= 90 and r.x1 >= 460
    assert r.y0 <= 330 and r.y1 >= 470


def test_two_separate_cards_far_apart_not_merged():
    """兩張完全分開（上下相距 > 卡片高度）的卡片不可被合併。"""
    img = _blank(700, 1200)
    _paste_block(img, (80, 80, 380, 220), (40, 110, 50))      # 上卡
    _paste_block(img, (80, 800, 380, 940), (40, 110, 200))    # 下卡（相隔 580px）
    regions = detect_regions(img)
    assert len(regions) == 2


def test_whiten_does_not_desaturate_light_colors():
    # 全幅淡彩（淡藍，飽和度足夠）不該被當背景清掉
    light_blue = Image.new("RGB", (80, 80), (190, 210, 245))
    out = np.asarray(whiten_background(light_blue), dtype=np.uint8)
    # 仍應帶藍（B 明顯 > R），不可變成純白
    assert out[40, 40][2] >= out[40, 40][0]
    assert not np.all(out == 255)

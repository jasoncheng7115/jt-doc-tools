"""名片辨識：切卡（四角框偵測、拉正、使用者框位檢查）。

合成資料（`tests/bc_synth.py`）有標準答案；真實樣本（維基共享資源，真人資料）只在開發機的
`temp_pdfs/business_cards/` 用評估腳本逐張目視，不進 git。判準：角點誤差（除以卡片短邊）。
"""
from __future__ import annotations

import math

import pytest
from PIL import Image

from app.tools.business_card_reader import detector as D
from tests import bc_synth as S


def test_single_card_with_rotation_and_perspective():
    im, truth = S.scene_single(deg=7, persp=0.06)
    card, multi = D.detect_single(im)
    assert "whole" not in card.flags and not multi
    assert S.corner_error(card.quad, truth) < 0.03


def test_six_cards_including_portrait_ones():
    im, truth = S.scene_multi(6, portrait_idx=(1, 4))
    found = D.detect_cards(im)
    assert len(found) == 6, "一頁 6 張（其中 2 張直式）要全部找到"
    for f, t in zip(found, truth):          # 排序：上到下、左到右，跟標準答案同順序
        assert S.corner_error(f.quad, t) < 0.04
    # 直式的那兩張，框的高 > 寬
    for i in (1, 4):
        w, h = D.quad_size(found[i].quad)
        assert h > w


def test_two_touching_cards_are_split_along_the_seam():
    im, truth = S.scene_touching_pair()
    found = D.detect_cards(im)
    assert len(found) == 2, "兩張緊貼的卡要沿交界拆開"
    assert all("split" in f.flags for f in found)
    for f, t in zip(found, truth):
        assert S.corner_error(f.quad, t) < 0.03


def test_colour_block_inside_a_card_is_not_a_seam():
    """卡面的色塊分界（左三分之一深藍）也是一條貫穿的直線，但那是「一階跳變」不是兩張卡的交界：
    兩張這樣的卡相貼時，要切在真正的交界（細縫），不可以切在色塊分界（實拍抓到過）。"""
    from PIL import ImageDraw
    W, H = 2600, 1400
    scene = S.background(W, H, seed=11)
    quads = [S.rect_quad(W / 2 - 451, H / 2, 900, 540), S.rect_quad(W / 2 + 451, H / 2, 900, 540)]
    for q in quads:
        im = S.card(accent=(37, 99, 235))
        ImageDraw.Draw(im).rectangle([0, 0, im.width // 3, im.height], fill=(25, 70, 190))
        S.place(scene, im, q, shadow=False)
    ImageDraw.Draw(scene).line([(W / 2, H / 2 - 270), (W / 2, H / 2 + 270)], fill=(90, 90, 90), width=2)
    found = D.detect_cards(scene)
    assert len(found) == 2
    for f, t in zip(found, quads):
        assert S.corner_error(f.quad, t) < 0.03


def test_no_cards_is_an_empty_list_not_a_success():
    assert D.detect_cards(S.background(1600, 1200)) == []
    card, multi = D.detect_single(S.background(1600, 1200))
    assert "whole" in card.flags, "單卡模式找不到邊界就用整張圖（不切到字）"


def test_card_filling_the_frame_uses_the_whole_image():
    im = S.card()
    card, _ = D.detect_single(im)
    w, h = im.size
    # 整張圖、或幾乎整張：不可以裁掉一半
    cw, ch = D.quad_size(card.quad)
    assert cw >= w * 0.9 and ch >= h * 0.9


def test_warp_produces_upright_card_with_card_ratio():
    im, truth = S.scene_single(deg=12, persp=0.05)
    out = D.warp_card(im, truth)
    assert 1.5 < out.width / out.height < 1.8
    assert max(out.size) == 1400
    assert D.warp_card(im, truth, rotate=90).height > D.warp_card(im, truth, rotate=90).width


@pytest.mark.parametrize("quad", [
    [[0, 0], [10, 0], [10, 10]],                                   # 三個點
    [[0, 0], [5, 0], [5, 5], [0, 5]],                              # 太小
    [[0, 0], [float("nan"), 0], [100, 100], [0, 100]],             # NaN
    [[0, 0], [float("inf"), 0], [100, 100], [0, 100]],             # 無限大
    [[0, 0], "x", [100, 100], [0, 100]],                           # 點的格式不對
    None,
])
def test_user_quads_are_validated(quad):
    with pytest.raises((ValueError, TypeError)):
        D.clamp_quad(quad, 1000, 800)


def test_user_quads_are_clamped_and_ordered():
    q = D.clamp_quad([[900, 700], [-50, 700], [-50, -40], [900, -40]], 800, 600)
    assert q[0] == [0.0, 0.0] and q[2] == [800.0, 600.0], "夾在圖內、排成左上起"


def test_sorting_is_rows_then_columns():
    cards = [D.Card(quad=S.rect_quad(cx, cy, 200, 120)) for cx, cy in [(800, 120), (200, 130), (500, 500), (210, 480)]]
    order = [tuple(round(x) for x in D.order_quad(c.quad).mean(axis=0)) for c in D.sort_cards(cards)]
    assert order == [(200, 130), (800, 120), (210, 480), (500, 500)]


def test_quality_hints():
    im = S.card()
    assert D.blur_score(im) > D.blur_score(im.resize((90, 54)).resize(im.size))
    glare = im.copy()
    from PIL import ImageDraw
    ImageDraw.Draw(glare).ellipse([300, 150, 700, 450], fill=(255, 255, 255))
    assert D.glare_ratio(glare) > D.glare_ratio(im)


def _band_scene(band: float, accents, seed: int = 3):
    """六張卡放深色木紋桌上，上緣色帶佔卡高 `band`。回（影像, 標準答案）。"""
    import random
    from PIL import ImageDraw
    rng = random.Random(seed)
    W, H = 3000, 2200
    sc = S.background(W, H, seed=seed)
    truth = []
    for i in range(6):
        r, c = divmod(i, 3)
        portrait = i in (1, 4)
        cw, ch = (540, 900) if portrait else (900, 540)
        q = S.rect_quad((c + 0.5) * W / 3 + rng.uniform(-40, 40), (r + 0.5) * H / 2 + rng.uniform(-30, 30), cw, ch,
                        rng.uniform(-8, 8))
        im = S.card(portrait=portrait, accent=accents[i % len(accents)])
        ImageDraw.Draw(im).rectangle([0, 0, im.width, int(im.height * band)], fill=accents[i % len(accents)])
        S.place(sc, im, q)
        truth.append(q)
    return sc, truth


@pytest.mark.parametrize("band", [0.08, 0.18])
def test_dark_top_band_on_a_dark_table_is_inside_the_frame(band):
    """深紅 / 深青 / 深藍的上緣色帶放在木紋桌上：原本框只框到白色那塊（色帶被當成桌面），角落誤差 7%～30%。"""
    sc, truth = _band_scene(band, [(150, 25, 25), (10, 80, 80), (25, 25, 70)])
    found = D.sort_cards(D.detect_cards(sc))
    assert len(found) == 6
    errs = [min(S.corner_error(f.quad, t) for f in found) for t in truth]
    assert max(errs) < 0.04, errs


def test_band_extension_ignores_shadows_and_plain_edges():
    """影子（比較暗的桌面、色相一樣）不是色帶：一般的卡框位不變。"""
    import numpy as np
    sc, truth = _band_scene(0.08, [(37, 99, 235), (16, 185, 129)])
    work, scale = D._work(sc)
    for f in D.detect_cards(sc):
        q = np.asarray(f.quad, np.float32) * scale
        assert np.abs(D._extend_bands(work, q) - D.order_quad(q)).max() < 1.5

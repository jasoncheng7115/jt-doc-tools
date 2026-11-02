"""掃描拼合「自動排列」：互不重疊、照上傳順序、放不下等比例縮小。

使用者 2026-11-01：預設依原始位置擺，但兩份掃描常落在同一個角落而疊在一起 ——
拼合前要能選「自動排列」，自動找空位排好。

判準落在幾何上（任兩塊不重疊、都在紙張留白之內、順序與大小），
不是「有沒有呼叫那個函式」。
"""
from __future__ import annotations

import importlib
import io
import itertools
import math

import fitz
import pytest
from PIL import Image

from app.tools.scan_merge.layout import (
    A4_H_PT, A4_W_PT, GAP_PT, MARGIN_PT, MAX_ITEMS, LayoutError, auto_layout,
)

R = importlib.import_module("app.tools.scan_merge.router")


def _pt(it):
    return (it["x"] * A4_W_PT, it["y"] * A4_H_PT,
            (it["x"] + it["w"]) * A4_W_PT, (it["y"] + it["h"]) * A4_H_PT)


def _assert_valid(out):
    eps = 0.05   # 四捨五入到小數 5 位的誤差（點）
    for it in out:
        x0, y0, x1, y1 = _pt(it)
        assert x0 >= MARGIN_PT - eps and y0 >= MARGIN_PT - eps, it
        assert x1 <= A4_W_PT - MARGIN_PT + eps and y1 <= A4_H_PT - MARGIN_PT + eps, it
    for a, b in itertools.combinations(out, 2):
        ax0, ay0, ax1, ay1 = _pt(a)
        bx0, by0, bx1, by1 = _pt(b)
        overlap = not (ax1 <= bx0 + eps or bx1 <= ax0 + eps or ay1 <= by0 + eps or by1 <= ay0 + eps)
        assert not overlap, f"{a} 與 {b} 重疊"


def test_empty():
    assert auto_layout([]) == []


def test_two_id_cards_sit_side_by_side_in_upload_order_at_original_size():
    # 兩張證件（約 86 × 54 mm），原本都在左上角 —— 依原始位置會完全疊在一起
    card = {"w": 243 / A4_W_PT, "h": 153 / A4_H_PT}
    out = auto_layout([{"id": "front", **card}, {"id": "back", **card}])
    _assert_valid(out)
    assert [o["id"] for o in out] == ["front", "back"]
    # 原本的大小放得下就不縮放
    for o in out:
        assert math.isclose(o["w"], card["w"], abs_tol=1e-4)
        assert math.isclose(o["h"], card["h"], abs_tol=1e-4)
    # A4 放得下兩張並排：正面在左、反面在右，同一列，整列水平置中
    front, back = out
    assert front["x"] < back["x"] and math.isclose(front["y"], back["y"], abs_tol=1e-4)
    left = front["x"] * A4_W_PT
    right = A4_W_PT - (back["x"] + back["w"]) * A4_W_PT
    assert math.isclose(left, right, abs_tol=0.1)


def test_wide_items_go_one_per_row_top_to_bottom():
    wide = {"w": 0.6, "h": 0.2}
    out = auto_layout([{"id": str(i), **wide} for i in range(3)])
    _assert_valid(out)
    ys = [o["y"] for o in out]
    assert ys == sorted(ys) and len(set(ys)) == 3
    # 間距是 GAP_PT
    gap = (out[1]["y"] - (out[0]["y"] + out[0]["h"])) * A4_H_PT
    assert math.isclose(gap, GAP_PT, abs_tol=0.1)


def test_too_many_items_shrink_uniformly_and_still_fit():
    items = [{"id": str(i), "w": 0.5, "h": 0.3} for i in range(30)]
    out = auto_layout(items)
    _assert_valid(out)
    scales = {round(o["w"] / 0.5, 3) for o in out}
    assert len(scales) == 1, f"要等比例一起縮：{scales}"
    assert next(iter(scales)) < 1
    assert [o["id"] for o in out] == [str(i) for i in range(30)]


def test_item_wider_than_page_is_shrunk_to_the_margins():
    out = auto_layout([{"id": "a", "w": 1.0, "h": 0.4}])
    _assert_valid(out)
    assert out[0]["w"] * A4_W_PT <= A4_W_PT - 2 * MARGIN_PT + 0.05
    # 等比例：寬高比不變
    assert math.isclose(out[0]["h"] / out[0]["w"], 0.4 / 1.0, rel_tol=1e-3)


@pytest.mark.parametrize("bad", [
    [{"id": "a", "w": -0.1, "h": 0.2}],
    [{"id": "a", "w": 0, "h": 0.2}],
    [{"id": "a", "w": float("nan"), "h": 0.2}],
    [{"id": "a", "w": "x", "h": 0.2}],
    [{"id": "a"}],
    ["not-a-dict"],
    "not-a-list",
    [{"id": str(i), "w": 0.1, "h": 0.1} for i in range(MAX_ITEMS + 1)],
])
def test_invalid_input_raises(bad):
    with pytest.raises(LayoutError):
        auto_layout(bad)


# ----- 端點 ----------------------------------------------------------------

def _hex(n):
    return f"{n:032x}"


def test_layout_endpoint_returns_non_overlapping_positions(client, auth_off):
    card = {"w": 0.41, "h": 0.18}
    r = client.post("/tools/scan-merge/layout",
                    json={"items": [{"id": _hex(1), **card}, {"id": _hex(2), **card}]})
    assert r.status_code == 200, r.text
    out = r.json()["items"]
    assert [o["id"] for o in out] == [_hex(1), _hex(2)]
    _assert_valid(out)


@pytest.mark.parametrize("body", [
    {"items": [{"id": "../etc", "w": 0.4, "h": 0.2}]},
    {"items": [{"id": _hex(1), "w": -1, "h": 0.2}]},
    {"items": "x"},
    {},
])
def test_layout_endpoint_rejects_bad_input(client, auth_off, body):
    r = client.post("/tools/scan-merge/layout", json=body)
    assert r.status_code == 400, r.text


def _scan_png(box) -> bytes:
    img = Image.new("RGB", (600, 850), (255, 255, 255))
    x0, y0, x1, y1 = box
    img.paste(Image.new("RGB", (x1 - x0, y1 - y0), (200, 30, 30)), (x0, y0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _image_rects(pdf: bytes):
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        return [fitz.Rect(i["bbox"]) for i in doc[0].get_image_info()]
    finally:
        doc.close()


def _api(client, layout=None):
    same = _scan_png((60, 50, 360, 230))      # 兩份掃描的內容落在同一個位置
    data = {"whiten": "true"}
    if layout:
        data["layout"] = layout
    return client.post("/tools/scan-merge/api/scan-merge", data=data, files=[
        ("files", ("front.png", same, "image/png")),
        ("files", ("back.png", same, "image/png")),
    ])


def test_api_default_keeps_original_positions_which_overlap(client, auth_off):
    r = _api(client)
    assert r.status_code == 200, r.text[:300]
    a, b = _image_rects(r.content)
    assert a.intersects(b), "預設依原始位置：同一位置的兩塊應該疊在一起"


def test_api_layout_auto_separates_them(client, auth_off):
    r = _api(client, "auto")
    assert r.status_code == 200, r.text[:300]
    rects = _image_rects(r.content)
    assert len(rects) == 2
    a, b = rects
    inter = a & b
    assert inter.is_empty or inter.get_area() < 1, f"自動排列不可以重疊：{a} {b}"


def test_api_layout_rejects_unknown_value(client, auth_off):
    r = _api(client, "random")
    assert r.status_code == 400

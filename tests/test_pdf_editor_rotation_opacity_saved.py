"""PDF 編輯器：畫面上轉過、調過透明度的物件，存檔後要跟畫面一樣。

2026-11-04 盤點時對過程式：
* 圖片的透明度前端有送、後端沒用 —— 存出來一律不透明；
* 圖片轉的不是 90 的倍數時 `insert_image(rotate=)` 直接丟例外，例外被吞掉 → **圖片整張不見**；
* 文字、矩形、橢圓、白底、螢光筆轉了之後前端根本沒送角度 → 存出來是正的。

判準落在產出上：文字的行方向、紙上哪裡有墨水、像素顏色。
"""
from __future__ import annotations

import io
import math
import os

import fitz
import pytest

os.environ.setdefault("JTDT_CSRF_DISABLE", "1")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


def _blank() -> bytes:
    d = fitz.open()
    d.new_page(width=595, height=842)
    out = d.tobytes()
    d.close()
    return out


def _upload(client) -> str:
    r = client.post("/tools/pdf-editor/load", files={"file": ("a.pdf", _blank(), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["upload_id"]


def _save(client, uid, objs) -> fitz.Document:
    r = client.post("/tools/pdf-editor/save", json={"upload_id": uid, "pages": [{"page": 0, "objects": objs}]})
    assert r.status_code == 200, r.text[:300]
    out = client.get(f"/tools/pdf-editor/download/{uid}")
    assert out.status_code == 200
    return fitz.open("pdf", out.content)


def _quad(x, y, w, h, angle):
    """以左上角為軸心順時針轉 angle 度（跟畫面上 Fabric 的正角度同方向；頁面座標 y 向下）。"""
    r = math.radians(angle)
    c, s = math.cos(r), math.sin(r)
    return [[x + dx * c - dy * s, y + dx * s + dy * c] for dx, dy in ((0, 0), (w, 0), (w, h), (0, h))]


def _px(doc, x, y, dpi=72):
    pix = doc[0].get_pixmap(dpi=dpi, alpha=False)
    k = dpi / 72
    return pix.pixel(int(x * k), int(y * k))


def _red_png() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (100, 50), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def _user_image(client, uid) -> str:
    r = client.post("/tools/pdf-editor/upload-image", data={"upload_id": uid},
                    files={"file": ("r.png", _red_png(), "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _text_dir(doc) -> tuple[float, float]:
    for b in doc[0].get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            if any(sp["text"].strip() for sp in ln["spans"]):
                return ln["dir"]
    raise AssertionError("產出裡找不到文字")


def _text(angle=0.0):
    o = {"id": "t1", "type": "text", "x": 200, "y": 200, "w": 200, "h": 30,
         "text": "ROTATE", "font": "helv", "font_size": 20, "color": "#000000"}
    if angle:
        o.update(angle=angle, quad=_quad(200, 200, 200, 30, angle))
    return o


def test_unrotated_text_runs_left_to_right(client):
    dx, dy = _text_dir(_save(client, _upload(client), [_text()]))
    assert dx == pytest.approx(1, abs=1e-3) and dy == pytest.approx(0, abs=1e-3)


@pytest.mark.parametrize("angle", [90, 30, 330])
def test_rotated_text_keeps_its_angle(client, angle):
    """畫面上順時針轉 angle 度 → 產出的行方向是 (cos, sin)（頁面座標 y 向下）。"""
    doc = _save(client, _upload(client), [_text(angle)])
    dx, dy = _text_dir(doc)
    r = math.radians(angle)
    assert (dx, dy) == (pytest.approx(math.cos(r), abs=1e-2), pytest.approx(math.sin(r), abs=1e-2)), (angle, dx, dy)
    assert "ROTATE" in doc[0].get_text()


def test_image_rotated_by_an_odd_angle_is_not_dropped(client):
    """30 度：原本 insert_image(rotate=30) 丟例外 → 圖片整張不見。"""
    uid = _upload(client)
    url = _user_image(client, uid)
    obj = {"id": "i1", "type": "image", "x": 200, "y": 300, "w": 200, "h": 100,
           "existing_src": url, "opacity": 1, "angle": 30, "quad": _quad(200, 300, 200, 100, 30)}
    doc = _save(client, uid, [obj])
    assert doc[0].get_images(), "轉 30 度的圖片沒有寫進 PDF"
    q = _quad(200, 300, 200, 100, 30)
    cx = sum(p[0] for p in q) / 4
    cy = sum(p[1] for p in q) / 4
    r, g, b = _px(doc, cx, cy)
    assert r > 200 and g < 60 and b < 60, ("圖片中心應該是紅的", (r, g, b))
    # 沒轉的話右上角 (395, 305) 在圖片裡；轉了 30 度之後那裡是空白
    assert _px(doc, 395, 305) == (255, 255, 255), "圖片沒有照角度轉"
    # 轉完之後的角：靠近右上那個頂點往中心收一點的地方要是紅的
    tx, ty = q[1]
    ix, iy = tx + (cx - tx) * 0.15, ty + (cy - ty) * 0.15
    r, g, b = _px(doc, ix, iy)
    assert r > 200 and g < 80, ("轉過的右上角附近應該是紅的", (r, g, b))


def test_image_opacity_is_applied(client):
    uid = _upload(client)
    url = _user_image(client, uid)
    doc = _save(client, uid, [{"id": "i1", "type": "image", "x": 100, "y": 100, "w": 200, "h": 100,
                               "existing_src": url, "opacity": 0.3}])
    r, g, b = _px(doc, 200, 150)
    assert r > 240 and 150 < g < 210 and 150 < b < 210, ("30% 的紅疊在白紙上應該是淡紅", (r, g, b))


def test_opaque_image_stays_opaque(client):
    uid = _upload(client)
    url = _user_image(client, uid)
    doc = _save(client, uid, [{"id": "i1", "type": "image", "x": 100, "y": 100, "w": 200, "h": 100,
                               "existing_src": url, "opacity": 1}])
    r, g, b = _px(doc, 200, 150)
    assert r > 240 and g < 20 and b < 20, (r, g, b)


@pytest.mark.parametrize("kind", ["rect", "ellipse", "whiteout", "highlight"])
def test_rotated_shapes_follow_the_quad(client, kind):
    """轉 45 度的方塊：中心有顏色；沒轉時才會蓋到的右上角是空白。"""
    uid = _upload(client)
    x, y, w, h, a = 250, 300, 100, 100, 45
    q = _quad(x, y, w, h, a)
    if kind == "whiteout":
        # 先在底下塗滿黑色（另一個物件），白底轉 45 度只蓋掉菱形那一塊
        objs = [{"id": "bg", "type": "rect", "x": 100, "y": 200, "w": 400, "h": 400,
                 "stroke": "#000000", "fill": "#000000", "stroke_width": 1}]
        objs.append({"id": "w", "type": "whiteout", "x": x, "y": y, "w": w, "h": h, "angle": a, "quad": q})
    elif kind == "highlight":
        objs = [{"id": "h", "type": "highlight", "x": x, "y": y, "w": w, "h": h,
                 "color": "#0000ff", "angle": a, "quad": q}]
    else:
        objs = [{"id": "s", "type": kind, "x": x, "y": y, "w": w, "h": h,
                 "stroke": "#0000ff", "fill": "#0000ff", "stroke_width": 1, "angle": a, "quad": q}]
    doc = _save(client, uid, objs)
    cx = sum(p[0] for p in q) / 4
    cy = sum(p[1] for p in q) / 4
    center = _px(doc, cx, cy)
    corner = _px(doc, x + w - 4, y + 4)       # 沒轉時的右上角
    if kind == "whiteout":
        assert center == (255, 255, 255), ("白底沒有蓋在菱形中心", center)
        assert max(corner) < 60, ("白底沒照角度轉：原本的右上角被蓋白了", corner)
    else:
        assert center[2] > 200 and center[0] < 200, (kind, "中心應該是藍色", center)
        assert corner == (255, 255, 255), (kind, "沒照角度轉：原本的右上角還有顏色", corner)


def test_bad_quad_falls_back_to_the_rect(client):
    """四個角格式不對：照舊畫正的方塊，不可以 500。"""
    uid = _upload(client)
    doc = _save(client, uid, [{"id": "s", "type": "rect", "x": 100, "y": 100, "w": 100, "h": 100,
                               "stroke": "#0000ff", "fill": "#0000ff", "stroke_width": 1,
                               "angle": 45, "quad": [[1, 2], ["x", 3]]}])
    assert _px(doc, 196, 104)[2] > 200

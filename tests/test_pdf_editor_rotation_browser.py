"""PDF 編輯器（真瀏覽器）：畫面上轉過的物件，按存檔之後產出的 PDF 也是轉過的。

後端那一半在 `test_pdf_editor_rotation_opacity_saved.py`（直接送四個角）；
這裡驗的是**前端算的四個角跟畫面上看到的一致**：用 Fabric 自己的轉換矩陣，
軸心、縮放、旋轉都算進去。另外驗便箋拿掉旋轉控制點。
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from pathlib import Path

import fitz
import pytest

from tests.test_pdf_editor_draft_survives_reload import editor  # noqa: F401

CHROME = "/usr/bin/chromium-browser"
pytestmark = pytest.mark.skipif(not Path(CHROME).exists(), reason="沒有 chromium，跳過真瀏覽器測試")


def test_rotated_objects_are_saved_rotated(editor):  # noqa: F811
    port, send, sample = editor

    def ev(expr):
        r = send("Runtime.evaluate", {"expression": expr, "returnByValue": True, "awaitPromise": True})
        res = (r.get("result") or {})
        if res.get("exceptionDetails"):
            raise AssertionError(res["exceptionDetails"])
        return (res.get("result") or {}).get("value")

    send("Page.enable"); send("Runtime.enable"); send("DOM.enable")
    send("Page.navigate", {"url": f"http://127.0.0.1:{port}/tools/pdf-editor/"})
    time.sleep(3.0)
    doc = send("DOM.getDocument", {"depth": -1})["result"]
    node = send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                      "selector": ".file-upload input[type=file]"})["result"]
    send("DOM.setFileInputFiles", {"files": [sample], "nodeId": node["nodeId"]})
    ev("document.querySelector('.file-upload input[type=file]').dispatchEvent(new Event('change',{bubbles:true}))")
    for _ in range(40):
        time.sleep(1)
        if ev("!document.getElementById('editor-panel').hidden && pageCanvases.length > 0"):
            break
    else:
        raise AssertionError("編輯器沒有開起來")

    # 畫面上放：轉 45 度的藍色實心方塊、轉 90 度的文字、轉 90 度的線
    # 都用 originX/Y = center 放，跟使用者拖旋轉控制點一樣（Fabric 以中心轉）
    info = ev("""(() => {
        const c = pageCanvases[0];
        const k = PT2PX;
        const r = new fabric.Rect({left: 300*k, top: 400*k, width: 100*k, height: 100*k,
            fill: '#0000ff', stroke: '#0000ff', strokeWidth: 1, originX: 'center', originY: 'center'});
        r._peType = 'rect'; c.add(r);
        r.rotate(45); r.setCoords();
        const ln = new fabric.Line([0, 0, 200*k, 0], {stroke: '#ff0000', strokeWidth: 3});
        ln._peType = 'line'; c.add(ln);
        ln.set({left: 100*k, top: 600*k}); ln.rotate(90); ln.setCoords();
        const t = new fabric.Textbox('SPIN', {left: 450*k, top: 150*k, fontSize: 20*k, width: 120*k,
            fontFamily: 'Helvetica', fill: '#000000'});
        t._peType = 'text'; c.add(t); t.rotate(90); t.setCoords();
        // 線轉完之後的兩端點（畫面像素 → pt），給 Python 對
        const lp = ln.calcLinePoints(), m = ln.calcTransformMatrix();
        const a = fabric.util.transformPoint(new fabric.Point(lp.x1, lp.y1), m);
        const b = fabric.util.transformPoint(new fabric.Point(lp.x2, lp.y2), m);
        c.discardActiveObject(); c.requestRenderAll();
        return {line: [a.x/k, a.y/k, b.x/k, b.y/k]};
      })()""")
    # 攔下送出的存檔內容
    ev("""(() => { window.__peBodies = []; const f = window.fetch;
        window.fetch = (u, o) => { if (String(u).includes('/save') && o && o.body) window.__peBodies.push(o.body);
                                   return f(u, o); }; })()""")
    ev("savePdf(false, {includeActive: true}).then(() => true)")
    bodies = ev("window.__peBodies")
    assert bodies, "沒有送出存檔"
    body = json.loads(bodies[-1])
    objs = {o["type"]: o for o in body["pages"][0]["objects"]}
    assert objs["rect"]["angle"] == pytest.approx(45) and len(objs["rect"]["quad"]) == 4
    assert objs["text"]["angle"] == pytest.approx(90)

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/tools/pdf-editor/download/{body['upload_id']}",
                                timeout=30) as r:
        pdf = fitz.open("pdf", r.read())
    page = pdf[0]
    pix = page.get_pixmap(dpi=72, alpha=False)
    # 菱形：中心藍、沒轉時的角（350-3, 350+3）空白
    c = pix.pixel(300, 400)
    assert c[2] > 200 and c[0] < 80, ("方塊中心應該是藍的", c)
    assert pix.pixel(347, 353) == (255, 255, 255), "方塊沒有照角度轉（原本的右上角有顏色）"
    assert pix.pixel(300, 400 - 65)[2] > 200, "菱形上方的頂點附近應該是藍的"
    # 線：順時針 90 度 → 從 (100,600) 往下
    x1, y1, x2, y2 = info["line"]
    assert abs(x2 - x1) < 6 and abs((y2 - y1) - 200) < 8, info
    mid = pix.pixel(int((x1 + x2) / 2), int((y1 + y2) / 2))
    assert mid[0] > 200 and mid[1] < 100, ("轉過的線中點應該是紅的", mid)
    # 沒轉時是 (100,601)→(300,601) 的水平線；Fabric 以中心轉，轉完是 x≈200 的直線 → 兩端那裡要是空白
    for x in (125, 275):
        assert pix.pixel(x, 601)[1] > 240, ("線沒照角度轉：原本水平的位置有顏色", x)
    # 文字：行方向往下
    dirs = [ln["dir"] for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
            if any("SPIN" in s["text"] for s in ln["spans"])]
    assert dirs, "產出找不到文字"
    assert dirs[0][0] == pytest.approx(0, abs=1e-2) and dirs[0][1] == pytest.approx(1, abs=1e-2), dirs


def test_sticky_notes_cannot_be_rotated():
    html = Path(__file__).resolve().parents[1].joinpath(
        "app/tools/pdf_editor/templates/pdf_editor.html").read_text(encoding="utf-8")
    i = html.index("group._peType = 'sticky';")
    block = html[i:i + 400]
    assert "lockRotation = true" in block and "mtr: false" in block, "便箋存成 PDF 圖示，不可以給旋轉"

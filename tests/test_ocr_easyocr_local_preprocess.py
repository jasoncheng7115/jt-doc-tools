"""EasyOCR 的去雜點：本機與遠端做一樣的事，而且只在影像真的有雜點、解析度也夠時才做。

2026-11-04 盤點：`recognize_image(preprocess=True)` 走遠端時伺服器一律做 3×3 中值濾波，
走本機 `_easyocr_recognize` 卻完全不理這個參數 —— 同一個引擎、同一份檔案，結果跟著
「有沒有設遠端」變。實測（`ocr_engine.EASYOCR_DENOISE_*` 的註解）又發現無條件濾波讓乾淨的
影像變差，所以改成先看影像再決定，兩條路傳同一個值。

這裡不真的跑 EasyOCR（開發機沒有 AVX2），換一個假的 reader 看它收到的影像。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import numpy as np
from PIL import Image

from app.core import ocr_engine as oe

ROOT = Path(__file__).resolve().parents[1]


def _speckled_png() -> bytes:
    """白底、一顆一顆孤立的黑點（掃描雜點）＋一條粗黑線（字的筆畫）。"""
    img = Image.new("RGB", (60, 40), "white")
    px = img.load()
    for x, y in ((5, 5), (20, 30), (40, 8), (55, 35)):
        px[x, y] = (0, 0, 0)
    for x in range(10, 50):
        for y in range(18, 23):
            px[x, y] = (0, 0, 0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class _FakeReader:
    def __init__(self):
        self.seen = None

    def readtext(self, arr, detail=1, paragraph=False):
        self.seen = np.array(arr)
        return [([[0, 0], [10, 0], [10, 5], [0, 5]], "字", 0.9)]


def _run(monkeypatch, **kw):
    r = _FakeReader()
    monkeypatch.setattr(oe, "_get_easyocr_reader", lambda langs: r)
    words = oe._easyocr_recognize(_speckled_png(), "chi_tra", **kw)
    assert words and words[0]["text"] == "字"
    return r.seen


def test_preprocess_removes_speckles_but_keeps_strokes(monkeypatch):
    a = _run(monkeypatch, preprocess=True)
    assert a.shape == (40, 60, 3), "EasyOCR 要收到 RGB"
    for x, y in ((5, 5), (20, 30), (40, 8), (55, 35)):
        assert tuple(a[y, x]) == (255, 255, 255), ("孤立雜點應該被濾掉", x, y)
    assert tuple(a[20, 30]) == (0, 0, 0), "筆畫不可以被濾掉"


def test_no_preprocess_passes_the_image_through(monkeypatch):
    a = _run(monkeypatch, preprocess=False)
    assert tuple(a[5, 5]) == (0, 0, 0), "preprocess=False 不可以動影像"


def test_default_is_preprocess(monkeypatch):
    a = _run(monkeypatch)
    assert tuple(a[5, 5]) == (255, 255, 255)


def _page(speckle: float, size=(1700, 2400)) -> bytes:
    """一頁假的文件：幾行粗黑字（筆畫）＋指定比例的孤立雜點。"""
    rng = np.random.default_rng(3)
    a = np.full((size[1], size[0], 3), 255, np.uint8)
    for row in range(10):
        y = 200 + row * 180
        for k in range(30):
            x = 120 + k * 48
            a[y:y + 40, x:x + 4] = 0          # 直筆畫
            a[y + 18:y + 22, x:x + 30] = 0    # 橫筆畫
    n = int(size[0] * size[1] * speckle)
    ys = rng.integers(1, size[1] - 1, n)
    xs = rng.integers(1, size[0] - 1, n)
    a[ys, xs] = 0
    buf = io.BytesIO()
    Image.fromarray(a).save(buf, "PNG")
    return buf.getvalue()


def test_clean_pages_are_not_denoised():
    assert oe.easyocr_wants_denoise(_page(0)) is False


def test_speckled_pages_are_denoised():
    assert oe.easyocr_wants_denoise(_page(0.005)) is True


def test_low_resolution_pages_are_not_denoised_even_with_speckles():
    """150 DPI 左右的影像濾了一律變差（實測小字掉 11～50%）。"""
    assert oe.easyocr_wants_denoise(_page(0.005, size=(1240, 1754))) is False


def test_unreadable_images_are_not_denoised():
    assert oe.easyocr_wants_denoise(b"not an image") is False


def _route(monkeypatch, *, remote: bool, denoise: bool):
    got = []
    monkeypatch.setattr(oe, "is_easyocr_available", lambda: True)
    monkeypatch.setattr(oe, "get_default_engine", lambda: "easyocr")
    monkeypatch.setattr(oe, "easyocr_wants_denoise", lambda png: denoise)
    from app.core import ocr_remote_settings as ors
    monkeypatch.setattr(ors, "is_enabled_and_configured", lambda: remote)
    # 遠端回 0 字 → 退到本機：兩條路都要拿到同一個值
    monkeypatch.setattr(oe, "_remote_easyocr_recognize",
                        lambda png, langs, preprocess=True: got.append(("remote", preprocess)) or [])
    monkeypatch.setattr(oe, "_easyocr_recognize",
                        lambda png, langs, preprocess=True: got.append(("local", preprocess)) or [{"text": "x"}])
    return got


def test_remote_and_local_get_the_same_decision(monkeypatch):
    for denoise in (True, False):
        got = _route(monkeypatch, remote=True, denoise=denoise)
        oe.recognize_image(b"png", "chi_tra", preprocess=True)
        assert got == [("remote", denoise), ("local", denoise)], got


def test_preprocess_false_never_denoises(monkeypatch):
    got = _route(monkeypatch, remote=True, denoise=True)
    oe.recognize_image(b"png", "chi_tra", preprocess=False)
    assert got == [("remote", False), ("local", False)]


def test_tesseract_first_falls_back_with_the_same_decision(monkeypatch):
    got = _route(monkeypatch, remote=False, denoise=True)
    monkeypatch.setattr(oe, "is_tesseract_available", lambda: True)
    monkeypatch.setattr(oe, "_tesseract_recognize", lambda *a, **k: [])
    oe.recognize_image(b"png", "chi_tra", engine="tesseract", preprocess=True)
    assert got == [("local", True)]


def test_local_and_remote_do_the_same_thing():
    """本機與遠端伺服器範本的前處理要同一個濾波：改一邊沒改另一邊就紅。"""
    tpl = (ROOT / "app/admin/ocr_remote_deploy/server_template.py").read_text(encoding="utf-8")
    loc = (ROOT / "app/core/ocr_engine.py").read_text(encoding="utf-8")
    pat = re.compile(r"ImageFilter\.MedianFilter\(size=(\d+)\)")
    assert pat.findall(tpl) == ["3"], "遠端範本的前處理改了：本機 _easyocr_image 要跟著改"
    assert pat.findall(loc) == ["3"]

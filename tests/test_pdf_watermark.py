"""Tests for the watermark service — focused on CJK font fallback."""
from __future__ import annotations

import pytest
from PIL import ImageFont

from app.tools.pdf_watermark.service import (
    _has_cjk, _font_covers_cjk, _load_font,
)


def test_has_cjk_detects_chinese():
    assert _has_cjk("已蓋章")
    assert _has_cjk("混合 mixed 中英")
    assert _has_cjk("カタカナ")
    assert _has_cjk("한글")


def test_has_cjk_false_for_ascii():
    assert not _has_cjk("Hello World 123")
    assert not _has_cjk("")


def test_load_font_picks_cjk_fallback_for_cjk_text():
    """If text has CJK and font_path is empty, the loaded font must
    actually cover CJK (regression for: 浮水印中文 → 顯示方框 on Windows)."""
    font = _load_font("", 32, text="已蓋章")
    if isinstance(font, ImageFont.FreeTypeFont):
        assert _font_covers_cjk(font), \
            "_load_font returned a non-CJK font for CJK text"


def test_load_font_skips_non_cjk_user_font_when_text_has_cjk():
    """Caller passes an explicit non-CJK font (DejaVuSans). For ASCII text
    we keep that choice; for CJK text we should fall back to a CJK face."""
    import PIL
    from pathlib import Path
    pil_dir = Path(PIL.__file__).parent
    dejavu = pil_dir / "fonts" / "DejaVuSans.ttf"
    if not dejavu.exists():
        pytest.skip("Bundled DejaVuSans not present")
    f_ascii = _load_font(str(dejavu), 24, text="hello")
    assert isinstance(f_ascii, ImageFont.FreeTypeFont)
    f_cjk = _load_font(str(dejavu), 24, text="中文")
    if isinstance(f_cjk, ImageFont.FreeTypeFont):
        assert _font_covers_cjk(f_cjk) or f_cjk.path != str(dejavu), \
            "CJK text got DejaVuSans (no CJK glyphs) — would render as tofu"

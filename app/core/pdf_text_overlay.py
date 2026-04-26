"""Overlay text values onto a PDF with auto-fit (shrink + wrap).

Each placement targets a slot rectangle rather than a single point: the
engine tries the user's preferred font size, shrinks progressively if the
text overflows the slot width, then wraps onto multiple lines if needed.
Rendering uses a system Unicode font (Arial Unicode on macOS, STHeiti,
Noto CJK…) so CJK, Latin and digits all render with correct metrics.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import fitz  # PyMuPDF


AVAILABLE_FONTS: list[tuple[str, str, Optional[str]]] = [
    # (id, display_name, font_file_path_if_any)
    ("auto", "自動（Arial Unicode / STHeiti）", None),
    ("arial_unicode", "Arial Unicode", "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
    ("stheiti_medium", "STHeiti 中黑", "/System/Library/Fonts/STHeiti Medium.ttc"),
    ("stheiti_light", "STHeiti 細黑", "/System/Library/Fonts/STHeiti Light.ttc"),
    ("kaiti", "楷體 / Kaiti", "/System/Library/Fonts/Supplemental/Kaiti.ttc"),
    ("pingfang", "蘋方 / PingFang", "/System/Library/Fonts/PingFang.ttc"),
    ("builtin_china_t", "PyMuPDF 內建 中文明體", None),
    ("builtin_china_ts", "PyMuPDF 內建 中文黑體", None),
]


_DEFAULT_AUTO_CHAIN = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/STHeiti Light.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]

_BUILTIN_MAP = {
    "builtin_china_t": "china-t",
    "builtin_china_ts": "china-ts",
}


def list_fonts() -> list[dict]:
    """Return UI-facing descriptors for every font that actually exists."""
    out: list[dict] = []
    for fid, name, path in AVAILABLE_FONTS:
        if fid == "auto":
            has_any = any(os.path.exists(p) for p in _DEFAULT_AUTO_CHAIN)
            out.append({"id": fid, "name": name, "available": has_any})
        elif fid.startswith("builtin_"):
            out.append({"id": fid, "name": name, "available": True})
        else:
            out.append({"id": fid, "name": name, "available": path is not None and os.path.exists(path)})
    return [f for f in out if f["available"]]


def _resolve_font_file(font_id: str) -> tuple[Optional[str], Optional[str]]:
    """Return (fontfile_path, builtin_name) — exactly one will be set."""
    if font_id in _BUILTIN_MAP:
        return None, _BUILTIN_MAP[font_id]
    if font_id == "auto" or not font_id:
        for p in _DEFAULT_AUTO_CHAIN:
            if os.path.exists(p):
                return p, None
        return None, "china-t"
    for fid, _name, path in AVAILABLE_FONTS:
        if fid == font_id and path and os.path.exists(path):
            return path, None
    return _resolve_font_file("auto")


@dataclass
class TextPlacement:
    page: int                                   # 0-indexed
    text: str
    slot: tuple[float, float, float, float]    # x0, y0, x1, y1 — bounding box
    base_font_size: float = 11.0
    min_font_size: float = 7.0
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    align: str = "left"                         # "left" | "center"
    # Metadata (not used by overlay rendering, but propagated so the UI and
    # template-saver can know which profile field a placement came from).
    source_key: str = ""                        # profile key (e.g. "company_name")
    kind: str = "text"                          # "text" | "check"
    option_text: str = ""                       # for check placements


def _break_natural_first(text: str) -> list[str]:
    """Split at whitespace / punctuation so word-wrap feels natural.

    We treat CJK break points ( 、，。／,/\\s) as preferred break locations
    and return the pieces (without the separators) so the caller can re-join
    greedily. If no preferred break exists, the whole text is returned as a
    single token so the caller falls back to character-level wrapping.
    """
    buf = ""
    out: list[str] = []
    for ch in text:
        if ch in " \t、，,/／":
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out


def _wrap_lines(
    text: str, font: fitz.Font, size: float, max_width: float
) -> list[str]:
    """Greedy wrap. Prefers whitespace/punctuation breaks; falls back to
    per-character wrapping for long CJK stretches."""
    lines: list[str] = []
    cur = ""
    tokens = _break_natural_first(text)
    for tok in tokens:
        candidate = cur + tok
        if font.text_length(candidate, fontsize=size) <= max_width:
            cur = candidate
            continue
        # Flush current line if non-empty and token itself fits
        if cur and font.text_length(tok, fontsize=size) <= max_width:
            lines.append(cur.rstrip())
            cur = tok
            continue
        # Token is too long — break per-char
        for ch in tok:
            test = cur + ch
            if font.text_length(test, fontsize=size) <= max_width:
                cur = test
            else:
                if cur:
                    lines.append(cur.rstrip())
                cur = ch
    if cur:
        lines.append(cur.rstrip())
    return lines or [text]


def _fit(
    text: str,
    font: fitz.Font,
    slot: tuple[float, float, float, float],
    base_size: float,
    min_size: float,
) -> tuple[float, list[str]]:
    """Pick a font size and line breakdown that fits within ``slot``.

    Tries the base size first, shrinking in 0.5pt increments down to
    ``min_size``. At each size, attempts a single line; if too wide, wraps
    into lines and checks total height. Falls back to min size + wrap even
    if it overflows (visual overflow beats disappearing text).
    """
    x0, y0, x1, y1 = slot
    max_w = max(1.0, x1 - x0)
    max_h = max(1.0, y1 - y0)
    size = base_size
    best_single: Optional[tuple[float, list[str]]] = None
    # Step down in 0.5pt increments for smooth fit.
    while size >= min_size - 0.01:
        w = font.text_length(text, fontsize=size)
        if w <= max_w:
            return size, [text]
        lines = _wrap_lines(text, font, size, max_w)
        total_h = len(lines) * size * 1.2
        if total_h <= max_h:
            return size, lines
        if best_single is None:
            best_single = (size, lines)
        size -= 0.5
    if best_single:
        return best_single
    return min_size, _wrap_lines(text, font, min_size, max_w)


def overlay_text(
    src_pdf: Path,
    dst_pdf: Path,
    placements: Iterable[TextPlacement],
    font_id: str = "auto",
) -> None:
    """Write each placement onto the PDF, auto-fitting inside its slot."""
    placements = list(placements)
    font_file, builtin = _resolve_font_file(font_id)
    # For width measurement we need a fitz.Font — use the TTF if available,
    # otherwise fall back to a CJK Font created from its internal name.
    if font_file:
        measure_font = fitz.Font(fontfile=font_file)
        font_alias = "overlay-font"
    else:
        measure_font = fitz.Font(builtin or "china-t")
        font_alias = builtin or "china-t"

    with fitz.open(str(src_pdf)) as doc:
        for pl in placements:
            if not pl.text or pl.page < 0 or pl.page >= doc.page_count:
                continue
            size, lines = _fit(
                pl.text, measure_font, pl.slot, pl.base_font_size, pl.min_font_size
            )
            page = doc[pl.page]
            x0, y0, x1, y1 = pl.slot
            slot_w = x1 - x0
            slot_h = y1 - y0
            line_h = size * 1.2
            total_h = len(lines) * line_h
            # Vertically center-ish: leave small top padding.
            base_y = y0 + max(size, (slot_h - total_h) / 2 + size * 0.85)
            for i, ln in enumerate(lines):
                line_w = measure_font.text_length(ln, fontsize=size)
                if pl.align == "center":
                    line_x = x0 + (slot_w - line_w) / 2
                else:
                    line_x = x0
                y = base_y + i * line_h
                kwargs = dict(
                    point=fitz.Point(line_x, y),
                    text=ln,
                    fontname=font_alias,
                    fontsize=size,
                    color=pl.color,
                    overlay=True,
                )
                if font_file:
                    kwargs["fontfile"] = font_file
                page.insert_text(**kwargs)
        doc.save(str(dst_pdf), garbage=3, deflate=True)

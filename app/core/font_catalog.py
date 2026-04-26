"""Font discovery for PDF editor.

Scans the host OS's font directories and returns a curated list of usable
fonts, prioritizing Taiwan-relevant traditional CJK fonts and including
common open-source options (Noto TC, Source Han TC, cwTeX, TW-Kai/Sung, etc).

Output is a list of dicts:
    {
        "id": "system:/path/to/font.ttf"  # stable opaque id
        "family": "PingFang TC",         # display name
        "label": "蘋方-繁 (PingFang TC)",
        "variant": "Regular",
        "category": "taiwan" | "free-cjk" | "cjk" | "latin" | "pymupdf",
        "cjk": "traditional" | "simplified" | None,
        "style": "sans" | "serif" | "script" | "mono" | "other",
        "path": "/absolute/path",
        "idx": 0   # TTC sub-font index when applicable
    }

PyMuPDF built-ins are exposed with id="pymupdf:<name>" and no path.
"""
from __future__ import annotations

import os
import platform
import re
import threading
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


# Taiwan-relevant font filename patterns + display metadata.
# First match wins. Patterns are case-insensitive substrings of filename.
_HINTS = [
    # (substring, family, style, cjk, category, label)
    ("pingfang",         "PingFang TC",              "sans",   "traditional", "taiwan",   "蘋方-繁 (PingFang TC)"),
    ("heititc",          "Heiti TC",                 "sans",   "traditional", "taiwan",   "黑體-繁 (Heiti TC)"),
    ("stheitimedium",    "STHeiti Medium",           "sans",   "traditional", "taiwan",   "STHeiti 中"),
    ("stheitilight",     "STHeiti Light",            "sans",   "traditional", "taiwan",   "STHeiti 細"),
    ("lihei pro",        "LiHei Pro",                "sans",   "traditional", "taiwan",   "儷黑 Pro"),
    ("lihei",            "LiHei",                    "sans",   "traditional", "taiwan",   "儷黑"),
    ("lisong",           "LiSong Pro",               "serif",  "traditional", "taiwan",   "儷宋 Pro"),
    ("applegothic",      "Apple LiGothic",           "sans",   "traditional", "taiwan",   "Apple LiGothic"),
    ("applelisung",      "Apple LiSung",             "serif",  "traditional", "taiwan",   "Apple LiSung"),
    ("biaukai",          "BiauKai",                  "script", "traditional", "taiwan",   "標楷體 (BiauKai)"),
    ("dfkaishu",         "DFKaiShu",                 "script", "traditional", "taiwan",   "華康楷書 (DFKaiShu)"),
    ("msjh",             "Microsoft JhengHei",       "sans",   "traditional", "taiwan",   "微軟正黑體"),
    ("jhenghei",         "Microsoft JhengHei",       "sans",   "traditional", "taiwan",   "微軟正黑體"),
    ("mingliu",          "MingLiU",                  "serif",  "traditional", "taiwan",   "細明體 (MingLiU)"),
    ("pmingliu",         "PMingLiU",                 "serif",  "traditional", "taiwan",   "新細明體 (PMingLiU)"),
    # FOSS CJK
    ("notosanstc",       "Noto Sans TC",             "sans",   "traditional", "free-cjk", "Noto Sans TC"),
    ("notoseriftc",      "Noto Serif TC",            "serif",  "traditional", "free-cjk", "Noto Serif TC"),
    ("notosanscjktc",    "Noto Sans CJK TC",         "sans",   "traditional", "free-cjk", "Noto Sans CJK TC"),
    ("notoserifcjktc",   "Noto Serif CJK TC",        "serif",  "traditional", "free-cjk", "Noto Serif CJK TC"),
    ("sourcehansans",    "Source Han Sans TC",       "sans",   "traditional", "free-cjk", "思源黑體 (Source Han Sans)"),
    ("sourcehansanstc",  "Source Han Sans TC",       "sans",   "traditional", "free-cjk", "思源黑體-繁"),
    ("sourcehanserif",   "Source Han Serif TC",      "serif",  "traditional", "free-cjk", "思源宋體 (Source Han Serif)"),
    ("sourcehanseriftc", "Source Han Serif TC",      "serif",  "traditional", "free-cjk", "思源宋體-繁"),
    ("tw-kai",           "TW Kai",                   "script", "traditional", "free-cjk", "TW Kai 楷書"),
    ("tw-sung",          "TW Sung",                  "serif",  "traditional", "free-cjk", "TW Sung 宋體"),
    ("cwtexyen",         "cwTeX Yen",                "sans",   "traditional", "free-cjk", "cwTeX 圓體"),
    ("cwtexming",        "cwTeX Ming",               "serif",  "traditional", "free-cjk", "cwTeX 明體"),
    ("cwtexkai",         "cwTeX Kai",                "script", "traditional", "free-cjk", "cwTeX 楷體"),
    ("cwtexfangsong",    "cwTeX Fang Song",          "script", "traditional", "free-cjk", "cwTeX 仿宋"),
    ("cwtexheib",        "cwTeX HeiBold",            "sans",   "traditional", "free-cjk", "cwTeX 粗黑"),
    ("genyomin",         "GenYoMin TW",              "serif",  "traditional", "free-cjk", "源雲明體"),
    ("gensen",           "GenSenRounded TW",         "sans",   "traditional", "free-cjk", "源流圓體"),
    ("jason-handwriting","Jason Handwriting",        "script", "traditional", "free-cjk", "Jason 手寫體"),
    # Also some simplified + common CJK
    ("notosanscjksc",    "Noto Sans CJK SC",         "sans",   "simplified",  "cjk",      "Noto Sans CJK SC"),
    ("notoserifcjksc",   "Noto Serif CJK SC",        "serif",  "simplified",  "cjk",      "Noto Serif CJK SC"),
    # Latin free
    ("dejavusans",       "DejaVu Sans",              "sans",   None,          "latin",    "DejaVu Sans"),
    ("dejavuserif",      "DejaVu Serif",             "serif",  None,          "latin",    "DejaVu Serif"),
    ("liberationsans",   "Liberation Sans",          "sans",   None,          "latin",    "Liberation Sans"),
    ("liberationserif",  "Liberation Serif",         "serif",  None,          "latin",    "Liberation Serif"),
]


_FONT_DIRS: list[Path] = []


def _detect_font_dirs() -> list[Path]:
    global _FONT_DIRS
    if _FONT_DIRS:
        return _FONT_DIRS
    dirs: list[Path] = []
    sysname = platform.system()
    if sysname == "Darwin":  # macOS
        dirs = [
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/Library/Fonts"),
            Path.home() / "Library" / "Fonts",
        ]
    elif sysname == "Windows":
        dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
                Path.home() / "AppData/Local/Microsoft/Windows/Fonts"]
    else:  # Linux / other
        dirs = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path.home() / ".fonts",
            Path.home() / ".local/share/fonts",
        ]
    _FONT_DIRS = [d for d in dirs if d.exists()]
    return _FONT_DIRS


def custom_fonts_dir() -> Path:
    """User-uploaded fonts live here; they are scanned alongside system
    fonts and get category='custom'."""
    from ..config import settings
    p = settings.fonts_dir
    p.mkdir(parents=True, exist_ok=True)
    return p


def _match_hint(filename: str) -> Optional[tuple]:
    fn = filename.lower().replace(" ", "").replace("-", "").replace("_", "")
    # Sort by longest pattern first to prefer more specific matches
    for pattern, family, style, cjk, category, label in sorted(
        _HINTS, key=lambda h: -len(h[0])
    ):
        if pattern.replace("-", "").replace("_", "") in fn:
            return (family, style, cjk, category, label)
    return None


_CACHE_LOCK = threading.Lock()
_CACHE: Optional[list[dict]] = None


# ---------- Hidden fonts persistence ----------
# Admin can hide certain detected fonts so they don't appear in tool font
# pickers. Hidden state lives in data/font_settings.json:
#     { "hidden": ["pymupdf:default", "system:/path/...", ...] }
def _settings_path() -> Path:
    from ..config import settings as _s
    return _s.data_dir / "font_settings.json"


def get_hidden_ids() -> set[str]:
    import json as _json
    p = _settings_path()
    if not p.exists():
        return set()
    try:
        return set(_json.loads(p.read_text(encoding="utf-8")).get("hidden", []))
    except Exception:
        return set()


def set_hidden_ids(ids: list[str]) -> None:
    import json as _json
    p = _settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_json.dumps({"hidden": sorted(set(ids))}, indent=2),
                 encoding="utf-8")


def list_fonts(include_hidden: bool = False) -> list[dict]:
    """Return the catalog. Scans + caches on first call.

    By default hidden fonts (admin's choice in font management page) are
    filtered out — tools / pickers use this default. Pass include_hidden=True
    in admin UI to display them with a `hidden` flag for re-show.
    """
    global _CACHE
    with _CACHE_LOCK:
        if _CACHE is not None:
            # Apply hidden filter on the cached scan result so admin can
            # toggle visibility without forcing a rescan.
            hidden = get_hidden_ids()
            if include_hidden:
                return [{**f, "hidden": f["id"] in hidden} for f in _CACHE]
            return [f for f in _CACHE if f["id"] not in hidden]
        out: list[dict] = []
        # PyMuPDF built-ins — always present (can be picked even if no
        # system font matches).
        out.extend([
            {"id": "pymupdf:default",   "family": "自動 (中文用繁體宋體 + Helvetica)",
             "label": "自動（中文繁體 + Helvetica）", "variant": "",
             "category": "pymupdf", "cjk": None, "style": "sans"},
            {"id": "pymupdf:sans",      "family": "PyMuPDF Sans",
             "label": "PyMuPDF 內建 Sans（繁中黑體 + Helvetica）", "variant": "",
             "category": "pymupdf", "cjk": None, "style": "sans"},
            {"id": "pymupdf:serif",     "family": "PyMuPDF Serif",
             "label": "PyMuPDF 內建 Serif（繁中宋體 + Times）", "variant": "",
             "category": "pymupdf", "cjk": None, "style": "serif"},
            {"id": "pymupdf:simplified","family": "PyMuPDF 簡體",
             "label": "PyMuPDF 簡體（SimSun）", "variant": "",
             "category": "pymupdf", "cjk": "simplified", "style": "serif"},
            {"id": "pymupdf:helv",      "family": "Helvetica",
             "label": "Helvetica（僅英數）", "variant": "",
             "category": "pymupdf", "cjk": None, "style": "sans"},
            {"id": "pymupdf:tiro",      "family": "Times",
             "label": "Times（僅英數）", "variant": "",
             "category": "pymupdf", "cjk": None, "style": "serif"},
            {"id": "pymupdf:cour",      "family": "Courier",
             "label": "Courier（等寬、僅英數）", "variant": "",
             "category": "pymupdf", "cjk": None, "style": "mono"},
        ])
        # System scan
        seen_paths: set[Path] = set()
        for d in _detect_font_dirs():
            try:
                for p in d.rglob("*"):
                    if not p.is_file():
                        continue
                    if p.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                        continue
                    if p in seen_paths:
                        continue
                    seen_paths.add(p)
                    hint = _match_hint(p.name)
                    if not hint:
                        continue
                    family, style, cjk, category, label = hint
                    variant = _variant_from_name(p.name)
                    out.append({
                        "id": f"system:{p}",
                        "family": family,
                        "label": f"{label}" + (f" {variant}" if variant else ""),
                        "variant": variant,
                        "category": category,
                        "cjk": cjk,
                        "style": style,
                        "path": str(p),
                        "idx": 0,
                    })
            except Exception:
                continue

        # Custom (user-uploaded) fonts — show every file (no hint filter)
        # with category='custom' so organisation fonts show up even if they
        # don't match our Taiwan/FOSS patterns.
        try:
            cdir = custom_fonts_dir()
            for p in sorted(cdir.rglob("*")):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                    continue
                stem = p.stem
                variant = _variant_from_name(p.name)
                out.append({
                    "id": f"custom:{p.name}",
                    "family": stem,
                    "label": f"{stem}" + (f" {variant}" if variant else ""),
                    "variant": variant,
                    "category": "custom",
                    "cjk": None,
                    "style": "sans",
                    "path": str(p),
                    "idx": 0,
                })
        except Exception:
            pass

        # Category sort: custom first, taiwan, free-cjk, cjk, latin, pymupdf
        order = {"custom": -1, "taiwan": 0, "free-cjk": 1,
                 "cjk": 2, "latin": 3, "pymupdf": 9}
        out.sort(key=lambda f: (order.get(f["category"], 8), f["label"]))
        _CACHE = out
        hidden = get_hidden_ids()
        if include_hidden:
            return [{**f, "hidden": f["id"] in hidden} for f in out]
        return [f for f in out if f["id"] not in hidden]


_VARIANT_PATTERNS = [
    ("ultrabold", "UltraBold"), ("extrabold", "ExtraBold"),
    ("semibold", "SemiBold"), ("demibold", "DemiBold"),
    ("bolditalic", "Bold Italic"),
    ("bold", "Bold"), ("italic", "Italic"), ("oblique", "Oblique"),
    ("light", "Light"), ("thin", "Thin"), ("medium", "Medium"),
    ("regular", ""), ("normal", ""), ("book", ""),
]


def _variant_from_name(filename: str) -> str:
    name = Path(filename).stem.lower().replace(" ", "").replace("-", "").replace("_", "")
    for key, label in _VARIANT_PATTERNS:
        if key in name:
            return label
    return ""


def refresh_cache() -> None:
    """Force a rescan (e.g., after user adds a font file)."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def resolve_font_id(font_id: str) -> Optional[dict]:
    """Find font entry by id; None if not found."""
    for f in list_fonts():
        if f.get("id") == font_id:
            return f
    return None

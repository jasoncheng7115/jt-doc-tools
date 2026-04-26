"""Detect □/☐ checkbox options in a PDF and help match them to profile values.

Only text-based checkboxes are handled — i.e. forms where the box is a
printed glyph, which is the common case for the vendor forms we've seen.
True form-widget checkboxes would be handled via PyMuPDF widgets; this
module deliberately ignores those.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


BOX_CHARS = "□☐"


@dataclass
class CheckboxOption:
    page: int
    box_rect: tuple[float, float, float, float]  # bbox of the □ glyph
    text: str                                     # the option label beside the box
    size: float                                   # font size of the glyph


def extract_checkboxes(pdf_path: Path) -> list[CheckboxOption]:
    """Return every checkbox option on every page, in reading order."""
    out: list[CheckboxOption] = []
    with fitz.open(str(pdf_path)) as doc:
        for pno in range(doc.page_count):
            out.extend(_page_checkboxes(doc[pno], pno))
    return out


def extract_blank_box_clusters(pdf_path: Path) -> list[list[CheckboxOption]]:
    """Find groups of 3–6 empty □ boxes printed in a row on the same page —
    these are almost always digit slots for a zip code on Taiwan forms.

    Each returned list is one cluster: CheckboxOptions whose ``text`` is
    empty (or just trivial characters), in left-to-right order, sharing the
    same baseline row on the same page.
    """
    out: list[list[CheckboxOption]] = []
    with fitz.open(str(pdf_path)) as doc:
        for pno in range(doc.page_count):
            page = doc[pno]
            raw = page.get_text("rawdict")
            for block in raw.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    # Each line that has ≥3 adjacent □ and (little or no text
                    # between them) is a cluster candidate.
                    cluster: list[CheckboxOption] = []
                    for span in line.get("spans", []):
                        size = span.get("size", 10.0)
                        for ch in span.get("chars", []):
                            c = ch.get("c", "")
                            if c in BOX_CHARS:
                                bb = tuple(ch.get("bbox") or (0, 0, 0, 0))
                                cluster.append(CheckboxOption(
                                    page=pno, box_rect=bb, text="", size=size,
                                ))
                    if 3 <= len(cluster) <= 8:
                        out.append(cluster)
    return out


def _page_checkboxes(page: fitz.Page, pno: int) -> list[CheckboxOption]:
    raw = page.get_text("rawdict")
    out: list[CheckboxOption] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            # Flatten chars across all spans in this line so the option text
            # that follows a □ can span into adjacent spans (forms often switch
            # fonts right after the box).
            flat: list[dict] = []
            for span in line.get("spans", []):
                size = span.get("size", 10.0)
                for ch in span.get("chars", []):
                    c = ch.get("c", "")
                    bbox = ch.get("bbox") or (0, 0, 0, 0)
                    flat.append({"c": c, "bbox": tuple(bbox), "size": size})
            for i, ch in enumerate(flat):
                if ch["c"] not in BOX_CHARS:
                    continue
                # Collect chars until the next box or end of line.
                text_chars: list[str] = []
                for j in range(i + 1, len(flat)):
                    c = flat[j]["c"]
                    if c in BOX_CHARS:
                        break
                    text_chars.append(c)
                text = "".join(text_chars).strip()
                text = re.sub(r"[\s_:：—─\-]+$", "", text)  # trim trailing blank-line decoration
                if not text:
                    continue
                out.append(
                    CheckboxOption(
                        page=pno,
                        box_rect=ch["bbox"],
                        text=text,
                        size=ch["size"],
                    )
                )
    return out


def options_near_label(
    options: list[CheckboxOption],
    label_rect: tuple[float, float, float, float],
    label_page: int,
    search_rect: tuple[float, float, float, float] | None = None,
) -> list[CheckboxOption]:
    """Pick the checkboxes that belong to this label.

    If ``search_rect`` is provided we treat it as a *hint* of where the
    value region lives, then broaden the horizontal range so we also catch
    boxes sitting in adjacent marker / option cells on the same row. The
    vertical range is bounded by the hint plus a small top margin so boxes
    printed above the label (same row, centred label) aren't missed. If no
    hint is supplied, fall back to a loose same-page proximity filter.
    """
    lx0, ly0, lx1, ly1 = label_rect
    if search_rect is not None:
        sx0, sy0, sx1, sy1 = search_rect
        # Stay strictly inside the value slot. The checkbox's *centre* must
        # fall inside the slot — boxes that just kiss the edge (e.g. the
        # top row of "員工人數" options brushing up against 負責人's slot
        # bottom) no longer get pulled into the neighbouring label.
        out: list[CheckboxOption] = []
        for o in options:
            if o.page != label_page:
                continue
            bx = (o.box_rect[0] + o.box_rect[2]) / 2
            by = (o.box_rect[1] + o.box_rect[3]) / 2
            if sx0 - 1 <= bx <= sx1 + 1 and sy0 - 1 <= by <= sy1 + 1:
                out.append(o)
        return out
    out = []
    for o in options:
        if o.page != label_page:
            continue
        oy = (o.box_rect[1] + o.box_rect[3]) / 2
        if ly0 - 40 <= oy <= ly1 + 240:
            out.append(o)
    return out


# Common aliases — lets profile values written in one style tick checkboxes
# printed in another (e.g. "台幣" ↔ "NTD", "美金" ↔ "USD"). Extend as needed.
_ALIASES: dict[str, list[str]] = {
    "台幣": ["ntd", "twd", "新台幣", "新臺幣"],
    "臺幣": ["ntd", "twd", "新台幣", "台幣"],
    "新台幣": ["ntd", "twd", "台幣"],
    "美金": ["usd", "美元"],
    "美元": ["usd", "美金"],
    "日圓": ["yen", "jpy", "日元"],
    "日幣": ["yen", "jpy", "日元"],
    "人民幣": ["rmb", "cny"],
    "歐元": ["eur", "euro"],
    "港幣": ["hkd"],
    "電匯": ["t/t", "tt", "wire"],
    "支票": ["check", "cheque"],
    "現金": ["cash"],
    "電子發票": ["電子計算機發票", "電計發票", "e-invoice"],
    # Payment-term aliases — forms mix CJK numerals, Arabic numerals, and
    # English equivalents interchangeably for the same settlement period.
    "月結三十天": ["30", "三十", "30天", "三十天", "月結30天", "月結30", "net 30", "net 30 days"],
    "月結四十五天": ["45", "四十五", "45天", "四十五天", "月結45天", "月結45", "net 45", "net 45 days"],
    "月結六十天": ["60", "六十", "60天", "六十天", "月結60天", "月結60", "net 60", "net 60 days"],
    "月結九十天": ["90", "九十", "90天", "九十天", "月結90天", "月結90", "net 90", "net 90 days"],
    "預付": ["prepaid", "in advance"],
}


# Simplified↔Traditional folds that forms mix interchangeably
# (e.g. "應稅內含" vs "應稅内含"). Fold to simplified for comparison only.
_CJK_FOLD = str.maketrans({
    "內": "内", "臺": "台", "戶": "户", "來": "来", "會": "会",
    "發": "发", "電": "电", "號": "号", "業": "业", "產": "产",
    "廠": "厂", "幣": "币", "稅": "税", "於": "于", "務": "务",
})


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return re.sub(r"\s+", "", s).translate(_CJK_FOLD).lower()


def _expand_with_aliases(value: str) -> list[str]:
    """Return [value, ...aliases] — the original first so it wins on exact match."""
    out = [value]
    nv = value.strip().lower()
    # map keys are not normalized (they're Chinese terms); try both
    for k, syns in _ALIASES.items():
        if k.lower() == nv or _normalize(k) == _normalize(value):
            out.extend(syns)
        elif nv in (s.lower() for s in syns):
            out.append(k)
            out.extend(s for s in syns if s.lower() != nv)
    return out


def match_all_options(
    options: list[CheckboxOption], value: str
) -> list[CheckboxOption]:
    """Return every checkbox whose label matches ``value`` (or any of its
    aliases) — lets a single profile value (e.g. "月結三十天") tick multiple
    related boxes (e.g. "30" on a compound row).
    """
    if not value or not options:
        return []
    candidates = _expand_with_aliases(value)
    seen: set[tuple[int, int, int]] = set()
    out: list[CheckboxOption] = []
    for cand in candidates:
        nc = _normalize(cand)
        if not nc:
            continue
        for o in options:
            no = _normalize(o.text)
            if not no:
                continue
            if no == nc or no in nc or nc in no:
                k = (o.page, int(o.box_rect[0]), int(o.box_rect[1]))
                if k in seen:
                    continue
                seen.add(k)
                out.append(o)
    return out


_NUM_RE = re.compile(r"\d+")


def _match_numeric_range(options: list[CheckboxOption], value: str) -> CheckboxOption | None:
    """If ``value`` is a single integer, parse each option as a range/bound
    and return the one the value falls into.

    Handles common form phrasings:
      "10人以下" / "10以下"    → upper bound = 10
      "11~50人" / "11-50"      → [11, 50]
      "201以上" / "201 up"     → lower bound = 201
    """
    v = value.strip()
    if not v.isdigit():
        return None
    n = int(v)
    for o in options:
        t = _normalize(o.text)
        nums = [int(x) for x in _NUM_RE.findall(t)]
        if not nums:
            continue
        if "以下" in t or "以內" in t or "≤" in t:
            if n <= nums[0]:
                return o
        elif "以上" in t or "≥" in t:
            if n >= nums[0]:
                return o
        elif len(nums) >= 2:
            lo, hi = min(nums[0], nums[1]), max(nums[0], nums[1])
            if lo <= n <= hi:
                return o
    return None


def match_option(
    options: list[CheckboxOption], value: str
) -> CheckboxOption | None:
    """Return the single checkbox whose label matches ``value`` best.

    Matching strategy (first wins):
      1. Numeric range match (e.g. value="2" → "10人以下").
      2. Exact match after whitespace/case normalization.
      3. Option fully contained in the value string (e.g. value is a list
         "電匯、匯款" and option is "電匯").
      4. Value fully contained in the option (value is a keyword).
    """
    hit = _match_numeric_range(options, value)
    if hit is not None:
        return hit
    if not value or not options:
        return None
    # Build a list of candidate strings — the original value plus any aliases
    # (e.g. 台幣 ↔ NTD, 電匯 ↔ T/T) so cross-notation checkboxes still match.
    candidates = _expand_with_aliases(value)
    for cand in candidates:
        nc = _normalize(cand)
        if not nc:
            continue
        for o in options:
            if _normalize(o.text) == nc:
                return o
    for cand in candidates:
        nc = _normalize(cand)
        if not nc:
            continue
        for o in options:
            no = _normalize(o.text)
            if no and no in nc:
                return o
        for o in options:
            no = _normalize(o.text)
            if no and nc in no:
                return o
    return None

"""Per-field LLM review (NEW APPROACH — overnight 2026-04-24 finding).

Replaces the whole-page "find errors in this 21-box form" prompt that hangs
small vision models for 10+ minutes. Instead crops each filled field into a
tiny tile and asks a binary "is this value correctly filled?" — completes in
30-60s total for 20+ fields.

Drop-in alongside `app/core/llm_review.py:review()`. Returns the same
`ReviewResult` shape so the existing frontend code keeps working.

Usage:
    from app.core.llm_review_per_field import per_field_review
    result = per_field_review(pdf_path, filled_fields, page_index=0)
    # result is the same ReviewResult dataclass as review()

To switch the production code over:
1. Edit `app/tools/pdf_fill/router.py:llm_review_start()`
2. Change `from ...core.llm_review import review, filled_from_placements`
   to  `from ...core.llm_review_per_field import per_field_review as review`
3. Test, ship.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx
from PIL import Image, ImageDraw

from .llm_review import (
    FilledField, ReviewResult, RoundResult, Correction,
)
from .llm_settings import llm_settings

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------- tuning --

DPI = 150               # render filled PDF at this DPI; 150 is sharp enough for OCR
# Crop padding — kept tight so the tile doesn't swallow neighbouring rows on
# compact forms (e.g. Supplier Payment Application Form has (中)/(英) stacked
# in one cell; too much padding made LLM read both rows and get confused).
# Vertical is especially tight; horizontal a bit more generous so the label
# to the left is still visible.
PAD_PX_V = 12           # vertical padding (top + bottom)
PAD_PX_H = 24           # horizontal right padding
LABEL_LEFT_EXTRA = 60   # extra room on left for label text
PER_FIELD_TIMEOUT = 15  # seconds; most successes complete <2s, anything longer is stuck
PT2PX = DPI / 72.0


# ------------------------------------------------------------- rendering --

def _render_page(pdf_path: Path, page_index: int) -> Image.Image:
    """Render full filled page once at DPI; we'll crop tiles from this."""
    import fitz
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[page_index]
        mat = fitz.Matrix(DPI / 72, DPI / 72)
        pix = page.get_pixmap(matrix=mat)
        return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()


def _crop_tile(page_img: Image.Image, slot_pt: tuple) -> bytes:
    x0, y0, x1, y1 = slot_pt
    px0 = max(0, int(x0 * PT2PX) - PAD_PX_H - LABEL_LEFT_EXTRA)
    py0 = max(0, int(y0 * PT2PX) - PAD_PX_V)
    px1 = min(page_img.width,  int(x1 * PT2PX) + PAD_PX_H)
    py1 = min(page_img.height, int(y1 * PT2PX) + PAD_PX_V)
    crop = page_img.crop((px0, py0, px1, py1))
    d = ImageDraw.Draw(crop, "RGBA")
    rx0 = int(x0 * PT2PX) - px0
    ry0 = int(y0 * PT2PX) - py0
    rx1 = int(x1 * PT2PX) - px0
    ry1 = int(y1 * PT2PX) - py0
    d.rectangle([rx0, ry0, rx1, ry1], outline=(220, 38, 38, 230), width=2)
    buf = io.BytesIO()
    crop.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# --------------------------------------------------------------- LLM call --

def _ollama_chat(base_url: str, model: str, prompt: str,
                 png: bytes, timeout: float) -> tuple[str, str]:
    """Native Ollama /api/chat call — single-shot, non-streaming, think:False.
    Returns (content, error). On failure: content='', error=<short reason>."""
    # Strip /v1 if the user's base_url is OpenAI-compat-style
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    payload = {
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": 0.0},
        "messages": [{
            "role": "user", "content": prompt,
            "images": [base64.b64encode(png).decode()],
        }],
    }
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(f"{base}/api/chat", json=payload)
        if r.status_code != 200:
            return "", f"HTTP {r.status_code}"
        msg = r.json().get("message", {})
        return msg.get("content", "").strip(), ""
    except httpx.TimeoutException:
        return "", "timeout"
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}: {e}"


# ------------------------------------------------------------- per-field --

def _q2_value_match(value: str) -> str:
    return (f"The red box should contain the value: \"{value}\".\n"
            f"Is the text inside the red box exactly this value? "
            f"Reply only YES or NO.")


def _q3_label_fit(value: str) -> str:
    return (f"The red box contains the value: \"{value}\".\n"
            f"Look at the LABEL text near the red box (above or to the left).\n"
            f"Does this value make sense for that label? Reply only YES or NO.")


def _q4_actual_text() -> str:
    """Ask what's actually in the red box. Used when Q2=NO to surface
    the OCR delta so the user can see *what* the mismatch is."""
    return ("What text is ACTUALLY inside the red box right now? "
            "Reply with just the text you see — nothing else. "
            "If the box is empty, reply NOTHING.")


def _fuzzy_close(expected: str, actual: str) -> bool:
    """Return True if LLM's OCR reading (actual) is close enough to the
    expected value to count as a match. Rescues Q2=NO false-negatives
    from:

      (a) punctuation noise (CO.,LTD → CO.,,LTD)
      (b) whitespace / line-break insertion
      (c) crop tile bleeding into adjacent text (expected appears as a
          prefix/substring of actual with trailing noise — common when
          a tight cell's neighbours get captured by the crop)

    Match is close when:
      - Normalized forms are equal, OR
      - expected (len ≥ 3) appears as a prefix of actual (case b/c), OR
      - expected (len ≥ 5) appears anywhere inside actual, OR
      - difflib similarity >= 0.92 after normalization (same-length typo)
    """
    import re
    import difflib
    if not expected or actual is None:
        return False
    def norm(s: str) -> str:
        s = re.sub(r"([,.\-_;:])\1+", r"\1", s.strip())   # ,, → ,
        s = re.sub(r"\s+", "", s)                          # drop whitespace
        return s.lower()
    ne = norm(expected)
    na = norm(actual)
    if not ne or not na:
        return False
    if ne == na:
        return True
    # Rescue (b)+(c): expected is prefix/substring of actual (crop noise)
    if len(ne) >= 3 and na.startswith(ne):
        return True
    if len(ne) >= 5 and ne in na:
        return True
    # Rescue (a): same-length typo / minor punctuation noise
    if abs(len(ne) - len(na)) <= max(4, len(ne) // 5):
        return difflib.SequenceMatcher(None, ne, na).ratio() >= 0.92
    return False


def _q5_label_pick(value: str, candidates: list[str]) -> str:
    """Ask which candidate label fits this value best. Used to decide where
    this value SHOULD go when Q3=NO (wrong-cell) indicates misplacement."""
    lines = "\n".join(f"{chr(65+i)}) {c}" for i, c in enumerate(candidates))
    return (f"The value \"{value}\" was placed here but the label nearby does not fit.\n"
            f"Which of the following labels best matches this value?\n{lines}\n"
            f"Reply with just the letter (e.g. A). If none fit, reply NONE.")


# ------------------------------------------------------------ entry point --

def per_field_review(
    pdf_path: Path,
    filled_fields: list[FilledField],
    *,
    page_index: int = 0,
    max_rounds: Optional[int] = None,  # ignored — per-field is single-pass
    profile_keys: Optional[list[str]] = None,  # learning currently disabled
    progress_cb=None,
    candidate_labels: Optional[list[str]] = None,  # for Q5 semantic placement
    label_to_slot: Optional[dict[str, tuple]] = None,  # label -> value_slot for auto-move
) -> ReviewResult:
    """Per-field crop + binary-yes/no review.

    For each filled text field on `page_index`:
      1. Crop ~600×150 tile around the field (label + box visible)
      2. Ask LLM Q2: "is the value '<text>' the text in the red box? YES/NO"
      3. If NO: ask Q3 "does the value fit the nearby label? YES/NO" to
         distinguish OCR-error (Q3=YES) from wrong-cell (Q3=NO).
      4. Aggregate suspect fields as Corrections.

    Skips checkbox / option-text fields (model can't reliably handle ✓).

    Single round; consensus voting is unnecessary because each query is
    narrowly scoped enough that random noise is rare.
    """
    result = ReviewResult(filled=list(filled_fields))
    s = llm_settings.get()
    if not s.get("enabled"):
        result.errors.append("LLM 校驗未啟用")
        return result

    base_url = s.get("base_url") or ""
    # Per-tool 覆寫優先（admin 在 LLM 設定頁可以給 pdf-fill 校驗指定模型）
    model = llm_settings.get_model_for("pdf-fill")

    def notify(n, total, msg):
        if progress_cb:
            try: progress_cb(n, total, msg)
            except Exception: pass

    page_img = _render_page(pdf_path, page_index)
    fields_on_page = [f for f in filled_fields if f.page == page_index
                      and (f.value or "").strip()
                      and (f.value or "").strip() != "✓"]   # skip checkboxes
    total = len(fields_on_page)
    notify(0, total, f"準備 {total} 個欄位的校驗")

    rr = RoundResult(round=1, verdict="needs_correction")
    started = time.monotonic()

    # Build placement_idx mapping so we can tell the apply step which
    # placement to remove/modify. filled_fields is derived from placements
    # (see filled_from_placements) — we track the original index.
    for pidx, f in enumerate(fields_on_page):
        f._pidx = pidx  # hack: stash for Correction.placement_idx
        # Note: pidx is index in fields_on_page (filtered, page0 text-only),
        # not the full placements list. The apply step maps back by
        # (label, value) which is more robust across snapshot formats.

    for i, f in enumerate(fields_on_page):
        notify(i + 1, total, f"校驗欄位 {i+1}/{total}: {f.label_text or f.profile_key}")
        png = _crop_tile(page_img, f.slot_pt)

        ans2, err2 = _ollama_chat(base_url, model, _q2_value_match(f.value),
                                   png, PER_FIELD_TIMEOUT)
        # Treat timeout as "uncertain — not a flag" (ignore noisy errors)
        if err2 == "timeout" or not ans2:
            continue

        if ans2.upper().startswith("YES"):
            continue   # value matches what's in the box — all good

        # Q4 FIRST — get LLM's actual OCR of the red box so we can do a
        # fuzzy-match rescue on Q2 false negatives (OCR noise like doubled
        # punctuation or whitespace causing Q2=NO when the value is
        # actually there). If actual ≈ expected, skip correction entirely.
        ans4, _ = _ollama_chat(base_url, model, _q4_actual_text(),
                                png, PER_FIELD_TIMEOUT)
        actual = None
        if ans4 and ans4.strip():
            a = ans4.strip()
            if a.upper() in {"NOTHING", "NONE", "(EMPTY)", "EMPTY"}:
                actual = ""  # explicitly empty
            else:
                # Strip common LLM prefixes like "I see: " etc.
                for p in ("I see:", "I see", "The text is", "Text:"):
                    if a.startswith(p):
                        a = a[len(p):].strip(" :\"'")
                actual = a[:200]

        # Fuzzy rescue: Q2 said NO but actual OCR matches value within
        # noise tolerance → treat as clean, don't flag this field.
        if actual and _fuzzy_close(f.value, actual):
            continue

        # Q3 — disambiguate wrong-cell vs value-mismatch
        ans3, _ = _ollama_chat(base_url, model, _q3_label_fit(f.value),
                                png, PER_FIELD_TIMEOUT)
        is_wrong_cell = ans3 and not ans3.upper().startswith("YES")

        # Q5 — only when wrong-cell AND candidate_labels were supplied. Asks
        # which label best fits the value, so the apply step can auto-move.
        suggested_label = None
        if is_wrong_cell and candidate_labels:
            # Exclude the current label from candidates; offer up to 8
            others = [c for c in candidate_labels
                      if c and c != (f.label_text or f.profile_key)][:8]
            if others:
                ans5, _ = _ollama_chat(base_url, model,
                                        _q5_label_pick(f.value, others),
                                        png, PER_FIELD_TIMEOUT)
                if ans5 and ans5.strip():
                    letter = ans5.strip()[0].upper()
                    if letter.isalpha() and (ord(letter) - 65) < len(others):
                        suggested_label = others[ord(letter) - 65]

        # Resolve suggested label → target slot (Phase 2.5 auto-move)
        suggested_slot_pt = None
        if suggested_label and label_to_slot:
            suggested_slot_pt = label_to_slot.get(suggested_label)

        rr.corrections.append(Correction(
            type="WRONG_PLACEMENT" if is_wrong_cell else "WRONG_VALUE",
            label=f.label_text or f.profile_key,
            current_value=f.value,
            expected_value=None,
            confidence=0.7 if is_wrong_cell else 0.5,
            reason=("LLM 認為值填到錯的欄位（value 跟 label 不搭）"
                    if is_wrong_cell
                    else "LLM 認為紅框內容與預期值不符"),
            actual_ocr=actual,
            suggested_label=suggested_label,
            placement_idx=getattr(f, "_pidx", None),
            slot_pt=tuple(f.slot_pt) if f.slot_pt else None,
            suggested_slot_pt=tuple(suggested_slot_pt) if suggested_slot_pt else None,
        ))

    rr.elapsed_s = round(time.monotonic() - started, 2)
    rr.accepted = list(rr.corrections)   # no consensus — accept all
    rr.verdict = "all_clear" if not rr.corrections else "needs_correction"
    result.rounds.append(rr)
    result.total_elapsed_s = rr.elapsed_s
    notify(total, total, f"完成（{rr.elapsed_s:.1f}s, {len(rr.corrections)} 建議）")
    return result

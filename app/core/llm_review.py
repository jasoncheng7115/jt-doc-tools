"""LLM 校驗附加功能 — review loop core (M2 + M3).

Pipeline:
  1. detect_fields() with LABEL_MAP                  (already exists, core)
  2. Compute values from profile                      (already exists in pdf-fill)
  3. Render preview PNG with red overlays per field   (this module: M2)
  4. Send PNG → LLM, parse corrections                (this module: M3)
  5. Conservative consensus + apply                   (this module: M3)
  6. Repeat up to N rounds                            (this module: M3)
  7. Persist learnings (deferred to next milestone)

Designed to be called only when LLM is enabled and the user opts in. Failures
are caught and surfaced via ``ReviewResult.errors`` — never re-raised, so the
caller can fall back to plain LABEL_MAP results without breaking the user.
"""
from __future__ import annotations

import io
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

from . import pdf_preview
from .llm_settings import llm_settings


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- prompt --

# --- Prompt variants per model family --------------------------------------
#
# Different vision models respond best to different prompt styles:
# - Qwen3-VL: 中文 prompt 直接最佳。對 JSON schema 與多步驟推理理解力強
# - Gemma:    英文骨架 + 中文範例最穩。更需要明示 JSON 結構
# - 其他/泛用: 簡化版，避免複雜句式

REVIEW_PROMPT_QWEN = """你是**台灣廠商資料表的審查員**，專門挑填寫錯誤。

圖中紅色細框 = 有人**已經填進去**的值。其他是空白欄位。

你的任務**只有一個**：找出紅框裡**填錯**的地方。
**不要**列出「需要填的欄位」、**不要**解釋表格結構、**不要**列出填對的欄位。

常見錯誤類型：
1. 中文值被填到英文欄位（例：公司英文名欄位填了中文公司名 + 「Co., Ltd.」結尾）
2. 英文值被填到中文欄位
3. 值跟欄位語意對不上（例：貿易條件填了「月結30天」，那是付款條件的值）
4. 同一個值重複出現在多個不相關欄位

**輸出規則**：
- 只回 JSON 物件，不要 markdown、不要說明、不要「以下是...」這類開場白
- 完全沒錯 → `{"corrections": []}`
- 有錯 → 每個錯誤一個物件

JSON 格式：
{
  "corrections": [
    {"label": "欄位名", "current": "紅框裡目前的值", "issue": "問題描述", "expected": "應該填什麼"}
  ]
}"""


REVIEW_PROMPT_GEMMA = """This is an auto-filled Taiwanese vendor form (red boxes = our fills).

Find errors like:
- Chinese text in English-only fields or vice versa
- Values in wrong cells
- Missing or extra fills

Reply as JSON:
{
  "corrections": [
    {"label": "<Chinese field name>", "issue": "<what's wrong>", "expected": "<what it should be>"}
  ]
}

If nothing is wrong, reply {"corrections": []}.
"""


REVIEW_PROMPT_GENERIC = """This filled vendor form — find any errors (wrong cell, missing, extra).

Reply as JSON:
{"corrections": [{"label": "field", "issue": "problem", "expected": "correct value"}]}

Empty array if nothing's wrong.
"""


def get_review_prompt(model: str) -> str:
    """Return the prompt variant best-tuned for the given model family.
    Unknown models fall back to the generic version."""
    m = (model or "").lower()
    if "qwen" in m:
        return REVIEW_PROMPT_QWEN
    if "gemma" in m:
        return REVIEW_PROMPT_GEMMA
    # llava, minicpm-v, llama vision, internvl ... all fall through to generic
    return REVIEW_PROMPT_GENERIC


# Back-compat alias for callers that import REVIEW_PROMPT directly.
REVIEW_PROMPT = REVIEW_PROMPT_QWEN


# ---------------------------------------------------------------- types --

@dataclass
class FilledField:
    """A field we filled (or tried to). Mirrors DetectedField but keeps only
    what we need to render + send to the LLM."""
    page: int
    profile_key: str
    label_text: str
    value: str
    # bbox of the slot we drew the value into, in PDF points (origin top-left)
    slot_pt: tuple[float, float, float, float]


def filled_from_placements(placements, profile_labels: dict | None = None) -> list[FilledField]:
    """Convert pdf_text_overlay.TextPlacement list (from pdf_fill service)
    into FilledField objects suitable for LLM review.

    ``profile_labels`` is the optional ``profile["labels"]`` dict so we can
    show a human-readable Chinese label in the LLM annotation rather than the
    bare profile key. Falls back to the key itself if not provided.
    """
    out: list[FilledField] = []
    for p in placements or []:
        if not p.text:
            continue  # skip empty / placeholder
        key = getattr(p, "source_key", "") or ""
        label = (profile_labels or {}).get(key, key)
        out.append(FilledField(
            page=int(getattr(p, "page", 0)),
            profile_key=key,
            label_text=label,
            value=str(p.text),
            slot_pt=tuple(p.slot) if hasattr(p, "slot") else (0.0, 0.0, 0.0, 0.0),
        ))
    return out


@dataclass
class Correction:
    type: str           # WRONG_PLACEMENT | WRONG_VALUE | MISSING | EXTRA
    label: str
    current_value: Optional[str]
    expected_value: Optional[str]
    confidence: float
    reason: str = ""
    # Extras from per-field review (Phase 1) — surface to UI + apply step
    actual_ocr: Optional[str] = None       # what LLM read from the red box
    suggested_label: Optional[str] = None  # label the value should belong to
    placement_idx: Optional[int] = None    # index into snapshot.placements (for apply)
    slot_pt: Optional[tuple] = None        # slot this placement occupies — precise match
    suggested_slot_pt: Optional[tuple] = None  # target slot (from detected cells) — auto-move target

    def key(self) -> tuple:
        """For consensus comparison across rounds — same (type, label, value
        suggestion) twice means the LLM is consistent about this issue."""
        return (self.type, self.label.strip(),
                (self.expected_value or "").strip())


@dataclass
class RoundResult:
    round: int
    verdict: str
    corrections: list[Correction] = field(default_factory=list)
    accepted: list[Correction] = field(default_factory=list)
    elapsed_s: float = 0.0
    error: Optional[str] = None


@dataclass
class LearnedItem:
    """Audit entry: what was changed in our knowledge base based on LLM
    feedback. Surfaced to the UI so the user sees what was learnt."""
    type: str       # "synonym_added" | "override_pending" | "exclusion_pending"
    detail: str
    correction_label: str = ""


@dataclass
class ReviewResult:
    """Returned to caller. Always populated even on failure — caller checks
    ``errors`` and decides whether to surface a warning."""
    filled: list[FilledField] = field(default_factory=list)
    rounds: list[RoundResult] = field(default_factory=list)
    learned: list[LearnedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "filled": [asdict(f) for f in self.filled],
            "rounds": [
                {
                    **{k: v for k, v in asdict(r).items()
                       if k not in ("corrections", "accepted")},
                    "corrections": [asdict(c) for c in r.corrections],
                    "accepted": [asdict(c) for c in r.accepted],
                }
                for r in self.rounds
            ],
            "learned": [asdict(l) for l in self.learned],
            "errors": self.errors,
            "total_elapsed_s": round(self.total_elapsed_s, 2),
        }


# ----------------------------------------------------------------- M2 --
# Overlay PNG: render a single page with our filled values drawn as red
# rectangles + small annotations so the LLM can see exactly what we did.

def _load_label_font(size_px: int) -> ImageFont.FreeTypeFont:
    """Find a CJK-capable font for the overlay annotations."""
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "C:/Windows/Fonts/msjh.ttc",
    ]
    for p in candidates:
        try:
            return ImageFont.truetype(p, size_px)
        except OSError:
            continue
    return ImageFont.load_default()


# Max dimension (px) of images sent to LLM. Vision models typically re-tile
# internally to ~768-1024 on longest side — sending larger is wasted
# bandwidth and bloats the context window. A4 @ 110 dpi ≈ 910×1287, which
# still keeps Chinese text readable; we cap at 1200 as a safety ceiling.
LLM_IMAGE_MAX_LONG_EDGE = 1200
LLM_RENDER_DPI = 110  # was 150 — cuts image size roughly in half


def _shrink_to_max(img: Image.Image, max_long: int = LLM_IMAGE_MAX_LONG_EDGE) -> Image.Image:
    """Resize ``img`` so its longest edge ≤ max_long px, preserving aspect.
    No-op if already small enough."""
    w, h = img.size
    long_edge = max(w, h)
    if long_edge <= max_long:
        return img
    scale = max_long / long_edge
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def render_raw_png(
    pdf_path: Path,
    page_index: int = 0,
    dpi: int = LLM_RENDER_DPI,
) -> bytes:
    """Render the given page to PNG with NO overlay (the "before" image).
    Capped at LLM_IMAGE_MAX_LONG_EDGE px on long side."""
    import uuid as _uuid
    tmp = Path(f"/tmp/llm_raw_{page_index}_{_uuid.uuid4().hex[:8]}.png")
    pdf_preview.render_page_png(pdf_path, tmp, page_index, dpi=dpi)
    try:
        with Image.open(tmp) as im:
            shrunk = _shrink_to_max(im.convert("RGB"))
            buf = io.BytesIO()
            shrunk.save(buf, format="PNG", optimize=True)
            return buf.getvalue()
    finally:
        try: tmp.unlink()
        except OSError: pass


def render_overlay_png(
    pdf_path: Path,
    filled_fields: list[FilledField],
    page_index: int = 0,
    dpi: int = LLM_RENDER_DPI,
) -> bytes:
    """Render the given page to PNG with MINIMAL red overlays per filled field.

    Each overlay is just a **thin red outline** around the slot — no caption,
    no fill, so the filled value is fully visible underneath. The LLM compares
    against the "before" PNG to see what changed; overlay just highlights
    *which cells* we touched, not *what we wrote* (the filled text itself
    shows the value).

    Previous versions drew a "key → value" caption above each box, but for
    forms with many fields (45+) the captions overwhelmed the image and
    made the actual filled content hard to read — hurting LLM accuracy.
    """
    # 1. Render the raw page to PNG via existing helper
    import uuid as _uuid
    tmp = Path(f"/tmp/llm_overlay_{page_index}_{_uuid.uuid4().hex[:8]}.png")
    pdf_preview.render_page_png(pdf_path, tmp, page_index, dpi=dpi)

    # 2. Open as PIL for drawing the overlay
    with Image.open(tmp) as base:
        img = base.convert("RGB")
    try:
        tmp.unlink()
    except OSError:
        pass

    # pt → px scale: the renderer used `dpi`. PDF native is 72 pt/inch.
    scale = dpi / 72.0
    draw = ImageDraw.Draw(img, "RGBA")

    fills_for_page = [f for f in filled_fields if f.page == page_index]
    for f in fills_for_page:
        x0, y0, x1, y1 = f.slot_pt
        rect = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
        # Thin red outline only — no fill, no caption. Filled text remains
        # fully readable so the LLM can assess correctness.
        draw.rectangle(rect, outline=(220, 38, 38, 230), width=1)

    # Cap size before sending — vision models auto-tile anyway; keeping
    # images below ~1200px saves tokens without losing readability.
    img = _shrink_to_max(img)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ----------------------------------------------------------------- M3 --
# Review loop. Stateless — caller passes in the already-filled fields
# (typically from pdf-fill's existing detect+fill pipeline) so this module
# doesn't have to know how values are computed.

def _parse_corrections(raw: dict, conf_threshold: float = 0.0) -> list[Correction]:
    """Parse corrections from LLM response, tolerating multiple schemas:

    Simplified (current prompt, minimal keys):
        {"label": "...", "issue": "...", "expected": "..."}

    Extended (old prompt, full keys):
        {"type": "...", "label": "...", "current_value": "...",
         "expected_value": "...", "confidence": 0.0-1.0, "reason": "..."}

    Unknown fields are ignored. Type is inferred from issue text when absent.
    Confidence defaults to 0.7 when absent (most simplified-schema items are
    real issues when the model bothers to mention them)."""
    out: list[Correction] = []
    for c in (raw.get("corrections") or []):
        if not isinstance(c, dict):
            continue
        # Confidence — new schema omits it entirely
        conf_raw = c.get("confidence")
        try:
            conf = float(conf_raw) if conf_raw is not None else 0.7
        except (TypeError, ValueError):
            conf = 0.7
        # Type — may be missing in new schema; infer from "issue" / "reason"
        ctype = str(c.get("type", "")).upper()
        reason_text = str(c.get("reason") or c.get("issue") or "")
        if ctype not in ("WRONG_PLACEMENT", "MISSING", "EXTRA"):
            low = reason_text.lower() + str(c.get("label", "")).lower()
            if "漏" in reason_text or "missing" in low or "沒填" in reason_text:
                ctype = "MISSING"
            elif "多" in reason_text or "不該" in reason_text or "extra" in low:
                ctype = "EXTRA"
            else:
                # Default: treat generic "something's wrong" as placement
                ctype = "WRONG_PLACEMENT"
        # Current + expected: accept either naming
        current_v = c.get("current_value") or c.get("current") or c.get("actual")
        expected_v = c.get("expected_value") or c.get("expected")
        out.append(Correction(
            type=ctype,
            label=str(c.get("label", "")),
            current_value=current_v,
            expected_value=expected_v,
            confidence=conf,
            reason=reason_text[:80],
        ))
    return out


def _consensus_filter(
    current: list[Correction],
    history: list[list[Correction]],
    n_required: int,
) -> list[Correction]:
    """A correction is accepted only if its key has appeared in the last
    ``n_required - 1`` rounds (so this round + n-1 prior = n total)."""
    if n_required <= 1:
        return list(current)
    if len(history) < n_required - 1:
        return []  # not enough history yet
    recent = history[-(n_required - 1):]
    out: list[Correction] = []
    for c in current:
        k = c.key()
        if all(any(p.key() == k for p in prev) for prev in recent):
            out.append(c)
    return out


def _try_learn_synonyms(
    accepted: list[Correction],
    profile_keys: list[str],
) -> list[LearnedItem]:
    """For accepted MISSING corrections whose ``label`` isn't a known synonym
    of any canonical key, try to add it as a synonym of the matching key
    (LLM tells us via ``expected_value`` which key it thinks).

    Conservative: only adds when (a) we can confidently identify the
    canonical key, (b) the label isn't already in any synonym list, and
    (c) the label is short / looks like a real label (not free text).
    """
    from .synonym_manager import synonym_manager
    from .pdf_form_detect import _normalize, _build_synonym_index, DEFAULT_LABEL_MAP

    learned: list[LearnedItem] = []
    syn_map = synonym_manager.get_map() or DEFAULT_LABEL_MAP
    idx = _build_synonym_index(syn_map)

    for c in accepted:
        if c.type != "MISSING":
            continue
        label = (c.label or "").strip()
        if not label or len(label) > 30:
            continue  # too long to be a label
        norm = _normalize(label)
        if not norm:
            continue
        if idx.get(norm):
            continue  # already a known synonym

        # Try to figure out the target canonical key. The LLM fills
        # ``expected_value`` with the suggested *value*, not the key. We can
        # match the value back to the user's profile to derive the key.
        target_key = None
        if c.expected_value:
            ev = str(c.expected_value).strip()
            for k in profile_keys:
                # cheap exact-match heuristic — keys whose value equals ev
                # in the profile (caller will verify)
                if ev and ev == k:
                    target_key = k
                    break
        if not target_key:
            # No safe inference possible; skip (admin will see the suggestion
            # in the corrections list and can manually add the synonym).
            continue

        if synonym_manager.add_synonym(target_key, label):
            learned.append(LearnedItem(
                type="synonym_added",
                detail=f"{target_key} ← 「{label}」",
                correction_label=label,
            ))
            logger.info("LLM-learned synonym: %s -> %s", target_key, label)
    return learned


def review(
    pdf_path: Path,
    filled_fields: list[FilledField],
    *,
    page_index: int = 0,
    max_rounds: Optional[int] = None,
    profile_keys: Optional[list[str]] = None,
    progress_cb=None,
) -> ReviewResult:
    """Run the LLM review loop. Returns ReviewResult with per-round details.

    ``filled_fields`` is the OUTPUT of the existing detect+compute pipeline
    (typically pdf-fill produces it). This function does not modify it; it
    only suggests corrections via the rounds[*].accepted list. The caller
    decides what to do with those corrections.

    ``profile_keys`` is the list of canonical keys the user has values for;
    used by the conservative synonym-learning step.

    ``progress_cb(round_n, total_rounds, message)`` is called before and
    after each round so an async caller can surface progress to the UI via
    the job_manager. Never raises — exceptions inside the callback are
    swallowed so a logging bug can't break the review.
    """
    result = ReviewResult(filled=list(filled_fields))
    settings = llm_settings.get()

    if not settings.get("enabled"):
        result.errors.append("LLM 校驗未啟用")
        return result

    client = llm_settings.make_client()
    if client is None:
        result.errors.append("LLM client 初始化失敗")
        return result

    rounds_n = int(max_rounds or settings.get("default_review_rounds", 2))
    rounds_n = max(1, min(rounds_n, 5))
    conf_threshold = float(settings.get("confidence_threshold", 0.8))
    n_required = int(settings.get("consecutive_required", 2))
    overall_timeout = float(settings.get("overall_timeout_seconds", 180))
    # Per-tool 覆寫優先（admin 在 LLM 設定頁可以給 pdf-fill 校驗指定模型）
    model = llm_settings.get_model_for("pdf-fill")

    deadline = time.monotonic() + overall_timeout
    started = time.monotonic()

    history: list[list[Correction]] = []
    consecutive_clear = 0

    def _notify(r_n, msg):
        if progress_cb is None:
            return
        try:
            progress_cb(r_n, rounds_n, msg)
        except Exception:  # noqa: BLE001
            pass

    _notify(0, f"準備 LLM 校驗（共 {rounds_n} 輪）")

    for r_n in range(1, rounds_n + 1):
        round_started = time.monotonic()
        if round_started > deadline:
            result.errors.append(
                f"整體 timeout ({overall_timeout:.0f}s) — 第 {r_n} 輪未執行")
            break

        _notify(r_n, f"第 {r_n}/{rounds_n} 輪：渲染 + 送 LLM 中…")
        rr = RoundResult(round=r_n, verdict="needs_correction")
        try:
            # Single image mode: just the filled "after" PNG with red outlines
            # marking our fills. Earlier two-image (before + after) mode
            # doubled tokens and made small vision models time out at 120s on
            # reasoning. Single image matches what works in OpenWebUI.
            after_png = render_overlay_png(
                pdf_path, result.filled, page_index=page_index)
            prompt = get_review_prompt(model)
            logger.info(
                "LLM review round %d: model=%s, image=%dB, prompt=%d chars",
                r_n, model, len(after_png), len(prompt),
            )
            raw = client.vision_query(after_png, prompt, model=model)
            import json as _json
            logger.info(
                "LLM review round %d: response=%s",
                r_n, _json.dumps(raw, ensure_ascii=False)[:800],
            )
        except Exception as e:  # noqa: BLE001
            rr.error = f"{type(e).__name__}: {e}"
            rr.elapsed_s = round(time.monotonic() - round_started, 2)
            result.rounds.append(rr)
            logger.warning("LLM review round %d failed: %s", r_n, e)
            continue

        verdict = str(raw.get("verdict", "needs_correction")).lower()
        rr.verdict = verdict
        rr.corrections = _parse_corrections(raw, conf_threshold)
        rr.accepted = _consensus_filter(rr.corrections, history, n_required)
        history.append(rr.corrections)
        rr.elapsed_s = round(time.monotonic() - round_started, 2)
        result.rounds.append(rr)

        # Persist accepted corrections that lead to safe knowledge updates.
        if rr.accepted and profile_keys:
            try:
                result.learned.extend(
                    _try_learn_synonyms(rr.accepted, profile_keys))
            except Exception as e:  # noqa: BLE001
                logger.warning("synonym-learning step failed: %s", e)

        _notify(r_n, (
            f"第 {r_n}/{rounds_n} 輪完成："
            f"{len(rr.corrections)} 建議 / {len(rr.accepted)} 接受"
            f"（{rr.elapsed_s:.1f}s）"
        ))

        if verdict == "all_clear" and not rr.corrections:
            consecutive_clear += 1
            if consecutive_clear >= 2:
                break
        else:
            consecutive_clear = 0

    result.total_elapsed_s = round(time.monotonic() - started, 2)
    _notify(rounds_n, f"完成（{result.total_elapsed_s:.1f}s）")
    return result

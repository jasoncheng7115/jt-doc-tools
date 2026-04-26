"""Apply LLM review corrections back to placements.

Conservative V1: only supports REMOVAL of flagged placements. Move/modify
variants (Phase 2.5) need LABEL_MAP to expose empty-cell coordinates and
are deferred.

Inputs:
  - snapshot placements (from {upload_id}_placements.json)
  - corrections list (from ReviewResult.rounds[-1].accepted)
  - which correction indices the user wants to apply

Output:
  - new placements list with flagged ones dropped
  - caller re-renders the filled PDF via pdf_text_overlay.overlay_text(...)

Match strategy: label + current_value. Simple and robust across format drift.
"""
from __future__ import annotations
from typing import Any, Optional


def filter_placements_by_corrections(
    placements: list[dict],
    corrections: list[dict],
    apply_indices: Optional[list[int]] = None,
    profile_labels: Optional[dict] = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (kept_placements, removed_placements, moved_placements).

    For each correction:
      - If ``suggested_slot_pt`` is present AND slot_pt match uniquely
        identifies the source placement → MOVE the placement (update its
        slot_pt in ``kept`` list, don't add to ``removed``).
      - Else if slot_pt matches → REMOVE (delete the placement).
      - Fallback: match by (label, value), one-shot each.

    ``corrections`` is the ``accepted`` list from a ReviewResult round.
    If ``apply_indices`` is ``None`` all corrections apply; otherwise only
    those listed.
    """
    profile_labels = profile_labels or {}
    targets = corrections if apply_indices is None else [
        corrections[i] for i in apply_indices if 0 <= i < len(corrections)
    ]
    kept: list[dict] = []
    removed: list[dict] = []
    moved: list[dict] = []

    # slot_pt → (action, suggested_slot_pt)  action in {"move", "remove"}
    slot_actions: dict[tuple, tuple[str, Optional[tuple]]] = {}
    label_val_kills: list[tuple[str, str]] = []  # fallback — consumed once each
    for c in targets:
        slot = c.get("slot_pt")
        sug = c.get("suggested_slot_pt")
        if slot and len(slot) == 4:
            key = tuple(round(float(x), 3) for x in slot)
            if sug and len(sug) == 4:
                slot_actions[key] = ("move", tuple(float(x) for x in sug))
            else:
                slot_actions[key] = ("remove", None)
        else:
            label_val_kills.append((
                (c.get("label") or "").strip(),
                (c.get("current_value") or "").strip(),
            ))
    consumed_fallback: set[int] = set()

    # Build occupancy set of all OTHER placements' slots so we can skip a
    # move that would collide with an existing value.
    other_slots: set[tuple] = {
        tuple(round(float(x), 3) for x in (q.get("slot_pt") or [0,0,0,0]))
        for q in placements
    }

    for p in placements:
        p_slot = tuple(round(float(x), 3) for x in (p.get("slot_pt") or [0,0,0,0]))
        # 1) Exact slot match → move or remove
        if p_slot in slot_actions:
            action, target = slot_actions[p_slot]
            target_key = (tuple(round(float(x), 3) for x in target)
                          if target else None)
            if action == "move" and target and target_key not in other_slots:
                moved_p = dict(p)
                moved_p["slot_pt"] = list(target)
                moved_p["_moved_from"] = list(p.get("slot_pt") or [])
                kept.append(moved_p)
                moved.append(moved_p)
            else:
                # Either pure remove, or move would overwrite → remove-only
                removed.append(p)
            continue
        # 2) Fallback: (label, value) — one-shot only, remove only
        p_value = (p.get("text") or "").strip()
        p_labels = {
            (p.get("label_text") or "").strip(),
            (p.get("profile_key") or "").strip(),
            (profile_labels.get(p.get("profile_key") or "", "")).strip(),
        }
        p_labels.discard("")
        matched = False
        for ci, (c_label, c_value) in enumerate(label_val_kills):
            if ci in consumed_fallback:
                continue
            if c_label in p_labels and p_value == c_value:
                consumed_fallback.add(ci)
                removed.append(p)
                matched = True
                break
        if not matched:
            kept.append(p)
    # Third return value: which placements were moved (subset of kept with
    # _moved_from key). Caller can surface "A → B" diff to UI.
    return kept, removed, moved  # type: ignore[return-value]


def placements_to_text_placements(placements: list[dict]):
    """Convert snapshot dict-format placements back into
    pdf_text_overlay.TextPlacement objects for re-rendering (all pages)."""
    from . import pdf_text_overlay
    out = []
    for p in placements:
        out.append(pdf_text_overlay.TextPlacement(
            page=int(p.get("page", 0)),
            text=p.get("text") or "",
            slot=tuple(p.get("slot_pt") or (0, 0, 0, 0)),
            base_font_size=float(p.get("base_font_size") or 12.0),
            source_key=p.get("profile_key") or "",
            kind=p.get("kind") or "text",
            option_text=p.get("option_text") or "",
        ))
    return out

"""Detect labels in a PDF and overlay matching profile values.

Pure function-style API so the router stays thin and the engine can be
exercised from a script for debugging.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ...core import pdf_checkbox, pdf_form_detect, pdf_layout, pdf_text_overlay, template_manager as _tm


_LIST_SEPARATORS = re.compile(r"[,、，/／;；|]")


@dataclass
class FillReport:
    detected_count: int                       # number of label spans matched
    filled_count: int                         # number of placements actually written
    matched_keys: dict[str, int]              # canonical key -> # occurrences
    unfilled_keys: list[str]                  # keys detected in PDF but with empty profile value
    placements: list[pdf_text_overlay.TextPlacement]
    checked_boxes: list[tuple[str, str]] = field(default_factory=list)
    # (profile_key, option_text) — for the report UI.
    fingerprint: str = ""
    applied_template: Optional[dict] = None   # template dict if we used one


def fill_pdf(
    src_pdf: Path,
    dst_pdf: Path,
    profile_fields: dict[str, str],
    overrides: Optional[dict[str, str]] = None,
    font_id: str = "auto",
) -> FillReport:
    """Detect labels in ``src_pdf`` and write the matching values from
    ``profile_fields`` (an arbitrary key->value map) into ``dst_pdf``.

    ``overrides`` lets the caller supply per-job values that take precedence
    over the stored profile (e.g. when the user tweaks a field on the
    review page before generating).
    """
    profile = dict(profile_fields)
    if overrides:
        profile.update({k: v for k, v in overrides.items() if v is not None})

    fingerprint = _tm.compute_fingerprint(src_pdf)
    template = _tm.template_manager.get_by_fingerprint(fingerprint)
    if template:
        return _fill_from_template(
            src_pdf, dst_pdf, profile, template, fingerprint, font_id
        )

    detected, _pages = pdf_form_detect.detect_fields(src_pdf)
    all_checkboxes = pdf_checkbox.extract_checkboxes(src_pdf)
    blank_clusters = pdf_checkbox.extract_blank_box_clusters(src_pdf)
    cells_per_page = pdf_layout.extract_cells_pdfplumber(src_pdf)
    digit_clusters_per_page: list[list[list[pdf_layout.Cell]]] = [
        pdf_layout.extract_digit_box_clusters(cells) for cells in cells_per_page
    ]

    placements: list[pdf_text_overlay.TextPlacement] = []
    checked: list[tuple[str, str]] = []
    unfilled: set[str] = set()
    # Track which checkbox labels we've already consumed so duplicate detections
    # (e.g. "付款方式" appears both as a real label and inside a notes row)
    # don't drop repeated ✓ marks on the same box.
    ticked_box_ids: set[tuple[int, int, int]] = set()
    # Track slots that already received text so two profile keys sharing a
    # bilingual label (e.g. "收款銀行 / Beneficiary Bank" mapped to both
    # bank_name and beneficiary_bank) don't stack their values on the same cell.
    # Maps (page, round(x0), round(y0)) -> index into placements list so we
    # can replace if a better (Chinese) candidate comes along later.
    used_text_slots: dict[tuple[int, int, int], int] = {}

    # Explicit Chinese↔English sibling mapping. Keys here are English-variant
    # profile keys; values are their Chinese counterparts. When a form has a
    # compound bilingual cell (single visual cell with 中文 label on one line,
    # English label on the next), both get detected at overlapping slots;
    # we prefer the Chinese sibling and skip the English one.
    ENGLISH_SIBLING_OF = {
        "english_name":          "company_name",
        "english_short_name":    "short_name",
        "english_address":       "address",
        "beneficiary_bank":      "bank_name",
        "beneficiary_bank_address": "bank_address",
        "payee_en":              "bank_account_name",
        "payee_address_en":      "bank_address",
        "owner_en":              "owner",
        "owner_title_en":        "owner_title_zh",
        "foreign_account_no":    "bank_account_no",
    }

    def _is_english_variant(key: str, val: str) -> bool:
        """Is this profile_key the English sibling of a Chinese equivalent?"""
        lk = (key or "").lower()
        if lk in ENGLISH_SIBLING_OF:
            return True
        if lk.startswith("english_") or lk.endswith("_en"):
            return True
        # Value is pure ASCII (no CJK) → likely English content
        if val and not any("一" <= c <= "鿿" for c in val):
            return True
        return False

    def _bbox_strict_overlap(a, b) -> bool:
        """True only if intersection has positive area — touching edges don't
        count. Use for cross-key conflicts (different label rows that abut
        should each keep their own placement)."""
        return a[2] > b[0] and b[2] > a[0] and a[3] > b[1] and b[3] > a[1]

    def _bbox_touch_overlap(a, b) -> bool:
        """True when bounding boxes touch or intersect. Use for same-key
        same-value dedup (LABEL_MAP bleeding one value into adjacent rows)."""
        return a[2] >= b[0] and b[2] >= a[0] and a[3] >= b[1] and b[3] >= a[1]

    def _y_overlap(a, b) -> bool:
        """Strict vertical overlap (positive intersection height)."""
        return a[3] > b[1] and b[3] > a[1]

    def _slot_area(s) -> float:
        return max(0.0, (s[2] - s[0])) * max(0.0, (s[3] - s[1]))

    # Order detected fields so Chinese-variant candidates are processed first;
    # any later English variant hitting the same slot will simply be skipped.
    detected = sorted(
        detected,
        key=lambda d: (0 if not _is_english_variant(d.profile_key,
                                                   profile.get(d.profile_key, "")) else 1),
    )

    for d in detected:
        value = profile.get(d.profile_key, "")
        if not value:
            unfilled.add(d.profile_key)
            continue

        # Universal checkbox-first: try to match profile value (including each
        # comma-separated sub-value) against any printed □ near the label. If
        # anything matches, tick those boxes and skip text placement.
        near = pdf_checkbox.options_near_label(
            all_checkboxes, d.label_rect, d.page, d.value_slot
        )
        if near:
            sub_values = [
                v.strip() for v in _LIST_SEPARATORS.split(value) if v.strip()
            ] or [value]
            matched_here: list[pdf_checkbox.CheckboxOption] = []
            seen_texts: set[str] = set()
            for v in sub_values:
                m = pdf_checkbox.match_option(near, v)
                if m and m.text not in seen_texts:
                    matched_here.append(m)
                    seen_texts.add(m.text)
            # Only skip text placement when the value_slot *itself* overlaps
            # ≥2 checkboxes AND the value is too short / keyword-like to be
            # useful as free text. The broader `near` set can catch boxes
            # from neighbouring rows (e.g. "員工人數" options bleeding into
            # 負責人's label area), so we restrict the check to the actual
            # target slot. This keeps number-bearing values (account
            # numbers) working next to "□支存 □活存 ___" while preventing
            # "電匯、匯款" from being painted over "□月結三十天 …".
            if not matched_here and d.value_slot is not None:
                sx0, sy0, sx1, sy1 = d.value_slot
                in_slot = [
                    o for o in near
                    if sx0 - 2 <= o.box_rect[0] <= sx1 + 2
                    and sy0 - 2 <= o.box_rect[1] <= sy1 + 2
                ]
                looks_like_keyword = (
                    len(value) <= 10
                    and not any(ch.isdigit() for ch in value)
                )
                if len(in_slot) >= 2 and looks_like_keyword:
                    unfilled.add(d.profile_key)
                    continue
            if matched_here:
                for opt in matched_here:
                    key = (opt.page, int(opt.box_rect[0]), int(opt.box_rect[1]))
                    if key in ticked_box_ids:
                        continue
                    ticked_box_ids.add(key)
                    bx0, by0, bx1, by1 = opt.box_rect
                    placements.append(
                        pdf_text_overlay.TextPlacement(
                            page=opt.page,
                            text="✓",
                            slot=(bx0, by0, bx1, by1),
                            base_font_size=opt.size,
                            min_font_size=opt.size * 0.8,
                            align="center",
                            source_key=d.profile_key,
                            kind="check",
                            option_text=opt.text,
                        )
                    )
                    checked.append((d.profile_key, opt.text))
                continue

        if d.slot_occupied:
            # Something is already printed inside the target slot (pre-filled
            # example or carry-over from a prior fill). Don't overwrite.
            continue
        slot = d.value_slot or (
            d.value_anchor[0], d.label_rect[1], d.value_anchor[0] + 240, d.label_rect[3]
        )

        # Fix A (same-key Y-overlap → prefer larger slot): when two detections
        # for the SAME profile_key have vertically-overlapping slots, it's
        # almost always LABEL_MAP matching TWO spans on the same row (e.g.
        # both "(英)" and "(English)" labels, producing a wide main slot AND
        # a tiny spurious narrow slot). Keep whichever has larger area.
        skip_this = False
        replace_idx = None
        for idx, p in enumerate(placements):
            if (p.source_key == d.profile_key
                and p.page == d.page
                and p.kind == "text"
                and _y_overlap(p.slot, slot)):
                if _slot_area(p.slot) >= _slot_area(slot):
                    skip_this = True   # existing is at least as big — drop new
                    break
                else:
                    replace_idx = idx  # new is bigger — replace existing
                    break
        if skip_this:
            continue
        if replace_idx is not None:
            placements.pop(replace_idx)
            # also drop stale slot_key entry if any
            used_text_slots = {k: v for k, v in used_text_slots.items()
                               if v != replace_idx}

        # Fix B (Chinese-sibling conflict): if this detection is an English
        # variant (e.g. beneficiary_bank, payee_en) and its slot has STRICT
        # positive-area overlap with an earlier placement using the Chinese
        # sibling key (bank_name, bank_account_name), this is a compound
        # bilingual cell — the Chinese fill is authoritative; skip the
        # English one. (Strict overlap, not touching — adjacent separate
        # rows like 公司名稱/英文名 row remain independent.)
        cn_sibling = ENGLISH_SIBLING_OF.get(d.profile_key.lower())
        if cn_sibling:
            conflict = any(
                p.source_key == cn_sibling and p.page == d.page
                and _bbox_strict_overlap(p.slot, slot)
                for p in placements
            )
            if conflict:
                continue

        # Bilingual-label dedup: if an earlier detection already chose this
        # slot for a Chinese variant, skip the English sibling. Because we
        # pre-sorted Chinese first, by the time we get here with an English
        # variant the slot is already owned.
        slot_key = (d.page, int(round(slot[0])), int(round(slot[1])))
        if slot_key in used_text_slots:
            continue
        # Same-key overlap dedup: if an earlier placement has the SAME
        # profile_key/value AND its slot BOUNDING-BOX intersects (not just
        # close), this is LABEL_MAP mapping the same value into two cells
        # that touch (e.g. company_name hitting Chinese-row y=163-183 AND
        # English-row y=183-204 because the row-header was matched twice).
        # Different rows with gaps (e.g. owner on 姓名 row + 簽名 row) are
        # legit and stay.
        for p in placements:
            if (p.source_key == d.profile_key
                and p.text == value
                and p.page == d.page
                and p.kind == "text"):
                pxa, pya, pxb, pyb = p.slot
                sxa, sya, sxb, syb = slot
                # Require BOTH horizontal and vertical overlap (touching OK)
                if pxb >= sxa and sxb >= pxa and pyb >= sya and syb >= pya:
                    break
        else:
            used_text_slots[slot_key] = len(placements)
            placements.append(
                pdf_text_overlay.TextPlacement(
                    page=d.page,
                    text=value,
                    slot=slot,
                    base_font_size=max(9.0, min(13.0, d.font_size)),
                    min_font_size=7.0,
                    source_key=d.profile_key,
                    kind="text",
                )
            )
            continue
        # fell through the for-break: duplicate found, skip
        continue

    # Zip-code digit clusters: 3~6 empty □ boxes printed in a row above or
    # inside the 登記/聯絡地址 cell. Distribute the zip_code value digit by
    # digit, left-aligned — leaving any extra boxes empty.
    zip_value = (profile.get("zip_code") or "").strip()
    if zip_value and blank_clusters:
        digits = [c for c in zip_value if c.isdigit()]
        if digits:
            # Which slots are address targets? Any detected field whose key is
            # "address" (or 聯絡地址) with a non-empty profile value.
            addr_slots = [
                d.value_slot for d in detected
                if d.profile_key == "address" and d.value_slot is not None
                and (profile.get(d.profile_key) or "")
            ]
            used_cluster_ids: set[int] = set()
            for addr in addr_slots:
                ax0, ay0, ax1, ay1 = addr
                # Find the nearest cluster whose centre-x sits inside the
                # address slot horizontally AND whose y is within ±30pt of
                # the slot's top (zip boxes usually sit just above the
                # address text, inside the same cell).
                best: list[CheckboxOption] | None = None
                best_dy: float = 1e9
                for cluster in blank_clusters:
                    if id(cluster) in used_cluster_ids:
                        continue
                    if not cluster:
                        continue
                    cx = (cluster[0].box_rect[0] + cluster[-1].box_rect[2]) / 2
                    cy = (cluster[0].box_rect[1] + cluster[-1].box_rect[3]) / 2
                    if not (ax0 - 4 <= cx <= ax1 + 4):
                        continue
                    # Cluster should be at-or-above the slot's centre by some margin.
                    ref_y = (ay0 + ay1) / 2
                    dy = abs(cy - ref_y)
                    if dy > 40:
                        continue
                    if dy < best_dy:
                        best = cluster
                        best_dy = dy
                if best is None:
                    continue
                used_cluster_ids.add(id(best))
                for i, opt in enumerate(best):
                    if i >= len(digits):
                        break
                    bx0, by0, bx1, by1 = opt.box_rect
                    placements.append(pdf_text_overlay.TextPlacement(
                        page=opt.page, text=digits[i],
                        slot=(bx0, by0, bx1, by1),
                        base_font_size=opt.size,
                        min_font_size=opt.size * 0.7,
                        align="center",
                        source_key="zip_code", kind="text",
                    ))

    # Digit-box grid for 受款帳號 / bank_account_no — drawn rectangles that
    # hold one character each. When a bank_account_no field is detected near
    # such a grid, distribute the account number digit-by-digit and remove
    # the single-string placement that would otherwise overlap.
    account_value = (profile.get("bank_account_no") or "").strip()
    if account_value and any(digit_clusters_per_page):
        account_chars = [c for c in account_value if c.isdigit() or c == "-"]
        account_chars = [c for c in account_chars if c.isdigit()]
        account_slots = [
            d for d in detected
            if d.profile_key == "bank_account_no" and d.value_slot is not None
        ]
        used_grid_ids: set[int] = set()
        consumed_placement_ids: set[int] = set()
        for d in account_slots:
            sx0, sy0, sx1, sy1 = d.value_slot
            best_grid = None
            best_dy = 1e9
            for grid in digit_clusters_per_page[d.page]:
                gx0 = grid[0][0]
                gx1 = grid[-1][2]
                gy0 = grid[0][1]
                gy1 = grid[0][3]
                if id(grid) in used_grid_ids:
                    continue
                # Grid's horizontal span should intersect the slot; grid y
                # must be within a few rows of the label.
                if gx1 < sx0 - 4 or gx0 > sx1 + 4:
                    continue
                gcy = (gy0 + gy1) / 2
                scy = (sy0 + sy1) / 2
                dy = abs(gcy - scy)
                # Grid typically sits in the same cell or the row just below
                # the label — tolerate up to ~80pt.
                if dy > 80:
                    continue
                if dy < best_dy:
                    best_grid = grid
                    best_dy = dy
            if best_grid is None:
                continue
            used_grid_ids.add(id(best_grid))
            # Drop the plain text placement for this field so the digit
            # placements don't double-overlay.
            for i, p in enumerate(placements):
                if (
                    p.source_key == d.profile_key
                    and p.page == d.page
                    and p.slot == d.value_slot
                ):
                    consumed_placement_ids.add(i)
            # Right-align by default — account numbers typically fill from
            # the right in Taiwanese bank forms.
            digits = account_chars[:len(best_grid)]
            start_idx = len(best_grid) - len(digits)
            for i, ch in enumerate(digits):
                bx0, by0, bx1, by1 = best_grid[start_idx + i]
                placements.append(pdf_text_overlay.TextPlacement(
                    page=d.page, text=ch,
                    slot=(bx0, by0, bx1, by1),
                    base_font_size=11.0,
                    min_font_size=7.0,
                    align="center",
                    source_key="bank_account_no", kind="text",
                ))
        if consumed_placement_ids:
            placements = [p for i, p in enumerate(placements) if i not in consumed_placement_ids]

    pdf_text_overlay.overlay_text(src_pdf, dst_pdf, placements, font_id=font_id)

    return FillReport(
        detected_count=len(detected),
        filled_count=len(placements),
        matched_keys=pdf_form_detect.summarize(detected),
        unfilled_keys=sorted(unfilled),
        placements=placements,
        checked_boxes=checked,
        fingerprint=fingerprint,
        applied_template=None,
    )


def _fill_from_template(
    src_pdf: Path,
    dst_pdf: Path,
    profile: dict[str, str],
    template: dict,
    fingerprint: str,
    font_id: str,
) -> FillReport:
    """Apply a saved template: write profile values at the recorded slots
    and tick checkboxes whose stored option text matches the profile value.
    """
    placements: list[pdf_text_overlay.TextPlacement] = []
    checked: list[tuple[str, str]] = []
    unfilled: set[str] = set()
    filled_count = 0
    used_keys: dict[str, int] = {}

    for f in template.get("fields", []):
        key = f.get("profile_key", "")
        used_keys[key] = used_keys.get(key, 0) + 1
        value = profile.get(key, "")
        if not value:
            unfilled.add(key)
            continue
        slot = tuple(f["slot"])
        placements.append(
            pdf_text_overlay.TextPlacement(
                page=f.get("page", 0),
                text=value,
                slot=slot,
                base_font_size=f.get("base_font_size", 11.0),
                min_font_size=7.0,
                source_key=key,
                kind="text",
            )
        )
        filled_count += 1

    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", (s or "")).lower()

    for cb in template.get("checkboxes", []):
        key = cb.get("profile_key", "")
        used_keys[key] = used_keys.get(key, 0) + 1
        value = profile.get(key, "")
        if not value:
            unfilled.add(key)
            continue
        sub_values = [
            v.strip() for v in _LIST_SEPARATORS.split(value) if v.strip()
        ] or [value]
        opt_norm = _norm(cb.get("option_text", ""))
        for sv in sub_values:
            nv = _norm(sv)
            if nv == opt_norm or opt_norm in nv or nv in opt_norm:
                bx = tuple(cb["box"])
                size = cb.get("size", 10.0)
                placements.append(
                    pdf_text_overlay.TextPlacement(
                        page=cb.get("page", 0),
                        text="✓",
                        slot=bx,
                        base_font_size=size,
                        min_font_size=size * 0.8,
                        align="center",
                        source_key=key,
                        kind="check",
                        option_text=cb.get("option_text", ""),
                    )
                )
                checked.append((key, cb.get("option_text", "")))
                filled_count += 1
                break

    pdf_text_overlay.overlay_text(src_pdf, dst_pdf, placements, font_id=font_id)

    return FillReport(
        detected_count=len(template.get("fields", [])) + len(template.get("checkboxes", [])),
        filled_count=filled_count,
        matched_keys=used_keys,
        unfilled_keys=sorted(unfilled),
        placements=placements,
        checked_boxes=checked,
        fingerprint=fingerprint,
        applied_template={"id": template["id"], "name": template.get("name", "")},
    )


def save_template_from_placements(
    src_pdf: Path, name: str, placements: list[dict],
    existing_id: Optional[str] = None,
) -> dict:
    """Save a template from client-supplied placements (after drag edits)."""
    fingerprint = _tm.compute_fingerprint(src_pdf)
    import fitz as _fitz
    with _fitz.open(str(src_pdf)) as doc:
        page_count = doc.page_count
    fields: list[dict] = []
    checkboxes: list[dict] = []
    for p in placements:
        slot = list(p.get("slot_pt") or [])
        if len(slot) != 4:
            continue
        kind = p.get("kind", "text")
        key = p.get("source_key") or p.get("profile_key") or ""
        if not key:
            continue
        if kind == "check":
            checkboxes.append({
                "profile_key": key,
                "option_text": p.get("option_text") or p.get("text") or "",
                "page": int(p.get("page", 0)),
                "box": slot,
                "size": float(p.get("base_font_size", 10.0)),
            })
        else:
            fields.append({
                "profile_key": key,
                "page": int(p.get("page", 0)),
                "slot": slot,
                "base_font_size": float(p.get("base_font_size", 11.0)),
            })
    return _tm.template_manager.save(
        name=name, fingerprint=fingerprint, page_count=page_count,
        fields=fields, checkboxes=checkboxes, tid=existing_id,
    )


def save_template_from_detection(
    src_pdf: Path,
    name: str,
    profile_fields: dict[str, str],
    existing_id: Optional[str] = None,
) -> dict:
    """Run detection on ``src_pdf`` and freeze the positions into a template."""
    fingerprint = _tm.compute_fingerprint(src_pdf)
    detected, pages = pdf_form_detect.detect_fields(src_pdf)
    all_checkboxes = pdf_checkbox.extract_checkboxes(src_pdf)

    fields: list[dict] = []
    checkboxes: list[dict] = []
    ticked_ids: set[tuple[int, int, int]] = set()

    for d in detected:
        value = profile_fields.get(d.profile_key, "")
        # Try checkbox first (same as fill_pdf)
        near = pdf_checkbox.options_near_label(
            all_checkboxes, d.label_rect, d.page, d.value_slot
        )
        matched_any = False
        if value and near:
            sub_values = [v.strip() for v in _LIST_SEPARATORS.split(value) if v.strip()] or [value]
            seen_txt: set[str] = set()
            for v in sub_values:
                m = pdf_checkbox.match_option(near, v)
                if not m or m.text in seen_txt:
                    continue
                seen_txt.add(m.text)
                key = (m.page, int(m.box_rect[0]), int(m.box_rect[1]))
                if key in ticked_ids:
                    continue
                ticked_ids.add(key)
                checkboxes.append({
                    "profile_key": d.profile_key,
                    "option_text": m.text,
                    "page": m.page,
                    "box": list(m.box_rect),
                    "size": m.size,
                })
                matched_any = True
        if matched_any:
            continue
        if d.value_slot is None:
            continue
        fields.append({
            "profile_key": d.profile_key,
            "page": d.page,
            "slot": list(d.value_slot),
            "base_font_size": max(9.0, min(13.0, d.font_size)),
        })

    return _tm.template_manager.save(
        name=name,
        fingerprint=fingerprint,
        page_count=len(pages),
        fields=fields,
        checkboxes=checkboxes,
        tid=existing_id,
    )

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from fastapi import Form

from ...config import settings
from ...core import office_convert, pdf_form_detect, pdf_preview, pdf_text_overlay
from ...core.job_manager import job_manager
from ...core.profile_manager import profile_manager
from ...core.history_manager import history_manager
from ...core.synonym_manager import synonym_manager
from ...core.template_manager import template_manager
from . import service


def _write_upload_as_pdf(data: bytes, filename: str, dst_pdf: Path) -> None:
    """Write ``data`` to ``dst_pdf`` after converting Office formats if needed."""
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        dst_pdf.write_bytes(data)
        return
    if office_convert.is_office_file(filename):
        # Write the original upload alongside, then convert.
        raw = dst_pdf.with_suffix(ext)
        raw.write_bytes(data)
        try:
            office_convert.convert_to_pdf(raw, dst_pdf)
        except RuntimeError as e:
            raise HTTPException(400, str(e))
        finally:
            try:
                raw.unlink()
            except OSError:
                pass
        return
    raise HTTPException(400, f"不支援的檔案格式：{ext}")

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, cid: Optional[str] = None):
    templates = request.app.state.templates
    companies = profile_manager.list_companies()
    selected_id = cid or profile_manager.active_id()
    profile = profile_manager.get(selected_id)
    sections = profile_manager.get_sectioned(selected_id)
    # Check whether the LLM add-on is configured (just to decide if the
    # toggle is shown). Wrapped so a broken settings file never breaks the
    # main pdf-fill page.
    llm_enabled = False
    try:
        from ...core.llm_settings import llm_settings
        llm_enabled = llm_settings.is_enabled()
    except Exception:  # noqa: BLE001
        pass
    return templates.TemplateResponse(
        "pdf_fill.html",
        {
            "request": request,
            "profile": profile,
            "sections": sections,
            "companies": companies,
            "selected_id": selected_id,
            "fonts": pdf_text_overlay.list_fonts(),
            "llm_enabled": llm_enabled,
        },
    )


@router.post("/preview")
async def preview(
    request: Request,
    file: UploadFile = File(...),
    font_id: str = Form("auto"),
    company_id: str = Form(""),
    use_llm_review: bool = Form(False),  # retained for backward compat; LLM now always async via /llm-review-start
    llm_max_rounds: int = Form(0),       # unused here; kept for API compat
):
    """Run detection + fill on the uploaded PDF and return a PNG of page 1
    plus a fill report so the user can sanity-check before downloading.
    """
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    # 寫入 owner 紀錄供 /preview/{name} 與 /download/{upload_id} ACL 使用
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    filled = settings.temp_dir / f"{upload_id}_filled.pdf"
    png = settings.temp_dir / f"{upload_id}_p1.png"
    try:
        _write_upload_as_pdf(data, file.filename, src)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    profile = profile_manager.get(company_id or None)
    report = service.fill_pdf(src, filled, profile["fields"], font_id=font_id)
    preview_dpi = 120
    # Render every page — both the filled and the raw (edit-mode) versions.
    import fitz as _fitz
    with _fitz.open(str(filled)) as _doc:
        total_pages = _doc.page_count
        pages_info = [
            {"width_pt": _doc[i].rect.width, "height_pt": _doc[i].rect.height}
            for i in range(total_pages)
        ]
    for i in range(total_pages):
        pdf_preview.render_page_png(
            filled, settings.temp_dir / f"{upload_id}_p{i+1}.png", i, dpi=preview_dpi
        )
        pdf_preview.render_page_png(
            src, settings.temp_dir / f"{upload_id}_raw_p{i+1}.png", i, dpi=preview_dpi
        )
    png = settings.temp_dir / f"{upload_id}_p1.png"
    raw_png = settings.temp_dir / f"{upload_id}_raw_p1.png"
    unmatched = pdf_form_detect.find_unmatched_candidates(src)

    # 寫一筆 fill_history（admin 可在 /admin/history/fill 看到）。
    # best-effort — 任何錯誤都不能擋 user request。
    try:
        from ...core import sessions as _sessions
        actor = _sessions.user_label(getattr(request.state, "user", None))
        history_manager.save(
            original_path=src,
            filled_path=filled,
            preview_path=png,
            original_filename=file.filename or "uploaded.pdf",
            template_id=(report.applied_template or {}).get("id") if report.applied_template else None,
            template_name=(report.applied_template or {}).get("name") if report.applied_template else None,
            company_id=company_id or None,
            username=actor or "",
            report={
                "detected": report.detected_count,
                "filled": report.filled_count,
                "checked": [{"key": k, "option": o} for k, o in report.checked_boxes],
                "fingerprint": report.fingerprint,
            },
        )
    except Exception:
        import logging as _lg
        _lg.getLogger(__name__).exception("fill history save failed")

    # Expose every placement on every page.
    page_placements = [
        {
            "i": i,
            "profile_key": pl.source_key,
            "text": pl.text,
            "slot_pt": list(pl.slot),
            "kind": pl.kind,
            "option_text": pl.option_text,
            "base_font_size": pl.base_font_size,
            "page": pl.page,
        }
        for i, pl in enumerate(report.placements)
    ]

    labels = profile["labels"]
    response = {
        "upload_id": upload_id,
        "preview_url": f"/tools/pdf-fill/preview/{png.name}",
        "raw_preview_url": f"/tools/pdf-fill/preview/{raw_png.name}",
        "filename": file.filename,
        "report": {
            "detected": report.detected_count,
            "filled": report.filled_count,
            "matched": [
                {"key": k, "label": labels.get(k, k), "count": n}
                for k, n in sorted(report.matched_keys.items())
            ],
            "unfilled": [
                {"key": k, "label": labels.get(k, k)}
                for k in report.unfilled_keys
            ],
            "checked_boxes": [
                {"key": k, "label": labels.get(k, k), "option": opt}
                for k, opt in report.checked_boxes
            ],
            "unmatched_labels": unmatched,
            "applied_template": report.applied_template,
            "fingerprint": report.fingerprint,
        },
        "upload_id": upload_id,
        "page": {
            "width_pt": pages_info[0]["width_pt"] if pages_info else 595,
            "height_pt": pages_info[0]["height_pt"] if pages_info else 842,
            "dpi": preview_dpi,
        },
        "pages": [
            {
                "index": i,
                "width_pt": pg["width_pt"],
                "height_pt": pg["height_pt"],
                "preview_url": f"/tools/pdf-fill/preview/{upload_id}_p{i+1}.png",
                "raw_preview_url": f"/tools/pdf-fill/preview/{upload_id}_raw_p{i+1}.png",
            }
            for i, pg in enumerate(pages_info)
        ],
        "placements": page_placements,
        "profile_keys": [
            {"key": k, "label": labels.get(k, k)} for k in profile["fields"].keys()
        ],
        "profile_values": dict(profile["fields"]),
        "profile_sections": [
            {
                "title": sec["title"],
                "keys": [r["key"] for r in sec["rows"]],
            }
            for sec in profile_manager.get_sections_for_edit(company_id or None)
        ],
        # LLM review is now separate: start via /llm-review-start to get a
        # job_id, poll /api/jobs/{id}, fetch result via /llm-review-result.
        # We always return null here — the client decides whether to start.
        "llm_review": None,
    }
    # Save minimal snapshot for async LLM review (needs src PDF + placements).
    # src PDF is already on disk as {upload_id}_in.pdf; save placements too.
    try:
        import json as _json
        # For Phase 2.5 auto-move we also need the *empty* cell coords that
        # LABEL_MAP detected but didn't fill. Re-run detection here (cheap —
        # already cached by PyMuPDF) to grab them.
        from ...core.pdf_form_detect import detect_fields as _detect_fields
        try:
            detected, _ = _detect_fields(src)
            detected_cells = [
                {
                    "page": d.page,
                    "profile_key": d.profile_key,
                    "label_text": d.label_text,
                    "value_slot": list(d.value_slot) if d.value_slot else None,
                    "slot_occupied": d.slot_occupied,
                }
                for d in detected if d.value_slot
            ]
        except Exception:
            detected_cells = []
        (settings.temp_dir / f"{upload_id}_placements.json").write_text(
            _json.dumps({
                "company_id": company_id or "",
                "placements": page_placements,
                "profile_labels": labels,
                "profile_keys": list(profile["fields"].keys()),
                "detected_cells": detected_cells,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return response


# ---------------- Async LLM review (附加功能) -----------------------------

@router.post("/llm-review-start")
async def llm_review_start(
    upload_id: str = Form(...),
    max_rounds: int = Form(0),
):
    """Start an async LLM review job for a previously-previewed upload.
    Returns {"job_id": "..."} — client polls /api/jobs/{id} and fetches
    the final result via /tools/pdf-fill/llm-review-result/{job_id}.

    Requires the src PDF and placements JSON to still be in temp_dir from
    a recent /preview call (they are until cleanup / restart)."""
    import json as _json
    from ...core.llm_settings import llm_settings
    from ...core.llm_review import filled_from_placements, FilledField
    from ...core.llm_review_per_field import per_field_review as review

    if not llm_settings.is_enabled():
        raise HTTPException(400, "LLM 校驗未啟用（請至 /admin/llm-settings）")

    # per-field review reads values from the FILLED PDF (not src) — the crop
    # tile must contain the text we placed for the LLM to OCR.
    src = settings.temp_dir / f"{upload_id}_filled.pdf"
    snap = settings.temp_dir / f"{upload_id}_placements.json"
    if not src.exists() or not snap.exists():
        raise HTTPException(404, "找不到上傳 / 無法做校驗，請重新上傳")

    try:
        snapshot = _json.loads(snap.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"placements JSON 解析失敗：{e}")

    # Reconstruct FilledField from saved placements (format from /preview).
    filled: list[FilledField] = []
    for p in snapshot.get("placements", []):
        txt = (p.get("text") or "").strip()
        if not txt:
            continue
        key = p.get("profile_key") or p.get("source_key") or ""
        label = snapshot.get("profile_labels", {}).get(key, key)
        filled.append(FilledField(
            page=int(p.get("page", 0)),
            profile_key=key,
            label_text=label,
            value=txt,
            slot_pt=tuple(p.get("slot_pt") or (0, 0, 0, 0)),
        ))

    profile_keys = list(snapshot.get("profile_keys") or [])

    # Build candidate labels + label→target-slot mapping for Q5 (semantic
    # placement) and Phase 2.5 auto-move. Prefer cells that are currently
    # EMPTY (slot_occupied=False) so we suggest moving values into unfilled
    # cells rather than overwriting existing ones.
    seen_lbl: set[str] = set()
    candidate_labels: list[str] = []
    label_to_slot: dict[str, tuple] = {}
    for d in snapshot.get("detected_cells", []):
        lbl = (d.get("label_text") or "").strip()
        slot = d.get("value_slot")
        if not lbl or not slot:
            continue
        if d.get("slot_occupied"):
            continue  # already filled — not a candidate destination
        if lbl not in seen_lbl:
            seen_lbl.add(lbl)
            candidate_labels.append(lbl)
            label_to_slot[lbl] = tuple(slot)
    # Fall back: include labels from currently-filled placements so Q5 has
    # context even on forms where detection pass missed an empty cell.
    for p in snapshot.get("placements", []):
        lbl = (p.get("label_text") or "").strip() or \
              snapshot.get("profile_labels", {}).get(p.get("profile_key", ""), "").strip()
        if lbl and lbl not in seen_lbl:
            seen_lbl.add(lbl)
            candidate_labels.append(lbl)

    def job_fn(job):
        def on_progress(r_n, total, msg):
            # r_n=0 means "preparing"; use small progress value
            if r_n <= 0:
                job.progress = 0.05
            else:
                # After each round completes, advance progress.
                job.progress = min(0.95, r_n / max(1, total))
            job.message = msg
        job.message = "啟動中…"
        job.progress = 0.02
        try:
            result = review(
                src, filled,
                page_index=0,
                max_rounds=max_rounds or None,
                profile_keys=profile_keys,
                progress_cb=on_progress,
                candidate_labels=candidate_labels,
                label_to_slot=label_to_slot,
            )
            job.meta["review_result"] = result.to_dict()
            job.progress = 1.0
            job.message = (
                f"完成（{result.total_elapsed_s:.1f}s）"
                if not result.errors
                else f"完成（有錯誤）"
            )
        except Exception as e:  # noqa: BLE001
            job.error = f"{type(e).__name__}: {e}"
            job.message = f"失敗：{e}"
            raise

    _s = llm_settings.get()
    _model = llm_settings.get_model_for("pdf-fill")
    job = job_manager.submit(
        "pdf-fill-llm", job_fn,
        meta={
            "upload_id": upload_id,
            "model": _model,
            "base_url": _s.get("base_url", ""),
        },
    )
    return {"job_id": job.id, "model": _s.get("model", "?")}


@router.get("/llm-review-result/{job_id}")
async def llm_review_result(job_id: str):
    """Fetch the review result JSON after a /llm-review-start job finishes.
    Returns 404 while job is still running (client should check job status
    first via /api/jobs/{id})."""
    job = job_manager.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    if job.status not in ("done", "error"):
        raise HTTPException(425, f"job still {job.status}")
    if job.error:
        return JSONResponse({"error": job.error}, status_code=200)
    result = (job.meta or {}).get("review_result")
    if result is None:
        return JSONResponse({"error": "no result in job"}, status_code=500)
    return result


@router.post("/llm-review-apply")
async def llm_review_apply(request: Request):
    """Apply LLM corrections back to the placements.json snapshot and
    re-render the filled PDF. V1 only supports REMOVAL of flagged cells.

    Request body:
        {
            "upload_id": "<hex>",
            "corrections": [
                {"label": "...", "current_value": "..."},
                ...
            ],
            "font_id": "auto" (optional)
        }

    Returns the new preview URLs + a summary of what was removed.
    """
    import json as _json
    from ...core.llm_apply_corrections import (
        filter_placements_by_corrections,
        placements_to_text_placements,
    )

    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    corrections = body.get("corrections") or []
    font_id = body.get("font_id") or "auto"
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    if not corrections:
        raise HTTPException(400, "no corrections to apply")
    from app.core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)

    src = settings.temp_dir / f"{upload_id}_in.pdf"
    snap_p = settings.temp_dir / f"{upload_id}_placements.json"
    filled = settings.temp_dir / f"{upload_id}_filled.pdf"
    if not src.exists() or not snap_p.exists():
        raise HTTPException(404, "upload 已過期或 placements 已遺失")

    try:
        snapshot = _json.loads(snap_p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"placements JSON 解析失敗：{e}")

    kept, removed, moved = filter_placements_by_corrections(
        snapshot.get("placements", []), corrections,
        profile_labels=snapshot.get("profile_labels", {}),
    )
    if not removed and not moved:
        return {"removed": 0, "moved": 0, "note": "沒有 placement 符合要處理的建議"}

    # Re-render filled PDF with the kept placements only
    tp_list = placements_to_text_placements(kept)
    pdf_text_overlay.overlay_text(src, filled, tp_list, font_id=font_id)

    # Re-render preview PNGs (all pages) and return URLs
    import fitz as _fitz
    with _fitz.open(str(filled)) as _doc:
        total = _doc.page_count
    pages_urls = []
    for i in range(total):
        f_png = settings.temp_dir / f"{upload_id}_p{i+1}.png"
        pdf_preview.render_page_png(filled, f_png, i, dpi=120)
        pages_urls.append({
            "index": i,
            "preview_url": f"/tools/pdf-fill/preview/{upload_id}_p{i+1}.png",
        })

    # Update snapshot so a later LLM review sees the new placements
    snapshot["placements"] = kept
    snap_p.write_text(
        _json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "removed": len(removed),
        "moved": len(moved),
        "kept": len(kept),
        "removed_items": [
            {"label": (p.get("label_text") or p.get("profile_key") or ""),
             "value": p.get("text") or ""}
            for p in removed
        ],
        "moved_items": [
            {"label": (p.get("label_text") or p.get("profile_key") or ""),
             "value": p.get("text") or "",
             "from": p.get("_moved_from"),
             "to": p.get("slot_pt")}
            for p in moved
        ],
        "pages": pages_urls,
    }


def _maybe_run_llm_review(
    src_pdf,
    placements,
    profile,
    opted_in: bool,
    max_rounds=None,
):
    """Wrap LLM review so failures never break /preview. Returns None when
    LLM is disabled / user didn't opt in / failure occurs.

    Called via ``asyncio.to_thread()`` from the async endpoint so the blocking
    httpx calls inside ``review()`` don't stall the event loop. That means
    sync positional signature here (to_thread forwards *args)."""
    if not opted_in:
        return None
    try:
        from ...core.llm_settings import llm_settings
        if not llm_settings.is_enabled():
            return {"skipped": "LLM 未啟用"}
        from ...core.llm_review import filled_from_placements
        from ...core.llm_review_per_field import per_field_review as review
        filled = filled_from_placements(placements, profile.get("labels"))
        if not filled:
            return {"skipped": "沒有填寫項目可校驗"}
        result = review(
            src_pdf,
            filled,
            page_index=0,
            max_rounds=max_rounds,
            profile_keys=list(profile["fields"].keys()),
        )
        return result.to_dict()
    except Exception as e:  # noqa: BLE001
        # Never bubble up — LLM is附加功能， must not break core fill flow.
        import logging
        logging.getLogger(__name__).warning("LLM review failed: %s", e)
        return {"skipped": f"LLM 校驗失敗：{e}"}


@router.post("/regenerate")
async def regenerate(request: Request):
    """Re-render the filled PDF after the user drags placements around.
    ``placements`` is a list of {i, slot_pt, text, kind, source_key, option_text, base_font_size}.
    """
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    font_id = body.get("font_id") or "auto"
    overrides = body.get("placements") or []
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    from app.core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")
    filled = settings.temp_dir / f"{upload_id}_filled.pdf"
    png = settings.temp_dir / f"{upload_id}_p1.png"

    placements = []
    for p in overrides:
        slot = tuple(p.get("slot_pt") or [0, 0, 100, 20])
        placements.append(pdf_text_overlay.TextPlacement(
            page=int(p.get("page", 0)),
            text=str(p.get("text", "")),
            slot=slot,
            base_font_size=float(p.get("base_font_size", 11.0)),
            min_font_size=7.0,
            align="center" if p.get("kind") == "check" else "left",
            source_key=str(p.get("source_key", "")),
            kind=str(p.get("kind", "text")),
            option_text=str(p.get("option_text", "")),
        ))
    pdf_text_overlay.overlay_text(src, filled, placements, font_id=font_id)
    import fitz as _fitz
    with _fitz.open(str(filled)) as _doc:
        total = _doc.page_count
    pages_urls = []
    for i in range(total):
        f_png = settings.temp_dir / f"{upload_id}_p{i+1}.png"
        r_png = settings.temp_dir / f"{upload_id}_raw_p{i+1}.png"
        pdf_preview.render_page_png(filled, f_png, i, dpi=120)
        if not r_png.exists():
            pdf_preview.render_page_png(src, r_png, i, dpi=120)
        pages_urls.append({
            "index": i,
            "preview_url": f"/tools/pdf-fill/preview/{f_png.name}",
            "raw_preview_url": f"/tools/pdf-fill/preview/{r_png.name}",
        })
    return {
        "ok": True,
        "preview_url": pages_urls[0]["preview_url"] if pages_urls else "",
        "raw_preview_url": pages_urls[0]["raw_preview_url"] if pages_urls else "",
        "pages": pages_urls,
    }


@router.post("/save-template")
async def save_template(request: Request):
    """Freeze positions into a template. Prefers client-supplied
    ``placements`` (after the user may have dragged them), and falls back
    to auto-detection when not provided."""
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    name = (body.get("name") or "").strip()
    company_id = body.get("company_id") or ""
    placements = body.get("placements") or []
    if not upload_id or not name:
        raise HTTPException(400, "upload_id and name required")
    from app.core.safe_paths import require_uuid_hex
    from ...core import upload_owner as _uo
    require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload not found or expired")
    profile = profile_manager.get(company_id or None)
    if placements:
        t = service.save_template_from_placements(src, name, placements)
    else:
        t = service.save_template_from_detection(src, name, profile["fields"])
    return {"ok": True, "template": {"id": t["id"], "name": t["name"],
                                        "fingerprint": t["fingerprint"],
                                        "fields": len(t.get("fields", [])),
                                        "checkboxes": len(t.get("checkboxes", []))}}


@router.post("/learn-synonym")
async def learn_synonym(request: Request):
    body = await request.json()
    key = (body.get("key") or "").strip()
    label = (body.get("label") or "").strip()
    if not key or not label:
        raise HTTPException(400, "key and label required")
    changed = synonym_manager.add_synonym(key, label)
    return {"ok": True, "changed": changed}


@router.post("/submit")
async def submit(
    request: Request,
    file: UploadFile = File(...),
    font_id: str = Form("auto"),
    company_id: str = Form(""),
):
    """Process the upload as a job and return a download link via the
    standard job-status endpoint."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")

    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    dst = settings.temp_dir / f"{upload_id}_filled.pdf"
    try:
        _write_upload_as_pdf(data, file.filename, src)
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    result_filename = _result_filename(file.filename)
    profile = profile_manager.get(company_id or None)
    # 抓 actor 起來給背景 job 用 — request.state.user 在 run() 跑時可能已釋放
    from ...core import sessions as _sessions
    actor = _sessions.user_label(getattr(request.state, "user", None))
    orig_filename = file.filename or "uploaded.pdf"

    def run(job):
        job.message = "辨識欄位…"
        job.progress = 0.2
        report = service.fill_pdf(src, dst, profile["fields"], font_id=font_id)
        job.message = f"完成（填了 {report.filled_count} 個欄位）"
        job.progress = 1.0
        job.result_path = dst
        job.result_filename = result_filename
        # 寫一筆 history（在 src.unlink 之前）
        try:
            history_manager.save(
                original_path=src,
                filled_path=dst,
                preview_path=None,
                original_filename=orig_filename,
                template_id=(report.applied_template or {}).get("id") if report.applied_template else None,
                template_name=(report.applied_template or {}).get("name") if report.applied_template else None,
                company_id=company_id or None,
                username=actor or "",
                report={
                    "detected": report.detected_count,
                    "filled": report.filled_count,
                    "checked": [{"key": k, "option": o} for k, o in report.checked_boxes],
                    "fingerprint": report.fingerprint,
                },
            )
        except Exception:
            import logging as _lg
            _lg.getLogger(__name__).exception("fill history save failed (job path)")
        try:
            src.unlink()
        except OSError:
            pass

    job = job_manager.submit("pdf-fill", run)
    return {"job_id": job.id}


@router.get("/preview/{name}")
async def serve_preview(name: str, request: Request):
    from app.core.safe_paths import safe_join
    from ...core import upload_owner
    p = safe_join(settings.temp_dir, name)
    # ACL — extract upload_id prefix, deny if not the owner (auth ON)
    uid = upload_owner.extract_upload_id(name)
    if uid:
        upload_owner.require(uid, request)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/png")


@router.get("/download/{upload_id}")
async def download_filled(upload_id: str, request: Request, name: str = "filled.pdf"):
    """Direct-download endpoint used by the preview page (bypasses jobs).

    The optional ``name`` query argument lets the client suggest the
    filename (e.g. ``<orig>_filled.pdf``) so the user gets a meaningful
    file name instead of the generic ``filled.pdf``.
    """
    from app.core.safe_paths import require_uuid_hex
    from ...core import upload_owner
    require_uuid_hex(upload_id, "upload_id")
    upload_owner.require(upload_id, request)
    p = settings.temp_dir / f"{upload_id}_filled.pdf"
    if not p.exists():
        raise HTTPException(404, "not found or expired")
    safe = Path(name).name or "filled.pdf"
    if not safe.lower().endswith(".pdf"):
        safe = safe + ".pdf"
    return FileResponse(str(p), media_type="application/pdf", filename=safe)


# ---------- Upload history (disabled) ----------
# History feature intentionally removed — files are ephemeral now.

@router.get("/history")
async def _history_page_disabled():
    raise HTTPException(404, "history feature disabled")

@router.get("/history/{hid}/file/{kind}")
async def _history_file_disabled(hid: str, kind: str):
    raise HTTPException(404, "history feature disabled")

@router.post("/history/{hid}/delete")
async def _history_delete_disabled(hid: str):
    raise HTTPException(404, "history feature disabled")

@router.post("/history/bulk-delete")
async def _history_bulk_delete_disabled():
    raise HTTPException(404, "history feature disabled")


@router.post("/history/{hid}/refill")
async def history_refill(hid: str, request: Request):
    # History feature disabled.
    raise HTTPException(404, "history feature disabled")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    font_id = (body.get("font_id") or "auto") if isinstance(body, dict) else "auto"
    company_id = (body.get("company_id") or "") if isinstance(body, dict) else ""

    entry = history_manager.get(hid)
    if not entry:
        raise HTTPException(404, "history not found")
    orig = history_manager.file(hid, "original")
    if not orig:
        raise HTTPException(404, "original file missing")

    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"{upload_id}_in.pdf"
    filled = settings.temp_dir / f"{upload_id}_filled.pdf"
    src.write_bytes(orig.read_bytes())

    profile = profile_manager.get(company_id or None)
    report = service.fill_pdf(src, filled, profile["fields"], font_id=font_id)
    preview_dpi = 120
    import fitz as _fitz
    with _fitz.open(str(filled)) as _doc:
        total_pages = _doc.page_count
        pages_info = [
            {"width_pt": _doc[i].rect.width, "height_pt": _doc[i].rect.height}
            for i in range(total_pages)
        ]
    for i in range(total_pages):
        pdf_preview.render_page_png(
            filled, settings.temp_dir / f"{upload_id}_p{i+1}.png", i, dpi=preview_dpi
        )
        pdf_preview.render_page_png(
            src, settings.temp_dir / f"{upload_id}_raw_p{i+1}.png", i, dpi=preview_dpi
        )
    png = settings.temp_dir / f"{upload_id}_p1.png"
    raw_png = settings.temp_dir / f"{upload_id}_raw_p1.png"
    unmatched = pdf_form_detect.find_unmatched_candidates(src)

    # Save a fresh history entry too (this is a new generation)
    try:
        history_manager.save(
            original_path=src, filled_path=filled, preview_path=png,
            original_filename=entry.get("filename") or "refill.pdf",
            template_id=(report.applied_template or {}).get("id") if report.applied_template else None,
            template_name=(report.applied_template or {}).get("name") if report.applied_template else None,
            company_id=company_id or None,
            report={
                "detected": report.detected_count, "filled": report.filled_count,
                "checked": [{"key": k, "option": o} for k, o in report.checked_boxes],
                "fingerprint": report.fingerprint,
            },
        )
    except Exception:
        pass

    labels = profile["labels"]
    return {
        "upload_id": upload_id,
        "preview_url": f"/tools/pdf-fill/preview/{png.name}",
        "raw_preview_url": f"/tools/pdf-fill/preview/{raw_png.name}",
        "filename": entry.get("filename") or "refill.pdf",
        "report": {
            "detected": report.detected_count,
            "filled": report.filled_count,
            "matched": [{"key": k, "label": labels.get(k, k), "count": n}
                         for k, n in sorted(report.matched_keys.items())],
            "unfilled": [{"key": k, "label": labels.get(k, k)} for k in report.unfilled_keys],
            "checked_boxes": [{"key": k, "label": labels.get(k, k), "option": opt}
                                for k, opt in report.checked_boxes],
            "unmatched_labels": unmatched,
            "applied_template": report.applied_template,
            "fingerprint": report.fingerprint,
        },
        "page": {
            "width_pt": pages_info[0]["width_pt"] if pages_info else 595,
            "height_pt": pages_info[0]["height_pt"] if pages_info else 842,
            "dpi": preview_dpi,
        },
        "pages": [
            {"index": i, "width_pt": pg["width_pt"], "height_pt": pg["height_pt"],
             "preview_url": f"/tools/pdf-fill/preview/{upload_id}_p{i+1}.png",
             "raw_preview_url": f"/tools/pdf-fill/preview/{upload_id}_raw_p{i+1}.png"}
            for i, pg in enumerate(pages_info)
        ],
        "placements": [
            {"i": i, "profile_key": pl.source_key, "text": pl.text,
             "slot_pt": list(pl.slot), "kind": pl.kind,
             "option_text": pl.option_text, "base_font_size": pl.base_font_size,
             "page": pl.page}
            for i, pl in enumerate(report.placements)
        ],
        "profile_keys": [{"key": k, "label": labels.get(k, k)} for k in profile["fields"].keys()],
        "profile_values": dict(profile["fields"]),
        "profile_sections": [
            {"title": sec["title"], "keys": [r["key"] for r in sec["rows"]]}
            for sec in profile_manager.get_sections_for_edit(company_id or None)
        ],
    }


def _result_filename(orig: str) -> str:
    stem = Path(orig).stem
    return f"{stem}_filled.pdf"

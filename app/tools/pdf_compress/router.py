"""Endpoints for PDF 壓縮 (pdf-compress)."""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core.job_manager import job_manager


logger = logging.getLogger("app.pdf_compress")
router = APIRouter()


# ------------------------------------------------------------- capability

def _find_ghostscript() -> Optional[str]:
    """Return a usable Ghostscript executable path, or None if missing.
    Tries PATH first (Mac/Linux: ``gs``; Windows: ``gswin64c`` / ``gswin32c``),
    then common install dirs on Windows."""
    for name in ("gs", "gswin64c", "gswin32c", "gswin64c.exe", "gswin32c.exe"):
        p = shutil.which(name)
        if p:
            return p
    # Windows default install dirs
    candidates: list[Path] = []
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if not pf:
            continue
        root = Path(pf) / "gs"
        if root.exists():
            for gs_dir in root.iterdir():
                if gs_dir.is_dir() and gs_dir.name.lower().startswith("gs"):
                    for exe in ("gswin64c.exe", "gswin32c.exe"):
                        cand = gs_dir / "bin" / exe
                        if cand.exists():
                            candidates.append(cand)
    return str(candidates[0]) if candidates else None


# ------------------------------------------------------------- analysis

def _analyze_pdf(src: Path) -> dict:
    """Return quick stats for the upload dialog so the user sees what
    they're compressing before hitting Go."""
    size = src.stat().st_size
    with fitz.open(str(src)) as doc:
        pg_count = doc.page_count
        img_xrefs: set[int] = set()
        for pno in range(pg_count):
            for img in doc[pno].get_images(full=True):
                if img and img[0]:
                    img_xrefs.add(img[0])
        font_xrefs: set[int] = set()
        for pno in range(pg_count):
            for f in doc[pno].get_fonts(full=True):
                if f and f[0]:
                    font_xrefs.add(f[0])
        has_acroform = bool(doc.is_form_pdf)
        annot_count = 0
        for pno in range(pg_count):
            p = doc[pno]
            annot_count += sum(1 for _ in (p.annots() or []))
    return {
        "size": size,
        "pages": pg_count,
        "image_count": len(img_xrefs),
        "font_count": len(font_xrefs),
        "has_form": has_acroform,
        "annot_count": annot_count,
    }


# ------------------------------------------------------------- compression

def _image_downsample_and_recompress(
    doc: "fitz.Document",
    *,
    max_dpi: float,
    jpeg_quality: int,
    progress_cb=None,
) -> dict:
    """Walk every image in the PDF; if its effective DPI on placement
    exceeds ``max_dpi``, resample with Pillow to ``max_dpi`` and re-embed
    as JPEG (or PNG if it has alpha). Returns {bytes_before, bytes_after,
    images_resampled, images_recompressed}.

    Pillow is required; if missing, this step is a no-op.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:
        logger.warning("Pillow not installed — skipping image recompression")
        return {"bytes_before": 0, "bytes_after": 0,
                "images_resampled": 0, "images_recompressed": 0, "skipped_no_pil": True}

    resampled = 0
    recompressed = 0
    bytes_before = 0
    bytes_after = 0

    # Collect (xref, first_page) pairs — xrefs are doc-global, so we only
    # replace each once but need a page to call replace_image on.
    first_seen: dict[int, int] = {}
    # Track each xref's placement rects across pages to compute effective DPI
    placements: dict[int, list[tuple[float, float]]] = {}
    for pno in range(doc.page_count):
        page = doc[pno]
        for img in page.get_images(full=True):
            xref = img[0]
            if xref not in first_seen:
                first_seen[xref] = pno
            placements.setdefault(xref, [])
        try:
            infos = page.get_image_info(xrefs=True) or []
        except Exception:
            infos = []
        for info in infos:
            xref = info.get("xref") or 0
            b = info.get("bbox")
            if not xref or not b:
                continue
            w_pt = float(b[2]) - float(b[0])
            h_pt = float(b[3]) - float(b[1])
            if w_pt > 0 and h_pt > 0:
                placements.setdefault(xref, []).append((w_pt, h_pt))

    total = len(first_seen)
    done = 0
    for xref, first_page in first_seen.items():
        done += 1
        if progress_cb:
            try: progress_cb(done, total)
            except Exception: pass
        try:
            pix = fitz.Pixmap(doc, xref)
        except Exception:
            continue
        # Drop CMYK / spot colour spaces → RGB for Pillow.
        if pix.n - pix.alpha >= 4:
            try:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            except Exception:
                continue
        orig_w, orig_h = pix.width, pix.height
        has_alpha = bool(pix.alpha)
        try:
            raw_png = pix.tobytes("png")
        except Exception:
            continue
        bytes_before += len(raw_png)

        # Effective DPI across all placements — take the largest rendering
        # so we don't blur a small image that is actually shown large.
        target_w, target_h = orig_w, orig_h
        rects = placements.get(xref, [])
        if rects and max_dpi > 0:
            max_eff_dpi = 0.0
            for w_pt, h_pt in rects:
                dpi_x = (orig_w / w_pt) * 72.0
                dpi_y = (orig_h / h_pt) * 72.0
                max_eff_dpi = max(max_eff_dpi, dpi_x, dpi_y)
            if max_eff_dpi > max_dpi and max_eff_dpi > 0:
                scale = max_dpi / max_eff_dpi
                target_w = max(1, int(round(orig_w * scale)))
                target_h = max(1, int(round(orig_h * scale)))

        try:
            img = Image.open(io.BytesIO(raw_png))
            # Normalise colour modes so JPEG encode doesn't crash.
            if has_alpha:
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
            else:
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
            if (target_w, target_h) != (orig_w, orig_h):
                img = img.resize((target_w, target_h), Image.LANCZOS)
                did_resize = True
            else:
                did_resize = False
            buf = io.BytesIO()
            if has_alpha:
                # Keep PNG for transparent images — JPEG would drop alpha.
                img.save(buf, format="PNG", optimize=True)
                new_bytes = buf.getvalue()
            else:
                img.save(buf, format="JPEG",
                         quality=int(jpeg_quality), optimize=True,
                         progressive=True)
                new_bytes = buf.getvalue()
        except Exception as exc:
            logger.warning("image xref %d: resize/re-encode failed — %s",
                           xref, exc)
            continue

        # Only swap if new is smaller — otherwise keep original.
        if len(new_bytes) < len(raw_png):
            try:
                page = doc[first_page]
                # `replace_image` takes raw image bytes; works for JPEG+PNG.
                page.replace_image(xref, stream=new_bytes)
                if did_resize: resampled += 1
                recompressed += 1
                bytes_after += len(new_bytes)
            except Exception as exc:
                logger.warning("image xref %d: replace_image failed — %s",
                               xref, exc)
                bytes_after += len(raw_png)
        else:
            bytes_after += len(raw_png)

    return {
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "images_resampled": resampled,
        "images_recompressed": recompressed,
        "skipped_no_pil": False,
    }


def _run_pymupdf_compression(
    src: Path, dst: Path, *,
    image_max_dpi: float,
    jpeg_quality: int,
    subset_fonts: bool,
    strip_annotations: bool,
    strip_forms: bool,
    strip_bookmarks: bool,
    strip_metadata: bool,
    progress_cb=None,
) -> dict:
    stats: dict = {}
    doc = fitz.open(str(src))
    try:
        if strip_metadata:
            try:
                doc.set_metadata({})
                stats["metadata_cleared"] = True
            except Exception:
                pass
        if strip_bookmarks:
            try:
                doc.set_toc([])
                stats["bookmarks_cleared"] = True
            except Exception:
                pass
        if strip_forms:
            # Walk every widget on every page
            removed_widgets = 0
            for pno in range(doc.page_count):
                page = doc[pno]
                try:
                    for w in list(page.widgets() or []):
                        try:
                            page.delete_widget(w); removed_widgets += 1
                        except Exception:
                            pass
                except Exception:
                    pass
            stats["widgets_removed"] = removed_widgets
        if strip_annotations:
            removed_annots = 0
            for pno in range(doc.page_count):
                page = doc[pno]
                try:
                    for a in list(page.annots() or []):
                        try:
                            page.delete_annot(a); removed_annots += 1
                        except Exception:
                            pass
                except Exception:
                    pass
            stats["annots_removed"] = removed_annots

        if image_max_dpi > 0:
            img_stats = _image_downsample_and_recompress(
                doc, max_dpi=image_max_dpi,
                jpeg_quality=jpeg_quality,
                progress_cb=progress_cb,
            )
            stats["image"] = img_stats

        if subset_fonts:
            try:
                doc.subset_fonts()
                stats["fonts_subset"] = True
            except Exception as exc:
                logger.warning("subset_fonts failed: %s", exc)
                stats["fonts_subset"] = False

        # Save with max garbage collection + deflate compression.
        save_kwargs: dict = {
            "garbage": 4,
            "deflate": True,
            "deflate_images": True,
            "deflate_fonts": True,
            "clean": True,
        }
        doc.save(str(dst), **save_kwargs)
    finally:
        doc.close()
    return stats


def _run_ghostscript(src: Path, dst: Path, preset: str,
                     gs_path: str) -> None:
    """Run Ghostscript with a /screen /ebook /printer /prepress preset."""
    assert preset in ("screen", "ebook", "printer", "prepress")
    cmd = [
        gs_path,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.7",
        f"-dPDFSETTINGS=/{preset}",
        "-dNOPAUSE", "-dQUIET", "-dBATCH", "-dSAFER",
        f"-sOutputFile={dst}",
        str(src),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=600)


# ---------------------------------------------------------------- presets

def _preset_params(preset: str) -> dict:
    """Map the three one-click presets to explicit option dicts."""
    if preset == "gentle":
        return {
            "image_max_dpi": 0,      # no downsample
            "jpeg_quality": 90,
            "subset_fonts": True,
            "strip_annotations": False,
            "strip_forms": False,
            "strip_bookmarks": False,
            "strip_metadata": True,
        }
    if preset == "balanced":
        return {
            "image_max_dpi": 150,
            "jpeg_quality": 80,
            "subset_fonts": True,
            "strip_annotations": False,
            "strip_forms": False,
            "strip_bookmarks": False,
            "strip_metadata": True,
        }
    if preset == "aggressive":
        return {
            "image_max_dpi": 96,
            "jpeg_quality": 65,
            "subset_fonts": True,
            "strip_annotations": True,
            "strip_forms": True,
            "strip_bookmarks": True,
            "strip_metadata": True,
        }
    raise ValueError(f"unknown preset: {preset}")


# --------------------------------------------------------------- endpoints

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    gs_path = _find_ghostscript()
    templates = request.app.state.templates
    return templates.TemplateResponse(
        "pdf_compress.html",
        {"request": request, "gs_available": bool(gs_path)},
    )


@router.post("/analyze")
async def analyze(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    upload_id = uuid.uuid4().hex
    src = settings.temp_dir / f"cmp_{upload_id}_in.pdf"
    src.write_bytes(data)
    try:
        (settings.temp_dir / f"cmp_{upload_id}_name.txt").write_text(
            file.filename or "document.pdf", encoding="utf-8")
    except Exception:
        pass
    stats = _analyze_pdf(src)
    stats["upload_id"] = upload_id
    stats["filename"] = file.filename
    return stats


@router.post("/submit")
async def submit(request: Request):
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    preset = (body.get("preset") or "balanced").strip()
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    src = settings.temp_dir / f"cmp_{upload_id}_in.pdf"
    if not src.exists():
        raise HTTPException(404, "upload expired")

    # Parameter resolution: preset vs advanced
    params: dict
    if preset == "advanced":
        params = {
            "image_max_dpi": float(body.get("image_max_dpi") or 0),
            "jpeg_quality": int(body.get("jpeg_quality") or 80),
            "subset_fonts": bool(body.get("subset_fonts", True)),
            "strip_annotations": bool(body.get("strip_annotations", False)),
            "strip_forms": bool(body.get("strip_forms", False)),
            "strip_bookmarks": bool(body.get("strip_bookmarks", False)),
            "strip_metadata": bool(body.get("strip_metadata", True)),
        }
    else:
        params = _preset_params(preset)

    use_gs = bool(body.get("use_ghostscript"))
    gs_preset = (body.get("gs_preset") or "ebook").strip()
    gs_path = _find_ghostscript() if use_gs else None
    if use_gs and not gs_path:
        raise HTTPException(400, "系統未安裝 Ghostscript")

    orig_size = src.stat().st_size
    orig_name = "document.pdf"
    try:
        orig_name = (settings.temp_dir /
                     f"cmp_{upload_id}_name.txt").read_text(encoding="utf-8").strip() or orig_name
    except Exception:
        pass
    stem = Path(orig_name).stem

    def run(job):
        wdir = settings.temp_dir / f"cmp_{upload_id}_out"
        wdir.mkdir(parents=True, exist_ok=True)
        # Intermediate output from PyMuPDF stage
        mupdf_out = wdir / f"{stem}_compressed_mupdf.pdf"
        t0 = time.time()
        job.message = "步驟 1 / 2：處理圖片與字型…"
        job.progress = 0.1

        def _img_cb(done: int, total: int):
            if total <= 0:
                return
            job.progress = 0.1 + (done / total) * 0.7
            job.message = f"步驟 1 / 2：處理圖片（{done} / {total}）"

        stats = _run_pymupdf_compression(
            src, mupdf_out,
            image_max_dpi=params["image_max_dpi"],
            jpeg_quality=params["jpeg_quality"],
            subset_fonts=params["subset_fonts"],
            strip_annotations=params["strip_annotations"],
            strip_forms=params["strip_forms"],
            strip_bookmarks=params["strip_bookmarks"],
            strip_metadata=params["strip_metadata"],
            progress_cb=_img_cb,
        )
        mupdf_size = mupdf_out.stat().st_size

        final_out = mupdf_out
        final_size = mupdf_size
        if use_gs and gs_path:
            job.message = "步驟 2 / 2：Ghostscript 強力壓縮…"
            job.progress = 0.85
            gs_out = wdir / f"{stem}_compressed.pdf"
            try:
                _run_ghostscript(mupdf_out, gs_out, gs_preset, gs_path)
                if gs_out.exists():
                    # Only keep GS result if it actually came out smaller.
                    if gs_out.stat().st_size < mupdf_size:
                        final_out = gs_out
                        final_size = gs_out.stat().st_size
                    else:
                        final_out = mupdf_out
                        final_size = mupdf_size
            except Exception as exc:
                logger.warning("Ghostscript pass failed: %s", exc)
                stats["ghostscript_error"] = str(exc)

        # Move to a stable output name the user sees.
        final_name = f"{stem}_compressed.pdf"
        dst = wdir / final_name
        if final_out != dst:
            shutil.move(str(final_out), str(dst))
            final_out = dst

        elapsed = time.time() - t0
        saved_bytes = max(0, orig_size - final_size)
        saved_pct = (saved_bytes / orig_size * 100) if orig_size else 0.0
        job.result_path = final_out
        job.result_filename = final_name
        job.progress = 1.0
        job.message = (
            f"完成：{_fmt_bytes(orig_size)} → {_fmt_bytes(final_size)}"
            f"（省 {saved_pct:.1f}%，耗時 {elapsed:.1f}s）"
        )
        # Stash summary for the /result endpoint (UI after-view)
        job.meta = dict(job.meta or {})
        job.meta["summary"] = {
            "orig_size": orig_size,
            "final_size": final_size,
            "saved_bytes": saved_bytes,
            "saved_pct": round(saved_pct, 2),
            "elapsed_s": round(elapsed, 2),
            "stats": stats,
            "filename": final_name,
        }

    job = job_manager.submit("pdf-compress", run, meta={"filename": orig_name})
    return {"job_id": job.id}


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.2f} GB"

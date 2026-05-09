"""Endpoints for 文件去識別化 (doc-deident)."""
from __future__ import annotations

import io
import logging
import re
import time as _t
import uuid
from pathlib import Path
from typing import Optional

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import office_convert, pdf_preview
from ...core.job_manager import job_manager
from . import patterns as P

logger = logging.getLogger("app.doc_deident")
router = APIRouter()


# ----------------------------------------------------------- plumbing

def _work(upload_id: str) -> Path:
    d = settings.temp_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _src_path(upload_id: str) -> Path:
    return settings.temp_dir / f"did_{upload_id}_src.pdf"


def _out_path(upload_id: str) -> Path:
    return settings.temp_dir / f"did_{upload_id}_out.pdf"


# ------------------------------------------------------------- detection

def _build_findings_for_page(page, selected_ids: set[str],
                             custom_regexes: list[tuple[str, re.Pattern]]
                             ) -> list[dict]:
    """Return a list of {type, value, masked, bbox, text} for every
    sensitive hit on this page. Each finding carries the PDF points bbox
    used later for redaction / mask rendering."""
    out: list[dict] = []
    # Per line: concat span texts, remember per-char span mapping so we
    # can map a regex match back to a bbox by union-ing span rects.
    td = page.get_text("dict")
    for block in td.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", []) or []
            if not spans:
                continue
            text_parts: list[str] = []
            span_map: list[int] = []  # span index for each char
            for si, sp in enumerate(spans):
                t = sp.get("text") or ""
                text_parts.append(t)
                span_map.extend([si] * len(t))
            line_text = "".join(text_parts)
            if not line_text.strip():
                continue

            def _emit(m, pat_label: str, pat_id: str, masked: str, grp: int = 0):
                try:
                    start, end = m.start(grp), m.end(grp)
                    if start < 0:
                        start, end = m.start(), m.end()
                except Exception:
                    start, end = m.start(), m.end()
                if start >= len(span_map) or end == 0:
                    return
                first_si = span_map[start]
                last_si = span_map[min(end - 1, len(span_map) - 1)]
                # Compute union bbox over involved spans. For same-line
                # matches this over-estimates width when the match is a
                # substring of a span (span reports full rect), so we
                # additionally clip horizontally by char-width estimate.
                bx0 = min(spans[i]["bbox"][0] for i in range(first_si, last_si + 1))
                by0 = min(spans[i]["bbox"][1] for i in range(first_si, last_si + 1))
                bx1 = max(spans[i]["bbox"][2] for i in range(first_si, last_si + 1))
                by1 = max(spans[i]["bbox"][3] for i in range(first_si, last_si + 1))
                # Tighten horizontally when the match sits inside a single
                # span and doesn't cover the whole span.
                if first_si == last_si:
                    sp = spans[first_si]
                    full_text = sp.get("text") or ""
                    if full_text:
                        sp_x0, _, sp_x1, _ = sp["bbox"]
                        cw = (sp_x1 - sp_x0) / max(1, len(full_text))
                        # Offset within the span
                        span_start_in_line = sum(len(spans[i].get("text") or "")
                                                 for i in range(first_si))
                        local_s = start - span_start_in_line
                        local_e = end - span_start_in_line
                        bx0 = sp_x0 + cw * max(0, local_s)
                        bx1 = sp_x0 + cw * max(local_e, local_s + 1)
                        by0 = sp["bbox"][1]
                        by1 = sp["bbox"][3]
                try:
                    emit_value = m.group(grp)
                except Exception:
                    emit_value = m.group(0)
                out.append({
                    "type": pat_id,
                    "type_label": pat_label,
                    "value": emit_value,
                    "masked": masked,
                    "bbox": [bx0, by0, bx1, by1],
                    "font_size": float(spans[first_si].get("size", 11) or 11),
                    "color_int": int(spans[first_si].get("color", 0) or 0),
                })

            # Built-in patterns
            for pat in P.CATALOG:
                if pat.id not in selected_ids:
                    continue
                for m in pat.regex.finditer(line_text):
                    try:
                        val = m.group(pat.value_group) if pat.value_group else m.group(0)
                    except Exception:
                        val = m.group(0)
                    if val is None:
                        continue
                    if not pat.validate(val):
                        continue
                    _emit(m, pat.label, pat.id, pat.mask(val), pat.value_group)
            # Custom user-supplied regexes (no checksum, no mask — use
            # "****" as default mask)
            for label, rx in custom_regexes:
                try:
                    for m in rx.finditer(line_text):
                        val = m.group(0)
                        masked = "*" * max(1, len(val))
                        _emit(m, label, f"custom:{label}", masked)
                except Exception:
                    continue
    return out


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    # Group patterns for UI rendering; preserve CATALOG order inside each group.
    grouped: dict[str, list[dict]] = {}
    for p in P.CATALOG:
        grouped.setdefault(p.group, []).append(
            {"id": p.id, "label": p.label, "default_on": p.default_on, "icon": p.icon}
        )
    # Stable group order
    preferred = ["個人身分", "聯絡方式", "金融資訊", "企業資料", "其他"]
    pattern_groups = [
        {"title": g, "entries": grouped[g]} for g in preferred if g in grouped
    ] + [
        {"title": g, "entries": items} for g, items in grouped.items() if g not in preferred
    ]
    from ...core.llm_settings import llm_settings
    return templates.TemplateResponse(
        "doc_deident.html",
        {"request": request, "pattern_groups": pattern_groups,
         "llm_enabled": llm_settings.is_enabled(),
         "llm_model": llm_settings.get_model_for("doc-deident") if llm_settings.is_enabled() else ""},
    )


@router.post("/detect")
async def detect(
    request: Request,
    file: UploadFile = File(...),
    types: str = Form(""),    # comma-separated pattern ids
    custom: str = Form(""),   # optional: "label|regex\nlabel2|regex2"
    llm_augment: str = Form(""),  # "1" → 啟用 LLM 補偵測（regex 抓不到的人名 / 職稱 / 客戶代號等）
):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    orig_name = file.filename or "document"
    ext = Path(orig_name).suffix.lower()

    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    pdf_path = _src_path(upload_id)
    # If PDF upload, write direct; if office, convert via soffice.
    if ext == ".pdf":
        pdf_path.write_bytes(data)
        source_type = "pdf"
    elif office_convert.is_office_file(orig_name):
        tmp = settings.temp_dir / f"did_{upload_id}_orig{ext}"
        tmp.write_bytes(data)
        try:
            office_convert.convert_to_pdf(tmp, pdf_path, timeout=120.0)
        except RuntimeError:
            raise HTTPException(
                500,
                "找不到 Office 轉檔引擎（OxOffice / LibreOffice）。請到「轉檔設定」確認。",
            )
        except Exception as exc:
            raise HTTPException(500, f"Office 轉 PDF 失敗：{exc}")
        if not pdf_path.exists():
            raise HTTPException(500, "轉檔未產生 PDF")
        source_type = "office"
    else:
        raise HTTPException(400, f"不支援的檔案格式：{ext}")

    # Stash original filename for the download step
    try:
        (settings.temp_dir / f"did_{upload_id}_name.txt").write_text(
            Path(orig_name).stem + ".pdf", encoding="utf-8")
    except Exception:
        pass

    selected_ids = set(t for t in (types or "").split(",") if t.strip())
    if not selected_ids:
        selected_ids = {p.id for p in P.CATALOG if p.default_on}

    # Parse custom regex spec: one rule per line, "label|regex"
    custom_regexes: list[tuple[str, re.Pattern]] = []
    for line in (custom or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            label, _, rx_str = line.partition("|")
            label = label.strip() or "自訂"
            rx_str = rx_str.strip()
        else:
            label = "自訂"
            rx_str = line
        try:
            custom_regexes.append((label, re.compile(rx_str)))
        except re.error as exc:
            raise HTTPException(400, f"自訂 regex 無效：{rx_str} — {exc}")

    findings_by_page: list[dict] = []
    all_findings: list[dict] = []
    page_texts: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        total_pages = doc.page_count
        for pno in range(doc.page_count):
            page = doc[pno]
            page_findings = _build_findings_for_page(page, selected_ids,
                                                    custom_regexes)
            for f in page_findings:
                f["page"] = pno + 1
                f["id"] = len(all_findings)
                all_findings.append(f)
            findings_by_page.append({
                "page": pno + 1,
                "count": len(page_findings),
            })
            page_texts.append(page.get_text("text") or "")

    # === LLM 補偵測（v1.4.27）===
    # regex 抓不到的 context-sensitive 案例（人名「王經理」「Dr. Chen」、
    # 客戶代號「KC-2024-A」、特殊地址簡稱等）— 把已抓到的列為「已知」
    # 給 LLM，請它找出疑似漏抓的，回 JSON list。最後逐一在原文 search 找
    # 確切位置 + bbox，加進 findings 並標 `source: "llm"`。
    llm_added = 0
    llm_warning = ""
    if str(llm_augment).lower() in ("1", "true", "on", "yes"):
        try:
            from ...core.llm_settings import llm_settings as _llms
            if _llms.is_enabled():
                full_text = "\n\n".join(
                    f"--- 第 {i+1} 頁 ---\n{t}" for i, t in enumerate(page_texts) if t.strip()
                )
                if full_text.strip():
                    already_known = list({f["value"] for f in all_findings})[:50]
                    extra = _llm_extra_findings(full_text, already_known)
                    if extra:
                        with fitz.open(str(pdf_path)) as doc2:
                            for item in extra:
                                txt = (item.get("text") or "").strip()
                                kind = (item.get("type") or "其他")
                                if not txt or len(txt) < 2:
                                    continue
                                # 在每頁全文 search 確切位置 + bbox
                                for pno in range(doc2.page_count):
                                    rects = doc2[pno].search_for(txt) or []
                                    for r in rects:
                                        f = {
                                            "id": len(all_findings),
                                            "page": pno + 1,
                                            "type_id": "llm_" + kind,
                                            "type_label": "[LLM] " + kind,
                                            "value": txt,
                                            "masked": "*" * len(txt),
                                            "bbox": [r.x0, r.y0, r.x1, r.y1],
                                            "source": "llm",
                                        }
                                        all_findings.append(f)
                                        llm_added += 1
        except Exception:
            # v1.5.4 CodeQL py/stack-trace-exposure: 不漏 exception 訊息給 user
            import logging as _lg
            _lg.getLogger(__name__).exception("LLM augment failed")
            llm_warning = "LLM 補偵測失敗,僅顯示 regex 結果"

    by_type: dict[str, int] = {}
    for f in all_findings:
        by_type[f["type_label"]] = by_type.get(f["type_label"], 0) + 1

    return {
        "upload_id": upload_id,
        "filename": orig_name,
        "source_type": source_type,
        "pages": total_pages,
        "findings": all_findings,
        "by_type": by_type,
        "by_page": findings_by_page,
        "llm_added": llm_added,
        "llm_warning": llm_warning,
    }


def _llm_extra_findings(full_text: str, already_known: list[str]) -> list[dict]:
    """Ask the LLM to find sensitive entities the regex missed. Returns
    a list of {text, type} dicts; bbox lookup is done by the caller."""
    from ...core.llm_settings import llm_settings as _llms
    client = _llms.make_client()
    if client is None:
        return []
    model = _llms.get_model_for("doc-deident")
    # 文件可能很長 — 截斷以免 LLM 超時。 8K char 大概對應 1500-2000 個中文字
    max_chars = 8000
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n\n…（後續省略）"
    known_str = "、".join(already_known[:30]) or "（無）"
    prompt = (
        "你是文件去識別化助手。請從下面文件中找出『可能屬於敏感個人 / 業務資料但容易被 regex 漏掉』"
        "的詞彙，包含但不限於以下類型：\n"
        "  - 人名（含「先生 / 小姐 / 博士 / 經理」等稱謂）、職稱、暱稱、別名\n"
        "  - 客戶代號 / 產品代號 / 員工編號 / 部門代號（含非標準格式如「客戶 KC-2024-A」「員編 E-12345」）\n"
        "  - 訂單 / 採購 / 銷貨 / ○○單號（如「訂購單 12345」「採購案 2025-第三季-001」這類非 PO/SO 前綴格式）\n"
        "  - 合約編號 / 案號 / 工單號（含「合約字號 110-A-001」這類本國公文式編號）\n"
        "  - 發票相關（電子發票、傳統發票字軌、收據編號）\n"
        "  - 特殊地址簡稱（「總部三樓會議室」「南港分公司」「松山營業所」這類口語化地點）\n"
        "  - 公司 / 機構 / 廠商名稱（含未冠 Co./Ltd./股份有限公司 後綴的簡稱）\n"
        "  - 行程 / 物流類（航班號 BR857、訂位代號 ABCDEF、貨運追蹤碼、車輛 VIN 碼、GPS 座標）\n"
        f"以下詞彙『已被偵測』，請不要重複列出：{known_str}\n\n"
        "回應**只能是純 JSON array**，每筆 `{\"text\": \"...\", \"type\": \"類別\"}`，"
        "type 用簡短中文（如 人名 / 職稱 / 代號 / 訂單號 / 合約號 / 公司名稱 / 航班號 / 地址）。"
        "例：`[{\"text\":\"王經理\",\"type\":\"人名\"},{\"text\":\"KC-2024-A\",\"type\":\"客戶代號\"},"
        "{\"text\":\"BR0857\",\"type\":\"航班號\"}]`。"
        "找不到就回 `[]`。**不要任何解釋文字、不要 ```json``` 包裝、不要前綴後綴。**\n\n"
        f"文件內容：\n{full_text}"
    )
    try:
        resp = client.text_query(prompt=prompt, model=model,
                                  temperature=0.0, think=False)
    except Exception as e:
        import logging as _lg
        _lg.getLogger(__name__).warning("LLM call failed: %s", e)
        return []
    raw = (resp or "").strip()
    # 容錯：去掉 ```json``` 包裝
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        import json as _json
        arr = _json.loads(raw)
        if not isinstance(arr, list):
            return []
        # Sanity filter: drop empty / overly long entries
        out = []
        for x in arr:
            if not isinstance(x, dict):
                continue
            t = (x.get("text") or "").strip()
            if not t or len(t) > 80:
                continue
            out.append({"text": t, "type": (x.get("type") or "其他").strip()[:16]})
        return out
    except Exception:
        return []


# ----------------------------------------------------------- processing

@router.post("/process")
async def process(request: Request):
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    pdf_path = _src_path(upload_id)
    if not pdf_path.exists():
        raise HTTPException(404, "upload expired")
    mode = (body.get("mode") or "mask").strip()
    if mode not in ("redact", "mask"):
        raise HTTPException(400, "mode 必須是 redact 或 mask")
    selections: list[dict] = body.get("selections") or []
    if not isinstance(selections, list):
        raise HTTPException(400, "selections 格式錯誤")

    # Group selections by page for efficient pass
    by_page: dict[int, list[dict]] = {}
    for s in selections:
        pno = int(s.get("page", 1)) - 1
        if pno < 0:
            continue
        by_page.setdefault(pno, []).append(s)

    out_path = _out_path(upload_id)
    # Redaction mode paints a black bar; Masking mode leaves the redacted
    # area transparent so the re-inserted masked text blends with the
    # original page background (otherwise we get an ugly white rectangle
    # floating on top of a coloured / image-backed page).
    mode_fill = (0, 0, 0) if mode == "redact" else None
    count_done = 0
    doc = fitz.open(str(pdf_path))
    try:
        for pno, items in by_page.items():
            if pno >= doc.page_count:
                continue
            page = doc[pno]
            # Pass 1: redact (destroy) every selected region so the
            # original sensitive text is truly removed.
            for s in items:
                bb = s.get("bbox") or []
                if len(bb) != 4:
                    continue
                rect = fitz.Rect(*bb)
                if mode_fill is None:
                    page.add_redact_annot(rect)            # no fill → transparent
                else:
                    page.add_redact_annot(rect, fill=mode_fill)
                count_done += 1
            try:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except Exception:
                page.apply_redactions()

            # Pass 2 (mask mode only): re-insert the masked value at
            # the same location, in the same font size / colour.
            if mode == "mask":
                for s in items:
                    bb = s.get("bbox") or []
                    if len(bb) != 4:
                        continue
                    masked = s.get("masked") or ""
                    if not masked:
                        continue
                    font_size = float(s.get("font_size") or 11.0)
                    color_int = int(s.get("color_int") or 0)
                    r = ((color_int >> 16) & 0xff) / 255.0
                    g = ((color_int >> 8) & 0xff) / 255.0
                    b = (color_int & 0xff) / 255.0
                    bx0, by0, bx1, by1 = bb
                    base_y = by1 - font_size * 0.18
                    has_cjk = any("一" <= c <= "鿿" for c in masked)
                    # Use built-in CJK font for CJK content, Helvetica for ASCII.
                    if has_cjk:
                        try:
                            page.insert_text(
                                fitz.Point(bx0, base_y), masked,
                                fontname="china-t", fontsize=font_size,
                                color=(r, g, b),
                            )
                        except Exception:
                            pass
                    else:
                        try:
                            page.insert_text(
                                fitz.Point(bx0, base_y), masked,
                                fontname="helv", fontsize=font_size,
                                color=(r, g, b),
                            )
                        except Exception:
                            pass
        doc.save(str(out_path), garbage=3, deflate=True)
    finally:
        doc.close()

    # Render each page of the processed PDF to PNG thumbs so the UI can
    # show a before-download preview.
    pages_info: list[dict] = []
    with fitz.open(str(out_path)) as d2:
        for i in range(d2.page_count):
            thumb = settings.temp_dir / f"did_{upload_id}_p{i+1}.png"
            pdf_preview.render_page_png(out_path, thumb, i, dpi=120)
            pages_info.append({
                "page": i + 1,
                "thumb_url": f"/tools/doc-deident/preview/{thumb.name}?t={int(_t.time())}",
                "large_url": f"/tools/doc-deident/preview/{thumb.name}",
            })

    return {
        "ok": True,
        "processed": count_done,
        "download_url": f"/tools/doc-deident/download/{upload_id}",
        "pages": pages_info,
    }


@router.get("/preview/{filename}")
async def preview(filename: str, request: Request):
    from app.core.safe_paths import safe_join, is_safe_name
    from ...core import upload_owner
    if not (filename.startswith("did_") and is_safe_name(filename)):
        raise HTTPException(400, "invalid")
    p = safe_join(settings.temp_dir, filename)
    rest = filename[4:].split("_", 1)[0]
    if rest:
        upload_owner.require(rest, request)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(str(p), media_type="image/png",
                        headers={"Cache-Control": "no-store"})


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    from app.core.safe_paths import require_uuid_hex
    from ...core import upload_owner
    require_uuid_hex(upload_id, "upload_id")
    upload_owner.require(upload_id, request)
    out = _out_path(upload_id)
    if not out.exists():
        raise HTTPException(404, "尚未處理或已過期")
    orig_name = "deidentified.pdf"
    try:
        n = (settings.temp_dir / f"did_{upload_id}_name.txt").read_text(encoding="utf-8").strip()
        if n:
            stem = Path(n).stem
            orig_name = f"{stem}_deidentified.pdf"
    except Exception:
        pass
    return FileResponse(str(out), media_type="application/pdf",
                        filename=orig_name)

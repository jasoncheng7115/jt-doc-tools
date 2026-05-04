"""Endpoints for PDF 字數統計."""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import uuid
from collections import Counter
from pathlib import Path

import fitz
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ...config import settings
from ...core.http_utils import content_disposition


router = APIRouter()

_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_EN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'-]*")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_SENT_SPLIT_RE = re.compile(r"[。！？!?\.]+\s*|[\r\n]{2,}")
_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")

# A small English stopword list — intentionally minimal so we don't pull NLTK.
_EN_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "of", "to", "in",
    "on", "at", "for", "with", "by", "from", "as", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "shall",
    "can", "this", "that", "these", "those", "it", "its", "i", "you",
    "he", "she", "we", "they", "them", "his", "her", "our", "their",
    "not", "no", "so", "than", "such", "into", "out", "up", "down", "off",
    "over", "under", "about", "above", "below", "between", "through",
    "all", "any", "some", "more", "most", "other", "same", "than", "too",
}


def _classify_chars(text: str) -> dict:
    """Count chars by category: cjk, en_letter, digit, punct, whitespace, other."""
    cjk = en = digit = punct = ws = other = 0
    for ch in text:
        if not ch:
            continue
        if _CJK_RE.match(ch):
            cjk += 1
        elif ch.isspace():
            ws += 1
        elif ch.isdigit():
            digit += 1
        elif ch.isalpha():
            en += 1
        else:
            cat = unicodedata.category(ch)
            if cat.startswith("P") or cat.startswith("S"):
                punct += 1
            else:
                other += 1
    return {
        "cjk": cjk, "en_letter": en, "digit": digit,
        "punct": punct, "whitespace": ws, "other": other,
    }


def _count_text(text: str) -> dict:
    """Word/sentence/paragraph counts for one chunk of text."""
    chars = _classify_chars(text)
    en_words = _EN_WORD_RE.findall(text)
    numbers = _NUM_RE.findall(text)
    paragraphs = [p for p in _PARA_SPLIT_RE.split(text) if p.strip()]
    # rough sentence count — split on CJK end-marks AND ASCII end-marks
    sentences = [s for s in _SENT_SPLIT_RE.split(text) if s.strip()]
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return {
        "chars": chars,
        "char_total": sum(chars.values()),
        "char_no_ws": sum(chars.values()) - chars["whitespace"],
        "cjk_chars": chars["cjk"],
        "en_words": len(en_words),
        "numbers": len(numbers),
        "word_total": chars["cjk"] + len(en_words),
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "lines": len(lines),
    }


def _top_freq(text: str, top: int = 20) -> dict:
    """Top N for: CJK single chars, CJK bigrams, English words (stopwords filtered)."""
    cjk_chars = _CJK_RE.findall(text)
    cjk_counter = Counter(cjk_chars)

    # bigrams: walk through text and keep only consecutive CJK pairs
    bigrams: list[str] = []
    last_was_cjk = False
    last_ch = ""
    for ch in text:
        is_cjk = bool(_CJK_RE.match(ch))
        if is_cjk and last_was_cjk:
            bigrams.append(last_ch + ch)
        last_was_cjk = is_cjk
        last_ch = ch
    bigram_counter = Counter(bigrams)

    en_words = [w.lower() for w in _EN_WORD_RE.findall(text)
                if w.lower() not in _EN_STOPWORDS and len(w) >= 2]
    en_counter = Counter(en_words)

    return {
        "cjk_chars":   [{"term": t, "count": c} for t, c in cjk_counter.most_common(top)],
        "cjk_bigrams": [{"term": t, "count": c} for t, c in bigram_counter.most_common(top)],
        "en_words":    [{"term": t, "count": c} for t, c in en_counter.most_common(top)],
    }


def _reading_minutes(cjk_chars: int, en_words: int) -> float:
    """Estimate reading time in minutes. CJK 300/min, English 200/min."""
    return round(cjk_chars / 300 + en_words / 200, 1)


def _analyze_pdf(data: bytes, filename: str = "document.pdf") -> dict:
    """Open PDF bytes, extract text per page, compute all stats."""
    if not data:
        raise HTTPException(400, "empty file")
    src = settings.temp_dir / f"wc_{uuid.uuid4().hex}_in.pdf"
    src.write_bytes(data)
    try:
        with fitz.open(str(src)) as doc:
            if doc.needs_pass:
                raise HTTPException(400, "PDF 已加密，請先解密再分析")
            page_count = doc.page_count
            pages: list[dict] = []
            full_text_parts: list[str] = []
            for i in range(page_count):
                ptext = doc[i].get_text("text") or ""
                full_text_parts.append(ptext)
                pc = _count_text(ptext)
                pages.append({
                    "page": i + 1,
                    "char_total": pc["char_total"],
                    "char_no_ws": pc["char_no_ws"],
                    "cjk_chars": pc["cjk_chars"],
                    "en_words": pc["en_words"],
                    "word_total": pc["word_total"],
                    "paragraphs": pc["paragraphs"],
                    "sentences": pc["sentences"],
                    "lines": pc["lines"],
                })
            full_text = "\n".join(full_text_parts)
        full = _count_text(full_text)
        freq = _top_freq(full_text, top=20)
        # Aggregate
        avg_per_page = round(full["word_total"] / max(page_count, 1), 1)
        avg_sent_len = (
            round(full["char_no_ws"] / full["sentences"], 1)
            if full["sentences"] else 0
        )
        result = {
            "filename": filename,
            "page_count": page_count,
            "summary": {
                "char_total":   full["char_total"],
                "char_no_ws":   full["char_no_ws"],
                "cjk_chars":    full["cjk_chars"],
                "en_words":     full["en_words"],
                "numbers":      full["numbers"],
                "word_total":   full["word_total"],
                "paragraphs":   full["paragraphs"],
                "sentences":    full["sentences"],
                "lines":        full["lines"],
                "avg_per_page": avg_per_page,
                "avg_sent_len": avg_sent_len,
                "reading_min":  _reading_minutes(full["cjk_chars"], full["en_words"]),
                "has_text":     full["word_total"] > 0,
            },
            "char_breakdown": full["chars"],
            "pages": pages,
            "freq": freq,
        }
        return result
    finally:
        try:
            src.unlink()
        except Exception:
            pass


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    from ...core.llm_settings import llm_settings
    return templates.TemplateResponse("pdf_wordcount.html", {
        "request": request,
        "llm_enabled": llm_settings.is_enabled(),
        "llm_model": llm_settings.get_model_for("pdf-wordcount") if llm_settings.is_enabled() else "",
    })


@router.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    llm_summarize: str = Form(""),  # "1" → 加 LLM 摘要 + 關鍵字
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    result = _analyze_pdf(data, file.filename or "document.pdf")
    if str(llm_summarize).lower() in ("1", "true", "on", "yes"):
        try:
            llm_extra = await _llm_summarize(data)
            if llm_extra:
                result["llm"] = llm_extra
        except Exception as exc:
            import logging as _lg
            _lg.getLogger(__name__).warning("LLM summarize failed: %s", exc)
            result["llm"] = {"error": str(exc)}
    return JSONResponse(result)


async def _llm_summarize(pdf_bytes: bytes) -> dict:
    """Extract document text + ask LLM for summary + keywords. Returns
    {summary: str, keywords: [str], model: str} or empty dict on failure."""
    from ...core.llm_settings import llm_settings as _llms
    import asyncio as _asyncio
    client = _llms.make_client()
    if client is None:
        return {}
    model = _llms.get_model_for("pdf-wordcount")
    # Extract text
    import fitz
    text_parts = []
    try:
        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
            for page in doc:
                text_parts.append(page.get_text("text") or "")
    except Exception:
        return {}
    full_text = "\n\n".join(text_parts).strip()
    if not full_text:
        return {}
    # Truncate to manageable size for LLM
    max_chars = 12000
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n\n…（後續省略）"
    prompt = (
        "你是文件助手。請根據下面文件內容，產出 JSON：\n"
        "  {\"summary\": \"3-5 句重點摘要（不要超過 200 字）\", "
        "\"keywords\": [\"前 10 大重要概念 / 名詞，由重要到次要排序\"]}\n"
        "**只能回 JSON，不要 markdown / 解釋 / 前綴 / 後綴 / ```json``` 包裝。**\n\n"
        f"文件內容：\n{full_text}"
    )
    def _call():
        return client.text_query(prompt=prompt, model=model,
                                  temperature=0.0, think=False)
    try:
        resp = await _asyncio.to_thread(_call)
    except Exception as e:
        return {"error": str(e), "model": model}
    raw = (resp or "").strip()
    import re as _re
    if raw.startswith("```"):
        raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=_re.MULTILINE)
    try:
        import json as _json
        parsed = _json.loads(raw)
        if not isinstance(parsed, dict):
            return {"error": "LLM 回應非 JSON object", "model": model}
        return {
            "summary": str(parsed.get("summary") or "").strip(),
            "keywords": [str(k) for k in (parsed.get("keywords") or [])][:15],
            "model": model,
        }
    except Exception as e:
        return {"error": f"LLM 回應解析失敗：{e}", "model": model,
                "raw": raw[:300]}


@router.post("/api/pdf-wordcount")
async def api_wordcount(file: UploadFile = File(...)):
    """Public API endpoint returning JSON stats."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "only PDF supported")
    data = await file.read()
    return JSONResponse(_analyze_pdf(data, file.filename or "document.pdf"))


@router.post("/export-csv")
async def export_csv(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "只支援 PDF")
    data = await file.read()
    result = _analyze_pdf(data, file.filename or "document.pdf")
    buf = io.StringIO()
    buf.write("﻿")  # UTF-8 BOM for Excel
    writer = csv.writer(buf)
    writer.writerow(["page", "char_total", "char_no_ws", "cjk_chars",
                     "en_words", "word_total", "paragraphs", "sentences", "lines"])
    for p in result["pages"]:
        writer.writerow([p["page"], p["char_total"], p["char_no_ws"],
                         p["cjk_chars"], p["en_words"], p["word_total"],
                         p["paragraphs"], p["sentences"], p["lines"]])
    base = Path(file.filename or "document.pdf").stem
    headers = {"Content-Disposition": content_disposition(f"{base}_wordcount.csv")}
    return Response(buf.getvalue().encode("utf-8"),
                    media_type="text/csv; charset=utf-8", headers=headers)

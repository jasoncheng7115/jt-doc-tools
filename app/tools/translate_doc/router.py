"""逐句翻譯 — backend.

API:
    GET  /                         → tool 頁面
    POST /extract-text             → 上傳 PDF / DOCX / TXT，回 sentences[]
    POST /translate-batch          → 對一批 sentences 翻譯，回 translations[]
    POST /translate-one            → 單句重新翻譯（為了 UI 重生成單句）
    POST /api/translate-doc        → 一次性 API：吃 text + target_lang，回對齊結果
                                      （符合「所有功能須有 API」規範）

LLM 設定來自 admin (`/admin/llm-settings`)。LLM 沒啟用時 endpoints 一律
回 503 + 提示去設定。我們不在這裡再開設定 UI。

注意：long doc 切句後會逐句送到 LLM，timeout 會放寬到 admin 設定值；
但建議單次 < 500 句以避免 UI hang 太久。500 句以上請自行分段。
"""
from __future__ import annotations

import io
import json
import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from ...config import settings
from ...core.llm_settings import llm_settings


router = APIRouter()


# 切句粒度：句點 / 中文句號 / 換行 / 問號 / 驚嘆號。中文與英文混合時
# 句尾標點要連帶吃進句子內，避免「。」獨立成句。
_SENT_SPLIT_RE = re.compile(
    r"(?<=[\.!?。！？])\s+|(?<=[\.!?。！？])(?=[A-Z一-鿿])|\n+"
)
# 上限保護：超過這數量就不全送 LLM（會 timeout / 沒意義）
MAX_SENTENCES = 800
MAX_TEXT_BYTES = 2 * 1024 * 1024  # 2 MB raw text


def _split_sentences(text: str) -> list[str]:
    """把長文字切成「逐句」的 list。空白行 / 純 whitespace 略過。
    保留行序，每句去頭尾空白。"""
    if not text:
        return []
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw = _SENT_SPLIT_RE.split(text)
    out = []
    for s in raw:
        s = (s or "").strip()
        if s:
            out.append(s)
    return out


def _extract_text_from_file(filename: str, data: bytes) -> str:
    """支援 PDF / DOCX / TXT。其他副檔名拋 400。"""
    name = (filename or "").lower()
    if name.endswith(".txt") or name.endswith(".md"):
        # Try utf-8 first, then fall back to common encodings.
        for enc in ("utf-8", "utf-8-sig", "big5", "cp950", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")
    if name.endswith(".pdf"):
        try:
            import fitz
        except ImportError:
            raise HTTPException(500, "PyMuPDF not available")
        try:
            with fitz.open(stream=data, filetype="pdf") as doc:
                pages = []
                for page in doc:
                    pages.append(page.get_text("text"))
                return "\n\n".join(pages)
        except Exception as e:
            raise HTTPException(400, f"PDF parse failed: {e}")
    if name.endswith(".docx"):
        try:
            from docx import Document
        except ImportError:
            raise HTTPException(500, "python-docx not available")
        try:
            doc = Document(io.BytesIO(data))
            paras = [p.text for p in doc.paragraphs if p.text.strip()]
            return "\n\n".join(paras)
        except Exception as e:
            raise HTTPException(400, f"DOCX parse failed: {e}")
    raise HTTPException(400, f"unsupported file type: {filename}")


def _detect_language(text: str) -> str:
    """超輕量語言偵測。看前 N 個字符的 unicode block 比例：
        - 30%+ CJK chars → 'zh' (粗略；繁中簡中沒分)
        - 否則 → 'en' (假設使用者翻的多半是中英對譯)
    回 ISO 639-1 二字碼，未知回 'auto'。"""
    sample = text[:2000]
    if not sample:
        return "auto"
    cjk_n = sum(1 for c in sample if "一" <= c <= "鿿")
    total_letters = sum(1 for c in sample if c.isalpha() or "一" <= c <= "鿿")
    if total_letters == 0:
        return "auto"
    if cjk_n / total_letters > 0.3:
        return "zh"
    return "en"


_LANG_NAMES = {
    "zh-TW": "繁體中文", "zh-CN": "簡體中文", "zh": "中文",
    "en": "英文", "ja": "日文", "ko": "韓文",
    "fr": "法文", "de": "德文", "es": "西班牙文", "vi": "越南文",
    "th": "泰文", "id": "印尼文", "ru": "俄文",
}


def _build_prompt(src_text: str, source_lang: str, target_lang: str) -> str:
    src_name = _LANG_NAMES.get(source_lang, source_lang or "原文")
    tgt_name = _LANG_NAMES.get(target_lang, target_lang or "目標語言")
    return (
        f"請把下面這句從「{src_name}」翻譯為「{tgt_name}」，"
        "翻譯要忠實、通順、符合該語言慣用的書寫風格。"
        "只輸出翻譯結果，不要附上原文、不要加任何標記、不要解釋、"
        "不要 markdown、不要前綴、不要後綴。"
        "如果原文是專有名詞或無法翻譯，照原樣輸出即可。\n\n"
        f"原文：{src_text}"
    )


def _translate_one(client, model: str, src: str,
                   source_lang: str, target_lang: str) -> dict:
    """單句翻譯 worker（給並行 executor 用）。
    空字串直接回 empty，不發 LLM call。任何 exception 回 error 字串，
    不 raise — caller 用 list 收集所有結果。"""
    if not src.strip():
        return {"src": src, "translated": "", "error": ""}
    prompt = _build_prompt(src, source_lang, target_lang)
    try:
        resp = client.text_query(
            prompt=prompt, model=model, temperature=0.0, think=False,
        )
        translated = (resp or "").strip()
        translated = re.sub(r"^(翻譯[:：]?\s*|Translation:\s*)",
                            "", translated, flags=re.IGNORECASE)
        return {"src": src, "translated": translated, "error": ""}
    except Exception as e:
        return {"src": src, "translated": "", "error": f"LLM 失敗：{e}"}


def _translate_sentences(
    sentences: list[str], source_lang: str, target_lang: str,
) -> list[dict]:
    client = llm_settings.make_client()
    if client is None:
        raise HTTPException(503, "LLM 服務未啟用，請到「設定 → LLM 設定」開啟")
    # Per-tool 模型覆寫優先；admin 在 LLM 設定頁可以給 translate-doc 指定
    # 不同模型（例如純文字翻譯用 qwen3:32b，校驗仍用 gemma4:26b）
    model = llm_settings.get_model_for("translate-doc")
    conf = llm_settings.get()
    # 並行數 — admin 在 LLM 設定可調，預設 4。對 1-句 case 退化成同步呼叫。
    concurrency = max(1, min(16, int(conf.get("translate_concurrency", 4))))
    n = len(sentences)
    if n <= 1 or concurrency == 1:
        return [_translate_one(client, model, src, source_lang, target_lang)
                for src in sentences]
    # ThreadPoolExecutor.map 保證 output 順序對應 input 順序（重要 — UI
    # 是依索引貼回原文位置）。內部 LLMClient 的 httpx 是同步的，所以用
    # thread pool 而不是 asyncio.gather。
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        results = list(ex.map(
            lambda src: _translate_one(client, model, src, source_lang, target_lang),
            sentences,
        ))
    return results


# ---- routes -----------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    s = llm_settings.get()
    return templates.TemplateResponse(
        "translate_doc.html",
        {
            "request": request,
            "llm_enabled": bool(s.get("enabled")),
            "llm_model": llm_settings.get_model_for("translate-doc"),
            "llm_default_model": s.get("model", ""),
            "llm_url": s.get("base_url", ""),
        },
    )


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "file too large (limit 50 MB)")
    text = _extract_text_from_file(file.filename or "", data)
    sentences = _split_sentences(text)
    detected = _detect_language(text)
    return {
        "filename": file.filename,
        "char_count": len(text),
        "sentence_count": len(sentences),
        "sentences": sentences[:MAX_SENTENCES],
        "truncated": len(sentences) > MAX_SENTENCES,
        "detected_lang": detected,
    }


@router.post("/translate-batch")
async def translate_batch(request: Request):
    if not llm_settings.is_enabled():
        raise HTTPException(503, "LLM 服務未啟用")
    body = await request.json()
    sentences = body.get("sentences") or []
    if not isinstance(sentences, list):
        raise HTTPException(400, "sentences must be array")
    if len(sentences) > MAX_SENTENCES:
        raise HTTPException(
            400, f"too many sentences ({len(sentences)} > {MAX_SENTENCES})")
    sentences = [str(s) for s in sentences]
    source_lang = str(body.get("source_lang") or "auto")
    target_lang = str(body.get("target_lang") or "zh-TW")
    if source_lang == "auto":
        source_lang = _detect_language("\n".join(sentences[:50]))
    # CRITICAL: 不能直接呼叫 _translate_sentences — 它是同步的 (內部
    # ThreadPoolExecutor + 阻塞 .map())，會卡住整個 async event loop →
    # 翻譯期間其他 request 全部排隊（v1.4.13 客戶回報：翻譯時開新分頁
    # 進其他工具都打不開）。用 asyncio.to_thread 把它送到 default
    # executor 跑，async loop 就能繼續處理其他請求。
    import asyncio as _asyncio
    results = await _asyncio.to_thread(
        _translate_sentences, sentences, source_lang, target_lang)
    return {
        "source_lang": source_lang,
        "target_lang": target_lang,
        "results": results,
    }


@router.post("/translate-one")
async def translate_one(request: Request):
    if not llm_settings.is_enabled():
        raise HTTPException(503, "LLM 服務未啟用")
    body = await request.json()
    src = str(body.get("src") or "").strip()
    if not src:
        raise HTTPException(400, "src is empty")
    source_lang = str(body.get("source_lang") or "auto")
    target_lang = str(body.get("target_lang") or "zh-TW")
    if source_lang == "auto":
        source_lang = _detect_language(src)
    import asyncio as _asyncio
    results = await _asyncio.to_thread(
        _translate_sentences, [src], source_lang, target_lang)
    return results[0] if results else {"src": src, "translated": "",
                                       "error": "no result"}


# ---- public API endpoint (符合「所有功能須有 API」規範) ---------------------

@router.post("/api/translate-doc")
async def api_translate_doc(request: Request):
    """One-shot translate API — 吃 raw text，回對齊好的中英並排 array。

    Body (JSON):
        {
          "text": "要翻譯的文字（必填）",
          "source_lang": "auto" | "en" | "zh" | "zh-TW" | ...   (default: auto)
          "target_lang": "zh-TW"   (default: zh-TW)
        }

    Response:
        {
          "source_lang": "...", "target_lang": "...",
          "results": [{"src": "...", "translated": "...", "error": ""}, ...]
        }
    """
    if not llm_settings.is_enabled():
        raise HTTPException(503, "LLM 服務未啟用 — 請到 admin / LLM 設定開啟")
    body = await request.json()
    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "text is empty")
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise HTTPException(400, f"text too large (limit {MAX_TEXT_BYTES} bytes)")
    sentences = _split_sentences(text)
    if not sentences:
        return {"source_lang": "auto", "target_lang": "zh-TW", "results": []}
    if len(sentences) > MAX_SENTENCES:
        raise HTTPException(
            400, f"too many sentences after split ({len(sentences)} > {MAX_SENTENCES})")
    source_lang = str(body.get("source_lang") or "auto")
    target_lang = str(body.get("target_lang") or "zh-TW")
    if source_lang == "auto":
        source_lang = _detect_language(text)
    # CRITICAL: 不能直接呼叫 _translate_sentences — 它是同步的 (內部
    # ThreadPoolExecutor + 阻塞 .map())，會卡住整個 async event loop →
    # 翻譯期間其他 request 全部排隊（v1.4.13 客戶回報：翻譯時開新分頁
    # 進其他工具都打不開）。用 asyncio.to_thread 把它送到 default
    # executor 跑，async loop 就能繼續處理其他請求。
    import asyncio as _asyncio
    results = await _asyncio.to_thread(
        _translate_sentences, sentences, source_lang, target_lang)
    return {
        "source_lang": source_lang,
        "target_lang": target_lang,
        "results": results,
    }

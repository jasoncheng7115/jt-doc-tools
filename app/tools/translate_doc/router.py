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


_LIST_MARKER_RE = re.compile(
    r"^("
    r"[IVXLCM]+\.?"          # Roman numerals: I, II, III, IV.
    r"|[ivxlcm]+\.?"         # lower-case roman: i, ii.
    r"|\d+[.)]?"             # 1, 1., 1)
    r"|[A-Za-z][.)]"         # A., a), b)
    r"|[•◦▪■◆●○\-*]"         # bullet glyphs
    r")$"
)


def _merge_list_markers(paras: list[str]) -> list[str]:
    """Merge bare "list marker" paragraphs (e.g. "I.", "1)", "•") into the
    next paragraph. Common case: original document put the marker on its
    own line — we'd otherwise translate them in isolation, losing context
    and giving the LLM nothing to translate from.
    """
    merged: list[str] = []
    pending: str | None = None
    for p in paras:
        s = p.strip()
        if not s:
            continue
        if pending is not None:
            merged.append(f"{pending} {s}")
            pending = None
            continue
        if len(s) <= 6 and _LIST_MARKER_RE.match(s):
            pending = s
            continue
        merged.append(s)
    if pending is not None:
        merged.append(pending)
    return merged


def _extract_text_from_odf(data: bytes, kind: str) -> str:
    """Parse OpenDocument (ODT / ODS / ODP) `content.xml` directly.

    ODF files are zips containing `content.xml`; the text we want lives in
    `<text:p>` / `<text:h>` elements (declared in the urn:oasis text
    namespace). We strip namespaces by hand instead of pulling in `lxml`.

    We don't bother going through soffice — that would require the binary,
    cost a subprocess + temp files per upload, and lose paragraph breaks
    when round-tripping through PDF.
    """
    import zipfile
    from xml.etree import ElementTree as ET
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            with zf.open("content.xml") as fp:
                tree = ET.parse(fp)
    except (zipfile.BadZipFile, KeyError) as e:
        raise HTTPException(400, f"{kind.upper()} parse failed: {e}")
    text_ns = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    raw_paras: list[str] = []
    for el in tree.iter():
        if el.tag in (text_ns + "p", text_ns + "h"):
            # itertext walks all descendants — joins text-runs split by spans
            txt = "".join(el.itertext()).strip()
            if txt:
                raw_paras.append(txt)
    return "\n\n".join(_merge_list_markers(raw_paras))


def _extract_text_from_file(filename: str, data: bytes) -> str:
    """支援 PDF / DOCX / ODT / ODS / ODP / TXT / MD。其他副檔名拋 400。"""
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
                paras: list[str] = []
                for page in doc:
                    text = page.get_text("text") or ""
                    # PyMuPDF separates paragraphs with blank lines; split on
                    # 2+ newlines to keep paragraph boundaries intact while
                    # merging soft line wraps within a paragraph.
                    for chunk in re.split(r"\n\s*\n+", text):
                        chunk = chunk.replace("\n", " ").strip()
                        if chunk:
                            paras.append(chunk)
                return "\n\n".join(_merge_list_markers(paras))
        except Exception as e:
            raise HTTPException(400, f"PDF parse failed: {e}")
    # Office / ODF：交給 soffice 匯出 UTF-8 文字（等同「打開→另存為純文字」），
    # 段落結構跟使用者在 OxOffice/LibreOffice 看到的一致。比直接 parse XML
    # 多 ~1-2 秒 subprocess，但結果穩定 — 列表編號、表格、註腳都正常。
    office_exts = (".docx", ".doc", ".odt", ".ods", ".odp", ".rtf")
    if name.endswith(office_exts):
        from ...core import office_convert
        import tempfile as _tf
        suffix = "." + name.rsplit(".", 1)[-1]
        with _tf.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(data)
            src_path = Path(tf.name)
        try:
            text = office_convert.convert_to_text(src_path)
        except Exception as e:
            raise HTTPException(400, f"office 檔解析失敗：{e}")
        finally:
            try:
                src_path.unlink()
            except Exception:
                pass
        # soffice TXT 輸出本來就有清楚的段落分行；直接重排即可
        paras = [ln.strip() for ln in re.split(r"\n\s*\n+", text) if ln.strip()]
        # 把每段內的單一換行折回去（保留段落感）
        paras = [re.sub(r"\s*\n\s*", " ", p) for p in paras]
        return "\n\n".join(_merge_list_markers(paras))
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
    # 針對台灣繁體要求 LLM 用台灣慣用 IT 術語（避免大陸用語滲入）
    tw_terminology = ""
    if target_lang in ("zh-TW", "zh"):
        tw_terminology = (
            "**重要**：請使用「台灣繁體中文」用語，IT / 技術術語請用台灣業界習慣翻譯，**禁止**用大陸 / 香港用詞。對照表："
            "kernel→核心（不是「內核」）、software→軟體（不是「軟件」）、hardware→硬體（不是「硬件」）、"
            "image→圖片 / 影像（不是「圖像」）、video→影片（不是「視頻」）、network→網路（不是「網絡」）、"
            "server→伺服器（不是「服務器」）、menu→選單（不是「菜單」）、screen→螢幕（不是「屏幕」）、"
            "save→儲存（不是「保存」）、default→預設（不是「默認」）、setting→設定（不是「設置」）、"
            "file→檔案（不是「文件」）、information→訊息 / 資訊（不是「信息」）、"
            "print→列印（不是「打印」）、font→字型（不是「字體」）、document→文件（不是「文檔」）、"
            "code→程式碼（不是「代碼」）、program→程式（不是「程序」）、function→函式 / 功能（不是「函數」當動詞）、"
            "data→資料（不是「數據」）、object→物件（不是「對象」當程式術語）、array→陣列（不是「數組」）、"
            "queue→佇列（不是「隊列」）、cache→快取（不是「緩存」）、download→下載（OK 兩岸通用）、"
            "upload→上傳、login→登入（不是「登錄」）、logout→登出、user→使用者（不是「用戶」當主語）、"
            "browser→瀏覽器、driver→驅動程式、interface→介面（不是「界面」）、"
            "framework→框架、library→程式庫 / 函式庫（IT 上下文，不是「圖書館」）、"
            "feature→功能 / 特色（不是「特性」）、bug→錯誤 / 臭蟲、release→版本 / 釋出、deploy→部署、"
            "click→點擊 / 按、support→支援（不是「支持」當技術動詞）、"
            "performance→效能（不是「性能」）、optimize→最佳化（不是「優化」當形容詞）、"
            "address→位址（IP/記憶體上下文，不是「地址」）、port→連接埠 / 通訊埠（不是「端口」）、"
            "container→容器（OK）、virtualization→虛擬化、virtual machine→虛擬機（OK）。"
        )
    return (
        f"請把下面這句從「{src_name}」翻譯為「{tgt_name}」，"
        "翻譯要忠實、通順、符合該語言慣用的書寫風格。"
        + tw_terminology +
        "只輸出翻譯結果，不要附上原文、不要加任何標記、不要解釋、"
        "不要 markdown、不要前綴、不要後綴。"
        "如果原文是專有名詞或無法翻譯，照原樣輸出即可。\n\n"
        f"原文：{src_text}"
    )


_FILLER_RE = re.compile(r"^[\s_\-=·.•◦▪■◇◆●○※—–…]+$")


def _translate_one(client, model: str, src: str,
                   source_lang: str, target_lang: str) -> dict:
    """單句翻譯 worker（給並行 executor 用）。
    空字串直接回 empty，不發 LLM call。任何 exception 回 error 字串，
    不 raise — caller 用 list 收集所有結果。"""
    if not src.strip():
        return {"src": src, "translated": "", "error": ""}
    # 「填寫位」(form blank fields like ___________) 不送 LLM — 直接 echo
    # 原樣，譯文欄維持空白（前端會顯示「（填寫位）」），保留原文版面對齊。
    if _FILLER_RE.match(src):
        return {"src": src, "translated": "", "error": "", "skipped": "filler"}
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
    from ...core.office_convert import detect_engine
    return templates.TemplateResponse(
        "translate_doc.html",
        {
            "request": request,
            "llm_enabled": bool(s.get("enabled")),
            "llm_model": llm_settings.get_model_for("translate-doc"),
            "llm_default_model": s.get("model", ""),
            "llm_url": s.get("base_url", ""),
            "office_engine": detect_engine(),
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

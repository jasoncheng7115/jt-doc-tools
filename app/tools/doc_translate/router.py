"""文件翻譯：整份辦公文件翻成另一種語言，**產出同格式、同版面的檔案**。

流程：上傳 → 抽出可翻譯的段落 → 背景作業逐段翻譯 → 寫回原檔 → 轉 PDF 出預覽。

翻譯本身重用「逐句翻譯」那一套（同一個 prompt、同樣的台灣用語對照表、同樣的
領域提示），差別只在**產出**：那邊給對照表，這邊把譯文寫回原檔。
"""
from __future__ import annotations

import asyncio
import io
import json
import threading
import time
import uuid
from pathlib import Path
import re
from dataclasses import dataclass
from typing import Optional

# 使用者上傳的 XML 一律走 defusedxml 剖析（實體展開 DoS）
from defusedxml.ElementTree import fromstring as _safe_fromstring

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from ...config import settings
from ...core import office_convert, office_text_map as otm, pdf_preview
from ...core import translation_glossary as _gloss
from ...core import atomic_json
from ...core import safe_paths as _sp, upload_owner as _uo
from ...core.http_utils import content_disposition
from ...core.job_manager import job_manager
from ...core.llm_settings import llm_settings
# 翻譯的邏輯與「逐句翻譯」共用一份 —— prompt、台灣用語對照表、領域提示、
# 「這段不用翻」的判斷都在那邊，複製一份一定會漂掉。
from ..translate_doc.router import (
    _build_prompt, _build_prompt_prefix, _detect_language, _is_no_translate,
    _translate_one, _warmup_llm, _LANG_NAMES,
)

router = APIRouter()

#: 預覽幾頁（跟其他工具一致）
PREVIEW_PAGES = 6
#: 單一檔案可翻的段落上限（預設值，管理員可調）。實測每段約 0.5~0.6 秒，
#: 2 萬段大約 3 小時 —— 背景作業跑得完，中途也可以按停止。
MAX_UNITS = 20000


def _src_path(upload_id: str) -> Path:
    return settings.temp_dir / f"dt_{upload_id}_src"


def _out_path(upload_id: str, ext: str) -> Path:
    return settings.temp_dir / f"dt_{upload_id}_out{ext}"


def _meta_path(upload_id: str) -> Path:
    return settings.temp_dir / f"dt_{upload_id}_meta.json"


def _read_meta(upload_id: str) -> dict:
    p = _meta_path(upload_id)
    if not p.exists():
        raise HTTPException(410, "上傳已過期，請重新上傳")
    return json.loads(p.read_text(encoding="utf-8"))


@dataclass
class _Piece:
    """一「行」原文。一個段落（試算表的一格）可能有好幾行 —— 換行要原樣保留。"""
    unit: int
    text: str
    result: Optional[str] = None


#: 一次請求最多合併幾段、以及合併後的原文長度上限。
#: **為什麼要合併**：翻成繁中時，那張台灣用語對照表就佔了 1,250 字元 ——
#: 一段一次請求的話，真正的內容不到 prompt 的 1%，等於把同一份指令送幾百遍。
#: 實測 200 段的文件，逐段送要一分多鐘以上。
BATCH_MAX_SEGMENTS = 40
#: 合併後的原文字數上限。**不要以為調大就會快** —— 同一份 343 段的真實文件實測：
#:   1,200 字元 → 129 批 / 129 次請求 / 0 次重試 / **607 秒**
#:   1,200 字元 → 110 批 / 305 次請求 / 17 批漏段 / 878 秒（同設定，模型當天狀況較差）
#:   4,000 字元 →  34 批 /  69 次請求 / **35 批漏段** / **1,361 秒**
#: 漏段的成本**跟批次大小成正比**：40 行的一批漏一段，那一整段生成就整個白做。
#: 所以批次要訂在「模型幾乎都照格式回」的大小，不是「指令成本攤得最平」的大小。
BATCH_MAX_CHARS = 1200
#: 段落標記。刻意用少見的符號，避免跟內文撞在一起。
_SEG_OPEN, _SEG_CLOSE = "⟦", "⟧"
_SEG_RE = re.compile(r"^\s*⟦\s*(\d+)\s*⟧\s?(.*)$")
#: 模型偶爾會在標記中間吐出**位元組 token 的字面寫法**（`⟦<0xC2>5⟧`）——
#: 那是 tokenizer 遇到不成字的位元組時的退路，會原樣變成文字。實測 gemma4:26b
#: 翻 40 段的批次時**每次都會出現一次**，於是那一段解析不到、整批被判定漏段
#: → 對切重試 → 白跑一次生成。正文不可能出現 `<0x??>` 這種東西，解析前直接拿掉。
_BYTE_TOKEN_RE = re.compile(r"<0x[0-9A-Fa-f]{2}>")


#: 一批原文最多佔上下文長度的多少（字數對 token）。實測（2026-11-09，推論機的 gemma4:26b 建 4096 的版本、
#: 中文→英文）：中文原文約 1 字 ≈ 1 token、譯文跟原文差不多，**提示＋輸出**要一起放得下。
#: 2,240 字用掉 3,976 token 剛好擠進 4096；3,029 字起輸出被截斷、整批解析失敗再對切重跑。
#: 取 0.4 留給指令（約 350 token）與譯文偏長的語言。
CONTEXT_CHAR_RATIO = 0.4


def _context_char_cap(client, model: str, char_cap: int) -> int:
    """依模型實際的上下文長度夾小一批的字數。讀不到（不是 Ollama、經過閘道、出錯）就照設定。

    上下文不夠時 Ollama 不會報錯，只會把輸出切斷：那一批對不上段數、對切重跑，結果正確但慢很多。
    先夾小就不用白跑那一次。
    """
    try:
        probe = client.context_probe(model) or {}
    except Exception:  # noqa: BLE001 — 讀不到上下文不影響翻譯本身
        return char_cap
    eff = probe.get("effective")
    if not isinstance(eff, int) or isinstance(eff, bool) or eff <= 0:
        return char_cap
    cap = max(200, int(eff * CONTEXT_CHAR_RATIO))
    if cap < char_cap:
        import logging
        logging.getLogger(__name__).info("文件翻譯：模型上下文 %d，一批字數 %d → %d", eff, char_cap, cap)
        return cap
    return char_cap


def _make_batches(indexes: list[int], items: list,
                  seg_cap: int = BATCH_MAX_SEGMENTS,
                  char_cap: int = BATCH_MAX_CHARS) -> list[list[int]]:
    """把要翻的段落切成幾批。順序保持原樣，方便對回去。"""
    out: list[list[int]] = []
    cur: list[int] = []
    size = 0
    for i in indexes:
        n = len(items[i].text)
        if cur and (len(cur) >= seg_cap or size + n > char_cap):
            out.append(cur)
            cur, size = [], 0
        cur.append(i)
        size += n
    if cur:
        out.append(cur)
    return out


def _build_batch_prompt(texts: list[str], source_lang: str, target_lang: str,
                        domain: str) -> str:
    """把好幾段合成一次請求。指令部分與逐句翻譯**共用同一份**。"""
    body = "\n".join(f"{_SEG_OPEN}{i + 1}{_SEG_CLOSE}{t}" for i, t in enumerate(texts))
    return (
        _build_prompt_prefix(source_lang, target_lang, domain)
        + f"下面有 {len(texts)} 段文字，每段前面有 {_SEG_OPEN}編號{_SEG_CLOSE} 標記。"
        f"請**逐段翻譯**，輸出時每段自成一行、並保留原本的 {_SEG_OPEN}編號{_SEG_CLOSE} 標記，"
        "編號與段數都要與原文完全一致，不可以合併、拆分、增加或省略任何一段。"
        "標記後面只放譯文，不要附上原文。\n\n"
        + body
    )


#: 指令裡才有的字串。回覆裡出現＝模型把 prompt 原樣吐回來了。
_ECHO_MARKS = ("只輸出翻譯結果", "逐段翻譯", "編號與段數都要與原文完全一致")


def _looks_like_echo(reply: str) -> bool:
    return any(m in reply for m in _ECHO_MARKS)


def _parse_batch_reply(reply: str, count: int) -> Optional[list[str]]:
    """把回覆拆回每段的譯文；對不上就回 None（呼叫端退回逐段翻）。

    **段數對不上時絕對不可以硬湊** —— 那會把 A 段的譯文寫進 B 段，
    產出的文件看起來很正常，只有讀的人會發現整份意思都錯了。
    """
    got: dict[int, str] = {}
    cur: Optional[int] = None
    for line in _BYTE_TOKEN_RE.sub("", reply or "").splitlines():
        m = _SEG_RE.match(line)
        if m:
            cur = int(m.group(1))
            got[cur] = m.group(2).strip()
        elif cur is not None and line.strip():
            got[cur] = (got[cur] + " " + line.strip()).strip()
    if len(got) != count or set(got) != set(range(1, count + 1)):
        return None
    return [got[i + 1] for i in range(count)]


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "doc_translate.html", {
        "request": request,
        "llm_enabled": llm_settings.is_enabled(),
        # 跟逐句翻譯一致：畫面上要看得到「這次會用哪個模型」——
        # 使用者才知道翻譯是送到哪裡、換模型要找誰。
        "llm_model": llm_settings.get_model_for("doc-translate"),
        # server 位址只有管理員看得到（樣板裡判斷）
        "llm_url": llm_settings.base_url_for("doc-translate"),
        "langs": _LANG_NAMES,
        "accept": ",".join(otm.SUPPORTED_EXTS),
        "preview_pages": PREVIEW_PAGES,
        "glossary_pairs": _gloss.pair_counts(),
    })


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    """收檔、抽出可翻譯的段落，回報段落數與預估。"""
    data = await file.read()
    if not data:
        raise HTTPException(400, "檔案是空的")
    name = file.filename or "document"
    ext = Path(name).suffix.lower()
    if not otm.is_supported(name):
        raise HTTPException(
            400,
            "只支援辦公文件（"
            + " / ".join(e.lstrip(".") for e in otm.SUPPORTED_EXTS)
            + "）。PDF 請用「逐句翻譯」——"
            "PDF 裡沒有段落，文字是定位好的碎片，換成長度不同的譯文版面一定跑掉。")

    upload_id = uuid.uuid4().hex
    _uo.record(upload_id, request)
    src = _src_path(upload_id)
    src.write_bytes(data)

    work_ext = otm.LEGACY_TO_MODERN.get(ext, ext)
    if work_ext != ext:
        # 舊的二進位格式改不了 XML —— 先轉成新格式，翻完再轉回去。
        work = settings.temp_dir / f"dt_{upload_id}_work{work_ext}"
        try:
            await asyncio.to_thread(
                office_convert.convert_with_filter, src, work,
                work_ext.lstrip("."), _filter_for(work_ext))
        except Exception:
            raise HTTPException(400, f"這份 {ext} 讀不進來（檔案可能毀損或不是真的 {ext}）")
        work_data = work.read_bytes()
    else:
        work_data = data

    try:
        units, _state = await asyncio.to_thread(otm.extract_units, work_data, work_ext)
    except Exception:
        raise HTTPException(400, "檔案讀不進來（可能毀損，或不是真正的辦公文件）")
    if not units:
        raise HTTPException(400, "這份文件裡找不到可以翻譯的文字")
    cap = max(100, min(100000, int((llm_settings.get() or {}).get(
        "doctr_max_units", MAX_UNITS))))
    if len(units) > cap:
        raise HTTPException(
            400, f"段落太多（{len(units)}，上限 {cap}）—— 請先拆成幾份再翻。")

    atomic_json.write_json(_meta_path(upload_id), {
        "filename": name, "ext": ext, "work_ext": work_ext,
        "units": len(units),
        "chars": sum(len(u.text) for u in units),
    })
    return {
        "upload_id": upload_id,
        "filename": name,
        "units": len(units),
        "chars": sum(len(u.text) for u in units),
        "sample": [u.text[:60] for u in units[:5]],
    }


def _filter_for(ext: str) -> str:
    return {
        ".docx": "MS Word 2007 XML",
        ".xlsx": "Calc MS Excel 2007 XML",
        ".pptx": "Impress MS PowerPoint 2007 XML",
        ".doc": "MS Word 97",
        ".xls": "MS Excel 97",
        ".ppt": "MS PowerPoint 97",
    }[ext]


@router.post("/start")
async def start(request: Request):
    if not llm_settings.is_enabled():
        raise HTTPException(503, "LLM 服務未啟用 —— 請先到「LLM 設定」啟用")
    body = await request.json()
    upload_id = str(body.get("upload_id") or "").strip()
    _sp.require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    meta = _read_meta(upload_id)
    source_lang = str(body.get("source_lang") or "auto")
    target_lang = str(body.get("target_lang") or "zh-TW")
    domain = str(body.get("domain") or "")[:200]
    # 預設套用字典：管理員特地設了對照表，不該因為使用者忘記勾而失效。
    use_glossary = bool(body.get("use_glossary", True))

    def run(job) -> None:
        _run_job(job, upload_id, meta, source_lang, target_lang, domain,
                 use_glossary=use_glossary)

    job = job_manager.submit(
        "doc-translate", run,
        # `upload_id` 一送出就要記：清理暫存檔時靠它認出「這是排隊中 / 跑到一半的作業的檔案」——
        # 等跑完才記的話，排隊超過暫存保留時間（預設 2 小時）時原檔會先被清掉
        meta={"filename": meta["filename"], "total": meta["units"],
              "target_lang": target_lang, "upload_id": upload_id},
        request=request,
    )
    job.meta["view_url"] = f"/tools/doc-translate/?job={job.id}"
    return {"job_id": job.id, "total": meta["units"]}


def _run_job(job, upload_id: str, meta: dict, source_lang: str,
             target_lang: str, domain: str,
             use_glossary: bool = True) -> None:
    from concurrent.futures import ThreadPoolExecutor

    client = llm_settings.make_client("doc-translate")
    if client is None:
        raise RuntimeError("LLM 服務未啟用")
    model = llm_settings.get_model_for("doc-translate")
    conf = llm_settings.get()
    concurrency = max(1, min(16, int(conf.get("translate_concurrency", 4))))

    ext, work_ext = meta["ext"], meta["work_ext"]
    src = _src_path(upload_id)
    work = (settings.temp_dir / f"dt_{upload_id}_work{work_ext}"
            if work_ext != ext else src)
    units, state = otm.extract_units(work.read_bytes(), work_ext)
    total = len(units)
    # 批次大小走管理設定（結果頁會顯示實際請求數，照那個數字調）
    seg_cap = max(1, min(100, int(conf.get("doctr_batch_segments", BATCH_MAX_SEGMENTS))))
    char_cap = max(200, min(20000, int(conf.get("doctr_batch_chars", BATCH_MAX_CHARS))))
    if source_lang == "auto":
        source_lang = _detect_language("\n".join(u.text for u in units[:50]))

    # 翻譯對照字典（只在這個語言對真的有條目時才成立；沒有就是 None，
    # 整條路徑一個位元組都不會變）
    glossary = None
    if use_glossary:
        m = _gloss.matcher_for(source_lang, target_lang)
        glossary = m if m else None

    job.message = f"準備中…（共 {total} 段）"
    _warmup_llm(client, model)
    # **預熱之後才讀上下文**：模型載入後 `/api/ps` 才看得到實際大小（沒載入時只看得到模型自己的參數，
    # 多半是空的 → 等於沒判斷）
    char_cap = _context_char_cap(client, model, char_cap)

    out: dict[int, str] = {}
    done = 0
    lock = threading.Lock()

    def _bump(n: int) -> None:
        """n 是這次完成的**行數**。畫面上仍以段數表示（行比段多，直接顯示會嚇人）。"""
        nonlocal done
        with lock:
            done += n
            _refresh_message_locked()

    def _refresh_message_locked() -> None:
        """更新進度列。**批數是主角、段數放括號裡。**

        為什麼：合併批次之後，進度只會在「整批回來」時才跳 —— 一批 40 行、
        四條並行，看起來就是一次跳一大格然後長時間不動，使用者會以為卡住了
        （實測回報「太慢了」，但同時量到的每段耗時其實沒有變）。寫成
        「已完成 N/M 批」至少講得出「還有幾批」，跳一格代表什麼也清楚。
        """
        frac = min(1.0, done / max(1, n_pieces))
        job.progress = 0.05 + 0.75 * frac
        n_batches = len(batches) or 1
        job.message = (f"翻譯中… 已完成 {stats['done_batches']}/{n_batches} 批"
                       f"（約 {int(frac * total)}/{total} 段）")

    def one(k: int, use_glossary: bool = True) -> None:
        """單行翻譯（批次對不上時的退路）。

        `use_glossary=False` 是給「字典的佔位符在批次回覆裡壞掉」的那幾段用的
        —— 那一段已經證明模型顧不好標記，再保護一次多半還是壞。
        """
        text = pieces[k].text
        try:
            # **`_translate_one` 回的是 dict 不是字串**
            # （`{"src", "translated", "error", "skipped"}`）。
            res = _translate_one(client, model, text,
                                 source_lang, target_lang, domain,
                                 glossary=glossary if use_glossary else None)
            translated = (res.get("translated") or "").strip()
            # 翻不出來（錯誤）或被判定不用翻 → 保留原文。
            if not translated or res.get("error"):
                translated = text
            # 字典在這一段退回不保護重翻了 —— **要記進統計**。回報 0 次
            # 退回但字典其實沒生效，比不回報還糟：使用者會以為它有用。
            if (res.get("glossary") or {}).get("fallback"):
                with lock:
                    stats["gloss_fallbacks"] += 1
        except Exception:
            translated = text
        with lock:
            pieces[k].result = translated

    stats = {"requests": 0, "fallbacks": 0, "done_batches": 0,
             "gloss_fallbacks": 0}

    def run_batch(batch: list[int]) -> None:
        """一次送一批。段數對不上就**對切再試**，切到只剩一行才逐行翻。

        為什麼不直接退回逐段：批次大了以後，一次漏段就要賠上幾十次單行請求 ——
        比不合併還慢。對切之後通常一半就過了，只有真正有問題的那幾行才會
        走到逐行。
        """
        if job.cancelled:
            # **直接 return 不要 raise**：`pool.map` 會把所有批次都排進去，
            # raise 只會在收結果時炸，其餘批次照樣跑完 —— 按了取消還要等
            # 好幾分鐘。而 job_manager 是看 `job.cancelled` 決定最終狀態的，
            # raise 反而會被標成「失敗」。
            return
        texts = [pieces[k].text for k in batch]
        # 翻譯對照字典：**每一段各自保護**（每段自己的編號從 1 開始），
        # 所以某一段的標記壞掉只會退那一段，不會整批重來。
        masked, maps = [], []
        for t in texts:
            mt, mp = ((t, {}) if glossary is None else _gloss.protect(t, glossary))
            masked.append(mt)
            maps.append(mp)
        parsed = None
        if len(texts) > 1:
            try:
                reply = client.text_query(
                    prompt=_build_batch_prompt(masked, source_lang, target_lang, domain),
                    model=model, temperature=0.0, think=False)
                parsed = _parse_batch_reply(reply or "", len(texts))
                # 模型有時會把**指令連同原文一起回**（回聲）。那種回覆裡的
                # ⟦n⟧ 標記剛好對得上段數，會「解析成功」但內容其實是原文 ——
                # 產出的文件看起來正常，實際上一個字都沒翻。
                if parsed is not None and _looks_like_echo(reply or ""):
                    parsed = None
            except Exception:
                parsed = None
        if parsed is None:
            # 對不上**不可以硬湊**，錯位比慢更糟：產出的文件看起來很正常，
            # 只有讀的人會發現整份意思都錯了。
            if len(batch) == 1:
                with lock:
                    stats["requests"] += 1
                one(batch[0])
                _bump(1)
                return
            with lock:
                stats["fallbacks"] += 1
            mid = len(batch) // 2
            run_batch(batch[:mid])
            run_batch(batch[mid:])
            return
        # 還原佔位符。**還原不完整的那幾段退回逐段不保護重翻** —— 產出裡
        # 絕對不可以殘留 `⟪1⟫`，那比翻錯還明顯。
        redo: list[int] = []
        results: list[str] = []
        for k, tr, mp in zip(batch, parsed, maps):
            out, ok = _gloss.restore(tr.strip(), mp)
            if not ok:
                redo.append(k)
                results.append("")
            else:
                results.append(out)
        with lock:
            stats["requests"] += 1
            if redo:
                stats["gloss_fallbacks"] += len(redo)
            for k, tr in zip(batch, results):
                if k not in redo:
                    pieces[k].result = tr or pieces[k].text
        for k in redo:
            one(k, use_glossary=False)
        _bump(len(batch))

    # **一格裡的換行要保留。** 試算表的儲存格常是「一句話 + 好幾個項目符號」，
    # 整格丟給模型會回成一整行 —— 條列全擠成一團（使用者實測回報）。
    # 而且批次的格式是「一段一行」，多行的段落本身就會把批次結構弄亂。
    # 所以**拆成行**送，翻完再用換行接回去。
    pieces: list[_Piece] = []
    for i, u in enumerate(units):
        for line in u.text.split("\n"):
            pieces.append(_Piece(unit=i, text=line))

    todo = []
    for k, pc in enumerate(pieces):
        if not pc.text.strip() or _is_no_translate(pc.text):
            pc.result = pc.text          # 空行 / 純符號原樣保留，不佔 LLM 請求
            done += 1
        else:
            todo.append(k)
    batches = _make_batches(todo, pieces, seg_cap, char_cap)
    job.message = (f"翻譯中… 已完成 0/{len(batches) or 1} 批"
                   f"（約 0/{total} 段）")

    n_pieces = len(pieces)

    def run_top_batch(batch: list[int]) -> None:
        """跑一個**原本切好的**批次。對切重試時分母不變 —— 分母會動的進度列
        比沒有進度列更糟（使用者會以為又多出工作）。"""
        run_batch(batch)
        with lock:
            stats["done_batches"] += 1
            _refresh_message_locked()

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(run_top_batch, batches))

    # 行 → 段：用換行接回去，儲存格裡的條列就保住了
    joined: dict[int, list[str]] = {}
    for pc in pieces:
        joined.setdefault(pc.unit, []).append(
            pc.result if pc.result is not None else pc.text)
    out = {i: "\n".join(v) for i, v in joined.items()}

    if job.cancelled:
        # 取消就不要產出檔案 —— 一份只翻了一半的文件比沒有更危險：
        # 看起來是正常的檔案，實際上後半段還是原文。
        job.message = "已取消"
        return

    job.message = "寫回檔案…"
    job.progress = 0.82
    new_bytes = otm.rebuild(state, out, units, target_lang=target_lang)
    result = _out_path(upload_id, work_ext)
    result.write_bytes(new_bytes)

    if work_ext != ext:
        # 來源是舊格式 → 轉回去，使用者拿到的副檔名跟他上傳的一樣
        final = _out_path(upload_id, ext)
        office_convert.convert_with_filter(result, final, ext.lstrip("."),
                                           _filter_for(ext))
        result = final

    job.message = "產生預覽…"
    job.progress = 0.9
    pages, total_pages = _make_preview(upload_id, result, src, ext)

    stem = Path(meta["filename"]).stem
    # **一定要設 `result_path`** —— 「我的作業」的下載鈕看的是這個
    # （`has_result`），不是 meta 裡的網址。少了它，作業顯示「已完成」卻
    # 沒有任何可以下載的東西，而且自動存入工作區、保留期清理也都不會認得它。
    job.result_path = result
    job.result_filename = f"{stem}_translated{ext}"
    job.meta.update({
        "download_url": f"/tools/doc-translate/download/{upload_id}",
        "download_name": f"{stem}_translated{ext}",
        "preview_pages": pages,
        # 整份有幾頁 —— 沒有這個數字，預覽停在第 6 頁會被讀成「只翻到第 6 頁」
        "total_pages": total_pages,
        "upload_id": upload_id,
        "translated": sum(1 for i, v in out.items() if v != units[i].text),
        "total": total,
        # 診斷用：批次真的有生效嗎？請求數遠多於批數就代表模型常漏段，
        # 那時候把批次調小才有意義（沒有這個數字只能猜）。
        "batches": len(batches),
        "llm_requests": stats["requests"],
        "batch_fallbacks": stats["fallbacks"],
        # 字典：套用了幾條、有幾段因為模型顧不好標記而退回不保護重翻。
        # **要顯示出來** —— 使用者設了字典就要看得到它有沒有生效。
        "glossary_terms": len(glossary.terms) if glossary else 0,
        "glossary_fallbacks": stats["gloss_fallbacks"],
    })
    job.message = f"完成（{total} 段）"
    job.progress = 1.0


#: 試算表轉 PDF 時，**超出紙寬的欄位會被丟到後面好幾頁**（實測一份四欄的
#: 責任矩陣：72 頁，而前 6 頁全部只有 A 欄）。預覽只取前幾頁，於是使用者
#: 看到的是「只有第一欄」的畫面 —— 而這個工具的預覽正是要證明「版面沒跑掉」，
#: 看不到其他欄就證明不了任何事（使用者回報「試算表 xlsx 會超過畫面」）。
#:
#: 修法是**在預覽用的副本上**打開「調整成一頁寬」的列印設定，直接改 XML
#: （不經 openpyxl 轉存 —— 那會把圖表與圖片弄丟，預覽反而更不像原稿）。
#: 原文與譯文兩邊套一樣的設定，比對才公平。**產出的檔案完全不受影響。**
_XLSX_SHEET_RE = re.compile(r"^xl/worksheets/sheet\d+\.xml$")


def _set_tag_attrs(tag: str, **attrs: str) -> str:
    """把一個標籤的屬性設成指定值 —— 本來就有的要**換掉**，不是再寫一次。

    ⚠ 這裡踩過一次很貴的坑：原本的寫法是直接把屬性接在標籤名後面，於是
    原檔已經寫了 `fitToPage="false"` 的表單會變成
    `<pageSetUpPr fitToPage="false" fitToPage="1"/>`。**XML 不允許同名屬性
    出現兩次**，那份工作表就此讀不進去 —— 而 LibreOffice 遇到讀不進去的
    工作表**不會報錯，會安靜地當成空白表**：回傳碼 0、PDF 產得出來、
    只是裡面只剩頁首頁尾（使用者看到的就是「譯文預覽整片空白」）。
    """
    self_closing = tag.rstrip().endswith("/>")
    body = tag[1:-2] if self_closing else tag[1:-1]
    # 名字與屬性之間的空白要留著（`partition(" ")` 會把它吃掉，於是屬性
    # 直接黏在標籤名後面 —— 那不但改不到原屬性，標籤名本身也壞了）
    m = re.match(r"\s*([^\s/>]+)(.*)$", body, re.S)
    if m is None:
        return tag
    name, rest = m.group(1), m.group(2)
    for key in attrs:
        rest = re.sub(rf"\s+{re.escape(key)}\s*=\s*\"[^\"]*\"", "", rest)
        rest = re.sub(rf"\s+{re.escape(key)}\s*=\s*'[^']*'", "", rest)
    extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return f"<{name}{rest.rstrip()}{extra}{'/>' if self_closing else '>'}"


def _fit_sheet_xml(x: str) -> str:
    """把一張工作表的列印設定改成「縮成一頁寬」。"""
    m = re.search(r"<pageSetUpPr\b[^>]*?/?>", x)
    if m:
        x = x[:m.start()] + _set_tag_attrs(m.group(0), fitToPage="1") + x[m.end():]
    else:
        m = re.search(r"<sheetPr\b[^>]*?/?>", x)
        if m and m.group(0).rstrip().endswith("/>"):
            opened = m.group(0).rstrip()[:-2].rstrip() + ">"
            x = (x[:m.start()] + opened
                 + '<pageSetUpPr fitToPage="1"/></sheetPr>' + x[m.end():])
        elif m:
            # 放在 `</sheetPr>` 之前 —— schema 規定 pageSetUpPr 排在
            # tabColor / outlinePr 後面，接在開始標籤後會排錯順序
            close = x.find("</sheetPr>", m.end())
            if close < 0:
                return x
            x = x[:close] + '<pageSetUpPr fitToPage="1"/>' + x[close:]
        else:
            x = x.replace("<dimension",
                          '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>'
                          "<dimension", 1)
    m = re.search(r"<pageSetup\b[^>]*?/?>", x)
    if m:
        x = (x[:m.start()]
             + _set_tag_attrs(m.group(0), fitToWidth="1", fitToHeight="0")
             + x[m.end():])
    else:
        x = x.replace("</worksheet>",
                      '<pageSetup fitToWidth="1" fitToHeight="0"'
                      ' orientation="landscape"/></worksheet>', 1)
    return x


def _fit_to_width(data: bytes) -> bytes:
    """回傳一份「列印時縮成一頁寬」的 xlsx 副本；改不動就原樣回傳。"""
    import zipfile
    out = io.BytesIO()
    try:
        # zip 炸彈：判斷集中在 `zip_guard`，全站一份
        import io as _io
        from ...core.zip_guard import check as _zip_check
        with zipfile.ZipFile(_io.BytesIO(data)) as _zchk:
            _zip_check(_zchk)
        with zipfile.ZipFile(io.BytesIO(data)) as zin, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in zin.namelist():
                raw = zin.read(name)
                if _XLSX_SHEET_RE.match(name):
                    patched = _fit_sheet_xml(raw.decode("utf-8")).encode("utf-8")
                    # **改完一定要確認它還讀得進去**：這份副本只是拿來算
                    # 預覽圖的，寧可退回原本的列印設定（欄位會被切到後面
                    # 幾頁），也不能給 soffice 一份它會安靜當成空白表的檔案
                    try:
                        _safe_fromstring(patched)
                    except Exception:      # noqa: BLE001
                        pass
                    else:
                        raw = patched
                zout.writestr(name, raw)
    except Exception:          # noqa: BLE001 - 預覽用，改不動就照原檔轉
        return data
    return out.getvalue()


def _render_side(upload_id: str, src_file: Path, side: str, ext: str) -> int:
    """把一份檔案轉成 PDF、前幾頁存成 PNG。回傳張數；失敗回 0。

    `ext` 要由呼叫端給 —— **不可以從檔名推**：原稿是存成
    `dt_<id>_src`（沒有副檔名），拿 `suffix` 判斷的話原文那邊永遠不算
    xlsx，於是只有譯文縮成一頁寬、原文照樣被切到後面幾頁，
    兩邊條件不同，並排比對就失去意義（使用者回報「原文表格又超出去了」）。
    """
    pdf = settings.temp_dir / f"dt_{upload_id}_{side}.pdf"
    if ext.lower() == ".xlsx":
        fitted = settings.temp_dir / f"dt_{upload_id}_{side}_fit.xlsx"
        fitted.write_bytes(_fit_to_width(src_file.read_bytes()))
        src_file = fitted
    office_convert.convert_to_pdf(src_file, pdf)
    import fitz
    with fitz.open(pdf) as doc:
        total = doc.page_count
        n = min(PREVIEW_PAGES, total)
    for i in range(n):
        png = settings.temp_dir / f"dt_{upload_id}_{side}_p{i + 1}.png"
        pdf_preview.render_page_png(pdf, png, page_index=i, dpi=90)
    return n, total


def _make_preview(upload_id: str, result: Path, source: Path,
                  ext: str) -> tuple[int, int]:
    """原文與譯文各出一份前幾頁的預覽圖。預覽失敗不影響下載。

    回傳 (預覽張數, 產出檔的總頁數)。

    **兩邊都要**：這個工具要證明的是「版面沒跑掉」，只看譯文那一份看不出來 ——
    要跟原稿並排比才知道框線、表格、圖片有沒有位移。

    **總頁數要一起回報**：預覽只有前幾頁，畫面上如果沒講清楚整份有幾頁，
    使用者會以為「翻譯只做到第 N 頁」——客戶真的這樣回報過（一份 11 頁的
    文件全部翻好了，但預覽停在第 6 頁）。
    """
    try:
        n_out, total = _render_side(upload_id, result, "out", ext)
    except Exception:
        return 0, 0
    try:
        n_src, _ = _render_side(upload_id, source, "src", ext)
    except Exception:
        n_src = 0
    return (min(n_out, n_src) if n_src else n_out), total


@router.get("/preview/{upload_id}/{page}")
async def preview(upload_id: str, page: int, request: Request,
                  large: str = "", side: str = "out"):
    """縮圖（90 dpi）；`?large=1` 給放大檢視用（170 dpi，第一次要求時才算）。

    縮圖的解析度只夠看「有沒有東西」，看不出版面有沒有跑掉 —— 而這個工具的
    整個賣點就是版面沒跑掉，所以放大要給真的看得清楚的圖。
    """
    _sp.require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    if not (1 <= page <= PREVIEW_PAGES):
        raise HTTPException(400, "頁碼超出範圍")
    if side not in ("out", "src"):
        raise HTTPException(400, "side 只能是 out 或 src")
    if large == "1":
        big = settings.temp_dir / f"dt_{upload_id}_{side}_p{page}_lg.png"
        if not big.exists():
            pdf = settings.temp_dir / f"dt_{upload_id}_{side}.pdf"
            if not pdf.exists():
                raise HTTPException(404, "預覽不存在")
            await asyncio.to_thread(pdf_preview.render_page_png, pdf, big,
                                    page_index=page - 1, dpi=170)
        return FileResponse(big, media_type="image/png")
    png = settings.temp_dir / f"dt_{upload_id}_{side}_p{page}.png"
    if not png.exists():
        raise HTTPException(404, "預覽不存在")
    return FileResponse(png, media_type="image/png")


@router.get("/download/{upload_id}")
async def download(upload_id: str, request: Request):
    _sp.require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    meta = _read_meta(upload_id)
    out = _out_path(upload_id, meta["ext"])
    if not out.exists():
        raise HTTPException(404, "還沒有產出（作業可能還沒跑完）")
    stem = Path(meta["filename"]).stem
    name = f"{stem}_translated{meta['ext']}"
    return FileResponse(out, filename=name,
                        headers={"Content-Disposition": content_disposition(name)})


@router.post("/api/doc-translate", include_in_schema=True)
async def api_doc_translate(request: Request, file: UploadFile = File(...),
                            target_lang: str = Form("zh-TW"),
                            source_lang: str = Form("auto"),
                            domain: str = Form("")):
    """對外 API：上傳辦公文件，直接回翻譯好的同格式檔案（同步，會等）。"""
    if not llm_settings.is_enabled():
        raise HTTPException(503, "LLM 服務未啟用")
    up = await upload(request, file)
    upload_id = up["upload_id"]
    meta = _read_meta(upload_id)

    class _J:            # 同步路徑沒有 job 物件，給一個最小替身
        progress = 0.0
        message = ""
        cancelled = False
        meta: dict = {}

    j = _J()
    j.meta = {}
    await asyncio.to_thread(_run_job, j, upload_id, meta,
                            source_lang, target_lang, domain)
    out = _out_path(upload_id, meta["ext"])
    stem = Path(meta["filename"]).stem
    name = f"{stem}_translated{meta['ext']}"
    return FileResponse(out, filename=name,
                        headers={"Content-Disposition": content_disposition(name)})

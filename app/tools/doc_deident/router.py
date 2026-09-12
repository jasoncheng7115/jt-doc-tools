"""Endpoints for 文件去識別化 (doc-deident)."""
from __future__ import annotations

import functools
import io
import logging
import re
import asyncio as _asyncio
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
from . import redact_core as _redact

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

#: 標籤與值分屬兩個儲存格時，允許往右找多遠（PDF points）。
#:
#: A4 寬 595pt。半頁已經涵蓋一般表單「標籤欄 + 內容欄」的距離，再寬就開始
#: 把不相干的兩欄湊成一對了。
_PAIR_MAX_GAP_X = 300.0
#: 往下找時允許的垂直間距，以上一行的行高為單位。
_PAIR_MAX_GAP_Y_RATIO = 1.6


def _line_units(page) -> list[dict]:
    """把整頁攤平成一串「文字單位」，每個單位帶著自己的 spans 與 bbox。

    一個單位就是 PyMuPDF 的一條 line。表格的每個儲存格會是各自的 line
    （有時甚至在不同 block），所以「標籤在左格、值在右格」這種寫法，
    在任何單一單位裡都看不到完整的句子 —— 這正是 issue #43 的根因。
    """
    units: list[dict] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", []) or []
            if not spans:
                continue
            text_parts: list[str] = []
            span_map: list[int] = []      # 每個字元屬於哪個 span
            span_starts: list[int] = []   # 每個 span 從第幾個字元開始
            pos = 0
            for si, sp in enumerate(spans):
                t = sp.get("text") or ""
                span_starts.append(pos)
                text_parts.append(t)
                span_map.extend([si] * len(t))
                pos += len(t)
            text = "".join(text_parts)
            if not text.strip():
                continue
            units.append({
                "text": text, "spans": spans, "span_map": span_map,
                "span_starts": span_starts,
                "bbox": [min(s["bbox"][0] for s in spans),
                         min(s["bbox"][1] for s in spans),
                         max(s["bbox"][2] for s in spans),
                         max(s["bbox"][3] for s in spans)],
            })
    return units


def _join_units(a: dict, b: dict) -> dict:
    """把兩個相鄰單位接成一個「虛擬單位」，中間補一個空白。

    span 的索引與起始位置都要重算 —— 之後把比對結果換算回座標時，靠的就是
    這兩張表。用累加長度去猜起始位置會因為中間多了那個空白而整個偏一格。
    """
    sep = " "
    text = a["text"] + sep + b["text"]
    spans = list(a["spans"]) + list(b["spans"])
    off = len(a["spans"])
    # 中間那個空白掛在 a 的最後一個 span 底下（值一定落在 b，不受影響）
    span_map = list(a["span_map"]) + [len(a["spans"]) - 1] + \
        [i + off for i in b["span_map"]]
    base = len(a["text"]) + len(sep)
    span_starts = list(a["span_starts"]) + [base + s for s in b["span_starts"]]
    return {"text": text, "spans": spans, "span_map": span_map,
            "span_starts": span_starts, "junction": len(a["text"])}


def _adjacent_pairs(units: list[dict]) -> list[dict]:
    """找出「可能是同一句話被拆成兩格」的相鄰單位。

    只認兩種相鄰：**同一列往右的下一格**、**正下方的那一格**。這兩種涵蓋了
    表單的兩種排法（標籤在左 / 標籤在上），又不會把整頁任意兩段文字湊成一對。
    每個單位最多各取最近的一個，數量是線性的。
    """
    pairs: list[dict] = []
    for a in units:
        ax0, ay0, ax1, ay1 = a["bbox"]
        ah = max(1.0, ay1 - ay0)
        right = None
        below = None
        for b in units:
            if b is a:
                continue
            bx0, by0, bx1, by1 = b["bbox"]
            # 同一列：垂直方向要有一半以上重疊，且在右邊
            overlap = min(ay1, by1) - max(ay0, by0)
            if overlap > 0.5 * min(ah, max(1.0, by1 - by0)) and bx0 >= ax1 - 2:
                gap = bx0 - ax1
                if gap <= _PAIR_MAX_GAP_X and (right is None or gap < right[0]):
                    right = (gap, b)
            # 正下方：水平方向要有重疊，且緊接著
            h_overlap = min(ax1, bx1) - max(ax0, bx0)
            if h_overlap > 0.3 * min(ax1 - ax0, max(1.0, bx1 - bx0)):
                gap_y = by0 - ay1
                if 0 <= gap_y <= _PAIR_MAX_GAP_Y_RATIO * ah and \
                        (below is None or gap_y < below[0]):
                    below = (gap_y, b)
        for cand in (right, below):
            if cand:
                pairs.append(_join_units(a, cand[1]))
    return pairs


@functools.lru_cache(maxsize=1)
def _label_words() -> frozenset[str]:
    """所有「欄位標籤」的詞彙表 —— **從註冊表實算**，不另外維護寫死的清單。

    跨格配對（issue #43）處理的正是表格版面，而表格常常一整欄都是標籤，
    於是上下相鄰的兩個標籤格會被配成一對：「被告代表人」＋「銀行帳號」
    兜成一句之後，人名的式子看到前綴「代表人」就把「銀行帳號」當成人名
    （issue #50）。值本身就是一個已知欄位標籤時直接丟掉。
    """
    return frozenset(p.label for p in P.CATALOG)


def _scan_unit(unit: dict, selected_ids: set[str],
               custom_regexes: list[tuple[str, re.Pattern]],
               *, labelled_only: bool = False) -> list[dict]:
    """在一個文字單位裡找敏感資料，回傳帶座標的結果。"""
    out: list[dict] = []
    text = unit["text"]
    spans = unit["spans"]
    span_map = unit["span_map"]
    span_starts = unit["span_starts"]
    junction = unit.get("junction")

    def _emit(m, pat_label: str, pat_id: str, masked: str, grp: int = 0):
        try:
            start, end = m.start(grp), m.end(grp)
            if start < 0:
                start, end = m.start(), m.end()
        except Exception:
            start, end = m.start(), m.end()
        if start >= len(span_map) or end == 0:
            return
        if junction is not None:
            # 接起來才成立的比對才收 —— 否則同一格內就找得到的東西會被重覆
            # 收兩次（一次來自原本的單位、一次來自這個虛擬單位）。
            if not (m.start() < junction and m.end() > junction):
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
                local_s = start - span_starts[first_si]
                local_e = end - span_starts[first_si]
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
        if labelled_only and not pat.value_group:
            # 沒有標籤的式子（身分證、Email…）在單一格裡就抓得到，
            # 不需要也不應該跨格 —— 跨格只會把兩段不相干的字湊成一筆。
            continue
        for m in pat.regex.finditer(text):
            try:
                val = m.group(pat.value_group) if pat.value_group else m.group(0)
            except Exception:
                val = m.group(0)
            if val is None:
                continue
            if not pat.validate(val):
                continue
            if labelled_only and val.strip() in _label_words():
                # 配到的「值」本身就是隔壁那一格的欄位標籤 → 不是資料（issue #50）
                continue
            _emit(m, pat.label, pat.id, pat.mask(val), pat.value_group)
    # Custom user-supplied regexes (no checksum, no mask — use
    # "****" as default mask)
    if not labelled_only:
        for label, rx in custom_regexes:
            try:
                for m in rx.finditer(text):
                    val = m.group(0)
                    masked = "*" * max(1, len(val))
                    _emit(m, label, f"custom:{label}", masked)
            except Exception:
                continue
    return out


def _build_findings_for_page(page, selected_ids: set[str],
                             custom_regexes: list[tuple[str, re.Pattern]]
                             ) -> list[dict]:
    """Return a list of {type, value, masked, bbox, text} for every
    sensitive hit on this page. Each finding carries the PDF points bbox
    used later for redaction / mask rendering."""
    units = _line_units(page)
    out: list[dict] = []
    for u in units:
        out.extend(_scan_unit(u, selected_ids, custom_regexes))

    # 第二輪：標籤與值被拆到兩個儲存格的情形。
    #
    # 「出生日期」在左格、「1998-12-28」在右格時，兩邊各自都不成立 ——
    # 需要標籤的式子（出生日期、銀行帳號、駕照…）在表格裡等於整組失效，
    # 而表格正是這些欄位最常出現的地方（GitHub issue #43）。
    seen = {(f["type"], f["value"], round(f["bbox"][0], 1), round(f["bbox"][1], 1))
            for f in out}
    for pair in _adjacent_pairs(units):
        for f in _scan_unit(pair, selected_ids, custom_regexes,
                            labelled_only=True):
            key = (f["type"], f["value"],
                   round(f["bbox"][0], 1), round(f["bbox"][1], 1))
            if key in seen:
                continue
            seen.add(key)
            out.append(f)
    return out


def _default_doc_lang(request: Request) -> str:
    """預設的**文件語言**（不是介面語言）。

    這兩件事會不一樣：介面開中文、手上是一份英文合約，是很常見的情況。
    所以只拿介面語言當**預設值**，畫面上可以改。

    為什麼要有「文件語言」這個概念：台灣的市話 / 地址 / 統編式子套在英文
    文件上不是「抓不到」而是**抓錯**（實測把護照號、IBAN 片段、信用卡片段
    都當成電話）。**誤判比漏抓更危險** —— 畫面會顯示「已處理」。
    """
    try:
        from ...core.ui_locale import resolve
        return "en" if str(resolve(request)).lower().startswith("en") \
            else "zh-Hant"
    except Exception:  # noqa: BLE001
        return "zh-Hant"


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    doc_lang = _default_doc_lang(request)
    # Group patterns for UI rendering; preserve CATALOG order inside each group.
    # **依文件語言過濾** —— 不然英文文件的畫面上會列一整排台灣專屬項目。
    grouped: dict[str, list[dict]] = {}
    for p in P.catalog_for(doc_lang):
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
    return templates.TemplateResponse(request, 
        "doc_deident.html",
        {"request": request, "pattern_groups": pattern_groups,
         "doc_langs": P.DOC_LANGS, "default_doc_lang": doc_lang,
         "llm_enabled": llm_settings.is_enabled(),
         "llm_model": llm_settings.get_model_for("doc-deident") if llm_settings.is_enabled() else ""},
    )


@router.post("/detect")
async def detect(
    request: Request,
    file: UploadFile = File(...),
    types: str = Form(""),    # comma-separated pattern ids
    doc_lang: str = Form(""),  # 文件語言（決定用哪一組式子；空 = 依介面語言）
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
            await office_convert.convert_to_pdf_async(tmp, pdf_path, timeout=120.0)
        except RuntimeError:
            raise HTTPException(
                500,
                "找不到 Office 轉檔引擎（OxOffice / LibreOffice）。請到「轉檔引擎設定」確認。",
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
        # 沒指定就用**這個文件語言**的預設集合（不是整份目錄）——
        # 整份目錄會把台灣專屬的式子套到英文文件上，那是抓錯不是抓不到。
        selected_ids = P.default_ids_for(doc_lang or _default_doc_lang(request))

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
            # v1.9.38：跳過 bad-CMap noise，避免誤報識別物
            from ...core.bad_cmap import is_bad_cmap_text, clean_pdf_text
            _t = page.get_text("text") or ""
            _cleaned = "\n".join(clean_pdf_text(ln) for ln in _t.split("\n")
                                   if ln and not is_bad_cmap_text(ln))
            page_texts.append(_cleaned)

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

    # 替換模式的建議值：兩種形式都先算好一起送。前端切換開關時不用重打伺服器，
    # 也不會把使用者已經手動改過的欄位洗掉。
    # 一致性靠 Replacer 自己的對應表：同一個原值在整份文件裡固定同一個假值 ——
    # 少了這條，一份報表裡同一個客戶會變成三個不同的人。
    from .fake_values import Replacer as _Replacer
    _safe, _valid = _Replacer(valid_checksum=False), _Replacer(valid_checksum=True)
    for f in all_findings:
        f["fake_safe"] = _safe.for_value(f.get("type", ""), f.get("value", ""))
        f["fake_valid"] = _valid.for_value(f.get("type", ""), f.get("value", ""))

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

@router.post("/find")
async def find_term(request: Request):
    """在**已經上傳的**檔案裡搜一個字詞，回傳與偵測結果同格式的項目。

    給替換模式用：使用者看完偵測結果，發現還有東西該換掉（自己公司的內部
    代號、某個承辦人的名字、專案代稱…），直接在結果頁加進去就好，不必回到
    上一步重設一次自訂正規式再整份重跑。

    只做「純字串比對」不吃正規式 —— 這裡是給使用者手打字詞用的，讓他們去
    背正規式不合理，而且一個寫壞的式子可以掃掉整份文件。要用正規式的人，
    上傳那一步的「自訂 regex」本來就在。
    """
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    from ...core import safe_paths as _sp, upload_owner as _uo
    _sp.require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    term = (body.get("term") or "").strip()
    if not term:
        raise HTTPException(400, "term required")
    if len(term) > 200:
        raise HTTPException(400, "term 太長")
    case_sensitive = bool(body.get("case_sensitive"))
    pdf_path = _src_path(upload_id)
    if not pdf_path.exists():
        raise HTTPException(404, "upload expired")

    valid_checksum = bool(body.get("valid_checksum"))

    def _work() -> list[dict]:
        from .fake_values import Replacer as _Replacer
        rep = _Replacer(valid_checksum=valid_checksum)
        hits: list[dict] = []
        with fitz.open(str(pdf_path)) as doc:
            for pno in range(doc.page_count):
                page = doc[pno]
                # PyMuPDF 的 search_for 本身不分大小寫，要區分時自己再比一次
                for rect in page.search_for(term) or []:
                    if case_sensitive:
                        got = page.get_textbox(rect) or ""
                        if term not in got:
                            continue
                    size, color = _span_style_at(page, rect)
                    hits.append({
                        "type": "custom_term",
                        "type_label": "自訂字詞",
                        "value": term,
                        "masked": "*" * len(term),
                        "bbox": [rect.x0, rect.y0, rect.x1, rect.y1],
                        "font_size": size,
                        "color_int": color,
                        "page": pno + 1,
                        "fake_safe": rep.for_value("custom_term", term),
                        "fake_valid": rep.for_value("custom_term", term),
                    })
        return hits

    hits = await _asyncio.to_thread(_work)
    return {"ok": True, "term": term, "count": len(hits), "findings": hits}


def _fit_font_size(text: str, size: float, box_width: float,
                   minimum: float = 4.0) -> float:
    """字太長就縮到塞得進原本的框。塞不下也不會小於 `minimum`。"""
    if box_width <= 0 or not text:
        return size
    has_cjk = any("一" <= c <= "\u9fff" for c in text)
    fontname = "china-t" if has_cjk else "helv"
    try:
        width = fitz.get_text_length(text, fontname=fontname, fontsize=size)
    except Exception:
        return size
    if width <= box_width or width <= 0:
        return size
    return max(minimum, size * (box_width / width))


def _span_style_at(page, rect) -> tuple[float, int]:
    """找出這個位置原本的字級與顏色，貼回去才不會突兀。找不到就用預設值。"""
    try:
        for block in (page.get_text("dict") or {}).get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    sb = span.get("bbox") or []
                    if len(sb) != 4:
                        continue
                    if sb[0] - 1 <= rect.x0 and rect.x1 <= sb[2] + 1 \
                            and sb[1] - 2 <= rect.y0 and rect.y1 <= sb[3] + 2:
                        return (float(span.get("size", 11) or 11),
                                int(span.get("color", 0) or 0))
    except Exception:
        pass
    return 11.0, 0


@router.post("/process")
async def process(request: Request):
    body = await request.json()
    upload_id = (body.get("upload_id") or "").strip()
    # 歸屬驗證：沒有這道檢查，B 可以改寫 A 的輸出檔 —— 對去識別化 / 隱藏內容
    # 清除這類工具，「被別人改掉輸出」本身就是要害（A 可能拿著被還原的檔案送出）。
    from ...core import safe_paths as _sp, upload_owner as _uo
    _sp.require_uuid_hex(upload_id, "upload_id")
    _uo.require(upload_id, request)
    if not upload_id:
        raise HTTPException(400, "upload_id required")
    pdf_path = _src_path(upload_id)
    if not pdf_path.exists():
        raise HTTPException(404, "upload expired")
    mode = (body.get("mode") or "mask").strip()
    if mode not in ("redact", "mask", "replace"):
        raise HTTPException(400, "mode 必須是 redact、mask 或 replace")
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
    # **整段重活都要在執行緒裡跑**。這支端點原本直接在事件迴圈上做
    # redaction + 存檔 + 逐頁算縮圖 —— 正式機實測一份文件卡了 116 秒，
    # 那段時間**全站對所有人都不回應**（作業佇列是空的，所以調整
    # 「最大同時作業數」完全沒用）。同一支工具的公開 API
    # （`/api/doc-deident`）本來就是包在 `to_thread` 裡的，只有網頁用的
    # 這條漏掉 —— 同一件事兩份實作，只有一份修過。
    def _work() -> tuple[int, list[dict], int]:
        count_done = 0
        # 有幾頁的選取範圍落在圖片上 —— 那些頁的圖必須重新編碼才能真的把
        # 像素刪掉，檔案會變大。只有真的發生時才提醒使用者（純文字 PDF
        # 顯示「檔案可能變大」是雜訊）。
        image_pages = 0
        doc = fitz.open(str(pdf_path))
        try:
            for pno, items in by_page.items():
                if pno >= doc.page_count:
                    continue
                page = doc[pno]
                # Pass 1: redact (destroy) every selected region so the
                # original sensitive text is truly removed.
                rects = []
                for s in items:
                    bb = s.get("bbox") or []
                    if len(bb) != 4:
                        continue
                    rects.append(fitz.Rect(*bb))
                    count_done += 1
                # 文字**與圖片像素**都要清掉 —— 判準與理由都在 redact_core，
                # 不要在這裡重新寫一份（原本網頁與 API 各一份，兩邊都把
                # 安全的預設關掉了）。
                if rects and page.get_images():
                    image_pages += 1
                _redact.apply_page_redactions(page, rects, fill=mode_fill)

                # Pass 2（遮罩 / 替換）：把字貼回原位，字級與顏色照原本的。
                # 兩種模式的差別只在貼什麼字串 —— 遮罩貼 `0912****678`，
                # 替換貼使用者填的（或自動產生的）假值。
                if mode in ("mask", "replace"):
                    for s in items:
                        bb = s.get("bbox") or []
                        if len(bb) != 4:
                            continue
                        if mode == "replace":
                            masked = (s.get("replacement") or "").strip()
                        else:
                            masked = s.get("masked") or ""
                        if not masked:
                            continue
                        font_size = float(s.get("font_size") or 11.0)
                        if mode == "replace":
                            # 遮罩的字數一定跟原值一樣，替換的**使用者想填多長就多長**
                            # → 不縮字的話會直接壓到隔壁欄位，而且是無聲的：
                            # 產出的檔看起來正常，收件方才會發現兩欄黏在一起。
                            font_size = _fit_font_size(
                                masked, font_size, float(bb[2]) - float(bb[0]))
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

        return count_done, pages_info, image_pages

    count_done, pages_info, image_pages = await _asyncio.to_thread(_work)

    return {
        "ok": True,
        "processed": count_done,
        "download_url": f"/tools/doc-deident/download/{upload_id}",
        "pages": pages_info,
        # > 0 表示有圖片被重新編碼（掃描件）→ 前端才顯示「檔案可能變大」
        "image_pages": image_pages,
    }


@router.get("/preview/{filename}")
async def preview(filename: str, request: Request):
    from app.core.safe_paths import safe_join, is_safe_name
    from ...core import upload_owner
    if not (filename.startswith("did_") and is_safe_name(filename)):
        raise HTTPException(400, "invalid")
    p = safe_join(settings.temp_dir, filename)
    # fail-closed：認不出 upload_id 就不給。原本是「切掉 `did_` 再取第一段，
    # 有值才檢查」—— `did__x.png` 這種檔名切出空字串，於是完全不檢查。
    upload_owner.require_by_filename(filename, request)
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


# ---- 對外 API：單次 upload + 偵測 + 自動遮罩 / 真遮蔽 + 直接回 PDF ----
@router.post("/api/doc-deident", include_in_schema=True)
async def api_doc_deident(
    request: Request,
    file: UploadFile = File(...),
    types: str = Form(""),       # comma-separated pattern ids（空 = 該語言的 default-on）
    doc_lang: str = Form(""),    # 文件語言：zh-Hant（預設）/ en
    mode: str = Form("mask"),    # mask（同字數的 *）/ redact（黑條真遮蔽）/ replace（換成假值）
    replacements: str = Form(""),      # replace 模式：JSON 物件 {"原值": "指定的新值"}
    valid_checksum: str = Form(""),    # replace 模式："1" → 產生可通過檢查碼的假值
):
    """單次上傳 PDF / Office，依 types 偵測敏感資料、依 mode 處理後回 PDF。

    `replace` 模式：沒有在 `replacements` 裡指定的值一律**自動產生**假值，
    同一個原值在整份文件裡固定對應同一個假值。`valid_checksum=1` 會讓身分證 /
    統編 / 信用卡算出正確的檢查碼（拿去測試系統不會被擋，但算得出來的號碼有
    可能剛好是某個真人的）。
    """
    if mode not in ("mask", "redact", "replace"):
        raise HTTPException(400, "mode 必須是 mask、redact 或 replace")
    replace_map: dict[str, str] = {}
    if mode == "replace" and replacements.strip():
        import json as _json
        try:
            loaded = _json.loads(replacements)
        except Exception:
            raise HTTPException(400, "replacements 必須是 JSON 物件")
        if not isinstance(loaded, dict):
            raise HTTPException(400, "replacements 必須是 JSON 物件")
        replace_map = {str(k): str(v) for k, v in loaded.items()}
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    orig_name = file.filename or "document"
    ext = Path(orig_name).suffix.lower()
    upload_id = uuid.uuid4().hex
    from ...core import upload_owner as _uo
    _uo.record(upload_id, request)
    pdf_path = _src_path(upload_id)
    if ext == ".pdf":
        pdf_path.write_bytes(data)
    elif office_convert.is_office_file(orig_name):
        tmp = settings.temp_dir / f"did_{upload_id}_orig{ext}"
        tmp.write_bytes(data)
        try:
            office_convert.convert_to_pdf(tmp, pdf_path, timeout=120.0)
        except Exception as exc:
            raise HTTPException(500, f"Office 轉 PDF 失敗：{exc}")
        finally:
            try: tmp.unlink(missing_ok=True)
            except Exception: pass
    else:
        raise HTTPException(400, f"不支援的檔案格式：{ext}")
    selected_ids = {t for t in (types or "").split(",") if t.strip()}
    if not selected_ids:
        selected_ids = P.default_ids_for(doc_lang)

    out_path = _out_path(upload_id)
    mode_fill = (0, 0, 0) if mode == "redact" else None
    # 一份文件共用一個 Replacer —— 同一個原值固定對應同一個假值。
    from .fake_values import Replacer as _Replacer
    _rep = _Replacer(valid_checksum=(valid_checksum or "").strip() == "1")

    def _do():
        with fitz.open(str(pdf_path)) as doc:
            # 1. 偵測所有 findings
            by_page: dict[int, list[dict]] = {}
            for pno in range(doc.page_count):
                page = doc[pno]
                findings = _build_findings_for_page(page, selected_ids, [])
                if findings:
                    by_page[pno] = findings
            # 2. 依 mode redact / mask
            for pno, items in by_page.items():
                page = doc[pno]
                rects = []
                for it in items:
                    bb = it.get("bbox") or []
                    if len(bb) != 4:
                        continue
                    rects.append(fitz.Rect(*bb))
                _redact.apply_page_redactions(page, rects, fill=mode_fill)
                if mode in ("mask", "replace"):
                    for it in items:
                        bb = it.get("bbox") or []
                        if len(bb) != 4:
                            continue
                        if mode == "replace":
                            orig = it.get("value") or ""
                            masked = replace_map.get(orig) or _rep.for_value(
                                it.get("type", ""), orig)
                        else:
                            masked = it.get("masked") or ""
                        if not masked:
                            continue
                        font_size = float(it.get("font_size") or 11.0)
                        if mode == "replace":
                            font_size = _fit_font_size(
                                masked, font_size, float(bb[2]) - float(bb[0]))
                        bx0, _, _, by1 = bb
                        base_y = by1 - font_size * 0.18
                        has_cjk = any("一" <= c <= "鿿" for c in masked)
                        font = "china-t" if has_cjk else "helv"
                        try:
                            page.insert_text(fitz.Point(bx0, base_y), masked,
                                             fontname=font, fontsize=font_size,
                                             color=(0, 0, 0))
                        except Exception:
                            pass
            doc.save(str(out_path), garbage=3, deflate=True)
            return sum(len(v) for v in by_page.values())
    processed = await _asyncio.to_thread(_do)
    stem = Path(orig_name).stem
    headers = {"X-Deident-Count": str(processed)}
    return FileResponse(str(out_path), media_type="application/pdf",
                        filename=f"{stem}_deidentified.pdf", headers=headers)

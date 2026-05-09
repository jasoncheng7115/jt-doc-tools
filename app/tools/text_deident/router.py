"""Endpoints for 文字去識別化."""
from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from ...config import settings
from ...core.http_utils import content_disposition
from ..doc_deident import patterns as P


router = APIRouter()


# ---------- text extraction (paste OR file upload) -------------------------

def _extract_text_from_file(filename: str, data: bytes) -> str:
    """Extract plain text from an uploaded file. Supports plain text
    (.txt/.md), DOCX/DOC/ODT/ODS/ODP/RTF (via soffice), and PDF (via PyMuPDF)."""
    name = (filename or "").lower()
    if name.endswith((".txt", ".md")):
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
                pages = [page.get_text("text") or "" for page in doc]
                return "\n\n".join(pages)
        except Exception as e:
            raise HTTPException(400, f"PDF parse failed: {e}")
    if name.endswith((".docx", ".doc", ".odt", ".ods", ".odp", ".rtf")):
        from ...core import office_convert
        suffix = "." + name.rsplit(".", 1)[-1]
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tf:
            tf.write(data)
            src_path = Path(tf.name)
        try:
            return office_convert.convert_to_text(src_path)
        except Exception as e:
            raise HTTPException(400, f"office 檔解析失敗：{e}")
        finally:
            try:
                src_path.unlink()
            except Exception:
                pass
    raise HTTPException(400, f"unsupported file type: {filename}")


# ---------- detection (re-uses doc-deident patterns) -----------------------

def _parse_custom_regexes(custom_text: str) -> list[tuple[str, re.Pattern]]:
    """Each line: ``label | regex``. Empty / malformed lines silently skipped."""
    out: list[tuple[str, re.Pattern]] = []
    if not custom_text:
        return out
    for raw in custom_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in line:
            continue
        label, _, rx = line.partition("|")
        label = label.strip()
        rx = rx.strip()
        if not label or not rx:
            continue
        try:
            out.append((label, re.compile(rx)))
        except re.error:
            continue
    return out


def _detect_findings(text: str, selected_ids: set[str],
                     custom_regexes: list[tuple[str, re.Pattern]]
                     ) -> list[dict]:
    """Walk full text; emit one finding per regex hit. Each finding has a
    char-level [start, end) span so the frontend can paint highlights without
    recomputing positions."""
    out: list[dict] = []
    fid = 0
    # Built-in patterns
    for pat in P.CATALOG:
        if pat.id not in selected_ids:
            continue
        for m in pat.regex.finditer(text):
            try:
                val = m.group(pat.value_group) if pat.value_group else m.group(0)
                if val is None:
                    continue
                if not pat.validate(val):
                    continue
                # Determine char range: full-match span (so highlighting
                # naturally covers any prefix label that's part of the pattern).
                if pat.value_group:
                    try:
                        start, end = m.start(pat.value_group), m.end(pat.value_group)
                    except Exception:
                        start, end = m.start(), m.end()
                else:
                    start, end = m.start(), m.end()
                out.append({
                    "id":         f"f{fid}",
                    "type":       pat.id,
                    "type_label": pat.label,
                    "group":      pat.group or "其他",
                    "value":      val,
                    "masked":     pat.mask(val),
                    "start":      start,
                    "end":        end,
                })
                fid += 1
            except Exception:
                continue
    # Custom user regexes (no checksum, default mask = same length of *)
    for label, rx in custom_regexes:
        try:
            for m in rx.finditer(text):
                val = m.group(0)
                out.append({
                    "id":         f"f{fid}",
                    "type":       f"custom:{label}",
                    "type_label": label,
                    "group":      "自訂",
                    "value":      val,
                    "masked":     "*" * max(1, len(val)),
                    "start":      m.start(),
                    "end":        m.end(),
                })
                fid += 1
        except Exception:
            continue
    # De-dupe: drop any finding whose [start, end) is fully contained inside
    # another (longer) finding of the same type. e.g. RE_ADDR matches
    # "(地址)" but RE_ROC_DATE inside also matches "123";
    # keep the longer one.
    out.sort(key=lambda f: (f["start"], -(f["end"] - f["start"])))
    kept: list[dict] = []
    for f in out:
        contained = False
        for prev in kept:
            if (prev["type"] == f["type"]
                    and prev["start"] <= f["start"]
                    and prev["end"] >= f["end"]):
                contained = True
                break
        if not contained:
            kept.append(f)
    # Re-id sequentially after dedup so the UI sees stable indices
    for i, f in enumerate(kept):
        f["id"] = f"f{i}"
    return kept


# ---------- fake substitution helpers --------------------------------------

# Tiny lookup tables — enough to feel like realistic Taiwanese data without
# pulling in `Faker` (~1 MB). All names are entirely fictional / generic.
_FAKE_SURNAMES = [
    "陳", "林", "黃", "張", "李", "王", "吳", "劉", "蔡", "楊",
    "許", "鄭", "謝", "郭", "洪", "邱", "曾", "廖", "賴", "周",
]
_FAKE_GIVEN = [
    "明翰", "宗翰", "建宏", "俊宏", "雅婷", "怡君", "美玲", "淑芬",
    "佳樺", "孟蓁", "志豪", "家瑋", "嘉文", "慧君", "雅琪", "詩涵",
]
_FAKE_COMPANIES = [
    "○○股份有限公司", "群益顧問股份有限公司", "○○股份有限公司",
    "○○股份有限公司", "○○有限公司",
]


def _fake_name(_v: str) -> str:
    import random
    return random.choice(_FAKE_SURNAMES) + random.choice(_FAKE_GIVEN)


def _fake_phone(_v: str) -> str:
    import random
    # 09XX-XXX-XXX format
    second = random.randint(10, 89)
    return f"09{second:02d}-{random.randint(0,999):03d}-{random.randint(0,999):03d}"


def _fake_landline(_v: str) -> str:
    import random
    return f"({random.choice(['02','03','04','05','06','07','08'])})" \
           f"{random.randint(2000,8999)}-{random.randint(0,9999):04d}"


def _fake_email(_v: str) -> str:
    import random, string
    user = ''.join(random.choices(string.ascii_lowercase, k=6))
    return f"{user}@example.com"


def _fake_tw_id(_v: str) -> str:
    """Generate a checksum-valid Taiwan ID number."""
    import random
    # Digits used by Taiwan ID checksum; first letter maps to 2-char num.
    letter_map = {
        'A':10,'B':11,'C':12,'D':13,'E':14,'F':15,'G':16,'H':17,'I':34,
        'J':18,'K':19,'L':20,'M':21,'N':22,'O':35,'P':23,'Q':24,'R':25,
        'S':26,'T':27,'U':28,'V':29,'W':32,'X':30,'Y':31,'Z':33,
    }
    while True:
        first = random.choice(list(letter_map.keys()))
        gender = random.choice(['1', '2'])
        body = [str(random.randint(0, 9)) for _ in range(7)]
        n = letter_map[first]
        digits = [n // 10, n % 10, int(gender)] + [int(d) for d in body]
        weights = [1, 9, 8, 7, 6, 5, 4, 3, 2, 1]
        total = sum(d * w for d, w in zip(digits, weights))
        check = (10 - (total % 10)) % 10
        candidate = first + gender + ''.join(body) + str(check)
        return candidate


def _fake_twbiz(_v: str) -> str:
    """Generate a checksum-valid 統一編號 (8 digits)."""
    import random
    # Brute force a valid one — cheap (avg ~10 tries).
    weights = [1, 2, 1, 2, 1, 2, 4, 1]
    for _ in range(200):
        digits = [random.randint(0, 9) for _ in range(8)]
        total = 0
        for d, w in zip(digits, weights):
            prod = d * w
            total += prod // 10 + prod % 10
        if total % 10 == 0 or (digits[6] == 7 and (total + 1) % 10 == 0):
            return ''.join(str(d) for d in digits)
    return '12345678'  # extremely unlikely to reach


def _fake_cc(_v: str) -> str:
    """Generate a Luhn-valid 16-digit credit-card-looking number with 4123 prefix."""
    import random
    digits = [4, 1, 2, 3] + [random.randint(0, 9) for _ in range(11)]
    # compute Luhn check digit
    checksum = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    check = (10 - (checksum % 10)) % 10
    digits.append(check)
    s = ''.join(str(d) for d in digits)
    return f"{s[:4]}-{s[4:8]}-{s[8:12]}-{s[12:16]}"


def _fake_addr(_v: str) -> str:
    import random
    cities = ["台北市", "新北市", "桃園市", "台中市", "高雄市"]
    districts = ["○○區", "中正區", "○○區", "中山區", "前鎮區"]
    roads = ["忠孝東路", "中山北路", "民生東路", "和平西路", "復興南路"]
    return (f"{random.choice(cities)}{random.choice(districts)}"
            f"{random.choice(roads)}{random.randint(1,8)}段"
            f"{random.randint(1,300)}號")


def _fake_ip(_v: str) -> str:
    import random
    return f"192.0.2.{random.randint(1,254)}"  # RFC 5737 doc range


def _fake_passport(_v: str) -> str:
    import random
    return ''.join(str(random.randint(0, 9)) for _ in range(9))


def _fake_default(v: str) -> str:
    """Fallback: keep length, replace alphanumerics with random chars
    of the same class. Preserves shape (email-ish, code-ish, ...)."""
    import random, string
    out = []
    for ch in v:
        if ch.isdigit():
            out.append(str(random.randint(0, 9)))
        elif ch.isupper():
            out.append(random.choice(string.ascii_uppercase))
        elif ch.islower():
            out.append(random.choice(string.ascii_lowercase))
        else:
            out.append(ch)
    return ''.join(out)


_FAKE_DISPATCH: dict[str, callable] = {
    "tw_id":            _fake_tw_id,
    "tw_arc":           _fake_tw_id,
    "tw_biz":           _fake_twbiz,
    "passport":         _fake_passport,
    "driver_license":   _fake_passport,
    "mobile":           _fake_phone,
    "landline":         _fake_landline,
    "email":            _fake_email,
    "cc":               _fake_cc,
    "addr":             _fake_addr,
    "ip":               _fake_ip,
    "name":             _fake_name,
    "company":          lambda _v: _FAKE_COMPANIES[hash(_v) % len(_FAKE_COMPANIES)],
}


def _fake_for(pat_id: str, value: str) -> str:
    fn = _FAKE_DISPATCH.get(pat_id)
    if fn is None:
        return _fake_default(value)
    try:
        return fn(value)
    except Exception:
        return _fake_default(value)


# ---------- text rewrite ----------------------------------------------------

def _apply_to_text(text: str, selections: list[dict], mode: str) -> str:
    """Walk selections in REVERSE start-position order so that earlier slice
    indices remain valid as we splice."""
    if mode not in ("redact", "mask", "fake"):
        raise HTTPException(400, "mode 必須是 redact / mask / fake")
    spans = sorted(
        ((int(s["start"]), int(s["end"]), s.get("type", ""), s.get("masked", ""), s.get("value", ""))
         for s in selections if s.get("start") is not None and s.get("end") is not None),
        key=lambda t: t[0], reverse=True,
    )
    out = text
    for start, end, type_id, masked, value in spans:
        if not (0 <= start < end <= len(out)):
            continue
        original = out[start:end]
        if mode == "redact":
            replacement = "█" * max(1, end - start)
        elif mode == "mask":
            replacement = masked or ("*" * max(1, end - start))
        else:  # fake
            replacement = _fake_for(type_id, value or original)
        out = out[:start] + replacement + out[end:]
    return out


# ---------- routes ----------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    templates = request.app.state.templates
    cats = []
    for grp_label, _grp_id in P.CATEGORY_ORDER if hasattr(P, "CATEGORY_ORDER") else []:
        cats.append(grp_label)
    # Fallback: derive groups from CATALOG
    if not cats:
        seen = []
        for pat in P.CATALOG:
            g = pat.group or "其他"
            if g not in seen:
                seen.append(g)
        cats = seen
    grouped: dict[str, list[dict]] = {g: [] for g in cats}
    for pat in P.CATALOG:
        g = pat.group or "其他"
        grouped.setdefault(g, []).append({
            "id": pat.id, "label": pat.label, "icon": pat.icon,
            "default_on": pat.default_on,
        })
    from ...core.office_convert import detect_engine
    from ...core.llm_settings import llm_settings
    return templates.TemplateResponse(
        "text_deident.html",
        {
            "request": request,
            "grouped": grouped,
            "office_engine": detect_engine(),
            "llm_enabled": llm_settings.is_enabled(),
            "llm_model": llm_settings.get_model_for("text-deident") if llm_settings.is_enabled() else "",
        },
    )


def _llm_extra_findings(full_text: str, already_known: list[str]) -> list[dict]:
    """Ask LLM to find sensitive entities the regex missed.
    Returns a list of {text, type} dicts; caller resolves text → char span."""
    from ...core.llm_settings import llm_settings as _llms
    client = _llms.make_client()
    if client is None:
        return []
    model = _llms.get_model_for("text-deident")
    max_chars = 8000
    if len(full_text) > max_chars:
        full_text = full_text[:max_chars] + "\n\n…（後續省略）"
    known_str = "、".join(already_known[:30]) or "（無）"
    prompt = (
        "你是文字去識別化助手。請從下面文字中找出『可能屬於敏感個人 / 業務資料但容易被 regex 漏掉』"
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
        f"文字內容：\n{full_text}"
    )
    try:
        resp = client.text_query(prompt=prompt, model=model,
                                  temperature=0.0, think=False)
    except Exception as e:
        import logging as _lg
        _lg.getLogger(__name__).warning("LLM call failed: %s", e)
        return []
    raw = (resp or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE)
    try:
        import json as _json
        arr = _json.loads(raw)
        if not isinstance(arr, list):
            return []
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


def _attach_llm_spans(text: str, llm_items: list[dict],
                      existing: list[dict]) -> list[dict]:
    """Resolve each LLM-suggested phrase to char-level spans in `text` and
    append as new findings. Skips phrases that overlap with already-known
    findings. Multiple occurrences of same phrase all get added."""
    if not llm_items:
        return existing
    # Build set of (start, end) of existing spans for overlap check
    existing_spans = [(f["start"], f["end"]) for f in existing]
    fid = len(existing)
    additions: list[dict] = []
    for item in llm_items:
        phrase = item["text"]
        type_label = "[LLM] " + item.get("type", "其他")
        pos = 0
        while True:
            idx = text.find(phrase, pos)
            if idx < 0:
                break
            end = idx + len(phrase)
            # Skip if overlaps with an existing finding
            overlap = any(not (end <= s or idx >= e) for s, e in existing_spans)
            if not overlap:
                additions.append({
                    "id": f"l{fid}",
                    "type": "llm",
                    "type_label": type_label,
                    "group": "[LLM] 補偵測",
                    "value": phrase,
                    "masked": "*" * max(1, len(phrase)),
                    "start": idx,
                    "end": end,
                })
                existing_spans.append((idx, end))
                fid += 1
            pos = end
    if not additions:
        return existing
    merged = sorted(existing + additions, key=lambda f: f["start"])
    for i, f in enumerate(merged):
        f["id"] = f"f{i}"
    return merged


@router.post("/extract-text")
async def extract_text(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty file")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(400, "file too large (limit 50 MB)")
    text = _extract_text_from_file(file.filename or "", data)
    return JSONResponse({
        "filename": file.filename,
        "text": text,
        "char_count": len(text),
    })


@router.post("/detect")
async def detect(request: Request):
    body = await request.json()
    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "text is empty")
    if len(text) > 1_000_000:
        raise HTTPException(400, "text too large (limit 1,000,000 chars)")
    selected_ids = set(body.get("types") or [p.id for p in P.CATALOG if p.default_on])
    custom_text = str(body.get("custom_regex") or "")
    custom_regexes = _parse_custom_regexes(custom_text)
    findings = _detect_findings(text, selected_ids, custom_regexes)
    llm_augment = bool(body.get("llm_augment"))
    llm_warning = ""
    if llm_augment:
        from ...core.llm_settings import llm_settings as _llms
        if not _llms.is_enabled():
            llm_warning = "已勾選 LLM 補偵測但 LLM 服務尚未啟用，本次跳過"
        else:
            already_known = [f["value"] for f in findings]
            try:
                extras = _llm_extra_findings(text, already_known)
                if extras:
                    findings = _attach_llm_spans(text, extras, findings)
            except Exception:
                # v1.5.4 CodeQL py/stack-trace-exposure: 不漏 exception 給 user
                import logging as _lg
                _lg.getLogger(__name__).exception("LLM augment failed")
                llm_warning = "LLM 補偵測失敗,僅顯示 regex 結果"
    # Aggregate counts for UI summary
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["type_label"]] = counts.get(f["type_label"], 0) + 1
    return {
        "ok": True,
        "findings": findings,
        "total": len(findings),
        "counts": [{"label": k, "count": v} for k, v in
                   sorted(counts.items(), key=lambda x: -x[1])],
        "char_count": len(text),
        "llm_warning": llm_warning,
    }


@router.post("/process")
async def process(request: Request):
    body = await request.json()
    text = str(body.get("text") or "")
    selections = body.get("selections") or []
    mode = (body.get("mode") or "mask").strip()
    if not text:
        raise HTTPException(400, "text is empty")
    if not isinstance(selections, list):
        raise HTTPException(400, "selections 格式錯誤")
    new_text = _apply_to_text(text, selections, mode)
    return {
        "ok": True,
        "text": new_text,
        "mode": mode,
        "processed": len(selections),
    }


@router.post("/download")
async def download(
    text: str = Form(...),
    filename: str = Form("text_deident.txt"),
):
    """Return the processed text as a downloadable .txt."""
    safe = re.sub(r"[^\w.-]", "_", filename) or "text_deident.txt"
    if not safe.lower().endswith(".txt"):
        safe += ".txt"
    return Response(
        text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": content_disposition(safe)},
    )


# Public API endpoint — same shape as /detect + /process combined
@router.post("/api/text-deident")
async def api_text_deident(request: Request):
    body = await request.json()
    text = str(body.get("text") or "")
    if not text.strip():
        raise HTTPException(400, "text is empty")
    if len(text) > 1_000_000:
        raise HTTPException(400, "text too large (limit 1,000,000 chars)")
    mode = (body.get("mode") or "mask").strip()
    selected_ids = set(body.get("types") or [p.id for p in P.CATALOG if p.default_on])
    custom_text = str(body.get("custom_regex") or "")
    custom_regexes = _parse_custom_regexes(custom_text)
    findings = _detect_findings(text, selected_ids, custom_regexes)
    new_text = _apply_to_text(text, findings, mode)
    return {
        "ok": True,
        "mode": mode,
        "findings": findings,
        "text": new_text,
        "char_count_before": len(text),
        "char_count_after": len(new_text),
    }

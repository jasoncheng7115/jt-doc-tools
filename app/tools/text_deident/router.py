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
    return templates.TemplateResponse(
        "text_deident.html",
        {
            "request": request,
            "grouped": grouped,
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

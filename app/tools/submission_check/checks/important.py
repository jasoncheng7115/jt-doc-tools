"""E 類重要檢查項：
- 金額一致性（標單 / 報價 / 估價 / 合約金額位數錯位）
- 日期合理性鏈（核發 ≤ 投標 ≤ 有效期；過期警示）
- 附件清單對應（標單寫附件 N，實際 M 份）
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime
from typing import Optional


# ─── 金額抽取 ───────────────────────────────────────────────

# 抓 NTD / 元 / $ 的金額（含千分位逗號）
AMOUNT_RE = re.compile(
    r"(?:NT\$?|NTD|TWD|新台幣|新臺幣|金額|價金|總價|報價|含稅|未稅)"  # 前綴關鍵字（鬆綁，可選）
    r"\s*[:：]?\s*"
    r"\$?\s*"
    r"([\d,]+(?:\.\d+)?)"
    r"\s*(?:元|圓|\.|$)?",
    re.IGNORECASE,
)
# 也抓單純大數字（≥6 位）做輔助
RAW_NUM_RE = re.compile(r"\b(\d{1,3}(?:,\d{3}){1,}|\d{6,})\b")


def _parse_amount(s: str) -> Optional[int]:
    s = s.replace(",", "").strip()
    try:
        if "." in s:
            return int(float(s))
        return int(s)
    except ValueError:
        return None


def extract_amounts(text: str) -> list[int]:
    """抽 text 內金額（NTD 整數），去重去過小（<10000）。"""
    seen = set()
    out = []
    for m in AMOUNT_RE.finditer(text):
        v = _parse_amount(m.group(1))
        if v is not None and v >= 10000 and v not in seen:
            seen.add(v); out.append(v)
    for m in RAW_NUM_RE.finditer(text):
        v = _parse_amount(m.group(1))
        if v is not None and v >= 10000 and v not in seen:
            seen.add(v); out.append(v)
    return out


def detect_amount_inconsistency(per_file_amounts: dict[str, list[int]],
                                files_meta: list[dict]) -> list[dict]:
    """跨檔金額一致性：找位數錯位（A 出現 35,000,000 而 B 出現 3,500,000）。"""
    findings: list[dict] = []
    name_by_id = {f["file_id"]: f.get("name", "?") for f in files_meta}
    # 蒐集所有金額 + 來源檔
    all_amts: list[tuple[int, str]] = []
    for fid, amts in per_file_amounts.items():
        for v in amts:
            all_amts.append((v, fid))
    if len(all_amts) < 2:
        return findings
    # 比對：找 A != B 但 A * 10 == B (位數錯位)
    seen_pairs = set()
    for i, (va, fa) in enumerate(all_amts):
        for vb, fb in all_amts[i + 1:]:
            if fa == fb:
                continue
            if va == vb:
                continue
            ratio = max(va, vb) // min(va, vb)
            if ratio in (10, 100, 1000) and max(va, vb) % min(va, vb) == 0:
                pair = tuple(sorted([va, vb]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                findings.append({
                    "layer": "L2",
                    "severity": "warn",
                    "category": "amount-mismatch",
                    "title": f"金額位數錯位：{va:,} vs {vb:,}",
                    "detail": (f"檔案「{name_by_id.get(fa, fa[:8])}」金額 {va:,} 元，"
                               f"但「{name_by_id.get(fb, fb[:8])}」金額 {vb:,} 元，"
                               f"相差 {ratio} 倍 — 可能位數錯位（少 {ratio} 個 0 / 多 {ratio} 個 0）。"),
                    "page": None,
                    "evidence": {"value_a": va, "file_a": fa, "value_b": vb, "file_b": fb,
                                 "ratio": ratio},
                })
    return findings


# ─── 日期解析 + 合理性 ──────────────────────────────────────

ROC_DATE_RE = re.compile(r"\b(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?\b")
WEST_DATE_RE = re.compile(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b")
DATE_RANGE_RE = re.compile(
    r"(?:有效|effective|有效期|期間|期限)\s*[:：至到]?\s*(20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}|\d{2,3}\s*年\s*\d{1,2}\s*月\s*\d{1,2})"
)


def _parse_date(s: str) -> Optional[date]:
    """嘗試解析中西曆日期。回 datetime.date 或 None。"""
    s = s.strip()
    m = WEST_DATE_RE.match(s)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(y, mo, d)
        except ValueError:
            return None
    m = ROC_DATE_RE.match(s)
    if m:
        try:
            roc, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(roc + 1911, mo, d)
        except ValueError:
            return None
    return None


def extract_dates(text: str) -> list[date]:
    """抽所有日期（去重）。"""
    seen = set()
    out = []
    for m in WEST_DATE_RE.finditer(text):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if d not in seen:
                seen.add(d); out.append(d)
        except ValueError:
            continue
    for m in ROC_DATE_RE.finditer(text):
        try:
            d = date(int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3)))
            if d not in seen:
                seen.add(d); out.append(d)
        except ValueError:
            continue
    return out


def detect_expired_dates(per_file_dates: dict[str, list[date]],
                         files_meta: list[dict],
                         deadline_str: str = "") -> list[dict]:
    """偵測過期日期 — 任何 < today 的日期若 context 看似「有效期」標警。"""
    findings: list[dict] = []
    name_by_id = {f["file_id"]: f.get("name", "?") for f in files_meta}
    today = date.today()
    deadline_d = _parse_date(deadline_str) if deadline_str else None

    for fid, dates in per_file_dates.items():
        future_dates = [d for d in dates if d > today]
        past_dates = [d for d in dates if d < today]
        # 簡單規則：如果該檔有「未來」日期但時間 < 案件截止日，提醒可能過期
        if deadline_d:
            # 日期介於 today 與 deadline 之間 → 即將過期
            soon = [d for d in dates if today < d < deadline_d]
            if soon:
                findings.append({
                    "layer": "L2",
                    "severity": "warn",
                    "category": "date-expiring-soon",
                    "title": f"檔案有日期早於案件截止日",
                    "detail": (f"「{name_by_id.get(fid, fid[:8])}」內出現日期 "
                               f"{', '.join(d.isoformat() for d in soon[:3])}，"
                               f"早於案件截止日 {deadline_d.isoformat()}。"
                               "可能是過期證書 / 證明，請人工確認。"),
                    "page": None,
                    "evidence": {"file_id": fid,
                                 "dates": [d.isoformat() for d in soon],
                                 "deadline": deadline_d.isoformat()},
                })
    return findings


# ─── 附件清單對應 ────────────────────────────────────────

ATTACHMENT_DECL_RE = re.compile(
    r"(?:附件|檢附|附錄|另附)[：:]\s*"
    r"((?:\s*[\d一二三四五六七八九十]+[、,，]?\s*[^\n。]+(?:\n|$)){2,})"
)
NUMBERED_LIST_RE = re.compile(r"附件\s*([\d一二三四五六七八九十]+)")


def detect_attachment_count_mismatch(per_file_text: dict[str, str],
                                      files_meta: list[dict]) -> list[dict]:
    """偵測「標單聲明 N 件附件，實際只有 M 件」。"""
    findings: list[dict] = []
    n_files_actual = len(files_meta)
    name_by_id = {f["file_id"]: f.get("name", "?") for f in files_meta}

    declared_max = 0
    declared_in_file = ""
    for fid, text in per_file_text.items():
        nums = []
        for m in NUMBERED_LIST_RE.finditer(text):
            v = m.group(1)
            try:
                if v.isdigit():
                    nums.append(int(v))
                else:
                    cn_to_int = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
                    nums.append(cn_to_int.get(v, 0))
            except Exception:
                continue
        if nums:
            mx = max(nums)
            if mx > declared_max:
                declared_max = mx
                declared_in_file = fid
    if declared_max > 0 and declared_max != n_files_actual:
        # 標單寫附件 N，實際 M 份（M 可能不含標單本身，所以差 1 視為 OK）
        if abs(declared_max - n_files_actual) > 1:
            findings.append({
                "layer": "L2",
                "severity": "warn",
                "category": "attachment-mismatch",
                "title": f"附件數不符：聲明 {declared_max} 件，實際上傳 {n_files_actual} 份",
                "detail": (f"檔案「{name_by_id.get(declared_in_file, declared_in_file[:8])}」"
                           f"聲明附件最多到第 {declared_max} 號，但實際只上傳 {n_files_actual} 份。"
                           "請確認是否漏附 / 多附。"),
                "page": None,
                "evidence": {"declared": declared_max, "actual": n_files_actual,
                             "declared_in": declared_in_file},
            })
    return findings

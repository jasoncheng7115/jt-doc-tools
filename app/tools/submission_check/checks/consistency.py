"""跨檔身分一致性 — 核心檢查。

抽公司名 / 統編 / 案號 / 人名 / 案件日期等實體，跨檔比對：
1. 對照 ground truth：標出所有「不符合」的出現
2. 沒 ground truth：頻率分析，標 outlier (可能漏改範本)

L2 (OCR) 的文字會餵進 _extract_entities_from_text() 一起跑同一套抽取邏輯；
L3 (LLM) 是進階變體合併（不在本層範圍）。
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Optional

# 公司 / 機構命名常見後綴
ORG_SUFFIX_RE = re.compile(
    r"([A-Za-z0-9一-鿿·]+?(?:股份有限公司|有限公司|企業社|商號|行號|工作室|事務所|診所|醫院|診療所))"
)
# 政府機關（部 / 處 / 局 / 署 / 委員會 / 廳 / 院 / 公所 / 縣政府 / 市政府）
GOV_SUFFIX_RE = re.compile(
    r"([一-鿿]{2,8}?(?:部|處|局|署|委員會|廳|院|公所|縣政府|市政府|衛生所))"
)
# 學校
SCHOOL_RE = re.compile(
    r"([一-鿿]{2,15}?(?:大學|學院|高中|高級中學|國中|國民中學|國小|國民小學|附小|附中))"
)
# 法人
LEGAL_PERSON_RE = re.compile(
    r"((?:財團法人|社團法人)[一-鿿]{2,15})"
)
# 統編
TAX_ID_RE = re.compile(r"\b(\d{8})\b")
# 案號（粗略）
CASE_NUM_RE = re.compile(r"\b([A-Z]{2,5}-?\d{2,4}-?\d{2,8})\b")
# 日期
DATE_RE = re.compile(
    r"\b(20\d{2}[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}|"
    r"\d{1,3}[年]\s*\d{1,2}[月]\s*\d{1,2}[日])"
)


def _normalize(s: str) -> str:
    """正規化 — 空白 / 全半形差異不算不同。"""
    return re.sub(r"\s+", "", s).strip()


def extract_entities_from_text(text: str) -> dict:
    """從一份文字抽出所有實體候選。
    回 {kind: [{value, normalized, count}, ...]}
    """
    if not text:
        return {}
    out: dict[str, Counter] = {
        "company": Counter(),
        "government": Counter(),
        "school": Counter(),
        "legal_person": Counter(),
        "tax_id": Counter(),
        "case_number": Counter(),
        "date": Counter(),
    }

    for m in ORG_SUFFIX_RE.finditer(text):
        v = _normalize(m.group(1))
        if 4 <= len(v) <= 40:
            out["company"][v] += 1
    for m in GOV_SUFFIX_RE.finditer(text):
        v = _normalize(m.group(1))
        if 3 <= len(v) <= 20:
            out["government"][v] += 1
    for m in SCHOOL_RE.finditer(text):
        v = _normalize(m.group(1))
        if 4 <= len(v) <= 30:
            out["school"][v] += 1
    for m in LEGAL_PERSON_RE.finditer(text):
        v = _normalize(m.group(1))
        if 6 <= len(v) <= 30:
            out["legal_person"][v] += 1
    for m in TAX_ID_RE.finditer(text):
        out["tax_id"][m.group(1)] += 1
    for m in CASE_NUM_RE.finditer(text):
        v = m.group(1)
        # 排除明顯的統編 / 電話
        if not re.fullmatch(r"\d{8,10}", v):
            out["case_number"][v] += 1
    for m in DATE_RE.finditer(text):
        out["date"][_normalize(m.group(1))] += 1

    return out


def _extract_pdf_text(path: Path) -> str:
    """從 PDF 抽文字層（OCR 留 L2）。"""
    try:
        import fitz
        doc = fitz.open(str(path))
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    except Exception:
        return ""


def _extract_docx_text(path: Path) -> str:
    """從 .docx 抽文字。"""
    try:
        from zipfile import ZipFile
        import xml.etree.ElementTree as ET
        with ZipFile(path) as z:
            if "word/document.xml" not in z.namelist():
                return ""
            xml_data = z.read("word/document.xml").decode("utf-8", errors="replace")
            root = ET.fromstring(xml_data)
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            texts = []
            for t in root.iter(ns + "t"):
                if t.text:
                    texts.append(t.text)
            return "\n".join(texts)
    except Exception:
        return ""


def extract_entities_from_file(path: Path) -> dict:
    """從單一檔案抽實體。"""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _extract_pdf_text(path)
    elif suffix in (".docx", ".doc"):
        text = _extract_docx_text(path)
    else:
        # 圖片 -> L2 OCR 才能抽（這裡先回空）
        return {}
    return extract_entities_from_text(text)


def aggregate_across_files(per_file_entities: dict[str, dict]) -> dict:
    """跨檔聚合 — 每個 kind 內每個 value 出現於哪些檔 / 共幾次。
    Input: {file_id: {kind: Counter}}
    Output: {kind: [{value, total_count, files: [file_id, ...]}, ...]}
    """
    agg: dict[str, dict[str, dict]] = {}
    for file_id, ents in per_file_entities.items():
        for kind, counter in ents.items():
            kk = agg.setdefault(kind, {})
            for value, n in counter.items():
                e = kk.setdefault(value, {"value": value, "total_count": 0, "files": set()})
                e["total_count"] += n
                e["files"].add(file_id)
    # convert sets → sorted list
    out: dict[str, list[dict]] = {}
    for kind, kk in agg.items():
        items = sorted(kk.values(), key=lambda e: -e["total_count"])
        for it in items:
            it["files"] = sorted(it["files"])
        out[kind] = items
    return out


def detect_consistency_findings(
    aggregated: dict,
    ground_truth: Optional[dict] = None,
    files_meta: Optional[list[dict]] = None,
    self_entities: Optional[list[dict]] = None,
) -> list[dict]:
    """產出跨檔一致性 findings。

    模式 1（有 ground truth）：抓所有「跟 ground truth 不符」的 value 出現
    模式 2（沒 ground truth）：頻率分析 — 主角候選頻率高 + outlier (< 推薦 20%)

    若 self_entities 有提供，會自動把屬於 user 自家的實體 / 統編排除（不誤標）。
    """
    findings: list[dict] = []
    files_meta = files_meta or []
    self_entities = self_entities or []
    file_id_to_name = {f["file_id"]: f.get("name", "?") for f in files_meta}

    def _names(file_ids: list[str]) -> str:
        return ", ".join(file_id_to_name.get(fid, fid[:8]) for fid in file_ids[:5])

    # 蒐集 self entities 的名稱（含別名）+ 統編 set 給後續比對
    self_names_norm: list[str] = []
    self_tax_ids: set = set()
    for se in self_entities:
        names = [se.get("name", "")] + (se.get("aliases") or [])
        for n in names:
            n_norm = _normalize(n)
            if n_norm:
                self_names_norm.append(n_norm)
        tid = _normalize(se.get("tax_id") or "")
        if tid and tid.isdigit():
            self_tax_ids.add(tid)

    def _is_self_entity(v: str) -> bool:
        if not v:
            return False
        v_norm = _normalize(v)
        for sn in self_names_norm:
            if sn and (sn in v_norm or v_norm in sn):
                return True
        return False

    gt = ground_truth or {}
    gt_main = (gt.get("main_entity") or {}).get("name") or ""
    gt_main_id = (gt.get("main_entity") or {}).get("identifier") or ""
    gt_counterparty = (gt.get("counterparty") or {}).get("name") or ""
    gt_main_norm = _normalize(gt_main)
    gt_id_norm = _normalize(gt_main_id)
    gt_counterparty_norm = _normalize(gt_counterparty)
    gt_case_num = _normalize(gt.get("case_number") or "")

    def _is_counterparty(v: str) -> bool:
        if not gt_counterparty_norm:
            return False
        return gt_counterparty_norm in v or v in gt_counterparty_norm

    # ─── 主角名比對 ───
    if gt_main_norm:
        # 政府機關常常是「對方」（招標方）— 預設不在 mismatch 範圍
        # 只比 company / legal_person，school 視情境可能是主角
        all_org_values: list[dict] = []
        for kind in ("company", "legal_person", "school"):
            all_org_values.extend(aggregated.get(kind, []))
        # 政府機關只在「user 沒明確指定對方時」才檢查
        if not gt_counterparty_norm:
            all_org_values.extend(aggregated.get("government", []))
        for entry in all_org_values:
            v = entry["value"]
            if gt_main_norm in v or v in gt_main_norm:
                continue
            if _is_counterparty(v):
                continue
            if _is_self_entity(v):
                continue  # 自家公司 — 已登錄為我方資料，不算 mismatch
            findings.append({
                "layer": "L2",
                "severity": "warn",
                "category": "identity-mismatch",
                "title": f"出現非預期主體：「{v}」",
                "detail": (f"預期主角：{gt_main}"
                           + (f"；對方：{gt_counterparty}" if gt_counterparty else "")
                           + f"。但「{v}」出現 {entry['total_count']} 次於"
                           f" {len(entry['files'])} 個檔案（{_names(entry['files'])}），"
                           "可能是從別案範本沿用未改。"),
                "page": None,
                "evidence": {"expected_main": gt_main,
                             "expected_counterparty": gt_counterparty,
                             "actual": v, "files": entry["files"],
                             "count": entry["total_count"]},
            })
    else:
        # 沒 ground truth — 自動推估 + outlier 警示
        # 只取「公司」「法人」當推估主角候選（政府 / 學校通常是對方）
        org_candidates: list[dict] = []
        for kind in ("company", "legal_person"):
            for e in aggregated.get(kind, []):
                e2 = dict(e); e2["kind"] = kind
                org_candidates.append(e2)
        org_candidates.sort(key=lambda e: -e["total_count"])
        if org_candidates:
            top = org_candidates[0]
            top_files = len(top["files"])
            for entry in org_candidates[1:]:
                # 標準：outlier 出現的「檔案數」< top 出現檔案數一半 (向下取整)
                # 例：top 在 3 檔出現、outlier 在 1 檔 → 1 < 1.5 → 標
                # 例：top 在 5 檔、outlier 在 2 檔 → 2 < 2.5 → 標
                # 例：top 在 2 檔、outlier 在 1 檔 → 1 < 1 → 不標 (太少 sample)
                # 用「<= top_files // 2」的話 top=2 outlier=1 也會標 → 改用此規則
                if len(entry["files"]) <= top_files // 2 or top_files >= 2 and len(entry["files"]) == 1:
                    findings.append({
                        "layer": "L2",
                        "severity": "warn",
                        "category": "identity-outlier",
                        "title": f"頻率異常的主體：「{entry['value']}」",
                        "detail": (f"本案件 主流主體看起來是「{top['value']}」"
                                   f"（{top['total_count']} 次），"
                                   f"但「{entry['value']}」只出現 {entry['total_count']} 次"
                                   f"於 {_names(entry['files'])}，疑似漏改範本。"),
                        "page": None,
                        "evidence": {"main_candidate": top["value"],
                                     "outlier": entry["value"],
                                     "files": entry["files"],
                                     "count": entry["total_count"]},
                    })

    # ─── 統編比對 ───
    tax_ids = aggregated.get("tax_id", [])
    if gt_id_norm and gt_id_norm.isdigit() and len(gt_id_norm) == 8:
        for entry in tax_ids:
            if entry["value"] != gt_id_norm:
                findings.append({
                    "layer": "L1",
                    "severity": "warn",
                    "category": "tax-id-mismatch",
                    "title": f"非預期統編：「{entry['value']}」",
                    "detail": (f"預期統編：{gt_id_norm}；但 {entry['value']} 出現於"
                               f" {_names(entry['files'])}。"),
                    "page": None,
                    "evidence": {"expected": gt_id_norm, "actual": entry["value"],
                                 "files": entry["files"]},
                })
    elif len(tax_ids) > 1:
        # 沒 ground truth：多統編並存可能是多家 / 漏改
        # 排除已登錄為「我方資料」的統編（user 自家公司統編出現是正常的）
        from .l1_rules import _validate_tax_id
        valid_tids = [t for t in tax_ids if _validate_tax_id(t["value"])]
        non_self_tids = [t for t in valid_tids if t["value"] not in self_tax_ids]
        # 只警告非我方統編；單一非我方統編當資訊提示，多個則警告
        if len(non_self_tids) >= 2:
            findings.append({
                "layer": "L1",
                "severity": "warn",
                "category": "tax-id-multiple",
                "title": f"本案件出現 {len(non_self_tids)} 個非我方有效統編",
                "detail": ("多個非我方統編並存 — 可能是聯合投標 / 多家代理，或漏改舊統編。"
                           " 統編：" + ", ".join(t["value"] for t in non_self_tids)),
                "page": None,
                "evidence": {"non_self_tax_ids": [t["value"] for t in non_self_tids],
                             "files": list({fid for t in non_self_tids for fid in t.get("files", [])})},
            })
        # 單一非我方統編 → 資訊性，不算問題

    # ─── 統編校驗碼錯誤（單獨 finding，不論有無 ground truth） ───
    from .l1_rules import _validate_tax_id
    for entry in tax_ids:
        if not _validate_tax_id(entry["value"]):
            findings.append({
                "layer": "L1",
                "severity": "fail",
                "category": "tax-id-invalid",
                "title": f"統編「{entry['value']}」校驗碼錯誤",
                "detail": (f"出現於 {_names(entry['files'])}（共 {entry['total_count']} 次），"
                           "可能輸入錯誤或位數錯位。"),
                "page": None,
                "evidence": {"tax_id": entry["value"], "files": entry["files"]},
            })

    # ─── 案號比對 ───
    if gt_case_num:
        for entry in aggregated.get("case_number", []):
            if entry["value"] != gt_case_num:
                findings.append({
                    "layer": "L2",
                    "severity": "info",
                    "category": "case-num-other",
                    "title": f"出現其他案號：「{entry['value']}」",
                    "detail": (f"預期案號：{gt_case_num}；其他案號 {entry['value']} 也出現於"
                               f" {_names(entry['files'])}。"),
                    "page": None,
                    "evidence": {"expected": gt_case_num, "actual": entry["value"],
                                 "files": entry["files"]},
                })

    return findings

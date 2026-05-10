"""User override 註解持久化。

Override = user 對某個 finding 標「誤報」/「已確認 OK」+ 原因 + 簽署人。
寫進 case.json 的 overrides[] list，下次重跑時保留並套用：
- 同 category + 同 evidence key 的 finding 自動降級為「已標 OK」+ 標註原 user / 時間
"""
from __future__ import annotations

import time
from typing import Optional

from . import case_manager as _cm


def _make_finding_key(finding: dict) -> str:
    """產生用來比對「同一個 finding」的鍵，跨版本穩定。
    用 category + 主要 evidence 欄位組成。
    """
    cat = finding.get("category", "")
    ev = finding.get("evidence") or {}
    # 不同 category 用不同的識別欄位
    if cat == "tax-id-mismatch":
        return f"{cat}:{ev.get('actual', '')}"
    if cat == "tax-id-invalid":
        return f"{cat}:{ev.get('tax_id', '')}"
    if cat == "identity-mismatch":
        return f"{cat}:{ev.get('actual', '')}"
    if cat == "identity-outlier":
        return f"{cat}:{ev.get('outlier', '')}"
    if cat == "metadata-leak":
        # 含哪幾個欄位
        meta = ev.get("metadata") or {}
        return f"{cat}:{','.join(sorted(meta.keys()))}"
    if cat == "duplicate-hash":
        return f"{cat}:{ev.get('sha256', '')[:16]}"
    if cat == "template-residue":
        return f"{cat}:{ev.get('file_id', '')[:8]}"
    # default: title + page
    return f"{cat}:{finding.get('title', '')[:50]}:{finding.get('page') or ''}"


def add_override(case_id: str, finding_key: str, verdict: str,
                 reason: str = "", by_user: Optional[str] = None) -> dict:
    """
    verdict: "false_positive" (誤報) | "confirmed_ok" (已人工確認 OK) | "wont_fix"
    """
    if verdict not in ("false_positive", "confirmed_ok", "wont_fix"):
        raise ValueError(f"invalid verdict: {verdict!r}")
    case = _cm.load_case(case_id)
    if not case:
        raise ValueError("case not found")
    overrides = case.setdefault("overrides", [])
    # 同 finding_key 已存在 → 更新
    for o in overrides:
        if o.get("finding_key") == finding_key:
            o.update({
                "verdict": verdict,
                "reason": reason,
                "by_user": by_user,
                "at": time.time(),
            })
            _cm.save_case(case)
            return o
    # 新建
    o = {
        "finding_key": finding_key,
        "verdict": verdict,
        "reason": reason,
        "by_user": by_user,
        "at": time.time(),
    }
    overrides.append(o)
    _cm.save_case(case)
    return o


def remove_override(case_id: str, finding_key: str) -> bool:
    case = _cm.load_case(case_id)
    if not case:
        return False
    overrides = case.get("overrides", [])
    n_before = len(overrides)
    case["overrides"] = [o for o in overrides if o.get("finding_key") != finding_key]
    _cm.save_case(case)
    return len(case["overrides"]) < n_before


def apply_overrides_to_findings(case: dict, findings: list[dict]) -> list[dict]:
    """把案件的 overrides 套到 findings list — 標記哪些已被 user 標 OK。
    回新 list（不 mutate input）。每個 finding 加 `_override` 欄位（若有）。
    """
    overrides_by_key = {o["finding_key"]: o for o in (case.get("overrides") or [])}
    out = []
    for f in findings:
        key = _make_finding_key(f)
        f2 = dict(f)
        if key in overrides_by_key:
            f2["_override"] = overrides_by_key[key]
            # 把 severity 降級顯示（保留原 severity 在 _original_severity）
            f2["_original_severity"] = f2.get("severity")
            f2["severity"] = "info"  # 已被 user 確認，降級為 info
        out.append(f2)
    return out


def count_findings_by_severity(findings: list[dict]) -> dict:
    """重算 fail/warn/info 計數（套用 override 後）。"""
    return {
        "fail": sum(1 for f in findings if f.get("severity") == "fail"),
        "warn": sum(1 for f in findings if f.get("severity") == "warn"),
        "info": sum(1 for f in findings if f.get("severity") == "info"),
    }

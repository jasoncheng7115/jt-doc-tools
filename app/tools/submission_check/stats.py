"""跨案件 stats — 給 admin 儀表板用。

純讀取既有 case files，不另存 stats 表（資料量小，每次重算可接受）。
"""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from typing import Optional

from . import case_manager as _cm


def gather_stats(days: int = 30) -> dict:
    """彙整過去 N 天的 case 跑況。"""
    cutoff = time.time() - days * 86400
    cases = _cm.list_cases(admin=True, limit=10000)
    cases = [c for c in cases if c.get("created_at", 0) >= cutoff]

    n_cases = len(cases)
    by_status = Counter()
    score_sum = 0
    score_n = 0
    finding_categories: Counter = Counter()
    finding_severities: Counter = Counter()
    files_total = 0
    layer_used: Counter = Counter()

    for c in cases:
        by_status[c.get("status", "?")] += 1
        files_total += c.get("files_count", 0)
        # 看最新版本的 report
        ver = c.get("current_version")
        if not ver:
            continue
        rep = _cm.load_version_report(c["case_id"], ver)
        if not rep:
            continue
        s = rep.get("summary", {})
        score = s.get("score")
        if isinstance(score, (int, float)):
            score_sum += score
            score_n += 1
        # layer used
        layers = s.get("layers", {})
        for ll, st in layers.items():
            if str(st).startswith("done"):
                layer_used[ll] += 1
        # findings
        for fds in rep.get("findings_per_file", {}).values():
            for fd in fds:
                finding_categories[fd.get("category", "")] += 1
                finding_severities[fd.get("severity", "")] += 1
        for fd in rep.get("cross_findings", []):
            finding_categories[fd.get("category", "")] += 1
            finding_severities[fd.get("severity", "")] += 1

    return {
        "days": days,
        "cases_total": n_cases,
        "files_total": files_total,
        "by_status": dict(by_status),
        "avg_score": round(score_sum / score_n, 1) if score_n else None,
        "layer_used": dict(layer_used),
        "top_categories": finding_categories.most_common(10),
        "severity_breakdown": dict(finding_severities),
    }

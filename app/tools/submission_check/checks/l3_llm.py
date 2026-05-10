"""L3 LLM — 用 LLM 做 fuzzy 變體合併、placeholder 判讀、修改範本痕跡推論。

核心 entry：
- merge_entity_variants() — 把「光寶 / 光寶科技 / LITE-ON / 光寶集團」聚成同一群
- detect_template_residue() — 用 LLM 看「這份文件是否從另一份 case 範本沿用未改」
- (vision 偽造偵測 留 v2)

LLM 沒設定就回空 list / 空 dict — caller 用 try-or-skip 模式跑。
"""
from __future__ import annotations

import json
import re
from typing import Optional


def _get_client_and_model():
    """取既有 LLM client + tool-specific model；沒設好就回 (None, None)。"""
    try:
        from app.core.llm_settings import llm_settings
        if not llm_settings.is_enabled():
            return None, None
        client = llm_settings.make_client()
        model = llm_settings.get_model_for("submission-check")
        return client, model
    except Exception:
        return None, None


def llm_available() -> bool:
    client, model = _get_client_and_model()
    return client is not None and bool(model)


def _safe_json_extract(text: str) -> dict:
    """從 LLM 回應抽 JSON，容錯各種包裝。"""
    if not text:
        return {}
    # markdown code fence
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    # plain JSON
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        # try first { ... } block
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {}


def merge_entity_variants(values: list[str]) -> list[list[str]]:
    """讓 LLM 把命名變體合成 group。

    Input: ["光寶科技", "光寶科技股份有限公司", "LITE-ON", "鴻海", "鴻海精密"]
    Output: [["光寶科技", "光寶科技股份有限公司", "LITE-ON"], ["鴻海", "鴻海精密"]]

    沒 LLM 時回 [[v] for v in values]（每個 value 各成一群）。
    """
    if not values:
        return []
    if len(values) <= 1:
        return [[v] for v in values]
    client, model = _get_client_and_model()
    if not client or not model:
        return [[v] for v in values]

    # 控制 input 大小避免吃光 context
    sample = values[:30]
    prompt = (
        "請判斷以下機構 / 公司命名變體中，哪些是同一個實體（同集團 / 同公司不同稱呼 / 中英對照）。\n"
        "規則：\n"
        "1. 「光寶」「光寶科技」「光寶科技股份有限公司」「LITE-ON」算同一個（同公司簡稱 / 全名 / 英文名）\n"
        "2. 「鴻海」「鴻海精密工業」「Foxconn」算同一個\n"
        "3. 「光寶」與「鴻海」是不同實體，分開\n"
        "4. 「子公司」「集團」也算同一個\n"
        "5. 純政府機關（如「經濟部」「經濟部標準檢驗局」）— 上下級關係算同群\n"
        "\n"
        "回 **JSON only**，格式：{\"groups\": [[\"變體 1\", \"變體 2\", ...], [\"變體 3\", ...]]}\n"
        "不要任何解釋、不要 markdown code fence、直接純 JSON。\n"
        "\n"
        "輸入清單：\n"
        + "\n".join(f"- {v}" for v in sample)
    )
    try:
        out = client.text_query(prompt=prompt, model=model, temperature=0.0,
                                  max_tokens=512, think=False)
        data = _safe_json_extract(out)
        groups = data.get("groups", [])
        if isinstance(groups, list) and groups:
            # 驗證：每個 group 元素必為 input 內的字串
            valid_set = set(sample)
            cleaned = []
            seen = set()
            for g in groups:
                if not isinstance(g, list):
                    continue
                grp = [v for v in g if v in valid_set and v not in seen]
                for v in grp:
                    seen.add(v)
                if grp:
                    cleaned.append(grp)
            # 補上 LLM 漏的 (各成單群)
            for v in sample:
                if v not in seen:
                    cleaned.append([v])
            return cleaned
    except Exception:
        pass
    return [[v] for v in sample]


def detect_template_residue(file_summaries: list[dict],
                              ground_truth_main: str = "") -> list[dict]:
    """LLM 判斷哪些檔案像是「從另一份案件範本沿用未改」。

    Input: [{"file_id":..., "name":..., "snippet": "前 500 字"},...]
    Output: list of findings:
      [{"file_id":..., "confidence": "high"/"med"/"low",
        "reason": "...", "evidence": "...原文片段..."},...]
    """
    if not file_summaries:
        return []
    client, model = _get_client_and_model()
    if not client or not model:
        return []

    files_block = []
    for fs in file_summaries[:20]:  # 上限避免 context 爆
        snippet = (fs.get("snippet") or "")[:500]
        files_block.append(f"--- {fs['name']} (id: {fs['file_id'][:8]}) ---\n{snippet}")

    prompt = (
        "你是文件審查助理。請判斷以下檔案中，哪些**疑似從別案範本沿用未改**（可能漏改了公司名 / 客戶名 / 案號 / 簽署人）。\n"
        "判斷依據：\n"
        "1. 文件內主要實體跟其他檔案不一致（落款、簽署、甲方欄位提到不同公司）\n"
        "2. 內容大致對但小細節（公司名 / 案號 / 日期）跟主流不一致\n"
        f"3. 預期主角是：{ground_truth_main or '（未指定，請用各檔最高頻實體推估）'}\n"
        "\n"
        "回 **JSON only**：\n"
        '{"residues": [{"file_id":"<8 字 hex prefix>", "confidence":"high|med|low", "reason":"<簡短說明>", "evidence":"<原文片段 < 100 字>"}]}\n'
        "若沒發現任何疑似，回 {\"residues\": []}。\n"
        "不要 markdown code fence、直接純 JSON。\n"
        "\n"
        "檔案內容：\n\n"
        + "\n\n".join(files_block)
    )
    try:
        out = client.text_query(prompt=prompt, model=model, temperature=0.0,
                                  max_tokens=1024, think=False)
        data = _safe_json_extract(out)
        residues = data.get("residues", [])
        # 驗 + 對應回完整 file_id
        prefix_to_id = {fs["file_id"][:8]: fs["file_id"] for fs in file_summaries}
        out_findings = []
        for r in residues:
            if not isinstance(r, dict):
                continue
            pf = (r.get("file_id") or "").lower()[:8]
            full = prefix_to_id.get(pf)
            if not full:
                continue
            conf = r.get("confidence", "low")
            sev = "warn" if conf == "high" else "info"
            out_findings.append({
                "layer": "L3",
                "severity": sev,
                "category": "template-residue",
                "title": f"LLM 判斷此檔疑似沿用別案範本未改（信心 {conf}）",
                "detail": r.get("reason", "") or "未提供詳細說明",
                "page": None,
                "evidence": {
                    "file_id": full,
                    "confidence": conf,
                    "snippet": (r.get("evidence") or "")[:200],
                },
                # finding 直接綁該檔
                "_for_file": full,
            })
        return out_findings
    except Exception:
        return []


def apply_variant_groups_to_aggregated(aggregated: dict, variant_groups: list[list[str]]) -> dict:
    """把 LLM 判斷的變體群 merge 進 aggregated 結果（同群合計次數 + 檔案集）。

    回新的 aggregated dict（不 mutate input）。
    """
    if not variant_groups:
        return aggregated
    out = {}
    for kind, items in aggregated.items():
        if kind != "company":  # 只對公司類做合併（避免亂併政府機關）
            out[kind] = list(items)
            continue
        by_value = {it["value"]: it for it in items}
        merged_items = []
        merged_values = set()
        for grp in variant_groups:
            if not grp:
                continue
            members = [v for v in grp if v in by_value]
            if not members:
                continue
            canonical = max(members, key=lambda v: len(v))  # 取最長作為代表
            total_count = sum(by_value[v]["total_count"] for v in members)
            files = sorted({fid for v in members for fid in by_value[v]["files"]})
            merged_items.append({
                "value": canonical,
                "aliases": [v for v in members if v != canonical],
                "total_count": total_count,
                "files": files,
            })
            merged_values.update(members)
        # 補上未被 LLM 提到的 (各自獨立)
        for v, it in by_value.items():
            if v not in merged_values:
                merged_items.append(it)
        merged_items.sort(key=lambda e: -e["total_count"])
        out[kind] = merged_items
    return out

"""L4 — Vision LLM：對檔案渲染後的影像做視覺分析。

跟 L3 LLM Text 的差異：
- L3 看「文字」：fuzzy 變體合併、placeholder 判讀、文字殘留推論
- L4 看「影像」：偽造痕跡（PS 過的數字 / 日期 / 章 / 印）、
  字型混用、圖層拼貼痕跡、視覺 layout 異常

每個檔案：
- PDF → 渲染第一頁（如有 page > 1，可選樣張）成 PNG → 送 vision LLM
- 圖片 → 直接送 vision LLM

Vision model 必須是支援多模態的（gemma4:26b / qwen-vl / llava / minicpm-v 等）。
若 admin 設的是純文字模型，L4 自動 skip。

成本警告：每檔一次 vision LLM call，30 秒 - 2 分鐘 / 檔。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


def _get_vision_client_and_model():
    """取既有 LLM client，且 model 必須是 vision 系列。"""
    try:
        from app.core.llm_settings import llm_settings
        from app.core.llm_client import ModelInfo
        if not llm_settings.is_enabled():
            return None, None
        model = llm_settings.get_model_for("submission-check")
        # 簡單 vision detection：model name 含 vl / vision / llava / gemma4 / minicpm
        m_lower = (model or "").lower()
        looks_vision = any(s in m_lower for s in (
            "vl", "vision", "llava", "minicpm-v", "gemma4", "gemma3", "internvl",
            "qwen-vl", "qwen2-vl", "qwen3-vl",
        ))
        if not looks_vision:
            return None, None
        client = llm_settings.make_client()
        return client, model
    except Exception:
        return None, None


def vision_available() -> bool:
    client, model = _get_vision_client_and_model()
    return client is not None and bool(model)


def _safe_json_extract(text: str) -> dict:
    if not text:
        return {}
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        text = m.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    return {}


def _render_pdf_pages(pdf_path: Path, dpi: int = 150,
                      max_pages: int = 10) -> list[tuple[int, bytes]]:
    """渲染 PDF 多頁為 PNG。回 [(page_no_1based, png_bytes), ...]。
    超過 max_pages 截斷（vision LLM 每頁一次 call，太多會跑很久）。
    """
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
    except Exception:
        return []
    out: list[tuple[int, bytes]] = []
    try:
        zoom = dpi / 72
        mat = fitz.Matrix(zoom, zoom)
        for pno in range(min(doc.page_count, max_pages)):
            try:
                png = doc[pno].get_pixmap(matrix=mat).tobytes("png")
                out.append((pno + 1, png))
            except Exception:
                continue
    finally:
        doc.close()
    return out


def _read_image(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except Exception:
        return None


def vision_check_file(file_path: Path, ground_truth_main: str = "",
                      ground_truth_counterparty: str = "",
                      timeout: float = 60.0) -> list[dict]:
    """對單一檔案做 vision LLM 分析，回 findings list。
    沒設定 vision LLM 或檔案非 PDF / 圖片 → 回 []。
    呼叫時用獨立 LLM client（強制本檔案 timeout 上限），避免單一 hung 請求拖
    整個 job。
    """
    import logging
    log = logging.getLogger(__name__)
    client, model = _get_vision_client_and_model()
    if not client or not model:
        return []
    # 換成自己的短 timeout client，避免吃 default 300s
    try:
        from app.core.llm_settings import llm_settings
        from app.core.llm_client import LLMClient
        s = llm_settings.get()
        client = LLMClient(
            base_url=s["base_url"],
            api_key=s.get("api_key") or None,
            timeout=float(timeout),
        )
    except Exception:
        pass
    log.info("L6 vision: calling %s for %s (timeout=%ss)", model, file_path.name, timeout)
    suffix = file_path.suffix.lower()
    # 蒐集要送 vision 的頁面：PDF 一頁一次 call、圖片只一張
    pages_to_send: list[tuple[Optional[int], bytes]] = []
    truncated = False
    if suffix == ".pdf":
        pages_data = _render_pdf_pages(file_path, max_pages=10)
        pages_to_send = [(p, b) for p, b in pages_data]
        # check truncation
        try:
            import fitz
            doc = fitz.open(str(file_path))
            if doc.page_count > 10:
                truncated = True
            doc.close()
        except Exception:
            pass
    elif suffix in (".jpg", ".jpeg", ".png", ".tif", ".tiff"):
        b = _read_image(file_path)
        if b:
            pages_to_send = [(None, b)]
    else:
        return []
    if not pages_to_send:
        return []

    prompt = (
        "你是文件鑑識助理。請看這張影像（送件文件的渲染頁），"
        "判斷是否有以下問題：\n"
        "1. 偽造 / PS 痕跡：數字 / 日期 / 文字看起來被改過（重描、字型不一致、像素邊緣異常）\n"
        "2. 章 / 印異常：章不完整、看起來是貼上的、章內字看不清\n"
        "3. 圖層拼貼：不同部分明顯來自不同來源（背景顏色 / 解析度 / 字體不一）\n"
        f"4. 身分一致性：影像內出現的公司 / 機構名是否吻合預期主角「{ground_truth_main or '(未指定)'}」"
        f"{f'、對方「{ground_truth_counterparty}」' if ground_truth_counterparty else ''}\n"
        "\n"
        "回 **JSON only**：\n"
        '{"anomalies": [{"type": "tamper|stamp|layer|identity", "confidence": "high|med|low",'
        ' "description": "<簡短說明>"}], "overall_concern": "high|med|low|none",'
        ' "summary": "<整體一句話評估>"}\n'
        "\n"
        "注意：只回報「疑似」級別，不下「確認偽造」結論。"
        "若視覺看不出明顯問題，回 {\"anomalies\": [], \"overall_concern\": \"none\", \"summary\": \"未發現視覺異常\"}"
    )
    findings: list[dict] = []
    for page_no, png in pages_to_send:
        try:
            result = client.vision_query(png, prompt=prompt, model=model, temperature=0.0)
            if not isinstance(result, dict) or "anomalies" not in result:
                if isinstance(result, dict):
                    for v in result.values():
                        if isinstance(v, str):
                            parsed = _safe_json_extract(v)
                            if parsed.get("anomalies") is not None:
                                result = parsed
                                break
                if not isinstance(result, dict) or "anomalies" not in result:
                    continue
        except Exception as e:
            log.warning("L6 vision call failed for %s p.%s: %s", file_path.name, page_no, e)
            continue

        anomalies = result.get("anomalies") or []
        for a in anomalies:
            if not isinstance(a, dict):
                continue
            conf = a.get("confidence", "low")
            sev = "warn" if conf == "high" else "info"
            a_type = a.get("type", "?")
            type_label = {"tamper": "偽造痕跡", "stamp": "章 / 印異常",
                           "layer": "圖層拼貼", "identity": "身分不一致"}.get(a_type, a_type)
            page_str = f" p.{page_no}" if page_no else ""
            findings.append({
                "layer": "L4",
                "severity": sev,
                "category": f"vision-{a_type}",
                "title": f"視覺疑似{type_label}（信心 {conf}）{page_str}",
                "detail": (a.get("description") or "未提供詳細說明") + " — 僅供參考，請人工確認。",
                "page": page_no,
                "evidence": {"vision_type": a_type, "confidence": conf,
                              "raw_description": a.get("description", ""), "page": page_no},
            })
        overall = result.get("overall_concern", "none")
        summary = result.get("summary", "")
        if overall in ("high", "med") and summary:
            findings.append({
                "layer": "L4",
                "severity": "info",
                "category": "vision-summary",
                "title": f"視覺整體評估（{overall}）" + (f" p.{page_no}" if page_no else ""),
                "detail": summary,
                "page": page_no,
                "evidence": {"overall_concern": overall, "page": page_no},
            })

    if truncated:
        findings.append({
            "layer": "L4",
            "severity": "info",
            "category": "vision-truncated",
            "title": "視覺分析只處理前 10 頁",
            "detail": f"檔案頁數較多，只送前 10 頁給 vision LLM 分析（避免成本爆炸）。",
            "page": None,
            "evidence": {"limit": 10},
        })
    return findings

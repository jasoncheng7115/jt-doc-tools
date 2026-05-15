"""清單識別 fixer。

把字面「1. 2. 3.」「(一) (二)」「• -」等開頭的連續段落識別成清單，套用 Word
List style 並剝離開頭符號。

策略（保守）：
- 連續 ≥ 2 個段落符合「同一種」清單模式 → 視為清單區段
- 已是 List/Heading style 的不動
- 表格內段落不動（避免破壞表格內編號）
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# 清單 pattern：(name, regex, list_style)
_LIST_PATTERNS: list[tuple[str, "re.Pattern", str]] = [
    ("arabic_dot",     re.compile(r"^(\d+)\.\s+(.+)"),                "List Number"),
    ("arabic_paren",   re.compile(r"^\((\d+)\)\s+(.+)"),              "List Number"),
    ("arabic_paren_f", re.compile(r"^([(（])(\d+)([)）])\s*(.+)"),     "List Number"),
    ("cn_num_comma",   re.compile(r"^([一二三四五六七八九十]+)、\s*(.+)"),  "List Number"),
    ("cn_num_dot",     re.compile(r"^([一二三四五六七八九十]+)\.\s*(.+)"),  "List Number"),
    ("cn_num_paren",   re.compile(r"^[(（]([一二三四五六七八九十]+)[)）]\s*(.+)"),  "List Number"),
    ("cn_top",         re.compile(r"^([壹貳參肆伍陸柒捌玖拾]+)、\s*(.+)"),  "List Number"),
    ("bullet_dash",    re.compile(r"^[-–]\s+(.+)"),                   "List Bullet"),
    ("bullet_star",    re.compile(r"^\*\s+(.+)"),                     "List Bullet"),
    ("bullet_dot",     re.compile(r"^[•‧·▪◆■□◇○●]\s*(.+)"),           "List Bullet"),
]

# 「保留字面」的台灣公文層級（不要剝離 "壹、" "一、"）
_PRESERVE_LITERAL = {"cn_top"}


def _match_pattern(text: str) -> tuple[str, str, str] | None:
    """回 (pattern_name, body_after_strip, list_style)。沒命中回 None。"""
    if not text:
        return None
    for name, rx, style in _LIST_PATTERNS:
        m = rx.match(text)
        if m:
            # body = 最後一個 capture group（內文）；其他是 marker
            body = m.group(m.lastindex)
            return name, body, style
    return None


def _is_listy_or_heading(p) -> bool:
    style_name = (p.style.name if p.style else "") or ""
    return ("List" in style_name) or ("Heading" in style_name) or ("Title" in style_name)


def _set_paragraph_style(p, style_name: str) -> None:
    """設 docx style — 不存在的 style 不會 raise，靜默 fallback。"""
    try:
        styles = p.part.document.styles
        if style_name in [s.name for s in styles]:
            p.style = styles[style_name]
    except Exception:
        pass


def _replace_paragraph_text(p, new_text: str) -> None:
    """把段落內容換成 new_text，保留第一個 run 的字型屬性。"""
    if not p.runs:
        p.add_run(new_text)
        return
    # 保留第一個 run 屬性，重設文字；後續 run 清空
    first = p.runs[0]
    first.text = new_text
    for r in p.runs[1:]:
        r.text = ""


def fix_list_detect(docx_doc, pdf_truth, alignment) -> dict:
    """掃整份 docx，連續 ≥ 2 個同 pattern 段落 → 套清單 style + 剝符號。"""
    paragraphs = list(docx_doc.paragraphs)
    converted = 0
    runs: dict[str, int] = {}  # pattern → count

    i = 0
    while i < len(paragraphs):
        p = paragraphs[i]
        if _is_listy_or_heading(p):
            i += 1
            continue
        text = (p.text or "").strip()
        match = _match_pattern(text)
        if not match:
            i += 1
            continue
        pattern_name, body, style_name = match
        # 看連續多少個段落同 pattern
        run_paras = [(p, body, style_name)]
        j = i + 1
        while j < len(paragraphs):
            np = paragraphs[j]
            if _is_listy_or_heading(np):
                break
            ntext = (np.text or "").strip()
            nm = _match_pattern(ntext)
            if not nm or nm[0] != pattern_name:
                break
            run_paras.append((np, nm[1], nm[2]))
            j += 1
        if len(run_paras) >= 2:
            # 連續清單成立 — 套用
            for rp, rbody, rstyle in run_paras:
                if pattern_name in _PRESERVE_LITERAL:
                    # 保留字面，只套樣式
                    pass
                else:
                    # 剝符號 + 套樣式
                    _replace_paragraph_text(rp, rbody)
                _set_paragraph_style(rp, rstyle)
                converted += 1
            runs[pattern_name] = runs.get(pattern_name, 0) + 1
            i = j
        else:
            i += 1

    return {"fixer": "list_detect", "converted_paragraphs": converted, "runs_by_pattern": runs}

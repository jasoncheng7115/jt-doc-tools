"""產出 Markdown 改善報告，下載給使用者除錯用。"""
from __future__ import annotations

from typing import Any


def render_markdown_report(report: dict[str, Any], src_filename: str = "") -> str:
    """從 pipeline 回的 report dict 產 Markdown 報告字串。"""
    lines: list[str] = []
    lines.append(f"# PDF 轉文書檔 改善報告")
    if src_filename:
        lines.append(f"\n**來源檔**：{src_filename}")
    lines.append("")

    # PDFTruth
    truth = report.get("pdf_truth") or {}
    if truth:
        lines.append("## PDF 真值解析\n")
        lines.append(f"- 頁數：{truth.get('pages', '-')}")
        lines.append(f"- 語言：{truth.get('language', '-')}")
        lines.append(f"- 內文字型：{truth.get('body_font', '-')} @ {truth.get('body_size', '-')}pt")
        lines.append(f"- 含掃描頁：{'是' if truth.get('has_scanned') else '否'}")
        fonts = truth.get("fonts") or []
        if fonts:
            lines.append(f"- 字型清單（{len(fonts)} 個）：")
            for f in fonts:
                emb = "embedded" if f.get("embedded") else "not-embedded"
                cmap = "+ToUnicode" if f.get("cmap") else "no-CMap"
                cjk = "CJK" if f.get("cjk") else "西文"
                lines.append(f"  - `{f.get('name')}` · {emb} · {cmap} · {cjk} · 用 {f.get('use', 0)} 次")
        lines.append("")

    # Alignment
    al = report.get("alignment") or {}
    if al:
        lines.append("## docx ↔ PDF 對齊\n")
        lines.append(f"- 對齊率：{al.get('match_rate', 0)*100:.0f}%")
        lines.append(f"- 已對齊段落：{al.get('matched', 0)}")
        lines.append(f"- 未對齊 docx 段落：{al.get('unmatched_docx', 0)}")
        lines.append(f"- 未對齊 PDF block：{al.get('unmatched_pdf', 0)}")
        lines.append("")

    # Diagnosis
    diag = report.get("diagnosis") or {}
    if diag:
        s = diag.get("summary") or {}
        lines.append("## 診斷摘要\n")
        lines.append(f"- docx 段落：{s.get('total_paragraphs_docx', 0)}")
        lines.append(f"- PDF text block：{s.get('total_blocks_pdf', 0)}")
        lines.append(f"- docx 表格：{s.get('total_tables_docx', 0)}")
        lines.append(f"- PDF 圖片：{s.get('total_images_pdf', 0)}")
        lines.append(f"- 中文字元比例：{s.get('cjk_ratio', 0)*100:.1f}%")
        issues = diag.get("issues") or []
        if issues:
            lines.append(f"\n### 偵測到 {len(issues)} 個議題\n")
            for it in issues[:30]:
                loc = it.get("location") or {}
                lines.append(
                    f"- `{it.get('id')}` · **{it.get('type')}** · {it.get('severity')}"
                    f" · 段 #{loc.get('paragraph_index', '-')} · {it.get('evidence', '')}"
                )
            if len(issues) > 30:
                lines.append(f"- … 另 {len(issues)-30} 個未列出")
        lines.append("")

    # Fixers
    fixers = report.get("fixers") or []
    if fixers:
        lines.append("## 後處理 fixer 變動\n")
        lines.append("| Fixer | 變動 |")
        lines.append("| --- | --- |")
        for f in fixers:
            name = f.get("fixer", "?")
            details = ", ".join(f"{k}={v}" for k, v in f.items() if k != "fixer")
            lines.append(f"| `{name}` | {details} |")
        lines.append("")

    # Style apply
    sa = report.get("style_apply") or {}
    if sa:
        items = sa.get("items") or []
        if items:
            lines.append("## 樣式套用\n")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    # Errors
    errs = report.get("errors") or []
    if errs:
        lines.append("## ⚠ 錯誤 / 警告\n")
        for e in errs:
            lines.append(f"- {e}")
        lines.append("")

    return "\n".join(lines)

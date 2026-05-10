"""Chaining — 把 finding category 對應到既有工具的修正路徑。

每個 category 給一個 (tool_url, label, hint_text)。
前端在每個 fail/warn finding 旁顯示「[一鍵跳 X 修]」按鈕。
"""
from __future__ import annotations


# (tool path, label, hint)
CATEGORY_TO_TOOL: dict[str, tuple[str, str, str]] = {
    # === 結構面 ===
    "metadata-leak":   ("/tools/pdf-metadata/", "中繼資料清除",
                         "用此工具清掉 metadata 內的作者 / 路徑 / 上次保存資訊。"),
    "js":              ("/tools/pdf-hidden-scan/", "隱藏內容掃描",
                         "用此工具一鍵清除 PDF 內 JavaScript / OpenAction。"),
    "open-action":     ("/tools/pdf-hidden-scan/", "隱藏內容掃描",
                         "同上，清除 OpenAction 動作。"),
    "embed":           ("/tools/pdf-hidden-scan/", "隱藏內容掃描",
                         "用此工具移除嵌入檔。"),
    "incremental":     ("/tools/pdf-metadata/", "中繼資料清除",
                         "重新另存 PDF 可移除修訂歷史。"),
    "track-changes":   ("/tools/pdf-hidden-scan/", "隱藏內容掃描（DOCX 版）",
                         "Word 內接受 / 拒絕所有追蹤修訂後另存。"),
    "comments":        ("/tools/pdf-hidden-scan/", "隱藏內容掃描",
                         "Word 內刪除所有審閱註解後另存。"),
    "macro":           ("/tools/pdf-hidden-scan/", "隱藏內容掃描",
                         "Word 另存為 .docx（非 .docm）即可去 macro。"),
    # === 文件本體 ===
    "form-blank":      ("/tools/pdf-editor/", "PDF 編輯器",
                         "用 PDF 編輯器在指定欄位填入應有內容。"),
    "duplicate-hash":  ("",  "",
                         "兩份檔案內容完全相同，請確認是否誤上傳，或從本案件移除多餘檔。"),
    # === 跨檔身分一致性 ===
    "tax-id-invalid":  ("/tools/pdf-editor/", "PDF 編輯器",
                         "找出統編位置修正校驗碼。"),
    "tax-id-mismatch": ("/tools/pdf-editor/", "PDF 編輯器",
                         "找出非預期統編並改成基準資訊內的統編。"),
    "tax-id-multiple": ("",  "",
                         "本案件多個有效統編 — 請人工確認預期主角統編。"),
    "identity-mismatch": ("/tools/pdf-editor/", "PDF 編輯器",
                          "找出非預期主體位置改回主角名稱。"),
    "identity-outlier":  ("/tools/pdf-editor/", "PDF 編輯器",
                          "把疑似漏改的範本字眼改回正確主角。"),
    "case-num-other":  ("/tools/pdf-editor/", "PDF 編輯器",
                         "改回預期案號。"),
    "template-residue": ("/tools/pdf-editor/", "PDF 編輯器",
                          "LLM 判斷此檔疑似沿用範本 — 請逐處檢查。"),
    # === E 類重要檢查 ===
    "amount-mismatch": ("/tools/pdf-editor/", "PDF 編輯器",
                         "確認金額位數正確（少 / 多 0）。"),
    "date-expiring-soon": ("",  "",
                           "證書 / 證明早於案件截止日 — 請更新到最新版。"),
    "attachment-mismatch": ("",  "",
                            "確認附件數量是否漏附 / 多附。"),
    # === L2 OCR 資訊性 ===
    "ocr-skipped":     ("",  "",
                         "資訊性提示，無需處理。"),
    "ocr-truncated":   ("",  "",
                         "若需處理後續頁面內容，請拆分檔案再上傳。"),
}


def chaining_for(category: str) -> dict:
    """回 {tool_url, label, hint}（找不到時 url/label 為空）。"""
    if not category:
        return {"tool_url": "", "label": "", "hint": ""}
    url, label, hint = CATEGORY_TO_TOOL.get(category, ("", "", ""))
    return {"tool_url": url, "label": label, "hint": hint}

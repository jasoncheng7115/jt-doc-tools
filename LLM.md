# LLM AI 加值功能

本專案的核心工具**完全不依賴 LLM 也能正常使用**;LLM 是**選用的加值層**,啟用後讓部分工具更聰明、更省力、更貼合實際情境。

> **預設關閉。** 安裝後不接任何 LLM 服務,所有核心工具(掃描、轉檔、編輯、簽章、加密、合併、分拆、去識別化…)照樣 100% 可用。
>
> 啟用方式:`/admin/llm-settings` 頁面填 OpenAI-compatible API base URL(如本機 Ollama / vLLM / LM Studio / DGX Spark)+ 選預設模型,即可在 8 個工具看到「啟用 LLM」選項。

## 為什麼要支援 LLM

| 場景 | 沒 LLM | 有 LLM |
|---|---|---|
| 把 PDF 雙欄排版的字一行一行抓出來 | 行被切斷,要手動接回 | 自動重排回原段落 |
| 抓敏感資料(身分證 / 電話) | regex 抓死規則(銀行帳號、統編) | 加抓「客戶代號 = `A-2024-0815`」「主管姓 王」這類 context 案例 |
| 看 100 頁的合約找改了什麼 | 行 diff 一條條看 | 直接告訴你「第 3.2 條保固期改 12→24 個月,第 5.1 條付款改月結 30→60」 |
| 註解 30 條意見要分類 | 手動按嚴重度排序 | 自動分「重大 / 一般 / 提問」三類 |

## 支援的工具(共 8 個)

> 模式分兩種:
> - **text** — 純文字 chat,任何 OpenAI-compatible 文字模型都能跑(Llama / Qwen / Mistral / Gemma 等)
> - **vision** — 多模態,需要視覺模型(qwen3-vl / gemma3 / LLaVA / minicpm-v 等)

### 1. 逐句翻譯 (`translate-doc`) — text

**LLM 做什麼**:把 source 文件逐句翻成目標語言,可保留 Word / ODT 排版重新輸出。

**典型效果**:
- 中翻英、英翻中、日翻中
- 保留段落、標題、清單結構
- 比 Google Translate 更尊重專業領域用詞(可在 prompt 提示「這是法律文件」「這是醫療報告」)

**省時感**:50 頁合約翻譯,從半天 → 10 分鐘。

### 2. 擷取文字 — LLM 段落重排 (`pdf-extract-text`) — text

**LLM 做什麼**:把 PDF 視覺被切斷的句子重新接回完整段落。

**典型效果**:
- 雙欄論文 → 單欄純文字,段落完整
- 學術期刊把footnote / header / page number 過濾掉
- 表格內容被 PyMuPDF 拆得亂七八糟時 → LLM 重組回 markdown 表格

**省時感**:抓 30 頁論文做摘錄,排版整理時間 30 min → 3 min。

### 3. 表單自動填寫 — LLM 校驗 (`pdf-fill`) — vision

**LLM 做什麼**:填完表單後,LLM 看渲染後的 PNG 比對「值有沒有正確落在欄位內」。

**典型效果**:
- 偵測欄位錯位(填到隔壁格)
- 偵測值被截斷(欄位太窄)
- 偵測 checkbox / radio 沒勾對

**省時感**:批次填 50 份廠商表,人工檢查 → 自動 review,只有 LLM flag 的才需人看。

> 預設模型 `qwen3-vl:8b`(DGX Spark + Ollama),也可用 `gemma3:27b`(品質更好,VRAM 更高)。

### 4. 文件去識別化 — LLM 補偵測 (`doc-deident`) — text

**LLM 做什麼**:regex 抓固定格式(身分證 / 電話 / 銀行帳號 / 統編),LLM 補抓 context-sensitive 案例。

**典型效果**:
- 「客戶代號:A-2024-0815」自動辨識
- 「主管 王經理」抓出姓氏
- 「聯絡 lulu@xxx」抓出英文人名
- 「應收帳款編號 R20260115-3」抓出非標準格式

**省時感**:法務 / 合規團隊清理舊合約批次,從一份份手動標 → LLM 跑完只看 review 報告。

### 5. 文字去識別化 — LLM 補偵測 (`text-deident`) — text

**LLM 做什麼**:同 doc-deident,但接受純文字輸入(貼上 / `.txt` / `.md`)。

**典型效果**:適合處理:
- 客服 ticket / Slack 對話備份
- email 內容(一般正文,非 PDF 附件)
- 程式 log 含使用者識別碼

**省時感**:支援 LLM 後可處理「沒有結構但有 context」的文字,regex 完全抓不到的客戶代號 / 內部編號等。

### 6. 字數統計 — LLM 摘要 / 關鍵字 (`pdf-wordcount`) — text

**LLM 做什麼**:計完字數後,LLM 額外生成:
- 3-5 句**摘要**
- TOP 10 **關鍵概念**

**典型效果**:
- 拿到一份 80 頁報告,30 秒內知道「主要在講什麼、主軸關鍵字是什麼」
- 適合會議前快速 brief

**省時感**:從要逐頁掃 30 分鐘到只需 30 秒讀摘要。

### 7. 註解整理 — LLM 自動分組 (`pdf-annotations`) — text

**LLM 做什麼**:抓 PDF 內所有 annotation 後,LLM 依語意自動分:
- **重大** — 影響結構的修改建議
- **一般** — 用詞 / 排版建議
- **提問** — 審閱者問問題

**典型效果**:
- 多人審閱的合約 / 論文 / 文件,一鍵看「真正重要的修改」
- 不用手動翻 50 條 sticky note 找哪些重要

**省時感**:30 條註解的合約,排序時間 15 min → 30 秒(LLM 出表後人只看 5 條重大)。

### 8. 文件差異比對 — LLM 變動摘要 (`doc-diff`) — text

**LLM 做什麼**:做完行 diff 後,LLM 額外提供「**主要修改了哪幾條條款 / 段落**」自然語言摘要。

**典型效果**:
- 「第 3.2 條保固期由 12 個月改為 24 個月」
- 「新增第 6 條:智慧財產權歸屬」
- 「刪除原第 8.4 條罰則條款」

**省時感**:看 100 行 diff 從一行一行讀 → 直接看 LLM 摘要的 3-5 條重點。

## 部署選項

| 部署 | 適用 | 模型範例 |
|---|---|---|
| **本機 Ollama** | 個人 / 小團隊試用 | `qwen3:8b`、`gemma3:4b`(消費級 GPU 可跑) |
| **DGX Spark / 工作站** | 公司內部單一 LLM 伺服器 | `qwen3-vl:8b`(視覺)、`gemma3:27b`(高品質文字) |
| **vLLM / LM Studio / jan.ai** | 偏好其他 OpenAI-compat 後端 | 視 backend 而定 |
| **遠端 OpenAI / Anthropic** | 不在意資料外送的場景 | 預設**不**支援(專案精神是不上雲),但 OpenAI-compat URL 可設,風險自負 |

## 啟用後 admin UI 看到什麼

`/admin/llm-settings`:
- Base URL 輸入框(預設 `http://localhost:11434/v1`)
- API key(本機 Ollama 不需要,雲端供應商需要)
- Timeout 秒數
- **預設模型** 下拉(從 `/v1/models` 拉清單)
- **每個工具 override 預設**(例如 vision 模型用 qwen3-vl,純文字用 gemma3)
- 「測試連線」按鈕,顯示 latency 跟模型清單

## 安全 / 隱私

- **預設關閉**,核心工具不依賴
- 啟用後也只有 admin 設定的 base URL 會被連到(SSRF 防護:URL allowlist + 雲端 metadata host blocklist,見 `app/core/url_safety.py`)
- 內部 LAN IP(10/8、172.16/12、192.168/16、127/8)允許 — 內網 LLM 是常見部署
- LLM 處理過的內容寫進 audit log(僅記 metadata:工具 ID、處理時間、輸入大小、是否成功;**不**記實際內容,因可能含隱私)
- 一般 user 看不到完整 LLM server URL,只看到模型名稱(避免內網 IP 外洩給其他 user)

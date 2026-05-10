# LLM AI 加值功能

本專案的核心工具**完全不依賴 LLM 也能正常使用**;LLM 是**選用的加值層**，啟用後讓部分工具更聰明、更省力、更貼合實際情境。

> **預設關閉。** 安裝後不接任何 LLM 服務，所有核心工具（掃描、轉檔、編輯、簽章、加密、合併、分拆、去識別化…）照樣 100% 可用。
>
> 啟用方式：`/admin/llm-settings` 頁面填 OpenAI-compatible API base URL（如本機 Ollama / vLLM / LM Studio / DGX Spark）+ 選預設模型，即可在 8 個工具看到「啟用 LLM」選項。

## 為什麼要支援 LLM

| 場景 | 沒 LLM | 有 LLM |
|---|---|---|
| 把 PDF 雙欄排版的字一行一行抓出來 | 行被切斷，要手動接回 | 自動重排回原段落 |
| 抓敏感資料（身分證 / 電話） | regex 抓死規則（銀行帳號、統編） | 加抓「客戶代號 = `A-2024-0815`」「主管姓 王」這類 context 案例 |
| 看 100 頁的合約找改了什麼 | 行 diff 一條條看 | 直接告訴你「第 3.2 條保固期改 12→24 個月，第 5.1 條付款改月結 30→60」 |
| 註解 30 條意見要分類 | 手動按嚴重度排序 | 自動分「重大 / 一般 / 提問」三類 |

## 支援的工具（共 8 個）

> 模式分兩種：
> - **text** — 純文字 chat，任何 OpenAI-compatible 文字模型都能跑（Gemma / Llama / Mistral 等）
> - **vision** — 多模態，需要視覺模型（gemma4 / gemma3 / LLaVA / minicpm-v 等）

### 1. 逐句翻譯 (`translate-doc`) — text

**LLM 做什麼**：把 source 文件分句送 LLM 翻譯，左原文右譯文並排對照，每句可單獨重譯。

**已實作的特色**:
- 並行翻譯（預設 4 並發，admin 可調 1-16）
- 中翻英、英翻中、繁/簡互轉、日中、韓中、法中、德中、西中、越中、泰中、印中、俄中
- 自動偵測來源語言或手動指定
- 內建台灣繁體 IT 用詞對照表（kernel→核心、software→軟體、image→圖片 等），目標語言設繁中時自動進 prompt 防止大陸用語滲入
- **可選填「文件領域」hint**（法律合約 / 醫療報告 / 軟體技術文件 等），會進 prompt 讓 LLM 挑對應專業用詞

**輸入支援**:PDF / DOCX / ODT / ODS / ODP / TXT / MD，或直接貼純文字
**輸出**:web 並排對照表（可複製譯文 / 對照），目前不輸出回 docx/odt 保留排版

### 2. 擷取文字 — LLM 段落重排 (`pdf-extract-text`) — text

**LLM 做什麼**：用 PyMuPDF 抽出後，逐段送 LLM 把被換行切散的句子重新合併成自然段落。

**已實作**:
- 抽完文字後勾選「LLM 段落重排」即啟動，SSE 即時 stream 進度
- 每段送 LLM 一次，timeout / 失敗會 fallback 原樣輸出
- 預覽前 5000 字
- 下載 TXT / Markdown / DOCX / ODT（後兩者需 Office 引擎）

### 3. 表單自動填寫 — LLM 校驗 (`pdf-fill`) — vision

**LLM 做什麼**：填完表單後，逐欄 crop 出 PNG 送 vision LLM,binary YES/NO 回答「這個欄位的值正確嗎」。

**已實作**:
- 單份 PDF / 單次填寫（目前**不支援批次多檔**）
- 啟動 LLM review 後走 job-based async，可 poll 進度
- 結果以 review 報告呈現，標出可疑欄位，user 決定要不要回頭調整

> 預設模型 `gemma4:26b`（DGX Spark + Ollama；MoE 架構、實測廠商表單 100% 準）。

### 4. 文件去識別化 — LLM 補偵測 (`doc-deident`) — text

**LLM 做什麼**:regex 抓固定格式（身分證 / 電話 / 銀行帳號 / 統編 等 14+ 種）,LLM 額外掃 context-sensitive 案例。

**已實作 LLM 抓的範例**:
- 「客戶代號：A-2024-0815」非標準格式
- 「主管 王經理」context 中的姓氏
- 「應收帳款編號 R20260115-3」內部編號

**輸入支援**：單份 PDF 或單份 Office 文書（目前**不支援批次多檔**）

### 5. 文字去識別化 — LLM 補偵測 (`text-deident`) — text

**LLM 做什麼**：同 doc-deident，但接受純文字輸入（貼上 / `.txt` / `.md`）。

**典型場景**:
- 客服對話 / Slack / log 含使用者識別碼
- Email 正文（一般文字，非 PDF 附件）
- 任何「沒結構但有 context」的文字

### 6. 字數統計 — LLM 摘要 / 關鍵字 (`pdf-wordcount`) — text

**LLM 做什麼**：計完字數後，**選填**勾「LLM 摘要 / 關鍵字」，額外請 LLM 生成內容摘要 + 關鍵概念列表。

**已實作**:
- 預設關閉（只計字數）;user 勾才送 LLM
- 摘要長度依模型自由發揮，prompt 不硬限句數

### 7. 註解整理 — LLM 自動分組 (`pdf-annotations`) — text

**LLM 做什麼**：抓 PDF 內所有 annotation 後，**選填**送 LLM 依「註解內容主題」自動分群。

**分群方式**:
- LLM 自由判斷（範例：「需修改文字」「格式問題」「詢問疑點」「已確認」「其他」等），非固定類別
- 每群給名稱 + 一句話 summary + 該群成員的 annotation id 列表

**輸入**：單份 PDF（目前**不支援批次多檔**）

### 8. 文件差異比對 — LLM 變動摘要 (`doc-diff`) — text

**LLM 做什麼**:2 份文件做完行 diff 後，**選填**請 LLM 用 3-5 句繁中話寫整體變動摘要（不超過 200 字）。

**已實作**:
- 預設關閉
- 摘要是「整體 high-level 變動」，不會逐條列出所有改動（細節在 diff 表）
- 適合搭配 diff 表一起看：LLM 摘要先抓主軸，diff 表看細節

## 部署選項

| 部署 | 適用 | 模型範例 |
|---|---|---|
| **本機 Ollama** | 個人 / 小團隊試用 | `gemma3:4b`（消費級 GPU 可跑；vision 任務建議改跑 `gemma4:26b`） |
| **DGX Spark / 工作站** | 公司內部單一 LLM 伺服器 | `gemma4:26b`（預設；視覺 + 文字皆可） |
| **vLLM / LM Studio / jan.ai** | 偏好其他 OpenAI-compat 後端 | 視 backend 而定 |
| **遠端 OpenAI / Anthropic** | 不在意資料外送的場景 | 預設**不**支援（專案精神是不上雲），但 OpenAI-compat URL 可設，風險自負 |

## 啟用後 admin UI 看到什麼

`/admin/llm-settings`:
- Base URL 輸入框（預設 `http://localhost:11434/v1`)
- API key（本機 Ollama 不需要，雲端供應商需要）
- Timeout 秒數
- **預設模型** 下拉（從 `/v1/models` 拉清單）
- **每個工具 override 預設**（例如 vision 模型用 gemma4，純文字用 gemma3）
- 「測試連線」按鈕，顯示 latency 跟模型清單

## 安全 / 隱私

- **預設關閉**，核心工具不依賴
- 啟用後也只有 admin 設定的 base URL 會被連到（SSRF 防護：URL allowlist + 雲端 metadata host blocklist，見 `app/core/url_safety.py`)
- 內部 LAN IP（10/8、172.16/12、192.168/16、127/8）允許 — 內網 LLM 是常見部署
- LLM 處理過的內容寫進 audit log（僅記 metadata：工具 ID、處理時間、輸入大小、是否成功；**不**記實際內容，因可能含隱私）
- 一般 user 看不到完整 LLM server URL，只看到模型名稱（避免內網 IP 外洩給其他 user）

# API 使用手冊

Jason Tools 文件工具箱對外提供 RESTful API，所有功能皆有對應 endpoint，可整合到自動化流程、自家系統或排程工作。

> **基本原則**：API 預設可不認證（與 web UI 同步開放）；需要鎖時管理員到 admin 「API Token」頁開啟 enforce 模式並核發 bearer token。

---

## 目錄

- [1. 認證](#1-認證)
  - [不啟用認證（預設）](#不啟用認證預設)
  - [啟用 API token](#啟用-api-token)
- [2. 通用約定](#2-通用約定)
- [3. 即時回應 API](#3-即時回應-api)
  - [3.1 文書轉 PDF（OxOffice/LibreOffice）](#31-文書轉-pdfoxofficelibreoffice)
  - [3.2 PDF 字數統計](#32-pdf-字數統計)
  - [3.3 PDF 註解整理](#33-pdf-註解整理)
  - [3.4 PDF 註解清除](#34-pdf-註解清除)
  - [3.5 PDF 註解平面化](#35-pdf-註解平面化)
  - [3.6 圖片轉 PDF](#36-圖片轉-pdf)
  - [3.7 文字差異比對](#37-文字差異比對)
  - [3.8 逐句翻譯](#38-逐句翻譯)
- [4. Job 模式 API（長時間操作）](#4-job-模式-api長時間操作)
  - [4.1 LLM 校驗（pdf-fill）](#41-llm-校驗pdf-fill)
  - [4.2 查 job 狀態](#42-查-job-狀態)
  - [4.3 下載 job 結果](#43-下載-job-結果)
  - [4.4 下載 job 結果為 PNG](#44-下載-job-結果為-png)
- [5. 完整端點對照表](#5-完整端點對照表)
  - [通用](#通用)
  - [工具直連](#工具直連)
  - [管理端（需 admin 登入或 admin role token）](#管理端需-admin-登入或-admin-role-token)
- [6. 整合範例](#6-整合範例)
  - [6.1 GitLab CI / GitHub Actions：把 Word 文件自動轉 PDF](#61-gitlab-ci--github-actions把-word-文件自動轉-pdf)
  - [6.2 Python 客戶端：批次清掉 PDF 註解](#62-python-客戶端批次清掉-pdf-註解)
  - [6.3 Shell：監看 job 完成後下載](#63-shell監看-job-完成後下載)
  - [6.4 Node.js：逐句翻譯](#64-nodejs逐句翻譯)
- [7. CLI 管理 token](#7-cli-管理-token)
- [8. 速率限制 / 大檔上限](#8-速率限制--大檔上限)
- [9. 變更歷史](#9-變更歷史)

---

## 1. 認證

### 不啟用認證（預設）

新安裝預設不啟用認證，所有 `/api/*` 直接可用，無需 token。

### 啟用 API token

管理員到 `admin → API Token`：
1. 點「核發新 token」→ 輸入用途名稱（例 `gitlab-ci`）→ 拿到 64 字 hex token（**只顯示一次，存好**）
2. 勾選「Enforce — 沒帶 token 一律拒絕」並儲存

之後所有 `/api/*` 必須帶以下任一形式：

```http
Authorization: Bearer 64char-hex-token-here
```

或 query string：

```http
GET /api/jobs/abc123?token=64char-hex-token-here
```

未帶或 token 無效 → `401 Unauthorized` JSON：

```json
{"ok": false, "detail": "需要有效的 API token（Authorization: Bearer ...）"}
```

> Token 透過 admin / `jtdt` CLI 管理，與 web 認證 (`jtdt-admin` / LDAP / AD) 完全獨立。

---

## 2. 通用約定

| 項目 | 說明 |
|---|---|
| Base URL | `http://your-server:8765`（依 `JTDT_HOST` / `JTDT_PORT` 而定） |
| Content-Type | 上傳檔案：`multipart/form-data`；JSON：`application/json` |
| 回應格式 | JSON（除非明確回 PDF / PNG / ZIP 二進位資料） |
| 錯誤格式 | `{"detail": "錯誤訊息"}` + 對應 HTTP 4xx/5xx |
| 大檔處理 | 大型 / 耗時操作走 **job 模式**：先回 `{"job_id": "..."}`，再用 `/api/jobs/{job_id}` 輪詢狀態，完成後 `/api/jobs/{job_id}/download` 取結果 |

---

## 3. 即時回應 API

這些 endpoint 同步處理、直接回 JSON 或檔案。適合小檔、快速分析。

### 3.1 文書轉 PDF（OxOffice/LibreOffice）

```bash
POST /api/convert-to-pdf
```

| 參數 | 類型 | 說明 |
|---|---|---|
| `file` | UploadFile | Word / Excel / PowerPoint / ODF |

**範例**：

```bash
curl -X POST http://localhost:8765/api/convert-to-pdf \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@報告.docx" \
  --output 報告.pdf
```

回應：PDF 二進位 (`application/pdf`)。失敗 4xx + JSON `{"detail": "..."}`。

---

### 3.2 PDF 字數統計

```bash
POST /tools/pdf-wordcount/api/pdf-wordcount
```

| 參數 | 類型 | 說明 |
|---|---|---|
| `file` | UploadFile | PDF |

**範例**：

```bash
curl -X POST http://localhost:8765/tools/pdf-wordcount/api/pdf-wordcount \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf"
```

回應 JSON（節錄）：

```json
{
  "filename": "document.pdf",
  "page_count": 12,
  "char_count": 18342,
  "word_count": 3521,
  "paragraph_count": 87,
  "sentence_count": 295,
  "estimated_reading_minutes": 12.5,
  "per_page_chars": [...],
  "top_words_zh1": [...], "top_words_zh2": [...], "top_words_en": [...]
}
```

---

### 3.3 PDF 註解整理

```bash
POST /tools/pdf-annotations/api/pdf-annotations
```

| 參數 | 類型 | 說明 |
|---|---|---|
| `file` | UploadFile | PDF |

回 JSON：每筆註解的頁碼、類型、作者、內容、座標、時間。

---

### 3.4 PDF 註解清除

```bash
POST /tools/pdf-annotations-strip/api/pdf-annotations-strip
```

| 參數 | 類型 | 說明 |
|---|---|---|
| `file` | UploadFile | PDF |
| `authors` | str (CSV，可選) | 只清這些作者；空白 = 全清 |
| `types` | str (CSV，可選) | 只清這些類型（`Highlight`, `Text`, `FreeText` ...） |

回應：清除後的 PDF 二進位。

---

### 3.5 PDF 註解平面化

```bash
POST /tools/pdf-annotations-flatten/api/pdf-annotations-flatten
```

| 參數 | 類型 | 說明 |
|---|---|---|
| `file` | UploadFile | PDF |

回應：平面化後的 PDF（註解燒進頁面內容流，收件方無法移除；表單欄位仍可填）。

---

### 3.6 圖片轉 PDF

```bash
POST /tools/image-to-pdf/api/image-to-pdf
```

| 參數 | 類型 | 說明 |
|---|---|---|
| `file` | UploadFile (multi) | 一張或多張圖片（PNG/JPG/GIF/TIFF/WebP/HEIC） |
| `page_size` | str | `A4` / `A3` / `A5` / `B5` / `Letter` / `Legal` / `Tabloid` / `original` |
| `orientation` | str | `portrait` / `landscape` / `auto` |
| `margin_mm` | float | 邊距（mm） |

回應：PDF 二進位。

---

### 3.7 文字差異比對

```bash
POST /tools/text-diff/api/text-diff
```

Body (JSON)：
```json
{"a": "原文", "b": "新文", "mode": "line"}
```

`mode`：`line` / `word` / `char`。

回 JSON：diff 結構，含每段差異的類型 (`equal` / `insert` / `delete` / `replace`) 與內容。

---

### 3.8 逐句翻譯

```bash
POST /tools/translate-doc/api/translate-doc
```

Body (JSON)：

```json
{
  "text": "Hello world. This is a test.",
  "source_lang": "auto",
  "target_lang": "zh-TW"
}
```

| 欄位 | 說明 | 預設 |
|---|---|---|
| `text` | 要翻譯的文字（必填） | — |
| `source_lang` | `auto` / `en` / `zh` / `zh-TW` / `ja` / `ko` / 等 | `auto` |
| `target_lang` | 目標語言 | `zh-TW` |

回 JSON：

```json
{
  "source_lang": "en",
  "target_lang": "zh-TW",
  "results": [
    {"src": "Hello world.", "translated": "你好，世界。", "error": ""},
    {"src": "This is a test.", "translated": "這是一個測試。", "error": ""}
  ]
}
```

> 需 admin 啟用 LLM 服務（`/admin/llm-settings`）。未啟用回 `503`。

---

## 4. Job 模式 API（長時間操作）

長時間或批次操作走 job queue。流程：

1. 呼叫對應工具的提交 endpoint → 拿到 `{"job_id": "..."}`
2. 輪詢 `GET /api/jobs/{job_id}` 直到 `status == "completed"`
3. 下載 `GET /api/jobs/{job_id}/download` 取結果（單檔 PDF / 多檔 ZIP）

### 4.1 LLM 校驗（pdf-fill）

```bash
POST /api/llm-review
```

| 參數 | 類型 | 說明 |
|---|---|---|
| `file` | UploadFile | PDF（已填好欄位，準備校驗） |
| `template_id` | str | 範本 ID（admin 端記住的版型） |
| `rounds` | int (可選) | 審查輪數，預設讀 admin 設定 |

回應：`{"job_id": "..."}`。

### 4.2 查 job 狀態

```bash
GET /api/jobs/{job_id}
```

回應：

```json
{
  "job_id": "abc123...",
  "status": "running",     // pending / running / completed / failed
  "progress": 0.65,        // 0.0 ~ 1.0
  "message": "校驗第 5 / 12 欄位",
  "error": null,           // 失敗時為錯誤訊息
  "tool": "pdf-fill-llm"
}
```

### 4.3 下載 job 結果

```bash
GET /api/jobs/{job_id}/download
```

回應：結果檔（PDF / ZIP / 視 job 而定）。Job 必須 `status==completed` 才能下載；未完成回 `409`。

### 4.4 下載 job 結果為 PNG

```bash
GET /api/jobs/{job_id}/download-png
```

把 job 的 PDF 結果 render 成 PNG 回傳。多頁 / 多檔自動打成 ZIP。

---

## 5. 完整端點對照表

### 通用

| Method | 路徑 | 用途 |
|---|---|---|
| GET | `/healthz` | 健康檢查（不需 token） |
| POST | `/api/convert-to-pdf` | 文書轉 PDF |
| POST | `/api/llm-review` | 啟動 LLM 校驗（job） |
| GET | `/api/jobs/{id}` | 查 job 狀態 |
| GET | `/api/jobs/{id}/download` | 下載 job 結果 |
| GET | `/api/jobs/{id}/download-png` | 下載 job 結果為 PNG |

### 工具直連

| Method | 路徑 | 用途 |
|---|---|---|
| POST | `/tools/translate-doc/api/translate-doc` | 逐句翻譯 |
| POST | `/tools/image-to-pdf/api/image-to-pdf` | 圖片轉 PDF |
| POST | `/tools/pdf-to-image/convert` | PDF 轉圖片（PNG / JPG） |
| POST | `/tools/pdf-to-office/convert` | PDF 轉 Word / OpenDocument（v1.8.32+，回 `job_id`） |
| POST | `/tools/pdf-wordcount/api/pdf-wordcount` | PDF 字數統計 |
| POST | `/tools/pdf-annotations/api/pdf-annotations` | 註解整理（列出） |
| POST | `/tools/pdf-annotations-strip/api/pdf-annotations-strip` | 註解清除 |
| POST | `/tools/pdf-annotations-flatten/api/pdf-annotations-flatten` | 註解平面化 |
| POST | `/tools/text-diff/api/text-diff` | 文字差異比對 |
| POST | `/tools/text-list/api/text-list` | 清單處理（去重、排序、計數、集合運算） |
| POST | `/tools/text-deident/api/text-deident` | 文字去識別化（regex / LLM 偵測 PII） |
| POST | `/tools/vat-lookup/api/vat-lookup` | 統編查詢（單筆） |
| POST | `/tools/vat-lookup/api/vat-lookup/batch` | 統編查詢（批次） |
| GET | `/api/vat-lookup/{vat}` | 統編查詢（單筆，path-style） |
| POST | `/tools/einvoice-scan/api/einvoice-scan` | 電子發票 QR Code 掃描 |
| GET | `/tools/einvoice-scan/api/backend-status` | 電子發票後端 zbar 狀態 |
| GET, POST, PUT, DELETE | `/tools/submission-check/api/self-entities` | 送件前檢核 — 自家公司主檔 CRUD |

### 管理端（需 admin 登入或 admin role token）

| Method | 路徑 | 用途 |
|---|---|---|
| GET | `/admin/api/assets` | 列出所有印章 / 簽名 / Logo / 浮水印資產 |
| GET, POST | `/admin/api/llm/settings` | 讀取 / 更新 LLM 設定 |
| POST | `/admin/api/llm/test-connection` | 測試 LLM 連線 |
| GET | `/admin/api/llm/models` | 從 LLM server 抓模型清單 |
| GET | `/admin/api/sys-deps` | 系統相依套件狀態 |
| GET | `/admin/api/branding` | 企業 logo 狀態 |
| GET | `/admin/api/settings-export/summary` | 設定匯出檔案清單 |
| POST | `/admin/api/tokens/create` | 核發新 token |
| POST | `/admin/api/tokens/revoke` | 撤銷 token |
| POST | `/admin/api/tokens/enforce` | 開關 enforce 模式 |

---

## 6. 整合範例

### 6.1 GitLab CI / GitHub Actions：把 Word 文件自動轉 PDF

```yaml
# .gitlab-ci.yml
convert-docs:
  script:
    - |
      for f in docs/*.docx; do
        curl -fsSL -X POST "http://jtdt.internal:8765/api/convert-to-pdf" \
          -H "Authorization: Bearer $JTDT_TOKEN" \
          -F "file=@$f" \
          --output "build/$(basename "$f" .docx).pdf"
      done
  artifacts:
    paths: [build/]
```

### 6.2 Python 客戶端：批次清掉 PDF 註解

```python
import requests
from pathlib import Path

API = "http://localhost:8765"
TOKEN = "YOUR_64_HEX_TOKEN"
H = {"Authorization": f"Bearer {TOKEN}"}

for pdf in Path("incoming/").glob("*.pdf"):
    with pdf.open("rb") as f:
        r = requests.post(
            f"{API}/tools/pdf-annotations-strip/api/pdf-annotations-strip",
            headers=H,
            files={"file": (pdf.name, f, "application/pdf")},
        )
    r.raise_for_status()
    (Path("clean/") / pdf.name).write_bytes(r.content)
    print(f"OK {pdf.name}")
```

### 6.3 Shell：監看 job 完成後下載

```bash
#!/bin/bash
TOKEN="YOUR_TOKEN"
API="http://localhost:8765"

JOB=$(curl -fsSL -X POST "$API/api/llm-review" \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@filled.pdf" -F "template_id=vendor_form_v3" \
  | jq -r .job_id)

echo "Job: $JOB"

while :; do
  S=$(curl -fsSL "$API/api/jobs/$JOB" -H "Authorization: Bearer $TOKEN")
  STATE=$(echo "$S" | jq -r .status)
  PROG=$(echo "$S" | jq -r .progress)
  echo "  $STATE  $PROG"
  [ "$STATE" = "completed" ] && break
  [ "$STATE" = "failed" ] && { echo "Failed"; exit 1; }
  sleep 2
done

curl -fsSL "$API/api/jobs/$JOB/download" \
  -H "Authorization: Bearer $TOKEN" \
  --output reviewed.pdf
echo "Saved: reviewed.pdf"
```

### 6.4 Node.js：逐句翻譯

```js
const r = await fetch(
  'http://localhost:8765/tools/translate-doc/api/translate-doc',
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer YOUR_TOKEN',
    },
    body: JSON.stringify({
      text: 'Hello world. This is a test.',
      source_lang: 'auto',
      target_lang: 'zh-TW',
    }),
  }
);
const j = await r.json();
console.log(j.results);
// [{src: 'Hello world.', translated: '你好，世界。', error: ''}, ...]
```

---

## 7. CLI 管理 token

```bash
# 列出
sudo jtdt auth show

# 列出所有 token（admin 端 UI 也可以）
# 直接讀檔可看（unhash 不可逆）：
sudo cat /var/lib/jt-doc-tools/data/api_tokens.json

# 撤銷（用 token 字串前 8 字認）— 透過 admin UI 比較容易；CLI 沒提供撤銷，
# 必要時直接清掉檔案後重啟服務即可：
sudo systemctl stop jt-doc-tools
sudo rm /var/lib/jt-doc-tools/data/api_tokens.json
sudo systemctl start jt-doc-tools
# 重啟後 admin UI 重新核發
```

---

## 8. 速率限制 / 大檔上限

目前**沒有內建** rate limit。建議部署時用反向代理（nginx / Caddy）加：

- `client_max_body_size 100M`（必設，否則 PDF 大檔會被拒）
- `proxy_read_timeout 900s` + `proxy_send_timeout 900s`（必設 — LLM 工具單筆推理常 5-15 分鐘，預設 60s 必定 504）
- `proxy_buffering off`（LLM streaming 友善）
- **多層 nginx 情境（自架 LLM proxy + jt-doc-tools 兩台 nginx）每一層都要設**，一層用預設整鏈就斷
- 如有公開暴露需求，建議加上 `limit_req_zone` 防濫用

詳見 [OPS.md](./OPS.md) 的「反向代理」段與「504 Gateway Timeout 排錯流程」。

---

## 9. 變更歷史

API 介面遵循 SemVer：minor 版本（如 1.4.x → 1.5.x）保證**後相容**；major 版本（1.x → 2.x）才會 breaking。新加 endpoint 不算 breaking。

完整變更紀錄見 [CHANGELOG.md](./CHANGELOG.md)。

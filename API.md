# API 使用手冊

Jason Tools 文件工具箱對外提供 RESTful API，所有工具皆有對應 endpoint，可整合到自動化流程、自家系統或排程工作。

> **基本原則**：API 預設可不認證（與 web UI 同步開放）；需要鎖時管理員到 admin 「API Token」頁開啟 enforce 模式並核發 bearer token。

---

## 目錄

- [1. 認證](#1-認證)
- [2. 通用約定](#2-通用約定)
- [3. 文件轉換 API](#3-文件轉換-api)
- [4. PDF 編修 API](#4-pdf-編修-api)
- [5. PDF 擷取與分析 API](#5-pdf-擷取與分析-api)
- [6. 表單與簽章 API](#6-表單與簽章-api)
- [7. 安全與隱私 API](#7-安全與隱私-api)
- [8. 文字與比對 API](#8-文字與比對-api)
- [9. 商務查詢 API](#9-商務查詢-api)
- [10. Job 模式 API](#10-job-模式-api)
- [11. 管理端 API](#11-管理端-api)
- [12. 整合範例](#12-整合範例)
- [13. CLI 管理 token](#13-cli-管理-token)
- [14. 速率限制 / 大檔上限](#14-速率限制--大檔上限)
- [15. 變更歷史](#15-變更歷史)

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
| Base URL | `http://your-server:8765`（依 `JTDT_HOST` / `JTDT_PORT` 而定，以下範例用 `localhost:8765`） |
| Content-Type | 上傳檔案：`multipart/form-data`；JSON：`application/json` |
| 認證標頭 | `-H "Authorization: Bearer YOUR_TOKEN"`（未啟用 enforce 時可省略） |
| 回應格式 | JSON（除非明確回 PDF / PNG / ZIP 二進位資料） |
| 錯誤格式 | `{"detail": "錯誤訊息"}` + 對應 HTTP 4xx/5xx |
| 大檔處理 | 大型 / 耗時操作走 **job 模式**：先回 `{"job_id": "..."}`，再用 `/api/jobs/{job_id}` 輪詢，完成後 `/api/jobs/{job_id}/download` 取結果 |

> 以下每個端點都附 `curl` 範例。回傳檔案的端點用 `--output 檔名` 存檔；回傳 JSON 的端點可接 `| jq` 美化。

---

## 3. 文件轉換 API

### 文書轉 PDF

把 Word / Excel / PowerPoint / ODF 轉成 PDF（走 OxOffice / LibreOffice 引擎）。

```text
POST /api/convert-to-pdf
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | Word / Excel / PowerPoint / ODF 文件 |

```bash
curl -X POST http://localhost:8765/api/convert-to-pdf \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@報告.docx" \
  --output 報告.pdf
```

回應：PDF 二進位（`application/pdf`）。失敗 4xx + JSON `{"detail": "..."}`。

### 圖片轉 PDF

把一張或多張圖片合併成單一 PDF。

```text
POST /tools/image-to-pdf/api/image-to-pdf
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `files` | file（可多個） | ✓ | PNG / JPG / GIF / TIFF / WebP / HEIC |
| `page_size` | str | | `A4`（預設）/ `A3` / `A5` / `B5` / `Letter` / `Legal` / `Tabloid` / `original` |
| `margin_mm` | float | | 邊距（mm），預設 `0` |
| `rotations` | str | | 各圖旋轉角度 CSV（對應上傳順序），例 `0,90,0` |
| `filename` | str | | 輸出檔名 |

```bash
curl -X POST http://localhost:8765/tools/image-to-pdf/api/image-to-pdf \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "files=@p1.jpg" -F "files=@p2.png" \
  -F "page_size=A4" -F "margin_mm=5" \
  --output album.pdf
```

回應：PDF 二進位。

### PDF 轉圖片

把 PDF 每頁轉成 PNG（多頁自動打包 ZIP）。

```text
POST /tools/pdf-to-image/convert
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `dpi` | int | | 解析度，預設 `150` |

```bash
curl -X POST http://localhost:8765/tools/pdf-to-image/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" -F "dpi=200" \
  --output pages.zip
```

回應：單頁回 PNG，多頁回 ZIP。

### PDF 轉 Office

把 PDF 反轉成 Word（.docx）或 OpenDocument（.odt）。走 job 模式回 `job_id`。

```text
POST /tools/pdf-to-office/convert
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `output_format` | str | | `docx`（預設）/ `odt` |
| `enable_postprocess` | bool | | 是否套 jtdt-refine 後處理，預設 `false` |

```bash
curl -X POST http://localhost:8765/tools/pdf-to-office/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@form.pdf" -F "output_format=docx" \
  | jq
# → {"job_id": "..."}；之後用第 10 章的 job API 取結果
```

回應：`{"job_id": "..."}`（見第 10 章「Job 模式 API」）。

---

## 4. PDF 編修 API

### PDF 合併

把多份 PDF 依上傳順序合併為一份。

```text
POST /tools/pdf-merge/api/pdf-merge
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `files` | file（可多個） | ✓ | 兩份以上 PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-merge/api/pdf-merge \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "files=@a.pdf" -F "files=@b.pdf" -F "files=@c.pdf" \
  --output merged.pdf
```

回應：合併後的 PDF。

### PDF 分割

依頁數 / 範圍切分 PDF。

```text
POST /tools/pdf-split/api/pdf-split
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `mode` | str | ✓ | `ranges`（依範圍）/ `every`（每 N 頁切一份）/ `single`（每頁一份） |
| `ranges` | str | | mode=ranges 時的範圍，例 `1-3,5,7-9`；mode=every 時填數字 N |

```bash
curl -X POST http://localhost:8765/tools/pdf-split/api/pdf-split \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@big.pdf" -F "mode=ranges" -F "ranges=1-3,8-10" \
  --output parts.zip
```

回應：切出的 PDF（多份打包 ZIP）。

### PDF 頁面處理

刪除 / 抽取 / 重排頁面。

```text
POST /tools/pdf-pages/api/pdf-pages
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `mode` | str | ✓ | `keep`（只留）/ `delete`（刪除）/ `reorder`（重排） |
| `spec` | str | ✓ | 頁碼規格，例 `1,3,5-8` |

```bash
curl -X POST http://localhost:8765/tools/pdf-pages/api/pdf-pages \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" -F "mode=keep" -F "spec=1,3,5-8" \
  --output picked.pdf
```

回應：處理後的 PDF。

### PDF 旋轉

旋轉指定頁面。

```text
POST /tools/pdf-rotate/api/pdf-rotate
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `angle` | int | ✓ | `90` / `180` / `270` |
| `pages` | str | | 套用頁碼，例 `1,3-5`；空白 = 全部 |

```bash
curl -X POST http://localhost:8765/tools/pdf-rotate/api/pdf-rotate \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@scan.pdf" -F "angle=90" -F "pages=1-4" \
  --output rotated.pdf
```

回應：旋轉後的 PDF。

### PDF 加頁碼

在頁面指定位置加頁碼。

```text
POST /tools/pdf-pageno/api/pdf-pageno
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `position` | str | | `bottom-center`（預設）/ `bottom-right` / `bottom-left` / `top-*` |
| `fmt` | str | | 格式樣板，例 `第 {n} 頁` / `{n} / {total}` |
| `start` | int | | 起始頁碼，預設 `1` |
| `font_size` | float | | 字級，預設 `10` |
| `margin_mm` | float | | 邊距（mm） |
| `color` | str | | 文字顏色 hex，例 `#000000` |

```bash
curl -X POST http://localhost:8765/tools/pdf-pageno/api/pdf-pageno \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@report.pdf" -F "position=bottom-center" \
  -F "fmt=第 {n} / {total} 頁" -F "start=1" \
  --output numbered.pdf
```

回應：加完頁碼的 PDF。

### PDF 多頁合一（N-up）

把多頁縮排到單頁（2-up / 4-up 等）。

```text
POST /tools/pdf-nup/api/pdf-nup
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `cols` | int | ✓ | 每頁欄數 |
| `rows` | int | ✓ | 每頁列數 |
| `paper` | str | | 紙張，`A4`（預設）/ `A3` / `Letter` ... |
| `orientation` | str | | `portrait` / `landscape` |

```bash
curl -X POST http://localhost:8765/tools/pdf-nup/api/pdf-nup \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@slides.pdf" -F "cols=2" -F "rows=2" \
  -F "paper=A4" -F "orientation=landscape" \
  --output 4up.pdf
```

回應：N-up 後的 PDF。

### PDF 壓縮

縮小 PDF 檔案大小。

```text
POST /tools/pdf-compress/api/pdf-compress
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `preset` | str | | `screen`（最小）/ `ebook`（預設）/ `printer` / `prepress` |

```bash
curl -X POST http://localhost:8765/tools/pdf-compress/api/pdf-compress \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@large.pdf" -F "preset=ebook" \
  --output small.pdf
```

回應：壓縮後的 PDF。

---

## 5. PDF 擷取與分析 API

### PDF 文字擷取

抽出 PDF 內所有文字（自動處理壞 CMap / OCR 雙層）。

```text
POST /tools/pdf-extract-text/api/pdf-extract-text
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-extract-text/api/pdf-extract-text \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" | jq
```

回應 JSON：逐頁文字 + 全文。

### PDF 圖片擷取

抽出 PDF 內嵌的所有圖片。

```text
POST /tools/pdf-extract-images/api/pdf-extract-images
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-extract-images/api/pdf-extract-images \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" \
  --output images.zip
```

回應：圖片打包 ZIP。

### PDF 附件擷取

列出 / 抽出 PDF 內嵌附件（embedded files）。

```text
POST /tools/pdf-attachments/api/pdf-attachments
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-attachments/api/pdf-attachments \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@with_attachments.pdf" \
  --output attachments.zip
```

回應：附件清單 + 內容（ZIP）。

### PDF 中繼資料

讀取 / 清除 PDF metadata、XMP、書籤、註解、表單。

```text
POST /tools/pdf-metadata/api/pdf-metadata
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `clear_info` | bool | | 清除文件資訊（作者 / 標題 / 軟體等） |
| `clear_xmp` | bool | | 清除 XMP metadata |
| `clear_toc` | bool | | 清除書籤 / 目錄 |
| `clear_annots` | bool | | 清除註解 |
| `clear_forms` | bool | | 清除表單欄位 |

```bash
curl -X POST http://localhost:8765/tools/pdf-metadata/api/pdf-metadata \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" -F "clear_info=true" -F "clear_xmp=true" \
  --output cleaned.pdf
```

回應：未帶 clear_* 旗標 → 回 metadata JSON；帶旗標 → 回清除後的 PDF。

### PDF 字數統計

統計頁數、字數、詞數、閱讀時間與高頻詞。

```text
POST /tools/pdf-wordcount/api/pdf-wordcount
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-wordcount/api/pdf-wordcount \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" | jq
```

回應 JSON（節錄）：

```json
{
  "filename": "document.pdf",
  "page_count": 12,
  "char_count": 18342,
  "word_count": 3521,
  "estimated_reading_minutes": 12.5,
  "per_page_chars": [/* ... */],
  "top_words_zh2": [/* ... */], "top_words_en": [/* ... */]
}
```

### PDF OCR

對掃描 PDF 做文字辨識，加上可選取的文字層。

```text
POST /tools/pdf-ocr/api/pdf-ocr
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `lang` | str | | 語言，例 `chi_tra+eng`（預設）/ `eng` / `chi_sim` |
| `dpi` | int | | 渲染解析度，預設 `300` |
| `skip_pages_with_text` | bool | | 已有文字層的頁略過，預設 `true` |

```bash
curl -X POST http://localhost:8765/tools/pdf-ocr/api/pdf-ocr \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@scan.pdf" -F "lang=chi_tra+eng" -F "dpi=300" \
  --output ocr.pdf
```

回應：加上文字層的 PDF。

### PDF 註解整理

列出 PDF 所有註解（頁碼、類型、作者、內容、座標、時間）。

```text
POST /tools/pdf-annotations/api/pdf-annotations
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-annotations/api/pdf-annotations \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@reviewed.pdf" | jq
```

回應 JSON：每筆註解的詳細資訊。

### PDF 註解清除

移除 PDF 註解（可依類型 / 作者篩選）。

```text
POST /tools/pdf-annotations-strip/api/pdf-annotations-strip
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `types` | str | | 只清這些類型 CSV（`Highlight` / `Text` / `FreeText` ...），空白 = 全清 |
| `authors` | str | | 只清這些作者 CSV，空白 = 全清 |
| `mode` | str | | 處理模式 |

```bash
curl -X POST http://localhost:8765/tools/pdf-annotations-strip/api/pdf-annotations-strip \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@reviewed.pdf" -F "types=Highlight,Text" \
  --output clean.pdf
```

回應：清除後的 PDF。

### PDF 註解平面化

把註解燒進頁面內容流（收件方無法移除；表單欄位仍可填）。

```text
POST /tools/pdf-annotations-flatten/api/pdf-annotations-flatten
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-annotations-flatten/api/pdf-annotations-flatten \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@annotated.pdf" \
  --output flattened.pdf
```

回應：平面化後的 PDF。

### PDF 隱藏內容掃描

掃描 PDF 內可能洩漏的隱藏內容（中繼資料、被遮蓋文字、圖層、附件等）。

```text
POST /tools/pdf-hidden-scan/api/pdf-hidden-scan
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |

```bash
curl -X POST http://localhost:8765/tools/pdf-hidden-scan/api/pdf-hidden-scan \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" | jq
```

回應 JSON：各類隱藏內容的偵測結果。

---

## 6. 表單與簽章 API

### PDF 表單填寫

自動辨識表單欄位並填入公司主檔資料。

```text
POST /tools/pdf-fill/api/pdf-fill
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 待填的 PDF 表單 |
| `company_id` | str | | 公司主檔 ID（admin 端建立） |
| `font_id` | str | | 填寫字型 ID |

```bash
curl -X POST http://localhost:8765/tools/pdf-fill/api/pdf-fill \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@vendor_form.pdf" -F "company_id=acme" \
  --output filled.pdf
```

回應：填好的 PDF。

### PDF 用印 / 簽名

在 PDF 上疊加印章 / 簽名圖。

```text
POST /tools/pdf-stamp/api/pdf-stamp
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `stamp_image` | file | ✓ | 印章 / 簽名圖（透明 PNG 佳） |
| `x_mm` | float | ✓ | 左下角 X 位置（mm） |
| `y_mm` | float | ✓ | 左下角 Y 位置（mm） |
| `width_mm` | float | ✓ | 寬度（mm） |
| `height_mm` | float | ✓ | 高度（mm） |
| `rotation_deg` | float | | 旋轉角度，預設 `0` |
| `page_mode` | str | | `all`（每頁）/ `first` / `last` / 指定頁 |

```bash
curl -X POST http://localhost:8765/tools/pdf-stamp/api/pdf-stamp \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.pdf" -F "stamp_image=@chop.png" \
  -F "x_mm=150" -F "y_mm=30" -F "width_mm=30" -F "height_mm=30" \
  -F "page_mode=last" \
  --output stamped.pdf
```

回應：蓋章後的 PDF。

### PDF 浮水印

加文字浮水印。

```text
POST /tools/pdf-watermark/api/pdf-watermark
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `text` | str | ✓ | 浮水印文字 |
| `opacity` | float | | 透明度 0–1，預設 `0.15` |
| `rotation_deg` | float | | 旋轉角度，預設 `45` |
| `mode` | str | | `tile`（平鋪）/ `center`（置中） |
| `text_color` | str | | 文字顏色 hex |
| `text_size_pt` | float | | 字級（pt） |

```bash
curl -X POST http://localhost:8765/tools/pdf-watermark/api/pdf-watermark \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" -F "text=機密" \
  -F "opacity=0.12" -F "mode=tile" \
  --output watermarked.pdf
```

回應：加浮水印後的 PDF。

### PDF 編輯器

依 overlay JSON 模型把文字 / 圖片 / 形狀 / 遮罩燒進 PDF（含真刪 redaction）。

```text
POST /tools/pdf-editor/api/pdf-editor
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 原 PDF |
| `model` | str (JSON) | ✓ | overlay 物件模型 JSON 字串 |

```bash
curl -X POST http://localhost:8765/tools/pdf-editor/api/pdf-editor \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" \
  -F 'model={"version":1,"pages":[{"page":0,"objects":[{"id":"o1","type":"text","x":100,"y":200,"w":120,"h":20,"text":"已蓋章","font":"Noto Sans TC","size":14,"color":"#cc0000"}]}]}' \
  --output edited.pdf
```

回應：套用 overlay 後的 PDF。模型格式詳見 web UI 的 pdf-editor。

---

## 7. 安全與隱私 API

### PDF 加密

加使用者 / 擁有者密碼並設定權限。

```text
POST /tools/pdf-encrypt/api/pdf-encrypt
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `user_pw` | str | | 開啟密碼 |
| `owner_pw` | str | | 權限密碼 |
| `algorithm` | str | | 加密演算法，例 `AES-256` |
| `allow_print` | bool | | 允許列印 |
| `allow_copy` | bool | | 允許複製內容 |

```bash
curl -X POST http://localhost:8765/tools/pdf-encrypt/api/pdf-encrypt \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" -F "user_pw=open123" \
  -F "owner_pw=admin456" -F "algorithm=AES-256" -F "allow_print=true" \
  --output encrypted.pdf
```

回應：加密後的 PDF。

### PDF 解密

用密碼移除 PDF 加密。

```text
POST /tools/pdf-decrypt/api/pdf-decrypt
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 加密的 PDF |
| `password` | str | ✓ | 開啟密碼 |

```bash
curl -X POST http://localhost:8765/tools/pdf-decrypt/api/pdf-decrypt \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@encrypted.pdf" -F "password=open123" \
  --output decrypted.pdf
```

回應：解密後的 PDF。

### 文件去識別化

對 Word / PDF 文件偵測並遮蔽個資（regex / 可選 LLM）。

```text
POST /tools/doc-deident/api/doc-deident
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF / Word 文件 |
| `types` | str | | 要偵測的 PII 類型 CSV（身分證 / 電話 / email / 地址 ...），空白 = 預設集 |
| `mode` | str | | `mask`（遮罩，預設）/ `redact`（真刪） |

```bash
curl -X POST http://localhost:8765/tools/doc-deident/api/doc-deident \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.pdf" -F "mode=mask" \
  --output deidentified.pdf
```

回應：去識別化後的檔案。

### 文字去識別化

對純文字偵測並遮蔽個資。

```text
POST /tools/text-deident/api/text-deident
```

Body（JSON）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `text` | str | ✓ | 待處理文字 |
| `mode` | str | | `mask`（預設）/ `redact` |
| `types` | array | | 要偵測的 PII 類型 ID 陣列，省略 = 預設集 |
| `custom_regex` | str | | 自訂偵測 regex |

```bash
curl -X POST http://localhost:8765/tools/text-deident/api/text-deident \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"我的電話是 0912345678，身分證 A123456789","mode":"mask"}'
```

回應 JSON：遮蔽後文字 + 命中的 PII 清單。

---

## 8. 文字與比對 API

### 文字差異比對

比對兩段文字差異。

```text
POST /tools/text-diff/api/text-diff
```

Body（JSON）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `text_a` | str | ✓ | 舊版文字 |
| `text_b` | str | ✓ | 新版文字 |

```bash
curl -X POST http://localhost:8765/tools/text-diff/api/text-diff \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text_a":"原始內容\n第二行","text_b":"修改內容\n第二行"}'
```

回應 JSON：每段差異的類型（`equal` / `insert` / `delete` / `replace`）與內容。

### 文件差異比對

比對兩份文件（PDF / Word）內容差異。

```text
POST /tools/doc-diff/api/doc-diff
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file_a` | file | ✓ | 舊版文件 |
| `file_b` | file | ✓ | 新版文件 |

```bash
curl -X POST http://localhost:8765/tools/doc-diff/api/doc-diff \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file_a=@v1.pdf" -F "file_b=@v2.pdf" | jq
```

回應 JSON：逐段差異結構。

### 清單處理

對文字清單做去重、排序、計數、集合運算等管線處理。

```text
POST /tools/text-list/api/text-list
```

Body（JSON）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `text` | str | ✓ | 每行一筆的清單文字 |
| `ops` | array | | 處理管線，每個元素 `{"op": "..."}`；op 可為 `dedup` / `sort` / `count` / `exclude` / `lower` / `upper` / `title` 等 |

```bash
curl -X POST http://localhost:8765/tools/text-list/api/text-list \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"banana\napple\napple\ncherry","ops":[{"op":"dedup"},{"op":"sort"}]}'
```

回應 JSON：`{"lines": [...], "count": N, "original_count": M, ...}`。

### 逐句翻譯

走本地端 LLM 逐句翻譯。

```text
POST /tools/translate-doc/api/translate-doc
```

Body（JSON）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `text` | str | ✓ | 待翻譯文字 |
| `source_lang` | str | | `auto`（預設）/ `en` / `zh` / `ja` / `ko` ... |
| `target_lang` | str | | 目標語言，預設 `zh-TW` |
| `domain` | str | | 領域提示（提升專業詞彙準確度） |

```bash
curl -X POST http://localhost:8765/tools/translate-doc/api/translate-doc \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello world. This is a test.","source_lang":"auto","target_lang":"zh-TW"}'
```

回應 JSON：

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

## 9. 商務查詢 API

### 統編查詢（單筆）

依 8 位統一編號反查公司 / 機關名稱、地址、行業類別。

```text
POST /tools/vat-lookup/api/vat-lookup
```

Body（JSON）：`{"vat": "12345678"}`

```bash
curl -X POST http://localhost:8765/tools/vat-lookup/api/vat-lookup \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"vat":"12345678"}' | jq
```

回應 JSON：公司名稱 / 地址 / 行業等。找不到回 `404`。

### 統編查詢（path style）

同上，但用 GET + path 參數。

```text
GET /api/vat-lookup/{vat}
```

```bash
curl http://localhost:8765/api/vat-lookup/12345678 \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
```

回應 JSON：同單筆查詢。

### 統編查詢（批次）

一次查多筆統編。

```text
POST /tools/vat-lookup/api/vat-lookup/batch
```

Body（JSON）：`{"vats": ["12345678", "23456789"]}`

```bash
curl -X POST http://localhost:8765/tools/vat-lookup/api/vat-lookup/batch \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"vats":["12345678","23456789"]}' | jq
```

回應 JSON：每筆查詢結果陣列。

### 電子發票掃描

解析電子發票 QR Code 內容。

```text
POST /tools/einvoice-scan/api/einvoice-scan
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 含發票 QR Code 的圖片 / PDF |

```bash
curl -X POST http://localhost:8765/tools/einvoice-scan/api/einvoice-scan \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@invoice.jpg" | jq
```

回應 JSON：發票號碼、日期、金額、賣方統編等。

### 電子發票後端狀態

查 QR Code 解碼後端（zbar）是否可用。

```text
GET /tools/einvoice-scan/api/backend-status
```

```bash
curl http://localhost:8765/tools/einvoice-scan/api/backend-status \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
```

回應 JSON：`{"available": true/false, ...}`。

### 送件前檢核 — 自家公司主檔

管理送件前檢核用的自家公司實體（CRUD）。

```text
GET    /tools/submission-check/api/self-entities
POST   /tools/submission-check/api/self-entities
PUT    /tools/submission-check/api/self-entities/{entity_id}
DELETE /tools/submission-check/api/self-entities/{entity_id}
```

POST / PUT 參數：`name`、`tax_id`、`address`、`aliases`、`type`、`note`。

```bash
# 列出
curl http://localhost:8765/tools/submission-check/api/self-entities \
  -H "Authorization: Bearer YOUR_TOKEN" | jq

# 新增
curl -X POST http://localhost:8765/tools/submission-check/api/self-entities \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "name=Acme 股份有限公司" -F "tax_id=12345678" \
  -F "address=台北市..." -F "type=company"

# 更新
curl -X PUT http://localhost:8765/tools/submission-check/api/self-entities/abc123 \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "name=Acme 國際" -F "tax_id=12345678"

# 刪除
curl -X DELETE http://localhost:8765/tools/submission-check/api/self-entities/abc123 \
  -H "Authorization: Bearer YOUR_TOKEN"
```

回應 JSON：實體清單 / 操作結果。

---

## 10. Job 模式 API

長時間或批次操作走 job queue。流程：

1. 呼叫對應工具的提交 endpoint → 拿到 `{"job_id": "..."}`
2. 輪詢 `GET /api/jobs/{job_id}` 直到 `status == "completed"`
3. 下載 `GET /api/jobs/{job_id}/download` 取結果（單檔 PDF / 多檔 ZIP）

### LLM 校驗（pdf-fill）

```text
POST /api/llm-review
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF（已填好欄位，準備校驗） |
| `template_id` | str | ✓ | 範本 ID（admin 端記住的版型） |
| `rounds` | int | | 審查輪數，預設讀 admin 設定 |

```bash
curl -X POST http://localhost:8765/api/llm-review \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@filled.pdf" -F "template_id=vendor_form_v3" \
  | jq
# → {"job_id": "..."}
```

回應：`{"job_id": "..."}`。

### 查 job 狀態

```text
GET /api/jobs/{job_id}
```

```bash
curl http://localhost:8765/api/jobs/abc123 \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
```

回應：

```json
{
  "job_id": "abc123...",
  "status": "running",
  "progress": 0.65,
  "message": "校驗第 5 / 12 欄位",
  "error": null,
  "tool": "pdf-fill-llm"
}
```

`status`：`pending` / `running` / `completed` / `failed`。

### 下載 job 結果

```text
GET /api/jobs/{job_id}/download
```

```bash
curl http://localhost:8765/api/jobs/abc123/download \
  -H "Authorization: Bearer YOUR_TOKEN" \
  --output result.pdf
```

回應：結果檔（PDF / ZIP）。未完成（`status != completed`）回 `409`。

### 下載 job 結果為 PNG

```text
GET /api/jobs/{job_id}/download-png
```

```bash
curl http://localhost:8765/api/jobs/abc123/download-png \
  -H "Authorization: Bearer YOUR_TOKEN" \
  --output result.zip
```

回應：把 job 的 PDF 結果 render 成 PNG（多頁 / 多檔自動打 ZIP）。

---

## 11. 管理端 API

需 admin 登入或 admin role token。

### 列出資產

列出所有印章 / 簽名 / Logo / 浮水印資產。

```text
GET /admin/api/assets
```

```bash
curl http://localhost:8765/admin/api/assets \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq
```

### 讀取 / 更新 LLM 設定

```text
GET  /admin/api/llm/settings
POST /admin/api/llm/settings
```

```bash
# 讀取
curl http://localhost:8765/admin/api/llm/settings \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq

# 更新
curl -X POST http://localhost:8765/admin/api/llm/settings \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enabled":true,"base_url":"http://localhost:11434","model":"gemma4:26b"}'
```

### 測試 LLM 連線

```text
POST /admin/api/llm/test-connection
```

```bash
curl -X POST http://localhost:8765/admin/api/llm/test-connection \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq
```

### 抓 LLM 模型清單

```text
GET /admin/api/llm/models
```

```bash
curl http://localhost:8765/admin/api/llm/models \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq
```

### 系統相依套件狀態

```text
GET /admin/api/sys-deps
```

```bash
curl http://localhost:8765/admin/api/sys-deps \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq
```

### 企業 logo 狀態

```text
GET /admin/api/branding
```

```bash
curl http://localhost:8765/admin/api/branding \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq
```

### 設定匯出清單

```text
GET /admin/api/settings-export/summary
```

```bash
curl http://localhost:8765/admin/api/settings-export/summary \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq
```

### Token 管理

```text
POST /admin/api/tokens/create    # 核發新 token
POST /admin/api/tokens/revoke    # 撤銷 token
POST /admin/api/tokens/enforce   # 開關 enforce 模式
```

```bash
# 核發
curl -X POST http://localhost:8765/admin/api/tokens/create \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"label":"gitlab-ci"}' | jq

# 開啟 enforce
curl -X POST http://localhost:8765/admin/api/tokens/enforce \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"enforce":true}'
```

---

## 12. 整合範例

### GitLab CI / GitHub Actions：把 Word 文件自動轉 PDF

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

### Python 客戶端：批次清掉 PDF 註解

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

### Shell：監看 job 完成後下載

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

### Node.js：逐句翻譯

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

## 13. CLI 管理 token

```bash
# 列出
sudo jtdt auth show

# 直接讀檔可看（unhash 不可逆）：
sudo cat /var/lib/jt-doc-tools/data/api_tokens.json

# 撤銷（CLI 沒提供撤銷，必要時直接清掉檔案後重啟服務）：
sudo systemctl stop jt-doc-tools
sudo rm /var/lib/jt-doc-tools/data/api_tokens.json
sudo systemctl start jt-doc-tools
# 重啟後 admin UI 重新核發
```

---

## 14. 速率限制 / 大檔上限

目前**沒有內建** rate limit。建議部署時用反向代理（nginx / Caddy）加：

- `client_max_body_size 100M`（必設，否則 PDF 大檔會被拒）
- `proxy_read_timeout 900s` + `proxy_send_timeout 900s`（必設 — LLM 工具單筆推理常 5-15 分鐘，預設 60s 必定 504）
- `proxy_buffering off`（LLM streaming 友善）
- **多層 nginx 情境（自架 LLM proxy + jt-doc-tools 兩台 nginx）每一層都要設**，一層用預設整鏈就斷
- 如有公開暴露需求，建議加上 `limit_req_zone` 防濫用

詳見 [OPS.md](./OPS.md) 的「反向代理」段與「504 Gateway Timeout 排錯流程」。

---

## 15. 變更歷史

API 介面遵循 SemVer：minor 版本（如 1.4.x → 1.5.x）保證**後相容**；major 版本（1.x → 2.x）才會 breaking。新加 endpoint 不算 breaking。

完整變更紀錄見 [CHANGELOG.md](./CHANGELOG.md)。

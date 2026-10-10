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

### 辦公文件轉 PDF

把 辦公文件 轉成 PDF（走 OxOffice / LibreOffice 引擎）。

```text
POST /api/convert-to-pdf
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 辦公文件 文件 |

```bash
curl -X POST http://localhost:8765/api/convert-to-pdf \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@報告.docx" \
  --output 報告.pdf
```

回應：PDF 二進位（`application/pdf`）。失敗 4xx + JSON `{"detail": "..."}`。

### 辦公文件格式互轉

同一類文件之間互轉格式：文書檔↔文書檔、試算表↔試算表、簡報↔簡報。走 job 模式回 `job_id`。

**先問可用的目標格式再送轉換。** `target` 的值會因安裝而異（沒裝 Impress
模組就不會有簡報那一組），不要把 id 寫死在自己的程式裡：

```text
GET /tools/office-convert/formats
```

```bash
curl -s http://localhost:8765/tools/office-convert/formats \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
# → {"families": [{"id": "text", "name": "文書檔",
#                  "sources": ["odt", "docx", ...],
#                  "targets": [{"id": "docx-2007", "ext": "docx",
#                               "label": "Word 2007", "note": "...",
#                               "common": true}, ...]}, ...]}
```

```text
POST /tools/office-convert/convert
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 辦公文件，可重複帶多份（多份會打包成 ZIP） |
| `target` | str | ✓ | 目標格式 id，取自上面的 `formats` |

同一次請求裡的檔案**必須同屬一類**（不能把試算表和文書檔混在一起送），
而且要與 `target` 所屬的類一致，否則回 400。

```bash
# 文書檔：.odt 轉成 Word 97–2003
curl -X POST http://localhost:8765/tools/office-convert/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@報告.odt" -F "target=doc" | jq
# → {"job_id": "...", "download_url": "/api/jobs/.../download"}

# 簡報：.pptx 轉成 ODF 簡報
curl -X POST http://localhost:8765/tools/office-convert/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@簡報.pptx" -F "target=odp" | jq
```

`.docx` / `.xlsx` / `.pptx` 各有兩個目標可選，差別是**相容模式**不是只有名稱：

| 目標 id | 產出 | 相容模式 |
|---|---|---|
| `docx-2007` | Word 2007 寫法 | 12 |
| `docx-365` | Word 2010–365 寫法 | 15 |
| `xlsx-2007` / `xlsx-ooxml` | Excel 2007–365 / Office Open XML | — |
| `pptx-2007` / `pptx-ooxml` | PowerPoint 2007–365 / Office Open XML | — |

回應：`{"job_id": "...", "download_url": "..."}`。單檔回原格式，多檔回 ZIP。
失敗 4xx + JSON `{"detail": "..."}`。

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

把 PDF 或辦公文件每頁轉成 PNG / WebP / JPEG（多頁自動打包 ZIP）。
大小可以用 DPI，也可以直接指定寬度（每一頁縮放成同一個寬度，高度依頁面比例）。

```text
POST /tools/pdf-to-image/convert
GET  /tools/pdf-to-image/download/{upload_id}
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF 或辦公文件（非 PDF 會先轉成 PDF） |
| `format` | string | | 輸出格式：`png`（預設）/ `webp` / `jpeg`（`jpg` 也收）；其他值回 400 |
| `width` | int | | 每一頁的寬度（像素，16～10000）；有給就不看 `dpi`。超出範圍回 400，不會安靜地改成別的寬度 |
| `dpi` | int | | 沒給 `width` 時的解析度，預設 `200`，範圍 72～600 |
| `quality` | int | | WebP / JPEG 的品質 1～100，預設 `80`；PNG 無損，不看這個值 |

單頁超過 4000 萬像素（或 WebP 單邊超過 16383 像素，那是格式本身的限制）時會縮小輸出，
那一頁的 `reduced` 是 `true`，頂層的 `reduced_pages` 列出是哪幾頁。

```bash
# 1. 上傳並轉換（網頁用：WebP、寬 1920）
curl -X POST http://localhost:8765/tools/pdf-to-image/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@slides.pdf" -F "format=webp" -F "width=1920" | jq
# → {"upload_id": "...", "page_count": 3, "format": "webp", "size_mode": "width",
#    "width": 1920, "dpi": null, "reduced_pages": [], "pages": [{"width_px": 1920, ...}]}

# 依 DPI 轉 PNG
curl -X POST http://localhost:8765/tools/pdf-to-image/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@document.pdf" -F "dpi=200" | jq

# 2. 下載
curl -s http://localhost:8765/tools/pdf-to-image/download/abc123 \
  -H "Authorization: Bearer YOUR_TOKEN" --output pages.zip
```

回應：第 1 步回每一頁的資訊（`width_px` / `height_px` / `dpi` / `size_bytes` / `reduced`）與 `upload_id`；第 2 步拿圖，單頁是該格式的圖片、多頁是 ZIP（裡面每一頁的檔名像 `slides_p1.webp`）。

### PDF 轉 Office

把 PDF 反轉成 Word（.docx）或 OpenDocument（.odt）。走 job 模式回 `job_id`。

```text
POST /tools/pdf-to-office/convert
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `output_format` | str | | `docx`（預設）/ `odt` |
| `engine` | str | | 轉換引擎：`pdf2docx-refine`（預設，穩定）/ `jtdt-reform`（自家版面重組）/ `jtdt-layout`（版面重現：LibreOffice/OxOffice Draw 精準匯入 + 自家轉換引擎重組成 docx/odt，版面近 1:1 的頁面錨定文字方塊，含圖片 / 框線；本質非流動文字、無真表格） |
| `enable_postprocess` | bool | | 僅 `pdf2docx-refine` 有效：是否套 jtdt-refine 後處理（25 fixer），預設 `false` |

```bash
# 預設引擎 pdf2docx-refine
curl -X POST http://localhost:8765/tools/pdf-to-office/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@form.pdf" -F "output_format=docx" \
  | jq
# → {"job_id": "...", "download_url": "/api/jobs/.../download"}

# 改用自家 jtdt-reform 引擎
curl -X POST http://localhost:8765/tools/pdf-to-office/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@form.pdf" -F "output_format=odt" -F "engine=jtdt-reform" \
  | jq

# 改用 jtdt-layout 版面重現引擎（表單 / 多欄 / 含框線表格版面最接近原 PDF）
curl -X POST http://localhost:8765/tools/pdf-to-office/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@form.pdf" -F "output_format=odt" -F "engine=jtdt-layout" \
  | jq
```

回應：`{"job_id": "...", "download_url": "..."}`；之後用第 10 章的 job API 輪詢 + 取結果。

**取得轉換前後對照縮圖**：job 完成後，`GET /api/jobs/{job_id}` 的 `meta.preview` 內含
`page_indices`（要預覽的 0-based 頁碼清單，≤ 6 頁全取 / > 6 頁取前 2 + 中 2 + 後 2）、
`orig_pages`、`result_pages`、`orig_chars`、`result_chars`。逐頁縮圖用：

```text
GET /tools/pdf-to-office/preview/{job_id}/orig/{page}     # 轉換前（原 PDF）
GET /tools/pdf-to-office/preview/{job_id}/result/{page}   # 轉換後（docx/odt 渲染）
```

`page` 為 1-based 頁碼（對應 `page_indices` 元素 +1），回傳 `image/png`。

```bash
# 完成後讀 preview 頁碼清單
curl -s http://localhost:8765/api/jobs/$JOB \
  -H "Authorization: Bearer YOUR_TOKEN" | jq '.meta.preview'
# 取第 1 頁的前 / 後對照縮圖
curl -s http://localhost:8765/tools/pdf-to-office/preview/$JOB/orig/1 \
  -H "Authorization: Bearer YOUR_TOKEN" --output before_p1.png
curl -s http://localhost:8765/tools/pdf-to-office/preview/$JOB/result/1 \
  -H "Authorization: Bearer YOUR_TOKEN" --output after_p1.png
```

---

### PDF 轉 Markdown

把 PDF 抽成 Markdown。單次呼叫直接回內容，不走 job 模式。

```text
POST /tools/pdf-to-markdown/api/pdf-to-markdown
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `include_images` | bool | | 是否一併輸出內嵌圖片，預設 `false` |
| `page_separator` | bool | | 頁與頁之間插入分隔線，預設 `true` |
| `image_format` | str | | 圖片格式 `png`（預設）/ `jpg`，僅 `include_images=true` 時有效 |

```bash
# 只要文字：回 text/markdown
curl -X POST http://localhost:8765/tools/pdf-to-markdown/api/pdf-to-markdown \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@report.pdf" --output report.md

# 連圖片一起：回 ZIP（.md + 圖片檔）
curl -X POST http://localhost:8765/tools/pdf-to-markdown/api/pdf-to-markdown \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@report.pdf" -F "include_images=true" --output report.zip
```

**回應型別依 `include_images` 而不同**：`false` 回 `text/markdown`，`true` 回 `application/zip`。

---

### Markdown 轉辦公文件

把 Markdown 轉成 PDF / Word / OpenDocument。內容可用 `file` 上傳，也可以直接用 `text` 帶進來。

```text
POST /tools/markdown-to-doc/api/markdown-to-doc
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | △ | Markdown 檔。與 `text` 擇一 |
| `text` | str | △ | 直接帶 Markdown 內容。與 `file` 擇一 |
| `format` | str | | `pdf`（預設）/ `docx` / `odt`。其他值回 400 |
| `theme` | str | | 版面主題，預設 `classic`。可用的值：`classic` / `github` / `academic` / `book` / `report` / `mono` / `teal` / `indigo` / `forest` / `coral` / `navy-gold` / `magazine`。不認得的值改用 `classic` |
| `font` | str | | 字型，預設 `default` |
| `title` | str | | 文件標題 |

```bash
# 檔案轉 PDF
curl -X POST http://localhost:8765/tools/markdown-to-doc/api/markdown-to-doc \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@notes.md" -F "format=pdf" --output notes.pdf

# 直接帶內容轉 Word
curl -X POST http://localhost:8765/tools/markdown-to-doc/api/markdown-to-doc \
  -H "Authorization: Bearer YOUR_TOKEN" \
  --form-string "text=# 標題

內文。" -F "format=docx" --output notes.docx
```

直接回檔案本身（不是 JSON）。`format` 給了 `pdf` / `docx` / `odt` 以外的值回 `400`。

---

## 4. PDF 編修 API


### PDF 轉簡報

把 PDF 反轉成 PowerPoint（.pptx）或 OpenDocument 簡報（.odp），**一頁對一張投影片**，
投影片尺寸沿用原稿（直向 PDF 也照樣還原）。走 job 模式回 `job_id`。

只有一顆引擎（jtdt-layout 版面重現），因此**沒有** `engine` 參數。

```text
POST /tools/pdf-to-slides/convert
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF |
| `output_format` | str | | `pptx`（預設）/ `odp` |

```bash
curl -X POST http://localhost:8765/tools/pdf-to-slides/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@deck.pdf" -F "output_format=pptx" \
  | jq
# → {"job_id": "...", "download_url": "/api/jobs/.../download"}

# 輸出 OpenDocument 簡報（物件多時比 .pptx 快）
curl -X POST http://localhost:8765/tools/pdf-to-slides/convert \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@deck.pdf" -F "output_format=odp" \
  | jq
```

前後對照縮圖與 pdf-to-office 相同：

```text
GET /tools/pdf-to-slides/preview/{job_id}/orig/{page}     # 轉換前（原 PDF）
GET /tools/pdf-to-slides/preview/{job_id}/result/{page}   # 轉換後（簡報渲染）
```

job 完成後 `GET /api/jobs/{job_id}` 的 `meta.stats` 內含 `pages`（投影片張數）、
`images`、`objects`（物件總數）。

> **環境需求**：需要 office 套件的 **Impress 模組**（`oxoffice-impress` /
> `libreoffice-impress`）。缺模組時只會看到「轉檔成功但找不到輸出」，
> 可在「設定 → 相依套件檢查」確認。

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
| `fmt` | str | | 格式樣板：`{n}` 是目前頁碼、`{N}` 是總頁數（`{total}` 同 `{N}`），例 `第 {n} 頁` / `{n} / {N}` |
| `start` | int | | 起始頁碼，預設 `1` |
| `font_size` | float | | 字級，預設 `10` |
| `margin_mm` | float | | 邊距（mm） |
| `color` | str | | 文字顏色 hex，例 `#000000` |

```bash
curl -X POST http://localhost:8765/tools/pdf-pageno/api/pdf-pageno \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@report.pdf" -F "position=bottom-center" \
  -F "fmt=第 {n} / {N} 頁" -F "start=1" \
  --output numbered.pdf
```

回應：加完頁碼的 PDF。

### PDF 頁面加框

替每一頁加上框線。收 PDF 與文書檔（辦公文件；文書檔會先自動轉成 PDF），輸出一律是 PDF。

```text
POST /tools/pdf-border/api/pdf-border
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF 或文書檔 |
| `mode` | str | | `page`（自頁緣內縮，預設）/ `content`（貼齊該頁內容） |
| `margin_mm` | float | | 邊距（mm），預設 `5` |
| `width_pt` | float | | 線寬（pt），預設 `1.5` |
| `color` | str | | 框線顏色 hex，預設 `#333333` |
| `style` | str | | `solid`（預設）/ `dashed` / `dotted` |
| `radius_mm` | float | | 圓角半徑（mm），預設 `0`（直角） |
| `opacity` | float | | 不透明度 `0`~`1`，預設 `1` |
| `double` | bool | | 內外雙框（獎狀 / 證書風格），預設 `false` |
| `double_gap_mm` | float | | 雙框兩線間距（mm），預設 `1.5` |
| `shadow` | bool | | 外側陰影，預設 `false` |
| `shadow_color` | str | | 陰影顏色 hex，預設 `#000000` |
| `shadow_blur_mm` | float | | 陰影擴散（mm），預設 `1.2` |
| `shadow_offset_mm` | float | | 陰影位移（mm），預設 `0.6` |
| `shadow_opacity` | float | | 陰影濃度 `0`~`1`，預設 `0.25` |
| `pages` | str | | 指定頁面，例 `1,3,5-8`；留空 = 全部 |
| `skip_first` | bool | | 首頁不加框（投影片封面常是滿版設計），預設 `false` |

```bash
curl -X POST http://localhost:8765/tools/pdf-border/api/pdf-border \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@slides.pptx" -F "mode=page" -F "margin_mm=4" \
  -F "width_pt=2" -F "color=#1e293b" -F "skip_first=true" \
  --output slides_border.pdf
```

回應：加完框線的 PDF。所有數值都會在伺服器端夾在安全範圍內。

### PDF 書籤與目錄

替 PDF 加書籤（閱讀器左側的導覽）與可點的目錄頁。**傳多個檔案會自動串接，並以檔名建立第一層書籤**，子文件原有的書籤降一層保留。收 PDF 與文書檔（文書檔會先自動轉成 PDF）。

```text
POST /tools/pdf-bookmark/api/pdf-bookmark
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `files` | file[] | ✓ | 一或多個 PDF / 文書檔。多檔時依順序串接 |
| `bookmarks` | str | | 自己指定書籤（JSON 陣列，每筆 `{"title","page","level"}`）。給了就取代自動產生的 |
| `auto` | bool | | 依字級自動偵測標題，預設 `false`（只在沒有 `bookmarks` 且單檔時有作用） |
| `toc_page` | bool | | 插入目錄頁，預設 `false`。**網頁介面的預設是勾起來的**，API 維持 `false` 是為了不讓既有的自動化呼叫突然多出一頁 |
| `toc_at` | int | | 目錄插在第幾頁之前，預設 `1`（最前面）。**有封面就填 `2`** |
| `toc_title` | str | | 目錄頁標題，預設 `目錄` |
| `toc_max_level` | int | | 目錄列到第幾層（1~3），預設 `3` |

書籤的**層級必須從 1 開始且一次只能加一層**（1→3 會被自動修成 1→2），**頁碼超出總頁數會被夾到最後一頁** —— 兩者都會自動修正，不會失敗。插入目錄頁之後頁碼會自動往後移，不需要自己算 —— **`toc_at` 之前的書籤不會被動到**（指向封面的那一筆仍然是第 1 頁）。

```bash
# 多檔串接 + 以檔名建書籤 + 產生目錄頁
curl -X POST http://localhost:8765/tools/pdf-bookmark/api/pdf-bookmark \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "files=@投標須知.pdf" -F "files=@規格書.pdf" -F "files=@價格標.pdf" \
  -F "toc_page=true" \
  --output 標案文件.pdf

# 單檔 + 自己指定書籤
curl -X POST http://localhost:8765/tools/pdf-bookmark/api/pdf-bookmark \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "files=@report.pdf" \
  -F 'bookmarks=[{"title":"第一章","page":1,"level":1},{"title":"1.1 背景","page":3,"level":2}]' \
  --output report_bm.pdf
```

回應：加好書籤的 PDF。

### PDF 騎縫章

一個印章切成數片蓋在連續頁面上 —— 任何一頁被抽換或掉頁，那一片就對不起來。收 PDF 與文書檔。

```text
POST /tools/pdf-seam-stamp/api/pdf-seam-stamp
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF 或文書檔，**至少 2 頁** |
| `stamp` | file | | 自己的印章圖（PNG / JPG，接近純白的底會自動去掉）。不給就由系統產生 |
| `text` | str | | 系統產生時的章面文字，預設 `騎縫章`（4 字以內排成田字） |
| `shape` | str | | `circle`（預設）/ `square` / `rect` |
| `color` | str | | 印章顏色 hex，預設 `#c81414` |
| `mode` | str | | `side`（側邊騎縫，預設）/ `spread`（對開跨頁） |
| `group` | int | | 一個章跨幾頁，預設 `2`；`0` = 整份文件一個章 |
| `edge` | str | | side 模式貼哪一邊：`right`（預設）/ `left` |
| `size_mm` | float | | 章的大小（mm），預設 `40` |
| `offset_mm` | float | | 離頁緣（mm），預設 `3` |
| `pos_mm` | float | | 上下位置（mm），`0` = 垂直置中 |
| `angle_deg` | float | | 角度，預設 `0` |
| `opacity` | float | | 濃度 `0`~`1`，預設 `1` |
| `jitter_pos` | bool | | 每組高度亂數，預設 `false` |
| `jitter_angle` | bool | | 每組角度亂數（上限 ±8 度），預設 `false` |
| `seed` | int | | 亂數種子；`0` = 隨機 |

**角度是整個章先轉好才切片** —— 反過來（先切再各自旋轉）接縫會對不起來。**同一組內的片位置與角度完全一致**，否則拼不回去。

```bash
curl -X POST http://localhost:8765/tools/pdf-seam-stamp/api/pdf-seam-stamp \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.pdf" -F "text=節省" -F "group=3" \
  -F "mode=side" -F "size_mm=45" -F "jitter_angle=true" \
  --output contract_seam.pdf
```

回應：蓋好騎縫章的 PDF。

### 掃描修正

把拍歪、掃歪的文件裁掉黑邊、拉正、去除不勻的底色。手機翻拍時會抓出紙張的
四個角做透視校正。**完全不用 AI 也不用 GPU**（傳統影像處理，CPU 約 0.8 秒/頁
@200 dpi）。

```text
POST /tools/doc-straighten/api/doc-straighten
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF、圖片（含手機拍的 HEIC）或文書檔 |
| `dpi` | int | | 處理解析度，150 / 200（預設）/ 300 |
| `binarize` | bool | | 轉成黑白。**預設關閉** —— 實測會讓中文的細筆畫消失、文字辨識率明顯下降；它的用途是縮小檔案 |
| `detect_quad` | bool | | 偵測紙張邊界做透視校正（預設開）。抓不到時自動退回只做拉正 |
| `enhance` | bool | | 清晰化：壓平不勻的底色與陰影（**預設開**）。實測手機翻拍的單邊硬陰影，文字辨識率 0.472 → 0.982；已經很平的掃描件開著也是零變動 |

```bash
curl -X POST http://localhost:8765/tools/doc-straighten/api/doc-straighten \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@scan.pdf" -F "dpi=200" \
  -o straightened.pdf -D -
# 回應標頭：
#   X-Straighten-Pages: 12
#   X-Straighten-Worst-Residual: 0.10      ← 修正後殘留的歪斜角（越接近 0 越好）
```

**`X-Straighten-Worst-Residual` 是驗收指標**：轉錯方向時「角度」看起來有變化，
只有殘留角會現形。正常應該在 0.2° 以內。

網頁介面走背景作業：`POST /tools/doc-straighten/load` 上傳 →
`POST /tools/doc-straighten/submit` 送出（回 `job_id`）→
`GET /api/jobs/{job_id}` 輪詢 → `GET /api/jobs/{job_id}/download` 取結果。
逐頁預覽是 `POST /tools/doc-straighten/preview`（回修正角度與殘留角）。

### PDF 頁面尺寸統一

把混合尺寸的頁面統一成同一種紙張。內容維持向量（文字仍選得到），不是轉成圖片。

```text
POST /tools/pdf-page-size/api/pdf-page-size
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | PDF 或文書檔 |
| `paper` | str | | `a3` / `a4`（預設）/ `a5` / `b4` / `b5` / `letter` / `legal` / `tabloid` / `custom` |
| `custom_w_mm` `custom_h_mm` | float | | `paper=custom` 時的寬高（mm） |
| `orientation` | str | | `auto`（跟著原頁，預設）/ `portrait` / `landscape` |
| `fit` | str | | `scale`（縮放留白，預設，不會掉內容）/ `center`（置中不縮放，超出會裁掉）/ `crop`（放大填滿） |
| `align` | str | | `center`（預設）/ `top-left` |
| `keep_same` | bool | | 原本就是目標尺寸的頁面不動，預設 `true` |

```bash
curl -X POST http://localhost:8765/tools/pdf-page-size/api/pdf-page-size \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@tender.pdf" -F "paper=a4" -F "orientation=auto" -F "fit=scale" \
  --output tender_a4.pdf
```

回應：統一尺寸後的 PDF。

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
| `preset` | str | | `gentle`（輕度）/ `balanced`（預設，平衡）/ `aggressive`（最小） |

```bash
curl -X POST http://localhost:8765/tools/pdf-compress/api/pdf-compress \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@large.pdf" -F "preset=balanced" \
  --output small.pdf
```

回應：壓縮後的 PDF。

### 掃描拼合

把多張掃描（如證件正、反面）中「有內容的區塊」自動偵測出來、保留原彩色，依其在原掃描中的相對位置合成到同一張 A4 白底 PDF。重疊時保留原位置不自動重排（需拖曳微調請改用網頁介面）。

```text
POST /tools/scan-merge/api/scan-merge
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `files` | file（可多個） | ✓ | 掃描檔，PDF / PNG / JPG / TIFF / WebP，各含一塊內容 |
| `whiten` | bool | | 是否把淡灰 / 微黃的掃描底色提亮成純白（不影響彩色內容），預設 `true` |
| `filename` | str | | 輸出檔名，預設 `scan-merge.pdf` |

```bash
curl -X POST http://localhost:8765/tools/scan-merge/api/scan-merge \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "files=@id-front.jpg" -F "files=@id-back.jpg" \
  -F "whiten=true" \
  --output id-merged.pdf
```

回應：單張 A4 白底 PDF 二進位。

**每一份都偵測不到內容區塊時回 `422`**（例如整張全白、或掃描時蓋子沒關的純黑）。
那不是請求格式的問題，而是「送進來的圖裡沒有東西可以拼」—— 訊息會說清楚是哪一種。

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
| `file` | file | ✓ | PDF、辦公文件（.doc / .docx / .odt / .xls / .xlsx / .ods / .ppt / .pptx / .odp）或純文字（.txt / .md / .csv / .log / .json / .xml / .html） |

辦公文件會**先轉成 PDF 再統計** —— 頁數與每頁字數才是「真的印出來會長那樣」的
數字。因此這支端點在收辦公文件時需要 Office 引擎（OxOffice / LibreOffice）。

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

### 會議錄音轉逐字稿

把會議錄音或錄影轉成**帶時間與發言者**的逐字稿。辨識在外部語音服務（JTLW）
那側跑 —— **要先在管理區「語音服務（JTLW）」設定好**，沒設定回 **503**。

```text
POST /tools/meeting-transcribe/api/meeting-transcribe
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 音訊 `.m4a` / `.mp3` / `.wav` / `.aac` / `.ogg` / `.opus` / `.flac`，影片 `.mp4` / `.mov` / `.mkv` / `.webm` |
| `language` | string | | `auto`（預設）或 BCP-47（`zh-Hant` / `en` / `ja` / `ko` …）。對方不支援時**送件當下**就回 400。`zh` / `zh-TW` 會換成 `zh-Hant` 再送出 |
| `num_speakers` | string | | 預設 `0` ＝ 讓它自己判。**建議就留 0** |
| `terms` | string | | **專有名詞或會議背景**（選填）：與會者姓名、公司與產品名稱、術語，一行一個，或用頓號 / 逗號 / 分號分隔；已知的聽錯寫法寫成「聽錯的寫法 → 正確寫法」一行；也可以寫幾句會議背景 |

**專有名詞**（v1.16.39 起）送給語音服務當專有名詞表，**用在校正**：寫對的照原樣保留，拼法相近的誤聽改成你寫的寫法（語音服務介面版本 2.8 起，只修標點的校正也會改）。一行寫一個最好（`Proxmox`、`PVE`、`Proxmox VE` 各一行）；聽成拼法差很多的寫法時校正不一定改得回來。**辨識時不參考這份清單**：語音服務實測過，清單上的詞沒出現在那場會議時，辨識會把它憑空插進逐字稿，所以不採用。重複的（不分大小寫）只留第一個、順序照你給的。最多 500 個，超過回 **400**，**不會安靜截掉**（被截掉的詞不會被用到，而呼叫端會以為送出去了）。

**已知的聽錯寫法**（v1.16.49 起）：已經知道會聽錯成什麼的，寫成一行「聽錯的寫法 → 正確寫法」（箭頭也可以寫 `->` 或 `=>`；好幾個聽錯的寫法用頓號 / 逗號分隔），例如 `Proksmox、Proxmux → Proxmox`。語音服務在校正之前照表換掉：不經過語言模型、任何校正等級都做，原始辨識層不變；拼法差很多、校正認不出來的誤聽靠這個才改得回來。英文要整個詞相符才換、不分大小寫，中文照字面 —— 中文的聽錯寫法太短的話可能換到別的詞裡面，請寫具體一點。正確寫法也會當成專有名詞送出。規則照語音服務：每個詞最多 20 個聽錯的寫法、每個 2～200 字；箭頭右邊只能寫一個詞（用頓號、逗號、分號、`|`、`／` 或一邊有空白的 `/` 隔開的算好幾個，`TCP/IP` 是一個詞）；聽錯的寫法不可以是清單上的另一個詞（照表換會把寫對的換掉；清單上寫成 `Proxmox VE / PVE` 的，`Proxmox VE` 與 `PVE` 各算一個詞）；同一個聽錯的寫法不可以對到兩個詞。違反時回 **400** 並講出是哪一行，不會送出去。一行有兩個以上箭頭、`#` 開頭或寫成句子的，照舊當會議背景。語音服務介面版本 2.9 起才收；舊版（或問不到版本）時不送，回應的 `variants_sent` 是 `false`。

寫成句子的行**不當成專有名詞送出**（v1.16.44 起）：有句號、驚嘆號、問號，或拆開之後有一段超過 40 字 / 10 個漢字的那一行，整行當會議背景 —— 一整句當成專有名詞，校正時照原樣保留、拿去比對拼法都沒有意義。「與會者：王小明、Bianca」這種行，冒號前面的標籤不送、後面照一串詞送；只有標籤的那一行（「與會人員如下：」）不送；`#` 開頭的標題行、一個字母都沒有的日期 / 時間 / 純數字也不送（v1.16.48）。整段原文放在回應的 `context`，轉送會議摘要時帶進會議背景。

**發言者分離用哪一種方法，看語音服務的版本**（v1.16.31 起）：語音服務的介面版本是 2.5 以上時，本系統會要求用 NVIDIA Nemotron 分辨發言者；2.4 以前（或問不到版本）時用原本的方法。回應的 `speaker_engine` 寫出這一件要求的是哪一種。語音服務在同一批真實辨識結果上量過（不指定人數）：中文 20 場「發言者搞錯」18.52% → 2.92%、英文 16 場 12.31% → 4.65%。

**同一個 `num_speakers` 在兩種方法下的意思相反**：

| | Nemotron（`speaker_engine` = `auto`） | 原本的方法（`legacy`） |
|---|---|---|
| 填的數字 | **只當上限**：分出來的人比它多才合併，比它少不動 | **硬分成那麼多群** |
| 填得比實際多 | 沒有影響 | 把主要發言者拆開 |
| 填得比實際少 | 把不同的人併成同一位 | 把不同的人併成同一位 |
| 建議 | 不確定就留 0；**寧可多填、不要少填** | 不確定就留 0；只講一兩句的人不要算進去 |

Nemotron 下指定正確人數與不指定的差距只有 0.01 個百分點（20 場、95% 信賴區間 [−0.03, 0.00]），**只能說「不輸」，不要為了「更準」去填它**。

新方法最多分出 8 位發言者。指定超過 8 位時，語音服務一定改用原本的方法；沒有指定時，只有新方法 8 個位置都用滿、而且原本的方法分出超過 8 位才會改用，否則照用新方法、最多分出 8 位。改用時原因寫在回應的 `diarization.reason`（代碼）與 `diarization.note`（說明）。確定超過 8 位的會議請指定人數。

**`auto` 只看錄音開頭約 30 秒的講話，決定整場用哪一種語言** —— 不是逐段判斷。
開頭若有人先講另一種語言（20 秒就夠），整場都可能辨識錯；開頭的靜音、雜音不影響。
已知整場的語言時請直接指定。
中英**整句**交錯的會議請指定佔多數的語言：另一種語言的整句大多無法正確辨識
（中文句子裡夾英文術語沒有問題）。

**這支是同步的** —— 37 分鐘的會議實測 7~8 分鐘（辨識 ＋ 發言者分離 ＋ 校正），
呼叫端的逾時要放寬。要背景處理請走網頁那條路
（`POST /upload` → `POST /start` → 拿作業編號輪詢 `/api/jobs/{id}`）。

| 狀態碼 | 意思 | 要做什麼 |
|---|---|---|
| **400** | 檔案格式不收、檔案是空的、專有名詞太多、聽錯的寫法寫得不對，或語音服務退回你帶的參數（`language` / `num_speakers` / 專有名詞） | 改參數；語言不確定就用 `auto` |
| **502** | 語音服務收下了但處理失敗（辨識失敗、結果是空的、它自己出錯） | 訊息裡有原因；一直發生請管理員查語音服務 |
| **503** | 還沒設定語音服務，或連不上 | 請管理員檢查「語音服務（JTLW）」設定與連線 |
| **504** | 等太久還沒結果（排隊超過 4 小時，或辨識超過上限） | 已經請對方取消；稍後再送 |

```bash
curl -X POST http://localhost:8765/tools/meeting-transcribe/api/meeting-transcribe \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@meeting.m4a" \
  -F "language=auto" \
  -F "num_speakers=0" \
  -F "terms=王小明、Bianca、Proxmox VE" | jq
```

回應 JSON（節錄）：

```json
{
  "source": {"filename": "meeting.m4a", "size_bytes": 17823213},
  "remote_job_id": "job_01M34PVW581P2QD3Q0YFT5Y903",
  "status": "succeeded",
  "uncorrected": false,
  "diarize_skipped": false,
  "speaker_engine": "auto",
  "diarization": {"requested": "auto", "engine": "nemotron", "note": null, "reason": null},
  "diarize_fallback": null,
  "diarize_saturated": false,
  "terms": ["王小明", "Bianca", "Proxmox VE"],
  "glossary": {"entries": 3, "keep_terms": 3, "asr_bias_terms": 0, "variants": 0},
  "variants": {},
  "variants_sent": false,
  "asr": {"model": "large-v3-turbo", "location": "gpu_server", "device": "cuda"},
  "context": "第四季備份規劃會議。\n王小明\nBianca\nProxmox VE",
  "layers": {"raw": 1045, "final": 1045, "speakers": 1045},
  "tasks": ["transcribe", "diarize", "correct"],
  "summary": {"correction_level": "punctuation_only",
              "correction": {"edited": 612, "variant_replacements": 0}},
  "upload_id": "3f9c0a1e5b7d4c2a9e8f6b1d0c3a5e7f",
  "retry_until": 1790000000.0,
  "segments": [
    {"seq": 1, "text": "各位早，我們開始。", "speaker": "S1",
     "start_ms": 1450, "end_ms": 2650}
  ]
}
```

**`segments` 是三層併起來的**：文字用校正後的、時間來自原始辨識層、
發言者來自 speakers 層，三層以 `seq` 對應。對不上的段落**不會硬湊** ——
寧可那一段沒有發言者，也不要把別人的名字貼上去。

`uncorrected` 為 `true` 代表**校正那一步失敗了**，你拿到的是原始辨識結果
（標點與錯字沒有修過）—— 這時候別把它當成校正過的內容去比對。

`diarize_skipped` 為 `true` 代表**這次用的辨識模式不做發言者分離**（例如台語模式）。
這時 `segments` 沒有 `speaker` 欄位，也不會把 `num_speakers` 送給語音服務。
辨識模式能做哪些處理以語音服務提供的清單為準，送件前會先查。

回應裡的 `speaker_engine` 是這一件**要求的**分離方法（`auto` 是 Nemotron、`legacy` 是原本的方法，沒做分離時是 `null`），`diarization` 是語音服務回報**實際用了哪一種**（介面版本 2.5 起才有，舊版是 `null`）。要求了 `auto` 而 `engine` 是 `legacy` 時，`note` 寫著原因（例如指定超過 8 人）。

回應裡的 `diarize_fallback` 只在要求了 Nemotron 卻改用原本的方法時才有，其他情況是 `null`：`reason` 是語音服務給的代碼（介面版本 2.6 起），`text` 是本系統依代碼挑的說明句子；代碼不認得或語音服務沒有給代碼時，`text` 是通用句子，`note` 附上語音服務的原文說明。

回應裡的 `diarize_saturated` 為 `true` 代表**新方法（Nemotron）的 8 個位置都用滿了**：實際發言者可能更多，有些人會被併進別人名下。確定超過 8 位時請帶 `num_speakers` 重送（語音服務介面版本 2.7 起才有這個訊號，舊版一律 `false`）。

回應裡的 `terms` 是這一件送出去的專有名詞（整理過的清單），`glossary` 是語音服務回報的用法：`entries` 收到幾個、`keep_terms` 幾個照原樣保持、`asr_bias_terms` 辨識時參考了幾個（平常是 0：辨識時不參考；只有語音服務的 GPU 伺服器不能用、改在服務本機辨識時才會大於 0）。沒送專有名詞時 `terms` 是空陣列、`glossary` 是 `null`。`context` 是 `terms` 那一欄的原文（含沒當成專有名詞的句子），沒填時是空字串。回應裡的 `asr` 是語音服務回報的辨識模型（`model`）、在哪裡跑（`location`：`gpu_server` 或 `api_host`）與裝置（`device`），語音服務介面版本 2.8 起才有，舊版或辨識失敗時是 `null`。

回應裡的 `variants` 是這一件寫的聽錯寫法（`{"Proxmox": ["Proksmox", "Proxmux"]}`，沒寫時是空物件），`variants_sent` 是有沒有真的送給語音服務（介面版本 2.9 起才收；`false` 時逐字稿沒有照表換）。換了幾處在 `summary.correction.variant_replacements`，語音服務收下幾個聽錯的寫法在 `glossary.variants`（這兩個也是 2.9 起才有）。

`speaker` 是代號（`S1` / `S2`…）。姓名對照是呼叫端自己的事；
網頁那條路可以點代號直接改成人名。

#### 補專有名詞、只重跑校正

轉完才發現人名或術語寫錯時，可以補專有名詞、**只重跑校正**（v1.16.41 起）：不重新辨識，`seq`、時間與發言者都不變，只有文字換成新的校正結果。

存好逐字稿之後，語音服務那邊會**再保留最多 24 小時**給你重跑（管理員可以在「語音服務（JTLW）」設定頁改短，0 ＝ 存好就請它刪除、不能重跑），之後本系統自動請它刪除。回應的 `retry_until` 是最晚還能重跑的時間（UNIX 秒；不能重跑時是 `null`），`upload_id` 是下面兩支要帶的編號。

```text
POST /tools/meeting-transcribe/retry
```

| 參數（JSON） | 類型 | 必填 | 說明 |
|---|---|---|---|
| `upload_id` | string | ✓ | 同步 API 回應裡的 `upload_id` |
| `terms` | string 或 string[] | ✓ | 專有名詞（**整份**，不是只有新增的，聽錯的寫法也要一起帶 —— 語音服務會用這一份取代原本的清單）；規則同送件時 |

回 `{"job_id": "…"}`，用 `/api/jobs/{id}` 輪詢；做完後 `GET /tools/meeting-transcribe/result/{upload_id}` 拿新的逐字稿（`retry.count` 是重跑過幾次）。

重跑只動校正，不重新辨識。聽成拼法差很多的寫法、校正認不出來的，寫成「聽錯的寫法 → 正確寫法」一行一起帶（語音服務介面版本 2.9 起照表換）。

| 狀態碼 | 意思 |
|---|---|
| **400** | 沒帶專有名詞、專有名詞太多，或聽錯的寫法寫得不對 |
| **409** | 不能重跑：已經請語音服務刪除（超過保留時間或已經按過「不用再改了」）、這次的辨識模式沒有校正、離刪除不到 15 分鐘，或正在重跑 —— 訊息會講是哪一種 |
| **503** | 還沒設定語音服務 |

```bash
curl -X POST http://localhost:8765/tools/meeting-transcribe/retry \
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"upload_id": "UPLOAD_ID", "terms": ["王小明", "Bianca", "Proxmox VE", "Proksmox → Proxmox"]}'
```

不需要再重跑時，請語音服務**現在就刪除**它那份逐字稿（本系統這邊的逐字稿不受影響）：

```text
POST /tools/meeting-transcribe/done
```

```bash
curl -X POST http://localhost:8765/tools/meeting-transcribe/done \
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"upload_id": "UPLOAD_ID"}'
```

回 `{"ok": true, "acked": true}`。`acked` 是 `false` 代表暫時連不上語音服務，本系統稍後會自動再通知一次。

---

### 會議摘要

把會議逐字稿整理成摘要、決議、待辦、風險與章節，**每一條都附段號**。

```text
POST /tools/meeting-summary/api/meeting-summary
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 逐字稿：`.vtt` / `.srt` / `.json` / `.txt` / `.md` / `.docx` / `.odt` |
| `second_pass` | string | | `1`（預設）跑第二輪複審；`0` 只跑第一輪，快但漏抓與誤抓都會多 |
| `context` | string | | **會議背景**（選填，上限 4000 字）：主題、與會者與職稱、專有名詞說明，或你自己要交代的話 |
| `replacements` | string | | **分析前先換掉的寫法**（選填）：JSON 陣列 `[{"from": "Bianka", "to": "Bianca"}]`，通常是從下面 `/api/term-suggestions` 的建議裡挑的。不是 JSON 陣列回 **400** |

**`context` 只拿來讀懂逐字稿，不會變成項目的來源。**
知道「某某是營運副總、會議主席」對判斷誰在交辦、誰是負責人很有幫助，
但背景裡寫的事情**沒有在會議上發生過** —— 決議 / 待辦 / 風險 / 未決問題
一律只從逐字稿產生。除了提示裡明講之外，還有兩道機制擋著：
引用驗證（項目必須指得回逐字稿的段落，背景不在被比對的範圍裡），
以及「跟背景很像、而且明顯比跟逐字稿更像」就丟掉。
被丟掉的會計進 `dropped_count`。

**這支是同步的** —— 一場兩小時的會議要跑幾分鐘（約 30~60 次模型請求），
呼叫端的逾時要放寬。要背景處理請走網頁那條路
（`POST /upload` → `POST /start` → 拿作業編號輪詢 `/api/jobs/{id}`）。

需要先在管理區啟用 LLM；沒啟用回 **503**。逐字稿讀不出東西回 **400**
（訊息會說得出支援哪些格式）。

**時間戳記是選用的**：純文字逐字稿沒有時間時，摘要 / 決議 / 待辦照常有，
但 `speaker_stats` 會是空的、`charts` 不會包含 `speaker_share` 與 `timeline`
—— 那兩個要靠時間算，猜出來的數字不能用。

```bash
curl -X POST http://localhost:8765/tools/meeting-summary/api/meeting-summary \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@meeting.vtt" \
  --form-string 'context=會議主題：第四季預算
與會者
王小明：財務部經理，會議主席
李美華：法務專員' | jq
```

回應 JSON（節錄）：

```json
{
  "summary": { "text": "這場會議確認了第四季的預算…",
               "grounded": true, "unsupported": [] },
  "items": {
    "decisions": [
      { "text": "第四季預算維持原案", "segment_ids": [18, 19] }
    ],
    "actions": [
      { "text": "月底前把修訂版寄給法務", "owner": "李美華",
        "due_text": "月底", "segment_ids": [42] }
    ],
    "risks": [], "questions": [], "impacts": []
  },
  "chapters": [
    { "title": "預算討論", "start_seq": 1, "end_seq": 60,
      "start_ms": 0, "end_ms": 840000, "duration_ms": 840000,
      "segment_ids": [1, 2, 3], "percentage": 38.5 }
  ],
  "mindmap": [
    { "node_id": "c1", "parent_id": null, "label": "預算討論",
      "type": "topic", "segment_ids": [1] }
  ],
  "charts": ["timeline", "topic_share", "speaker_share", "mindmap"],
  "speaker_stats": {
    "王小明": { "speaking_ms": 512000, "percentage": 61.2,
                "turn_count": 24, "average_turn_ms": 21333,
                "chars": 4210, "char_pct": 58.3, "first_seq": 1 }
  },
  "dropped_count": 3,
  "llm_calls": 41,
  "context": "會議主題：第四季預算\n與會者\n王小明：財務部經理，會議主席\n李美華：法務專員",
  "source": { "filename": "meeting.vtt", "segments": 186 }
}
```

摘要的 `grounded` 表示摘要裡的數字與英文詞**在逐字稿裡都找得到**，找不到的列在 `unsupported`（中文的講法是否忠實只能靠人看與段號）。

回應裡的 `context` 是送進來的會議背景（去掉頭尾空白），**沒送就沒有這個欄位**；網頁匯出的文件會把它原文放在標題之後、摘要之前。

`dropped_count` 是**引用對不上原文而被丟掉的項目數** —— 每一條抽出來的內容
都要在它宣稱的段落裡找得到，找不到就不留。這個數字偏高時代表模型在編，
換一個模型會比調參數有效。

#### 依會議背景修正逐字稿的專有名詞

逐字稿常把人名、產品名寫錯（`Bianca` 寫成 `Bianka`、王小明寫成王曉明）。會議背景裡寫了正確的寫法時，這支可以找出逐字稿裡**可能**寫錯的地方，**只建議、不改、不存**，也不需要 LLM：

```text
POST /tools/meeting-summary/api/term-suggestions
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 逐字稿（同上） |
| `context` | string | | 會議背景；沒給就沒有建議 |

```bash
curl -X POST http://localhost:8765/tools/meeting-summary/api/term-suggestions \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@meeting.vtt" \
  -F "context=與會者：王小明（財務長）、Bianca（PM）" | jq
```

```json
{
  "terms": ["王小明", "Bianca"],
  "suggestions": [
    {"from": "Bianka", "to": "Bianca", "count": 2, "seqs": [2],
     "example": "報價我已經寄給 Bianka 了，Bianka 會再確認。"},
    {"from": "王曉明", "to": "王小明", "count": 1, "seqs": [1],
     "example": "王曉明今天要報告第四季的預算。"}
  ]
}
```

比對規則是固定的，寧可少建議也不亂建議：英文差一兩個字母、或中間多了空白 / 連字號；中文三到八個字、**每個字讀音都對得上**（同音不同字）。只差大小寫、單複數、兩個字的詞、背景自己就這樣寫的、像兩個詞都說得通的，一律不建議。

挑好的放進 `/api/meeting-summary` 的 `replacements`，**分析之前**套用：英文照整個詞換（`Biankas` 不會被換掉一截），回應多一個 `replacements`（實際換了什麼、各幾處，一處都沒換到的不列）。網頁那條路匯出的 `.json` 附著逐字稿，換過的段落把原文留在 `orig_text`。

建議抓不到的寫法（辨識聽錯差太多，例如 `Groxmoxity` 應為 `Proxmox`）也可以自己放進 `replacements`，規則相同；英文換的時候**分大小寫**，要連小寫的 `groxmoxity` 一起換就另外放一組。

```bash
curl -X POST http://localhost:8765/tools/meeting-summary/api/meeting-summary \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@meeting.vtt" \
  -F "context=與會者：王小明（財務長）、Bianca（PM）" \
  --form-string 'replacements=[{"from": "Bianka", "to": "Bianca"}]' | jq '.replacements'
# → [{"from": "Bianka", "to": "Bianca", "count": 2}]
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
  | jq
# → {"job_id": "...", "upload_id": "...", "download_url": "/api/jobs/.../download"}
```

回應：`job_id`（OCR 在背景跑）。用第 10 章的作業 API 輪詢，完成後從 `download_url` 下載加上文字層的 PDF。第一次使用會先下載辨識模型，要多等一段時間。

#### 外部 GPU OCR Server

管理員可在 `admin/ocr-langs → 外部 GPU 識別伺服器` 部署 `jt-ocr-server` 到 GPU 主機，jtdt 透過 HTTP 呼叫遠端 EasyOCR，速度比 CPU 快 10× 以上。設定相關 admin endpoints：

```text
GET  /admin/ocr-langs/deploy/install.sh        # 下載安裝腳本
GET  /admin/ocr-langs/deploy/uninstall.sh      # 下載解除安裝腳本
GET  /admin/api/ocr-langs/external/status      # 讀取目前設定
POST /admin/api/ocr-langs/external/save        # 儲存 URL / Token / Timeout / 啟用
POST /admin/api/ocr-langs/external/test        # 測試連接
```

啟用後 `/tools/pdf-ocr/*` 與內部呼叫 EasyOCR 的工具會自動走遠端 GPU，連線失敗自動退回本機。`jt-ocr-server` 自身對外端點：

```text
GET  /healthz        # 不需 token,回 GPU/VRAM 資訊
GET  /version        # 需 Bearer token
POST /ocr            # 需 Bearer token,multipart image + langs form
```

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
| `stamp_image` | file | | 印章 / 簽名圖（透明 PNG 佳）。跟 `asset_id` 二選一 |
| `asset_id` | str | | 改用資產庫裡的印章 / 簽名 / Logo（id 請向管理員取得：管理區的資產管理，或 `GET /admin/api/assets`）。跟 `stamp_image` 二選一 |
| `x_mm` | float | | 左下角 X 位置（mm） |
| `y_mm` | float | | 左下角 Y 位置（mm） |
| `width_mm` | float | | 寬度（mm） |
| `height_mm` | float | | 高度（mm） |
| `rotation_deg` | float | | 旋轉角度，預設 `0` |
| `page_mode` | str | | `all`（每頁）/ `first` / `last`，預設 `all` |
| `pages_json` | str | | 指定頁：JSON 陣列，0 起算頁碼（如 `[0,2,4]`）。提供時優先於 `page_mode`；超出範圍的頁碼會被忽略 |
| `placements_json` | str | | **每頁獨立位置**（選用）。JSON 陣列，每個物件自帶頁碼與座標 → 不同頁可放不同位置、**同一頁可放多個**。提供時會忽略 `x_mm` / `y_mm` / `width_mm` / `height_mm` / `rotation_deg` / `page_mode` / `pages_json`；**未提供則行為與舊版完全相同** |

```bash
curl -X POST http://localhost:8765/tools/pdf-stamp/api/pdf-stamp \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.pdf" -F "stamp_image=@chop.png" \
  -F "x_mm=150" -F "y_mm=30" -F "width_mm=30" -F "height_mm=30" \
  -F "pages_json=[0,2]" \
  --output stamped.pdf
```

回應：蓋章後的 PDF。

位置欄位沒給時：用 `asset_id` 就照那顆章在資產庫設好的位置，上傳圖則是 105 / 250 / 30 / 30 mm。

```bash
# 用資產庫裡的公司大章，位置照資產庫的設定
curl -X POST http://localhost:8765/tools/pdf-stamp/api/pdf-stamp \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.pdf" -F "asset_id=ASSET_ID" \
  --output stamped.pdf
```

透過 API 蓋的章跟網頁版一樣會存進「用印簽名歷史」（原檔與成品，稽核員可查）；上傳的印章圖不另外存檔，只記下指紋。

**每頁獨立位置（placements）**：適合多頁合約 / 續保單這種「每頁簽名位置不同、同一頁要簽好幾處」的情境。

| placement 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `page` | int | ✓ | 0 起算頁碼；超過該 PDF 頁數者自動跳過 |
| `x_mm` / `y_mm` | float | ✓ | 位置（mm） |
| `width_mm` / `height_mm` | float | | 尺寸（mm），預設 30×30 |
| `rotation_deg` | float | | 旋轉角度，預設 `0` |
| `asset_id` | str | | 這一處改用資產庫裡的某一顆章（沒給就用上面的 `stamp_image` 或 `asset_id`）。每一處都有自己的 `asset_id` 時不必上傳圖 |

```bash
# 第 1 頁蓋 2 處、第 3 頁蓋 1 處，第 2 頁不蓋
curl -X POST http://localhost:8765/tools/pdf-stamp/api/pdf-stamp \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@policy.pdf" -F "stamp_image=@sign.png" \
  -F 'placements_json=[
        {"page":0,"x_mm":40,"y_mm":250,"width_mm":20,"height_mm":20},
        {"page":0,"x_mm":150,"y_mm":100,"width_mm":20,"height_mm":20},
        {"page":2,"x_mm":100,"y_mm":150,"width_mm":25,"height_mm":25}]' \
  --output stamped.pdf
```

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
| `mode` | str | | `mask`（遮罩，預設）/ `redact`（真刪）/ `replace`（換成假值） |
| `replacements` | str | | 僅 `replace`：JSON 物件 `{"原值": "指定的新值"}`。沒指定的一律自動產生 |
| `valid_checksum` | str | | 僅 `replace`：`1` = 產生可通過檢查碼的假值（見下方說明） |

```bash
curl -X POST http://localhost:8765/tools/doc-deident/api/doc-deident \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.pdf" -F "mode=mask" \
  --output deidentified.pdf
```

回應：去識別化後的檔案。

#### 替換模式（`mode=replace`）

把偵測到的資料換成另一個看起來正常、但不是真的值 —— 適合拿去測試系統、
給外部看的報表、教學範例。原文一樣是**真的刪除**。

沒有在 `replacements` 裡指定的值會**自動產生**，依欄位型別給對的樣子
（身分證、統編、手機、Email、信用卡、人名、地址、日期…），而且**同一個原值
在整份文件裡固定對應同一個假值**。

`valid_checksum` 預設不開，產生的假值**刻意不通過檢查碼**，絕不會撞到真人資料。
設成 `1` 之後身分證 / 統編 / 信用卡會算出正確的檢查碼（拿去測試系統不會被擋，
但算得出來的號碼有可能剛好是某個真人的）。Email 用 `example.com`、IP 用
`192.0.2.x`、MAC 用 `00:00:5E`，都是文件專用的保留範圍，兩種設定都安全。

```bash
curl -X POST http://localhost:8765/tools/doc-deident/api/doc-deident \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.pdf" -F "mode=replace" \
  -F 'replacements={"0912345678":"0955555555"}' \
  -F "valid_checksum=1" \
  --output replaced.pdf
```

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

回應 JSON：逐段差異結構。每一頁除了 `diff`（逐行的文字差異）之外還帶著：

| 欄位 | 說明 |
|---|---|
| `uid` | 這次比對的識別碼（**回應最上層**），拿去抓頁面圖 |
| `pages[].marks.a` / `.b` | 那一頁的差異框。每筆是 `{tag, line, rects}`，`tag` 是 `delete` / `insert` / `replace` |
| `pages[].marks[].rects` | `[x, y, w, h]`，**0~1 的比例**（不是畫素）—— 乘上你顯示的頁面圖尺寸就是位置 |
| `pages[].size.a` / `.b` | 該頁的 pt 尺寸 `[寬, 高]` |

抽不到文字座標時（掃描件、文字被轉成外框）`marks` 會是空陣列 ——
**那不代表沒有差異**，`diff` 那邊還是有的。

#### 頁面圖

```text
GET /tools/doc-diff/page-image/{uid}/{slot}/{page}
```

| 參數 | 說明 |
|---|---|
| `uid` | 上面那次比對回的識別碼 |
| `slot` | `a`（舊版）或 `b`（新版） |
| `page` | 從 1 開始 |

回 `image/png`（150 dpi）。Office 檔回的是**轉成 PDF 之後**的版面。
檔案過期會回 `410`；`uid` / `slot` / `page` 不合法一律 `404`。

```bash
curl -X POST http://localhost:8765/tools/doc-diff/api/doc-diff \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file_a=@v1.pdf" -F "file_b=@v2.pdf" > diff.json
UID=$(jq -r .uid diff.json)
curl -H "Authorization: Bearer YOUR_TOKEN" \
  "http://localhost:8765/tools/doc-diff/page-image/$UID/b/1" -o page1.png
```

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

### 文件翻譯

把整份辦公文件翻成另一種語言，**回傳同格式、同版面的檔案** —— 只換文字，
不重排版面。支援 `.doc` / `.docx` / `.odt`、`.xls` / `.xlsx` / `.ods`、
`.ppt` / `.pptx` / `.odp`。

> **不收 PDF**：PDF 裡沒有段落，文字是定位好的碎片，換成長度不同的譯文之後
> 版面一定跑掉。要翻 PDF 請用下面的「逐句翻譯」。

```text
POST /tools/doc-translate/api/doc-translate
```

Form（multipart）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `file` | file | ✓ | 辦公文件（上列九種副檔名） |
| `target_lang` | str | | 目標語言，預設 `zh-TW` |
| `source_lang` | str | | `auto`（預設）/ `en` / `ja` / `ko` … |
| `domain` | str | | 領域提示（法律合約、醫療報告…），提升專業用詞準確度 |

```bash
curl -X POST http://localhost:8765/tools/doc-translate/api/doc-translate \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "file=@contract.docx" \
  -F "target_lang=en" \
  -F "domain=法律合約" \
  -o contract_translated.docx
```

回應是**翻譯後的檔案本身**（`Content-Disposition: attachment`），格式與上傳的相同。

> 這個端點是**同步**的：整份翻完才回應，大檔會撞到反向代理的逾時。
> 網頁介面走的是背景作業版（`POST /tools/doc-translate/start`，
> 送出後用 `/api/jobs/{job_id}` 查進度，完成後到
> `GET /tools/doc-translate/download/{upload_id}` 取檔）。
>
> 需 admin 啟用 LLM 服務（`/admin/llm-settings`）。未啟用回 `503`。

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

#### 大量句子：背景作業版

上面那個端點是**同步**的：整份翻完才回應，句數一多就會撞到反向代理的逾時。
幾百句以上請改用背景作業 —— 送出後立刻拿到作業編號，翻譯在伺服器繼續跑，
**呼叫端可以離線**，之後再回來查進度與結果。

```text
POST /tools/translate-doc/start
GET  /tools/translate-doc/job/{job_id}?start=0
```

`start` 的 Body（JSON）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `sentences` | list[str] | ✓ | 已切好的句子陣列（自己切，或先用 `/extract-text`） |
| `source_lang` | str | | `auto`（預設）/ `en` / `zh` … |
| `target_lang` | str | | 目標語言，預設 `zh-TW` |
| `domain` | str | | 領域提示 |
| `filename` | str | | 顯示用的來源檔名（會出現在「我的作業」） |

```bash
JOB=$(curl -s -X POST http://localhost:8765/tools/translate-doc/start \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sentences":["Hello world.","This is a test."],"target_lang":"zh-TW"}' \
  | jq -r .job_id)

curl -s "http://localhost:8765/tools/translate-doc/job/$JOB" \
  -H "Authorization: Bearer YOUR_TOKEN" | jq
```

查詢的回應：

```json
{
  "status": "running",
  "progress": 0.5,
  "message": "翻譯中… 1 / 2 句",
  "elapsed": 3.2,
  "total": 2,
  "start": 0,
  "results": [
    {"src": "Hello world.", "translated": "你好，世界。"},
    {"src": "This is a test."}
  ],
  "cancelled": false
}
```

* `status`：`pending` / `running` / `done` / `error` / `cancelled` / `interrupted`
* **原文一送出就查得到**（右側還沒翻好的項目沒有 `translated` 欄位），
  所以呼叫端可以邊跑邊顯示。
* `start=N` 只回第 N 筆之後的資料 —— 幾萬句時不必每次拉全部。
* 取消請用共用的作業端點：`POST /api/jobs/{job_id}/cancel`。
* 歸屬與其他作業端點一致：非擁有者一律 `404`。

### 公文撰擬

把白話需求寫成「簽」或「函」，或依來文與辦理方向擬「簽辦意見」。**回傳的是草稿**：段名、項次、結語、稱謂由程式排，內容由模型寫；草稿裡的金額、日期、法規、條號、文號與「業經核准」這類說法都拿去跟你送進來的內容比，找不到依據的放在 `issues`（不刪）。網頁介面在中文、英文、日文介面都可以用；不論介面語言，產出一律是繁體中文的臺灣公文格式。

```text
POST /tools/official-doc/api/official-doc
```

Body（JSON），三種模式共用：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `mode` | str | | 模式：`sign`（簽，預設）/ `letter`（函）/ `endorse`（簽辦意見） |
| `length` | str | | 篇幅：`short`（精簡）/ `normal`（一般，預設）/ `long`（詳細） |
| `use_kb` | bool | | 設成 `true` 時先在公文知識庫裡找跟這件事相關的資料再撰寫（預設不查）。查得到哪些資料集依這把 Token 的使用者決定；只有用途是「業務依據」的資料算草稿的依據 |
| `use_history` | bool | | 設成 `true` 時先在**這把 Token 的使用者自己**的歷史案件裡找內容相近的（最多 3 件，取最新一版）當寫法參考（預設不查）。查不到別人的案件，管理員也一樣；歷史案件不算草稿的依據 |

簽（`mode=sign`）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `narrative` | str | ✓ | 需求敘述：用白話寫要辦的事（為什麼、要做什麼、多少錢、什麼時候），上限 4,000 字 |
| `unit` | str | | 承辦單位，寫進抬頭「簽　　於〇〇」 |
| `addressee` | str | | 陳核對象，一行一個（接在「敬陳」後面） |
| `closing` | str | | 主旨結語：`核示`（預設，「，簽請　核示。」）/ `鑒核` / `核准` / `none`（只加句號） |
| `with_date` | bool | | 設成 `true` 時，在抬頭下面加上今天的民國日期 |

函（`mode=letter`）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `narrative` | str | ✓ | 需求敘述：要請對方做什麼、期限、附件，上限 4,000 字 |
| `issuer` | str | | 發文身分：`agency`（公務機關，預設）/ `company`（企業發給政府機關）。企業時 `relation` 固定是 `company`（送什麼都不看），自稱「本公司」、稱對方 `貴〇`（看不出來時 `貴機關`），不用 `鈞〇`、不套簽的寫法；抬頭不寫檔號、保存年限、密等，地址、統一編號、聯絡人、署名用印沒填就標 `〔待補：…〕` |
| `org` | str | | 發文機關全銜（企業時是公司名稱），寫進標題 `〇〇〇　函` |
| `receiver` | str | | 受文者。稱謂取受文者名稱的最後一個字（`東湖區公所` → `貴所`） |
| `relation` | str | | 行文關係：`up`（上行，對上級機關）/ `peer`（平行）/ `down`（下行，對所屬機關）/ `people`（對人民或團體）/ `unknown`（不確定，預設）。稱謂與期望語都依它決定；`unknown` 時草稿把期望語與稱謂標成 `〔待確認：…〕`。企業發函由 `issuer=company` 決定，這裡不用送 |
| `closing` | str | | 期望語，**要屬於那個行文關係**（見下表），空的＝該行文關係的第一個；`relation=unknown` 時不收 |
| `speed` | str | | 速別：`普通件`（預設）/ `速件` / `最速件` |
| `doc_no` | str | | 發文字號（選填），例如 `府資字第1150000001號`，上限 200 字。沒送就留空，草稿不會自己編一個 |
| `copies` | str | | 正本（空的＝同受文者） |
| `cc` | str | | 副本 |
| `signature` | str | | 署名，例如 `局長　王○○` |
| `contact_fields` | object | | 聯絡資訊，一欄一個鍵（每一欄都可以不送）：`address` 地址、`tax_id` 統一編號（只有 `issuer=company` 才用）、`person` 聯絡人姓名、`person_label` 那一欄在草稿上的名稱（`聯絡人`（預設）或 `承辦人`）、`phone` 電話、`fax` 傳真、`email` 電子信箱。欄位名稱與順序由系統寫，沒送的欄不出現；值裡的換行收成一行 |
| `contact` | str | | 舊寫法：一行一項的文字，上限 500 字。有送 `contact_fields` 時不看這一欄。認得的行（地址、住址、聯絡人、承辦人、電話、傳真、電子信箱、統一編號…）分進各欄，**認不得的行不放進草稿**，列在回應的 `contact_unplaced` |
| `attachments` | str | | 附件 |
| `org_codes` | object | | 機關名稱對機關代碼的對照（選填）：鍵是機關全銜、值是機關代碼（例如 `Q1000000`），最多 40 筆，匯出 DI 檔時用。只存名稱寫在發文機關、受文者、正本或副本裡，而且公文電子交換系統地址簿裡那個代碼就是這個名稱的；對不上的不存（不回錯） |

| `relation` | 稱謂 | 可用的期望語 |
|---|---|---|
| `up` | `鈞〇`（`鈞府`、`鈞部`） | `請　鑒核` / `請　核示` / `請　鑒察` / `請　核備` |
| `peer` | `貴〇` | `請　查照` / `請　查照辦理` / `請　查照見復` / `請　惠允見復` / `請　同意見復` |
| `down` | `貴〇` | `請　照辦` / `請　查照` / `請　轉知` / `請　確實辦理` |
| `people` | `台端`（團體用 `貴〇`） | `請　查照` / `請　照辦` |
| `company` | `貴〇`（看不出來時 `貴機關`） | `請　查照` / `請　惠予審查` / `請　惠予辦理` / `請　惠予同意` / `請　惠復` |

稱謂前空一格（挪抬）、`擬請　貴局` 與 `請求　貴局` 改成 `請　貴局` 都由程式處理。發文日期、檔號、密等**留空** —— 那些由公文系統發文時給；發文字號只寫你送的 `doc_no`，沒送也留空。

簽辦意見（`mode=endorse`）：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `source` | str | ✓ | 來文內容（或你整理的來文大綱），上限 12,000 字 |
| `direction` | str | ✓ | 辦理方向：你打算怎麼辦理，上限 2,000 字。**沒填回 400** —— 同意、駁回或存查是你的決定，不替你決定 |
| `outline` | bool | | 設成 `true` 表示 `source` 是你整理的大綱，不是來文全文 |
| `units` | str | | 承辦/協辦單位 |
| `internal_deadline` | str | | 內部期限（跟來文的期限分開寫） |
| `fmt` | str | | 格式：`compact`（精簡一段，預設）/ `list`（條列） |
| `closing` | str | | 結尾：`陳核`（預設）/ `陳閱` / `none` |

依修改後的資料重新產生（三種模式都收，選填）：

| 欄位 | 類型 | 說明 |
|---|---|---|
| `facts` | array | 上一次回應的 `facts`（可以改過）。帶了就不再請模型整理資料，只撰寫一次。**伺服器會重新判斷每一項的狀態**：標成 `provided` 的，`quote` 要真的在 `narrative` / `source` 裡找得到，不然降成 `inferred` |
| `overrides` | object | 以欄位代號對應新的值，例如 `{"budget_source": "115年度資訊設備費"}`。改過的那一項狀態變成 `confirmed`，檢查拿它當依據；給空字串表示「沒有這項資料」 |

承辦單位、陳核對象等短欄位與 `overrides` 的值上限 200 字。**超過上限一律回 400 並講出上限與目前字數，不會自動截斷**（截掉的部分可能正好是期限或條件）；選項不在清單裡也回 400。

```bash
curl -X POST http://localhost:8765/tools/official-doc/api/official-doc \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "sign", "unit": "資訊室", "addressee": "主任秘書\n局長",
       "narrative": "本局資訊室兩台印表機已使用8年，經常卡紙，維修廠商表示零件已停產。擬以115年度資訊設備費新臺幣6萬元汰換雷射印表機2台，預計11月30日前完成採購。"}' | jq
```

回應 JSON（`facts` 節錄；這份草稿的說明多寫了一條你沒提到的法規，所以 `issues` 有兩條）：

```json
{
  "mode": "sign",
  "text": "簽　　於資訊室\n主旨：汰換資訊室雷射印表機2台，簽請　核示。\n說明：\n一、本局資訊室印表機已使用8年，經常卡紙，維修廠商表示零件已停產。\n二、經費由115年度資訊設備費支應，並依政府採購法第49條辦理。\n擬辦：擬購置雷射印表機2台，預算新臺幣6萬元，於11月30日前完成採購。\n敬陳\n主任秘書\n局長",
  "facts": [
    { "key": "amount", "label": "金額", "value": "新臺幣6萬元",
      "quote": "新臺幣6萬元", "status": "provided" },
    { "key": "budget_source", "label": "經費來源", "value": "115年度資訊設備費",
      "quote": "115年度資訊設備費", "status": "provided" }
  ],
  "issues": [
    { "code": "law_unsupported", "severity": "error",
      "message": "法規「政府採購法」不是你提供的，請確認是否適用，或刪除。",
      "template": "法規「{0}」不是你提供的，請確認是否適用，或刪除。",
      "args": ["政府採購法"], "snippet": "政府採購法" },
    { "code": "article_unsupported", "severity": "error",
      "message": "「第49條」不是你提供的條號，請確認或刪除。",
      "template": "「{0}」不是你提供的條號，請確認或刪除。",
      "args": ["第49條"], "snippet": "第49條" }
  ],
  "llm_calls": 2
}
```

簽辦意見：

```bash
curl -X POST http://localhost:8765/tools/official-doc/api/official-doc \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "endorse", "units": "資訊室", "direction": "擬由資訊室填報後函復",
       "source": "嘉禾市政府115年10月1日府資字第1150012345號函：請各機關於115年10月20日前填報資訊設備盤點資料，逾期視同無資料。"}' | jq '.text'
# → "嘉禾市政府函請各機關於115年10月20日前填報資訊設備盤點資料，擬由資訊室填報後函復，陳核。"
```

函：

```bash
curl -X POST http://localhost:8765/tools/official-doc/api/official-doc \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "letter", "org": "嘉禾市資訊局", "receiver": "嘉禾市東湖區公所",
       "relation": "down", "closing": "請\u3000照辦", "cc": "本局資訊管理科",
       "signature": "局長\u3000王○○", "attachments": "資訊資產清冊1份",
       "contact_fields": {"address": "嘉禾市文化路1號", "person_label": "承辦人",
                          "person": "王小明", "phone": "(02)1234-5678"},
       "narrative": "請各區公所於115年10月20日前填報資訊資產清冊，以電子郵件回傳。"}' | jq -r '.text'
```

回應欄位：

| 欄位 | 說明 |
|---|---|
| `text` | 草稿全文（純文字，可以直接貼進公文系統）。沒提供的必要資料寫成 `〔待補：…〕`、互相矛盾的寫成 `〔待確認：…〕`，不會自行補上或擇一 |
| `facts` | 資料表，每一項是 `{key, label, value, quote, status}`。狀態有五種：`provided`（原文有，`quote` 是原文裡的那一段）、`inferred`（模型推論的，要確認）、`missing`（未提供）、`conflict`（矛盾，另有 `values` 列出各種寫法）、`confirmed`（你在 `overrides` 改過的，原本的值記在 `was`） |
| `issues` | 檢查結果，依嚴重度排序。嚴重度有三種：`error`（找不到依據，或草稿還寫著你改掉的舊值）、`todo`（待補、待確認）、`hint`（建議）。每一條的 `message` 是填好的中文，`template` 與 `args` 是同一句的樣板與參數（要自己翻譯時用），`snippet` 是草稿裡的那一段原文 |
| `llm_calls` | 這次呼叫了模型幾次（回答不合格式而重問也算） |
| `references` | 有 `use_kb` 或 `use_history` 時參考了哪些資料：`{id, title, dataset_name, version_label, locator_text, heading, purpose, text, source_url, used}`。`purpose` 是 `substantive_basis`（業務依據，**只有這一種算依據**）/ `format_reference`（格式與用語參考）/ `style_example`（寫作範例）/ `past_case`（自己的歷史案件，另有 `case_id`、`case_rev`、`case_updated`）；`used` 是模型說它有引用。兩個都沒用時是空陣列 |
| `kb_note` | 公文知識庫的狀況：空字串（正常，或沒用公文知識庫）/ `none`（找不到相關資料）/ `failed`（查詢失敗；草稿照樣產生，只是沒有參考資料，原因寫在服務記錄） |
| `history_note` | 歷史案件的狀況：空字串（正常，或沒用歷史案件）/ `none`（自己的案件裡沒有相近的）/ `failed`（查詢失敗；草稿照樣產生） |
| `contact_unplaced` | 用舊寫法 `contact` 送的聯絡資訊裡，對不到任何欄位、沒有放進草稿的那幾行（陣列；都對得到時是空陣列） |

| `code` | 意思 |
|---|---|
| `qty_unsupported` / `date_unsupported` | 金額、數量或日期在你給的內容裡找不到（萬元換算、中文數字、民國與西元都會換算後再比） |
| `law_unsupported` / `article_unsupported` / `docno_unsupported` | 法規名稱、條號、文號不是你提供的 |
| `claim_unsupported` | 「業經核准」「已決標」「依法應」「驗收合格」「免罰」「不可抗力」這類把事情寫成已確定的說法，你沒有這樣寫。你在需求裡寫的「不要寫成已經核准」是寫作指示，不算你有寫 |
| `word_unsupported` | 草稿寫了「含稅」「未稅」，你提供的內容裡沒有 |
| `placeholder` | 草稿裡的 `〔待補：…〕` / `〔待確認：…〕`，送出前要補上 |
| `missing_fact` | 缺了該有的資料：要花錢卻沒寫金額或經費來源、沒寫目的或辦理方式、沒寫期程；函還有沒填受文者或發文機關（企業發函是公司名稱） |
| `relation_unknown` | 函的行文關係是「不確定」，期望語與稱謂要送出前確認 |
| `salutation` | 函的稱謂跟行文關係不合（上行文寫了 `貴〇`、非上行文或企業發的函寫了 `鈞〇`） |
| `company_wording` | 企業發的函寫了機關內部簽的用語（`擬辦`、`簽請`、`陳核`）或機關的自稱（`本局`） |
| `action_not_in_direction` / `action_negated` | 簽辦意見的 `擬…` 裡出現你的辦理方向沒有的動作，或你明講不要的動作（例如方向寫 `不用轉知`，草稿寫 `擬轉知`） |
| `missing_section` | 簽沒有「主旨」段（`error`）或「擬辦」段（`hint`） |
| `long_subject` | 主旨超過 120 字 |
| `omitted` | 原文提到的數字、日期或你自己寫的條號，草稿裡沒寫到 |
| `proposal_no_approval` | 簽的擬辦沒有寫請主管同意什麼（`擬請同意…`） |
| `weekday_mismatch` | 日期與星期對不上（沒寫年份的以今年算，訊息會講出來）；不會替你改 |
| `date_past` | 期限已經過了（寫成 `…前` 的期限；`原本預計…前` 這種舊期限不算） |
| `attachment_mentioned` | 函：你的內容提到要附東西，附件欄卻是空的 |
| `stale_value` | 你在 `overrides` 改過的值，草稿還寫著舊的 |
| `injection_suspect` | 來文裡有一段像是寫給 AI 的指令。那一句在送模型之前已經拿掉，也不拿來當依據；草稿仍要逐句核對 |

**檢查驗不到語意**：因果寫反、結論寫錯只能靠人看；`issues` 是空的不代表草稿是對的，送出前一定要人工核對。

狀態碼：欄位不對（必填沒填、超過上限、選項不在清單裡）回 **400**；LLM 沒啟用回 **503**；模型呼叫失敗，或連問兩次都沒照格式回答，回 **502**（訊息是固定的一句，原因只寫進服務記錄）。

**這支是同步的**（一份約 2～4 次模型請求），呼叫端的逾時要放寬。要背景處理、或要改完草稿再檢查與匯出，走下面網頁用的那條路。

#### 背景作業與匯出（網頁用的那條路）

這幾支一樣可以帶 Bearer token 呼叫；**每一支都要帶案件編號（`case_id`），而且只拿得到自己的案件**。

| 端點 | 說明 |
|---|---|
| `POST /tools/official-doc/start` | 欄位同上（JSON）。回 `{"job_id": "...", "case_id": "...", "contact_unplaced": [...]}`；用 `/api/jobs/{job_id}` 查進度，完成後作業的結果檔是 ODT 草稿（`/api/jobs/{job_id}/download`） |
| `GET /tools/official-doc/result/{case_id}` | 取結果：`{case_id, mode, inputs, title, draft, created_at}`，`draft` 是 `{mode, text, facts, issues, content, llm_calls}`；過期或被清掉回 **410** |
| `POST /tools/official-doc/check` | 送 `{"case_id": "...", "text": "改過的草稿"}`，回 `{"issues": [...]}`：拿目前的文字重新檢查，**不呼叫模型**；依據是建立案件時你送進來的內容 |
| `POST /tools/official-doc/export` | 送 `{"case_id": "...", "text": "...", "fmt": "odt", "title": "...", "draft_mark": true, "extras": {...}}`，回檔案。格式（`fmt`）有 `txt` / `odt` / `docx` / `pdf` / `png` / `svg` / `json` / `di`；照**送來的文字**匯出，也就是你改過的版本 |
| `POST /tools/official-doc/di-preview` | 送 `{"case_id": "...", "text": "...", "title": "..."}`，回 `{filename, mode, root, dtd, xml, notes, valid, errors}`：跟匯出 `di` 同一支產生器，`xml` 就是下載拿到的那一份；`valid` 是有沒有通過 DTD 檢查，`notes` 是注意事項（`{code, args}`，例如沒對到機關代碼的機關） |
| `GET /tools/official-doc/orgs` | 查機關名稱（公文電子交換系統地址簿，管理員下載過才有）：參數 `q`（名稱或代碼開頭）與 `limit`（1 到 2000，預設 10），回 `{"orgs": [{"name": "...", "id": "..."}], "exact": "...", "total": 23, "max": 2000}`；`exact` 是名稱完全相同而且只有一筆時的機關代碼，同名好幾個就是空的；`total` 是符合的總筆數（清單只列前幾筆時用來講出還有幾筆沒列），`max` 是一次最多列幾筆。排序：名稱完全相同的在前，接著是主機關，內部單位排在後面 |
| `POST /tools/official-doc/extract-text` | multipart `file` → `{"text": "...", "chars": 11, "filename": "..."}`：從 PDF、Word（`.docx` / `.doc`）、ODT、RTF、純文字（`.txt` / `.md`）抽出文字給你貼進欄位，**檔案不留**；上限 20 MB |
| `GET /tools/official-doc/api/cases` | 歷史案件清單：`{"cases": [...], "show_owner": false, "limit": 500}`，新的在前。查詢參數 `q`（比對名稱、標題、案件編號）與 `mode`（`sign` / `letter` / `endorse`）都選填。每一筆有 `case_id`、`name`、`title`（檔名用，主旨前 20 字）、`subject`（主旨整句）、`mode`、`latest_rev`、`issues`、`created_at`、`updated_at`、`deleted`、`imported`（從 DI 檔匯入的）、`di_ok`（下載得到 DI 檔嗎）；管理員拿到的是每個人的（含已刪除），多一個 `owner` |
| `GET /tools/official-doc/case/{case_id}` | 一個案件的清單資料，加上 `has_result` 與 `job`（最近一件作業的 `{id, status}`，還在跑時可以接著查進度） |
| `POST /tools/official-doc/case/{case_id}/rename` | 送 `{"name": "..."}` 改名（最多 80 字，超過回 **400**，不截斷）；空字串＝用草稿的標題 |
| `DELETE /tools/official-doc/case/{case_id}` | 刪除案件：之後本人查不到、打不開；真的從磁碟移除照「檔案保留 / 清理」的公文撰擬案件保留期（預設 365 天，從刪除那天起算） |
| `GET /tools/official-doc/case/{case_id}/di` | 下載案件**最新那一版**的 DI 檔（檔名是案件名稱或標題）；簽辦意見沒有 DI 檔，回 **400**。標頭 `X-Jtdt-Di-Valid` 是有沒有通過 DTD 檢查，`X-Jtdt-Di-Notes` 是注意事項有幾條 |
| `POST /tools/official-doc/cases/di` | 送 `{"case_ids": ["...", "..."]}`（最多 100 件），回 zip，一件一個 DI 檔。簽辦意見與還沒有草稿的略過：打包了幾件在標頭 `X-Jtdt-Di-Count`，略過幾件在 `X-Jtdt-Di-Skipped`；全部都略過回 **400**。**任何一件不是你的就整批回 404** |
| `POST /tools/official-doc/cases/import` | multipart `files`（可以好幾個，DI 檔或整包 zip，一次最多 50 份），每一份 DI 檔變成一件歷史案件，擁有者是上傳的人。回 `{"imported": [{filename, case_id, title, mode, mode_name, notes, issues}], "failed": [{filename, code, args, error}], "skipped": 2}`：讀不進來的一份不影響其他份；`skipped` 是壓縮檔裡不是 DI 檔的檔案數。只收函與簽；讀檔時不展開實體、不連網路，每份上限 1 MB |

案件（輸入、草稿、版本）存在伺服器上，照「檔案保留 / 清理」的保留期留著，不跟著暫存檔的保留期走。
別人的、不存在的、已刪除的案件一律回同一個 **404**（分得出來的話，就能拿任意編號問「這個案件存不存在」）。

匯出的細節：

* ODT 由程式直接產生，**不需要 Office 引擎**；Word 與 PDF（`docx` / `pdf`）經 Office 引擎轉，沒有引擎時回 **503**。
* 頁首預設標「草稿」，`draft_mark` 設成 `false` 就不標。檔名用 `title`（最長 40 字，檔名不能用的字元會拿掉），下載的檔名是 `<title>-草稿.<fmt>`。
* 字型用標楷體；伺服器上沒有標楷體時，PDF 改用其他楷體，再沒有就用明體。
* JSON 帶 `"format": "jtdt-official-doc"` 與 `"format_version": 1`，內容是整份案件；`draft.text` 換成送來的文字，`draft.issues` 依那份文字重新檢查。
* 電子公文 DI 檔（`di`）是政府電子公文的文書本文檔（XML），格式照檔案管理局〈文書及檔案管理電腦化作業規範〉104 版 DTD（函 `104_2_utf8.dtd`、簽 `104_5_utf8.dtd`），給承辦人匯入機關自己的公文系統，再照常取號、簽核、發文。發文日期、發文字號、文號留空就留空，有填就整串照放；機關代碼先用 `org_codes`，沒有才照名稱查地址簿（名稱完全相同而且只有一筆才填）。簽辦意見沒有 DI 檔，找不到「主旨：」那一行也產生不了，兩種都回 **400**。回應標頭 `X-Jtdt-Di-Valid` 是有沒有通過 DTD 檢查（`1` 或 `0`），`X-Jtdt-Di-Notes` 是注意事項幾條。
* 圖片（`png` / `svg`）跟 PDF 同一個版面：一頁時回那一張（PNG 200 dpi；SVG 的字轉成外框，沒有標楷體的電腦也長得一樣），多頁時一頁一張打包成 zip（`<title>-草稿-png.zip`），最多 30 頁。

版面加註（`extras`，選填；只用在 `odt` / `docx` / `pdf` / `png` / `svg`，**不會寫進草稿文字**，預覽圖也照著畫）：

| 欄位 | 說明 |
|---|---|
| `page_numbers` | 設成 `true` 時頁尾加「第○頁　共○頁」 |
| `binding_line` | 設成 `true` 時左側加裝訂線（虛線與「裝」「訂」「線」） |
| `copy_mark` | 左上角的標示：`正本` / `副本` / `抄本`；只有函才有 |
| `send_method` | 左上角的「發文方式：…」：`電子交換` / `郵寄` / `掛號郵寄` / `專差送達` / `親自送達` / `傳真` / `電子郵件`；只有函才有 |
| `delegate` | 署名下方印「本案依分層負責規定授權○○決行」的那幾個字（例如 `業務主管`），最多 20 字；只有機關發的函才有 |
| `receiver_address` | 受文者的郵遞區號與地址（開窗信封），印在「受文者」上面，最多 80 字；只有函才有 |
| `endorse_frame` | 設成 `true` 時加上單獨列印用的抬頭與承辦人欄：最上面 `簽辦意見` 與來文那一行（機關與文號），最下面承辦人與日期；只有簽辦意見才有。來文那一行由伺服器從案件的資料表取，送來的不收 |

選項不在清單上、字數超過回 **400**；不適用的欄位（例如簽送來 `copy_mark`）直接不用。

```bash
# 改完草稿之後重新檢查，再匯出 ODT
curl -X POST http://localhost:8765/tools/official-doc/check \
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"case_id": "CASE_ID", "text": "簽　　於資訊室\n主旨：…"}' | jq '.issues'

curl -X POST http://localhost:8765/tools/official-doc/export \
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"case_id": "CASE_ID", "text": "簽　　於資訊室\n主旨：…", "fmt": "odt", "title": "汰換印表機"}' \
  -o draft.odt

# 匯出電子公文 DI 檔（匯入機關的公文系統用）
curl -X POST http://localhost:8765/tools/official-doc/export \
  -H "Authorization: Bearer YOUR_TOKEN" -H "Content-Type: application/json" \
  -d '{"case_id": "CASE_ID", "text": "嘉禾市資訊局　函\n…\n主旨：…", "fmt": "di", "title": "盤點"}' \
  -o draft.di
```

需要先在管理區啟用 LLM（`/admin/llm-settings`）。`check`、`export`、`result` 與 `extract-text` 不呼叫模型。

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

### 乘車證明整理

解析一批台鐵 / 高鐵 / Uber 乘車證明 PDF，回結構化 JSON。**只解析、不寫入使用者的清單**。

```text
POST /tools/transit-proof/api/transit-proof
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `files` | file[] | ✓ | 乘車證明 PDF，一次最多 200 個 |

```bash
curl -X POST http://localhost:8765/tools/transit-proof/api/transit-proof \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "files=@proof1.pdf" -F "files=@proof2.pdf" | jq
```

```json
{
  "ok": true,
  "count": 1,
  "entries": [{"date": "...", "from": "...", "to": "...", "amount": 0}],
  "failed": [{"file": "other.pdf",
              "error": "無法辨識為乘車證明（格式不符或版面不支援）"}]
}
```

**認不出來的檔案不會讓整批失敗** —— 成功的進 `entries`，失敗的逐檔列在 `failed`
（HTTP 仍是 200）。要判斷有沒有漏，看 `failed` 是不是空的，不要只看 HTTP 狀態。
超過 200 個檔案回 `400`；完全沒帶 `files` 回 `422`。

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
GET /api/jobs/{job_id}/download/{filename}    # 同一份結果，只是讓瀏覽器存成這個檔名
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

### 列出自己的作業（「我的作業」）

```text
GET /api/jobs?active=false&limit=50&offset=0
```

「我的作業」頁用的清單，只列自己的作業（`active=true` 只列排隊中與進行中的）。每一列有 `status`、`progress`、`has_result`（下載檔還在不在）、`view_url`（「開啟」要去的那一頁）與 `view_ok`。

回應裡的 `view_ok` 是 `view_url` 那一頁現在還打不打得開：`view_url` 有值但 `view_ok` 是 `false`，代表資料已過保留期被清掉。判斷要不要給「開啟」請用 `view_ok`，**不可以用 `has_result` 代替**（逐句翻譯沒有下載檔，但「開啟」打得開）。

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

`api_key` **讀取時不會回傳金鑰本身**：已設定時是 `"__KEPT__"`，沒設定是空字串。
更新時送 `"__KEPT__"` 或不送這個欄位＝不動，送新的字串＝換成這把（加密存放），送空字串＝移除。

### 測試 LLM 連線

```text
POST /admin/api/llm/test-connection
```

| 參數 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `base_url` | str | | 要測的位址。**不給就測目前已存檔的設定** |
| `api_key` | str | | 同上，不給就用已存檔的。送 `"__KEPT__"` 時，**只有 `base_url` 跟存檔的一樣才會帶上存著的金鑰** |
| `timeout_seconds` | num | | 上限 30 秒（管理頁不能卡住） |

JSON body。管理頁的「測試連線」按鈕會把**還沒存檔**的那組設定帶進來；
命令列只想確認「現在這組通不通」時整個 body 都可以省略。

```bash
# 測目前存檔的設定
curl -X POST http://localhost:8765/admin/api/llm/test-connection \
  -H "Authorization: Bearer ADMIN_TOKEN" | jq
# → {"ok": true, "latency_ms": 42, "error": null, "models": [...]}

# 測一組還沒存的設定
curl -X POST http://localhost:8765/admin/api/llm/test-connection \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"base_url": "http://10.0.0.5:11434/v1"}' | jq
```

連不上時**回 200**、`ok: false` 加 `error` —— 「我們測不到對方」不是
這個請求本身失敗。

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
GET /admin/api/settings-export/categories
```

```bash
curl http://localhost:8765/admin/api/settings-export/categories \
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

### 簽章網址取檔（不是給人呼叫的）

有些外部服務只收「網址」—— 檔案不是我們上傳給它，是它拿我們給的網址自己來拉。
這種情況我們給的是**短效簽章網址**，而不是 API Token：

```text
GET /api/speech/audio/{file_id}?exp=<到期的 unix 秒數>&sig=<簽章>
```

* **認證是網址本身**，不帶 `Authorization`。簽章綁住「檔案 id ＋ 到期時間」，
  所以改 `exp` 延期會讓簽章失效。
* **驗不過一律回 404** —— 格式不對、簽章錯、過期、檔案不在，四種在外面
  看起來一模一樣（回 403 等於告訴對方「這個 id 是存在的」）。
* 網址由伺服器端產生，**沒有公開的端點可以要一組簽章** ——
  這一條列在這裡是為了「這份文件不漏掉任何端點」，不是給你呼叫的。

拉檔拿到 404 時，最常見的原因是**網址過期**而不是檔案不見了。

### 管理介面自己用的 XHR 端點

下面這些是**管理頁面自己呼叫的端點**，不是穩定的對外介面：它們的參數與回傳
會跟著頁面改，**不保證相容**。列出來的目的是「這份文件不漏掉任何端點」，
需要自動化的話請優先用上面那些有明確契約的 API。

| 端點 | 方法 | 對應的管理頁 |
|---|---|---|
| `/admin/api/check-latest-version` | POST | 系統狀態 —— 檢查有沒有新版 |
| `/admin/api/upload-limit/probe` | POST | 系統狀態 —— 量反向代理的上傳上限 |
| `/admin/api/llm/context-variant` | POST | LLM 設定 —— 在 Ollama 上建一個上下文較大的版本（`model` 與 `num_ctx`，回新名字，例如 `gemma4:26b-ctx32k`；只對已存檔的伺服器建，原本的模型不動）|
| `/admin/api/ocr-langs/set-engine` | POST | OCR 語言包 —— 切換預設引擎 |
| `/admin/api/ocr-langs/set-quality` | POST | OCR 語言包 —— 切換辨識品質 |
| `/admin/api/ocr-langs/switch-active` | POST | OCR 語言包 —— 切換啟用的語言 |
| `/admin/jobs/api/list` | GET | 作業佇列 —— 目前的作業清單 |
| `/admin/jobs/api/history` | GET | 作業佇列 —— 歷史紀錄 |
| `/admin/jobs/api/cancel/{job_id}` | POST | 作業佇列 —— 取消一件作業 |
| `/admin/jobs/api/pause` | POST | 作業佇列 —— 暫停 / 恢復派送 |
| `/admin/jobs/api/concurrency` | POST | 作業佇列 —— 最大同時作業數 |
| `/admin/jobs/api/priority-users` | GET / POST | 作業佇列 —— 優先派送名單（**順序就是優先序**）|
| `/admin/jobs/api/user-search` | GET | 作業佇列 —— 指定優先使用者時的搜尋框 |
| `/admin/api/jtlw/settings` | POST | 語音服務（JTLW）—— 存送件位址與金鑰 |
| `/admin/api/jtlw/test` | POST | 語音服務（JTLW）—— 測連線（`/health` ＋ `/capabilities` 兩段）|
| `/admin/api/jtlw/profiles` | GET | 語音服務（JTLW）—— 取對方提供的處理設定清單（下拉用）|
| `/admin/knowledge/api/overview` | GET | 公文知識庫 —— 資料集、段數、目前的檢索方式 |
| `/admin/knowledge/api/datasets` | POST | 公文知識庫 —— 建立資料集 |
| `/admin/knowledge/api/datasets/{dataset_id}` | POST | 公文知識庫 —— 修改資料集（名稱、類別、用途、可見群組）|
| `/admin/knowledge/api/datasets/{dataset_id}/delete` | POST | 公文知識庫 —— 刪除資料集（連同版本與段落）|
| `/admin/knowledge/api/datasets/{dataset_id}/versions` | GET | 公文知識庫 —— 資料集裡的文件版本 |
| `/admin/knowledge/api/datasets/{dataset_id}/upload` | POST | 公文知識庫 —— 上傳文件（背景處理）|
| `/admin/knowledge/api/versions/{version_id}/activate` | POST | 公文知識庫 —— 啟用這一版 |
| `/admin/knowledge/api/versions/{version_id}/deactivate` | POST | 公文知識庫 —— 停用這一版 |
| `/admin/knowledge/api/versions/{version_id}/delete` | POST | 公文知識庫 —— 刪除這一版 |
| `/admin/knowledge/api/versions/{version_id}/reprocess` | POST | 公文知識庫 —— 重新切段 |
| `/admin/knowledge/api/versions/{version_id}/meta` | POST | 公文知識庫 —— 改版本資訊（版本、日期、發布機關、出處網址）|
| `/admin/knowledge/api/versions/{version_id}/preview` | GET | 公文知識庫 —— 看切出來的段落 |
| `/admin/knowledge/api/versions/{version_id}/file` | GET | 公文知識庫 —— 下載原檔 |
| `/admin/knowledge/api/search` | POST | 公文知識庫 —— 試查 |
| `/admin/knowledge/api/embedding` | GET / POST | 公文知識庫 —— 嵌入服務設定（`use_llm_server` 預設 `true`：沿用 LLM 設定裡公文撰擬用的那台；金鑰不回傳；GET 另附沿用的那台 `llm_server` 與重建進度 `rebuild`；畫面在 LLM 設定頁）|
| `/admin/knowledge/api/embedding/test` | POST | 公文知識庫 —— 測嵌入服務連線 |
| `/admin/knowledge/api/embedding/models` | POST | 公文知識庫 —— 列出伺服器上的嵌入模型（給「嵌入模型」下拉用；Ollama 只列能做嵌入的；每個模型附 `usage`：系統會自動加的前綴與載入參數，沒有的是 `null`）|
| `/admin/knowledge/api/rebuild` | POST | 公文知識庫 —— 重建向量索引（背景）|
| `/admin/knowledge/api/vectors/disable` | POST | 公文知識庫 —— 停用向量檢索（退回關鍵字檢索）|
| `/admin/knowledge/api/groups` | GET | 公文知識庫 —— 設定可見群組用的群組清單 |
| `/admin/knowledge/api/gov/status` | GET | 公文知識庫 → 政府公開資料 —— 來源、下載檔、選取與匯入的狀態（不連外）|
| `/admin/knowledge/api/gov/{gid}/search` | GET | 公文知識庫 → 政府公開資料 —— 在已下載的清單裡搜尋（`q`、`limit`、`offset`；`browse=1` 沒有關鍵字時回整份清單、`level` 依位階篩選、`keys_only=1` 回整個範圍的代碼給全選用）|
| `/admin/knowledge/api/gov/{gid}/selection` | GET / POST | 公文知識庫 → 政府公開資料 —— 選取的項目（POST `{keys, confirm}`；量大回 409 `need_confirm`）|
| `/admin/knowledge/api/gov/{gid}/selection/reset` | POST | 公文知識庫 → 政府公開資料 —— 選取回到預設建議 |
| `/admin/knowledge/api/gov/packages/{pid}` | POST | 公文知識庫 → 政府公開資料 —— 改下載網址 / 備用網址 |
| `/admin/knowledge/api/gov/packages/{pid}/reset` | POST | 公文知識庫 → 政府公開資料 —— 網址還原預設 |
| `/admin/knowledge/api/gov/packages/{pid}/upload` | POST | 公文知識庫 → 政府公開資料 —— 手動上傳下載檔，換上之後接著匯入選取的項目（背景處理）|
| `/admin/knowledge/api/gov/{gid}/download` | POST | 公文知識庫 → 政府公開資料 —— 下載清單（背景，不匯入）|
| `/admin/knowledge/api/gov/{gid}/import` | POST | 公文知識庫 → 政府公開資料 —— 匯入選取的項目（背景）|
| `/admin/knowledge/api/gov/{gid}/update` | POST | 公文知識庫 → 政府公開資料 —— **下載並匯入**（畫面上唯一的按鈕）：重新下載 ＋ 匯入選取的項目，有異動的才建新版本；全部下載失敗但有上次的清單時照那份匯入（結果 `summary.stale_list`）（背景）|

政府公開資料那幾支的 `{gid}` 是 `moj`（全國法規資料庫）/ `ndc`（國發會行政規則）/ `ey`（行政院釋例），`{pid}` 是 `moj-law`、`moj-order`、`ndc-rules-1`、`ndc-rules-2`、`ey-mailbox`、`ey-interp`。

另外 `GET /admin/knowledge/api/datasets/{dataset_id}/versions` 的回應多了選用欄位 `gov`（政府公開資料匯入的版本才有：`group`、`group_name`、`key`、`license`、`attribution`、`notice`、`abolished`）。

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

## 14b. 使用者工作區（session 認證，非 Bearer API）

「我的工作區」是綁定登入 session 的網頁功能（cookie 認證），**不屬於對外 Bearer API**，因為每個檔案以登入者帳號隔離。需 admin 在「工作區設定」啟用；停用時下列端點一律回 404。

| 端點 | 方法 | 說明 |
|---|---|---|
| `/workspace` | GET | 「我的工作區」頁面 |
| `/workspace/api/list` | GET | 列出自己的檔案（`?accept=pdf,png` 過濾）+ 容量 + 保留時數；每個檔案多 `preview`（有沒有縮圖）與 `kind`（`pdf` / `image` / `office` / `text` / `audio` / `video`） |
| `/workspace/api/count` | GET | 檔案數（側欄徽章用） |
| `/workspace/save` | POST | 存檔：`job_id`（伺服器端複製 job 結果）或 `file`（直接上傳 bytes）。格式依內容判斷：PDF / PNG、辦公文件、純文字、錄音 / 錄影檔（.m4a / .mp3 / .wav / .aac / .ogg / .opus / .flac / .mp4 / .mov / .mkv / .webm）；錄音檔另有單檔上限（`max_audio_mb`），超過回 413 |
| `/workspace/file/{file_id}` | GET | 取檔（`?dl=1` 下載）|
| `/workspace/thumb/{file_id}` | GET | 縮圖（PDF 首頁渲染 / PNG 原圖）；純文字與錄音檔回 1×1 透明佔位圖（`Cache-Control: no-store`），請照 `preview` 判斷，不要拿它當縮圖 |
| `/workspace/delete` | POST | 刪除（`file_id`）|
| `/workspace/rename` | POST | 重新命名（`file_id`、`name`）|
| `/tools/meeting-transcribe/from-workspace` | POST | 把自己工作區裡的錄音檔交給轉逐字稿（JSON `{"file_id": "…"}`，不重新上傳），回的內容同 `/tools/meeting-transcribe/upload`；別人的檔案 / 不存在 → 404、不是錄音 → 400、語音服務沒設定 → 503 |

容量額度、單檔上限、錄音檔上限、保留時數、啟用 / 停用由 admin 在 `/admin/workspace` 設定（全站統一，無個人特例）。

---

## 14c. 使用者通知與收件匣（session 認證，非 Bearer API）

跟 §14b 的工作區同性質：綁登入 session（cookie），**不屬於對外 Bearer API**，
因為內容按登入者隔離。未登入一律 401 / 302。

| 端點 | 方法 | 說明 |
|---|---|---|
| `/api/my/notify` | GET | 讀自己的通知設定（要不要在作業完成時通知、走哪些管道）|
| `/api/my/notify` | POST | 存自己的通知設定 |
| `/api/my/inbox` | GET | 站內通知列表（未讀優先）|
| `/api/my/inbox/seen` | POST | 標記已讀（`id` 或 `all=1`）|

> 這四支**刻意不列入 Bearer API**：它們回傳的是「這個登入者的」資料，
> 拿 token 呼叫沒有意義（token 綁的是使用者，但這些端點的設計是給瀏覽器用的）。

---

## 15. 變更歷史

API 介面遵循 SemVer：minor 版本（如 1.4.x → 1.5.x）保證**後相容**；major 版本（1.x → 2.x）才會 breaking。新加 endpoint 不算 breaking。

完整變更紀錄見 [CHANGELOG.md](./CHANGELOG.md)。

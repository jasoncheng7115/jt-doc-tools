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
| `theme` | str | | 版面主題，預設 `classic` |
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
| `stamp_image` | file | ✓ | 印章 / 簽名圖（透明 PNG 佳） |
| `x_mm` | float | ✓ | 左下角 X 位置（mm） |
| `y_mm` | float | ✓ | 左下角 Y 位置（mm） |
| `width_mm` | float | ✓ | 寬度（mm） |
| `height_mm` | float | ✓ | 高度（mm） |
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

**每頁獨立位置（placements）**：適合多頁合約 / 續保單這種「每頁簽名位置不同、同一頁要簽好幾處」的情境。

| placement 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `page` | int | ✓ | 0 起算頁碼；超過該 PDF 頁數者自動跳過 |
| `x_mm` / `y_mm` | float | ✓ | 位置（mm） |
| `width_mm` / `height_mm` | float | | 尺寸（mm），預設 30×30 |
| `rotation_deg` | float | | 旋轉角度，預設 `0` |
| `asset_id` | str | | 改用某個共用資產的圖（預設用上傳的 `stamp_image`） |

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

解析一批台鐵 / 高鐵乘車證明 PDF，回結構化 JSON。**只解析、不寫入使用者的清單**。

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

### 管理介面自己用的 XHR 端點

下面這些是**管理頁面自己呼叫的端點**，不是穩定的對外介面：它們的參數與回傳
會跟著頁面改，**不保證相容**。列出來的目的是「這份文件不漏掉任何端點」，
需要自動化的話請優先用上面那些有明確契約的 API。

| 端點 | 方法 | 對應的管理頁 |
|---|---|---|
| `/admin/api/check-latest-version` | POST | 系統狀態 —— 檢查有沒有新版 |
| `/admin/api/upload-limit/probe` | POST | 系統狀態 —— 量反向代理的上傳上限 |
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
| `/workspace/api/list` | GET | 列出自己的檔案（`?accept=pdf,png` 過濾）+ 容量 + 保留時數 |
| `/workspace/api/count` | GET | 檔案數（側欄徽章用） |
| `/workspace/save` | POST | 存檔：`job_id`（伺服器端複製 job 結果）或 `file`（直接上傳 bytes）；僅 PDF / PNG |
| `/workspace/file/{file_id}` | GET | 取檔（`?dl=1` 下載）|
| `/workspace/thumb/{file_id}` | GET | 縮圖（PDF 首頁渲染 / PNG 原圖）|
| `/workspace/delete` | POST | 刪除（`file_id`）|
| `/workspace/rename` | POST | 重新命名（`file_id`、`name`）|

容量額度、單檔上限、保留時數、啟用 / 停用由 admin 在 `/admin/workspace` 設定（全站統一，無個人特例）。

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

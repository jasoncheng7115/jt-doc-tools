# jt-doc-tools 測試計畫

每次發版前都跑 `pytest`。覆蓋以下面向：

> **資安項目已拆到獨立計畫：`TEST_PLAN_SECURITY.md`**（越權 / RBAC / 滲透測試 /
> 源碼掃描 / ZAP）。拆開的理由是執行方式不同 —— 那些項目需要「啟用認證 + 兩個以上
> 帳號 + 攻擊者視角」，判定標準是「拿不到」而不是「功能正常」，混在功能清單裡會被
> 當成一般項目快速帶過。**發版前兩份都要跑完。**

## 0. 全站頁面截圖 —— **每次發版都要逐張目視**（使用者要求，2026-08-28）

> 自動化測試**看不出版面長歪**。`page_visual_check.py` 斷言的是「可見控制項有沒有
> 消失」、樣板守門看的是原始碼形狀 —— v1.14.60 有一張卡片攤成整個視窗寬、左邊
> 壓到側欄底下，這些檢查**全部照樣綠燈**，是使用者截圖回報才發現的。
> 「畫面看起來不對」這一類只有真的用眼睛看才抓得到。

```bash
# 1) 起一個 auth-off 拋棄式實例（先塞幾個使用者 / 群組，空清單看不出版面）
JTDT_DATA_DIR=$(mktemp -d) JTDT_CSRF_DISABLE=1 \
  .venv/bin/python -m uvicorn app.main:app --port 8799
# 2) 抓圖 + 產生接觸表（工具頁 + 一般頁 + **全部管理頁**，從路由表列舉）
.venv/bin/python scripts/page_screenshots.py --base http://127.0.0.1:8799
```

- [ ] **每一張接觸表都真的看過**（`temp/shots/<run>/sheet-*.png`，每張九格附路徑）
- [ ] 沒有卡片超出內容欄 / 壓到側欄 / 整頁水平捲動
- [ ] 沒有元素黏在一起或擠成一欄（該三欄的地方是三欄）
- [ ] 沒有原樣印出來的星號等 markdown 記號、沒有把程式碼當文字顯示
- [ ] 圖示該有的地方有圖示，沒有破圖
- [ ] 管理頁**有資料時**與**空狀態**都看過（兩種版面不同）

> 「跑過腳本」不等於「看過」——截圖存下來沒人看的話，這一節等於沒做。

## 0.6 英文介面 —— **只掃「頁面剛載入」的狀態是不夠的**（使用者要求，2026-09-05）

> **這一節是被打臉之後改寫的。** 第一版只在頁面載入後掃一次，跑出「0 條殘留」，
> 我據此回報「全部翻完」。使用者接著一連截了十幾張圖：對話框、屬性面板、
> 作業清單、通知面板、下拉選單、錯誤訊息、有資料才出現的表格 —— **全是中文**。
> 原因是那些字**在頁面剛載入時根本還不存在**，掃描當然看不到。
> 「掃出 0 條」跟「翻完了」是兩件事，前者只證明「我掃到的那些是乾淨的」。

所以驗收要**三種方法一起用**，缺一種就會有一整類漏掉：

### ① 靜態掃描 —— 看得到「所有分支」，包含永遠沒被觸發的那些

    python tools/i18n_wrap_template_text.py --dry app/       # 樣板文字節點
    python tools/i18n_wrap_js_display.py --dry --broad app static/js   # JS 顯示字串
    python tools/i18n_wrap_html_strings.py --dry app static/js         # 字串裡的 HTML
    python tools/i18n_wrap_template_literals.py app          # template literal 裡的 HTML

- [ ] 四支都回報 **0 條**（有殘留就是還沒包）
- [ ] `pytest tests/test_i18n_catalog.py` 全綠（每個 `tr()` 的鍵都有英文）

**為什麼靜態的不可少**：綁 `0.0.0.0` 才出現的警告列、只有錯誤時才走到的分支、
沒有資料時的空狀態 —— 瀏覽器那一輪不見得會走到，靜態掃描一定看得到。

### ② 瀏覽器逐頁掃 —— 看得到「執行期才生出來的字」

    JTDT_DATA_DIR=$(mktemp -d) JTDT_CSRF_DISABLE=1 uvicorn app.main:app --port 8799
    python tools/i18n_untranslated_scan.py --base http://127.0.0.1:8799

- [ ] 回報 **0 條**
- [ ] **知道它的極限**：只涵蓋「頁面載入後的靜止狀態」。對話框、要點開的面板、
      有資料才出現的表格、送出後的結果區**都不在裡面**。

### ③ 人工逐頁操作 —— 前兩種都涵蓋不到的互動狀態

**這一項不可以用自動化取代**（前兩種加起來仍然漏掉了十幾處，是使用者截圖抓到的）。
每一支工具至少走一次「上傳 → 送出 → 看結果」，並把下列狀態逐一打開：

- [ ] **對話框**：確認、提示、錯誤（`showConfirm` / `showToast` / `alert`）——
      標題、內文、兩顆按鈕都要看
- [ ] **側欄的帳號功能表**：我的帳號、語言、登出
- [ ] **通知面板**：作業完成的那幾列（工具名稱、狀態、時間）
- [ ] **屬性面板 / 工具列**：PDF 編輯器選一個物件之後的右側面板
- [ ] **下拉選單展開後**的每一個選項與分組標題
- [ ] **有資料的表格**：使用者清單、作業清單、檔案用量（空表格看不出問題）
- [ ] **執行中與完成後**：進度文字、耗時、結果區的按鈕與提示
- [ ] **錯誤狀態**：故意送壞檔、超過上限、沒有權限

### 版面（英文比中文寬約 1.7 倍）

- [ ] 欄位標題不可以蓋住輸入框或勾選框（`.form-row > label`，只認直接子層）
- [ ] 按鈕文字不可以折行（`Set default` 折兩行會把整排卡片撐高）
- [ ] 側欄分類名稱只能一行
- [ ] 條列與段落的行距要夠（英文折行後兩行會黏在一起）
- [ ] 登入卡片的輸入框寬度不可以被標題欄擠掉

### 兩條鐵則

- [ ] **繁體中文位元組完全相同**：`python tools/i18n_zh_baseline.py --compare`
      （i18n 不可以動到中文的任何一個位元組；改動是刻意的才重存基準）
- [ ] **不該翻的沒有翻**：品牌名、語言選項本身、欄位標籤同義詞字典、統編資料庫的
      公司名、會計科目規則的關鍵字、頁碼格式（會原樣印進 PDF）、格式預覽的範例、
      要複製去貼的組態檔範例 —— 這些顯示中文才是對的，容器上標 `data-i18n="skip"`。

## 0.7 新增管理頁的收尾清單 —— **這五樣漏一樣就會安靜出事**

v1.15.19 加翻譯對照字典時，完整套件一次紅了四條，**全是這張清單上的東西**，
而且每一條的症狀都不是「報錯」，是**安靜地少一塊**。做新的管理頁時照這張走：

| # | 要做的事 | 漏掉的症狀 |
|---|---|---|
| 1 | 樣式放 **`{% block head %}`** | base.html 沒有 `styles` 這個區塊 —— **Jinja 不會報錯，那段被安靜丟掉**，畫面變成沒有框線的裸表格 |
| 2 | 新設定檔加進 `settings_export.CATEGORIES` | 「設定備份 / 匯入」漏掉它 → 客戶搬機器時**這份設定不見了**，而且要用到才發現 |
| 3 | 所有 `tr()` 的字串補進 `app/i18n/en.json`（含 **JS 裡的**與**從資料算出來的**標籤） | 英文介面下那幾塊是中文 |
| 4 | 側欄項目補**中英搜尋關鍵字** | 管理員搜不到這一頁 |
| 5 | 改了共用函式的**簽章或回傳形狀** → **回頭改測試裡的替身** | 兩天踩兩次：①假函式收不下新參數 → 正式碼的 `except` 吞掉 TypeError → 作業「完成但每筆都是空的」②回傳從 `int` 改成 tuple，`lambda: 0` 的替身讓 11 條測試紅。**改完先 `grep` 測試裡有沒有替身**，不要等完整套件 |

> **這五條都有守門**（`test_template_head_block` / `test_settings_export` /
> `test_i18n_catalog` / `test_i18n_dynamic_labels` / `test_tool_search_keywords`）
> —— 但守門是在**完整套件**才跑到的。做完先跑這幾支，不要等到最後。

---

## 0.5 端到端驗收 —— **驗到「產出的檔案本身」**（使用者要求，2026-09-01）

> issue #51 是這條規則的由來：文件去識別化的地址式子把 `[縣市]` 寫成字面
> `<縣市>`，**大部分縣市的地址從上線起就沒抓到過**，而且完全無聲。
> 那個 bug 在單元層級一眼可見，卻活了很多版 —— 因為**沒有任何測試是「拿一份
> 真的有地址的檔案跑一次，看它最後有沒有被遮掉」**。
>
> 對「會產出檔案」的工具，端點回 200、中間結果有幾筆、畫面顯示成功，
> **都不算驗收**。唯一算數的是**把產出的檔案打開來看內容**。

驗收的最後一步一律是這個形狀：

```
上傳 / 輸入 → 呼叫端點 → 取回產出檔 → 重新打開它 → 斷言內容
              （PDF 重新抽文字、ZIP 列內容、docx 解 XML、圖片算墨水）
```

- [ ] 去識別化類：產出檔裡**抽不到**那幾段個資
      （`tests/test_doc_deident_e2e.py`、`tests/test_text_deident_e2e.py`）
- [ ] 寫字進 PDF 類（表單填寫 / 用印 / 頁碼 / 浮水印）：**算圖數墨水**，
      不可以用 `get_text()` 當通過依據（見 §6.21，v1.14.19 的正式機故障）
- [ ] 轉檔類：產出檔要**真的載得進**目標應用程式（見 pdf-to-slides 那條）
- [ ] 預覽類：預覽與最終產出必須**位元組相同**（見騎縫章那條）

**守門**：`tests/test_output_verification_coverage.py` 只釘死判得準的那條線 ——
兩支去識別化工具的端到端測試要在，且最後一步必須是「把產出取回來、確認那段
個資不在裡面」。其餘工具的輸出層驗收是人的判斷（有些走內部函式驗得更嚴，
例如騎縫章逐頁比對位元組、字型改動一律算圖數墨水），**刻意不用啟發式自動判定**
—— 判太鬆會變成一支永遠綠的假測試，判太緊會把驗得更嚴的工具誤報成缺口。

**人工盤點的已知缺口**（會產出檔案，但目前只驗到端點層；新加功能時優先補）：
`markdown-to-doc`、`office-to-pdf`、`pdf-decrypt`、`pdf-extract-images`、
`pdf-metadata`、`pdf-nup`、`pdf-to-markdown`、`pdf-attachments`。
補完一支就從這裡刪掉一支。

## 1. 自動化測試（pytest）

執行：
```bash
.venv/bin/python -m pytest -q
```

### 1.1 路由 smoke (`tests/test_smoke_routes.py`)
- 所有公開路由（首頁 / healthz / admin 頁 / 每個工具頁）都應回 200
- 回歸：`/tools/pdf-fill/?cid=…` 不能 500（pydantic forward-ref 問題）
- 停用的工具（例：`aes-zip`, `enabled=False`）**不**應註冊路由

### 1.2 PDF 工具端到端 (`tests/test_pdf_tools.py`)
- `pdf-merge` 合併 1+2 頁 → 結果 3 頁
- `pdf-merge` 拒絕單檔
- `pdf-split` mode=each 切 10 頁 → ZIP 內 10 個 PDF
- `pdf-split` mode=ranges `1-3,5,7-` → ZIP 內 3 個 PDF
- `pdf-rotate` 整份 90 度 → 每頁 rotation==90
- `pdf-rotate` 指定頁面 (`3,5`, 180) → 只有 p3/p5 旋轉，其他 0
- `pdf-rotate` **水平鏡射** (mode=flip-h) → 內容翻轉但頁數不變
- `pdf-rotate` **垂直鏡射** (mode=flip-v)
- `pdf-pages` mode=drop `2-4` → 剩 7 頁
- `pdf-pages` mode=reorder `5,4,3,2,1` → 5 頁
- `pdf-pageno` 印頁碼 → 抽取文字確認 `1/2`、`2/2` 出現
- 通用 `/api/jobs/{id}/download-png` → 兩頁 PDF 回 ZIP，內含 2 個 PNG

### 1.3 欄位偵測單元測試 (`tests/test_pdf_form_detect.py`)
- `_normalize` 處理 `**` / `1.` 前綴與 `:`／`：` 後綴
- NFKC 折疊：U+F9F7（compat 立）≡ U+7ACB（canonical 立）
- 簡繁折疊：傳真號碼 ≡ 传真号码
- `_split_multi_colon_span("銀行名稱：     銀行代號：")` 切成兩段
- 同義字索引找得到 `公司名稱` / `duns / 鄧白氏`
- 用 PyMuPDF 動態建 PDF，驗證偵測到 `company_name`
- 印章區排除：`公司章` 同列的 `負責人` 必須被排除

### 1.4 Admin API (`tests/test_admin_apis.py`)
- 轉檔設定：可儲存自訂路徑與 builtin 順序，回讀含新 path
- 公司 profile：建立 → 啟用 → 用 `?cid=` 讀 pdf-fill 200 → 刪除
- 同義詞：POST/save 後 GET 回 200
- **字型管理**：GET `/admin/fonts` 200、`/api/fonts` 列出字型清單
- **LLM 設定**：GET `/admin/llm-settings` 200，預設 `enabled=False`
- **API Token**：可建立/列表/刪除 token；`/api/*` 需帶 bearer

### 1.5 資產與圖像 (`tests/test_assets_and_image_utils.py`)
- 上傳 200x100 PNG → match-aspect 後 width/height ratio ≈ 2:1
- 裁剪右半 (`x=0.5,w=0.5`) → 結果 preset 比例 ≈ 1:1
- `remove_white_background` 對 400x400 白底中間黑方塊 → 自動裁掉空白邊界，輸出尺寸落在 90~130

### 1.6 資產縮圖載入 (`tests/test_asset_thumbnails_resolve.py`)
- 每個已登錄資產的 `/assets/{id}/thumb` 與 `/file` 都回 200（印章/簽名 picker 不破圖）
- 匯出 → 合併匯入（會重新分配 id）後縮圖仍載入得到（防 import 沒同步 file_key/thumb_key → 縮圖 404 破圖,2026-06-27 客戶回報）
- file_key/thumb_key 指向不存在的檔時退回 `{id}.png`

### 1.7 授權邊界 (`tests/test_authz_boundaries.py`)
- **垂直越權**:已登入的非 admin 一般使用者 → 所有 /admin/* 頁 + admin 寫入（改站名/關認證/列使用者/建 token）一律非 200（401/403/302）
- **工具權限**:default-user 沒有的工具（pdf-fill/pdf-stamp）UI 與後端動作端點都擋；有的（pdf-merge）可用
- **水平越權**:B 使用者不可下載 A 的工作區檔（/workspace/file/{id}）與 A 的上傳檔（/tools/pdf-editor/file/{upload_id}）

### 1.8 使用者工作區 (`tests/test_workspace.py` + `tests/test_workspace_api.py`)
核心（`workspace.py`）：
- 存 PDF / PNG → meta 正確（ext / mime / 顯示名）；list 回該使用者的檔
- PNG 以 magic bytes 偵測（檔名沒 .png 也自動補副檔名）
- 非 PDF/PNG（zip 等）→ `UnsupportedType`
- get / rename / delete CRUD 正常；刪除後 get 回 `NotFound`
- **跨使用者隔離**：bob 拿 alice 的 file_id → `NotFound`；list 互不可見
- 每人容量額度超過 → `QuotaExceeded`；單檔上限超過 → `QuotaExceeded`
- **停用** → save 回 `WorkspaceDisabled`、list 回空（功能完全隱藏）
- 認證 OFF → 單一共用工作區 key `__single__`，仍可存取
- 保留掃描 `sweep_older_than`：backdate 後掃掉過期項
- 設定 save/get roundtrip（enabled 為布林）

端點（`workspace_routes.py`，auth OFF / 單機）：
- `GET /workspace` 頁面 200、含「我的工作區」
- save → list → file(serve) → delete 一輪；serve 回 `application/pdf`
- save 非 PDF/PNG → 400
- `?accept=png` 過濾掉 PDF
- **停用時** `/workspace`、`/workspace/save`、`/workspace/api/list` 全回 404

### 1.9 乘車證明整理（`tests/test_transit_proof_parser.py` + `tests/test_transit_proof_api.py`）

- 解析器：高鐵電子車票證明（label：value）+ 台鐵購票證明（打散版面用特徵正則）；日期正規化 ISO、乘車日排除印製日期、乘車區間抽起訖時間 / 站名、車種不被「乘車區間」誤匹配、高鐵站名去「高鐵 / 車站」；非乘車證明 / 空欄位 → ParseError。
- 端點：頁面渲染、上傳解析 + 票號去重、非乘車證明 PDF 進 failed、7 種格式匯出（csv/xlsx/ods/json/xml/txt/md）+ 非法格式 400 + 空清單 400、CSV 預設 4 欄（日期/交通工具/來源-目的/費用）、設定 roundtrip（勾選 / 順序 / 格式 / 匯出標題）套用到匯出、刪除單筆、對外 API 不寫 buffer。
- **手動驗收**：拉多張台鐵 + 高鐵 PDF → 表格出現 4 欄 + 底部加總；「設定」加欄位 / 改格式 / 排序 → 表格與匯出同步；各格式下載可開。合成 PDF 測試須用 CJK 字型（`fontname="china-t"`）否則抽文字變 notdef。

### 1.10 目錄瀏覽 filter（`tests/test_dir_filter.py` + `tests/test_directory_filter_api.py`）

- 純函式：規則 → LDAP filter（類型→objectClass、名稱關鍵字 escape_filter_chars 轉義、多欄位）；符合物件 → 剪枝樹（祖先鏈、共用祖先合併去重、matched 旗標、parent 排在 child 前、cycle-safe、無 root 停在 DC 層）。
- 設定 roundtrip / 清洗（空規則丟棄、無效類型過濾、無效 default_mode 忽略）。
- 端點：`/directory/filter` GET/POST roundtrip（backend-agnostic）；`/directory/selected` 非目錄後端回 400；目錄頁可渲染。
- **手動驗收（需 LDAP / AD）**：進 /admin/directory → 預設「已選定」模式；設定 filter 加規則（名稱關鍵字 + 類型 + OU 子樹）→ 儲存 → 樹只留符合分支；切「全部」看完整目錄樹；點 OU 指派角色仍正常。

### 1.11 每頁畫面 + 關鍵元素可見性回歸（`scripts/page_visual_check.py`）

**目的**：抓「元素 / 功能靜默消失」這一類 regression（例：v1.12.30 CSP 樣式重構
讓「下載」按鈕、臨時資產縮圖、個資限用章預覽在存檔 / 選圖後一直不顯示，
v1.12.71 修）。純像素比對對字型 / 時間戳 / 動態內容太吵，所以主檢查是
「可見互動元素清單」比對 + 關鍵狀態斷言，截圖僅供人工對照。

**需要**：headless chromium（開發機上是 `chromium-browser`）+ 一個 auth-off 本機實例。

**跑法（發版前）**：
```bash
# 1) 起 auth-off 實例（臨時 data dir）
JTDT_DATA_DIR=$(mktemp -d) JTDT_CSRF_DISABLE=1 \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8799 &
# 2) 比對（有元素消失就 exit 1）
.venv/bin/python scripts/page_visual_check.py --base http://127.0.0.1:8799
# 3) UI 有意改動後才更新 baseline
.venv/bin/python scripts/page_visual_check.py --base http://127.0.0.1:8799 --update
```

**檢查內容**：
- 逐一載入 39 個工具落地頁 + 首頁 + 工作區，擷取「可見互動元素清單」
  （可見按鈕文字 / 輸入 / 上傳區；用 `offsetParent` + computed `display` 判定
  「真的看得到」，能抓 CSS 規則造成的隱藏）。
- 與 baseline（`tests/visual/baseline_inventory.json`，進版控）比對：baseline 有、
  現在不見的可見按鈕 → **FAIL（功能消失）**；控制項數量下降 → warn。
- 每個工具落地頁至少要有一個可見的互動控制項，否則 FAIL。
- **關鍵狀態斷言**：pdf-editor 從工作區載入 → 儲存並預覽 → `#btnDownload` 必須
  可見（直接守住 download-after-save 這類「動作後才出現」的元素）。
- 截圖 + 清單存 `temp/visual/<run>/`（gitignore，供人工前後對照）。

**已驗證能抓到**：① 在 baseline 塞假按鈕 → 比對報「消失的可見按鈕」；
② 還原 v1.12.71 下載鈕修法（重現 bug）→ 報「存檔後下載按鈕仍不可見」。

### 1.12 缺中文字型提示 (`tests/test_cjk_font_notice.py`)

- 偵測層：挑得到黑體 / 只挑得到明體 → ok；兩種都挑不到 → 帶回 `sys_deps`
  那一份的安裝指令（`font_health.py` 內不可自己寫死 apt 指令）
- 偵測炸掉時 Jinja global 回 ok=True（**寧可安靜，不可誤報**）
- **自動列舉**會把中文畫進 PDF 的工具（`ast` 看 import，不掃註解），
  模板少 include 提示元件就 FAIL —— 已用「拿掉浮水印的 include」驗過會紅
- 字型齊全 → 頁面上沒有這塊；缺字型 → 管理員看到安裝路徑、一般使用者
  看到「請聯絡管理員」且**不出現任何管理區連結**
- `.cjk-warn` 樣式必須在 `platform.css`（元件內不可有 `<style>`）

### 1.13 四種登入方式的實機驗證（`temp/authtest/verify_logins.py`）

**與 pytest 的差別**：pytest 走 TestClient（ASGI 內部呼叫），LDAP 端是假的；
這支**真的起一個 uvicorn**、**真的對一台 OpenLDAP 做 bind**、**真的送表單帶
CSRF token**，驗的是「使用者按下登入之後會發生什麼」。

**需要**：開發機上的測試目錄（`slapd` + `ldap-utils`，suffix `dc=jtdt,dc=test`，
含 memberof overlay 與 AD 相容屬性的 schema）。

**跑法**：`python temp/authtest/verify_logins.py`（37 項，全部要 [OK]）

| 階段 | 驗到什麼 |
|---|---|
| 本機 | 未登入被擋 / 錯密碼不發 session / 正確密碼發 session / whoami / 登出失效 / 一般使用者只拿到自己角色的工具 |
| LDAP | 真實 bind、JIT 開通、顯示名稱與信箱帶入、memberOf 群組同步、錯密碼與不存在帳號同一訊息、**空密碼被核心擋下**（RFC 4513 未認證 bind） |
| LDAP OU | OU 上指派的角色生效（判準挑**預設角色沒有**的工具）+ 反向對照、搬 OU 後 **DN 換綁**（id 不變、寫稽核 `user_dn_rebind`） |
| AD | 以 sAMAccountName 登入、來源標記 `ad`、大小寫不敏感、`userAccountControl` 三態 |
| SSO (OIDC) | 登入頁列出提供者、導向 IdP、state/nonce、回呼驗簽換 token 發 session、以 `sub` 當識別碼、**偽造 state 被拒** |

SAML 由 `tests/test_sso_saml_e2e.py` 涵蓋（自架 IdP、真 xmlsec 簽章，含竄改 /
換錯金鑰 / 重放三種攻擊路徑）。

**踩過的坑**：①目錄是**持久**的，腳本要自己把搬走的帳號放回去，否則第二次跑
會出現三條假失敗；②OU 的 subject key 是**精確字串比對**，要用目錄實際回傳的
大小寫（管理介面指派時寫的也是目錄回來的那一份）。

### 1.98 資料庫 schema 遷移 —— 每一支都要有「舊資料升上來」的測試 🆕 v1.14.95

**全新資料庫升級測不到災難** —— 表本來就是空的，怎麼搬都不會少東西。
v1.12.0 的 `_m8` 就是這樣過關的：它重建 `users` 表時沒關外鍵，
`DROP TABLE` 的隱含 DELETE 觸發子表的 ON DELETE CASCADE，
**把 `group_members` 與 `sessions` 整個清空**；要「先塞舊版資料再升級」
才驗得出來。

- [ ] **每一支 `_m*` 都要有一份「先建舊版結構 + 塞資料 → 跑升級 → 資料還在、
      形狀正確」的測試**（`tests/test_auth_db_migration_v8.py` 是範本）。
- [ ] **重建表一律 `PRAGMA foreign_keys=OFF; … ; ON;`**（migrate 連線是 autocommit，
      pragma 要放在 executescript 內才生效）。
- [ ] **授權 backfill（`_mN_grant_*`）**：拿一份**舊版本時代建立**的資料庫升上來，
      內建角色要拿得到新工具 —— 這條漏掉的症狀是「新工具對老客戶永遠不出現」，
      而且完全無聲（v1.14.17 抓到 `transit-proof` / `pdf-border` 兩支）。
- [ ] **索引類（`_m24`）驗的是查詢計畫**不是速度：`tests/test_db_query_plans.py`
      要求 SQLite 的計畫**不可以出現 SCAN**。功能測試看不出這種缺陷
      （功能完全正確，只是資料量大時慢）。
- [ ] 升級**不可以卡住啟動**：大表加索引要能在合理時間內做完，或放到背景。

- **`app/core/auth_db.py`**：`_m1_initial`、`_m2_username_source_unique`、`_m3_rename_pdf_diff_to_doc_diff`、`_m4_grant_image_to_pdf`、`_m5_grant_translate_doc`、`_m6_totp_columns`、`_m7_audit_seed_column`、`_m8_sso_sources`、`_m9_role_seed_snapshot`、`_m10_role_default_for_new`、`_m11_group_sync_cache`、`_m12_unprovision_mirrored_users`、`_m13_grant_pdf_to_slides`、`_m14_user_email`、`_m15_directory_presence`、`_m16_session_last_seen`、`_m17_directory_account_state`、`_m18_grant_transit_proof_and_border`、`_m19_grant_pdf_bookmark`、`_m20_grant_seam_stamp`、`_m21_grant_page_size`、`_m22_grant_office_convert`、`_m23_canon_ou_subject_keys`、`_m24_index_group_members_user`、`_m25_grant_doc_translate`
- **`app/core/audit_db.py`**：`_m1_initial`
- **`app/core/job_store.py`**：`_m1_initial`、`_m2_metrics`、`_m3_started_at`

### 1.99 全部自動化測試一覽 🆕 v1.14.95

上面 §1.1 起是**逐項寫出驗收內容**的重點測試。但 `tests/` 底下實際有兩百多支，
2026-09-04 稽核發現**測試計畫只提到其中 96 支** —— 另外一百多支等於沒有出現在
發版門檻的視野裡：它們照跑，可是「這支在守什麼」沒有人看得到，要判斷某個功能
有沒有被守住只能自己去翻程式。

所以這裡列全。**說明直接取自每支測試檔自己的開頭說明**（不是另外寫一份），
改了程式說明就跟著變，不會漂。守門 `tests/test_test_plan_coverage.py` 會確認
每一支測試檔都在這張表裡。

<!-- BEGIN test-index (由 tools/build_test_plan_index.py 產生，不要手改) -->

共 **245 支測試檔**。說明取自每支檔案自己的開頭說明，
跑 `python tools/build_test_plan_index.py` 重建。

> 這裡**刻意不列函式數** —— 那個數字每加一條測試就會變，
> 會讓「一覽表過期」的守門在每次寫測試時都紅一次（純噪音）。
> 要看實際跑了幾項看 pytest 的結尾摘要；README 的徽章另有守門。

| 測試檔 | 守的是什麼 |
|---|---|
| `test_ad_account_state.py` | AD 端的帳號狀態：已停用偵測 + 密碼到期預警 |
| `test_ad_ou_move.py` | AD / LDAP 帳號搬 OU（DN 改變）後要能繼續登入（issue #47） |
| `test_ad_primary_group.py` | AD 的「主要群組」（primaryGroupID）也要算成使用者的群組 |
| `test_addr_pattern_coverage.py` | 台灣地址的涵蓋率（GitHub issue #51） |
| `test_admin_apis.py` | Admin API regression tests. |
| `test_admin_exception_leak.py` | 管理區不可把例外原文吐到畫面上（CodeQL py/stack-trace-exposure） |
| `test_admin_form_styles.py` | 管理區的設定頁要用同一套表單樣式 |
| `test_admin_picker_css.py` | admin 角色/群組 picker 的長名稱不可溢出重疊（2026-06-30 客戶回報） |
| `test_admin_privacy_boundary.py` | 管理員的隱私界線要是**一份**政策（F10，v1.15.28） |
| `test_admin_users_table.py` | 使用者清單的欄位索引與排序型別要對得起來 |
| `test_api_doc_contract.py` | API 文件契約回歸測試 |
| `test_api_doc_coverage.py` | 每個工具的 API 都要在 `github/API.md` 與 `TEST_PLAN.md` §4 出現 |
| `test_api_gate_and_csrf_edges.py` | API token 閘與 CSRF 豁免的邊界 |
| `test_api_page_builder.py` | `github/build-api-page.py` 產出的 api.html 不可以毀損 |
| `test_asset_image_acl.py` | ACL test for the login-gated shared-asset image endpoints (GitHub #28). |
| `test_asset_thumbnails_resolve.py` | 資產縮圖必須載入得到 — 防「import 後 file_key/thumb_key 與磁碟檔名不一致 |
| `test_assets_and_image_utils.py` | Asset upload + crop + match-aspect + remove-bg auto-crop. |
| `test_audit_forward_framing.py` | 稽核轉送的訊框格式（外部稽核 F07，v1.15.28） |
| `test_audit_forward_per_destination.py` | 稽核轉送：每個目的地各自一個游標、失敗不前移、不自我餵食（F08，v1.15.28） |
| `test_audit_timezone.py` | 稽核 / 上傳記錄的時間解讀必須與畫面一致（GitHub issue #48） |
| `test_auditor_readonly.py` | 稽核員必須是唯讀角色 —— 而 admin 不該因為隱私規則而失去管理能力 |
| `test_auth_db_migration_v8.py` | Regression: auth_db migration v8 (SSO sources) must NOT wipe data. |
| `test_auth_ldap_security.py` | LDAP / AD 登入路徑資安回歸測試（審查後補） |
| `test_auth_ldap_sync.py` | Unit tests for auth_ldap._sync_user — collision behaviour. |
| `test_auth_local.py` | Tests for app.core.auth_local (local credential auth + lockout). |
| `test_auth_middleware.py` | Tests for the auth middleware (gate that requires session when auth on). |
| `test_auth_modes_matrix.py` | 認證「開 / 關」兩種模式下的全功能矩陣（發版必跑） |
| `test_auth_routes.py` | End-to-end tests for the auth HTTP layer. |
| `test_auth_settings.py` | Tests for app.core.auth_settings (backend selection + bootstrap). |
| `test_auth_settings_fail_secure.py` | 認證設定讀不到的時候，**不可以無聲地把認證關掉** |
| `test_authz_boundaries.py` | 登入後的授權邊界測試（2026-06-27 使用者要求）： |
| `test_autosave_reason_coverage.py` | 自動存入工作區的每一個失敗原因，畫面上都要有對應的說法 |
| `test_background_capability.py` | 哪些工具支援背景作業 —— 一律**推導**，不維護清單 |
| `test_badhost_path_gate.py` | Regression test for the Starlette BADHOST path-poisoning bypass |
| `test_boxed_digits_and_sublabel.py` | 兩種讓欄位「有偵測到卻填不進去」的版型 |
| `test_broken_input_no_500.py` | 任何工具端點收到壞輸入都不可以回 500 |
| `test_cjk_font_notice.py` | 缺中文字型時，**一般使用者**在工具頁上看得到提示（v1.14.47） |
| `test_cjk_font_renders.py` | 寫進 PDF 的中文**必須畫得出來** |
| `test_cli_data_dir_ownership.py` | 以 root 寫資料目錄的 CLI 指令，收尾**一定要把擁有者改回去** |
| `test_cli_health_check.py` | `jtdt update` 的健康檢查要探對地方，失敗要說得出原因 |
| `test_cli_update_rollback.py` | 升級失敗時要真的回復，而且訊息要說出實際結果（外部稽核 F03，v1.15.28） |
| `test_client_ip_audit.py` | Client-IP resolution for audit / history / display — app/core/client_ip.py. |
| `test_cookie_flags_on_delete.py` | 刪除 cookie 的回應也要帶安全旗標 |
| `test_cpu_limit.py` | CPU 限制（轉檔不影響網頁回應）的測試 |
| `test_cpu_simd_probe.py` | CPU SIMD 指令集偵測 + sys-deps PyMuPDF 條目測試 |
| `test_csp_nonce.py` | CSP nonce 靜態回歸測試（Phase 1：script-src 移除 'unsafe-inline'） |
| `test_csrf.py` | CSRF middleware（app/core/csrf.py）單元測試 —— 直接以 ASGI 呼叫 middleware， |
| `test_csv_injection.py` | 匯出的 CSV 不可以讓試算表把內容當公式執行（CSV / 公式注入，CWE-1236） |
| `test_db.py` | Tests for app.core.db (SQLite layer). |
| `test_db_health.py` | SQLite 完整性檢查、熱備份與復原 |
| `test_db_query_plans.py` | 熱路徑的 SQL 不可以整表掃描 |
| `test_declared_dependencies.py` | `app/` 直接 import 的第三方套件，**一定要宣告成相依** |
| `test_deident_label_not_value.py` | 跨格配對時，欄位標籤不可以被當成值（GitHub issue #50） |
| `test_deident_replace_mode.py` | 文件去識別化的第三種模式：替換 |
| `test_dependency_declaration_sop.py` | 新增 Python 相依時的六處宣告，一處都不能漏 |
| `test_dependency_declarations_agree.py` | 三份相依宣告必須互相對得上（外部稽核 F12，v1.15.30） |
| `test_dir_filter.py` | 目錄瀏覽「已選定」模式 filter 的純函式 + 設定測試 |
| `test_directory_browser.py` | 目錄瀏覽（AD/LDAP OU treeview → 指派權限給 OU，2026-07-01） |
| `test_directory_cleanup.py` | 批次停用「目錄已無 / AD 端已停用」的帳號，以及排程自動停用 |
| `test_directory_filter_api.py` | 目錄瀏覽「已選定」filter 端點整合測試（auth OFF = 單機 admin） |
| `test_directory_presence.py` | 目錄裡已經找不到的帳號要看得出來（離職 / 停用偵測） |
| `test_directory_role_assign.py` | 目錄瀏覽：指派角色給**單一使用者**與**群組**（原本只能指派給 OU） |
| `test_directory_schema_matrix.py` | 目錄查詢要能在 **AD / OpenLDAP / UCS** 三種結構上都跑得起來 |
| `test_directory_sync.py` | Scheduled AD/LDAP directory sync + the perf fixes it enables (v1.12.67). |
| `test_doc_deident_e2e.py` | 文件去識別化：**走完整條路徑**的驗收（issue #50 / #51） |
| `test_doc_deident_english.py` | 英文文件的去識別化（第 2 批，v1.15.32） |
| `test_doc_deident_english_e2e.py` | 英文文件去識別化的端到端（v1.15.32） |
| `test_doc_deident_image_residue.py` | 去識別化必須把**圖片裡的**個資也刪掉（外部稽核 F01，v1.15.28） |
| `test_doc_deident_table_labels.py` | 標籤與值分屬兩個表格儲存格時也要偵測得到（GitHub issue #43） |
| `test_doc_diff.py` | Tests for the renamed 文件差異比對 tool (formerly pdf-diff). |
| `test_doc_translate.py` | 文件翻譯：產出**同格式、同版面**的檔案 |
| `test_doc_translate_spreadsheet_view.py` | 試算表翻譯的兩件事：預覽要看得到東西、產出要開在內容的開頭 |
| `test_docs_english_pages.py` | 介紹站與 API 手冊的英文版（GitHub Pages） |
| `test_docs_links.py` | 介紹網站與 API 手冊的連結不可以指向不存在的東西 |
| `test_docs_numeric_claims.py` | 公開文件裡的數字宣稱要跟程式對得上 |
| `test_docs_tool_categories.py` | 介紹站的工具分類要跟程式裡的一致 |
| `test_docx_textbox_translation.py` | 含**文字方塊**的 .docx 翻譯 —— 同一段文字會被收好幾次 |
| `test_effective_permissions.py` | 「這個人最終有哪些工具、從哪來」的檢視 |
| `test_einvoice_formatters.py` | Tests for einvoice-scan field formatters (M3.2). |
| `test_einvoice_scan.py` | Tests for einvoice-scan tool — QR parser, buffer storage, HTTP endpoints. |
| `test_error_message_scrub.py` | 錯誤訊息不可以把使用者送的字串原樣吐回去 |
| `test_extract_text_glyph_repair.py` | 壞掉的文字對應表：擷取文字 / 字數統計 / 逐句翻譯也要能還原 |
| `test_font_display_names.py` | 自訂上傳字型的顯示名稱 |
| `test_format_terminology.py` | 格式用語要一致：「辦公文件」是統稱，「文書檔」是其中一類 |
| `test_forwarded_proto.py` | `X-Forwarded-Proto` 的解析要全站一致 |
| `test_generated_css_valid.py` | `generated-inline.css` 裡不可以出現 JavaScript 運算式 |
| `test_glyph_text_recovery.py` | 從字形反查還原文字 —— 對付壞掉的 ToUnicode 對照表 |
| `test_heic_support.py` | HEIC / HEIF（iPhone 照片）要真的解得開（GitHub issue #49） |
| `test_host_stats_container.py` | 系統狀態 CPU 在容器(LXC/Docker)內要顯示容器自己的用量，不抓宿主機 |
| `test_i18n_catalog.py` | 語系檔與樣板的一致性守門 |
| `test_i18n_dynamic_labels.py` | 程式端產生的顯示字串（`tr(變數)`）也必須有英文 |
| `test_id_from_body_acl.py` | 「id 由使用者傳入」的端點一律要有 ACL —— 靜態全面掃描 |
| `test_installer_languages.py` | Windows 安裝程式在英文 Windows 上要顯示英文（v1.15.27） |
| `test_installer_product_name.py` | Windows 安裝程式的產品名稱多語系 + Linux 服務的安全強化（第 1 批，v1.15.31） |
| `test_job_acl.py` | Regression tests for the /api/jobs/* per-job ownership ACL (v1.12.61). |
| `test_job_api_acl.py` | 「我的工作」/ 管理區工作監控的 API 與權限邊界 |
| `test_job_autosave.py` | 作業完成後自動存入工作區 |
| `test_job_cancel.py` | Tests for job cancellation (停止轉換). |
| `test_job_id_acl.py` | 換掉 job id 能不能看到別人的作業？ |
| `test_job_manager_cancel_release.py` | 取消 / 清理之後不可以留著執行函式（外部稽核 F05，v1.15.28） |
| `test_job_png_export.py` | PNG 匯出：不整份堆記憶體、暫存要有人清、要有併行上限（F09，v1.15.28） |
| `test_job_priority.py` | 優先派送名單 —— 指定的使用者送出的作業會插到佇列最前面 |
| `test_job_queue.py` | 背景工作的佇列 / 持久化 / 記憶體准入 |
| `test_job_timestamps.py` | 作業的三個時間點：送出 / 開始 / 結束 |
| `test_json_error_handling.py` | 非 JSON / 壞掉的 request body 應回 400（而非 500） |
| `test_latin_ext_garbled_recovery.py` | 擷取結果被映到拉丁擴充區、而且每個 span 都很短 —— 舊的判準抓不到 |
| `test_ldap_attribute_portability.py` | LDAP 查詢的屬性清單不可以夾帶 AD 專屬屬性 |
| `test_ldap_failover.py` | 多台 DC 容錯與連線逾時 |
| `test_license_declaration.py` | 本專案宣告的授權必須處處一致（v1.14.48 起改為 AGPL-3.0-or-later） |
| `test_llm_per_field_consensus.py` | LLM 逐欄校驗：連兩輪都指出同一個問題才採納 |
| `test_llm_stream_deadline.py` | 串流回應要有**整次生成的上限**，不是只有每個 chunk |
| `test_llm_url_ssrf.py` | SSRF defence — admin-supplied LLM base URL must reject suspicious schemes |
| `test_looks_garbled.py` | Regression tests for pdf_editor._looks_garbled(). |
| `test_migration_fk_cascade.py` | 重建資料表的 migration 一律要關掉外鍵，否則升級會**清空子表** |
| `test_nav_visibility_and_whoami.py` | Tests for v1.1.5 - v1.1.7 visibility / identity changes. |
| `test_nested_group_permissions.py` | 巢狀群組的權限要往上繼承 |
| `test_net_ssl_corp_tls.py` | 企業 TLS 攔截環境的 Python 端信任修正（2026-06-30 客戶回報） |
| `test_new_tools_input_boundaries.py` | 三支新工具（書籤與目錄 / 騎縫章 / 頁面尺寸統一）的輸入邊界 |
| `test_no_blocking_endpoints.py` | async 端點裡不可以直接做重活 —— 那會把整站鎖住 |
| `test_no_dynamic_style_injection.py` | 前端 JS 不可以動態注入 `<style>` —— CSP 會把它整段擋掉 |
| `test_no_native_dialogs.py` | 樣板裡不可以用瀏覽器原生的 alert / confirm / prompt（使用者要求） |
| `test_no_sample_names_in_public.py` | 測試樣本的檔名 / 客戶公司名不可以出現在會公開的檔案裡 |
| `test_no_tr_shadowing.py` | `tr` 是表格列最自然的變數名，也是前端翻譯函式的名字 —— 撞名會讓整段 JS 當場死掉 |
| `test_notify.py` | 作業完成通知：管道發送、設定分層、觸發條件 |
| `test_notify_privacy.py` | 通知送出去的內容不可以外洩多餘的東西 |
| `test_notify_settings_form.py` | 通知設定頁的兩件事：**存進去的值不可以被自動帶值蓋掉**、欄位要看得到內容 |
| `test_ocr_avx2_guard.py` | 本機 EasyOCR 在缺 AVX2 的 CPU 上會 SIGILL 打掛整個服務 |
| `test_ocr_server_gpu_select.py` | Unit tests for jt-ocr-server's auto GPU selection (server_template.py). |
| `test_office_convert.py` | 辦公文件格式互轉（office-convert） |
| `test_office_source_validation.py` | 辦公文件的**來源檔**壞掉時，要在送進 soffice 之前就擋下來 |
| `test_online_sessions.py` | 在線人數、某人的登入裝置清單、強制登出 |
| `test_open_redirect.py` | Open-redirect regression — closes CodeQL alerts #14 / #15 |
| `test_ops_iis_prereq_order.py` | IIS 反向代理的安裝順序：**URL Rewrite 要先裝，ARR 後裝。** |
| `test_ou_key_canon.py` | OU 授權的 DN 大小寫 / 空白正規化（v1.14.48） |
| `test_output_verification_coverage.py` | 去識別化類工具**必須**驗到「產出本身」（使用者要求，2026-09-01） |
| `test_owasp_top10.py` | OWASP Top 10 (2025) regression suite. |
| `test_passwords.py` | Tests for app.core.passwords (scrypt hashing + policy). |
| `test_path_traversal_audit.py` | Audit every tool router for unsafe path expressions. |
| `test_pdf_annotations.py` | Tests for the pdf-annotations tool. |
| `test_pdf_annotations_flatten.py` | Tests for the pdf-annotations-flatten tool. |
| `test_pdf_annotations_strip.py` | Tests for the pdf-annotations-strip tool. |
| `test_pdf_attachments_strip.py` | pdf-attachments「產生無附件副本」測試 |
| `test_pdf_bookmark.py` | 書籤與目錄 |
| `test_pdf_border.py` | 頁面加框（pdf-border） |
| `test_pdf_compress.py` | Tests for the pdf-compress tool, focused on transparency preservation. |
| `test_pdf_editor_font_subset.py` | PDF 編輯器寫進去的中文：字形要看得見、檔案不可以是十幾 MB |
| `test_pdf_fill_positioning.py` | 表單自動填寫的定位規則 |
| `test_pdf_form_detect.py` | Unit tests for the field detector. Builds tiny synthetic PDFs in memory |
| `test_pdf_ocr_preview_acl.py` | End-to-end ACL test for pdf-ocr `/preview/{uid}.pdf` endpoint (v1.7.6). |
| `test_pdf_page_size.py` | 頁面尺寸統一 |
| `test_pdf_pageno_cjk.py` | pdf-pageno 中文頁碼字型回歸 |
| `test_pdf_seam_stamp.py` | 騎縫章 |
| `test_pdf_stamp_blend.py` | Regression tests for the Multiply blend mode applied to pdf-stamp output. |
| `test_pdf_stamp_date_resolution.py` | Regression: the handwriting date stamp must render crisp, not blurry. |
| `test_pdf_stamp_pages.py` | Regression tests for pdf-stamp per-page selection (`_resolve_pages`). |
| `test_pdf_stamp_placements.py` | pdf-stamp「每頁獨立位置」placements 模式測試（issue #38 / Phase B） |
| `test_pdf_stamp_rotated.py` | Regression: stamp placement must honour page /Rotate (GitHub #28 follow-up). |
| `test_pdf_to_image_page_order.py` | 辦公文件轉圖片：ZIP 內檔名頁碼必須對應 PDF 實際頁數 |
| `test_pdf_to_office_a_b_fixers.py` | Sprint B 二階段 5 個 fixer 單元測試（v1.8.60）： |
| `test_pdf_to_office_api_engine.py` | 對外 API /tools/pdf-to-office/convert 的引擎參數與 meta 測試 |
| `test_pdf_to_office_bbox_fixers.py` | Sprint B 新 fixer 單元測試： |
| `test_pdf_to_office_c_fixers.py` | v1.8.61 C 階段強化 fixer 測試 |
| `test_pdf_to_office_d_fixers.py` | v1.8.62 D 階段 fixer 測試 |
| `test_pdf_to_office_draw_engine.py` | pdf-to-office 第三引擎 draw（版面重現）測試 |
| `test_pdf_to_office_jtdt_reform.py` | v1.8.63 jtdt-reform engine 單元 + 端對端測試 |
| `test_pdf_to_slides.py` | pdf-to-slides（PDF 轉簡報）測試 |
| `test_pdf_tools.py` | End-to-end tests for the simple PDF tools (merge / split / rotate / pages / |
| `test_pdf_watermark.py` | Tests for the watermark service — focused on CJK font fallback. |
| `test_pdf_watermark_batch.py` | pdf-watermark 逐檔順序上傳（issue #27） |
| `test_pdf_wordcount.py` | Tests for the pdf-wordcount tool. |
| `test_placeholder_extraction.py` | 擷取出來全是佔位字元（圓點 / 星號…）但畫面上其實是真的字 |
| `test_preview_acl_failopen.py` | 預覽端點的 ACL 不可以「認不出 upload_id 就放行」 |
| `test_preview_is_not_the_result.py` | **預覽只有前幾頁時，畫面一定要講出整份有幾頁。** |
| `test_preview_page_range.py` | 縮圖 / 預覽的頁碼超出範圍要回 4xx，**不可以 500** |
| `test_proxy_scheme_mismatch.py` | 代理宣稱的協定 ≠ 瀏覽器實際的協定（客戶回報，v1.15.26） |
| `test_proxy_sso.py` | Reverse-proxy (Kerberos/SPNEGO) SSO — app/core/proxy_sso.py + middleware. |
| `test_real_samples_smoke.py` | 拿**真實的**樣本檔掃過所有吃單一 PDF 的工具 |
| `test_redos_ad_dn.py` | ReDoS regression for RE_AD_DN — closes CodeQL alert #13 |
| `test_restrict_stamp_render.py` | 個資限用章的渲染 —— 橫式 / 直式 / 對角線 |
| `test_retention_periods.py` | 檔案保留期：**設定頁上的每一個數字都要真的生效** |
| `test_roles.py` | Tests for app.core.roles. |
| `test_roles_default_and_seed.py` | Tests for the new-user default role + seed-snapshot behaviour (v1.12.53). |
| `test_roles_rbac.py` | 內建角色（RBAC）的完整性檢查 |
| `test_safe_paths_and_owner.py` | Tests for app.core.safe_paths and app.core.upload_owner. |
| `test_same_as_ref.py` | 把「同上」「同登記地址」展開成實際內容 |
| `test_save_queue.py` | Tests for app.core.save_queue (v1.7.17). |
| `test_scan_merge_api.py` | 掃描拼合 (scan-merge) — 端點 / ACL / 公開 API 測試 |
| `test_scan_merge_detector.py` | 掃描拼合 — 內容偵測 + 背景淨白 單元測試 |
| `test_scheduled_export.py` | Scheduled settings export (v1.12.54). |
| `test_seal_zone_marker.py` | 用印區的排除條件：**標籤才算，說明句不算** |
| `test_seam_preview_speed.py` | 騎縫章預覽：只蓋要看的那一頁 |
| `test_seed_bootstrap_gap.py` | 新工具要真的到得了**既有客戶**，不是只有全新安裝看得到 |
| `test_sessions.py` | Tests for app.core.sessions (issue / lookup / revoke). |
| `test_settings_export.py` | Category-based settings export / import (v1.12.54). |
| `test_settings_export_roundtrip.py` | 設定備份：**匯出的檔案要匯得回去** |
| `test_signpath_notes_are_private.py` | SignPath 的往來筆記不可以出現在公開版（v1.15.27） |
| `test_smoke_routes.py` | Smoke tests: every public page renders 200, no 500s. |
| `test_smtp_relay_modes.py` | 通知信的三種寄送方式 |
| `test_sso.py` | Tests for the SSO feature (OIDC + SAML): settings encryption, JIT |
| `test_sso_oidc_e2e.py` | Real end-to-end OIDC login against a self-hosted, spec-conformant mini IdP. |
| `test_sso_saml_e2e.py` | Real end-to-end SAML login with a genuinely signed SAML Response. |
| `test_stamp_watermark_preview_acl.py` | End-to-end ACL test for pdf-stamp / pdf-watermark preview endpoints (#28 pt2). |
| `test_static_image_budget.py` | 自家的介面圖片不可以大到離譜（v1.14.61） |
| `test_submission_check_acl.py` | 送件檢核（submission-check）的案件 ACL 測試 |
| `test_taiwan_terminology.py` | 使用者看得到的文字不可以用中國大陸用詞 |
| `test_template_block_placement.py` | 兩個「看不到 JS 例外、只有畫面怪怪的」樣板雷的守門 |
| `test_template_head_block.py` | 工具模板的 `<style>` 一定要放在 base.html 真的有的區塊裡 |
| `test_template_js_syntax.py` | Inline-JS syntax check for every Jinja2 template (v1.7.14). |
| `test_template_renders.py` | 每一支模板都要**渲染得起來**，而且註解裡不可以寫出樣板標籤的字面寫法 |
| `test_test_plan_coverage.py` | 測試計畫本身的守門：計畫沒涵蓋到的東西要紅燈 |
| `test_text_deident_e2e.py` | 文字去識別化：走完整條路徑的驗收 |
| `test_text_diff.py` | Tests for the new 文字差異比對 tool — paste-text variant of doc-diff. |
| `test_text_list.py` | Tests for text-list tool — pipeline ops, file extraction, export formats. |
| `test_tool_search_keywords.py` | 每一支工具都要有搜尋關鍵字（中文 + 英文） |
| `test_tool_ui_locales.py` | 工具的介面語系白名單（`ToolMetadata.locales`） |
| `test_transit_proof_api.py` | 乘車證明工具端點整合測試（合成 PDF，auth OFF = 單機） |
| `test_transit_proof_files.py` | 乘車證明的**原始檔**：存得下、看得到、別人拿不到、刪掉就不見 |
| `test_transit_proof_parser.py` | 乘車證明解析器單元測試（合成 fixture，不含真實票號 / 統編 / 站名資料） |
| `test_translate_doc_job.py` | 逐句翻譯改成背景作業（離開頁面也會繼續跑） |
| `test_translate_doc_pagination.py` | 逐句翻譯：admin 可設定句數上限 + 分頁大小，前端分頁 |
| `test_translation_glossary.py` | 翻譯對照字典：單位內部的專有名詞怎麼翻（或不要翻） |
| `test_translation_glossary_e2e.py` | 字典在兩支翻譯工具上真的有作用（**驗產出，不驗中間狀態**） |
| `test_ttc_subfont.py` | `.ttc` 要挑對子字型，否則寫進 PDF 的中文是**日文字形** |
| `test_ui_locale.py` | 介面語言切換端點 `/ui-locale` 的安全性（開放重導） |
| `test_update_backup.py` | 升級前的備份：**該留的要留、空間不夠要在停服務之前就擋下來** |
| `test_upgrade_v1_14_6.py` | 升級到 v1.14.6：既有客戶的資料目錄要能無痛接上 |
| `test_upload_limits.py` | 這台機器實際能收多大的檔案 —— 系統狀態頁的「可上傳的檔案大小」 |
| `test_upload_validation_parity.py` | 上傳的檔案不是 PDF 時要回 400，不是 500 |
| `test_url_safety.py` | safe_next open-redirect sanitizer — including the encoded-slash hardening. |
| `test_user_email.py` | 帳號上的信箱欄位（作業完成通知要寄給誰） |
| `test_user_manager.py` | Tests for app.core.user_manager + app.core.group_manager. |
| `test_users_bulk_ops.py` | 使用者批次操作（啟用 / 停用 / 指派角色）與伺服器端分頁 |
| `test_v1_4_99_audit_2fa.py` | v1.4.99 — auditor role + TOTP 2FA + separation-of-duties tests. |
| `test_vat_db.py` | Tests for vat_db (M4.a). |
| `test_vat_upload_and_group_sync.py` | 2026-06-30 客戶回報兩項： |
| `test_version_consistency.py` | Release-time version consistency — every source agrees on `app/main.py:VERSION`. |
| `test_windows_git_guidance.py` | Windows 缺 git 時的指引不可以只講 winget |
| `test_windows_service_restart.py` | Windows 的 `jtdt restart` 必須真的把服務啟起來（2026-08-24 實機重現） |
| `test_workspace.py` | Tests for the user-workspace core (app/core/workspace.py). |
| `test_workspace_api.py` | HTTP-level tests for the workspace endpoints (auth OFF / single mode). |
| `test_workspace_office_thumbnail.py` | 工作區的 Office / ODF 檔要有第一頁縮圖 |
| `test_workspace_ooxml_detect.py` | 工作區的型別判斷要以**內容型別**為準，不是主檔的路徑名 |
| `test_workspace_save_button.py` | 「存至工作區」按鈕出現的條件，必須跟工作區真正收得下的格式一致 |
| `test_workspace_thumb_pending.py` | 縮圖還沒做好時回的那張空白圖，不可以被瀏覽器快取 |
| `test_zip_bomb_guard.py` | zip 炸彈：**每一條讀使用者 zip 的路徑都要擋得住** |

<!-- END test-index -->

## 2. 手動驗收清單（每個版本）

### 2.1 填單用印

#### PDF 表單填寫 (pdf-fill)
- [ ] 上傳廠商 PDF（`temp_pdfs/` 內的真實樣本，四種不同版型）
- [ ] 自動偵測欄位且公司資料正確帶入
- [ ] 切換第二公司不會 500
- [ ] 拖曳藍框微調位置 → 套用新位置
- [ ] 編輯模式 ↔ 合成模式切換
- [ ] 下載 PDF / 下載 PNG 都可用
- [ ] Office 來源（docx/xlsx/odt）自動先轉 PDF 再偵測

#### PDF 用印與簽名 (pdf-stamp)
- [ ] 同時看得到 印章/簽名/Logo 三類資產
- [ ] **所有印章/簽名/Logo 縮圖都實際載入顯示（無破圖）** — 特別是經「匯入（合併/取代）」進來的資產（回歸 2026-06-27 簽名破圖）
- [ ] 上傳檔案後預覽區自動出現，編輯/合成模式可切換
- [ ] 多檔上傳 → ZIP 下載

#### 浮水印 (pdf-watermark)
- [ ] 只列出 type=watermark 的資產（沒有就提示去資產管理上傳）
- [ ] 平鋪填滿 / 指定位置 兩個模式都可用
- [ ] 透明度 / 旋轉 即時預覽
- [ ] 結果 PDF 在閱讀器中無法選取移除浮水印
- [ ] 多檔批次 → ZIP

### 2.2 檔案編輯

#### PDF 編輯器 (pdf-editor) 🆕
- [ ] 上傳 PDF 正確 render（PDF.js 背景 + Fabric overlay）
- [ ] 新增文字框（選字型、字級、顏色、粗體、斜體、底線、旋轉）
- [ ] 字型選單顯示系統 + 內建 CJK + 自訂，不是原生下拉
- [ ] 新增圖片框（從 asset 或直接上傳）
- [ ] 新增形狀 / 白底遮罩 / 螢光筆 / 底線 / 刪除線 / 便箋 / 手繪
- [ ] 點選 canvas 上的既有文字/圖片 → 紅框反白
- [ ] 刪除既有物件（redact 真刪，非浮層蓋）
- [ ] AcroForm widget 刪除（如果 PDF 有表單欄位）
- [ ] vector path / 線條刪除
- [ ] **多選批次改屬性**：Shift+click 多個物件、改字型同時套用
- [ ] **整份換字型**：右側面板按鈕一鍵替換全文字物件字型
- [ ] 復原 / 重做
- [ ] 存檔後重新開啟，物件保留或已 redact（destructive 項目）

#### 合併 (pdf-merge)
- [ ] 2 份以上 PDF 依序合併
- [ ] 單檔拒絕
- [ ] 檔案順序可拖曳調整，產出順序與畫面一致
- [ ] 混合直橫 / 不同尺寸的來源都併得起來
- [ ] 單一檔案也能送出（不強制兩份以上）

#### 分拆 (pdf-split)
- [ ] 每頁一份 / 範圍模式都可用
- [ ] 依頁碼範圍分拆（`1-3,5`）
- [ ] 每頁一檔模式產出的檔名含頁碼且排序正確
- [ ] 多檔產出自動打包成 ZIP
- [ ] 超出總頁數的範圍回 4xx 不是 500

#### 轉向 (pdf-rotate) 🆕 加入鏡射
- [ ] 整份 90/180/270 旋轉
- [ ] 指定頁面旋轉
- [ ] **水平鏡射**（flip-h）內容左右翻轉
- [ ] **垂直鏡射**（flip-v）內容上下翻轉
- [ ] 向量品質保留（非 raster 重繪）

#### 頁面整理 (pdf-pages)
- [ ] 刪除指定頁面
- [ ] 重新排序頁面
- [ ] 保留 / 刪除兩種模式（含 keep / delete 別名）
- [ ] 縮圖可勾選，勾選結果與送出的頁碼一致
- [ ] 全刪時擋下並提示

#### 插入頁碼 (pdf-pageno) 🆕 視覺選位
- [ ] **2×3 位置選擇格**點選直接換位置
- [ ] 格式 chips（1、1/10、第 1 頁、Page 1）
- [ ] 字級 / 邊距滑桿即時調整
- [ ] 顏色選色器
- [ ] 起始頁碼與跳過頁設定
- [ ] 輸出 PDF 頁碼正確

#### PDF 壓縮 (pdf-compress) 🆕
- [ ] 三個預設（無損 / 平衡 / 極限）都能縮小
- [ ] 進階模式：圖片 DPI / JPEG 品質 / 字型子集化 / 移除註解 分別生效
- [ ] 若系統裝 Ghostscript，進階選項可勾選 GS pass
- [ ] 檔案大小比原檔小；文字內容仍可抽取

### 2.3 內容擷取

#### 擷取文字 (pdf-extract-text) 🆕
- [ ] 擷取 → TXT / Markdown / Word / ODT 四種輸出
- [ ] 段落結構（第二輪合併相鄰 block）正確
- [ ] **LLM 重排** 預設關閉；開啟後 progress NDJSON 事件正常流入
- [ ] LLM 處理時按鈕 disable、顯示進度
- [ ] think mode 被關閉（輸出裡沒殘留 `<think>...</think>`）
- [ ] 取消 / 中斷處理

#### 擷取圖片 (pdf-extract-images)
- [ ] 抽出所有嵌入圖片 → ZIP
- [ ] 內嵌圖片逐張抽出，張數與原稿相符
- [ ] 透明 PNG（SMask）抽出來不會變黑底
- [ ] 多張自動 ZIP，單張直接下載
- [ ] 沒有圖片時給明確訊息

#### PDF 附件萃取 (pdf-attachments) 🆕
- [ ] 列出 EmbeddedFiles 清單（含檔名 / 大小）
- [ ] 單檔下載 / 全部打包 ZIP
- [ ] 沒附件時顯示空狀態

#### 多頁合併 (pdf-nup)
- [ ] 2 / 4 / 8 合 1 三種都試，頁序由左而右、由上而下
- [ ] 邊界不裁到字（最外圈留白看得出來）
- [ ] 原稿直橫混排時每一格仍等比縮放不變形
- [ ] 預覽與下載的結果一致

#### 註解擷取 (pdf-annotations)
- [ ] 清單列出作者 / 類型 / 頁碼 / 內容
- [ ] 三種輸出（CSV / JSON / Markdown）都下載得到且欄位對得上
- [ ] 沒有註解的 PDF 給明確訊息，不是空白頁
- [ ] 大量註解（100 筆以上）不逾時

#### 註解平面化 (pdf-annotations-flatten)
- [ ] 平面化後在閱讀器裡**選不到也刪不掉**註解
- [ ] 螢光筆 / 便箋 / 手繪三種都燒得進去
- [ ] 視覺位置與原稿相同（逐頁比對，不位移）
- [ ] 結果訊息講明「無法再編輯，建議保留原檔」

#### 註解移除 (pdf-annotations-strip)
- [ ] 全部刪除 / 依作者 / 依類型 三種模式
- [ ] `/AF` 附件關聯一併清掉（「無附件副本」真的沒有附件）
- [ ] 頁面內容不受影響（文字仍可選取）

#### OCR 文字辨識 (pdf-ocr)
- [ ] 中文影像 PDF 辨識後文字**可選取**，highlight 寬度與字對齊
- [ ] 「停止辨識」即時中止，畫面顯示已停止
- [ ] EasyOCR / Tesseract 兩個引擎都跑得起來；退回時訊息寫明原因
- [ ] 非拉丁語系互斥（勾了繁中就不能同時勾日文）
- [ ] 完成後內嵌 viewer 載得起來

#### 字數統計 (pdf-wordcount)
- [ ] 中英混排的字數與 Word 統計差距在 ±1% 內
- [ ] PDF / docx / odt / txt 四種來源都算得出來
- [ ] 多檔批次有逐檔與跨檔總計
- [ ] CSV 匯出欄位齊全

#### 清單處理 (text-list)
- [ ] 排序 / 去重 / 篩選 / 大小寫 / 取頭尾，操作可疊加
- [ ] 十萬行清單不逾時
- [ ] 上傳檔案與貼上文字兩條路結果一致

#### 文字差異比對 (text-diff)
- [ ] 長行換行後左右兩欄仍逐列對齊
- [ ] 單格複製與左右全文複製都拿得到正確內容
- [ ] 完全相同時明確顯示「沒有差異」

#### 文字去識別化 (text-deident)
- [ ] 貼文字與上傳 .txt / .md / .docx / .odt / .pdf 都能偵測
- [ ] 編修 / 遮罩 / 替換假資料三種模式各跑一次
- [ ] **產出裡抽不到原本的個資**（§0.5 的端到端判準）
- [ ] 假資料模式產生的號碼檢查碼正確（可選「刻意不通過」）

#### 逐句翻譯 (translate-doc)
- [ ] 背景作業模式；關掉分頁再回來用 `?job=` 接得回去
- [ ] 對照表分頁後「複製全文」不漏頁（讀資料不是讀 DOM）
- [ ] 上傳解析中顯示 spinner，不是空白
- [ ] 單句重試鈕有效
- [ ] **翻譯對照字典**：設一條 `Acer→宏碁` 之後，含 Acer 的句子譯文一定是「宏碁」
- [ ] 勾選取消後就**不會**套用（同一句重翻，Acer 保持原樣）
- [ ] 勾選**只在選到的語言對真的有條目時才出現**（換成日文就消失）
- [ ] **三個送出點都要帶旗標**：整批送出、單句重試、背景作業 —— 漏一個的症狀是
      「大部分有效、偶爾沒效」，最難查

#### 文件翻譯 (doc-translate)
- [ ] 九種辦公格式各上傳一份 → 產出**同副檔名**
- [ ] 版面與原稿一致（框線 / 表格 / 頁首頁尾 / 圖片都在原位）
- [ ] 行內的顏色與斜體保留（紅字提示不可變黑）
- [ ] 附前 6 頁預覽
- [ ] 上傳 PDF 要被擋下並說明原因
- [ ] 結果摘要的請求數遠少於段數（批次真的有生效）
- [ ] **翻譯對照字典**：含 `Acer` / `Foxconn` 的文件翻完，**打開產出檔**確認用的是
      指定的譯法（不是看端點回 200）
- [ ] 「不要翻譯」的詞（產品名 / 專案代號）在產出裡**原樣保留**
- [ ] 勾選取消後就不會套用；結果摘要看得到「字典 N 條 / 退回 M 段」
- [ ] **產出裡不可以出現 `⟪1⟫`** —— 全文搜一次，一個都不能有

#### 統編查詢 (vat-lookup)
- [ ] 8 位統編反查毫秒回
- [ ] 名稱 / 地址 / 行業模糊搜尋，1 個字就查得到
- [ ] 台 / 臺異體字互通
- [ ] 統計圖可點下鑽
- [ ] 批次查詢貼一整欄統編

#### 電子發票處理 (einvoice-scan)
- [ ] QR 雙碼解析（左右兩段都要）
- [ ] 手機連續掃描與拍照上傳兩條路
- [ ] 欄位顯示 / 順序 / 格式設定即時套用並跟著帳號走
- [ ] 匯出 CSV / XLSX / ODS / JSON 欄位標題正確
- [ ] 當期發票檢查標出非報帳用與逾期

#### 送件前檢核 (submission-check)
- [ ] 規則 / OCR / LLM 三層可分別開關
- [ ] 案件建立 → 檢核 → 重新檢核（新版本）→ 歸檔整條走完
- [ ] 儀表板只有 admin 看得到
- [ ] 自家實體登錄後不再被誤標「非預期主體」

### 2.4 格式轉換

#### 辦公文件轉 PDF (office-to-pdf)
- [ ] .docx / .xlsx / .pptx / .odt 各轉一份
- [ ] OxOffice 優先（`find_soffice` 命中 OxOffice）
- [ ] 產出頁數與原稿一致，中文不缺字
- [ ] 同時多份上傳時排隊處理不互相干擾

#### 辦公文件轉圖片 (pdf-to-image) 🆕 擴充 Office
- [ ] PDF 每頁 → PNG
- [ ] **Office 檔案（docx/xlsx/pptx/odt）先自動轉 PDF 再轉圖**
- [ ] 單頁直接下 PNG、多頁自動 ZIP

#### 辦公文件格式互轉 (office-convert) 🆕 v1.14.34
- [ ] 上傳 `.odt` → 只顯示文書檔那一組目標；換上傳 `.pptx` → 切到簡報那一組
- [ ] 一次混上傳兩類（.odt + .ods）→ **前端當場擋下**並講出混到哪兩類
- [ ] `.odt` 轉 Word 97–2003：下載鈕顯示「下載 .doc」（不是「下載 PDF」）
- [ ] **同副檔名互轉**（.pptx 選 pptx 目標）要真的轉（soffice 對同目錄同副檔名
      會無聲跳過 —— 核心已改為獨立輸出目錄，`test_office_convert.py` 守著）
- [ ] `.docx` 兩個版本目標（Word 2007 / Word 2010–365）產出的相容模式
      分別是 12 / 15（`zipfile` 開 `word/settings.xml` 看 `w:val`）
- [ ] 多檔 → ZIP；轉完「存至工作區」有出現且存得進去（.xlsx 也要）
- [ ] 跨類（.ods 配 docx 目標）走 API 直打 → 400，不是產出一份壞檔
- [ ] `GET /tools/office-convert/formats` 三個家族都在；缺 Impress 的機器
      簡報家族整組消失（不是留一組永遠轉不出來的）

#### 書籤與目錄 (pdf-bookmark) 🆕 v1.14.20
- [ ] 多檔上傳自動串接，檔名成為第一層書籤；子文件原書籤降一層、頁碼加偏移
- [ ] 貼上目錄文字解析（含頁碼在行尾）；層級不合法時自動 normalize 並逐條回報
- [ ] 頁碼超出總頁數 → 明確擋下（PyMuPDF 預設無聲夾到最後一頁）
- [ ] 插目錄頁：書籤頁碼 / 目錄上印的頁碼 / 目錄連結三者一起位移；
      插入點之前的書籤**不可平移**（封面那筆不能指到目錄自己）

#### 頁面尺寸統一 (pdf-page-size) 🆕 v1.14.20
- [ ] 混合尺寸 PDF 統一成 A4：內容仍是向量、文字仍選得到（不是轉成圖）
- [ ] 原本就是目標尺寸的頁**不重放**（不多包一層 XObject）
- [ ] 帶 /Rotate 的頁面尺寸判斷正確（`page.rect` 已是視覺尺寸，不可再算一次）

#### 騎縫章 (pdf-seam-stamp) 🆕 v1.14.20
- [ ] 印章切片蓋在連續頁上，預覽「拼回去」看接縫是否對得起來
- [ ] 旋轉在切片**之前**（先切再各自轉會對不起來）；切片寬度累進取整無殘條
- [ ] 同一組內位置與角度完全一致；亂數種子有回報可重現
- [ ] 一般使用者權限與「用印與簽名」一致（`test_roles_rbac.py` 守著）

#### 頁面加框 (pdf-border) 🆕 v1.14.16
- [ ] 單線 / 雙線 / 圓角 / 陰影各出一份，框不壓到內容
- [ ] 自訂邊距與線寬生效；多頁整份都有框
- [ ] 粗細 / 顏色 / 線型 / 圓角 / 雙線 / 陰影逐項改都看得出來
- [ ] 辦公文件來源先轉 PDF 再加框
- [ ] 邊框不會蓋到原本的內容

#### 乘車證明整理 (transit-proof) 🆕 v1.14.17
- [ ] 上傳台鐵 / 高鐵乘車證明 PDF → 日期、交通工具、起訖、費用成表
- [ ] 多份批次 → 單一彙整表；CSV 匯出欄位齊全（公式注入已由
      `test_csv_injection.py` 守）
- [ ] 台鐵購票證明與高鐵電子車票證明各一份都解得出來
- [ ] 日期 / 交通工具 / 起訖 / 費用四欄正確
- [ ] 七種格式匯出（CSV / XLSX / ODS / JSON / XML / TXT / MD）
- [ ] 欄位顯示設定改完立即套用

#### 掃描拼合 (scan-merge) 🆕 v1.11.0
- [ ] 拉入多張掃描（PDF / PNG / JPG）各含一塊內容 → 自動偵測出區塊
- [ ] **保留原彩色**：合成結果不轉黑白 / 不去彩（彩色內容飽和度不掉）
- [ ] **依原位置**：每塊擺到它在原掃描中的相對位置；重疊以紅框警示、不自動重排
- [ ] A4 預覽可拖曳移動、拖右下控點等比縮放
- [ ] **背景淨白**（預設開）把淡灰 / 微黃掃描底色提亮成純白，彩色內容不受影響；可關閉
- [ ] 產生單張 A4 白底 PDF（595×842 pt）
- [ ] 空白頁回 422（找不到內容）
- [ ] crop 取圖 ACL：非法 id 400、不存在 404、跨 user 擋
- [ ] **公開 API** `POST /tools/scan-merge/api/scan-merge`（form-data 多檔）回 PDF

#### PDF 轉文書檔 (pdf-to-office)
- [ ] 三顆引擎各轉一份，前後對照預覽都出現
- [ ] 內容遺失超過 50% 有紅字警示
- [ ] 物件量提示（黃 / 紅兩級）依實測門檻出現
- [ ] 大型文件自動分段後合併成**單一檔案**（不是 zip）

#### PDF 轉簡報 (pdf-to-slides)
- [ ] 直向 PDF 的尺寸照原樣還原
- [ ] 產出真的載得進 Impress / PowerPoint
- [ ] 一頁對一張投影片，張數與原稿相同

#### PDF 轉 Markdown (pdf-to-markdown)
- [ ] 標題 / 表格 / 粗體都保留
- [ ] `include_images=true` 改回傳 ZIP
- [ ] 左右對照可捲動、側欄可收折

#### Markdown 轉文書檔 (markdown-to-doc)
- [ ] 三種輸出（PDF / docx / odt）都產得出來
- [ ] 頁面預覽 lightbox 可翻頁、ESC 關閉
- [ ] odt 的 mimetype 是 text 不是 text-web
- [ ] 六種字型都選得到且產出真的換了字型
- [ ] 程式碼區塊底色不會每行重疊

#### 圖片轉 PDF (image-to-pdf)
- [ ] 多圖排序 / 旋轉 / 刪除單頁
- [ ] 頁面大小選項生效
- [ ] HEIC 來源在缺 pillow-heif 時給明確錯誤，不是 500

### 2.5 資安處理 🆕 全新分類

#### 文件去識別化 (doc-deident) 🆕
- [ ] 上傳 PDF 或 Office（先轉 PDF）
- [ ] 偵測 12 類：身分證 / 手機 / Email / 統編 / 信用卡 / 住址 / 銀行帳號 / ...
- [ ] 台灣身分證末碼校驗、統編加權檢查、信用卡 Luhn 都正確
- [ ] **遮蔽模式**：真 redact（`apply_redactions`），下載後原文無法復原
- [ ] **脫敏模式**：透明 redact + 蓋上 mask 文字（不是白底方塊）
- [ ] 處理完顯示頁面預覽縮圖 + lightbox 放大

#### PDF 密碼保護 (pdf-encrypt) 🆕
- [ ] 設開啟密碼 + 擁有者密碼 + 權限（禁列印/複製/編輯/擷取）
- [ ] AES-256 加密
- [ ] 下載後用 reader 開啟需要密碼

#### PDF 密碼解除 (pdf-decrypt) 🆕
- [ ] 已知密碼解除 → 輸出無密碼副本
- [ ] 多檔批次套用同一密碼
- [ ] 無開啟密碼但有權限限制：留空密碼也能解除權限

#### Metadata 清除 (pdf-metadata) 🆕
- [ ] 分析頁顯示 Info dict / XMP / 修訂歷史 / 標記
- [ ] 選擇性清除（個別勾選）
- [ ] 全部清除 → 輸出無痕副本
- [ ] 再次分析確認欄位為空

#### 隱藏內容掃描 (pdf-hidden-scan) 🆕
- [ ] 掃出 7 類：JS / 嵌入檔 / URI / launch action / 白字/頁面外 / 3D / 多媒體
- [ ] 風險清單顯示類型 + 位置
- [ ] 一鍵清除後再掃確認乾淨

#### 文件差異比對 (doc-diff) 🆕
- [ ] 上傳舊 / 新兩份 PDF
- [ ] 並排顯示 opcodes（紅=刪 / 綠=增 / 黃=改）
- [ ] Metadata 差異區塊
- [ ] 跨頁也能比對

### 2.6 設定 (admin)

#### 資產管理
- [ ] 上傳 + 去背 + 裁剪 + match-aspect
- [ ] 三類資產（stamp / signature / watermark / logo）分開列示

#### 公司資料
- [ ] 新增第二公司、欄位編輯、匯入匯出

#### 同義詞
- [ ] 新增條目並儲存

#### 表單範本
- [ ] 列表顯示已記住版型

#### 轉檔設定
- [ ] 拖曳排序、新增自訂路徑、儲存後重讀正確
- [ ] OxOffice / LibreOffice 優先序

#### 字型管理 🆕
- [ ] 內建 CJK 字型清單（Noto Sans TC / Noto Serif TC）
- [ ] 系統字型掃描 + 重掃按鈕
- [ ] 自訂字型上傳（.ttf / .otf）
- [ ] 刪除自訂字型
- [ ] pdf-editor 的字型 picker 能看到所有來源

#### LLM 設定 🆕
- [ ] 預設 enabled=False
- [ ] 填 endpoint / model 後測試連線
- [ ] 關閉時核心工具仍能正常運作

#### API Token 🆕
- [ ] 建立 / 列表 / 刪除 token
- [ ] 用 bearer 呼叫 `/api/*` 成功；無 token 回 401

#### 工作區設定 (admin/workspace) 🆕
- [ ] 啟用 / 停用切換；停用後重新整理任一工具頁，「存至工作區」「從工作區載入」按鈕與側欄「我的工作區」全部消失
- [ ] 設定每人容量額度 / 單檔上限 / 保留時數並儲存
- [ ] 「目前佔用」表列出各使用者佔用與總量

#### 記錄轉發（log forward）（2026-08-16 稽核補列 —— 原本整頁零驗收）
- [ ] 新增 syslog / CEF / GELF 目的地各一，測試送出有到（tcpdump 或收端確認）
- [ ] 收端不通時：retry 3 次後放棄，本機稽核出現 `audit_forward_failed`
- [ ] 停用的目的地不送

#### OCR 語言包管理（原本零驗收）
- [ ] 列出已裝 / 可裝語言；補裝一種後 pdf-ocr 立即可選
- [ ] 遠端 OCR 伺服器部署腳本可下載（install.sh / uninstall.sh）

#### 統編資料庫管理（原本零驗收）
- [ ] 財政部檔上傳走背景（頁面立即回 started，不卡住）
- [ ] 進度列會動；完成後筆數正確、vat-lookup 查得到新資料
- [ ] 排程自動更新設定存讀一致

#### 稽核記錄頁（原本只驗權限，沒驗頁面本身）
- [ ] 分頁列表、依 user / 事件類型 / 時間篩選有效
- [ ] CSV 匯出欄位齊全（公式注入由 `test_csv_injection.py` 守）
- [ ] 使用者篩選的模糊比對（LIKE）與 datalist 建議正常

### 2.6b 使用者工作區 (我的工作區) 🆕
- [ ] 任一 job 型工具（如蓋章 / 合併 / OCR）完成後出現「存至工作區」，按下後存入成功
- [ ] 任一上傳區出現「從工作區載入」，挑檔後該檔灌入工具流程（PDF/PNG，依工具 accept 過濾；非 PDF/PNG 工具不顯示此鈕）
- [ ] 「我的工作區」頁：容量條、檔案清單、下載 / 重新命名 / 刪除
- [ ] 額度已滿時再存 → 友善錯誤「容量已滿」
- [ ] 啟用認證時：A 帳號看不到 B 帳號的檔（清單與直連 file_id 皆不可）
- [ ] 保留時數到期後（或手動 retention sweep）過期檔被清除

### 2.7 介面

- [ ] 側欄品牌顯示 logo（深底）
- [ ] 首頁 hero 顯示淺底 logo + 三個特色 pill
- [ ] favicon 顯示
- [ ] 工具卡片依分類分組
- [ ] **每個工具有獨一無二的 icon 與顏色**（首頁與側欄一致）
- [ ] **側欄 active tile 白底延伸到右邊內容區**（無紫色縫隙）
- [ ] **側欄捲軸浮動**（只在 hover / 滾動時顯示）
- [ ] **搜尋支援中英文**（輸入 `form` 或 `填寫` 都能找到 pdf-fill）
- [ ] 視窗縮窄到 ≤ 900px：側欄收起、漢堡按鈕展開、項目正確點選
- [ ] **缺中文字型提示**：把系統中文字型移開（或在字型管理把它們隱藏）後開
      浮水印 / 插入頁碼 → 頁面最上方出現黃色提示；管理員看得到安裝指令，
      一般使用者看到「請聯絡管理員」。裝回字型後提示消失

### 2.8 術語檢查

- [ ] UI 使用台灣繁體用詞：圖片 / 軟體 / 字型 / 列印 / 檔案 / 訊息 / 影片 / 網路 / 伺服器 / 選單 / 螢幕 / 儲存 / 預設 / 設定
- [ ] 避免中國大陸用詞：圖像 / 軟件 / 字體 / 打印 / 文檔 / 信息 / 視頻 / 網絡 / 服務器 / 菜單 / 屏幕 / 保存 / 默認 / 設置

## 3. 跨平台檢查

### macOS
- [ ] OxOffice 已安裝時 `find_soffice` 命中 `/Applications/OxOffice.app/...`
- [ ] 原生 overlay 捲軸在 hover 時顯示

### Linux
- [ ] `apt install libreoffice` 後命中 `/usr/bin/soffice` 或 `/usr/bin/libreoffice`
- [ ] Ghostscript 若裝了 (`/usr/bin/gs`) 壓縮進階模式可用

### Windows
- [ ] LibreOffice 安裝後命中 `C:\Program Files\LibreOffice\program\soffice.exe`
- [ ] `shutil.which("soffice.exe")` 回 fallback 路徑
- [ ] `/admin/conversion` 顯示 Windows builtin 路徑且可使用
- [ ] Ghostscript `gswin64c.exe` 偵測

## 3.4 Windows 安裝程式（GitHub Releases 上那支 .exe）🆕 v1.15.6

**它是瘦 bootstrapper**：安裝時才去 `git clone --branch main`，所以**檔名上的
版本沒有意義** —— 裝出來的一定是當下的 `main`。驗收要驗「裝出來的那一版」，
不是檔名。

    # 從 GitHub Releases 抓最新那支，複製到 Win11 測試機
    powershell -Command "Start-Process setup.exe -ArgumentList '/S' -Wait -Verb RunAs"

- [ ] **簽章 `Valid`**：`(Get-AuthenticodeSignature setup.exe).Status`
- [ ] 無介面安裝（/S） **exit code 0**
- [ ] 服務 `RUNNING`、`curl http://127.0.0.1:8765/healthz` 回 `{"ok":true}`
- [ ] `jtdt status` 的版本 = **GitHub main 當下的版本**（不是檔名那個）
- [ ] **資料目錄原封不動**：安裝前後檔案數與 `*.sqlite` 清單一致
- [ ] 「設定 → 應用程式」顯示的版本**跟實際裝的一樣**
      （2026-09-05 抓到：登錄檔寫打包時的版本，實際是 main 的版本）
- [ ] **解除安裝走同一支 `setup.exe /uninstall`**，安裝目錄裡**不該有** `uninstall.exe`
      （舊版留下的那支要在升級時被清掉）
- [ ] 解除安裝後：服務移除、登錄檔項目移除、防火牆規則移除、`jtdt` 不在 PATH
- [ ] **使用者資料預設保留**（`%ProgramData%\jt-doc-tools\Data` 的四個 sqlite 還在）
- [ ] 解除安裝程式**不會卡住**（NSIS 在 session 0 沒有桌面，用 `CopyFiles`
      會停在那裡不返回 —— 一定要用 `System::Call kernel32::CopyFile`）
- [ ] **端到端**：上傳 → 轉檔 → 下載，產出打開來看得到內容
      （不是只看 HTTP 200 —— §0.5 那條）

## 3.5 CLI 指令（`jtdt`）🆕 v1.14.95

**web 上不去的時候只剩它** —— 啟用 LDAP / AD 之後如果設定寫錯，畫面上救不回來，
緊急復原全靠 CLI（`jtdt auth show / disable / set-local` + `reset-password` 四件套）。
所以這幾支的驗收標準跟工具一樣嚴，而且**必須在服務沒跑的情況下也能用**。

### 服務控制
- [ ] `jtdt start` / `stop` / `restart` / `status` / `run` / `open` / `logs` / `version`
      —— 每支跑得起來，`status` 要能正確分辨「跑著」與「沒跑」
      （**判 PID 一律 `lsof -tiTCP:<port>`，不要 `pgrep`** —— `.venv/bin/python`
      是 symlink，ps 印的是解析後的路徑，pgrep 抓不到）。
- [ ] `jtdt bind` 改監聽位址後重啟生效（Windows 走 WinSW 的 XML）。

### 更新
- [ ] `jtdt update` **拒絕降版**：`origin/main` 比目前舊要中止並還原。
- [ ] **root 跑 update 不可以撞 git 的 dubious ownership**（安裝目錄屬於服務帳號）。
- [ ] update 完成後**新檔要 chown 回服務帳號**，否則服務讀不到 `.venv` 裡的新東西。
- [ ] 輸出的 `vX -> vY` 就是**版本有沒有 bump 的檢查點** —— 印出來前後相同
      就是忘了 bump（動到 `app/` 或 `static/` 一定要 bump）。
- [ ] **改 update 流程本身要連測兩個版本**：這一次跑的是改之前的 `cli.py`
      （已經載進記憶體了），修好的行為要下一次更新才看得到。
- [ ] 加了新相依之後：`jtdt update` 要真的把它裝起來，
      **部署後驗 `import <新模組>`，不能只看 `/healthz`**（延遲匯入會蓋掉缺相依）。

### 緊急復原（**服務沒跑也要能用**）
- [ ] `jtdt auth show` / `jtdt auth disable` / `jtdt set-local`（= `jtdt auth set-local`）
      / `jtdt reset-password <user>` —— 直接動 sqlite，**不需要服務在跑**，也不需要登入。
- [ ] `jtdt audit-user create <帳號>` —— 建立本機**稽核員**（唯讀稽核記錄、
      **強制 2FA**）。判準：建出來的帳號登入後只看得到稽核記錄，
      而且**沒設定 2FA 之前登不進去**；`--password` 只是免互動，
      不可以在共用機器上用（會留在 shell history）。
- [ ] 寫進 `data/` 的檔案（`auth_settings.json` 等）**要 chown 回服務帳號**：
      sudo 跑出來的是 `root:root` mode 600，服務讀不到，
      使用者看到的是「設定不見了」。

### 資料庫 / OCR 語言包 / 安裝
- [ ] `jtdt db-backup` / `db-backups` / `db-check` / `db-restore`
      —— 備份可還原、`db-check` 抓得出毀損。
- [ ] `jtdt ocr-lang list` / `install` / `remove` / `switch` / `quality`
      —— 裝完該語言在 OCR 工具的選單裡出得來。
- [ ] `jtdt install` / `uninstall`（含 `--purge`）
      —— Windows 的 `--purge` 要用 detached 的清理程序刪安裝目錄，
      否則 `jtdt.cmd` 把自己刪掉、cmd.exe 讀下一行會噴「找不到批次檔。」。
- [ ] `jtdt list` / `show` / `disable` / `remove` / `switch` 等子指令都跑得起來。

### 共通
- [ ] **CLI 的說明訊息一律英文 ASCII**（純文字 TTY / Windows console / minimal
      container 渲染不出中文），GUI 與網頁介面才用繁體中文。
- [ ] Windows **沒有 `sudo`**：文件裡的指令要分平台寫
      （Linux/macOS `sudo jtdt update`；Windows 先開系統管理員 PowerShell）。

## 4. API 覆蓋檢查 🆕（v1.8.55 起完整列出，現 47 個工具）

每個工具至少 1 個 `/api/<tool-id>` endpoint（路徑：`/tools/<tool-id>/api/<tool-id>` 或 `/tools/<tool-id>/convert`）。發版前 curl 抽測：

> **這份清單靠人維護一定會漂**（歷史教訓：v1.14.20 核對時曾發現 7 支工具沒列、`API.md` 少 3 支 —— 已補，留此句是講「為什麼要有自動比對」）。
> `tests/test_api_doc_coverage.py` 會用**實際路由表**反向比對這份清單與 `github/API.md`，
> 漏列直接紅燈。手動抽測仍要做 —— 那支測試只保證「有寫」，不保證「寫的是對的」。

### 結構操作（PDF in / PDF out）
- [ ] `/tools/pdf-compress/api/pdf-compress` — POST file + preset → PDF
- [ ] `/tools/pdf-split/api/pdf-split` — POST file + pages → PDF or ZIP
- [ ] `/tools/pdf-rotate/api/pdf-rotate` — POST file + angle → PDF
- [ ] `/tools/pdf-pages/api/pdf-pages` — POST file + keep_pages → PDF
- [ ] `/tools/pdf-pageno/api/pdf-pageno` — POST file + style → PDF
- [ ] `/tools/pdf-nup/api/pdf-nup` — POST file + n → PDF
- [ ] `/tools/pdf-merge/api/pdf-merge` — POST files[] → PDF
- [ ] `/tools/pdf-encrypt/api/pdf-encrypt` — POST file + password → PDF
- [ ] `/tools/pdf-decrypt/api/pdf-decrypt` — POST file + password → PDF
- [ ] `/tools/pdf-border/api/pdf-border` — POST file + 框線設定 → PDF
- [ ] `/tools/pdf-bookmark/api/pdf-bookmark` — POST files[] + 書籤設定 → PDF（書籤 / 目錄頁）
- [ ] `/tools/pdf-seam-stamp/api/pdf-seam-stamp` — POST file + 章來源 → PDF（切片蓋在連續頁）
- [ ] `/tools/pdf-page-size/api/pdf-page-size` — POST file + paper → PDF（統一尺寸）

### 內容擷取
- [ ] `/tools/pdf-extract-text/api/pdf-extract-text` — POST file → JSON `{pages:[...]}`
- [ ] `/tools/pdf-extract-images/api/pdf-extract-images` — POST file → ZIP
- [ ] `/tools/pdf-attachments/api/pdf-attachments` — POST file → ZIP
- [ ] `/tools/pdf-wordcount/api/pdf-wordcount` — POST file → JSON `{words, chars, ...}`
- [ ] `/tools/pdf-hidden-scan/api/pdf-hidden-scan` — POST file → JSON `{findings, totals}`
- [ ] `/tools/pdf-metadata/api/pdf-metadata` — POST file + clear_* flags → cleaned PDF

### 用印 / 簽名 / 浮水印 / 表單
- [ ] `/tools/pdf-stamp/api/pdf-stamp` — POST file + stamp_image → PDF
- [ ] `/tools/pdf-watermark/api/pdf-watermark` — POST file + text → PDF
- [ ] `/tools/pdf-fill/api/pdf-fill` — POST file + company_id → PDF

### 註解
- [ ] `/tools/pdf-annotations/api/pdf-annotations` — POST file → JSON
- [ ] `/tools/pdf-annotations-strip/api/pdf-annotations-strip` — POST file → PDF
- [ ] `/tools/pdf-annotations-flatten/api/pdf-annotations-flatten` — POST file → PDF

### 格式轉換
- [ ] `/api/convert-to-pdf` (in main.py) — POST file → PDF (office-to-pdf)
- [ ] `/tools/office-convert/formats` — GET → 可用家族與目標格式（target id 因安裝而異）
- [ ] `/tools/office-convert/convert` — POST files[] + target → 原格式或 ZIP（async job）；
      跨類（試算表配文書檔的 target）與不存在的 target 都應是 400 不是 500
- [ ] `/tools/pdf-to-image/convert` — POST file → ZIP/PNG
- [ ] `/tools/pdf-to-office/convert` — POST file → docx/odt（async job）
- [ ] `/tools/image-to-pdf/api/image-to-pdf` — POST files[] → PDF
- [ ] `/tools/scan-merge/api/scan-merge` — POST files[] → 單張 A4 白底 PDF
- [ ] `/tools/pdf-to-slides/convert` — POST file → pptx/odp（async job）
- [ ] `/tools/pdf-to-markdown/api/pdf-to-markdown` — POST file → `text/markdown`；
      `include_images=true` 改回 ZIP（**回應型別會變**）
- [ ] `/tools/markdown-to-doc/api/markdown-to-doc` — POST file 或 text + format → pdf/docx/odt；
      非法 format 應是 400 不是 500

### 文字工具
- [ ] `/tools/text-list/api/text-list` — POST text → JSON
- [ ] `/tools/text-diff/api/text-diff` — POST text → JSON / HTML
- [ ] `/tools/text-deident/api/text-deident` — POST text → JSON
- [ ] `/tools/doc-translate/api/doc-translate` — POST office file → 翻譯後的**同格式**檔案
- [ ] `/tools/translate-doc/api/translate-doc` — POST file → translated file

### 文件處理
- [ ] `/tools/doc-deident/api/doc-deident` — POST file → de-identified
- [ ] `/tools/doc-diff/api/doc-diff` — POST file_a + file_b → JSON
- [ ] `/tools/pdf-editor/api/pdf-editor` — POST file + edits json → PDF
- [ ] `/tools/pdf-ocr/api/pdf-ocr` — POST file + langs → `{job_id}` (async)

### 查詢 / 分析 / 檢核
- [ ] `/tools/vat-lookup/api/vat-lookup` + `/api/vat-lookup/batch`
- [ ] `/api/vat-lookup/{vat}` (path-style GET in main.py)
- [ ] `/tools/einvoice-scan/api/einvoice-scan` + `/api/backend-status`
- [ ] `/tools/submission-check/api/self-entities` (CRUD)
- [ ] `/tools/transit-proof/api/transit-proof` — POST files[] → JSON `{ok, count, entries, failed}`；
      **認不出的檔不會讓整批失敗**（HTTP 仍 200），要看 `failed` 是不是空的

### 共通驗證項
每個 endpoint 至少要：
- [ ] 拒絕非 PDF / 空檔（400）
- [ ] 啟用認證時 token 驗證 + ACL（`upload_owner.require()` 防跨 user 取檔）
- [ ] 大檔（> 限額）回 413 而不是 OOM
- [ ] 回應 `Content-Disposition` 中文檔名 RFC 5987（走 `http_utils.content_disposition`）

### 自動化覆蓋（理想）
新加 endpoint 由兩支既有測試守：`tests/test_api_doc_coverage.py`（路由表 ↔ 文件雙向比對）與 `tests/test_broken_input_no_500.py`（**全部**工具 POST 端點 × 壞輸入不可 500，從路由表自動列舉，新工具自動被涵蓋）。發版前 `uv run pytest tests/test_api_doc_coverage.py tests/test_broken_input_no_500.py -q` 必綠。（原本這裡寫「tests/test_api_endpoints.py（待補）必綠」—— 一個不存在的檔案當發版門檻，指令必然失敗，2026-08-16 稽核改掉。）

## 4.6 非工具 API（管理 / 作業 / 通知）🆕 v1.14.56

§4 只涵蓋「每個工具至少一支 API」。**管理區、作業佇列、通知這些 API 之前
一條驗收都沒有** —— 它們同樣是對外的攻擊面，而且改壞了整個管理功能會死掉
（2026-08-26 稽核補上）。清單由 `tests/test_test_plan_coverage.py` **從路由表
自動比對**，新增端點沒列進來就紅燈，不靠人記得。

> 判準都一樣：①未登入一律拒絕 ②一般使用者碰管理端點一律拒絕
> ③壞輸入回 4xx 不可 500 ④寫入端點不帶 CSRF token 要被擋。
> 這四條由 `tests/test_authz_boundaries.py`、`tests/test_broken_input_no_500.py`、
> `temp/sec-audit/pentest.py` 自動涵蓋；下面列的是**功能**驗收。

### 管理區設定 API
- [ ] `POST /admin/api/check-latest-version` — 回目前版本與最新版本；連不到網路時要回錯誤訊息，不可讓頁面一直轉
- [ ] `GET|POST /admin/api/llm/settings` — 存檔後重新整理值要留著；數值欄位（逾時、並行數、句數上限）超範圍要被 clamp
- [ ] `GET /admin/api/llm/models` — 列出遠端模型；伺服器連不上時回錯誤訊息不可拋例外
- [ ] `POST /admin/api/llm/test-connection` — 成功 / 失敗都要有明確訊息（失敗訊息不可洩漏內部路徑或憑證）
- [ ] `POST /admin/api/ocr-langs/set-engine` — 切換 easyocr / tesseract 後，OCR 工具實際用的引擎要跟著改
- [ ] `POST /admin/api/ocr-langs/set-quality`、`POST /admin/api/ocr-langs/switch-active` — 設定有寫進去且重啟後仍在
- [ ] `GET /admin/api/ocr-langs/external/status`、`POST /admin/api/ocr-langs/external/save`、
      `POST /admin/api/ocr-langs/external/test` — 遠端 GPU OCR 設定；**test 要真的打對方**，不可只回 200
- [ ] `GET /admin/api/settings-export/categories` — 類別清單要跟實際可匯出的項目一致
- [ ] `POST /admin/api/tokens/create`、`POST /admin/api/tokens/revoke`、`POST /admin/api/tokens/enforce`
      — 建立的 token 立即可用、撤銷後立即失效、enforce 開關會改變未帶 token 的行為

### 作業佇列 API
- [ ] `GET /api/jobs/{job_id}` — 進度 / 狀態；**別人的作業要拿不到**
- [ ] `POST /api/jobs/{job_id}/cancel` — 取消後狀態要變、正在跑的要真的停
- [ ] `GET /api/jobs/{job_id}/download`、`GET /api/jobs/{job_id}/download/{_filename}`、
      `GET /api/jobs/{job_id}/download-png` — 歸屬驗證；作業過期回 410 不可 500
- [ ] `POST /admin/jobs/api/cancel/{job_id}` — 管理員可取消任何人的作業
- [ ] `POST /admin/jobs/api/pause` — 暫停後新作業排隊不派送，恢復後會繼續
- [ ] `POST /admin/translation-glossary/save` — 整份覆寫；重複 / 不合法要回 400 並指出第幾條
- [ ] `POST /admin/translation-glossary/preview` — 試打一段文字，回命中的條目與遮罩後的樣子
- [ ] `GET  /admin/translation-glossary/export` — CSV（UTF-8 BOM，Excel 開得開）
- [ ] `GET|POST /admin/jobs/api/priority-users` — **順序就是優先序**，讀回來不可以被重新排序（v1.14.7 踩過：讀取時 `sorted()` 把拖好的順序洗掉）
- [ ] `GET /admin/jobs/api/user-search` — 模糊比對；非管理員不可用

### 通知 / 其他
- [ ] `GET /api/my/inbox` — 只回自己的通知
- [ ] `POST /api/my/inbox/seen` — 標記已讀；別人的通知 id 標不動
- [ ] `POST /api/llm-review` — LLM 逐欄校驗；LLM 關閉時要回明確訊息不可 500
- [ ] `PUT|DELETE /tools/submission-check/api/self-entities/{entity_id}` — 只能改 / 刪自己的；別人的 id 要被拒

### 管理頁（每一頁至少開得起來且功能可用）

清單同樣由 `tests/test_test_plan_coverage.py` 從路由表比對，新增管理頁沒列進來會紅燈。

- [ ] `/admin/api-tokens` — 建立 / 撤銷 token，開關 enforce
- [ ] `/admin/log-forward` — 新增目的地、三種格式（syslog / cef / gelf）、送測試訊息
- [ ] `/admin/synonyms` — 同義詞新增 / 刪除，會影響表單填寫的欄位對應
- [ ] `/admin/translation-glossary` — 翻譯對照字典（逐句翻譯 / 文件翻譯共用）
  - [ ] 新增 / 編輯 / 刪除 / 停用；停用的條目不生效但留著
  - [ ] 新增時語言依**目前介面語言**自動帶（中文介面 → 原文英文、譯文中文）
  - [ ] 「不要翻譯」模式**不需要填譯文**，而且存得起來
  - [ ] 同一個語言對裡原文重複要被擋下，訊息**指出是第幾條**
  - [ ] 譯文空白 / 語言相同 / 語言沒填，都要擋下並說得出原因
  - [ ] 搜尋與分頁：貼進幾百條之後畫面不可以卡住
  - [ ] CSV **匯出再匯入**回得來（含中文、逗號、引號）
  - [ ] CSV 匯入會顯示「讀入 N 條、覆蓋 M 條」，**要按儲存才生效**
  - [ ] CSV 匯出的儲存格不可以被 Excel 當成公式（`=` 開頭要被中和）
  - [ ] 「試一段文字」：貼一段原文看得到命中哪幾條與會保護幾處
  - [ ] 存檔會寫稽核（`/admin/audit` 篩 `glossary_change`）
  - [ ] **樣式有套上**（表格不是無框線的裸 HTML）—— `{% block %}` 名字打錯時
        Jinja 不會報錯，那段會被安靜丟掉
- [ ] `/admin/templates` — 範本列表與刪除
- [ ] `/admin/vat-db`、`/admin/vat-db/info`、`/admin/vat-db/schedule` — 統編資料庫下載 / 上傳匯入（背景執行，頁面不可卡住）、排程設定、狀態顯示
- [ ] `/admin/directory/tree`、`/admin/directory/user-roles`、`/admin/directory/group-roles` — 目錄瀏覽的樹狀展開與角色指派（含 OU / 群組 / 個人三種對象）
- [ ] `/admin/system-status/databases` — 各資料庫大小與最舊一筆時間
- [ ] `/admin/audit/export.csv` — 稽核記錄匯出；**公式注入防護**（`=` 開頭的欄位要被前綴處理，見 TEST_PLAN_SECURITY）

### 4.6.1 之前靠「尾段字串」假通過的那幾支 🆕 v1.15.30

> 涵蓋守門原本比對「`/api/` 之後那一截」—— `list` / `count` / `assets` /
> `history` 這種字在四千行的文件裡**必然**找得到，所以那幾支端點從來沒有真的
> 被檢查過（實算：84 支裡 8 支假通過）。判準已改成**完整路徑**。

- [ ] `GET /workspace/api/count` —— 側欄的工作區檔案數
  - [ ] **未登入不可以回數字**（那會洩漏「這台有多少檔案」）
  - [ ] 工作區停用時回 0 或明確的停用狀態，**不可以 500**
  - [ ] 數字要跟 `/workspace` 頁面實際列出的份數一致（別人的檔案不算）
- [ ] `GET /tools/einvoice-scan/api/backend-status` —— 後端（QR 解碼器）可用狀態
  - [ ] 缺相依時要回「不可用 + 說得出缺什麼」，**不可以 500**
        （那是部署問題不是使用者送錯東西）
  - [ ] 這支不吃使用者輸入 → 要驗它**不會洩漏路徑或版本細節**
- [ ] `POST /tools/vat-lookup/api/vat-lookup/batch` —— 統編批次查詢
  - [ ] 一次丟 500 筆要有上限與明確錯誤，不可以讓請求跑到逾時
  - [ ] 混雜不合法統編時，**回報哪幾筆不合法**而不是整批失敗
  - [ ] 查不到的統編與「資料庫還沒下載」要分得出來

## 4.8 管理區「會改狀態」的端點 🆕 v1.15.30

> **為什麼補這一節**：涵蓋守門原本把整個 `/admin` 前綴跳過（只有
> `test_admin_pages_appear_in_the_plan` 守頁面本身），於是**103 支會改狀態的
> 管理端點裡有 79 支一條驗收都沒有** —— 而這些正是「按下去會改到別人資料」
> 的那些（刪使用者、清工作區、匯入設定、改權限矩陣）。
>
> **不逐支寫成一大段散文**：下面每一組共用同一份判準，組內只列端點與
> 需要特別注意的地方。逐支抄同樣的四句話只會讓人不想讀，而不想讀的清單
> 等於沒有清單。

### 每一支都要過的四條（共用判準）

- [ ] **未登入** → 302 / 401，**不可以**執行動作
- [ ] **已登入但不是管理員** → 403，**而且動作沒有發生**（要回去確認狀態沒變，
      不是只看回應碼）
- [ ] **成功後有稽核記錄**（`/admin/audit` 查得到誰在什麼時候改了什麼）
- [ ] **失敗訊息不外洩內部細節**（路徑、堆疊、SQL）—— 只回使用者看得懂的話

> 破壞性動作（刪除、清空、匯入覆蓋）另外要有**前端二次確認**，
> 而且**先試算再動手**（批次刪除那條在 v1.14.x 就是這樣做的）。

### 逐組清單

#### OCR 語言包（`/admin/ocr-langs/*`）

- [ ] `/admin/ocr-langs/install`
- [ ] `/admin/ocr-langs/uninstall`

#### SSO 單一登入（`/admin/sso/*`）

- [ ] `/admin/sso/proxy-save`
- [ ] `/admin/sso/save`
- [ ] `/admin/sso/test`

#### 使用者管理（`/admin/users/*`）

- [ ] `/admin/users/bulk/delete`
- [ ] `/admin/users/bulk/disable-view`
- [ ] `/admin/users/bulk/enabled`
- [ ] `/admin/users/bulk/roles`
- [ ] `/admin/users/create`
- [ ] `/admin/users/{uid}/delete`
- [ ] `/admin/users/{uid}/reset-password`
- [ ] `/admin/users/{uid}/reset-totp`
- [ ] `/admin/users/{uid}/sessions/revoke`
- [ ] `/admin/users/{uid}/unlock`
- [ ] `/admin/users/{uid}/update`

#### 同義詞（`/admin/synonyms/*`）

- [ ] `/admin/synonyms/add`
- [ ] `/admin/synonyms/import`
- [ ] `/admin/synonyms/save`

#### 品牌外觀（`/admin/branding/*`）

- [ ] `/admin/branding/reset`
- [ ] `/admin/branding/site-name`
- [ ] `/admin/branding/upload`

#### 字型管理（`/admin/fonts/*`）

- [ ] `/admin/fonts/bulk-hidden`
- [ ] `/admin/fonts/delete`
- [ ] `/admin/fonts/refresh`
- [ ] `/admin/fonts/rename`
- [ ] `/admin/fonts/toggle-hidden`
- [ ] `/admin/fonts/upload`

#### 工作區設定（`/admin/workspace/*`）

- [ ] `/admin/workspace/clear-all`
- [ ] `/admin/workspace/clear-user`
- [ ] `/admin/workspace/save`

#### 檔案保留 / 清理（`/admin/retention/*`）

- [ ] `/admin/retention/save`
- [ ] `/admin/retention/sweep-now`

#### 權限矩陣（`/admin/permissions/*`）

- [ ] `/admin/permissions/set`

#### 歷史記錄（`/admin/history/*`）

- [ ] `/admin/history/{kind}/{hid}/delete`

#### 目錄瀏覽（`/admin/directory/*`）

- [ ] `/admin/directory/filter`
- [ ] `/admin/directory/group-roles`
- [ ] `/admin/directory/ou-roles`
- [ ] `/admin/directory/user-roles`

#### 系統狀態（`/admin/system-status/*`）

- [ ] `/admin/system-status/databases/backup`
- [ ] `/admin/system-status/upload-limit`

#### 統編資料庫（`/admin/vat-db/*`）

- [ ] `/admin/vat-db/auto-download`
- [ ] `/admin/vat-db/clear`
- [ ] `/admin/vat-db/schedule`
- [ ] `/admin/vat-db/upload`

#### 群組管理（`/admin/groups/*`）

- [ ] `/admin/groups/create`
- [ ] `/admin/groups/directory-sync/run`
- [ ] `/admin/groups/directory-sync/settings`
- [ ] `/admin/groups/sync-ldap`
- [ ] `/admin/groups/{gid}/delete`
- [ ] `/admin/groups/{gid}/update`

#### 翻譯對照字典（`/admin/translation-glossary/*`）

- [ ] `/admin/translation-glossary/preview`
- [ ] `/admin/translation-glossary/save`

#### 表單範本（`/admin/templates/*`）

- [ ] `/admin/templates/{tid}/delete`
- [ ] `/admin/templates/{tid}/rename`

#### 角色管理（`/admin/roles/*`）

- [ ] `/admin/roles/create`
- [ ] `/admin/roles/{role_id}/delete`
- [ ] `/admin/roles/{role_id}/set-default`
- [ ] `/admin/roles/{role_id}/update`

#### 記錄轉發（`/admin/log-forward/*`）

- [ ] `/admin/log-forward/save`

#### 設定備份（`/admin/settings-export/*`）

- [ ] `/admin/settings-export/download`
- [ ] `/admin/settings-export/import`
- [ ] `/admin/settings-export/preview`
- [ ] `/admin/settings-export/run-now`
- [ ] `/admin/settings-export/schedule`

#### 設定檔（匯入 / 匯出）（`/admin/profile/*`）

- [ ] `/admin/profile/create`
- [ ] `/admin/profile/import`
- [ ] `/admin/profile/save`
- [ ] `/admin/profile/{cid}/activate`
- [ ] `/admin/profile/{cid}/delete`

#### 認證設定（`/admin/auth-settings/*`）

- [ ] `/admin/auth-settings/disable`
- [ ] `/admin/auth-settings/ldap-save`
- [ ] `/admin/auth-settings/ldap-test-connection`
- [ ] `/admin/auth-settings/ldap-test-login`
- [ ] `/admin/auth-settings/policy-save`
- [ ] `/admin/auth-settings/unlock-all`
- [ ] `/admin/auth-settings/unlock-key`

#### 資產管理（印章 / 簽名 / Logo / 浮水印）（`/admin/assets/*`）

- [ ] `/admin/assets/import`
- [ ] `/admin/assets/upload`
- [ ] `/admin/assets/{asset_id}/crop`
- [ ] `/admin/assets/{asset_id}/default`
- [ ] `/admin/assets/{asset_id}/delete`
- [ ] `/admin/assets/{asset_id}/match-aspect`
- [ ] `/admin/assets/{asset_id}/save`

#### 轉換設定（`/admin/conversion/*`）

- [ ] `/admin/conversion/save`

#### 通知設定（`/admin/notify/*`）

- [ ] `/admin/notify/save`
- [ ] `/admin/notify/test/{channel}`

## 4.7 工具的非 API 端點 —— **畫面上實際打的那些** 🆕 v1.14.95

§4 只保證「每個工具至少一支 `/api/`」有驗收，§4.6 補了管理 / 作業 / 通知 API。
**但使用者在畫面上按的每一顆按鈕，打的其實是這一層**（`analyze` / `preview` /
`thumb` / `download` / `export-*` / 暫存區 CRUD）—— 而它們一條驗收都沒有。

這不是理論風險。這個專案歷來最痛的幾個 bug **全部出在這一層**，而且 `/api/`
那條路都是好的：

| 版本 | 出事的端點 | 症狀 |
|---|---|---|
| v1.14.17 | `pdf-nup` 的 `preview` / `generate` | **水平越權**：B 拿 A 的 upload_id 就下載得到對方的 PDF |
| v1.14.62 | `pdf-seam-stamp` 的逐頁預覽 | 每看一頁要 90～104 秒（每頁都合成整份，只取一頁） |
| v1.14.9 | 工作區縮圖 | 永遠空白（佔位圖沒有快取標頭，瀏覽器把空白那張存起來了） |
| v1.12.12 | `pdf-attachments` 的「無附件副本」 | 產出檔裡附件還在（PDF/A-3 的 `/AF` 沒清） |

> **判準依類型分六條**（下面清單逐支勾，但判準看這裡）：
>
> 1. **產出類**（`download` / `export-*`）—— **把產出打開來看內容**才算驗收（§0.5）。
>    端點回 200、筆數對、畫面顯示成功，**都不算**。
> 2. **預覽 / 縮圖類**（`preview` / `thumb` / `*-preview`）—— ①真的回到圖而且不是白的
>    ②**預覽要跟最終產出一致**（騎縫章那次是逐頁比對兩者的 PNG 位元組）
>    ③不可以「算整份、只用一頁」④空白佔位圖一定要 `Cache-Control: no-store`。
> 3. **兩段式分析**（`analyze` / `load`）—— 分析結果寫 sidecar，後續端點吃 `upload_id`
>    不重傳；**過期回 410 不可 500**；`upload_id` 走嚴格格式驗證（防路徑跳脫）。
> 4. **暫存區 CRUD**（`buffer` / `entry` / `case` / `override`）—— 只能動自己的；
>    批次刪除**先整批試算再動手**；刪完就地移除那一列，不可 `location.reload()`。
> 5. **LLM 加值**（`llm-*`）—— LLM 關閉或連不上時要**優雅退場**：明確訊息、不可 500、
>    更不可以把上游的 HTML 錯誤頁當成結果塞進去（v1.8.58 踩過）。
> 6. **全部共通** —— 壞輸入回 4xx（`tests/test_broken_input_no_500.py` 從路由表自動
>    列舉）、寫入端點要帶 CSRF、下載 / 預覽一律 `upload_owner.require()` 驗歸屬。

清單由 `tests/test_test_plan_coverage.py` **從路由表自動比對**：新增端點沒補進來
就紅燈。**不要用啟發式去猜「這支有沒有被測到」** —— 那條路試過，判準寬一點是
永遠綠的假測試，嚴一點就把驗得更嚴的工具誤報（v1.14.63 的教訓）。想看哪些端點
目前沒有自動化測試碰過，跑 `python tools/report_endpoint_test_coverage.py`，
那份是**提示不是判決**。

共 **267 支**（工具首頁不列，§2 已逐支驗收）。

**全站（認證 / 帳號 / 工作區 / 介面語言）**

- [ ] `GET /`
- [ ] `POST /2fa-verify`
- [ ] `GET /2fa-verify`
- [ ] `GET /auth/oidc/callback`
- [ ] `GET /auth/oidc/login`
- [ ] `POST /auth/saml/acs`
- [ ] `GET /auth/saml/login`
- [ ] `GET /auth/saml/metadata`
- [ ] `GET/POST /auth/saml/sls`
- [ ] `GET /branding/logo`
- [ ] `POST /change-password`
- [ ] `GET /healthz`
- [ ] `POST /login`
- [ ] `GET /login`
- [ ] `POST /logout`
- [ ] `GET /logout`
- [ ] `GET /me/2fa`
- [ ] `POST /me/2fa/disable`
- [ ] `POST /me/2fa/start`
- [ ] `POST /me/2fa/verify`
- [ ] `POST /me/email`
- [ ] `GET /my-jobs`
- [ ] `GET /setup-admin`
- [ ] `POST /setup-admin`
- [ ] `POST /setup-admin/reuse-existing`
- [ ] `GET /i18n/{locale}.js` — **前端字串的字典**（`static/js/i18n.js` 的 `tr()` 讀它）。
      判準：①繁體中文請求回**空字典**、而且樣板根本不輸出這個 `<script src>`
      ②不認得的語言碼回空字典**不是 404**（404 會在主控台留紅字，看起來像壞了）
      ③帶 `If-None-Match` 回 **304**（字典 100 KB，每頁重下一份很浪費）
      ④未登入也拿得到（登入頁自己要用），但裡面**不含任何使用者資料**。
- [ ] `POST /ui-locale`
- [ ] `GET /tools/pdf-diff` / `GET /tools/pdf-diff/` — **舊工具 id 的相容轉址**（v1.1.61 改名 `pdf-diff` → `doc-diff`）。判準：轉址碼是 **308 不是 301**（301 只保留 GET，POST 的 body 會掉），且 `{rest:path}` 子路徑一起轉。
- [ ] `GET /whoami`
- [ ] `GET /workspace`
- [ ] `POST /workspace/delete`
- [ ] `GET /workspace/file/{file_id}`
- [ ] `POST /workspace/rename`
- [ ] `POST /workspace/save`
- [ ] `GET /workspace/thumb/{file_id}`

**doc-deident（文件去識別化）**

- [ ] `POST /tools/doc-deident/detect`
- [ ] `GET /tools/doc-deident/download/{upload_id}`
- [ ] `POST /tools/doc-deident/find`
- [ ] `GET /tools/doc-deident/preview/{filename}`
- [ ] `POST /tools/doc-deident/process`

**doc-diff（文件差異比對）**

- [ ] `POST /tools/doc-diff/compare`

**doc-translate（文件翻譯）**

- [ ] `GET /tools/doc-translate/download/{upload_id}`
- [ ] `GET /tools/doc-translate/preview/{upload_id}/{page}`
- [ ] `POST /tools/doc-translate/start`
- [ ] `POST /tools/doc-translate/upload`

**einvoice-scan（電子發票處理）**

- [ ] `GET /tools/einvoice-scan/accounting-rules/builtin`
- [ ] `DELETE /tools/einvoice-scan/buffer`
- [ ] `GET /tools/einvoice-scan/buffer`
- [ ] `POST /tools/einvoice-scan/buffer/delete-batch`
- [ ] `POST /tools/einvoice-scan/buffer/llm-classify`
- [ ] `POST /tools/einvoice-scan/buffer/reclassify-accounting`
- [ ] `PATCH /tools/einvoice-scan/buffer/{invoice_id}`
- [ ] `DELETE /tools/einvoice-scan/buffer/{invoice_id}`
- [ ] `POST /tools/einvoice-scan/export`
- [ ] `GET /tools/einvoice-scan/handoff-qr`
- [ ] `GET /tools/einvoice-scan/period-info`
- [ ] `POST /tools/einvoice-scan/scan`
- [ ] `POST /tools/einvoice-scan/scan-text`
- [ ] `GET /tools/einvoice-scan/settings`
- [ ] `PUT /tools/einvoice-scan/settings`
- [ ] `POST /tools/einvoice-scan/settings/reset`

**image-to-pdf（圖片轉 PDF）**

- [ ] `POST /tools/image-to-pdf/delete/{fid}`
- [ ] `GET /tools/image-to-pdf/full/{fid}`
- [ ] `POST /tools/image-to-pdf/generate`
- [ ] `GET /tools/image-to-pdf/thumb/{fid}`
- [ ] `POST /tools/image-to-pdf/upload`

**markdown-to-doc（Markdown 轉辦公文件）**

- [ ] `POST /tools/markdown-to-doc/convert`
- [ ] `GET /tools/markdown-to-doc/download/{upload_id}/{fmt}`
- [ ] `GET /tools/markdown-to-doc/preview/{upload_id}/{page}`

**office-convert（辦公文件格式互轉）**

- [ ] `POST /tools/office-convert/convert`
- [ ] `GET /tools/office-convert/formats`
- [ ] `POST /tools/office-convert/submit`

**office-to-pdf（辦公文件轉 PDF）**

- [ ] `POST /tools/office-to-pdf/submit`

**pdf-annotations（註解整理）**

- [ ] `POST /tools/pdf-annotations/analyze`
- [ ] `POST /tools/pdf-annotations/export-csv`
- [ ] `POST /tools/pdf-annotations/export-json`
- [ ] `POST /tools/pdf-annotations/export-review`
- [ ] `POST /tools/pdf-annotations/export-todo`
- [ ] `GET /tools/pdf-annotations/preview/{upload_id}/{page}`

**pdf-annotations-flatten（註解平面化）**

- [ ] `POST /tools/pdf-annotations-flatten/analyze`
- [ ] `GET /tools/pdf-annotations-flatten/baked-download/{baked_uid}`
- [ ] `GET /tools/pdf-annotations-flatten/baked-preview/{baked_uid}/{page}`
- [ ] `POST /tools/pdf-annotations-flatten/flatten`

**pdf-annotations-strip（註解清除）**

- [ ] `POST /tools/pdf-annotations-strip/analyze`
- [ ] `GET /tools/pdf-annotations-strip/preview/{upload_id}/{page}`
- [ ] `POST /tools/pdf-annotations-strip/strip`

**pdf-attachments（PDF 附件萃取）**

- [ ] `GET /tools/pdf-attachments/file/{uid}/{name}`
- [ ] `POST /tools/pdf-attachments/scan`
- [ ] `POST /tools/pdf-attachments/strip`
- [ ] `GET /tools/pdf-attachments/stripped/{uid}`
- [ ] `POST /tools/pdf-attachments/zip`

**pdf-bookmark（書籤與目錄）**

- [ ] `POST /tools/pdf-bookmark/auto-detect`
- [ ] `GET /tools/pdf-bookmark/download/{upload_id}`
- [ ] `POST /tools/pdf-bookmark/load`
- [ ] `POST /tools/pdf-bookmark/parse-list`
- [ ] `POST /tools/pdf-bookmark/submit`
- [ ] `GET /tools/pdf-bookmark/thumb/{upload_id}/{page_no}`
- [ ] `POST /tools/pdf-bookmark/toc-preview`
- [ ] `POST /tools/pdf-bookmark/validate`

**pdf-border（頁面加框）**

- [ ] `POST /tools/pdf-border/load`
- [ ] `POST /tools/pdf-border/preview`
- [ ] `POST /tools/pdf-border/submit`
- [ ] `GET /tools/pdf-border/thumb/{upload_id}/{page}`

**pdf-compress（PDF 壓縮）**

- [ ] `POST /tools/pdf-compress/analyze`
- [ ] `POST /tools/pdf-compress/submit`

**pdf-decrypt（PDF 密碼解除）**

- [ ] `POST /tools/pdf-decrypt/submit`

**pdf-editor（PDF 編輯器）**

- [ ] `GET /tools/pdf-editor/assets`
- [ ] `POST /tools/pdf-editor/detect-objects`
- [ ] `GET /tools/pdf-editor/download/{upload_id}`
- [ ] `GET /tools/pdf-editor/file/{upload_id}`
- [ ] `GET /tools/pdf-editor/fonts`
- [ ] `POST /tools/pdf-editor/list-objects`
- [ ] `POST /tools/pdf-editor/load`
- [ ] `GET /tools/pdf-editor/preview/{filename}`
- [ ] `POST /tools/pdf-editor/replace-all-fonts`
- [ ] `POST /tools/pdf-editor/save`
- [ ] `POST /tools/pdf-editor/undo-replace-all-fonts`
- [ ] `POST /tools/pdf-editor/upload-image`

**pdf-encrypt（PDF 密碼保護）**

- [ ] `POST /tools/pdf-encrypt/submit`

**pdf-extract-images（擷取圖片）**

- [ ] `POST /tools/pdf-extract-images/extract`
- [ ] `GET /tools/pdf-extract-images/file/{batch_id}/{name}`
- [ ] `POST /tools/pdf-extract-images/load`
- [ ] `GET /tools/pdf-extract-images/page-thumb/{upload_id}/{page}`
- [ ] `POST /tools/pdf-extract-images/submit`
- [ ] `POST /tools/pdf-extract-images/zip-selected`

**pdf-extract-text（擷取文字）**

- [ ] `GET /tools/pdf-extract-text/download/{batch_id}/{fmt}`
- [ ] `POST /tools/pdf-extract-text/extract`
- [ ] `POST /tools/pdf-extract-text/llm-reflow`

**pdf-fill（表單自動填寫）**

- [ ] `GET /tools/pdf-fill/download/{upload_id}`
- [ ] `GET /tools/pdf-fill/history`
- [ ] `POST /tools/pdf-fill/history/bulk-delete`
- [ ] `POST /tools/pdf-fill/history/{hid}/delete`
- [ ] `GET /tools/pdf-fill/history/{hid}/file/{kind}`
- [ ] `POST /tools/pdf-fill/history/{hid}/refill`
- [ ] `POST /tools/pdf-fill/learn-synonym`
- [ ] `POST /tools/pdf-fill/llm-review-apply`
- [ ] `GET /tools/pdf-fill/llm-review-result/{job_id}`
- [ ] `POST /tools/pdf-fill/llm-review-start`
- [ ] `POST /tools/pdf-fill/preview`
- [ ] `GET /tools/pdf-fill/preview/{name}`
- [ ] `POST /tools/pdf-fill/regenerate`
- [ ] `POST /tools/pdf-fill/save-template`
- [ ] `POST /tools/pdf-fill/submit`

**pdf-hidden-scan（隱藏內容掃描）**

- [ ] `POST /tools/pdf-hidden-scan/clean`
- [ ] `GET /tools/pdf-hidden-scan/download/{uid}`
- [ ] `POST /tools/pdf-hidden-scan/scan`

**pdf-merge（檔案合併）**

- [ ] `POST /tools/pdf-merge/submit`

**pdf-metadata（中繼資料清除）**

- [ ] `POST /tools/pdf-metadata/analyze`
- [ ] `POST /tools/pdf-metadata/clean`
- [ ] `GET /tools/pdf-metadata/download/{uid}`

**pdf-nup（多頁合併）**

- [ ] `GET /tools/pdf-nup/download/{upload_id}`
- [ ] `POST /tools/pdf-nup/generate`
- [ ] `POST /tools/pdf-nup/load`
- [ ] `POST /tools/pdf-nup/preview`

**pdf-ocr（OCR 文字辨識）**

- [ ] `GET /tools/pdf-ocr/download/{upload_id}`
- [ ] `GET /tools/pdf-ocr/preview/{upload_id}.pdf`
- [ ] `POST /tools/pdf-ocr/run/{upload_id}`
- [ ] `POST /tools/pdf-ocr/upload`

**pdf-page-size（頁面尺寸統一）**

- [ ] `GET /tools/pdf-page-size/download/{upload_id}`
- [ ] `POST /tools/pdf-page-size/load`
- [ ] `POST /tools/pdf-page-size/preview`
- [ ] `POST /tools/pdf-page-size/submit`
- [ ] `GET /tools/pdf-page-size/thumb/{upload_id}/{page_no}`

**pdf-pageno（插入頁碼）**

- [ ] `POST /tools/pdf-pageno/load`
- [ ] `POST /tools/pdf-pageno/preview-thumb`
- [ ] `POST /tools/pdf-pageno/submit`
- [ ] `GET /tools/pdf-pageno/thumb/{upload_id}/{page}`

**pdf-pages（頁面整理）**

- [ ] `POST /tools/pdf-pages/load`
- [ ] `POST /tools/pdf-pages/submit`
- [ ] `POST /tools/pdf-pages/submit-from-upload`
- [ ] `GET /tools/pdf-pages/thumb/{upload_id}/{page}`

**pdf-rotate（頁面轉向）**

- [ ] `POST /tools/pdf-rotate/finalize`
- [ ] `POST /tools/pdf-rotate/finalize-png`
- [ ] `POST /tools/pdf-rotate/load`
- [ ] `POST /tools/pdf-rotate/submit`
- [ ] `GET /tools/pdf-rotate/thumb/{upload_id}/{page}`

**pdf-seam-stamp（騎縫章）**

- [ ] `POST /tools/pdf-seam-stamp/assembled`
- [ ] `POST /tools/pdf-seam-stamp/load`
- [ ] `POST /tools/pdf-seam-stamp/preview`
- [ ] `POST /tools/pdf-seam-stamp/stamp-preview`
- [ ] `POST /tools/pdf-seam-stamp/stamp-upload`
- [ ] `POST /tools/pdf-seam-stamp/submit`
- [ ] `GET /tools/pdf-seam-stamp/thumb/{upload_id}/{page_no}`

**pdf-split（頁面分拆）**

- [ ] `POST /tools/pdf-split/submit`

**pdf-stamp（用印與簽名）**

- [ ] `GET /tools/pdf-stamp/pdf-preview/{upload_id}`
- [ ] `POST /tools/pdf-stamp/preview`
- [ ] `POST /tools/pdf-stamp/preview-all-pages`
- [ ] `GET /tools/pdf-stamp/preview-bg/{upload_id}/{page_idx}`
- [ ] `POST /tools/pdf-stamp/preview-stamped`
- [ ] `GET /tools/pdf-stamp/preview/{name}`
- [ ] `POST /tools/pdf-stamp/render-date`
- [ ] `POST /tools/pdf-stamp/render-restrict-stamp`
- [ ] `GET /tools/pdf-stamp/restrict-fonts`
- [ ] `GET /tools/pdf-stamp/restrict-templates`
- [ ] `POST /tools/pdf-stamp/submit`

**pdf-to-image（辦公文件轉圖片）**

- [ ] `POST /tools/pdf-to-image/convert`
- [ ] `GET /tools/pdf-to-image/download/{upload_id}`
- [ ] `GET /tools/pdf-to-image/preview/{filename}`

**pdf-to-markdown（PDF 轉 Markdown）**

- [ ] `POST /tools/pdf-to-markdown/convert`
- [ ] `GET /tools/pdf-to-markdown/download/{upload_id}/{kind}`
- [ ] `GET /tools/pdf-to-markdown/pdf/{upload_id}`

**pdf-to-office（PDF 轉文書檔（Beta））**

- [ ] `POST /tools/pdf-to-office/convert`
- [ ] `GET /tools/pdf-to-office/preview/{job_id}/{kind}`
- [ ] `GET /tools/pdf-to-office/preview/{job_id}/{kind}/{page}`
- [ ] `GET /tools/pdf-to-office/report/{job_id}`
- [ ] `POST /tools/pdf-to-office/submit`
- [ ] `POST /tools/pdf-to-office/upload`

**pdf-to-slides（PDF 轉簡報）**

- [ ] `POST /tools/pdf-to-slides/convert`
- [ ] `GET /tools/pdf-to-slides/preview/{job_id}/{kind}`
- [ ] `GET /tools/pdf-to-slides/preview/{job_id}/{kind}/{page}`
- [ ] `POST /tools/pdf-to-slides/submit`
- [ ] `POST /tools/pdf-to-slides/upload`

**pdf-watermark（浮水印）**

- [ ] `POST /tools/pdf-watermark/batch/create`
- [ ] `POST /tools/pdf-watermark/batch/{batch_id}/add`
- [ ] `POST /tools/pdf-watermark/batch/{batch_id}/process`
- [ ] `POST /tools/pdf-watermark/preview`
- [ ] `POST /tools/pdf-watermark/preview-watermarked`
- [ ] `GET /tools/pdf-watermark/preview/{name}`
- [ ] `POST /tools/pdf-watermark/submit`
- [ ] `GET /tools/pdf-watermark/text-png`

**pdf-wordcount（字數統計）**

- [ ] `POST /tools/pdf-wordcount/analyze`
- [ ] `POST /tools/pdf-wordcount/analyze-multi`
- [ ] `POST /tools/pdf-wordcount/analyze-text`
- [ ] `POST /tools/pdf-wordcount/export-csv`

**scan-merge（掃描拼合）**

- [ ] `GET /tools/scan-merge/crop/{cid}/{variant}`
- [ ] `POST /tools/scan-merge/delete/{cid}`
- [ ] `POST /tools/scan-merge/generate`
- [ ] `GET /tools/scan-merge/source/{sid}`
- [ ] `POST /tools/scan-merge/upload`

**submission-check（送件前檢核）**

- [ ] `GET /tools/submission-check/admin-stats`
- [ ] `DELETE /tools/submission-check/case/{case_id}`
- [ ] `GET /tools/submission-check/case/{case_id}`
- [ ] `GET /tools/submission-check/cases`
- [ ] `GET /tools/submission-check/file/{case_id}/{file_id}`
- [ ] `POST /tools/submission-check/override/{case_id}`
- [ ] `DELETE /tools/submission-check/override/{case_id}/{finding_key}`
- [ ] `GET /tools/submission-check/page-preview/{case_id}/{file_id}/{page}`
- [ ] `GET /tools/submission-check/result/{case_id}/{version}`
- [ ] `POST /tools/submission-check/run/{case_id}`
- [ ] `GET /tools/submission-check/self-entities`
- [ ] `POST /tools/submission-check/upload`

**text-deident（文字去識別化）**

- [ ] `POST /tools/text-deident/detect`
- [ ] `POST /tools/text-deident/download`
- [ ] `POST /tools/text-deident/extract-text`
- [ ] `POST /tools/text-deident/process`

**text-diff（文字差異比對）**

- [ ] `POST /tools/text-diff/compare`

**text-list（清單處理）**

- [ ] `POST /tools/text-list/export/{fmt}`
- [ ] `POST /tools/text-list/process`
- [ ] `POST /tools/text-list/upload`

**transit-proof（乘車證明整理）**

- [ ] `DELETE /tools/transit-proof/buffer`
- [ ] `GET /tools/transit-proof/buffer`
- [ ] `GET /tools/transit-proof/file/{entry_id}` —— 看**原始乘車證明**。
      驗收：①自己的那筆點得開、回的是 PDF ②**拿別人的 entry_id 一律 404**
      （歸屬由路徑結構決定：檔案在 `<使用者雜湊>/` 底下，路徑從當前登入者算出）
      ③清空清單之後再點就 404（檔案要跟著清掉）
- [ ] `POST /tools/transit-proof/buffer/delete-batch`
- [ ] `POST /tools/transit-proof/entry/{entry_id}`
- [ ] `DELETE /tools/transit-proof/entry/{entry_id}`
- [ ] `POST /tools/transit-proof/export`
- [ ] `POST /tools/transit-proof/settings`
- [ ] `GET /tools/transit-proof/settings`
- [ ] `POST /tools/transit-proof/upload`

**translate-doc（逐句翻譯）**

- [ ] `POST /tools/translate-doc/export`
- [ ] `POST /tools/translate-doc/extract-text`
- [ ] `GET /tools/translate-doc/job/{job_id}`
- [ ] `POST /tools/translate-doc/start`
- [ ] `POST /tools/translate-doc/translate-batch`
- [ ] `POST /tools/translate-doc/translate-one`

**vat-lookup（統編查詢）**

- [ ] `POST /tools/vat-lookup/batch`
- [ ] `GET /tools/vat-lookup/db-info`
- [ ] `POST /tools/vat-lookup/lookup`
- [ ] `POST /tools/vat-lookup/search`
- [ ] `POST /tools/vat-lookup/stats`


## 4.5 壓力測試 🆕（v1.7.50+）

詳細跑法 / 驗收門檻 / 歷史紀錄見獨立文件 **[STRESS_TEST.md](STRESS_TEST.md)**（涵蓋 1 / 5 / 10 / 30 / 50 並行使用者場景，輕重型工具混合）。

- [ ] 1 user 跑過：p95 < 500 ms 100% 成功
- [ ] 5 users 跑過：吞吐有上升、成功率 100%
- [ ] 10 users 跑過：p95 < 1500 ms、成功率 ≥ 99%
- [ ] 30 users 跑過：成功率 ≥ 98%
- [ ] 50 users 跑過：成功率 ≥ 95%
- [ ] 任一階段成功率突降 → 看 server log 找 root cause

## 5. 發版前最終檢查

1. `git status` 沒有未追蹤的暫存檔
2. `pytest` 全數綠燈
3. **文件 / 設定備份涵蓋度檢查**（v1.14.6 起列為發版必跑）：
   ```bash
   python tools/check_docs_tool_coverage.py        # 工具是否都寫進 README / 介紹站
   python tools/check_settings_export_coverage.py  # 新設定檔是否都納入「設定備份 / 匯入」
   python tools/check_version_consistency.py       # 五處版本號一致
   ```
   後者是 v1.14.6 補上的：`settings_export.CATEGORIES` 是**人工維護**的清單，加新設定
   檔漏加不會有任何錯誤訊息，只有客戶搬機還原後才會發現設定不見了（該版一次補了
   16 項，其中 `sso_settings.json` 從 v1.12.0 起就沒被備份過，而「認證設定」分類的
   說明卻寫著含 OIDC / SAML）。
4. **認證開 / 關兩種模式的全功能矩陣**（v1.14.6 起列為發版必跑）：
   ```bash
   uv run pytest tests/test_auth_modes_matrix.py -v
   ```
   這個專案幾乎每條路徑都有兩種行為（工作區儲存鍵、作業歸屬、通知偏好、權限閘、
   admin 頁可見性…），而**很容易只顧到一邊** —— 例如新的 admin 頁忘了掛權限
   dependency，在單機模式下完全看不出來（那時本來就全員放行）。人工把 41 個工具
   在兩種模式各點一遍不現實，所以用同一組斷言自動跑兩遍。
5. **資安測試計畫全數通過**（v1.14.6 起）：見 `TEST_PLAN_SECURITY.md` ——
   自動化 18 支測試檔 + 滲透測試腳本（7 類 + 反向對照）+ bandit + ZAP 兩目標
   （High / Medium / Low 全 0）。
6. **OWASP regression 全數綠燈**（v1.5.3 起列為發版必跑）：
   ```bash
   uv run pytest -v \
       tests/test_owasp_top10.py \
       tests/test_llm_url_ssrf.py \
       tests/test_path_traversal_audit.py \
       tests/test_version_consistency.py \
       tests/test_redos_ad_dn.py
   ```
   `test_version_consistency` 確保 `app/main.py:VERSION` / `pyproject.toml` / `uv.lock` / `README` / `CHANGELOG` 五處版本號完全一致（v1.5.3 慘案訓練）。
7. 重啟 server，所有路由 200（以 curl 跑 1.1 列表）
8. 手動跑一輪 2.x 清單
9. 跑完 §6 「歷史回歸案例」清單
10. 更新 `app/main.py` `VERSION` + `pyproject.toml` `version` + `github/CHANGELOG.md` 加一筆 + `github/README.md` 標題版號
11. 重啟，確認 footer 顯示新版本號
12. 確認停用的工具（`aes-zip`）仍保留程式碼但未顯示於側欄／首頁
13. **推 GitHub 後 5–15 分鐘**檢查 GitHub native scan：
    - <https://github.com/jasoncheng7115/jt-doc-tools/security/dependabot> — Open alert 數應持平或下降
    - <https://github.com/jasoncheng7115/jt-doc-tools/security/code-scanning> — CodeQL 新警告當天處理或記入「已知議題」

## 6. 歷史回歸案例（每次發版必過）

每條附「修在哪個版本」+「測試方法」+「預期行為」。任一條 fail 視為 regression 必須修復才能發版。

### 6.1 pdf-editor

- [ ] **OCR 中文亂碼擷取** (v1.2.4 / v1.2.5)
  - 上傳 `~/Nextcloud/文件檔/Proxmox VE 手冊/1 Proxmox VE 準備與安裝.pdf`
  - 點選原 PDF 上「網路基本設定」→ 應顯示「網路基本設定」（非「翕⊕ㄱ」之類）
  - 點選「登入系統」→ 應顯示「登入系統」
  - 預期：自動 OCR 重建、訊息「已用 OCR 自動辨識…」

- [ ] **OCR 西文字型用 eng-only** (v1.3.1)
  - 同上 PDF，點選「Proxmox VE」(OpenSans-Bold 字型) → 應顯示「Proxmox VE」(非「ProXimoxX VE」)

- [ ] **OCR 短標題 padding 不抓鄰近 span** (v1.2.5)
  - 「網路基本設定」OCR 結果不應含前後鄰近文字（不是「VE 網路基本設定一」）

- [ ] **OCR 等待時提示** (v1.3.4)
  - 點選需 OCR 的文字 → 500ms 後狀態列應顯示「辨識中…（原文字字型無 Unicode 對應表，正在 OCR 重建文字）」

- [ ] **既有透明 PNG 擷取保留 alpha** (v1.3.3)
  - PDF 內含透明背景 + 陰影圖片時，點選 → 擷取出來的圖**不可變黑底**

- [ ] **undo 到最早不會 redact 既有物件** (v1.1.99)
  - 載入 PDF → 點擷取一段文字 → undo 回到最早
  - 預期：BG 重新渲染後，原 PDF 文字仍完整顯示（不該變空白）

- [ ] **存檔後既有物件不重影** (v1.1.97)
  - 點擷取既有文字後存檔 → 預覽 BG 已含新文字，且 Fabric 上的同位置物件 fade 到 opacity 0.01
  - 預期：不該看到「BG 文字 + Fabric 文字」雙層重影

- [ ] **下載按鈕** (v1.1.96)
  - 純 anchor + download attribute；按下要觸發瀏覽器下載 dialog
  - 若特定瀏覽器不下載，先請使用者開無痕視窗排除擴充功能

### 6.34 v1.14.54 — 壞掉的文字對應表要從字形反查，不可以用 OCR（每次發版必過）

客戶回報：PDF 編輯器點文件上原本的中文，文字框裡整排變成 `••••••`。

- [ ] **圓點型的擷取失敗要被抓到**
  - `tests/test_placeholder_extraction.py` 全綠
  - 舊的 `_looks_garbled` 對 `•`（U+2022，一般標點區）是無感的 ——
    `●`(U+25CF) / `□`(U+25A1) 落在 Geometric Shapes 所以抓得到，圓點抓不到
- [ ] **判斷靠寬度不靠字元**
  - 真的點引導符（`目錄………12`）每點只有 0.2～0.35 字寬 → 不可被判為壞掉
  - 擷取壞掉時每個「點」佔滿一個中文字寬
- [ ] **還原走字形反查，不是 OCR**
  - `tests/test_glyph_text_recovery.py` 全綠；`recovered_from_font=true`、
    `ocr_used=false`
  - 反查**不可以多吃隔壁的字**（水平範圍必須是半開區間 —— 下一個字的原點
    正好落在這個框的右緣）
  - 反查到控制字元一律當作查不到（實測在真實表單上踩到 NUL）
  - 查不齊時**整段放棄**，不可以吐半段正確半段問號
- [ ] **不可以把本來正確的文字改壞**（這條比原本的 bug 更嚴重）
  - 拿 `temp_pdfs/` 全部樣本掃一遍被判不可靠的 span，比對「反查結果 vs 原擷取」
  - 判準：**不同的必須是 0**（v1.14.54 實測 相同 54｜不同 0｜查不到 100）
- [ ] **旗標語意**：字形反查成功時 `extracted_text_unreliable` 要是 false
  - 前端是先看這個旗標就直接放棄，忘了清會變成「已經還原出文字，使用者
    卻還是拿不到」（真實瀏覽器測試抓到的）
- [ ] **內部自動 OCR 不可以打掛服務**
  - 缺 AVX2 的機器上本機 EasyOCR 會 SIGILL；`tests/test_ocr_avx2_guard.py` 全綠
  - 注意「選 tesseract 也會反向掉回 EasyOCR」那條路徑
  - OCR 工具的手動引擎切換行為**不變**（沿用「不自動退」的決定）
- [ ] **真實瀏覽器**：`temp/editor-dots/cdp_dots_test.py` 9/9

### 6.35 v1.14.55 — 端點不可以把整站鎖住（每次發版必過）

2026-08-26 使用者實測：跑「文件去識別化」時**全站兩分鐘完全不回應**。
日誌是「事件迴圈被卡住 116.4 秒 / 慢請求 116.9 秒 POST /tools/doc-deident/process」，
而當下作業佇列是空的 —— 同步的重活直接跑在事件迴圈裡。

- [ ] `tests/test_no_blocking_endpoints.py` 全綠
- [ ] 同形狀的端點**只准變少不准變多**（`KNOWN_REMAINING`），新工具不可以再犯
- [ ] 判讀陷阱：watchdog 警告寫「調低最大同時作業數可緩解」，兇手是同步的
      請求處理函式時**照那句去調完全沒用**

### 6.36 v1.14.55 — 文件去識別化的替換模式（每次發版必過）

- [ ] **原值一定要真的消失**：處理完把 PDF 的文字抽出來，原本的身分證 / 電話
      **一個都不可以還找得到**（看起來處理過了但抽得出來，是這類工具最要命的失敗）
- [ ] **同一個原值固定對應同一個假值** —— 否則一份報表裡同一個客戶會變三個人
- [ ] **預設的假值不可以通過檢查碼**（不會撞到真人資料）；打開「可通過驗證」
      之後身分證 / 統編 / 信用卡要真的通過（否則「適合拿去測試」是空話）
- [ ] Email 用 `example.com`、IP 用 `192.0.2.x`、MAC 用 `00:00:5E`（保留範圍）
- [ ] **格式要保住**：身分證 10 碼、信用卡 16 碼、銀行帳號的分隔符號位置不變
- [ ] **太長要自動縮字**塞回原框（不縮會壓到隔壁欄位，而且是無聲的）
- [ ] 手動改過的替換值，切換「可通過驗證」開關時**不可以被洗掉**
- [ ] 自訂字詞（`/find`）：找得到位置、有建議的替換值、**別人的 upload_id 拿不到東西**
- [ ] 公開 API 同步支援（`mode=replace` / `replacements` / `valid_checksum`）
- [ ] 真實瀏覽器：`temp/deident-replace/cdp_replace_test.py` 8/8

### 6.37 v1.14.56 — 端點一律不可以鎖住事件迴圈（每次發版必過）

- [ ] `tests/test_no_blocking_endpoints.py` 全綠，且 `KNOWN_REMAINING` **是 0**
- [ ] 新端點要算縮圖 → `await pdf_preview.render_page_png_async(...)`
- [ ] 新端點要轉檔 → `await office_convert.convert_to_pdf_async(...)`（docx / odt 同）
- [ ] 其他重活 → 包成同步閉包再 `await asyncio.to_thread(_work)`
- [ ] **判定的兩個陷阱**（都踩過）：
      ①「巢狀函式裡的重活不算」是錯的 —— 包成閉包正是修法本身，只看巢不巢狀的話，
      有人把 `await to_thread(_work)` 改回 `_work()` 反而抓不到。要看**有沒有真的
      派到別的執行緒**（`to_thread` / 背景作業）。
      ②交給 `job_manager` 的閉包**不算阻塞**，把它們算進來會讓數字虛胖，
      虛胖的指標沒人會認真看。
- [ ] 管理區的端點定義在 `build_router()` **裡面**，縮排比模組層級多一層 ——
      自動化改寫時縮排要從 AST 的 `col_offset` 取，寫死會產生
      `await outside async function`

### 6.38 v1.14.56 — 測試計畫本身要有守門（每次發版必過）

發版門檻是照這份計畫跑的，**計畫漏了什麼那塊就等於沒驗過**，而且報告看起來
仍然全綠。

- [ ] `tests/test_test_plan_coverage.py` 全綠
- [ ] 新工具 / 新 API / 新管理頁沒寫進計畫要紅燈（從路由表與註冊表實算，
      不寫死期望值 —— 寫死的數字自己就是下一個會漂的東西）
- [ ] 計畫裡**指令引用的檔案**必須存在（照抄會 file not found 的那種，
      2026-08-16 稽核踩過：一個不存在的測試檔被當成發版門檻）
- [ ] 掃描只掃**指令行**不掃說明文字 —— 說明裡會引用「當初寫錯的檔名」當反例

### 6.39 v1.14.57 — 壞掉的文字對應表：抽取類工具也要還原（每次發版必過）

v1.14.54 只修了 PDF 編輯器；擷取文字、字數統計、逐句翻譯走同一條路徑、
同一個盲點（同一份檔案抽出來是 `••••••`，字數算成 0 個中文字）。

- [ ] `tests/test_extract_text_glyph_repair.py` 全綠
- [ ] **正常的 PDF 一個位元都不可以變** —— `page_text_repaired()` 對正常頁面
      要回 `None`（代表「照原本的路徑走」）。這是整個修法的安全閥：為了救
      1% 的壞檔把 99% 的好檔弄出細微差異（斷行、空白）是更糟的結果
- [ ] 拿 `temp_pdfs/` 真實樣本掃過，**被判成「壞掉」的頁面數必須是 0**
- [ ] 三種回傳值語意不可混：`None`（本來就好）/ 還原後的字 / `""`（確定壞掉
      但救不回，呼叫端該丟掉那段）
- [ ] 字數統計檔案裡有**三處**各自抽取文字 —— 只改一處會出現「API 對了、
      網頁還是錯的」

### 6.40 v1.14.57 — 真實樣本要被拿來測（每次發版必過）

`temp_pdfs/` 有 29 份真實廠商表單 + 6 份 Office 檔，但 2026-08-27 盤點時發現
**只有「表單自動填寫回歸」在用**，其餘工具的測試全部跑合成 PDF。而真正的意外
都在真實檔案裡：壞掉的文字對應表、Wingdings 核取方塊、直書、掃描件、奇怪的
表格版型、旋轉頁、缺字型 —— 這些合成檔造不出來（兩天內連續踩到三種）。

- [ ] `tests/test_real_samples_smoke.py` 全綠（22 支工具 × 29 份樣本，約 2 分鐘）
- [ ] 判準是「**不可以炸掉**」：不回 5xx、不拋例外
- [ ] 另外驗**抽文字類工具真的抽得到字** —— 只驗狀態碼不夠，客戶回報的那次
      就是一路回 200 但內容是一整片圓點
- [ ] 樣本沒了要**看得見地 skip**，不可以安靜跳過（少跑一項比跑出紅字危險，
      因為報告看起來仍然是綠的）
- [ ] **樣本含客戶資料**：測試只看狀態碼與結構，不印內容、不寫出任何檔案

### 6.41 v1.14.58 — 刪使用者不可以卡住整站（每次發版必過）

客戶回報：「刪 user 會卡住，多刪幾個系統就像掛掉」「超久才回應」。三個原因疊在一起。

- [ ] `tests/test_db_query_plans.py` 全綠 —— **熱路徑的 SQL 不可以出現 `SCAN`**
      （`group_members` 的主鍵是 `(group_id, user_id)`，用 user_id 單獨查
      **用不到**那個索引，而刪 users 會觸發它的 CASCADE）
- [ ] `tests/test_no_blocking_endpoints.py` 的 `MUST_OFFLOAD` 全綠 ——
      重活**藏在被呼叫的函式裡**時掃描抓不到，只能逐支列管
- [ ] 前端每筆操作後**不可以 `location.reload()`** —— 使用者管理頁要統計整個
      目錄（客戶 18,611 位），刪十筆等於重算十次
- [ ] 這類缺陷**從功能測試看不出來**（功能完全正確，只是慢，而且要資料量夠大
      才看得出來）→ 一律用查詢計畫驗，不要用計時（計時在小資料上永遠是綠的）

### 6.42 v1.14.58 — 批次刪除使用者（每次發版必過）

- [ ] `tests/test_users_bulk_ops.py` 的批次刪除項全綠
- [ ] 內建管理員 / 內建稽核員 / 自己 → 跳過但**其他照刪**（一顆地雷不該讓整批停擺）
- [ ] **先整批試算再動手**：刪完一個管理員都不剩就整批中止（刪到一半才發現
      就救不回來了）
- [ ] 寫稽核（不可逆的操作一定要留紀錄）
- [ ] 前端要求**打字確認數量**
- [ ] **測試的兩個陷阱**（都踩過）：①`admin_session` 登入的就是 seed 管理員本人，
      拿他去刪自己會被「不可刪除自己」擋掉 —— seed 那條防線等於沒驗到，要用
      **另一個管理員**登入才測得到；②端點與 `user_manager` 兩層防線**各自都夠**，
      只拔一層變異不會紅，要同時拔掉才驗得出測試有沒有牙齒

### 6.43 v1.14.58 — 作業的三個時間點（每次發版必過）

- [ ] `tests/test_job_timestamps.py` 全綠
- [ ] 送出 / 開始 / 結束都要**存進資料庫**（開始時間原本只活在記憶體裡）
- [ ] **從資料庫還原作業時要帶回 `started_at`** —— 少了這步，之後任何一次
      upsert 都會把值寫成 NULL，資料靜靜地不見
- [ ] 舊資料沒有這個值 → 顯示「—」，**不可以拿送出時間硬湊**

### 6.44 v1.14.58 — 自訂字型的顯示名稱（每次發版必過）

- [ ] `tests/test_font_display_names.py` 全綠
- [ ] 上傳後顯示的是**字型檔內建的名稱**，不是檔名
- [ ] 管理員可以自訂；**留空會退回內建名稱**，不是退回檔名
- [ ] 名稱會反映到 **PDF 編輯器的字型下拉**（`label` 欄位）—— 後端有值但下拉
      沒變等於沒做
- [ ] 壞掉的字型檔要安靜回空字串，不可以讓整份清單掛掉
- [ ] 系統字型不可改名（掃出來的，改了下次掃描就沒了）；`custom:` 之外的
      id 一律拒絕，路徑要限制在自訂字型資料夾內

### 6.45 v1.14.59 — 可上傳的檔案大小（每次發版必過）

- [ ] `tests/test_upload_limits.py` 全綠
- [ ] 反向代理的上限用 `Expect: 100-continue` 問，**不可以真的傳檔案去測**
      （伺服器會在讀完 body 前就回應，測出來的數字不可信）
- [ ] **問不到要說問不到** —— 回一個看起來像答案的數字比沒有這個功能更糟
- [ ] 清單裡「可調 / 寫死」的標示要誠實（說可調卻寫死 → 管理員會去找一個
      不存在的設定欄位）
- [ ] 探測端點**不可以接受呼叫端指定目標** —— 那就是 SSRF
- [ ] `POST /admin/api/upload-limit/probe` —— 只有管理員可用；目標綁死在當前
      連線的 Host；會寫稽核（`upload_limit_probe`）

### 6.46 v1.14.59 — 以文件為單位的快取不可以放模組層級（每次發版必過）

- [ ] `tests/test_glyph_text_recovery.py` 的 `test_page_cache_lives_on_the_document` 全綠
- [ ] **不可以用 `id(doc)` 當鍵**：文件每個請求開一份、用完就關，`id()` 會被
      重複使用 → 下一份文件讀到上一份的資料 → **反查出別份文件的字**，無聲
- [ ] 判斷指紋：**單跑全綠、合跑失敗，而且每次失敗的項目還不一樣**
- [ ] 行為測試（跨文件不污染）對這個變異**沒有牙齒**（id 重用不保證發生），
      要靠結構測試釘住「快取掛在文件物件上」

### 6.47 v1.14.60 — 面板收折與標題圖示（每次發版必過）

- [ ] `temp/ui-1458/cdp_sysstatus.py` 7/7（真實瀏覽器）
- [ ] **收折要驗「點了之後 class 真的變」**，不是只看標題存在 —— 這個 bug 的
      本質就是「看起來一樣、但點了沒反應」
- [ ] 全站收折機制的條件是 **`<h2>` 必須是 `.panel` 的第一個子元素**
      （`static/js/toast.js`）。包在 `<div>` 裡就接不到，而且完全無聲
- [ ] **HTML 樣板裡的字串不可以寫 markdown** —— `**粗體**` 會原樣印出星號。
      判準：頁面文字裡不可以出現 `**`
- [ ] 新增區塊標題時挑**語意相符**的圖示；圖示名稱要真的存在
      （`components/icons.html`，用不存在的名字不會報錯、只是沒圖）

### 6.2 圖片轉 PDF (image-to-pdf, v1.3.0+)

- [ ] **拖曳多張圖片** → 縮圖網格出現
- [ ] **再加圖片** → 已存在的縮圖不被覆蓋，新的加在後面
- [ ] **拖曳重新排序** → 順序變更後產生的 PDF 對應新順序
- [ ] **逐頁旋轉** (↺ / ↻) → 縮圖視覺旋轉、PDF 對應頁旋轉
- [ ] **逐頁刪除** (×) → 縮圖移除，產出 PDF 不含該頁
- [ ] **頁面大小：原始** → 每頁尺寸等於圖片尺寸
- [ ] **頁面大小：A4** → 全部頁面 A4，圖片置中、依比例自動轉向
- [ ] **邊距 10mm** → 圖片離邊 10mm
- [ ] **背景色** → 非「原始」時 letterbox 區用此色
- [ ] **EXIF 自動正向** → 手機照片不應躺著
- [ ] **HEIC / WebP / TIFF** 格式接受
- [ ] **公開 API** `POST /tools/image-to-pdf/api/image-to-pdf`（form-data 多檔）回 PDF 檔
- [ ] 縮圖右上紅色 × **一直顯示**（不靠 hover）
- [ ] 設定面板 4 列 label 對齊整齊、說明文字看得出歸屬哪一列

### 6.3 jtdt CLI

- [ ] **`jtdt`（無參數）印分組指令清單** (v1.3.6)
  - 不應只印一行 `usage: jtdt [-h] {start,stop,...}`
  - 應分「服務控制 / 升級與維護 / 緊急復原」三組

- [ ] **`jtdt update` 拒絕降版** (v1.3.5)
  - 在 origin 改成過期 file:// 的測試環境上跑 `jtdt update`
  - 預期：偵測新版 < 舊版 → abort + 還原 + 印 git remote 修復指令

- [ ] **`jtdt update` 處理 force-pushed remote** (v1.2.3)
  - 用 `git reset --hard origin/main` 而非 `git pull --ff-only`
  - 預期：force-pushed 的 origin 也能順利升級，不會「Not possible to fast-forward」

- [ ] **`jtdt update` 自動補裝系統相依** (v1.2.2+)
  - 缺 tesseract 時自動 `apt/brew/winget install`
  - 失敗只 warn 不 abort 升級
  - 結尾印「相依套件狀態」表

- [ ] **`jtdt auth show / disable / set-local`** 不需 service running 也能跑（緊急復原）
- [ ] **`jtdt reset-password <user>`** 同上

### 6.4 相依套件檢查 (admin/sys-deps, v1.2.3+)

- [ ] 設定區第一個項目顯示「**相依套件檢查**」
- [ ] 頁面顯示 stat cards（就緒 / 必要相依缺 / 選用相依缺）
- [ ] tesseract / Office / CJK 字型 / pytesseract / Pillow 各一列
- [ ] 缺漏項目顯示對應平台的安裝指令（Linux: apt / macOS: brew / Windows: winget）
- [ ] `GET /admin/api/sys-deps` 回 JSON

### 6.5 認證設定 lockout 防呆 (v1.3.14)

- [ ] **未啟用認證時，/admin/auth-settings 下方 backend 設定整段鎖定**：
  - 黃底 banner「請先啟用認證才能設定 backend」顯示
  - LDAP 表單灰階、不可輸入、tab 跳過 (`inert` 屬性)
  - 「驗證測試」按鈕同樣 inert
- [ ] **backend 防線**：未啟用時 `POST /admin/auth-settings/ldap-save` 直接回 HTTP 409，body 含「Cannot configure LDAP/AD backend before authentication is enabled」
- [ ] **完整鎖死情境驗證**：未啟用狀態下 curl POST `backend=ad` → 1) 回 409、2) `auth_settings.json` 內 `backend` 仍是 `off`（未被改）、3) `GET /admin/auth-settings` 仍回 HTTP 200。**任何一步失敗 = 客戶會被鎖在外面，必修。**

### 6.6 升級流程 (含 DB migration)

- [ ] 從 v1.0.x 升到目前版本，所有 migration 跑完不報錯
- [ ] v3 migration: pdf-diff → doc-diff 既有 perms 遷移
- [ ] v4 migration: 既有 pdf-to-image 權限自動授予 image-to-pdf
- [ ] 升級後 default-user / clerk role 含新工具權限
- [ ] 升級後 service user 仍能讀 .venv 內檔案（chown 還原正確）

### 6.7 用詞檢查（push 前 grep）

```bash
# 不應出現的中國用語：
grep -rnE "回滾|軟依賴|硬依賴|系統依賴(?!\s*$)|圖像(?![幾何])|軟件|字體|打印|文檔|信息|視頻|網絡|服務器|菜單|屏幕|保存|默認|設置" \
  app/ static/ github/CHANGELOG.md github/README.md --include='*.py' --include='*.html' --include='*.md'
```

- [ ] grep 結果應為空（除了 memory / to_github.md 的解釋脈絡）
- [ ] 「依賴」→「相依」、「回滾」→「還原」、「硬刷」→「強制重新整理」

### 6.8 landing page (`docs/`)

- [ ] 「線上 PDF 工具的隱憂」/ 「地端自架 + 開源 才能安心」字級 24px / 字重 800
- [ ] 工具總數 / 「N 個工具」與 README hero 一致
- [ ] 截圖無內網 IP / browser chrome
- [ ] hero / 安裝指令 tab 切換正常

### 6.9 v1.4.0 — 11 項使用者建議（每次發版必過）

#### 6.9.1 OxOffice X11 runtime libs（fix #9 #10 #11）

- [ ] Fresh Linux Debian/Ubuntu minimal 上跑 `bash install.sh`：自動 `apt install libxinerama1 libxrandr2 ...`，office-to-pdf 不會炸 `libXinerama.so.1`
- [ ] 既有客戶 `sudo jtdt update`：偵測到缺 X11 lib → 自動 `apt install`，summary 表顯示「OxOffice X11 libs：完整」
- [ ] `/admin/sys-deps` 出現「OxOffice / LibreOffice 執行時依賴 X11 lib」項目，全數綠燈
- [ ] 上傳 .docx 到 office-to-pdf → 成功轉成 PDF（不是 oosplash error）
- [ ] 文件差異比對 PDF vs DOCX → 不會卡在「office 轉 PDF 失敗」

#### 6.9.2 pdf-editor 文字物件不可消失（fix #6）

- [ ] 上傳含中文文字的 PDF → 用 pick tool 選一段既有文字 → 顯示 OCR 還原的文字 IText
- [ ] 點空白處 deselect → IText 視覺保留（opacity = 1，不會 fade 變空白）
- [ ] 等 ~1s（auto-save 觸發）→ 重新點該文字位置 → 仍能編輯，不會看到「物件變空白」
- [ ] 直接用 T 工具新增文字 → 輸入 → 點空白 deselect → 文字仍可見

#### 6.9.3 角色管理全選 / 全不選 / 反選（#2）

- [ ] `/admin/roles` 編輯非 admin 角色，看到工具矩陣上方有 `全選` `全不選` `反選` 按鈕 + 計數「已選 X / Y」
- [ ] 點全選 → 所有 checkbox 勾選 + 計數更新
- [ ] 點全不選 → 全部清空 + 計數變 0 / Y
- [ ] 點反選 → 勾選與未勾選對調
- [ ] 個別點 checkbox → 計數即時更新
- [ ] 「儲存」按鈕送出 → role 套用成功

#### 6.9.4 pdf-rotate 預覽頁個別轉向（#3）

- [ ] 上傳多頁 PDF → 縮圖下方出現 `↺ ↻ 180° ⇆ ⇅ ─` 工具列
- [ ] 點 ↻ → 該頁綠框 + 徽章 `★ ↻ 90°`（綠色背景表示個別覆寫）
- [ ] 再點同一個 ↻ → 取消覆寫，回到全頁設定
- [ ] 點 ─ → 此頁明確不轉，即使全頁設定有套用也不轉
- [ ] 提交 → 結果 PDF 該頁照個別覆寫設定轉
- [ ] 公開 API 也接受 `per_page` JSON：`curl -F per_page='{"3":"rotate-180"}' .../submit`

#### 6.9.5 每頁右上「回首頁」按鈕（#4）

- [ ] 任何工具頁 / admin 頁右上角有圓角「首頁」按鈕（含 home 圖示 + 「首頁」字）
- [ ] 點按鈕跳到 `/`
- [ ] 在 `/` 本身按鈕隱藏（不會出現「回首頁」沒反應）
- [ ] 手機 viewport（< 600 px）只顯示圖示，不顯示文字
- [ ] login 頁不顯示（沒 sidebar 的頁）

#### 6.9.6 企業 Logo / 識別（#1）

- [ ] `/admin/branding` 頁面開啟正常，顯示「目前 Logo」（預設或自訂）
- [ ] 上傳 PNG / JPG / WEBP → 預覽即時顯示 → 上傳成功 → 重整看到自訂 logo 出現在 sidebar / favicon / 首頁 hero / login 頁
- [ ] 上傳 > 5 MB → 拒絕並顯示錯誤
- [ ] 上傳非圖片（如 .pdf 改名 .png）→ 拒絕（PIL verify 抓到）
- [ ] 點「還原預設」→ 確認 → logo 變回內建
- [ ] `GET /branding/logo` 公開 endpoint：未設自訂回 404，有設回 PNG
- [ ] `/branding/` 路徑 prefix 在 `_PUBLIC_PREFIXES`（login 頁能讀到自訂 logo）

#### 6.9.7 用印與簽名臨時資產（#7）

- [ ] `/tools/pdf-stamp` 在資產區下方有「臨時上傳一張（僅本次）」按鈕
- [ ] 上傳圖檔 → 出現綠框臨時資產項目，radio 自動選中
- [ ] PDF 預覽顯示臨時 logo 位置（編輯模式）→ 拖曳 / 縮放正常
- [ ] 提交蓋章 → 成功產出 PDF，圖位置正確
- [ ] 重整頁面後 sessionStorage 還在 → 臨時資產仍可見
- [ ] 開新分頁 → 臨時資產不存在（sessionStorage per tab）
- [ ] 「移除」按鈕 → 清掉
- [ ] 蓋章送出後在 admin 稽核記錄看到 `event_type=temp_asset_used` + 檔名 + sha256 前 16 字
- [ ] data/ 內**不會**有臨時 logo 殘留（temp_dir 內 `stamp_temp_*.png` 由 2hr 排程清掉）

#### 6.9.8 逐句翻譯工具（#5）

- [ ] LLM 未啟用：`/tools/translate-doc` 顯示黃底警告「LLM 服務尚未啟用」+ 連結到 `/admin/llm-settings`，按鈕 disabled
- [ ] LLM 啟用後：貼一段中英混合文字 → 點「開始翻譯」 → 並排對照表出現
- [ ] 每句左原文 / 右譯文，譯文預設繁中
- [ ] 點某句 ↻ → 該句重新翻譯（不影響其他）
- [ ] 上傳 PDF → 解析出文字並切句 → 翻譯
- [ ] 上傳 DOCX → 同上
- [ ] 上傳 .txt → 同上
- [ ] 「複製譯文」/「複製對照」按鈕 → 剪貼簿正確
- [ ] 公開 API：`curl -X POST .../api/translate-doc -d '{"text":"hello","target_lang":"zh-TW"}'` → 回 JSON
- [ ] sidebar 搜尋「翻譯」/ `translate` 都找得到
- [ ] 既有客戶升級後：原本有 `text-diff` 權限的角色自動拿到 `translate-doc`（v5 migration）

#### 6.9.9 doc-deident 精準度（#8）

- [ ] 「生日：民國 70 年 3 月 21 日」→ 偵測到 `dob`
- [ ] 「出生日期： 1985-03-21」→ 偵測到 `dob`
- [ ] 「+886-912-345-678」→ 偵測到 `mobile`（含 +886）
- [ ] 「(電話) #123」→ 偵測到 `landline` 含分機
- [ ] 「(地址) 100 號 5 樓之 1」→ 偵測到 `addr` 含「樓之」
- [ ] 「Passport: 123456789」→ 偵測到 `passport`
- [ ] 「駕照號碼：F123456789」→ 偵測到 `driver_license`
- [ ] 純 9 位數字（無 Passport label）→ **不**誤認為 passport（false positive 修正）
- [ ] 「FROM 123」→ **不**誤認為 plate（前後標點要求）

#### 6.9.10 設定備份 / 匯入（#64）

- [ ] `/admin/settings-export` 顯示目前 data/ 內檔案 / 目錄列表 + 大小
- [ ] 點「下載備份壓縮檔」 → 下載 `jtdt-settings-YYYYMMDD-HHMMSS-vX.Y.Z.zip`
- [ ] 解壓 zip 看到 `manifest.json` + `data/` 結構正確
- [ ] 上傳同份 zip 「開始匯入」 → 確認對話框 → 匯入成功
- [ ] data/ 內出現 `*.bak.YYYYMMDD_HHMMSS` 備份檔
- [ ] 上傳壞檔（非 zip / 缺 manifest）→ 拒絕並顯示錯誤
- [ ] 上傳含 path traversal 的 zip（手工構造 `../etc/passwd`）→ 拒絕「unsafe path」
- [ ] 勾選「也覆寫歷史記錄目錄」 + 匯入 → fill_history 等也覆蓋
- [ ] 公開 API：`GET /admin/api/settings-export/summary` 回 JSON

### 6.11 v1.4.x 後續發現的問題（每次發版必過）

#### 6.11.1 Windows install.ps1 NSSM bundled-first（GitHub issue #1，v1.4.2 修）

- [ ] `github/packaging/windows/nssm.exe` 存在且 ~330 KB
- [ ] Fresh Win11 從 GitHub 跑 install.ps1（拔網路或防火牆鎖 nssm.cc 下）→ 仍能裝起來（用 bundled）
- [ ] install.ps1 內 `Install-Nssm` 必須在 `Fetch-Code` 之後（順序顛倒會找不到 bundled）
- [ ] Network fallback 用 `Invoke-WebRequest -TimeoutSec 20`，**禁止** `Net.WebClient.DownloadFile`（沒 timeout 卡好幾分鐘）

#### 6.11.2 客戶升級不准弄壞既有設定（v1.4.2 LDAP 慘案）

- [ ] `_run_auth_helper` 跑完後固定 chown 整個 data dir 回 service user（防止 sudo 寫的檔變 root:root mode 600 service 讀不到）
- [ ] `svc_update` 結尾跑一次 `_chown_data_files_back()`（self-heal 過去被汙染的客戶機）
- [ ] 模擬：在客戶機把 `data/auth_settings.json` chown 成 `root:root mode 600` → 跑 `sudo jtdt update` → 升級完後該檔回 `jtdt:jtdt` → 服務讀得到 → web UI LDAP 設定還在
- [ ] 模擬：客戶設好 LDAP → `sudo jtdt auth disable` → 檢查 `auth_settings.json` 仍 `jtdt:jtdt`、ldap 區段 fields 完整保留
- [ ] 既有 `auth.sqlite` 內 users 在升級後一個都沒少（migrations 全 INSERT OR IGNORE，不 UPDATE / DELETE）
- [ ] 既有 `role_perms` / `subject_perms` 行數升級前後一致（_m4 / _m5 只新增 image-to-pdf / translate-doc 行）

#### 6.11.3 setup-admin 偵測既有 user → 提供「沿用既有 admin 恢復」（v1.4.2）

- [ ] 既有 `auth.sqlite` 內有 user + `auth_settings.json backend=off` → 進 `/setup-admin` 看到藍色 reuse panel + 既有帳號清單
- [ ] 點「恢復本機認證」→ backend 變 local，不建新 user，session 全清，導去 /login
- [ ] /login 顯示提示訊息「已恢復本機認證，沿用 N 個既有帳號」
- [ ] 用既有 admin 帳號 + 密碼登入成功
- [ ] 沒有既有 user → setup-admin 顯示一般 form（建新 admin）
- [ ] reuse 流程結束 `auth_settings.json` ldap 區段未被清掉

#### 6.11.4 友善 403 / 401 / 404 錯誤頁（v1.4.2）

- [ ] 非 admin 在瀏覽器訪問 `/admin/llm-settings` → 友善 403 HTML 頁面（不是 raw JSON）
- [ ] 未登入訪問 `/admin/*` → 友善 401 HTML + 「去登入」按鈕
- [ ] 純 API client (Accept: application/json) 仍然回 JSON，不被改成 HTML

#### 6.11.5 跨用戶 upload_id 資安隔離（v1.4.83 修，重大）

啟用認證後，原本任一已登入 user 拿到別人的 upload_id 即可下載對方的 PDF / preview PNG。新增 `app/core/upload_owner.py` 寫入 sidecar JSON 紀錄 upload_id 屬於哪個 user_id，下載端點用 ACL 比對。

- [ ] **跨 user 拒絕**：兩個 user A、B 各自登入後，A 上傳一份 PDF 到任一工具（例如 pdf-fill /preview）→ 從瀏覽器 DevTools 抄下 `upload_id` → 在 B 的 session 用 curl 帶 cookie 打 `/tools/pdf-fill/download/{A 的 upload_id}` → **必須回 403** access denied
- [ ] **同 user 自己**：A 用自己的 cookie 抓自己的 upload_id → 200 OK 拿到檔案
- [ ] **Admin override**：把 user 設為 admin role → 抓他人 upload_id → 200 OK（為了客服 / 故障排除留的後門）
- [ ] **Anonymous 無法存取**：未登入 curl `/tools/*/download/<任何 id>` → 401 redirect to /login
- [ ] **Auth OFF（單機模式）**：關掉認證 → 任何 upload_id 都能拿（功能維持原樣）
- [ ] **Path traversal 阻擋**：`curl '/tools/pdf-fill/preview/../../etc/passwd'` → 400 invalid filename
- [ ] **UUID 格式檢查**：`curl '/tools/pdf-fill/download/INVALID'` → 400 invalid upload_id
- [ ] **Sidecar 清理**：上傳後 3 小時（temp_hours TTL 預設 2hr）→ `data/temp/.owners/<id>.json` 也應該被 retention sweeper 清掉，不只 PDF
- [ ] **Owner record missing**：手動刪掉 `.owners/<id>.json`（模擬升級前 legacy 檔）→ 該 upload_id 對非 admin 一律 403、對 admin 仍可存取
- [ ] **新單元測試 34 項全數綠燈**：`uv run pytest tests/test_safe_paths_and_owner.py -v`

#### 6.11.6 安全 headers middleware（v1.4.83 加）

- [ ] `curl -I http://localhost:8765/` → 回應含 `X-Content-Type-Options: nosniff` / `X-Frame-Options: SAMEORIGIN` / `Referrer-Policy: strict-origin-when-cross-origin` / `Permissions-Policy: ...interest-cohort=()`
- [ ] HTTPS 連線（reverse proxy 後）→ 額外含 `Strict-Transport-Security: max-age=15552000; includeSubDomains`
- [ ] 純 HTTP 連線**不**發 HSTS（不鎖內網 plain-HTTP 安裝）
- [ ] iframe embed 從 cross-origin 載入頁面 → 被 X-Frame-Options 擋掉

#### 6.11.7 Windows Tesseract 不需手動加 PATH（v1.4.88 修，GitHub issue #4）

客戶 Windows 機反映：用 install.ps1 裝完 Tesseract OCR，pdf-editor 仍顯示「OCR 不可用」需手動加 `C:\Program Files\Tesseract-OCR` 進系統 PATH 才行。Winget 安裝 UB-Mannheim 套件有時不會自動加 PATH，使用者也未必有 admin。修法：①程式碼端 `app/core/sys_deps.py:configure_pytesseract()` 探測標準路徑後設 `pytesseract.pytesseract.tesseract_cmd`，不需 PATH；②`install.ps1` 加 `Add-TesseractToPath` 主動補進 system PATH（雙保險）；③`jtdt update` 結尾的 sys-deps summary 也用相同邏輯，不會誤報缺。

- [ ] **故意拔 PATH**：Win11 上把 Tesseract 從 system PATH 移掉但保留 `C:\Program Files\Tesseract-OCR\tesseract.exe`，重啟 service → pdf-editor 仍能跑 OCR（紅框點下去能還原文字）
- [ ] **`jtdt sys-deps` 不誤報**：上述狀態下跑 `jtdt sys-deps` → tesseract 顯示 OK 不是 missing
- [ ] **install.ps1 主動補 PATH**：Fresh Win11 跑 install.ps1 → 觀察 log 應有 `Adding Tesseract to system PATH: C:\Program Files\Tesseract-OCR`；裝完後新開 PowerShell `tesseract --version` 應該抓得到
- [ ] **重複跑 install.ps1 不重複加 PATH**：再跑一次 install.ps1 → 不應重複 append PATH（檢查 system Path 不應有兩個 `Tesseract-OCR`）
- [ ] **macOS / Linux 行為不變**：標準位置 `/usr/local/bin/tesseract` 或 brew 路徑能被探到；`shutil.which` 仍是首選

### 6.13 v1.5.0 — 認證 / 角色 / 稽核員 / 2FA / 鎖定機制（每次發版必過）

#### 6.13.1 全新安裝啟用認證 → jtdt-auditor 自動建（v1.5.0）

- [ ] `jtdt auth set-local` + service restart 後 `auth.sqlite` 出現 username=jtdt-auditor 的本機帳號
- [ ] 該帳號 `password_hash IS NULL`、`totp_required=1`、`is_audit_seed=1`
- [ ] subject_roles 有 `(user, <uid>, auditor)` 對應

#### 6.13.2 升級保留資料（v5 → v7 schema）

- [ ] migration v6（totp_*）+ v7（is_audit_seed）對既有 user 行不影響
- [ ] 既有 default-user 角色的 role_perms 不被 wipe

#### 6.13.3 jtdt-auditor 第一次登入流程

- [ ] NULL pw 狀態 login → form 「帳號或密碼錯誤」（拒絕，不會跳 /2fa-verify）
- [ ] `sudo jtdt reset-password jtdt-auditor` 設密碼 → login 302 to `/2fa-verify`
- [ ] /2fa-verify GET 在 forced_setup 模式顯示 QR + 把 secret 寫進 DB
- [ ] 提交 6 碼正確 → 302 + jtdt_session cookie + totp_enabled=1
- [ ] 提交 6 碼錯誤 → 200 重新顯示

#### 6.13.4 admin 重設使用者 2FA（v1.5.0 新增 #6 BUG 修法）

- [ ] /admin/users 頁每個 user row 多了「重設 2FA」按鈕
- [ ] 點下去 → POST /admin/users/{uid}/reset-totp → 200 ok
- [ ] DB 內該 user totp_secret=NULL, totp_enabled=0；sessions 全清
- [ ] 該 user 下次登入 → 看到 QR（forced setup 重新走一次）
- [ ] 內建 jtdt-admin / jtdt-auditor 也有「重設 2FA」按鈕（不可刪但可重設）

#### 6.13.5 帳號鎖定 / 解鎖（v1.5.0 新增）

- [ ] 連錯密碼 5 次 → form 出現「嘗試次數過多，請於 N 分鐘後再試」
- [ ] /admin/users 頁被鎖的 user 顯示「解鎖」按鈕（黃底）
- [ ] 點「解鎖」→ POST /admin/users/{uid}/unlock → DB lockouts 該 user key 清掉
- [ ] /admin/auth-settings 頁有「清除所有鎖定」按鈕 → 一鍵清光（含 IP-based）

#### 6.13.6 職責分離 / 稽核員權限矩陣

- [ ] **admin 不可看**：/admin/uploads /admin/history/fill /stamp /watermark → 一律 403（v1.5.0 強化）
- [ ] admin 仍可看：/admin/audit /admin/system-status + 其他所有設定區
- [ ] admin sidebar 自動隱藏 uploads + 3 個 history 條目（_nav_settings_visible filter）
- [ ] auditor → /admin/audit /admin/system-status /admin/uploads /admin/history/* 都 200
- [ ] auditor → /admin/users /admin/roles /admin/auth-settings 一律 403
- [ ] auditor → /tools/任何工具/ 一律 403
- [ ] 每次 auditor view 寫一筆 `auditor_view` audit event（admin 看得到，auditor 沒刪除按鈕）
- [ ] auditor 自己 POST /me/2fa/disable → 403「您的角色強制使用 2FA」
- [ ] /admin/roles 頁面稽核員 row 不顯示工具勾選方塊（admin role 也是）
- [ ] admin POST tools=[…] 給 auditor role → 寫不進 role_perms（silently no-op）
- [ ] admin 試刪 jtdt-auditor → 400「不能刪除內建稽核員帳號」
- [ ] enforce_auditor_isolation 啟動時跑：auditor user 不可有其他 role / 直接 tool perm，totp_required 必為 1

#### 6.13.7 LDAP 共存

- [ ] LDAP backend ON 時 jtdt-admin / jtdt-auditor 仍可用 realm=local 登入
- [ ] LDAP user 認證未受 v1.5.0 改動影響
- [ ] `jtdt auth show` 正確顯示 LDAP server URI / search base / bind DN（不是 (unset)）

#### 6.13.8 jtdt update 不弄壞 auth_settings.json

- [ ] update 流程開始前 snapshot auth_settings.json bytes
- [ ] update 結束前若 file 內容變了 → 自動 restore + 警告
- [ ] 升級後 backend / LDAP server URI / TLS 設定全保留
- [ ] 重大原則：客戶升級版本，原有設定必需留存

### 6.12 機密 / 內網檢查（push 前必跑）

```bash
grep -rnE "192\.168\.|10\.[0-9]+\.[0-9]+\.[0-9]+|親測|OSSII 內部" \
  github/ --include='*.md' --include='*.html' --include='*.py' \
  | grep -vE "10\.0\.0\.|192\.168\.1\.10[^0-9]"
```

- [ ] 無真實內網 IP（test fixture 用 `10.0.0.x` / `192.168.1.10` placeholder OK）
- [ ] 無「親測」「內部」之類用語


### 6.14 v1.14.6 — 設定備份補齊 + 工作佇列 / 持久化 / 併行度（每次發版必過）

自動化：`tests/test_settings_export.py`、`tests/test_job_queue.py`、
`tests/test_job_api_acl.py`。以下為需人工確認或跨行程重啟才驗得到的項目。

#### 6.14.1 設定備份 / 匯入涵蓋度

- [ ] `python tools/check_settings_export_coverage.py` 回 0（新設定檔都已納管）
- [ ] 管理區「設定備份 / 匯入」看得到新分類：SSO 單一登入、目錄同步 / 過濾、
      記錄轉送、檔案保留 / 清理、排程備份設定、併行度設定、OCR 設定、
      掃描工具欄位偏好、掃描暫存資料、使用者工作區、送件檢查（自家實體）
- [ ] 「認證設定」分類的說明**不再**宣稱含 OIDC / SAML（那項獨立成 SSO 分類）
- [ ] **SSO 跨機還原**（最重要，是這批的核心 bug）：
      A 機設好 OIDC（含 client secret）→ 匯出 → 在 **B 機**（不同
      `.session_secret`）匯入 → B 機的 SSO 登入**要能成功**。
      舊行為是複製密文過去，B 機解不開 → 設定看起來都在但登入一直失敗。
- [ ] 備份 zip 內**沒有** `.session_secret`（有的話等於把偽造登入的能力送出去）
- [ ] 使用者工作區 / 掃描暫存資料 / 各類歷史 → 預設**不勾選**（量大）

#### 6.14.2 工作持久化（重啟後不遺失）

- [ ] 送出一份大檔轉換 → 等完成 → **重啟服務** → 「我的作業」仍列得出來，
      且「下載」按得到、檔案正確
- [ ] 轉換**進行中**時砍掉服務 → 重啟後該筆顯示「已中斷」+ 說明需重新送出
      （不可繼續顯示「進行中」讓使用者等一個永遠不會完成的工作）
- [ ] 結果檔被保留期限清掉後，該筆顯示「結果已逾期清除」而**不是**一個按了 404 的下載鈕

#### 6.14.3 佇列 / 併行度 / OOM 防線

- [ ] 管理區「背景作業與併行度」：同時送出超過上限的工作 → 多的顯示「排隊中」，
      不是全部一起跑
- [ ] 調高「最大同時工作數」→ 排隊中的**立刻**被派出去（不必等下一次送出）
- [ ] 「暫停派送」→ 新工作停在排隊中；**已經在跑的照樣跑完**（UI 有說明原因）
- [ ] 取消排隊中的工作 → 直接移出佇列；取消執行中的 → 下一個 checkpoint 停止
- [ ] 併行度填一個誇張數字（9999）→ 被夾到硬上限，不可真的生效
- [ ] macOS：「Office 轉檔同時數」欄位**停用**且顯示原因（Aqua bootstrap 競爭）；
      Linux / Windows 可調
- [ ] 記憶體不足時新工作**排隊**而不是硬開（`held_for_ram` 會亮）；
      且沒有任何工作在跑時仍會派一個出去（不可整個服務靜止）

#### 6.14.6 逐句翻譯的背景作業（v1.14.6）

- [ ] 送出後**關掉分頁**，隔一段時間回到「我的作業」→ 那筆作業還在跑 / 已完成
- [ ] 從「我的作業」點「看進度 / 開啟」→ 回到逐句翻譯頁，看得到目前進度與已完成的句子
- [ ] 網址帶 `?job=<id>` 直接開 → 一樣接得回來（重新整理也是）
- [ ] **一送出就看得到全部原文**（右側空白），不是等做完才出現
- [ ] 已花時間顯示的是**伺服器算的**（從別的分頁回來不會變成「已花 0 秒」）
- [ ] 中途按「停止翻譯」→ 狀態變已停止，已完成的句子保留
- [ ] 另一個帳號拿到 job id → 進度查詢回 404（譯文就是文件內容）
- [ ] 服務重新啟動 → 該作業顯示「已中斷，請重新送出」，不是永遠轉圈
- [ ] **外部服務名額**：翻譯進行中，另一個需要 LLM 的工具不會卡死
      （曾經因為名額被重複取得而自我鎖死，症狀是作業永遠停在「準備中」）

#### 6.14.7 帳號信箱與通知收件人（v1.14.6）

- [ ] AD / LDAP 使用者登入後，管理區「使用者管理」看得到從目錄帶入的信箱
- [ ] **不必等登入**：改完信箱屬性後按「立即同步」→ 尚未登入過的鏡射使用者也有信箱
      （UCS 用 `mailPrimaryAddress`，AD 用 `mail`）
- [ ] 目錄那邊沒填信箱的帳號 → 不可以把管理員手動補的值清成空白
- [ ] SSO（OIDC / SAML）登入 → 信箱由 IdP 帶入
- [ ] 通知設定的 Email 那一列**沒有輸入框**，只顯示「會寄到 ○○○」與去哪改
- [ ] 直接送 `{"email_to": "..."}` 給 `/api/my/notify` → 不會生效（擋在伺服器端）
- [ ] 帳號沒有信箱 → Email 管道不啟用（不是錯誤，也不可以噴例外）
- [ ] **從未設定過通知偏好的人**：管理員開好 Email + 帳號有信箱 → 跑一個超過門檻的
      作業就收得到（不必自己去勾任何東西）
- [ ] 使用者把管道全部取消勾選並儲存 → 之後不再收到（不可以又被自動打開）
- [ ] 不會收到任何通知時，通知設定區有明說「目前不會收到任何通知」
- [ ] 通知信是 HTML 版型（標題列 / 狀態徽章 / 欄位表 / 按鈕），且純文字版也在
- [ ] 信裡看得到**站台 logo 與工具圖示**，且**不需要按「顯示圖片」**（內嵌附件，不是外部網址）
- [ ] 管理員換過 logo → 之後寄出的信用新的那張
- [ ] 圖片產不出來時信照樣寄得出去（只是沒有圖）
- [ ] 「站台網址」沒填 → 信裡不放按鈕；填了非 http(s) 的值 → 不被接受
- [ ] 認證設定的「信箱屬性」改成別的名稱後存檔 → 重新整理仍在（不可無聲消失）
- [ ] 側欄「我的帳號」看得到信箱；沒設定時顯示「尚未設定 — 通知會寄不出去」
- [ ] **本機帳號**：卡片上按「修改」→ 存檔 → 重開卡片仍是新值
- [ ] **AD / LDAP / SSO 帳號**：卡片上**沒有**修改鈕，並說明由來源端管理
- [ ] 直接打 `POST /me/email`（目錄帳號）→ 403（擋在伺服器端，不是只藏 UI）
- [ ] 未登入打 `POST /me/email` → 被擋（不可跟著轉址誤判成 200）

#### 6.14.8 工作區的 Office 檔縮圖（v1.14.6）

- [ ] 存一個 .docx / .pptx / .odt 進工作區 → 稍等一下卡片出現第一頁縮圖
      （第一次開頁面可能還是空白，幾秒後自動補上，不必手動重新整理）
- [ ] 同一個檔第二次開頁面 → 立刻有縮圖（走快取，不會再轉一次）
- [ ] 超過 80 MB 的檔 → 不做縮圖，畫面不破圖
- [ ] 毀損的檔 → 失敗一次之後不再重試（不可以每次開頁面都跑一次 Office 引擎）
- [ ] 一頁十幾個 Office 檔 → 頁面**立刻**顯示，不可以卡住等轉檔

#### 6.14.9 「可以關掉這一頁」的標示（v1.14.6）

- [ ] 任一個有背景作業的工具送出後 → 進度列出現這行提示
- [ ] 作業完成 / 失敗 / 取消 → 提示收起
- [ ] 提示只做在共用進度列，個別工具沒有各自再寫一份（文案不會分歧）

#### 6.14.10 文件去識別化：表格裡的欄位（issue #43, v1.14.7）

- [ ] Word 表格「出生日期 | 1998-12-28」要被偵測到，遮蔽框落在**值**那一格
- [ ] 段落四種寫法都要抓到：`1998/12/18`、`1998-12-19`、`1998.12.20`（點分隔）、
      `民國87年12月21日`
- [ ] `DOB: 12/18/1998` 要整個吃掉，不可只抓 `12/18/19`（遮蔽後留著 `98`）
- [ ] 沒有標籤的裸日期不可被當成出生日期
- [ ] 跨格配對不可產生重複，也不可讓身分證 / Email 這類不需標籤的式子跨格湊配
- [ ] 同樣驗一次銀行帳號 / 駕照號碼放在表格裡（同一條程式路徑）

#### 6.14.11 我的工作區：大檔縮圖（v1.14.7）

- [ ] 30 MB 級的 .pptx 存進工作區後，兩分鐘內縮圖會自己出現（不必手動重新整理）
- [ ] 空白佔位圖的回應帶 `Cache-Control: no-store`
- [ ] 縮圖產好之後重新整理頁面，不會因為瀏覽器快取而仍顯示空白

#### 6.14.12 新工具：頁面加框（pdf-border, v1.14.11）

- [ ] 上傳 PDF → 顯示頁數、每頁預覽都出現框線
- [ ] 上傳 .pptx / .odp → 自動轉成 PDF 後加框，狀態列顯示「已由文書檔轉成 PDF」
- [ ] 從工作區載入一份簡報，流程與直接上傳一致
- [ ] 兩種定位：自頁緣內縮（每頁位置一致）/ 貼齊內容（框跟著內容走、不溢出頁面）
- [ ] 線條：粗細 / 顏色 / 實線・虛線・點線 / 圓角 / 不透明度，改動後預覽自動更新
- [ ] 內外雙框、外側陰影各自開關，子選項跟著顯示 / 隱藏
- [ ] 首頁不加框 → 第 1 頁預覽標「不加框」且變淡
- [ ] 指定頁面 `1,3,5-8` → 只有這幾頁有框；**打錯字（例如「第一頁」）要變成全部加框，不可以一頁都不畫**
- [ ] 四個快速套用（投影片外框 / 獎狀雙框 / 細灰線 / 圓角卡片）都會同步所有欄位並重畫預覽
- [ ] 點縮圖開放大檢視，可用 ‹ › 與方向鍵翻頁、ESC 關閉
- [ ] 送出後走背景作業（進度列 + 可關頁面），完成可下載並「存至工作區」
- [ ] 旋轉頁（/Rotate 90）的框線要落在可見頁面內，不可跑出頁外或只畫一半
- [ ] API `POST /tools/pdf-border/api/pdf-border` 依 API.md 範例呼叫可得加框 PDF
- [ ] 線寬 / 邊距給極端值（例如 `width_pt=500`）時伺服器要夾住，不可把整頁塗滿

#### 6.14.13 AD / LDAP 帳號管理一輪（v1.14.14）

**使用者清單**
- [ ] 點來源篩選（local / ldap / ad）清單要正確過濾，**不可以整份消失**
- [ ] 「最後登入」排序要真的按時間，從未登入的排最後
- [ ] 搜尋 / 來源 / 狀態篩選是**伺服器端**：篩出來的總數要是全庫的數字，不是當前頁
- [ ] 超過一頁時有分頁控制，換頁後篩選條件保留

**批次操作**
- [ ] 列選 + 全選（部分選取時全選框呈現 indeterminate）
- [ ] 批次啟用 / 停用 / 加上角色 / 移除角色都會生效並寫稽核
- [ ] **停用自己 → 被擋**；**停用內建管理員 → 被擋**
- [ ] **全選所有管理員後停用 → 被擋**（否則沒有人進得了管理區）
- [ ] 被跳過的帳號要顯示原因，不可以靜靜少做

**目錄已無（離職偵測）**
- [ ] 完整同步後，AD 端刪掉 / 移出範圍的帳號要出現在「目錄已無」
- [ ] 本機帳號與 SSO 帳號**永遠不可以**被標記
- [ ] 還沒做過完整同步時，這個檢視要顯示說明而不是 0 筆
- [ ] 帶名稱過濾的同步**不可以**更新判定基準
- [ ] 該帳號重新登入成功後，標記要消失

**巢狀群組**
- [ ] 權限指派給上層群組 → 子群組成員要拿得到
- [ ] 目錄端設出環狀關係（A→B→A）時，權限查詢不可以卡住

**有效權限**
- [ ] `GET /admin/users/{id}/effective` 列出的工具要與該使用者實際看得到的一致
- [ ] 每個工具都標得出來源；巢狀繼承要標明是繼承來的
- [ ] 稽核員一律 0 個工具（即使同時有 admin 角色）

**故障可觀測性**
- [ ] 關掉 LDAP 伺服器後登入：畫面只顯示通用訊息，**稽核有 `ldap_unavailable`**
- [ ] AD 帳號鎖定後登入：稽核的 `ad_reason` 要顯示「帳號已被鎖定」
- [ ] 同步失敗要記下是哪個群組 / 什麼原因，並保留歷史
- [ ] 同步失敗會發出通知（需先設定通知管道）

#### 6.14.3b 網頁回應與轉檔隔離（「網頁回應永遠優先」）

原始症狀：正式機轉檔期間整站空轉，但 CPU / 記憶體看起來都有餘裕。
**這一節每次發版都要在真的多核機器上跑**，本機開發機（核心數多、沒有其他負載）
重現不出來 —— 2026-07-30 就是在 8 核開發機上測不出、在 6 核正式機上才發生。

- [ ] 送出 2–4 份大型轉檔，同時每 0.5 秒打一次 `/healthz`：
      **不可有任何一次超過 1 秒**（修正前最久 226 秒）
- [ ] 轉檔進行中點側欄任何一頁（尤其「系統狀態」）→ 立即切換，不空轉
- [ ] `ps -o pid,ni` 看 soffice.bin：nice 應為 19（作業執行緒 10 + 子行程 10）
- [ ] `taskset -p <soffice pid>` / `os.sched_getaffinity`：核心數應等於設定值，
      且**至少留一顆**不給轉檔（預設「自動」）
- [ ] 「轉檔 CPU 上限」改 25% / 50% / 100% → 下一個轉檔的核心遮罩跟著變
      （改設定不必重啟服務）
- [ ] 選 100%（不限制）→ 不設遮罩；此時允許網頁變慢，屬管理員明示的選擇
- [ ] macOS：欄位停用並說明「沒有提供限制核心的介面」，但轉檔仍降優先權
- [ ] Windows：核心限制有效（psutil），執行緒優先權不適用 →
      soffice 由 `BELOW_NORMAL_PRIORITY_CLASS` 處理
- [ ] 單核機器（或 cpuset 只有 1 顆）→ 不可算出 0 顆核心而讓轉檔跑不動
- [ ] 已被 cgroup cpuset 限制過的容器 → 只在既有遮罩內挑核心，不可挑到遮罩外
- [ ] 事件迴圈延遲監看：人為卡住主執行緒 > 1 秒 → 記錄出現警告並附當時作業數
- [ ] 單一請求超過 3 秒 → 記錄留下慢請求警告（含路徑與耗時）

#### 6.14.3c 外部服務（LLM / 遠端 GPU OCR）同時呼叫上限

- [ ] 預設為 1：同時送出多個需要 LLM / 遠端 OCR 的作業 →
      對外請求**一次只有一個**，其餘在本機等
- [ ] 上限調高後立即生效（不必重啟）
- [ ] 外部服務逾時 / 斷線 → 名額要**確實釋放**（不可卡死後續所有作業）
- [ ] 這個上限與「最大同時作業數」互不影響（本機估算擋不到遠端負載）

#### 6.14.4 權限邊界（水平越權）

- [ ] 認證開啟：A 使用者的「我的作業」**看不到** B 的工作
- [ ] 認證開啟：A 不可取消 B 的工作（回 404，不確認其存在）
- [ ] 認證開啟：未登入呼叫 `/api/jobs` → 401
- [ ] 認證關閉（且僅此時）：以來源電腦區分，頁面上有說明同一 NAT 出口會混在一起
- [ ] 一般使用者存取 `/admin/jobs` 與其 API → 403

#### 6.14.5 規模（8000 人情境）

- [ ] 管理區「檔案保留 / 清理」有「作業紀錄（我的作業）」一列，預設 30 天
- [ ] 保留期到期後舊紀錄被清掉，**但執行中 / 排隊中的不論多舊都不刪**
- [ ] 28 萬筆時「我的作業」查詢仍在數 ms（實測 1.2 ms / 74 MB）

#### 6.14.6 資料庫毀損防護（v1.14.6）

自動化：`tests/test_db_health.py`（24 項）。以下為需人工或離線環境確認的項目。

- [ ] `jtdt db-check` 在**服務停止**時仍可執行（資料庫壞掉時網頁本來就上不去）
- [ ] `jtdt db-backup` → `jtdt db-backups` 看得到剛建立的備份
- [ ] 人為打壞 `auth.sqlite`（測試機才做）→ `jtdt db-check` 回非 0 並列出影響與復原指令
- [ ] `jtdt db-restore auth.sqlite` → 帳號資料完整回來；毀損的原檔另存為 `.corrupt.<時間>`
- [ ] 毀損狀態下執行備份 → **略過**且既有備份數不變（不可用壞檔覆蓋好備份）
- [ ] 拿一份被打壞的備份去還原 → 被擋下，且正式檔沒有被覆蓋
- [ ] 服務啟動時若資料庫毀損 → 記錄有明確訊息、稽核有 `db_corruption` 事件、
      **服務仍然起得來**（單一資料庫壞掉不該讓整個服務停擺）
- [ ] 管理區「系統狀態 → 資料庫健康狀態」顯示正確，「立即備份」可用
- [ ] CLI 輸出全為英文 ASCII（純文字終端 / 精簡容器 / Windows 主控台皆可讀）

#### 6.14.7 升級路徑（既有客戶）

自動化：`tests/test_upgrade_v1_14_6.py`（10 項）。原則是**客戶升級版本，原有
設定必需留存**，且不需要客戶手動做任何事。

- [ ] **沒有新的第三方相依** → `install.sh` / `setup-python.cmd` / `cli.py`
      三處的 import 煙霧測試都不必改（有新增相依時要走「五處 SOP」）
- [ ] 舊 `retention.json`（缺 `job_records_days`）→ 自動補預設，客戶調過的
      其他天數**不被重設**
- [ ] 舊資料目錄沒有 `jobs.sqlite` / `concurrency.json` / `db_backups/`
      → 啟動或首次使用時自動建立
- [ ] 併行度預設維持**舊行為**（同時 2 個工作、Office 轉檔 1 個）——
      升級不可默默改變併行度而讓客戶機器變慢或變爆
- [ ] 「外部服務同時呼叫數」預設 1、「轉檔 CPU 上限」預設「自動（保留 1 核給網頁）」
      —— 升級後兩者都不需要管理員動手就生效
- [ ] **升級當下正在轉檔的使用者**：`jtdt update` 會重啟服務 → 該工作變成
      「已中斷」，頁面要明確顯示並提示重新送出，**不可讓進度條一直轉**
      （共用進度元件 + pdf-ocr + submission-check 三處都要處理）
- [ ] `sudo jtdt db-backup` 之後，`data/` 內新產生的檔案**不是 root 所有**
      （走 `_run_auth_helper` 會自動 chown 回服務帳號）
- [ ] 升級後管理區的「檔案保留 / 清理」多一列「工作紀錄」，且舊值都在
- [ ] 升級後側欄多出「我的作業」，管理區多出「背景作業與併行度」

#### 6.14.8 作業完成通知（v1.14.6）

自動化：`tests/test_notify.py`（25 項）。以下需真的外部服務或人工確認。

- [ ] 管理區「作業完成通知」→ 各管道「傳送測試」實際收得到；**失敗時顯示實際
      原因**（例如「Connection refused」），不是只說「失敗」
- [ ] 憑證存檔後頁面顯示遮罩；**只改別的欄位再存檔，憑證不會被洗掉**
- [ ] `data/notify_settings.json` 內**看不到明文** token / 密碼 / webhook URL
- [ ] 使用者到「我的作業」選管道 → 個人管道（Email / Telegram / LINE）沒填自己的
      位址時**不會送**；團隊頻道不需填
- [ ] 使用者選了管理員**沒啟用**的管道 → 不會送（不能繞過管理員）
- [ ] 跑超過門檻的作業完成 → 收得到通知；**短作業不通知**
- [ ] 通知內容只有工具名 / 檔名 / 狀態 / 耗時，**沒有檔案內容**
- [ ] 故意把管道設成連不通 → 作業本身仍然成功（通知失敗不可影響作業）
- [ ] 升級後預設是**關閉**的（不可無預警開始往外送訊息）
- [ ] **跨機還原**：A 機設好 → 匯出 → B 機匯入 → 通知直接可用（不必重新輸入憑證）

#### 6.14.9 站內通知 + 自動存入工作區（v1.14.6）

- [ ] 側欄帳號旁有通知按鈕；有新完成的作業時顯示紅點
- [ ] 點開顯示最近完成的作業（工具 / 檔名 / 狀態 / 多久前）；面板**不被側欄裁切**
- [ ] 「全部標示為已讀」後紅點消失；再有新作業完成又會出現
- [ ] **認證關閉時通知按鈕也要在**（單機使用者一樣需要）
- [ ] 只看得到自己的作業（認證開啟依帳號、關閉依來源電腦）
- [ ] **開著頁面等**作業完成 → **不會**自動存入工作區（人就在那裡）
- [ ] 送出後**關掉頁面**，完成後 → 自動存入工作區，清單顯示「已自動存入」
      且**不再顯示「存至工作區」按鈕**
- [ ] 工作區容量調到很小 → 顯示「工作區容量已滿，未自動存入」且下載連結仍在
- [ ] 工作區**停用**時 → 不自動存，改顯示「結果將於 N 小時後清除」
- [ ] `.pptx` / `.odp` 存得進工作區（原本會被拒收）

### 6.15 v1.14.16 — AD / LDAP 管理一輪（每次發版必過）

自動化：`tests/test_ldap_failover.py`（14 項）、`test_ad_primary_group.py`（14）、
`test_ad_account_state.py`（40）、`test_directory_cleanup.py`（31）、
`test_online_sessions.py`（29）、`test_directory_role_assign.py`（19）、
`test_effective_permissions.py`（11）、`test_directory_presence.py`（12）。
以下需要**真的 AD / LDAP 環境**或人工確認。

#### 6.15.1 多台 DC 容錯

- [ ] 伺服器欄位填兩台（逗號分隔）→ **存得下去**（`type="url"` 會讓整個表單送不出）
- [ ] 停掉第一台 → 仍然登得進去（自動換第二台）
- [ ] 第一台修好後**會被重新使用**（不是永久排除 —— `exhaust` 給的是秒數）
- [ ] 兩台都不通 → 幾秒內回「無法連線到認證伺服器」，**不是卡住幾十秒**
- [ ] 稽核記錄有 `ldap_unavailable`，畫面上**沒有**原始例外訊息

#### 6.15.2 AD 主要群組

- [ ] 把某人的 primaryGroupID 改成一個有指派角色的群組（且該群組**不在**他的
      memberOf）→ 他登入後**拿得到**那個群組的權限
- [ ] OpenLDAP 環境登入完全正常（沒有 objectSid，不可以出錯）

#### 6.15.3 帳號狀態（AD 已停用 / 密碼到期）

- [ ] AD 端停用某人 → 同步後使用者清單出現「AD 已停用」徽章與檢視
- [ ] AD 端啟用回來 → 同步後徽章**消失**（狀態要跟著回正常）
- [ ] 密碼快到期的人出現「密碼 N 天後到期」；已過期顯示「密碼已過期」
- [ ] 套了細緻密碼原則（PSO）的人日期**正確**（不是用網域 maxPwdAge 算的）
- [ ] 設了「密碼永久有效」的人**不顯示**到期
- [ ] OpenLDAP / 本機 / SSO 帳號**完全不出現**這兩種徽章

#### 6.15.4 批次停用 / 排程自動停用

- [ ] 「目錄已無」→「全部停用」：確認訊息寫出**實際會動到幾個人**
- [ ] 停用後帳號與角色指派**都還在**，重新啟用即恢復
- [ ] 停用後按鈕**消失**（沒有還啟用中的人）
- [ ] 故意讓待停用人數超過目錄帳號的 20% → **整批中止、一個都沒動**，
      訊息點出可能是服務帳號密碼過期 / 搜尋範圍被改
- [ ] 排程自動停用**預設是關閉**；升級後不可自己開始停用任何人
- [ ] 帶名稱過濾的同步**不會**觸發自動停用
- [ ] 內建管理員永遠不被停用

#### 6.15.5 在線 session

- [ ] 啟用認證 → 使用者清單顯示「N 人在線」；**單機模式不顯示**
- [ ] 同一人開三個瀏覽器 → 算 **1 人**（不是 3）
- [ ] 閒置超過 15 分鐘後從在線人數消失
- [ ] 「登入裝置」看得到瀏覽器 / 作業系統、來源位址、最後活動時間
- [ ] 個別登出 → 那一台下一個動作被導回登入頁，**其他裝置不受影響**
- [ ] 「全部登出」→ 全部被踢；稽核有 `session_revoke`
- [ ] 瀏覽器開發者工具看不到 token 或完整雜湊

#### 6.15.6 目錄瀏覽指派角色

- [ ] 選一個**從沒登入過**的目錄使用者 → 指派角色 → 使用者管理看得到他
      （**未啟用**狀態）
- [ ] 該使用者第一次登入 → 自動啟用，**先前指派的角色還在**（沒被預設角色蓋掉）
- [ ] 「所屬群組」點「角色」→ 設得了群組權限
- [ ] 同名不同 DN → 拒絕並說明衝突對象

#### 6.15.7 有效權限面板

- [ ] 編輯使用者 → 展開「有效權限」→ 列出實際能用的工具與**來源規則**
- [ ] 從上層群組繼承來的標成「巢狀繼承」
- [ ] 管理員顯示「所有工具」；稽核員顯示 0 個工具

### 6.16 v1.14.18 — 「同上」展開 + LLM 逐欄校驗保守規則（每次發版必過）

自動化：`tests/test_same_as_ref.py`（39，含端到端真的產 PDF 抽文字）、
`tests/test_llm_per_field_consensus.py`（13，用腳本化假模型跑真的兩輪流程）。

#### 6.16.1 「同上」展開

- [ ] 公司資料把「發票地址」填成 `同上` → 填出來的表單上是**實際地址**，不是「同上」
- [ ] 填成「同公司地址」「同登記地址」→ 一樣展得開
- [ ] 「電話」填成 `同上` → **保持原字面**（沒有約定俗成的對象，不可以亂猜）
- [ ] 「英文地址」填成 `同上` → **保持原字面**（中文地址不可以填進英文欄）
- [ ] 指到的欄位是空的 → 保持原字面，**不可以變成空白**
- [ ] 結果頁列出「以下的『同上』已展開成實際內容」，且看得到原本填的是什麼
- [ ] 公司名叫「同心圓…」之類「同」開頭的客戶，其他欄位的 `同上` 一樣展得開

#### 6.16.2 LLM 逐欄校驗

> LLM 校驗預設關閉；要測需先在管理區開啟並指定模型。

- [ ] 校驗跑完後結果頁顯示**兩輪**；被採納的列是綠色
- [ ] 同一個問題**不會列兩次**（去重）
- [ ] 只在其中一輪被指出的疑慮**仍然列得出來**，但不標成已採納
- [ ] 管理區把「連續幾輪」設成 1 → 只跑一輪，行為與舊版相同
- [ ] 第二輪只重問可疑欄位（看進度訊息「再確認 N/M」的 M 應**遠小於**總欄位數）

### 6.17 v1.14.19 — 中文字形與字型子集化（每次發版必過）

自動化：`tests/test_ttc_subfont.py`（27 項，含端到端產 PDF 驗字型名稱與檔案大小）。

#### 6.17.1 字形（`.ttc` 子字型）

- [ ] 表單填寫產出的 PDF，內嵌字型名稱含 **CJK TC**（不是 CJK JP）
- [ ] 頁碼、PDF 編輯器產出的中文同樣是 TC
- [ ] 目視確認：**「海」是兩點（每），不是一橫（毎）**；「過」「郎」「船」「直」
      也應為台灣寫法
- [ ] 浮水印打中文字 → 同樣是台灣字形
- [ ] 把系統 CJK 字型移走 / 改名 → **不可以整個印不出來**（退回內建字型即可）

#### 6.17.2 檔案大小

- [ ] 一張乾淨空白表單填幾個中文欄位 → 產出**不超過幾百 KB**（修正前是 13 MB）
- [ ] 產出的 PDF 文字**選得起來、複製得出來、搜尋得到**
- [ ] 100 頁文件加中文頁碼 → **秒級完成**（修正前每頁都重算一次字型子集）
- [ ] 填入罕用字（例如姓名裡的異體字）→ **不可以變成空白方框**；
      真的縮不出來時要退回完整字型（檔案變大是可接受的，缺字不行）

### 6.18 v1.14.20 — 三個新工具（每次發版必過）

自動化：`tests/test_pdf_bookmark.py`（28）、`tests/test_pdf_seam_stamp.py`（40）、
`tests/test_pdf_page_size.py`（22）。三支都用真實瀏覽器（CDP）驗過完整流程。

#### 6.18.1 書籤與目錄

- [ ] 一次選 3 個 PDF → 自動串接，**每個檔名成為第一層書籤**
- [ ] 子文件原有的書籤降一層保留，頁碼有加偏移
- [ ] 手動把第一筆改成第 2 層 → **自動修回第 1 層並說明原因**
- [ ] 頁碼填超過總頁數 → 夾到最後一頁並說明
- [ ] 勾「產生目錄頁」→ 產出多一頁；**書籤頁碼、目錄上的頁碼、目錄連結三者一致**
- [ ] 目錄頁的中文不可以是缺字方框
- [ ] 貼上「標題 + 頁碼」清單（含縮排）→ 層級正確；看不出頁碼的行會被列出來

#### 6.18.2 騎縫章

- [ ] 兩種模式各有**示意圖**（不是只有文字）
- [ ] 三種印章來源都能用：資產庫 / 上傳 / 系統產生
- [ ] 上傳帶白底的章 → 白底變透明，不會蓋住內文
- [ ] 每組 2 頁 / 3 頁 / 整份 → 組數顯示正確且**立刻更新**（不用等預覽圖）
- [ ] 「拼回去」的預覽是**完整的章**（片與片之間的縫是刻意畫的）
- [ ] 加角度之後拼回去**仍然完整**（先轉再切）
- [ ] 開亂數 → 不同組位置 / 角度不同，**同一組內完全一致**
- [ ] 產生後回報亂數種子；填回去重跑得到**一模一樣**的結果
- [ ] 印出來實測：把連續幾頁的邊緣對齊，看得出是同一個章

#### 6.18.3 頁面尺寸統一

- [ ] 上傳混合尺寸的檔 → **先列出有幾種尺寸**並提醒
- [ ] 尺寸一致的檔 → 明說「本來就一致，不一定需要處理」
- [ ] 統一成 A4「跟著原頁方向」→ A3 橫變 A4 橫、A4 直不動
- [ ] 產出的**文字仍然選得到**（不可以被轉成圖片）
- [ ] 原本就是目標尺寸的頁面**沒有被重放**（報告要說有幾頁沒動）
- [ ] 「置中不縮放」遇到比紙張大的頁面 → **警告會裁掉**
- [ ] 有 `/Rotate` 的頁面方向判斷正確

### 6.19 v1.14.21 — 三個新工具的介面回饋（每次發版必過）

> 這一節全部來自使用者實際操作後的回報。共通點是**單元測試都測不到** ——
> 要嘛是版面（要看畫面），要嘛是「模板誰呼叫誰」（要真瀏覽器）。

#### 6.19.1 設定欄位的排版（三支新工具共通）

- [ ] 每個欄位的**說明文字自己一行**，不會擠在輸入框右邊
      （`af-note` 必須是 `display:block`；`<small>` 預設是 inline）
- [ ] 同一區內的**數字框、下拉框、文字框等寬**
      （原本的寬度規則只涵蓋 `text` / `url` / `password`）
- [ ] 勾選框文字長到要折行時，**方框仍對齊第一行**不會被推到中間
- [ ] 單位（mm / % / 度）在欄位裡，不在標籤裡

#### 6.19.2 騎縫章

- [ ] 章面文字打**公司全名**（10 字以上）→ 長方章**變寬**，字級不變小
      （高度不變就是字級沒被動過）
- [ ] 同樣的長字串在圓章 / 方章 → **分行**（直行、右至左），圓章仍是正圓
- [ ] 「印章來源」的卡片與下方欄位之間**有留白**
- [ ] 「從資產庫選」是**縮圖清單**不是下拉；縮圖要真的載入（不是破圖）
- [ ] 換選另一個資產 → 印章預覽跟著更新
- [ ] 一個章跨 N 頁時，預覽把**那一組的每一頁都列出來**（不是只有一頁）
- [ ] 「2. 印章」「3. 怎麼蓋」「4. 預覽」是**三張獨立卡片**

#### 6.19.3 頁面尺寸統一

- [ ] 預覽**一次列六頁**，每張都真的載入
- [ ] 「3. 預覽」是獨立卡片，不在「2. 統一成」裡面

#### 6.19.4 書籤與目錄

- [ ] 有「3. 預覽」卡片；**沒有可看的東西時會說明原因**
      （沒書籤 / 有書籤但沒勾目錄頁，兩種訊息都算通過）
- [ ] 勾「在最前面產生目錄頁」→ 預覽真的顯示目錄頁的圖
- [ ] 產生完的結果訊息**有講書籤在閱讀器側邊欄看**
      （使用者回報過「沒看到目錄」，實際上書籤有做出來）

#### 6.19.5 作業通知的工具圖示

- [ ] 通知清單每一列**都有圖示方塊**（缺一個整排就對不齊）
- [ ] 模擬舊分頁（把某工具從 `#toolIconSprite` 移除）→ 改用**通用圖示**，
      不可以整個方塊消失
- [ ] 三處都要驗：通知下拉、`/my-jobs`、`/admin/jobs`

#### 6.19.6 守門測試（會自動跑，但發版前確認有過）

- [ ] `tests/test_api_doc_coverage.py` —— 以實際路由表反查 `API.md` 與本檔 §4
- [ ] `tests/test_api_page_builder.py` —— `api.html` 不可含 NUL；
      巢狀行內標記（粗體裡包程式碼）要完整還原
- [ ] `tests/test_template_js_syntax.py` / `tests/test_csp_nonce.py`

### 6.20 v1.14.22 — 預覽的載入狀態與頁數（每次發版必過）

- [ ] 縮圖**載入中顯示轉圈**（`.jt-thumb.is-loading`），不是破圖或空白
      —— 每張都是向伺服器要的，往返要時間
- [ ] 縮圖**算不出來時顯示紅字「算不出來」**（`.jt-thumb.is-error`），不留空白
- [ ] 騎縫章 / 頁面尺寸統一的預覽**預設 20 頁**（文件不足 20 頁就全部）
- [ ] 20 張是**有限併行**（4 條），不是逐一等
- [ ] **騎縫章的預覽以「組」為單位包起來**，同一組的頁面**永遠在同一行**
      （驗法：每個 `.sm-group` 內所有 `.sm-page` 的 `getBoundingClientRect().top` 相同）
      —— 這個工具要看的就是相鄰兩頁的接縫，被換行拆開等於預覽沒有用

### 6.21 v1.14.22 — 中文寫進 PDF 必須看得見（每次發版必過）

> v1.14.19 ~ v1.14.21 的正式機故障：字型子集化把字形重新編號，繪製引擎用
> **原始編號**去取 → 什麼都畫不出來。**文字層完全正常**（搜尋、複製、抽取都對），
> 只有畫面空白，所以任何「文字抽得到」的檢查都會誤判成通過。

- [ ] **一律算圖數墨水**，不可以用 `get_text()` 當作通過的依據
- [ ] 表單自動填寫：填入中文 → 下載的 PDF **看得到字**
- [ ] 用印與簽名（含日期、個資限用章）：中文看得到
- [ ] 插入頁碼：中文頁碼格式（第 N 頁）看得到
- [ ] 浮水印：中文浮水印看得到
- [ ] 書籤與目錄的目錄頁：標題看得到
- [ ] 產出檔案**沒有暴增**（子集化仍在生效，約 820 KB 而不是 16 MB）
- [ ] `tests/test_cjk_font_renders.py` 全綠

### 6.22 v1.14.22 — 目錄頁的插入位置（每次發版必過）

- [ ] 「插在第幾頁」填 1 → 目錄在最前面（與舊行為相同）
- [ ] 填 2 → 目錄排在**封面後面**，第 1 頁仍是原本的封面
- [ ] **插入點之前的書籤頁碼不動**（封面那筆仍是第 1 頁，不可以指到目錄自己）
- [ ] 目錄上印的頁碼與**目錄項目的連結**都符合同一個規則
- [ ] 填超過總頁數不會炸掉（會夾到合法範圍）

### 6.23 v1.14.23 — 預覽縮圖不可以讓人誤判邊界（每次發版必過）

> 加框工具的預覽縮圖，卡片自己有一圈灰框線 + 白色內距 → 看起來像「框線離
> 頁緣還有距離」，實際上是貼齊的。使用者要判斷的正是框線位置。

- [ ] 預覽縮圖的容器**沒有自己的框線**（灰底襯白紙加陰影）
- [ ] 頁面加框：邊距設 **0** → 框線正好在白紙邊緣，外面直接是灰底，
      **不可以有白色間隙**
- [ ] 頁面加框：邊距設 5mm → 看得出框線確實內縮
- [ ] 頁面尺寸統一：預覽圖裡的灰框是**目標紙張邊界**，
      容器不可以再畫一條混淆
- [ ] 騎縫章、書籤與目錄的縮圖同樣處理

### 6.24 v1.14.24 — 工具之間的檔案交接（每次發版必過）

> 之後的「工作流程串多個工具」會走同一條路，所以這一節驗的是**通用機制**，
> 不是書籤→頁碼這一對。

- [ ] 書籤與目錄做完 → 結果訊息有「用『插入頁碼』補上」的連結
- [ ] 點下去 → **頁碼工具收到那份檔案**（檔名正確，不是 `document.pdf`）
- [ ] 檔案有存進**我的工作區**（`source_tool` 記著來源工具）
- [ ] 網址上的 `from_ws` / `from_job` / `from_name` **用完就清掉**
      （重新整理不該再抓一次）
- [ ] 一頁有多個上傳框時（如騎縫章），**只有第一個**吃這個參數
- [ ] 工作區被管理員停用 → 退回 `from_job`，功能仍可用
- [ ] 拿**別人的** file_id / job_id → 取不到（伺服器端驗歸屬）

### 6.25 v1.14.24 — 更新後前端要立刻生效（每次發版必過）

- [ ] `curl -I /static/js/file_upload.js` 有 `Cache-Control: no-cache`
- [ ] 沒有這個標頭時瀏覽器會用啟發式快取 → 升級後好幾小時還在跑舊的
      JS / CSS，**重新整理也沒用**；開發時就踩過一次
- [ ] 改過前端之後實測：更新 → 重新整理 → 新功能立刻可用

### 6.26 v1.14.25 — 書籤與目錄的預設值、檔名、預覽連動（每次發版必過）

- [ ] 「產生可以印出來的目錄頁」**預設是勾起來的**
- [ ] API 的 `toc_page` **仍然預設 `false`**（不可以連動改掉，
      會讓既有自動化呼叫突然多一頁）
- [ ] 上傳 `年度報告.pdf` → 產出檔名是 **`年度報告_bookmarked.pdf`**
      （不是寫死的 `bookmarked.pdf`）
- [ ] 接到「插入頁碼」時帶過去的檔名**是產出檔名**（`result_filename`），
      不是輸入檔名
- [ ] **改書籤標題或頁碼 → 目錄預覽跟著重畫**
      （目錄內容就是那張表，不重畫等於顯示的是上一版）

### 6.27 v1.14.26 — 貼上清單的解析效能與用詞（每次發版必過）

- [ ] 「書籤與目錄」貼上**一行兩萬個點、結尾沒有數字**的內容 ×20 行
      → **一秒內**解析完（原本每行要 5.4 秒，是可以拿來癱瘓伺服器的輸入）
- [ ] 正常的目錄清單解析結果不變（層級、引導點、警告訊息）
- [ ] `tests/test_taiwan_terminology.py` 全綠
      —— 只掃**使用者看得到的文字**；程式註解、說明文件、
      以及**刻意收錄大陸用詞的搜尋關鍵字**都要排除

### 6.28 v1.14.27 — cryptography 升版與解析效能（每次發版必過）

- [ ] `cryptography` 已是 50.x（49.0.0 的 PKCS#7 有 Bleichenbacher oracle；
      本專案只用 Fernet 與 PyJWT RS256，**沒有用到 PKCS#7**，屬不可利用，
      但 49.x 無修正版可退）
- [ ] SSO（OIDC / SAML）端對端測試全綠 —— 升 cryptography 最可能撞到的就是這裡
- [ ] Fernet 加解密正常（SSO 設定、通知管道的密鑰都靠它）
- [ ] 「書籤與目錄」貼上清單的解析，**每一版踩過的最壞輸入都要跑**：
      整行都是點 / 一長串數字接非空白 / 數字後一大片空白
      —— **換了寫法就要重新設計最壞輸入**，拿舊的去驗會誤判成修好了

### 6.29 表單自動填寫 — 改動必跑全表單回歸（每次發版必過）

> 定位邏輯（`compute_value_slot` / `pdf_form_detect`）**所有表單都會走**。
> 改壞了使用者不會馬上發現，等表寄出去才知道欄位填錯格。

- [ ] 改動**之前**先存基準：
      `python temp_pdfs/_regress/run_fill_regress.py --save before`
- [ ] 改完比對：`... --compare before`
- [ ] **判準：沒有任何一份變差**
      —— 填入數不可減少、**疊字不可增加**、原本座標不可位移
- [ ] 非填寫類（公文 / 說明書）自動略過，不列入判準
- [ ] 樣本涵蓋五種特殊版型（後置標籤 / 純底線 / 雙欄 / 直書標籤欄 / 逐格分寫）
- [ ] **樣本不可上 git、檔名與客戶名不可寫進 CHANGELOG**
      （`tests/test_no_sample_names_in_public.py` 會擋）

### 6.30 v1.12.95 — .docx 表單底色蓋掉整頁文字（VML z-index）（每次發版必過）

> Word 匯出對**純圖形**走 VML，而 VML 的 z-index 匯出時整個不寫 → 依規範
> 等同疊在文字層之上，底色塊把整頁文字蓋掉（.odt 正常、只有 .docx 壞）。
> 在 ODF 端設 `draw:z-index` 救不了 —— 資訊在匯出當下就掉了。
> 修法是轉出 .docx 後直接改寫（`_fix_docx_vml_zorder`）。
> （2026-08-16 稽核發現這宗一直沒進 §6，補上。）

- [ ] 含底色塊的表單 PDF 轉 .docx，開檔後文字**在色塊之上**（不是被蓋掉）
- [ ] 同一份轉 .odt 對照 —— 兩種輸出都要對

### 6.31 v1.14.34-35 — 作業完成列的按鈕要跟實際產出一致（每次發版必過）

> 兩宗同根因：**同一份清單在兩個地方各寫一份，遲早漂掉。**
> ①「下載 PNG」原本無條件顯示，但那個端點是把結果 PDF 算成圖 ——
> 產出不是 PDF 的工具掛著一顆必然失敗的鈕。
> ②「存至工作區」的副檔名判斷寫死在 JS（pdf|png|docx|odt），伺服器端
> v1.14.6 就多收了 xlsx/ods/pptx/odp —— 伺服器收得下、鈕卻不出現，
> 而且沒有任何錯誤訊息（使用者親自看到才回報）。

- [ ] 轉出 `.xlsx` / `.odp`：主下載鈕顯示「下載 .xlsx」等（不是「下載 PDF」）
- [ ] 產出非 PDF 時「下載 PNG」不出現；產出是 PDF 時要出現且能下載
- [ ] 產出 `.xlsx`「存至工作區」出現、按下真的存進去
- [ ] `tests/test_workspace_save_button.py` 全綠（JS 不可再寫死清單）

### 6.32 v1.14.34 — soffice 的回傳碼不可靠（每次發版必過）

> soffice 會一邊印無關警告（找不到 Java）一邊正常轉完，也可能在收尾才被
> 中止（實測 rc=137 = SIGKILL，多半是記憶體或同時轉太多份）。
> 先看回傳碼會把**已轉好的檔案白白丟掉**。判準一律是「有沒有拿到可用檔案」。

- [ ] `tests/test_office_convert.py` 的
      `test_good_output_wins_over_bad_exit_code` /
      `test_killed_process_says_so_instead_of_blaming_the_format` 綠
- [ ] 同副檔名互轉（pptx→pptx）真的有轉（無聲跳過守門也在同一檔）

### 6.33 v1.14.37 — 毀損檔案一律 400，不可 500（每次發版必過）

> 2026-08-16 全端點壞輸入掃描：毀損 PDF 打全部工具端點，**28 個回 500**。
> 使用者會以為服務掛了而一直重試（其實是檔案壞了），監控端全是假警報。
> 修法是全域 `fitz.FileDataError` 處理器（同 JSONDecodeError 的做法）。

- [ ] `tests/test_broken_input_no_500.py` 全綠
      （從路由表自動列舉全部工具 POST 端點，新工具自動被涵蓋）


### 6.48 v1.14.61 — 樣板放錯區塊 / 行內程式被切斷（每次發版必過）

> 兩個都**不會有任何錯誤訊息**：伺服器回 200、主控台乾淨、「元素有沒有消失」
> 的自動檢查全綠。使用者回報「最下面卡片超過畫面」才發現第一個；順著查才發現
> 權限矩陣頁**自 v1.14.32 起把兩百行程式碼印在畫面上**。

- [ ] `tests/test_template_block_placement.py` 全綠
      —— ①`{% block scripts %}` 裡不可以有看得見的標記（那個區塊在 `<main>`
      外面，放進去會攤成整個視窗寬）②兩個 script 區塊之間不可以漏出程式碼
      （**不是去找結束標籤的字面寫法** —— 對剖析器來說它就是合法的結尾）
- [ ] 全站版面掃描：`scripts/page_visual_check.py` 不可出現
      「卡片超出內容欄」或「整頁有水平捲動」
- [ ] 權限矩陣頁**有 subject 時**：計數徽章是程式填上的數字、搜尋框打字會過濾、
      類型分頁會切換（`temp/perm-cdp/cdp_permissions.py`）
- [ ] 權限矩陣頁**沒有任何使用者或群組時**：主控台**零例外**
      （空狀態整段跳過，不是一個一個補判斷）
- [ ] `tests/test_static_image_budget.py` 全綠 —— 自家介面圖片單檔 ≤ 200 KB

### 6.49 v1.14.63 — 表格的標題與數字要同一邊（每次發版必過）

> 使用者回報「欄位標題跟數字對齊方向不對 很難對照著看」：數字欄的標題貼左、
> 數字貼右，眼睛要橫著走一整格才對得起來。

- [ ] `temp/perm-cdp/cdp_table_align.py` 全綠（逐頁比對每一欄「標題的對齊方向」
      與「多數儲存格的對齊方向」）
- [ ] **跑之前要先讓表格有資料** —— 空表格整張不渲染，這條檢查會安靜地跳過，
      變異驗證照樣全綠（實際踩過）。系統狀態頁的用量表要先在
      `data/temp/.owners/` 塞幾筆 owner 紀錄 + 對應檔案
- [ ] 合計列不可以有空白的數字欄（同一列其他欄有數字時，空一格看起來像壞掉）

### 6.50 v1.14.64 — 去識別化的地址與跨格配對（每次發版必過）

> issue #51：`RE_ADDR` 的 `[縣市]` 誤寫成字面 `<縣市>`，**新北 / 桃園 / 高雄 /
> 基隆 / 新竹與所有「縣」的地址從上線起就沒抓到過**，而且完全無聲。
> issue #50：表格裡上下相鄰的兩個標籤格被配成一對，欄位名稱被當成人名。
>
> **這個 bug 在單元層級一眼可見，卻活了很多版** —— 因為沒有任何測試是
> 「拿一份真的有地址的檔案跑一次，看它最後有沒有被遮掉」。

- [ ] `tests/test_doc_deident_e2e.py` 全綠 —— **走完整條路徑**：
      合成 PDF → `/detect` → `/process` → **重新抽文字，確認那幾段地址不在裡面了**
- [ ] `tests/test_text_deident_e2e.py` 全綠 —— 同一條路徑的文字版
      （這支工具與文件版共用偵測式子，同一個 bug 一起中招）
- [ ] `tests/test_addr_pattern_coverage.py` 全綠（27 項：六都＋市＋縣、
      三種空白樣態、六個**不該**命中的公文與判決文句、釘死 `<縣市>` 那一條）
- [ ] `tests/test_deident_label_not_value.py` 全綠（標籤詞彙表從註冊表實算）
- [ ] 空白只吃**同一行**：`臺北市…路` ＋換行＋姓名＋換行＋`1號`
      **不可以**被兜成一筆地址（遮蔽框會跨行畫）

> 合成測試 PDF 的注意事項：用 `NotoSansCJK` 某些子字型寫「路」，抽回來會變成
> **相容表意文字 U+F937**（不是 U+8DEF），regex 對不上 —— 那是測試素材的問題，
> 不是產品的。要驗地址就避開這個字，或先做 NFKC 正規化再比對。

### 6.51 v1.14.65 — 表單自動填寫的三種「整欄不見」（每次發版必過）

> 使用者提供的一份表單偵測結果是**零**。三個問題都無聲：畫面上那一欄就是空的，
> 看不出是版型沒支援、還是資料沒填。

- [ ] `tests/test_seal_zone_marker.py` 全綠 —— 填表說明裡提到印鑑**不可以**
      讓整份表單失效；用印區的**標籤**照樣要擋掉它以下的欄位
- [ ] `tests/test_boxed_digits_and_sublabel.py` 全綠 —— 逐格分寫的小格是值區
      （單獨一兩個窄欄仍要跳過）；值格裡的子標籤不算已填、但值要從它後面開始
- [ ] **改動 `pdf_form_detect` / `pdf_layout` 一律跑全表單回歸**
      （`temp_pdfs/_regress/run_fill_regress.py --save before` → 改 → `--compare before`），
      判準是**沒有任何一份變差**、**疊字數不可增加**
- [ ] 合成樣本 `syn_seal_note_above.pdf` 要在語料裡（說明句在上、欄位在下、
      值格裡有子標籤、頁尾才是真的用印區）

> 合成樣本的字集要**列全**（`make_synthetic.py:_ALL_CHARS`）：缺一個字抽出來
> 就是 `\x00`，那個標籤偵測不到，**合成表自己會變成壞樣本**（這一輪又踩到，
> 「開戶銀行」「通訊地址」因此測不到）。

### 6.52 v1.14.66 — 括號即填寫位置、同義詞、對話框（每次發版必過）

- [ ] `tests/test_boxed_digits_and_sublabel.py` 全綠 —— 含
      `郵遞區號(     )` 的括號中間就是郵遞區號的位置，而且**不可以壓在
      「郵遞區號(」上面**（用逐字座標，不是等寬估算）
- [ ] 「開戶全名」「解放行代號」（表單上的誤植，款→放）都認得
- [ ] `tests/test_no_native_dialogs.py` 全綠 —— 樣板不可走瀏覽器原生
      `prompt` / `confirm`（`showXxx` 不在時的退路寫法允許）
- [ ] 用眼睛看一次：字型管理「變更名稱」跳出的是**本站的對話框**
      （有標題列、不會頂著網域名）

### 6.53 v1.14.67~83 — 新工具「文件翻譯」（每次發版必過）

> 這支工具的賣點是「**產出同格式、同版面，只換文字**」。所以驗收一路走到
> 產出的檔案本身：能不能打開、版面有沒有跑掉、譯文有沒有真的進去。

**自動化（`tests/test_doc_translate.py`）**

- [ ] 全綠。含端到端：假 LLM → 跑完整個作業 → **打開產出檔確認每段都換成譯文**
- [ ] 批次：段數對不上**先把那一批對切重試**，切到只剩一行才逐行翻 ——
      **絕不硬湊**（錯位會把 A 行的譯文寫進 B 行，文件看起來完全正常，
      只有讀的人會發現整份意思錯了）。驗收方式是「12 行的批次只送 4 次成功請求
      （12 → 6+6 → 3×4），不是退回 12 次單行」
- [ ] **行層級的格式要留住**：一格裡「說明文字 + 換行 + 紅色斜體補充」，翻完
      那行補充仍是紅色斜體（譯文寫回它自己的 run）。換行在 run 結尾（Excel 的
      寫法）與在下一個 run 開頭兩種都要認
- [ ] 模型把 prompt 連同原文吐回來（回聲）要被擋下 —— 它的段落標記剛好對得上，
      會「解析成功」但一個字都沒翻
- [ ] 取消：**不可以產出半翻的檔案**（比沒有更危險）
- [ ] `word/document2.xml` 這種主檔名也要認得（Word 自己會這樣寫）

**人工（每次動到這支工具就走一遍）**

- [ ] 九種格式各一份：`.doc` / `.docx` / `.odt`、`.xls` / `.xlsx` / `.ods`、
      `.ppt` / `.pptx` / `.odp` → 產出**副檔名與上傳的相同**（舊格式是內部轉新格式
      翻完再轉回去），打得開，框線 / 表格 / 頁首頁尾 / 圖片都在原位
- [ ] 前 6 頁預覽是**左原文、右譯文**並排，點得開大圖（大圖是另外算的 170 dpi，
      不是把縮圖拉大）
- [ ] 翻成中文時**行距不可以變** —— 原稿的 run 只有拉丁字型時，要補指定東亞字型，
      否則 Word 會退到日文 MS Mincho（行高變大、字形也是日文的）
- [ ] 上傳 PDF 被擋下，而且**說明原因**並導向逐句翻譯
- [ ] 翻譯中「開始翻譯」是停用的（否則會被連按、每按一次多送一份作業）
- [ ] 「我的作業」那一列有**下載鈕**（看的是 `result_path`，不是自訂 meta 的網址）；
      按「開啟」回到工具頁會接回結果，不是空白頁
- [ ] LLM 沒啟用時整個介面擋住並說明；狀態列顯示這次用哪個模型
      （管理員另外看得到 server 位址）

**效能（調參時的依據，不是每次都要跑）**

- [ ] 結果摘要會顯示「合併成 N 次 LLM 請求」與退回批數 —— 退回多才需要調批次大小
- [ ] 實測基準：每段約 0.5~0.6 秒（gemma4:26b、並行 4）；批次從 10 加到 40 段
      每段耗時不變（合併省的是重複送指令的成本，10 段就攤平了）
- [ ] **真正的限制是「每批字元」不是「每批段數」**：技術文件的儲存格動輒兩三百字，
      字數上限 1,200 時 343 段的檔案送了 **305 次**請求（等於沒合併）。
      調參後看結果摘要的請求數 ——「請求數 ≈ 段數」就是沒生效


### 6.54 v1.14.86~87 — LLM 的思考關不掉 / 批次標記被位元組 token 弄壞（每次發版必過）

> 這兩宗都是「**看起來正常但在白燒算力**」—— 沒有錯誤訊息，只是慢，而且慢的
> 原因跟直覺完全不同（第一次我以為要調批次大小、甚至換模型，都是錯的方向）。

- [ ] **`reasoning_effort:"none"` 一定要送**：Ollama 0.33 起 `think:false` 對
      gemma4 沒有用了。驗法是打一次翻譯、看回覆的 `reasoning` 欄位字數 ——
      **必須是 0**。實測沒關掉時：十段的批次 23,650 字思考、譯文只有 347 字、
      151 秒（關掉之後 4.7 秒）。
- [ ] **批次標記要容得下位元組 token**：模型會把不成字的位元組原樣吐成
      `<0xC2>` 這種**字面文字**，混進 `⟦5⟧` 中間就變成 `⟦<0xC2>5⟧` ——
      解析不到那一段、整批判定漏段、對切重試白跑一次生成。
      驗法是 `tests/test_doc_translate.py` 的
      `test_stray_byte_token_in_the_marker_is_tolerated`，以及**看結果摘要的
      「對切重試」批數**：同一份 343 段的檔案，修正前 22 批、修正後 12 批。
- [ ] **每批字元不要調大**：實測 1,200 字元是 129 批 / 129 次請求 / 607 秒；
      4,000 字元變成 34 批 / 69 次請求 / **35 批漏段** / 1,361 秒。
      漏段的成本跟批次大小成正比 —— 批次要訂在「模型幾乎都照格式回」的大小。

### 6.55b v1.14.89 — 圖示與 emoji（每次發版必過）

- [ ] **通知面板**：登入 / 未登入兩種狀態下，「通知」標題都有鈴鐺、
      「全部標示為已讀」都有勾勾，且圖示與文字**垂直對齊**。
      （兩份樣板是分開的 —— 只改一份會有一種狀態沒圖示。）
- [ ] **已經有圖示的地方不要再放 emoji**：文件去識別化的三張處理模式卡片
      標題不可以出現 emoji；三個圖示的語意要對得上
      （Redaction＝劃掉的眼睛、Masking＝`#`、Replacement＝交換箭頭；
      **不是**垃圾桶 / 鉛筆 / 循環箭頭 —— 鉛筆是「編輯」、循環箭頭是「重新整理」）。
- [ ] 新開的 CSS 類別名一定要在 `platform.css`（或該樣板的 `<style>`）裡有定義
      —— 這條記過（`af-field` / `af-ctl` / `af-note` 那次）。

### 6.55 v1.14.89 — 試算表的對照預覽只看得到第一欄（每次發版必過）

- [ ] 上傳一份**欄位超過紙寬**的 .xlsx（例如四欄的責任矩陣）→ 翻譯完成後，
      前 6 頁預覽的**第一頁就要看得到最右邊那一欄**。
      修正前：Calc 把超出紙寬的欄位丟到後面，整份 72 頁而**前 6 頁全是 A 欄**，
      使用者看到的是「只有第一欄、右邊被切掉」的畫面。
- [ ] **產出的檔案不可以被動到** —— 「調整成一頁寬」只加在**預覽用的副本**上。
      驗法：下載回來的 .xlsx 的 `xl/worksheets/sheet1.xml` **不可以**出現
      `fitToPage`。
- [ ] 原文與譯文兩邊要套**一樣**的列印設定，否則比對不公平。
- [ ] 壞掉 / 非 zip 的檔案要原樣轉（預覽是附屬品，不可以害整個作業失敗）。

### 6.56 v1.14.90 — 樣板的全域函式被迴圈變數遮蔽（每次發版必過）

- [ ] 首頁、側欄、任何有 `{% for t in ... %}` 的樣板都要**開得起來**。
      i18n 的樣板函式當初叫 `t()`，而全站有十幾個樣板用 `t` 當工具的迴圈變數
      → 迴圈裡呼叫 `t('字')` 變成呼叫那個 dict，`'dict' object is not callable`
      **整頁 500**。改名 `tr()` 之後才沒事。
- [ ] 守門：`tests/test_i18n_catalog.py` 釘死「樣板全域只註冊 `tr`，
      不可以再出現單字母的 `t`」—— 這種撞名不會有靜態警告，只會在
      「剛好那一頁有迴圈」的時候炸。

### 6.57 v1.14.91 — 語言只認明確選擇，不看瀏覽器（每次發版必過）

- [ ] 帶 `Accept-Language: en-US` 但**沒有選過語言**的請求 → 介面仍是**繁體中文**，
      七支中文專用工具**照常可用**。
      為什麼不自動切：切成英文會把那七支工具反灰，**台灣同事只因為瀏覽器是
      英文就少了七支工具**，而且他不會知道為什麼。
- [ ] 選過語言之後（cookie `jtdt_locale`）才切換，重新整理仍然記得。
- [ ] `POST /ui-locale` 的 `next` 只收站內路徑（`/` 開頭且不是 `//`），
      擋開放轉址。

### 6.58 v1.14.92 — 產生出來的 CSS 裡不可以混進 JS 運算式（每次發版必過）

- [ ] `static/css/generated-inline.css` 必須是**合法 CSS**。
      這份檔是把樣板裡的行內 `style="..."` 抽出來產生的（CSP 不准行內樣式），
      而抽的時候把兩段**含 JS 字串運算**的樣式一起抽了進去
      （`background:' + avatarBg + ';`）→ 那兩條規則整條無效，
      **大頭照沒有底色**，而且**沒有任何錯誤訊息**（瀏覽器只是安靜跳過壞規則）。
- [ ] 守門 `tests/test_generated_css_valid.py`：不可以出現 `' +` / `+ '`
      這種字串串接的痕跡，也不可以有沒配對的引號。

### 6.59 v1.14.93~94 — 英文版文件是「生成」的（每次發版必過）

- [ ] `docs/index-en.html`、`docs/api-en.html`、`README_en.md` **不可以有殘留中文**
      （守門 `tests/test_docs_english_pages.py` 逐行檢查，程式區塊除外）。
- [ ] 改了中文版之後**要重跑生成器**（`build-i18n-page.py` / `build-i18n-md.py`），
      否則英文版停在舊內容 —— 這個專案已經吃過兩次虧
      （`github/TEST_PLAN.md` 手動複製漂了 182 行、介紹站的工具數與卡片對不上）。
- [ ] 中英兩版最上面的語言切換要**互相指得到**（`README.md` ↔ `README_en.md`、
      `CHANGELOG.md` ↔ `CHANGELOG_en.md`、兩個網頁的 langSwitch）。
- [ ] README 的 pytest 徽章不可以低於 `tests/` 裡 `def test_` 的個數
      （守門 `test_readme_pytest_badge_is_not_stale`）—— 它曾經停在 **470**，
      而實際是 5,9xx。

### 6.60 v1.14.94 — 字數統計收辦公文件（每次發版必過）

- [ ] 九種辦公格式（doc/docx/odt、xls/xlsx/ods、ppt/pptx/odp）都能統計，
      而且**頁數是轉成 PDF 後的真實頁數**，不是段落數硬湊的。
- [ ] 毀損檔案或缺 Office 引擎 → **400**，不是 500；判準是「**有沒有拿到可用檔案**」
      而不是 soffice 的回傳碼。
- [ ] `OFFICE_TOOL_IDS` 要含 `pdf-wordcount`（漏列會低估記憶體、派送時開太多份），
      README 與介紹站的**扳手標記**同步（`check_docs_tool_coverage.py` 會擋）。

---

### 6.61 v1.14.97 — 英文版文件的譯文要對得上原文（每次發版必過）

- [ ] `docs/index-en.html` / `docs/api-en.html` / `README_en.md` **殘留中文 0**。
- [ ] **行內標籤與連結要一模一樣**（`test_translation_keeps_the_same_inline_tags_and_links`）
      —— 譯到一半被截斷、或整條貼錯鍵，標籤數就對不上。
- [ ] **逐區塊比對中英兩份產出**：英文區塊以標點開頭、中文不是 → 紅。
      判準**放在產出的頁面上不放在語系檔**（語系檔裡「以標點開頭」有時候是對的）。
- [ ] **純標點的鍵不可以存在** —— 表格裡孤零零一個 `—` 會變成到處都對得上的鍵。
- [ ] 改了中文版之後**要重跑生成器**（`build-i18n-page.py` / `build-i18n-md.py`）。

### 6.62 v1.14.98~99 — 前端字串翻譯（每次發版必過）

- [ ] **繁體中文不載字典**：中文頁面不可以出現 `<script src="/i18n/...">`，
      `GET /i18n/zh-Hant.js` 回空字典。
- [ ] `GET /i18n/en.js` 帶 `If-None-Match` 要回 **304**。
- [ ] **CDP 驗行為**（`temp/i18n-cdp/cdp_i18n_test.py`）：英文介面按下按鈕跳英文
      提示、繁中介面**一字未變**。判準要挑「只有真的翻到才會出現」的訊號 ——
      沒翻到時 `tr()` 原樣回傳中文，**一樣沒有 JS 例外**。
- [ ] JS 的 `tr()` 鍵**不可以含樣板語法**（Jinja 先渲染，執行期查不到，而且無聲）。
- [ ] **三元運算與字串串接的字串不可以包** —— 那些可能是拿去比較或送給伺服器的值，
      翻掉之後畫面正常、只有邏輯壞，而且只在英文介面才壞。

### 6.63 v1.15.0 — i18n 不可以碰領域資料（每次發版必過）

- [ ] 欄位同義詞字典、會計科目詞庫、去識別化的式子**一個字都不可以進語系檔**
      （`test_domain_data_modules_never_use_the_translation_helper`）。
      翻掉會讓表單自動填寫**安靜地抓不到欄位** —— 畫面顯示「已處理」，只有收件方發現。
- [ ] **產品名不自動翻**：那是品牌，而且管理員可以自訂站台名稱。

---

### 6.64 v1.15.1 — 縮圖頁碼超出範圍不可以 500（每次發版必過）

- [ ] `/tools/<id>/thumb/<upload_id>/0`、`/99` 一律 **4xx**（判準是「不是 5xx」，
      有些工具自己先擋回 400 也對）。頁碼在路徑上 —— 那是使用者送錯網址，
      **500 會讓人以為服務掛了而一直重試**。
- [ ] **反向對照**：第 1 頁還是要畫得出圖而且不是空的。
      只驗「超範圍會被擋」的話，把端點改成永遠回 404 也會過。
- [ ] 守門 `tests/test_preview_page_range.py`；修法是**全域處理器**
      （`PageOutOfRange` → 404），不是逐支端點改 —— 逐支改下一支新工具又會漏。

### 6.65 v1.15.1 — 英文介面要真的跑得動（每次發版必過）

- [ ] `temp/i18n-cdp/cdp_en_e2e.py`：英文介面送 PDF 進去，字數統計算得出頁數
      與字數、頁面轉向取得縮圖且不是空的。
- [ ] **下拉的 `value` 不可以變成中文** —— 那些是送給伺服器的值，翻掉之後
      畫面完全正常、只有邏輯壞，而且只在英文介面才壞。

---

### 6.66 v1.15.3 — 圖示與對話框（每次發版必過）

- [ ] **同一組內不可以有兩支工具用同一個圖示**（`test_no_two_tools_in_one_group_share_an_icon`）。
      側欄相鄰又同圖示等於要讀完字才分得出來（v1.15.3 一次清掉四對）。
      **跨組重複不管** —— 那是兩個不同的清單。
- [ ] **圖示名稱必須存在於 `icons.html`** —— 打錯不會報錯，只是沒有圖。
- [ ] **對話框的標題與按鈕**（`showConfirm` 的 `title` / `okText` / `cancelText`）
      在英文介面下要是英文。**只在對話框呼叫範圍內翻** —— `title:` 在別的地方是
      資料不是顯示文字（書籤的 `{title, page, level}`），翻掉是改壞資料。
- [ ] 新畫的 SVG 圖示要**算圖確認過**才收（snap chromium 的 `--screenshot`
      要寫到 `~/snap/chromium/common/`，寫 `/tmp` 會落在它自己的沙箱裡）。

---

### 6.98 v1.15.32 — 英文文件的去識別化（**每次發版必過**）

- [ ] `pytest tests/test_doc_deident_english.py tests/test_doc_deident_english_e2e.py` 綠燈
- [ ] **台灣真實樣本零退步**（`temp_pdfs`）—— 判準同表單回歸：
      原本抓到的一筆都不可以少
- [ ] **語系隔離兩個方向**：英文文件上台灣專屬式子不可以啟用
      （它們在英文文件上是**抓錯**不是抓不到）；中文文件上英文式子也不啟用
- [ ] **誤判語料**：一份滿是料號 / ISBN / 版本號的英文文件，
      敏感類別命中必須是 **0**（只驗「抓得到」的話，放寬到抓一切也會過）
- [ ] 有檢查碼的一律驗：IBAN mod-97、SSN 不發的號段、NANP 首位、NI 保留前綴
- [ ] **端到端要打開產出看內容**（§0.5）：英文 PDF 跑完，
      SSN / 電話 / Email / IBAN 都撈不回來，而標題等內容要留著
- [ ] 替換模式：英文文件的假值**不含中文**，而且假 SSN / IBAN / 電話
      **刻意不合法**（驗得過的假號碼可能真的屬於某個人）
- [ ] 人名的式子不可以吃掉下一個欄位的第一個字（`\s+` → 單一空白）
- [ ] 文件語言的下拉在兩支工具上都有，預設跟著介面語言、可以改
- [ ] 反灰清單只剩五支（統編查詢 / 電子發票 / 送件前檢核 / 乘車證明 /
      表單自動填寫）—— 它們靠的是台灣的資料庫與版型，不是語言問題

---

### 6.97 v1.15.31 — 產品名稱多語系與服務硬化（**每次發版必過**）

- [ ] `pytest tests/test_installer_product_name.py tests/test_installer_languages.py` 綠燈
- [ ] 安裝程式裡剩下的中文只有兩個刻意保留的字面值
      （`APPNAME` 的預設值、`LEGACY_SM_FOLDER`）
- [ ] **解除安裝要從登錄檔讀回開始功能表路徑**，不可以用當下的語系重算
- [ ] 刪除前驗過那個路徑在 `$SMPROGRAMS\` 底下（可疑就拒絕並留痕跡）
- [ ] 舊版的中文資料夾名仍然清得掉
- [ ] `install.sh` 產生的 unit 與 `packaging/jt-doc-tools.service` **同一組
      硬化設定**（判準是「整行的指令賦值」，不是字串出現過 ——
      註解裡列了那些名字，用字串比對會沒有牙齒）
- [ ] 可寫路徑只有資料目錄；外部匯出路徑的限制**寫在產生出來的 unit 裡**

**人工（要真的機器）**

- [ ] Windows 三種情境（已用獨立探針在 zh-TW 機器上驗過，
      **不動既有安裝**）：記下的路徑刪得掉 / 可疑路徑被拒 / 舊資料夾清得掉
- [ ] **完整循環仍待做**：裝 → 解除安裝 → 重裝，以及
      「裝舊版（中文資料夾）→ 升級 → 解除安裝，舊資料夾也要消失」
      —— 這個會讓測試機短暫離線，留給有人看著的時候跑
- [ ] Linux：`systemd-run` 帶那五項設定逐項確認
      （安裝目錄不可寫、資料目錄可寫、字型可讀、`/mnt` 被擋）
- [ ] **英文畫面仍沒有人親眼看過** —— 手邊兩台 Windows 都是 zh-TW

---

### 6.96 v1.15.29 — 外部稽核第二批（**每次發版必過**）

**F08 稽核轉送**

- [ ] `pytest tests/test_audit_forward_per_destination.py` 綠燈
- [ ] 一個目的地失敗 → **只有它的游標停住**，其他目的地照常前進
- [ ] 失敗的目的地會退避（不然每輪都在重試，把迴圈拖垮）
- [ ] **`audit_forward_failed` 不可以被轉送**（會自我餵食）；失敗記錄有冷卻
- [ ] 升級時沿用舊的共用游標當起點（否則重送整份歷史）
- [ ] 說明文字不可以再承諾「不漏送、不重複」
- [ ] **要驗真的送到 Graylog**，不是只確認 socket 沒報錯（人工項）

**F09 PNG 匯出**

- [ ] `pytest tests/test_job_png_export.py` 綠燈
- [ ] 不可以把每頁 bytes 堆成 list、也不可以用 BytesIO 組整包 zip
- [ ] 暫存要落在 `settings.temp_dir`（清理只掃那裡、**而且只刪檔案跳過目錄**
      → 產出必須平鋪）
- [ ] 中間檔用完立刻刪；產出在下載結束後刪
- [ ] 有併行上限（這條路不經過作業准入）
- [ ] **人工**：反覆下載十次後看 `/tmp` 與資料磁碟有沒有長大

**F10 管理員的隱私界線**

- [ ] `pytest tests/test_admin_privacy_boundary.py` 綠燈
- [ ] 上傳 / 預覽與作業產出**兩條路走同一份政策**（判準走 AST）
- [ ] 越權讀取一定寫稽核；**讀自己的不算越權**；同一資源有去重視窗
- [ ] 政策關掉時要真的拒絕，而且沒有東西可稽核
- [ ] 改政策時，權限矩陣與產品說明要跟著改（人工項）

---

### 6.95 v1.15.28 — 外部稽核第一批（**每次發版必過**）

**F01 去識別化要真的刪掉圖片裡的個資**

- [ ] `pytest tests/test_doc_deident_image_residue.py` 綠燈
- [ ] 判準是**把產出的圖片抽出來檢查像素**（有 tesseract 的話再 OCR 一次）
      —— 「畫面有黑框、文字抽不到」完全不算
- [ ] 遮罩 / 替換模式也要清掉圖片像素
- [ ] **圖片不可以整張消失**（那樣掃描件會整頁空白）、選取範圍外的內容不可以動
- [ ] `PDF_REDACT_IMAGE_NONE` 不可以出現在 `doc_deident`（判準走 AST）
- [ ] 結果頁要提醒「檔案可能變大」並指路到 PDF 壓縮，**且只在真的動到圖片時顯示**

**F03 升級失敗要真的回復**

- [ ] `pytest tests/test_cli_update_rollback.py` 綠燈
- [ ] 三條失敗路徑（降版偵測 / `uv sync` 失敗 / 相依 import 失敗）都會回復
- [ ] 訊息要分得出三種結局：完整回復 ／ 程式碼回去了但相依沒 ／ 連程式碼都回不去
- [ ] **不可以再出現 `restoring previous state` 這種只說不做的字串**
- [ ] `uv` / `git` 不存在時不可以丟例外（那是錯誤處理途中的第二次爆炸）

**F07 GELF TCP 的訊框**

- [ ] `pytest tests/test_audit_forward_framing.py` 綠燈
- [ ] GELF TCP 以 `\0` 結尾且訊息內無原始換行；GELF UDP 不加分隔符
- [ ] syslog / CEF 維持換行（**修 GELF 不可以順手改掉這兩種**）
- [ ] 分隔符只能在一個地方決定（formatter 裡不可以自己加）

**F05 取消要釋放執行函式**

- [ ] `pytest tests/test_job_manager_cancel_release.py` 綠燈
- [ ] 取消排隊中的作業 → callable 立刻釋放，**但那一列要留著顯示「已取消」**
- [ ] 記憶體裁切與過期清理丟掉作業列時，附帶狀態也要丟
- [ ] 所有「這件作業結束了」的路徑都走同一個 `_forget`（判準走 AST）

---

### 6.94 v1.15.27 — 安裝程式在英文 Windows 上要是英文（**每次發版必過**）

- [ ] `pytest tests/test_installer_languages.py` 綠燈
- [ ] 對話框與元件名稱不可以寫死中文（解除安裝那三句最容易漏 ——
      那條路徑在語言選擇之前就結束）
- [ ] 每條 LangString 都要有全部宣告語言的版本
      —— **`makensis` 不會警告這件事**（實測拿掉一條英文條目，零警告）
- [ ] `makensis -DVERSION=<版本> installer.nsi` 編得過
- [ ] 產品名稱 / 開始功能表捷徑**維持中文**是刻意的（它們是路徑）；
      要改必須連同「刪除時試各語系舊名字」一起做，並實機跑
      裝 → 解除安裝 → 重裝
- [ ] **不要用執行期 `StrCpy $LANGUAGE` 去驗英文畫面** —— NSIS 在啟動時就
      選定語言表，改了不會重新解析（繁中機器上實測過）

---

### 6.93 v1.15.26 — 代理宣稱的協定要跟瀏覽器實際的一致（**每次發版必過**）

> 客戶回報「遠端電腦一上傳就 CSRF token 遺失或不正確，本機不會」。
> 根因是 `OPS.md` 的 IIS 範例寫死 `X-Forwarded-Proto: https`，站台卻只有 http
> → cookie 帶 `Secure` → 瀏覽器丟掉。

- [ ] `pytest tests/test_proxy_scheme_mismatch.py` 綠燈
- [ ] `OPS.md` 的 IIS 範例**不可以**寫死 `value="https"`
- [ ] 共通要求那一節要寫出「**在伺服器本機測不出來**」（localhost 是例外）
- [ ] 403 的訊息在偵測得到時要說出是哪個標頭設錯了
- [ ] 反向（代理說 http、瀏覽器是 https）**不可以**報成故障
- [ ] **不可以**因為看到 `Origin: http://…` 就不加 `Secure`（cookie 降級）
- [ ] 要重現的話：起一個會加 `X-Forwarded-Proto: https` 的代理，用真的瀏覽器
      分別開 `http://localhost:<埠>` 與 `http://<別的主機名稱>:<埠>` ——
      前者 cookie 存得下來、後者存不下來

---

### 6.92 v1.15.25 — 設定頁自動帶值不可以蓋掉存好的值（**每次發版必過**）

> 客戶回報：「連接埠就算改成 25 按儲存，下次再回來看又變 587。」
> 換寄送方式時幫忙帶慣例埠號的那段程式，**在頁面載入時也跑了一次**。

- [ ] `pytest tests/test_notify_settings_form.py` 綠燈
- [ ] 用真的瀏覽器走一次：`/admin/notify` 把埠改 25 → 儲存 → **重新載入**
      → 仍然是 25（舊版會變 587）
- [ ] 換寄送方式時仍會帶慣例埠（外部帳號 587、轉送 / 直送 25），
      但**自己填過的非慣例值（如 2526）一個字都不碰**
- [ ] **判準是「這個值是不是使用者存的」**，不是「這次載入他有沒有打字」——
      那個旗標每次載入都會重置，等於沒有防護
- [ ] 站台網址欄看得到完整網址（不可以繼承數字欄位的 `width: 110px` 靠右樣式）
- [ ] 這一頁只有埠號這一處會自動改欄位的值（新增自動帶值時要重新確認）

---

### 6.91 v1.15.24 — 文件裡的安裝順序（**每次發版必過**）

> 客戶回報：`OPS.md` 的 IIS 那節寫「先裝 ARR + URL Rewrite」——
> **ARR 相依於 URL Rewrite**，反過來裝會裝不起來。

- [ ] `pytest tests/test_ops_iis_prereq_order.py` 綠燈
- [ ] 標題與內文都照正確順序（URL Rewrite → ARR）
- [ ] **要寫出「為什麼」** —— 只換順序不說原因，下一個人還是會調回去
- [ ] 附官方下載連結（WebPI 已退役，現在是手動裝 MSI）

### ⚠ 文件錯誤沒有任何自動化抓得到

> 程式完全正確、測試全綠、CI 全綠 —— **只有照著文件做的人會卡住**，
> 而他多半不會回報，會以為是自己的環境有問題。

- [ ] 凡是「照著做」的步驟（安裝順序、相依關係、前置條件），
      改動時要用**字面守門**釘住，不能只靠 review

---

### 6.90 v1.15.23 — 預覽不是產出（**每次發版必過**）

> 客戶回報「只翻到第六頁」。實跑他們的檔案：182 段翻了 179 段、產出 11 頁
> 每一頁都有內容 —— 只是預覽只算前 6 頁。他們同時說「整體字數跟原始檔案
> 接近」，正好印證下載的檔案是完整的。

- [ ] `pytest tests/test_preview_is_not_the_result.py` 綠燈
- [ ] **每一支只出部分預覽的工具**都要在預覽結束的位置有擋板
      （目前：文件翻譯、PDF 轉文書檔）
- [ ] 擋板**不可以是灰色小字**（要有框線與底色）—— 使用者是捲到最後一張
      才下判斷的，寫在上面的說明他早就捲過去了
- [ ] 擋板上要寫得出**整份幾頁、後面還有幾頁**，並且**就地放下載鈕**
- [ ] 摘要要明說「全部都已翻譯完成」，每張預覽的標題要寫「第 N 頁 / 共 M 頁」

### ⚠ 使用者只能從畫面判斷

> 這條的通則：**畫面上看得到的東西如果只是產出的一部分，就一定要講出
> 「完整的有多少」**。否則使用者會把看得到的當成全部 —— 而且他不會來問，
> 他會以為功能壞了。

---

### 6.89 v1.15.22 — 串流回應要有整次生成的上限（**每次發版必過**）

> 客戶的年報翻到第 24 段就永遠卡住。那一段是表格的填空欄位
> （`For the period from<16 個不斷行空白> to`），模型停不下來。

- [ ] `pytest tests/test_llm_stream_deadline.py` 綠燈
- [ ] **兩處串流迴圈都要檢查**（只補一處等於沒補；用 AST 判斷真的有呼叫，
      寫在註解裡不算）
- [ ] 錯誤訊息要說得出**是模型不是網路** —— 連線失敗要查網路、模型停不下來
      要看那一段文字，處理方式完全不同
- [ ] 上限設 0 時不強制（留給刻意要跑很久的部署）
- [ ] **這個 bug 的症狀是「什麼都沒發生」**：畫面顯示「翻譯中… N/M」不動、
      沒有錯誤、也不會失敗。驗收要看**進度會不會前進**，不是看有沒有紅字

### 從 PDF 來的文件要翻譯，畫面上要說用哪一顆引擎

- [ ] 文件翻譯頁看得到「先轉 .docx、引擎選 pdf2docx-refine」
- [ ] PDF 轉文書檔頁的三張引擎卡各自標明適不適合拿去翻譯
- [ ] 判準是**保住幾段完整段落**（實測 11 / 6 / 0），不是視覺相似度 ——
      jtdt-layout 視覺 0.997 最高，卻是翻譯最差的那一顆

---

### 6.88 v1.15.21 — 含文字方塊的 Word 檔翻譯（**每次發版必過**）

> 客戶的兩份 PDF 轉成文書檔再翻譯時抓到。**任何含文字方塊的 Word 文件都會踩到。**

- [ ] `pytest tests/test_docx_textbox_translation.py` 綠燈
- [ ] 一個文字方塊只能算**一段**（不是四段）：外層容器段落不算、
      舊格式那份不算
- [ ] **譯文要落在自己的方塊裡** —— 修正前第一個小標題框會收到整頁的譯文
- [ ] 譯文要鏡射到舊格式那一份（不然檔案裡藏著一份完整原文）
- [ ] **節點數對不上就整組不動**（硬對會把 A 方塊的譯文寫進 B 方塊）
- [ ] 一般段落與表格儲存格的行為完全不變

### PDF 轉文書檔：三顆引擎的實測（`temp_pdfs/customer/`，**不可公開**）

> 客戶提供的兩份英文文件。判準是**轉回 PDF 後跟原檔逐頁比對**。

- [ ] 有文字層的文件（12 頁年報）：`jtdt-layout` 12.7s / 12 頁 / 視覺 0.997、
      `jtdt-reform` 183s / 12 頁 / 0.958、`pdf2docx-refine` 40s / **14 頁** / 0.951
- [ ] **文字被轉成外框曲線的 PDF**（15 頁法律文件，0 個字元）：
      **沒有任何引擎救得回文字** —— 那不是引擎的問題，要先跑 OCR。
      `jtdt-layout` 27s / 3 頁 / 0.997、`jtdt-reform` 把整頁切成 **414 張圖片碎片**、
      `pdf2docx-refine` 3 頁變 **1 頁**
- [ ] 轉完再翻譯，跟原 PDF 比：`jtdt-layout` **12 → 12 頁、0.971**；
      `pdf2docx-refine` 12 → **15 頁**、0.943

---

### 6.87 v1.15.19 — 翻譯對照字典（**每次發版必過**）

> 企業內部的專有名詞（`Acer→宏碁`、產品名不要翻）要有一致的譯法。

- [ ] `pytest tests/test_translation_glossary.py tests/test_translation_glossary_e2e.py` 綠燈
- [ ] **不可以靠 prompt**：`_build_prompt_prefix()` 的輸出不因為字典而改變
      （翻繁中的指令已 1,179 字元、每批內容上限 1,200 —— 塞進去會把批次擠掉一半）
- [ ] 拉丁詞的**前後邊界都要驗**：`Acer` 不可以命中 `Acerbic` **也不可以命中
      `MyAcer`**（只驗一邊的話，拿掉另一邊守門照樣全綠）
- [ ] 中日韓詞用子字串（硬加 `\b` 會讓中文詞整個匹配不到）
- [ ] **最長優先**：`Acer Chromebook` 要贏過 `Acer`
- [ ] **產出裡絕對不可以殘留 `⟪1⟫`**：模型把標記弄丟 / 改壞 / 重複吐兩次，
      都要退回不保護重翻，而且**要誠實回報退回了幾次**
- [ ] 勾選要真的關得掉，而且**只在該語言對有條目時才出現**
- [ ] 詞條裡的 `⟦⟧` / `⟪⟫` / 控制字元要被清掉（會破壞批次協定與還原）
- [ ] **退回的計數兩條路都要算**：批次那條與單段那條。第一版只加了批次，
      單段的退回不計 —— 回報「0 次退回」但字典其實沒生效，**比不回報還糟**
- [ ] 字典為空時，`protect()` 要原樣回傳、對照表是空的
      （沒設字典的安裝，這條路徑上一個位元組都不會變）
- [ ] 幾千條的字典不可以讓每段文字都重讀檔（依 mtime 失效的快取），
      但管理員存完要**立刻生效**，不必重啟

### ⚠ 只驗核心模組不算驗收

> 接線錯了（沒把比對器傳下去、旗標沒讀到）核心測試照樣全綠，而使用者拿到的
> 譯文裡專有名詞還是錯的。

- [ ] 兩支工具都要有**跑完整條路徑、打開產出檔**的測試
- [ ] 拉丁詞的邊界要**前後兩邊都驗**：只驗 `Acerbic`（後邊界）的話，
      把前邊界拿掉守門照樣全綠 —— 要再加一個 `MyAcer`

---

### 6.86 v1.15.17 — 升級前的備份與每請求的設定讀取（**每次發版必過**）

- [ ] `pytest tests/test_update_backup.py` 綠燈
- [ ] **不可重建的資料永遠不可以進跳過清單**（`auth.sqlite` / 各種 history /
      `assets` / `fonts` / `workspace` …）—— 跳過清單的判準只有一條：
      **這東西自己會長回來**
- [ ] **空間檢查要在 `svc_stop()` 之前**：先停服務再複製的話，磁碟滿掉時
      使用者拿到的是「服務停著、備份寫到一半、空間被吃光」
- [ ] 備份失敗要明講（不可以無聲繼續）
- [ ] 行為層也要驗：只驗跳過清單的話，`ignore=` 比對錯東西照樣全綠

### ⚠ 每一個請求都會跑到的程式碼不可以做同步 I/O

> v1.15.12 的上傳上限檢查在最外層中介層，讀設定沒有快取 —— 每個請求
> （連靜態檔與 healthz）一次檔案讀取，就在事件迴圈上。

- [ ] 中介層讀設定要有**依 mtime 失效**的快取（改完要立刻生效）
- [ ] 驗法是**攔截檔案讀取實際數**：靜態掃描看不到藏在被呼叫函式裡的 I/O
      （這次就是這樣藏的）。首頁 / 工具頁 / 我的作業每請求應為 **0 次開檔**

---

### 6.85 v1.15.16 — 以 root 執行的 CLI 與公開樹的完整性（**每次發版必過**）

> 主動稽核「只有安裝／升級才會遇到」的問題時找到的。

- [ ] `pytest tests/test_cli_data_dir_ownership.py` 綠燈
- [ ] **以 root 寫資料目錄的指令收尾一定要還原擁有者**
      （`jtdt reset-password` / `ocr-lang *` / `auth *` / `update`）——
      少了它，服務帳號會拿到 `attempt to write a readonly database`
- [ ] 判斷「有沒有呼叫防護」一律走 **AST 的 Call 節點**（寫在註解裡不算）
- [ ] 公開樹要有測試計畫叫人跑的每一個檔案（`tools/` 與 `scripts/` 都要同步）
- [ ] 指令行的判準要認得**帶路徑的直譯器**（`.venv/bin/python …`）

### 升級路徑（拿舊版建的資料實跑）

- [ ] v1.12.0 / v1.14.46 / v1.15.7 的資料目錄，用最新版開得起來
- [ ] **資料要活著**：`group_members` 筆數不變（`_m8` 當年就是在這裡清空的）
- [ ] 新工具的權限要自動補進既有角色（`role_perms` 會變多）
- [ ] 用升級後的舊資料掃**全部 GET 路由，不可以有 5xx**
- [ ] 只用 `requirements.txt` 建的乾淨環境要載得出**全部工具**
      （少一個相依宣告就會少工具，而且是安靜的）

---

### 6.84 v1.15.15 — `jtdt update` 的健康檢查與相依宣告（**每次發版必過**）

> 客戶回報：更新完印 `Health check timed out`，服務其實是好的。

- [ ] `pytest tests/test_cli_health_check.py tests/test_declared_dependencies.py` 綠燈
- [ ] **綁定位址要去問服務**，不可以只讀執行更新那個 shell 的環境變數
      （`sudo jtdt update` 繼承不到 systemd 的 `Environment=`）
- [ ] 三種平台的設定格式都讀得出來：systemd unit / macOS launcher / WinSW XML
- [ ] `0.0.0.0` 探 loopback；綁單一網卡時**loopback 也要探**
- [ ] **本機探測不走代理**（`http_proxy` 設著時「連自己」會被送去代理）
- [ ] 健康檢查失敗要印出：探過哪些位址、服務狀態、**日誌最後 20 行**
- [ ] 改過 port 的安裝，`jtdt status` 印出來的網址要是對的

### ⚠ 直接 import 的第三方套件一定要宣告，傳遞相依不算

> `defusedxml` 從 v1.15.8 起被四個模組直接 import 卻沒有宣告。沒裝到的
> 機器上那四支工具**在啟動時被安靜跳過**（日誌一行 ERROR，服務照常起來、
> healthz 照樣 200）。

- [ ] `app/` 直接 import 的第三方套件，`pyproject.toml` 與
      `requirements.txt` 都要有（`test_every_third_party_import_is_declared`）
- [ ] 三處相依煙霧測試（`app/cli.py` / `install.sh` / `setup-python.cmd`）
      要跟著補 —— 煙霧測試沒列到的東西，缺了不會有人發現

---

### 6.83 v1.15.14 — 試算表翻譯的預覽與捲動位置（**每次發版必過**）

> 使用者回報三件事：並排預覽右邊（譯文）整片空白、左邊（原文）的表格被切到
> 頁面外面、打開翻譯後的檔案乍看是空的。

- [ ] `pytest tests/test_doc_translate_spreadsheet_view.py` 綠燈
- [ ] **改完 XML 一定要確認它還讀得進去**：預覽用的「縮成一頁寬」副本，
      每一張工作表改完都要剖析一次，剖析不過就退回原檔的列印設定
- [ ] **改屬性要用換的不是再寫一次**：原檔已經有 `fitToPage` /
      `fitToWidth` / `fitToHeight` 時，改完每個屬性都只能出現**一次**
- [ ] **原文與譯文兩邊要套一樣的列印設定**：原稿存成沒有副檔名的暫存檔，
      判斷格式**不可以看檔名**，要由呼叫端把格式傳進來
- [ ] 拿一份四欄的試算表實跑：兩邊預覽都要看得到**全部四欄**
      （修正前是譯文 1 頁空白、原文 6 頁只有 A 欄）
- [ ] **翻譯後的檔案要開在內容的開頭**：`pane` / `sheetView` 的
      `topLeftCell`、選取的儲存格都歸零；ODF 走 `settings.xml`
- [ ] **凍結與分割的位置不可以一起歸零**，儲存格內容一個位元都不變

### ⚠ soffice 讀不進去的工作表**不會報錯，會變成一張空白表**

> 這次的檔案回傳碼 0、PDF 產得出來、頁首頁尾都在，只是一個儲存格都沒有。
> 「轉出來了」不是「轉對了」——判準要看**產出裡面有沒有東西**。

- [ ] 預覽類的驗收要**算圖數墨水**或抽文字，不可以只看「檔案有產出」

---

### 6.82 v1.15.13 — zip 炸彈（**每次發版必過**）

> 辦公文件、工作區、送件檢核、文件翻譯、逐句翻譯、資產匯入、統編資料庫
> 全都是 zip。先前只有辦公文件那條路有防護。

- [ ] `pytest tests/test_zip_bomb_guard.py` 綠燈
- [ ] **每一條讀使用者 zip 的路徑都接上防護**
      （`test_every_user_facing_zip_read_is_guarded` 用 AST 檢查真正的呼叫）
- [ ] **好檔案一個都不能誤擋**：正常的 docx / odt、以及「小檔案但高壓縮比」
      的純文字 .odt 都要照常處理
- [ ] **統編資料庫的匯入仍然做得起來**（170 萬筆，解開後好幾百 MB，
      上限放寬到 8 GB）—— 這條最容易在「統一門檻」時被誤擋
- [ ] **設定備份的匯入維持它自己那一套**（2 GiB / 512 MiB / zip-slip 白名單），
      不可以為了統一而換成通用判斷

### ⚠ 涵蓋型守門要用 AST，不要用字串比對

> 這條守門第一版檢查「函式裡有沒有提到 `zip_guard`」，結果被**我自己寫的註解**
> 騙過去（註解裡就有那四個字），拿掉防護後照樣全綠。

- [ ] 判斷「有沒有呼叫某個防護」一律走 AST 的 `Call` 節點
- [ ] **變異驗證第一次沒紅時要去追為什麼** —— 先確認變異真的套用了
      （比對前後的出現次數），不要解釋成「大概是快取」

### 6.81 v1.15.12 — 缺中日韓字型時的行為（**每次發版必過**）

> CI 的核心 job 沒裝字型，一次紅十條，訊息是
> `TypeError: cannot unpack non-iterable NoneType` —— **看不出跟字型有關**。

- [ ] 用 `temp/ci-sim/no_sysdeps.py` 跑一輪（同時關掉 `find_soffice()` 與
      `best_cjk_path()`）→ 不可以有 FAILED / ERROR，只能有 skip
- [ ] 每個 `best_cjk_path(...)` 的呼叫點都是**先判斷再解包**，
      skip 的原因寫得出「這台機器沒有中文字型」
- [ ] CI 的核心 job 要裝 `fonts-noto-cjk` —— **只加 gate 不補相依，
      那十幾條「中文真的畫得出來」的驗證就在 CI 上整片消失**

### ⚠ CI 失敗要看得到是哪一條

- [ ] pytest / bandit 失敗時把失敗項目寫成 `::error::` annotation
      （job 的 log 需要 repo admin 權限才讀得到，只看得到 exit code 等於沒有線索）
- [ ] 判讀方式：`GET /repos/{owner}/{repo}/check-runs/{job_id}/annotations`
      —— **匿名就讀得到**

### 6.79 v1.15.11 — 乘車證明的原始檔（**每次發版必過**）

- [ ] 上傳一份乘車證明 → 表格那一列有**眼睛圖示**，點下去開得出原始 PDF
- [ ] **拿別人的 entry_id 打 `/tools/transit-proof/file/<id>` 一律 404**
      （歸屬由路徑結構決定，路徑從當前登入者算出、不吃請求參數）
- [ ] 同一張證明重複上傳 → 只佔一份空間（去重的不存第二份）
- [ ] **四條刪除路徑都要清檔**：單筆 / 批次 / 全部清空 / 上限淘汰。
      清空之後再點那個連結要 404
- [ ] 「檔案保留 / 清理」頁看得到**乘車證明原始檔**（佔用空間、最舊一筆、保留期），
      設 0 = 永久保留時**完全不刪**
- [ ] 這個功能之前上傳的舊資料**不該出現按鈕**（沒有原件，點了會 404）
- [ ] 磁碟寫不下時**清單本身仍要建立** —— 原件只是附加價值

### 6.80 v1.15.11 — 使用者上傳的 XML 與對外下載（**每次發版必過**）

- [ ] `bandit -r app -ll -iii -q` 回 **0**（Medium 以上、高信心）。
      **不可以把門檻調到 `-lll` 來讓它變綠** —— 第一次真的跑到這一步就掃出
      四處用 stdlib `ElementTree` 解析使用者上傳的 XML
- [ ] 解析上傳檔內部 XML 一律走 `defusedxml`；**`EntitiesForbidden` 要被接住**
      （它不是 `ParseError` 的子類，漏接會變成 500）
- [ ] 對外下載一律走 `app/core/safe_fetch.py`（只放行 http / https）——
      管理員可設定的鏡像若被設成 `file://` 不可以讀到本機檔案

### 6.78 v1.15.10 — 缺系統相依時的行為（**每次發版必過**）

> CI 的 runner 沒有 LibreOffice，抓到 Markdown 轉辦公文件回 **500**。
> 缺相依是**部署層面**的問題，不是使用者送錯東西 —— 500 會讓人以為服務
> 整個掛了而一直重試。

- [ ] 缺 Office 引擎時，工具端點回 **503**（不是 500），訊息說得出要裝什麼
- [ ] 驗法：`pytest -p no_sysdeps`（把 `find_soffice()` 關掉）跑
      `tests/test_broken_input_no_500.py`
- [ ] **需要 soffice 的測試一定要掛 `@_gate`** —— 缺相依要 **skip 不是 fail**
      （`test_every_soffice_dependent_test_is_gated` 用 AST 檢查）

### ⚠ 驗證環境要**連系統層一起對齊**

> 2026-09-06 我報過一次「乾淨環境 5306 passed」，但那個環境**只有 Python 層
> 乾淨**（跑在開發機上，有 LibreOffice / 字型 / node / zbar）。CI 是全裸的
> ubuntu-latest，於是整整一類問題（缺系統相依）完全沒被涵蓋到。

驗 CI 行為時三件事都要對齊，缺一件就會漏掉一整類：

| 層 | 怎麼對齊 |
|---|---|
| 專案樹結構 | 用**標準 clone**（沒有 `github/` 那一層） |
| Python 相依 | 用只裝 `requirements.txt` 的乾淨 venv（**版本會跟 uv.lock 不同，那是刻意的**） |
| **系統相依** | pytest plugin 同時關掉 `find_soffice()` **與 `best_cjk_path()`**；或用容器不裝 soffice / node / 字型 |

> **藏一半等於沒藏。** 只關 soffice 的話，「缺中日韓字型」那一類完全驗不到 ——
> 2026-09-06 就是這樣回報了一次假的「模擬全綠」，CI 照樣紅十條。

### 6.74 v1.15.9 — 路由表列舉要**跟得上框架版本**（每次發版必過）

> CI 第一次真跑就紅：`requirements.txt` 是 `starlette>=1.3.1,<2`，開發機被
> uv.lock 鎖在 **1.3.1**，CI 從範圍解析裝到 **1.6.0**。新版把 `include_router()`
> 的路由包進 `_IncludedRouter`（**沒有 `.path`**），`test_broken_input_no_500`
> 在收集階段就 `AttributeError` → pytest exit 2 → 兩分鐘內整個 job 紅。

- [ ] `python tools/route_index.py` 印出的路由數與上一版相近（目前 536 條）
- [ ] **逐路由參數化的守門都要先呼叫 `assert_sane(app)`**
      —— 新版底下頂層只看得到 **3 條** `/tools/` 路由，
      「只跳過沒有 `.path` 的物件」會讓那些守門**縮成三條然後全綠**
- [ ] 六個讀路由表的地方都走 `tools/route_index.py`：
      `test_broken_input_no_500` / `test_api_doc_coverage` / `test_test_plan_coverage` /
      `i18n_untranslated_scan` / `i18n_zh_baseline` / `report_endpoint_test_coverage`

> **開發機與 CI 裝的版本本來就不同**（uv.lock 鎖定 vs 範圍解析）——
> 這是**特性不是缺陷**：CI 裝最新版才會提早撞到框架升級的相容性問題。
> 所以修的是程式碼的版本強健度，**不是把版本釘死**。

### 6.75 v1.15.9 — 毀損 / 惡意的辦公文件（每次發版必過）

> 使用者把一份**被截斷的 docx**（36 KiB 整、沒有中央目錄）拉進逐句翻譯，
> soffice **回傳碼 0 卻不產檔**，畫面只丟一句自相矛盾的
> 「轉檔成功但找不到輸出 .txt」。

- [ ] `pytest tests/test_office_source_validation.py` 綠燈
- [ ] 拿一份**截斷的 docx**（把好檔案攔腰切一半）丟進逐句翻譯 / 文字去識別化，
      要看到「檔案不完整或已毀損…請重新取得或另存新檔」而**不是**開發者術語
- [ ] **好檔案一個都不能誤擋** —— 正常的 docx / xlsx / odt 照常轉
- [ ] 每個轉檔入口都先驗（`test_every_conversion_entry_point_validates_first`）

### 6.76 v1.15.9 — 上傳的防護（每次發版必過）

- [ ] **巨集**：轉檔用的拋棄式設定檔要寫入 `DisableMacrosExecution`
      —— `--safe-mode` 只是重設設定檔，**跟巨集無關**，很容易誤會
- [ ] **全域上傳上限**：超過設定值要回 **413**，且 body 不被讀取；
      管理頁「可上傳的檔案大小」可調、會寫稽核
- [ ] **顯示要與實際一致**：設了數字就顯示數字，設 0 就明講不限
      （`test_app_global_limit_is_reported_accurately`）
- [ ] **有寫在清單上不等於擋得住** —— 另有一條驗中介層真的存在
- [ ] zip 炸彈：解開後 > 1 GB 或壓縮比 > 200 的辦公文件要拒絕

### 6.77 v1.15.9 — 錯誤訊息不可以把原始回應丟給使用者（每次發版必過）

- [ ] 任何 `if (!r.ok)` 的分支都走 `window.friendlyServerError(r, …)`，
      **不可以** `await r.text()` 直接顯示（使用者截圖看到
      `Parsing failed: {"detail":"…"}` 這種原始 JSON）
- [ ] 後端只回**寫給使用者看**的訊息，開發者術語留在日誌

### 6.67 v1.15.8 — `tr` 被同名變數遮蔽（**每次發版必過**）

> **正式機上整支工具不能用**：使用者回報「乘車證明整理，我拉檔案進去都出錯」，
> 畫面是 `上傳錯誤：tr is not a function`。一次掃出 **16 處**，橫跨三支工具與
> 九個管理頁 —— 全是 i18n 包 `tr()` 那幾輪埋進去的。

- [ ] `pytest tests/test_no_tr_shadowing.py` 綠燈（掃全部樣板與 `static/js`）
- [ ] **實機拉一份檔案進「乘車證明整理」**，表格要真的畫出來
      （只看「有沒有 JS 例外」不夠 —— 這種遮蔽語法完全合法，`node --check` 是綠的）
- [ ] 書籤與目錄、字數統計、群組管理、歷史、作業、記錄轉發、系統狀態、
      使用者管理、同義詞、表單範本 —— 每一頁都要**做一次會畫表格的操作**
- [ ] 記錄轉發：**故意把 Host 清空按儲存**，要看到中文/英文的錯誤訊息而不是當掉
      （那條路徑原本是 `const tr = { … tr('Host 為必填') }`，**在自己的初始式裡
      呼叫自己**，暫時性死區直接 ReferenceError）

> **為什麼既有兩道防線都是綠的**：`test_template_js_syntax` 只驗語法（這種遮蔽
> 合法）；i18n 掃描器只看「有沒有包 `tr()`」，不看包進去的地方 `tr` 是不是別的東西。

### 6.68 v1.15.8 — 內建角色的說明（**每次發版必過**）

- [ ] `pytest tests/test_seed_bootstrap_gap.py::test_legacy_role_description_is_refreshed`
- [ ] 升級後開 `/admin/roles`，`finance` / `sales` / `legal-sec` 的說明要是**這一版的**
      —— 舊的那段**描述了角色其實沒有的權限**（把一般使用者本來就有的浮水印 /
      加密 / 去識別化寫成「另加」，legal-sec 更是把「少 29 個工具」寫成「＋」）
- [ ] **管理員改過的說明不可以被蓋掉** —— 手動改一個角色的說明再重啟，要還在

### 6.69 v1.15.8 — 資料 vs 顯示文字的界線（**每次發版必過**）

> 這一輪同一條界線踩了三次。判準一律是「**值還等於出廠預設才翻**」，
> 而且**翻譯後的值不可以流進編輯表單**。

- [ ] 英文介面下把某個內建角色改名為「會計部」→ 兩種語言都要顯示「會計部」
      （不可以變回 `Finance`），而且**再按一次儲存不會把它變成英文**
- [ ] SSO 登入按鈕：沒改過 → 英文介面顯示英文；管理員填過字 → 照他寫的顯示；
      **設定頁的輸入框永遠是原值**
- [ ] 用印限用章的字型下拉：`標楷體（自動找系統最佳）` 要翻，
      **使用者上傳的字型名稱不可以被翻**

### 6.70 v1.15.8 — 守門自己失效的四種樣態（**每次發版必過**）

> 這四種在 pytest 輸出裡**跟「全部通過」長得一模一樣**。

- [ ] **寫死 `github/` 那一層** → 一律走 `tools/repo_paths.py` 的 `public_root()`
      （判準是檔案在不在，不是資料夾名字）。用**標準 clone** 跑一次
      `pytest tests/`，不能只在開發機跑
- [ ] **逐類 / 逐檔參數化的守門要驗自己有收到檔案**
      （`test_taiwan_terminology::test_the_scan_actually_reaches_every_class_of_file`、
      `test_no_tr_shadowing::test_the_scan_actually_reads_something`）
- [ ] **跳過條件會不會因為環境而永遠成立** —— `test_pdf_watermark` 找的是
      **Pillow 內附**的 DejaVuSans，而 Pillow 12.3 起不再內附，那條守門一直在跳過
- [ ] **同步腳本要在最後一次編輯之後才跑**：`diff -rq` 對過 `sync-to-github.sh`
      的同步項目沒有差異才算數（v1.15.7 就是 19:51 同步、19:58 才改，
      那兩條守門根本沒進公開版，而 CHANGELOG 已經寫著修好了）

### 6.71 v1.15.8 — Windows 上跑完整測試（**每次發版必過**）

> 2026-09-06 **第一次**在 Windows 上跑完整套件：39 failed / 10 errors。
> **一條產品 bug 都沒有**，全是測試自己不跨平台 —— 但每一條都代表
> 那個守門在 Windows 上等於不存在。

- [ ] 在 `.154` 跑 `pytest tests/ -q`，失敗數不可增加
- [ ] 路徑當字串比對時一律 `.as_posix()`（`str(Path)` 在 Windows 給反斜線，
      豁免清單全用 `/` 寫 → 全部對不上 → 假陽性一大片）
- [ ] `read_text()` / `write_text()` 一律帶 `encoding="utf-8"`（Windows 預設 cp950）
- [ ] 暫存路徑用 `tempfile.gettempdir()`，**不可以寫死 `/tmp`**
      （`test_owasp_top10` 那條**路徑穿越**的資安守門就是這樣在 Windows 上從沒執行過）
- [ ] POSIX-only 的 API（`time.tzset` / `os.sched_setaffinity`）要 `hasattr` 防護

### 6.72 v1.15.8 — i18n 掃描器的兩個盲點（**每次發版必過**）

> 四支掃描器**全部回報 0**，而使用者一頁一頁截圖回報中文。

- [ ] **自訂函式的引數**（`setHint('全部：完整目錄樹')`）
- [ ] **template literal 裡的屬性值**（`placeholder="(自動產生)"`）
- [ ] **字串串接**組出來的（相依套件檢查進頁時的等待訊息）
- [ ] 掃描器改判準之後要重掃一輪，並記下當時的殘留數
      —— **「掃出 0 條」只證明「我掃到的那些是乾淨的」**

### 6.73 v1.15.8 — 版面（**每次發版必過**）

- [ ] 管理區設定頁的分區卡片**填滿到卡片右緣**，右邊不留空白條
      （`.auth-form` 原本夾在 `max-width: 760px`）
- [ ] 側欄的導覽名稱英文是 **Title Case**（`Synonyms` 不是 `synonyms`）——
      語系檔的鍵在句子中間用是小寫才對，所以是**渲染時**補首字大寫

---

---

## 6.9 i18n（介面語言）—— 分階段驗收

> **最高原則：加 i18n 不可以改壞現有功能。** 所以每一個階段的第一條驗收都是
> 「**繁體中文底下完全沒有變化**」，而不是「英文看起來對不對」。
> 本專案以繁體中文為主，英文是附加。

### 6.9.1 階段 1（v1.14.88）— 工具的語系白名單

- [ ] `tests/test_tool_ui_locales.py` 六項全綠。第一條
      `test_traditional_chinese_still_shows_every_tool` 就是最高原則的守門。
- [ ] 起乾淨實例抓首頁：**繁中側欄 47 支**，`vat-lookup` / `einvoice-scan` /
      `pdf-fill` / `pdf-stamp` / `doc-deident` / `transit-proof` 一支都不可以少。
- [ ] **反灰 ≠ 消失**（v1.14.93 起改成這樣）：語言不符的工具**仍然列在側欄與首頁**，
      只是反灰、點不下去、也不給釘選，滑鼠移上去說明得出為什麼。
      路由照常掛載、`/api/<tool-id>` 照常可用（有人可能介面用英文、手上卻正好
      有一份中文表單）。
- [ ] **權限不可以跟語言有關**：權限矩陣仍然列出全部工具；`roles.py` 裡不可以
      出現 `locale`。
- [ ] 語系值只能用共用常數（`TAIWAN_ONLY` / `CHINESE`）—— 每支工具各寫一份
      tuple 一定會漂。
- [ ] `check_docs_tool_coverage.py` 仍然通過（工具總數是**全部**，不因語言而變）。

### 6.9.2 階段 2（v1.14.89）— 語言切換與回退

- [ ] **語系檔是空的時候，切成英文畫面必須完全正常、全部回退繁體中文** ——
      不可以出現空白按鈕或 `nav.tools.title` 這種 key。這條比「英文對不對」更重要。
- [ ] 側欄與首頁磁磚**兩種語言都是 47 支**；英文底下有 **7 支反灰**（v1.15.2 起：印章那兩支已解除限制）、繁中 **0 支**
      （磁磚故意不過濾 —— 「我的作業」靠它畫工具圖示，隱藏工具的既有作業一樣要
      顯示得出來，這個雷 v1.14.21 踩過）。
- [ ] 語言判斷順序：**明確選過的 cookie > `Accept-Language` > 繁體中文**。
      `zh-CN` / `zh-Hans` **不可以**被對到繁體中文（硬對過去會讓簡中使用者
      看到繁中卻以為系統支援簡中）。
- [ ] `<html lang>` 要跟著語系走（螢幕閱讀器與瀏覽器的「翻譯此頁」都看它）。
- [ ] 切換用 **POST**（會改變狀態，不可以用 GET 連結），因此不需要 inline 事件
      處理器；`/ui-locale` 要在 `_PUBLIC_EXACT` 裡（登入頁也要能切）。
- [ ] `next` 只接受站內路徑（`//` 開頭要擋 —— open redirect）。
- [ ] 未啟用認證（單機模式）時也要能切換並記住。

### 6.9.3 階段 3（進行中）— 外殼字串英文化，**每一批都要跑這一組**

> 譯文的 key 就是**繁體中文原文**（gettext 的 msgid 做法）：查不到翻譯時
> 自動回退成中文，而且中文仍然留在樣板裡 —— `test_taiwan_terminology.py`
> 這類掃描器才不會變成永遠綠燈的假測試。

- [ ] **繁中零變化**：這一批改過的每一頁，用**前一版的樣板**與**這一版的樣板**
      各渲染一次繁中頁面，兩份 HTML **正規化 nonce / csrf 之後必須位元組相同**。
      （比逐像素比對更嚴也更便宜 —— 截圖會受捲軸寬度、字型微調、資料內容影響，
      HTML 不會。實際做法：把上一版的樣板從 `github/` 的公開版複製回來、起同一個
      拋棄式實例抓一次、換回新樣板再抓一次。）
- [ ] 英文截圖**逐張目視**：英文比中文寬約 1.7 倍，重點看側欄、按鈕、表格標題、
      工具卡片有沒有被擠爆或截斷（**版面跑掉自動化測試抓不到**）。
- [ ] 未翻字串清單：這一批的範圍內不可以有漏掉的字串。
- [ ] 領域資料**一個字都不可以進語系檔**（`tools/i18n_inventory.py` 的
      `DOMAIN_DATA`：表單欄位的中文標籤關鍵字、會計科目詞庫、去識別化的式子）。
      翻掉會讓表單自動填寫**安靜地抓不到欄位**。
- [ ] 帶變數與複數的句子要走參數，不可以用字串拼接（中文沒有複數、英文有）。

### 6.9.3b 階段 B（v1.14.96 起）— 工具內部畫面，**每一批都要跑這一組**

工具的內部頁面一批就會動到幾十個檔、幾百行樣板。**肉眼看不完，而且壞掉的樣子
通常是「畫面看起來正常」** —— 少一個空白、多一層跳脫、`<br>` 被跳脫成文字。

- [ ] **中文位元組零差異**：`python tools/i18n_zh_baseline.py --save` 改之前存，
      改完 `--compare` 要回「52 頁位元組相同」。**每包完一批就跑一次**，
      不要累積到最後 —— 差異一多就分不出是哪一步弄壞的。
- [ ] 語系檔的 key **一定是中文原文**（`test_catalog_entries_are_all_traditional_chinese_keys`）。
      抽 key 的正規式要有**前綴邊界** —— 沒有的話 Jinja 的 `selectattr('installed')`
      會被當成 `tr('installed')` 收進語系檔（v1.14.96 踩到，被這條守門擋下）。
- [ ] **含 `&` 的字串不要包**：`{{ tr('…') }}` 會經過自動跳脫，原本寫死的
      `&nbsp;` / `&amp;` 會變成看得見的字面。判準就是位元組比對會紅。
- [ ] `{% block title %}` 與 `{% with hint='…' %}` 這兩種**不是文字節點**的位置
      要另外一輪處理，否則頁面標題與上傳框的說明會留在中文。
- [ ] 英文頁面逐支目視：英文比中文寬約 1.7 倍，重點看按鈕、下拉、表格標題。

### 6.9.3c 階段 B（下）— **`<script>` 裡的字串**（v1.14.98 完成）

工具樣板的 `<script>` 裡有 **1,311 條**中文（按鈕文字、錯誤訊息、動態插入的
說明）。**不可以沿用樣板的 `tr()`** —— 那是伺服器端渲染時求值的，JS 執行期
拿不到。而且其中很多是 template literal（`` `已選：${file.name}` ``），
**變數內插之後 key 就對不上**，直接包會變成查不到翻譯、安靜回退成中文。

**v1.14.98 的做法**：前端一支同名的 `tr()`（`static/js/i18n.js`），字典由
`GET /i18n/<locale>.js` 提供；**只包不可能被當成值用的位置**（356 條），
三元運算與字串串接留著。驗收重點：
- [ ] 位元組比對要把 `<script>` 區塊排除（JS 原始碼本來就會變），
      改用 **CDP 驅動真實瀏覽器**驗行為（按鈕文字、錯誤訊息真的變英文）。
- [ ] 帶變數的句子一律走**參數化**（`tr('已選：{0}').replace(...)`），
      不可以把內插後的整句當 key。
- [ ] 繁體中文底下字典是**空的**（`tr()` 原樣回傳），確保零風險零成本。

### 6.9.4 之後幾個階段的驗收重點（先寫下來，做到再勾）

- [ ] **管理區**（1,919 條）：階段 A 完全不碰 —— 驗收是「切成英文時管理區仍然
      是繁中且完全正常」。
- [ ] **工具內部**（3,215 條）：逐支做，每支獨立驗收。
- [ ] **產出的文件**（頁碼「第 N 頁」、目錄頁標題、CSV 匯出欄位、通知信）：
      要先決定跟**介面語言**走還是跟**文件語言**走。稽核記錄尤其要注意 ——
      寫入時就翻好會讓歷史資料中英混雜，正確做法是存代碼、顯示時才翻。
- [ ] **文件**：`CHANGELOG.md` / `README.md` 維持繁體中文不動，另外增加
      `CHANGELOG_en.md` / `README_en.md`（等英文介面做得差不多再產出）。

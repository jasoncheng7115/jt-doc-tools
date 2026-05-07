# 更新記錄

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號採 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

---

## [1.4.96] - 2026-05-07

### 新增

- **使用者自訂釘選工具**：sidebar 每個工具卡片右上角加金色星星按鈕（hover 顯示），點下去即釘選 / 取消釘選；已釘選的工具會在 sidebar 最上方「釘選」群組鏡像出現，方便快速存取常用工具。資料存 `localStorage['jtdt:pinned']`，跨工具頁保留；目前 per-browser，未來可加 server-side 同步多裝置

---

## [1.4.95] - 2026-05-07

### 修正

- **補完 12 個工具的 `upload_owner.record()` 呼叫**：v1.4.83 安全強化漏掉的工具現在也會把 upload_id 歸屬到登入 user，admin 系統狀態頁的「目前佔用」欄位現在會反映**所有**工具的上傳活動。覆蓋：pdf-merge / pdf-split / pdf-rotate / pdf-pages / pdf-pageno / pdf-compress / pdf-extract-images / pdf-decrypt / pdf-encrypt / office-to-pdf / image-to-pdf / pdf-diff
- 真正純 text body 的工具（text-diff / text-deident /detect / translate-doc）沒有 upload_id 不必加；wordcount /analyze 是 transient 立即清，也不加。這幾個工具的 user 活動透過 audit middleware 仍會出現在「近 30 天」欄

---

## [1.4.94] - 2026-05-07

### 修正

- **`/admin/system-status` 使用者表只看得到 admin 的問題**：客戶反映用了好幾天，多 user 上傳，卻只看到 admin 的紀錄。三個原因 + 三個修法：
  1. **16 個工具沒呼叫 `upload_owner.record()`**（v1.4.83 只覆蓋 14 個）→ 那些工具的暫存檔沒 owner 歸屬。**修法**：表格新增「**近 30 天上傳次數 / 上傳量**」兩欄，從 `audit_events.tool_invoke` 事件還原（middleware 一律記錄 username + size_bytes，所有工具都涵蓋）。即使檔案已被 retention sweeper 清掉、或工具沒寫 owner record，仍能看到該 user 的真實活動量
  2. **owner sidecar 隨 temp file 一起 2 小時就清** → 過去資料無法從 disk 還原。**修法**：同上，audit 事件預設保留 90 天
  3. **匿名 / 未追蹤暫存檔不顯示** → admin 不知道沒歸屬的 disk 用量去哪。**修法**：新增「(未追蹤暫存)」row，匯總所有沒對應 owner sidecar 的 temp 檔
- 表格欄位重排為「目前檔案數 / 目前容量 / 近 30 天上傳次數 / 近 30 天上傳量 / 活動量視覺化」；長條圖以 30 天活動或目前佔用較大者為基準

### 改善

- `/admin/system-status` 拆成兩個 endpoint：`/host` 走 5 秒輪詢即時更新 CPU/RAM/IO，`/users` 拉檔案統計（cache 60 秒）非同步載入，避免 disk walk 阻塞首頁載入
- 使用者統計加 skeleton loading 狀態，刷新中顯示動畫

---

## [1.4.93] - 2026-05-07

### 改善

- **PDF 編輯器：選物件後 toolbar 折行的視覺問題**：選物件後右屬性面板出現會擠縮 toolbar 寬度，原本 zoom 控制會掉到下一行，視覺上很亂。改善：①autosave 的 verbose hint「（改動後 ~1 秒自動重算）」在 1450px 以下隱藏，hover 仍透過 title 屬性看完整文字；②按鈕 padding 從 6px 12px 縮成 5px 9px；③zoom slider 寬度從 120px 縮成 90px / 70px。視窗 1450px 以下 toolbar 仍能維持單行
- **`/admin/system-status` 使用者檔案表加入橫向長條圖 + 標題列點擊排序**：每個 user 一個 bar 顯示佔比（彩色 gradient，高佔比顯示 warn/crit 顏色），點選使用者 / 檔案數 / 容量標題切換排序方向（升降序）

---

## [1.4.92] - 2026-05-07

### 新增

- **新增 `/admin/system-status` 系統狀態頁**：CPU 使用率（含 load avg）、RAM + Swap、各分區 disk 容量、disk I/O 速率（read/write MB/s）、網路速率（bps）、本服務行程資源（PID / RSS / 執行緒 / CPU%）。下方表格列出**所有使用者的檔案數 + 容量**（含暫存上傳 owner ACL 紀錄 + 表單填寫 / 用印 / 浮水印歷史目錄）。每 5 秒自動刷新可關。新增 dependency `psutil>=5.9,<7`

### 修正

- **PDF 編輯器：點「選既有物件」選到「測試驗證」這類短中文片語，文字變成 OCR 結果「測試驗和 iia)」**：根因 — `_looks_garbled()` Signal b)「CJK ≥ 2 且 0 個 common chars 判 garbled」門檻太低，「測試驗證」4 字都不在 `_COMMON_TC` 集合裡 → 誤判 garbled → 觸發 OCR → OCR 在小 bbox 上吐出雜訊。修法：①Signal b) 門檻從 2 拉高到 8（真 Identity-H garbage 通常 10+ 字長）②`_COMMON_TC` 補入測 / 試 / 驗 / 證 / 收 / 評估等常見 business / test 字

---

## [1.4.91] - 2026-05-07

### 修正

- **PDF 編輯器：T 加文字框後改字型，左邊內容預覽不變**：根因 — `savePdf()` 一律 SKIP 當前選取物件（避免 bake 重複 layer 在 Fabric overlay 上），但對「字型變更」這種已經完成的編輯動作，這個 skip 反而讓使用者看不到效果。新增 `savePdf(isAuto, {includeActive: true})` 選項；font change handler（單選 + batch 多選）改用此選項，bake 包含 active obj，使用者立刻看到字型差別。selection 維持，可繼續編輯

---

## [1.4.90] - 2026-05-07

### 修正

- **PDF 編輯器：點「選既有物件」選到 TOC 目錄的 leader dots「........」，文字變成「eeeeeee...」**：根因 — TOC 的 leader dots 用 Identity-H subset font 儲存，glyph index 0x65 對應「.」glyph 但 ToUnicode CMap 把它 map 回 codepoint 0x65 = 'e'，PyMuPDF extract 出來變一堆「eeeee」。原本 `_looks_garbled()` 只偵測 CJK 範圍 + 符號雜亂，沒抓 ASCII 重複字串。新增 Signal c)：偵測 8+ 連續同字母 / 數字（real text 不可能 8 連碼），判定 garbled → 走 OCR fallback 拿回正確的點點。同時 `_replace_all_fonts_sync` 也預過濾 garbled span（不 redact 也不 re-insert，保持原始 layout）

---

## [1.4.89] - 2026-05-07

### 新增

- **Windows install.ps1 / jtdt update：自動補 chi_tra.traineddata**：UB-Mannheim winget silent install 預設不含繁中訓練檔，導致客戶 OCR 仍不能跑中文。新增 `Ensure-TesseractChiTra` (PowerShell) 與 `_ensure_tesseract_chi_tra()` (Python)：偵測缺 chi_tra 時直接從 `tessdata_fast` GitHub repo 下載 ~12MB 補進 `<install>/tessdata/`，不需重裝整個 Tesseract

### 改善

- **doc-deident 偵測類型選擇 UI 改成 text-deident 同款緊湊三欄卡片**：取代原本垂直 accordion + master checkbox 的版面，省空間 + 視覺一致；每張卡片右上 [全選 / 取消 / 反向] 三鍵取代原 master 三態（更直覺）。所有 checkbox 仍保留 `value=`/`data-group=` 維持向下相容
- 整列 hover 光棒（與 text-deident 同步），點擊判定範圍清楚

---

## [1.4.88] - 2026-05-07

### 修正

- **Windows Tesseract 安裝後仍顯示「缺」要手動加 PATH（GitHub issue #4）**：UB-Mannheim Tesseract 透過 winget 安裝有時不會自動加進 system PATH，造成 `Get-Command tesseract` 找不到 → install.ps1 顯示安裝失敗、`jtdt sys-deps` 報缺、pdf-editor OCR 不可用。客戶要手動加 `C:\Program Files\Tesseract-OCR` 到 PATH 然後重啟服務才行。三層修補：
  1. **app code**：`app/core/sys_deps.py` 新增 `_find_tesseract_binary()` + `configure_pytesseract()` 探測標準安裝路徑（`C:\Program Files\Tesseract-OCR\tesseract.exe` 等）並自動設 `pytesseract.tesseract_cmd`，**不需 PATH** 也能跑；服務啟動時自動呼叫，pdf-editor `_ocr_bbox()` 也防御性再呼叫一次
  2. **install.ps1**：新增 `Add-TesseractToPath`，winget 裝完或偵測到既有安裝時主動把 Tesseract 目錄補進 system PATH（重複呼叫 idempotent）
  3. **jtdt update**：`_print_system_deps_summary` 改用 `_resolve_tesseract_binary()`，不再單靠 PATH 判定缺
- TEST_PLAN §6.11.7 加入回歸測試清單

---

## [1.4.87] - 2026-05-07

### 改善

- **text-deident 卡片 5 倍數高度對齊取消**：v1.4.86 用 placeholder 補齊到 5/10/15/20，但 IT 資料卡的長 label 換行讓對齊邏輯失效，視覺反而更亂。改回原本「有幾項就幾行高」自然高度
- **資料類型每一列加 hover 整列光棒**：滑鼠移上去整列 label 變淺藍底（已勾選的 hover 變稍深），點擊判定範圍清楚，不用瞇眼對應 checkbox 與標籤

---

## [1.4.86] - 2026-05-07

### 新增

- **doc-deident / text-deident 共用偵測 catalog 增加 6 種敏感資料**：
  - 企業資料：**電子發票號碼**（AB-12345678 / AB12345678）、**訂單 / 採購單號**（PO/SO/INV/QT/WO/RMA/DO/DN/CN 前綴）
  - 其他：**車輛 VIN 碼**（17 字符 ISO 3779 格式）、**GPS 座標**（十進位 + DMS 兩種寫法）、**航班號**（2-3 字母 + 1-4 數字）、**訂位代號 PNR**（標籤式 6 字元英數）
- **LLM 補偵測 prompt 同步擴充**：新加類型也納入 prompt 提示清單（員工編號 / 部門代號 / 訂單號 / 合約號 / 發票相關 / 公司簡稱 / 行程物流），LLM 抓得到 regex 漏抓的非標準格式變體
- **text-deident 分類卡片高度對齊到 5 的倍數**：每張卡片自動補 placeholder label（visibility:hidden）讓底部對齊「5/10/15/20」樓層高度，視覺更整齊

### 改善

- **doc-deident / text-deident「LLM 補偵測」說明文案**：v1.4.84 寫成「預設 gemma4:26b」改成「經實測 gemma4:26b 效果與效能兼具，為目前推薦模型」（100% 命中、平均 11 秒），更明確標示推薦理由

### 修正

- **「文件差異比對」工具描述用詞**：「文字差異**標紅**」是中國 IT 圈簡稱，改回台灣全寫「文字差異**以紅色標示**」（v1.4.83 之前已改、回歸測試發現主程式仍掛舊版）

---

## [1.4.85] - 2026-05-07

### 改善

- **text-deident 資料類型分類版面**：v1.4.84 用 CSS multi-column 反而讓最右一欄空著、企業資料/其他底下大片空白。改成顯式 3 欄 grid + flex stack：col1 = 個人身分/聯絡方式/金融資訊、col2 = 企業資料/其他（與未列入分類）、col3 = IT 資料（獨佔最高欄）。視覺穩定可預測；窄螢幕降階為 2 欄（IT 跨整列）/ 1 欄

---

## [1.4.84] - 2026-05-07

### 改善

- **doc-deident / text-deident 兩個工具的「LLM 補偵測」說明改寫得更具說服力**：原本只列「context-sensitive 案例」幾個分類字，使用者看不出實際差別。改成具體舉例（人名 + 稱謂 / 職稱 / 自訂代號 / 口語化地址 / 公司機構）+ 強調本機跑（預設 gemma4:26b、4 份廠商表單 × 73 欄位 100% 命中、不出網路 / 不上雲 / 不留資料），把「為什麼要勾」講清楚
- **text-deident 資料類型分類版面重排**：原本用 grid `auto-fill minmax(220px, 1fr)`，IT 資料項目特別多，自己佔一整欄，旁邊一大片空白。改成 CSS multi-column（瀏覽器自動平衡欄高），個人身分 / 聯絡方式 / 金融資訊三張矮卡會自動堆疊在同一欄與 IT 資料齊高；視窗縮窄時自動降階為 3/2/1 欄

---

## [1.4.83] - 2026-05-07

### 安全（重要）

啟用認證後，原本任一已登入使用者只要拿到別人的 `upload_id`（網址、瀏覽歷史、伺服器 log、截圖外洩等管道）就能下載對方的 PDF / 預覽 PNG，且 `/preview/{name}` 系列端點對檔名沒做 path traversal 防護。本次集中修補：

- **新增 `app/core/safe_paths.py`**：strict allowlist `[A-Za-z0-9._\-]{1,255}`、拒絕 `..` / `/` / `\` / NUL；`safe_join()` 用 `relative_to()` 做 containment check 連 symlink escape 都擋；`require_uuid_hex()` 強制 32 字元小寫 hex 驗證 upload_id
- **新增 `app/core/upload_owner.py`**：每次上傳產 `upload_id` 時記一筆 sidecar JSON（`<temp>/.owners/<id>.json`）寫入 owner user_id；下載／預覽端點先 `require()` 比對當前 user。Auth OFF 時直通；admin（`effective_tools == ALL`）一律放行；missing owner record（legacy 或被掃掉）非 admin 一律拒
- **覆蓋 14 個工具的所有檔案存取端點**：pdf-fill / pdf-stamp / pdf-watermark / pdf-editor / pdf-attachments / pdf-metadata / pdf-hidden-scan / pdf-nup / pdf-to-image / doc-deident / pdf-extract-text / pdf-annotations / pdf-annotations-strip / pdf-annotations-flatten — 33 個 `/preview` `/download` `/file` `/baked-*` 端點全部加上 `safe_paths` 驗證 + `upload_owner.require()` ACL；upload-creating 端點同步 `upload_owner.record()`
- **新增安全 headers middleware**：`X-Content-Type-Options: nosniff` / `X-Frame-Options: SAMEORIGIN` / `Referrer-Policy: strict-origin-when-cross-origin` / `Permissions-Policy` 關閉 camera/mic/geolocation/interest-cohort；HTTPS 連線加 HSTS 6 個月（HTTP 不發避免內網 plain-HTTP 被鎖）
- **retention sweeper 擴充**：`.owners/` 目錄內的 sidecar 也按 TTL 清掉（避免 stale 記錄無限累積）
- **新增 34 個單元測試** `tests/test_safe_paths_and_owner.py` 覆蓋 path traversal 拒絕、symlink escape、UUID 驗證、ACL 各種組合（auth on/off、admin override、missing record、跨 user）

舊行為（無 ACL、無 path traversal 檢查）等於每位 user 都是 admin。客戶若已啟用認證且擔心歷史 upload_id 已外洩，請在升級後手動 `rm -rf data/temp/.owners` 清掉所有 owner 紀錄（後續上傳會重新建立 — 副作用：升級當下進行中的上傳對話會 403，重新整理頁面即可）

---

## [1.4.82] - 2026-05-05

### 修正

- **表單填寫沒在 admin/history/fill 留下記錄**：`pdf_fill/router.py:preview()` 與 `submit()` 內被標註「History persistence disabled」沒呼叫 `history_manager.save()`，admin 進歷史記錄頁永遠看到「尚無歷史記錄」。`history_refill` 路徑（已停用）才有 save。修正後兩個入口都會 best-effort 寫入 history（含 actor username 經 `sessions.user_label()`、template_id、company_id、報告 stats）

---

## [1.4.81] - 2026-05-05

### 改善

- **PDF 編輯器字型下拉支援動態高度**：原本寫死 `max-height: 360px`，下方視窗還有空間也撐不開。改成依 `getBoundingClientRect()` 動態算可用空間（上限 600px），下方夠就往下展，不夠才取上方空間
- **挑完字型立即 bake，不等 800ms debounce**：CSS generic 的 `serif`/`sans-serif` 在 Fabric 即時預覽看起來常常一樣（系統 fallback 不固定），真正的視覺差別在後端 bake 後 PNG 才看得到。改成 `pfFont` / `pfBatchFont` 的 change handler 直接呼叫 `doAutoSave()` 立刻 trigger bake（自動預覽關閉時尊重設定不觸發）

---

## [1.4.80] - 2026-05-05

### 修正

- **PDF 編輯器 toolbar 寬度夠時也被強制換行**：v1.4.79 改成兩個獨立 row 直接強制兩列，視窗寬時其實能擠進一條卻硬被分開。改回單一 `.pe-top` flex container（chip 樣式維持），`.pe-status` 用 `flex: 1 1 100%` 強制獨佔下一行 — 寬時所有按鈕一條，窄時自然 wrap

---

## [1.4.79] - 2026-05-05

### 新增

- **PDF 編輯器頂部 toolbar 改成兩段式固定排版**：原本所有按鈕（儲存 / 下載 / 復原 / 重做 / 整份換字型 / 設定 / 自動預覽 / 縮放 / 狀態）擠成一條 `flex-wrap: wrap`，視窗寬度一變整列就亂跳很難用。現在分成「動作按鈕列」+「設定 / 縮放 / 狀態列」兩段，自動預覽、縮放控制各自加 chip 樣式邊框；狀態文字單獨 100% 寬，內容多也不會把按鈕推到下一行
- **整份換字型可復原**：endpoint 在覆寫 src.pdf 前先備份到 `pe_{id}_src_pre_repl.pdf`；換完字型後狀態列旁出現「↶ 復原此次換字型」按鈕，點下去走新增的 `/undo-replace-all-fonts` 端點 restore backup + 重渲染預覽 + 重 bake out.pdf。每次換字型只保最近一份備份，避免無限疊加

---

## [1.4.78] - 2026-05-05

### 修正

- **整份換字型完成後忘記更新下載檔**：endpoint 只改 `src.pdf` + 重繪預覽 PNG，但 `out.pdf`（下載連結指向的檔）沒同步 — 使用者下載拿到的是換字型前的舊版。改完後前端自動觸發一次 `savePdf(true)` 把 overlay + 新 src bake 進 out.pdf，使用者直接按下載就拿得到新字型版
- **整份換字型遇到有底色的儲存格（表頭灰、總計橘等）會出現白底蓋掉**：`add_redact_annot` 預設 `fill=(1,1,1)` 白色覆蓋層，把底下原本的色塊一起蓋掉。改成 `fill=None` 不畫覆蓋層 — redact 只移除文字 content stream item，底色矩形原樣保留

---

## [1.4.77] - 2026-05-05

### 修正

- **PDF 編輯器：選 PyMuPDF Serif 後 bake 出來不是宋體（仍是 sans）**：根因是 PyMuPDF 內建 `china-t` / `china-ts` / `china-s` / `china-ss` 在 Linux host 上實際渲染**全部都是厚實 sans-serif**（與 PyMuPDF 文件所述 MingLiU / SimSun 不符），用 china-t 跟 china-ts bake 看起來一模一樣，使用者選 serif 跟 sans 看不出差別。修法：新增 `font_catalog.best_cjk_path(style, cjk)` 探測系統實際安裝的 CJK 字型（NotoSerif/SansCJK TC、Source Han Serif/Sans TC、TW-Sung、cwTeX-Ming/Yen 等），新增 `_upgrade_cjk_font()` 在 `save()` / `_resolve_fonts_for_pref()` 內把 china-* 升級為實際字型路徑（用 `page.insert_font` 註冊，per-page cache 不重複註冊）。如果 host 沒任何系統 CJK 字型才 fall back 到原 china-* 內建。Linux 客戶現在 PyMuPDF Serif → Noto Serif CJK TC，PyMuPDF Sans → Noto Sans CJK TC，視覺上才有差別

---

## [1.4.76] - 2026-05-05

### 修正

- **PDF 編輯器：「整份換字型」一按就 500 Internal Server Error**：`pdf_editor/router.py` 內 `_style_suffix()` 是 `save()` 的 nested function，閉包綁了 `bold`/`italic` 變數。但模組級的 `_insert_mixed_text()`（被 `_replace_all_fonts_sync` 透過 `_resolve_fonts_for_pref` 走到）也在用，跑到那條路徑時 `NameError: name '_style_suffix' is not defined`。把 `_style_suffix` hoist 到模組層級接受 `bold`/`italic` 顯式參數，所有 caller 同步補上 `, bold, italic`，並把 `_resolve_fonts_for_pref` 內重複定義的 `_style` 也清掉

---

## [1.4.75] - 2026-05-05

### 修正

- **PDF 編輯器：選自訂上傳字型畫面預覽對的、自動儲存後重畫卻跑掉變回 PyMuPDF 預設**：`pdf_editor/router.py:save()` 內 text 物件渲染只判斷 `font_pref.startswith("system:")`，漏了 `"custom:"` 分支，導致 admin 上傳的字型（如「微軟正黑體」）在後端 bake 時被 fall through 到 `china-t` built-in。同時補 `fontname.startswith("uc")` 進不分割 ASCII/CJK runs 的判斷
- **字型分類標題背景太淺看不出區隔**：把分類 band 從 `#f1f5f9`（淺灰）換成 `#dbeafe`（藍 100）+ 左側 4px `#2563eb` accent bar + 上下 `#93c5fd` 邊線，文字色改 `#1e40af` 加粗，與下方字型項目視覺差距明顯

---

## [1.4.74] - 2026-05-05

### 新增

- **PDF 編輯器字型下拉，分類標題加整條淺灰背景帶**：「自訂上傳字型」/「PyMuPDF 內建」等分類標題視覺上與字型項目清楚分區，捲動時 sticky 定位讓使用者隨時知道目前在哪個分類

---

## [1.4.73] - 2026-05-05

### 修正

- **PDF 編輯器字型下拉清單仍被右屬性面板裁切**：v1.4.72 用 `position:absolute` + `right:0` 翻邊只是位移，沒解決根因 — 右屬性面板有 `overflow:hidden`，無論往左或往右展超出 trigger 範圍都會被切。改成 `position:fixed` + 由 JS 用 `getBoundingClientRect()` 即時計算座標，popup 完全脫離祖先 overflow。並加入：①下方空間不夠時自動往上開、②兩邊都不夠時夾在 viewport 邊界內、③`scroll`/`resize` 即時 reposition

---

## [1.4.72] - 2026-05-05

### 修正

- **PDF 編輯器加入文字時，自訂上傳的字型不出現在下拉選單**：`pdf_editor/router.py:list_fonts()` 的分類白名單少寫 `"custom"`，導致 admin 上傳的公司字型（例如客戶上傳的「微軟正黑體」）在 catalog 看得到、編輯器卻選不到。修正後 `custom` 分類排在最上方優先顯示
- **字型下拉清單寬度不夠，長字型名稱被截掉看不到全貌**：右屬性面板窄、預設清單跟著 trigger 同寬導致 `PyMuPDF 內建 Sans（繁中黑體 + Helvetica）` 等長 label 被擠成 `PyMuPDF 內建 Sans (...)`。改成 `width:max-content; min-width:100%; max-width:360px` 讓清單依內容自動撐開；JS 量到右邊會超出 viewport 時自動翻邊改成右對齊（往左展），畫面無論在哪個寬度都看得完整

---

## [1.4.71] - 2026-05-05

### 新增

- **字型管理：上傳區美化（拖曳區 + 多選 + 即時檔名）**：與企業 Logo 上傳區同調性，虛線方框、滑入上浮陰影、選好檔變綠色，支援把多個 .ttf / .otf / .ttc 直接拖進來，已選清單即時顯示
- **字型管理：每個分類獨立「全部顯示 / 全部隱藏」按鈕（眼睛 icon）**：例如想一鍵把 PyMuPDF 內建全藏，或把整個西文開源分類關掉，不需逐一點。新增 `POST /admin/fonts/bulk-hidden` 端點
- 圖示庫加入 `eye-off`（給隱藏狀態用）

### 修正

- **Linux host 沒啟用 UTF-8 locale 時，上傳中文檔名字型 / 處理中文檔名 PDF 會炸**：systemd unit `jt-doc-tools.service` 加上 `LANG=C.UTF-8` / `LC_ALL=C.UTF-8` / `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1` 四個 Environment（C.UTF-8 是 glibc 內建，不需另裝 zh_TW locale）
- `jtdt update` 現在會自動補全舊安裝（< v1.4.71）少掉的 locale Environment 行，idempotent，已存在不重加
- 字型上傳端點加上 ascii filesystem fallback：若偵測到 host 的 `sys.getfilesystemencoding()` 不是 UTF-8 且檔名含 CJK，自動改用 sha256[:16] 當檔名避免 `UnicodeEncodeError`

---

## [1.4.70] - 2026-05-05

### 修正

- **首頁 hero 標題沒跟著站台名稱改變**：`web/router.py` 的 `index()` 把 `app_name=...` (boot-time cached 字串) 當 TemplateResponse context 傳入，會 override Jinja global 的動態值。改成不傳，讓 home.html 直接走 Jinja global → 動態 `branding.get_site_name()` 即時生效

---

## [1.4.69] - 2026-05-05

### 修正

- **`/admin/branding` 開頁 500 Internal Server Error**：v1.4.68 加站台名稱欄位時用了 `settings.app_name`，但 `app/admin/router.py` 沒 import `settings`。改用本地 helper `_br_default_app_name()` 從 `..config` 讀

---

## [1.4.68] - 2026-05-05

### 新增

- **企業 Logo / 識別頁加入「站台名稱」自訂**：可把預設的「Jason Tools 文件工具箱」改成自家品牌（例如「某某公司文件工具箱」）。新名稱即時套用到 sidebar 上方、瀏覽器分頁標題、首頁 hero、登入頁，不需重啟服務。儲存於 `data/branding/site_name.txt`，最長 60 字
- 後端 `core/branding.py:get_site_name() / set_site_name()` + `POST /admin/branding/site-name` API
- Jinja `app_name` global 改成 lazy 動態讀取（不再 boot-time cache）

### 改善

- **企業 Logo 上傳區改成漂亮的拖曳區**：之前只是裸 `<input type="file">`；改成圓 icon + 標題 + 說明的 dashed drop-zone，hover 浮起 + 配色，支援拖檔到區內

---

## [1.4.67] - 2026-05-05

### 修正

- **README + THIRD-PARTY-NOTICES + packaging/README 還寫 NSSM**：v1.4.44 起改用 WinSW 但這 3 個文件沒同步，使用者誤以為服務還是用 NSSM。一併更新：
  - README 安裝位置表：Windows 服務 `(NSSM)` → `(WinSW)`，macOS 從 LaunchDaemon 改成正確的 `.app + LaunchServices` 描述、data 路徑改回 `~/Library/...`（per-user 不是 system-wide）
  - THIRD-PARTY-NOTICES 加入 WinSW 條目；NSSM 條目改為「已棄用，保留供舊安裝偵測用」
  - packaging/README.md 更新成 WinSW 路徑

---

## [1.4.66] - 2026-05-05

### 改善

- **LDAP/AD 帳號的密碼說明文字精簡**：「密碼由 LDAP 目錄端管理，請聯絡 IT 修改。」→「密碼由 LDAP 目錄端管理。」— 後半句多餘

---

## [1.4.65] - 2026-05-05

### 安全強化

- **`/change-password` 加 rate limit + audit failed attempts**：v1.4.64 雖然 user_id 一律從 server-side session lookup（不從 body）防止改別人密碼，但**session 被偷時**舊密碼仍可暴力試。這版加：
  - 失敗 5 次（10 分內）→ 該 user 鎖 10 分鐘 (HTTP 429)
  - 每次失敗寫 audit `event_type=password_change_fail` (含 reason / fail_count)
  - 鎖定後寫 audit `event_type=password_change_lockout`
  - 成功後 audit `event_type=password_change`，username 統一用 `user_label()` 格式 (`jason@local` / `jason@ldap`)
- 端點安全清單（內部稽查確認 ✓）：
  - `user_id` from session lookup ONLY，body 不接受 → 不可能改別人密碼
  - SameSite=Lax cookie 擋跨站 CSRF
  - `verify_password()` 是 constant-time argon2 比對
  - LDAP / AD 帳號明確 reject（不能在這裡改目錄端密碼）
  - 新密碼 8-128 字元、不能與舊密碼相同、不能全空白
  - 變更後 revoke 其他 session 但保留呼叫方 session

---

## [1.4.64] - 2026-05-05

### 新增

- **本機帳號自助變更密碼**：「我的帳號」對話框內 source=local 的使用者多一顆「變更密碼」按鈕；輸入舊密碼 + 新密碼 + 確認新密碼 → POST `/change-password` 驗證舊密碼後更新；其他裝置 / 瀏覽器的 session 全被登出，目前這個視窗保留。LDAP / AD 使用者顯示「密碼由目錄端管理，請聯絡 IT」說明
- 後端 `core/user_manager.py:change_password()` — 驗證舊密碼 (constant-time) → 強度檢查 → 更新 hash → revoke 其他 session（保留呼叫方 session）
- 稽核記錄 `event_type=password_change`

---

## [1.4.63] - 2026-05-05

### 新增

- **`text-deident` / `doc-deident` 加 3 個 IT 資料 pattern**：
  - **SSH 公鑰** — `ssh-rsa` / `ssh-ed25519` / `ecdsa-sha2-*` 等開頭，連帶 base64 主體 + comment
  - **PEM 區塊** — `-----BEGIN CERTIFICATE-----` / `BEGIN OPENSSH PRIVATE KEY` / `BEGIN RSA PRIVATE KEY` ... `-----END...-----` 整段抓
  - **Hash 雜湊值** — MD5 (32) / SHA-1 (40) / SHA-224 (56) / SHA-256 (64) / SHA-384 (96) / SHA-512 (128) hex 值 + bcrypt `$2[aby]$...`
  - 都歸 IT 資料分類、預設關（避免一般文件誤抓 hex），使用者貼 logs / 設定檔 / git diff 給 AI 前先勾選去識別化
- **「我的帳號」對話框排版重做**：原本只是表格 label/value 列；改成大頭像（依 source 配色：LDAP 藍 / AD 紫 / 本機綠 / 單機灰）+ 顯示名 + username@source pill + 角色 badges + 工具網格（admin 顯示綠色「全部工具」橫條，一般使用者顯示工具卡片格）

---

## [1.4.62] - 2026-05-05

### 修正

- **README + docs/index.html 移除「AES 加密壓縮檔」**：實際 `aes-zip` tool 在 metadata 是 `enabled=False`（暫時下架），文件還寫著會誤導使用者去找
- 用戶之前在 docs 看不到「文字去識別化」是 GitHub Pages 邊緣 cache 還沒更新；live HTML 確認已含此項

---

## [1.4.61] - 2026-05-05

### 改善

- **「豆腐」改回台灣用語「方框」/「缺字方框」**：描述字型缺字回退 .notdef glyph 的「豆腐」/「豆腐方框」是大陸 / 港澳俚語，台灣不這樣講。`pdf-watermark/service.py` 註解 + sys-deps 描述改成「空白方框 (缺字)」/「缺字方框」

---

## [1.4.60] - 2026-05-05

### 改善

- **`text-deident` 分組工具按鈕改用直觀的 SVG icon**：
  - 全選 = ☑ 勾選方框
  - 取消 = ☐ 空方框
  - 反向 = 半實半空方框（左實右空）
  - 比之前的 plus / reset / refresh 一眼看得懂
- **「帳號/密碼斜線對」改名「帳號/密碼斜線組合」**：「對」字偏 stiff，「組合」更符合中文使用慣例

---

## [1.4.59] - 2026-05-05

### 修正

- **`text-deident` 分組標題被擠到斷行**：v1.4.58 加大「全選 / 取消 / 反向」按鈕後，220px 寬的卡片裝不下「個人身分 + 三顆按鈕」，標題垂直排成「個 人 身 分」。改成 icon-only 24×24px 方鈕（保留 title tooltip），h4 加 `white-space:nowrap`，標題完整單行顯示

---

## [1.4.58] - 2026-05-05

### 改善

- **`text-deident` 偵測完自動套用一次**：使用者按「偵測敏感資料」/「重新偵測」後不用再手動點「套用至全部」，下方處理結果直接出現。要排除某筆 / 切 mode 重套，再手按「套用至全部」即可
- **`text-deident` 分組「全選 / 取消 / 反向」按鈕加大 + 加 icon**：之前太小看不清，改成 11.5px + 圖示，hover 全選變綠 / 取消變紅 / 反向變藍

---

## [1.4.57] - 2026-05-05

### 修正

- **`text-deident` 偵測結果列欄寬不對齊**：v1.4.56 用 `minmax(140px, max-content)` 讓每行 type 欄寬獨立計算 → 不同行 orig / repl 對不齊。改用固定 200px + `overflow-wrap: anywhere`，所有行共用同寬 type 欄
- **README + docs 加入「文字去識別化」工具描述**：之前漏更新；docs/index.html 資安處理 card 補上 `<li>` 條目，README hero 段早已含

---

## [1.4.56] - 2026-05-05

### 修正

- **`text-deident` 偵測結果列 type 欄太短**：「帳號/密碼斜線對 (admin/pass)」之類較長的 type label 被擠到下一行。grid 欄位改成 `28px / minmax(140px, max-content) / 1fr / 1fr` — type 自動撐到內容寬，不再折行

---

## [1.4.55] - 2026-05-05

### 改善

- **`text-deident` / `doc-deident` IP 位址歸到「IT 資料」分類**：之前在「其他」與人名 / 車牌混在一起，IT context 不直覺；移到 IT 資料 跟 hostname / MAC / URL 等同組

---

## [1.4.54] - 2026-05-05

### 新增

- **`text-deident` / `doc-deident` 偵測「帳號 / 密碼」**：客戶 log 範例 `admin / qazwsxedc` 之前抓不到。新增兩個 IT 資料 pattern：
  - `cred_label` — 標籤式 `password: xxx` / `密碼: yyy` / `api_key=zzz`
  - `cred_pair` — 斜線對 `admin/pass` / `user / password`（兩側必須像帳號密碼，避免吃到 URL path 或日期）

### 改善

- **`text-deident` 操作流程重整**：套用 / 下載 / 複製按鈕從「處理方式」搬到新「5. 套用 / 輸出」步驟，跟「3. 處理方式」職責切清楚。處理後文字往下移為「6. 處理後文字」
- **`pdf-rotate` 套用範圍 layout 修正**：之前選「自填頁碼」會把計數「N 頁將被套用」擠到第二行，現在計數固定獨立一行，左 70px 內縮對齊輸入框
- **`pdf-rotate` 轉向按鈕重新設計**：原本扁平單色按鈕；改成 gradient 背景 + 圓形 icon-wrap + per-mode hover 配色（旋轉藍 / 鏡像紫 / 清除紅）+ hover lift 動畫

---

## [1.4.53] - 2026-05-05

### 重大變更

- **頁面轉向工具大改 UX**：
  - 上傳後**自動顯示縮圖**（不再需要按「開始」）
  - 縮圖區上方加 toolbar：6 個轉向按鈕（90 / 180 / 270 / 左右 / 上下 / 清除）+ 套用範圍下拉（**所有頁 / 偶數頁 / 奇數頁 / 自填頁碼**，例 `1,3-5,8-10`）
  - 每張縮圖下方有獨立轉向小按鈕，可單頁覆寫
  - 完成區三個按鈕：**下載 PDF / 下載 ZIP（每頁 PNG，150 DPI）/ 處理新檔案**
  - 後端新增 `/finalize` (PDF 輸出) 跟 `/finalize-png` (PNG ZIP) 同步 endpoint，從 `/load` 暫存的檔案直接出結果，不再走 job manager

### 新增

- **文字去識別化每個分組加 icon**：個人身分 / 聯絡方式 / 金融資訊 / 企業資料 / 其他 / IT 資料 各自配色 icon

### 改善

- **macOS install.sh 直接 root 登入時自動偵測 GUI 桌面 user**：之前直接 root 跑會 die「不能用 root」；現在用 `stat /dev/console` 抓出登入桌面的 user 當 .app 擁有者，並 warn 建議下次改用 sudo
- **文字去識別化 UI 文字 / 樣式**：
  - 替換假資料副標改「置換成擬造資訊」（更精準）
  - 「自訂 regex」前面拿掉手寫 `▸`（跟原生 `<details>` triangle 重複）
  - 整段塗黑副標精簡掉「不可還原」（看圖示就懂）

---

## [1.4.52] - 2026-05-05

### 改善

- **文字去識別化「編修」說明簡化**：「整段塗黑 · 不可還原」改為「整段塗黑」 — 直接看圖示就懂，後半句多餘

---

## [1.4.51] - 2026-05-05

### 改善

- **使用者顯示名稱統一加上認證領域 (`username@realm`)**：歷史記錄 / 稽核記錄 / 其他「使用者」欄位之前只顯示 `jason`，現在會顯示 `jason@local` 或 `jason@ldap`。多領域同名（PVE 風格）情境下才能分清是誰
- **新增 `sessions.user_label()` 共用 helper**：處理 dict / object 兩種 session user 結構，集中格式化邏輯，避免每個工具重新拼字串
- **新工具會自動加入適合的預設角色**：之前加新 tool 後，已存在客戶的 `default-user` / `clerk` / `finance` / `sales` / `legal-sec` 內部 role row 不會更新，新 tool 沒人看得到。`seed_builtin_roles` 改成 startup 時 top-up（只 ADD 不 REMOVE，admin 自訂的 grants 不會被洗掉）
- **文字去識別化處理方式按鈕重新設計**：之前像三顆普通 .btn，改成 segmented card（icon + 標題 + 副標、per-mode 配色：藍 / 黑 / 橘、active 帶外光暈），更直覺看出三個模式是「擇一」

### 修正

- **`pdf-watermark` `submit` handler 第二處 actor 取值仍是壞掉的舊 getattr-on-dict pattern**：v1.4.50 sed 替換時漏掉一處且結尾縮排破掉。改用集中 helper 一勞永逸

---

## [1.4.50] - 2026-05-05

### 修正

- **LDAP 使用者操作的 history 記錄仍顯示「(匿名)」** — v1.4.43 沒修對：
  - v1.4.43 把 `actor = getattr(getattr(request.state, "user", None), "username", "") or ""` 留著當「修正版」，但 `request.state.user` **是 dict 不是 object**（`sessions.lookup()` 回 dict），`getattr(dict, "username", "")` 永遠回 `""` → 永遠匿名
  - 修：`_u = getattr(request.state, "user", None); actor = (_u.get("username") if isinstance(_u, dict) else getattr(_u, "username", "")) or ""` — 同時相容 dict 與 object
  - 修了三個 stamp + 兩個 watermark 共 5 處
- **歷史記錄的「表單填寫 / 用印簽名 / 浮水印」切換改為 tab 樣式**：之前用 `.btn` 看起來像三顆獨立按鈕，現在改成下劃底線分頁、active 有藍底，視覺更清楚是「分頁切換」

### 文件

- README + docs/index.html 工具數從 29 → 30（含 text-deident 文字去識別化）

---

## [1.4.49] - 2026-05-05

### 新增

- **新工具：文字去識別化（text-deident）**：貼文字 / 上傳 .txt .md .docx .doc .odt .pdf 等檔案，偵測敏感資料後可選 **遮罩**（王*明）/ **編修**（█）/ **替換假資料**（產生新假姓名 / 假號碼，保留格式）。流程同 doc-deident 但走純文字（不需 PDF coord 處理），結果可下載成 .txt / 複製到剪貼簿
- **新增「IT 資料」類別偵測**（給 log / 設定檔 / debug 訊息貼到 AI 前先去識別化用）：
  - 主機名稱 (FQDN，內網 TLD 慣例)
  - MAC 位址
  - AD / LDAP DN（CN= / OU= / DC= …）
  - Windows 帳號（DOMAIN\\user）
  - UUID / GUID
  - 內網 URL / 任意 URL（含公開域名）
  - 域名 / FQDN（含公開域名）
  - 本機路徑（含使用者名，例如 /home/jcheng/、C:\\Users\\admin\\）
  - API token / 金鑰（mixed-case 高熵 ≥ 32 字 + 已知 prefix 如 sk-、ghp_、AIza、AKIA …）
  - 全部 default off — 一般商務文件容易誤抓，使用者自選開啟
- **`text-deident` 加入 LLM 補偵測**：跟 doc-deident 同型，可勾選「LLM 補偵測（找 regex 漏掉的）」抓人名 / 職稱 / 客戶代號等 context-sensitive 案例
- **每個偵測分組卡片加入「全選 / 取消 / 反向」按鈕**：之前每組要一個個勾，現在每張卡片右上角有微型 toolbar
- **處理後文字搬到最下方專屬 panel**：之前左右並排佔版面，按下「套用至全部」後新 panel 出現並自動捲動到視野中

### 修正

- **`api_token` regex 誤抓 UUID**：之前用 `re.IGNORECASE`，導致「混大小寫」lookahead 對全 lowercase UUID 也成立。移除 IGNORECASE，prefix-based 匹配保留 case-sensitive

---

## [1.4.48] - 2026-05-05

### 改善

- **`jtdt update` 結尾自動 self-bootstrap 系統依賴檢查**：之前修正 update flow 內的 helper（如 `_migrate_nssm_to_winsw`、新加的 `_ensure_*` probe）只有在「下次再跑 update」時才生效（CLAUDE.md `feedback_jtdt_update_self_bootstrap.md`）。現在每次升級結尾用 venv 的 fresh Python 子行程跑 `_ensure_system_deps_for_update`，新加的依賴/移轉邏輯立即吃到。Idempotent，已是 WinSW 的安裝直接 short-circuit
- **Win11 完整端到端驗證**：v1.4.26 NSSM 安裝 → 移轉到 v1.4.47 WinSW → uninstall → 全新 install.ps1 → v1.4.47 WinSW 三條路徑都通過

---

## [1.4.47] - 2026-05-05

### 修正

- **install.ps1 `Write-WinswXml` 參數 `$Host` 撞到 PS 自動變數**：PowerShell 的 `$Host` 是 read-only built-in，當作參數會印「無法覆寫 Host 變數」。改名 `$BindHost`

---

## [1.4.46] - 2026-05-05

### 修正

- **NSSM→WinSW 移轉後 svc_start 印 1056 錯誤訊息**：移轉腳本本身已啟動服務，update flow 結尾再 `sc.exe start` 會收到 1056「服務已執行中」誤判失敗。Win 平台 svc_start 現在把 1056 視為成功
- **移轉時 nssm.exe 因被 SCM 鎖住無法刪**：之前印 ugly 「[WinError 5] 存取被拒」。改為呼叫 `MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)` 排程下次重開機自動清掉，訊息溫和「nssm.exe still in use; queued for removal on next reboot」

---

## [1.4.45] - 2026-05-05

### 修正

- **NSSM→WinSW 移轉腳本呼叫 `_run_capture` 多傳了 `timeout` 參數**：v1.4.44 引入時誤以為 helper 接受 timeout，實際它只接 cmd list。Win11 測試機第二次 jtdt update 時遇到 `_run_capture() got an unexpected keyword argument 'timeout'` warning，移轉沒跑成功
- 修法：移除三處 `timeout=5`；sc.exe 本來就秒回不會卡

---

## [1.4.44] - 2026-05-05

### 重大變更

- **Windows 服務 wrapper 從 NSSM 換成 WinSW**：
  - 新安裝走 WinSW（v2.12.0、MIT、GitHub Release 託管、Jenkins 等大型專案在用）
  - **舊客戶 `jtdt update` 自動移轉**：偵測到 NSSM-wrapped service 時自動讀取 registry 中的 JTDT_HOST / JTDT_PORT / JTDT_DATA_DIR、停止舊服務、解除安裝、改用 WinSW 重新註冊（service name `jt-doc-tools` 不變，所有 sc.exe / 監控整合繼續運作）
  - 移除 NSSM 的理由：2014 後無更新、nssm.cc 不時 503/404 (issues #1, #3)、AV PUA 誤判頻繁
  - WinSW 配置由 `bin/jtdt-svc.xml` 管理；`jtdt bind` 直接改 XML 後重啟服務
- **install.ps1 加入 UTF-8 BOM**：Win11 PowerShell 5.1 預設用 CP950 解碼無 BOM 檔，含中文的腳本因此 parse 失敗（issues #1 / #3 真正根因）。加 BOM 後 `ParseFile` 通過 SYNTAX OK；客戶 v1.4.43 升級時就自動拉到正確版本

### 內部
- `app/cli.py:_migrate_nssm_to_winsw` — 完整 NSSM→WinSW 移轉邏輯，含 env var preserve / WinSW SHA256 驗證 / fallback 從 GitHub Release 下載
- `app/cli.py:_write_winsw_xml` — XML 安全產生器（escape、UTF-8 寫入）
- `app/cli.py:svc_bind` 在 Windows 上現在直接改 WinSW XML 重啟服務，不再印「請手動跑 nssm」

---

## [1.4.43] - 2026-05-04

### 修正

- **Windows install.ps1 在 `Install-Nssm` 函式炸掉「陳述式區塊或類型定義中缺少 '}'」**（GitHub issues [#1](https://github.com/jasoncheng7115/jt-doc-tools/issues/1) / [#3](https://github.com/jasoncheng7115/jt-doc-tools/issues/3)）：
  - 根因：`Install-Nssm` 內含中文的 here-string `@" ... "@`，部分 PowerShell 版本 / 編碼把 `"@` 誤判為字串內容而非結尾，整支 script 後面所有 `}` 都被當成字串 → parser 找不到函式的閉合 `}`
  - 修法：改用普通雙引號字串 + ``n` 換行串接，避開 here-string 跟非 ASCII 字元的相容性陷阱（@chihhao0312 in issue #1 提供的修法）
- **用印與簽名歷史記錄全顯示「(匿名)」（即使已登入 LDAP）**（客戶 v1.3.14 回報）：
  - 根因：`pdf_stamp` 背景 job 的 `stamp_history.save()` 用 `getattr(getattr(job, "_actor", None), "username", "")` 取使用者，但 job 物件根本沒 `_actor` 屬性 → 永遠拿到 `""` → 顯示匿名
  - 修法：在 route handler 開頭就把 actor username 抓進 closure，傳給 `stamp_history.save(username=actor)`
- **浮水印歷史記錄同樣全匿名**（同根因）：
  - `pdf_watermark` 的 `watermark_history.save()` 之前直接 hardcode `username=""`，且 actor 只在 asset-mode 路徑捕獲；text-mode 完全沒抓
  - 修法：route handler 一開頭就無條件抓 actor，傳進 closure

---

## [1.4.42] - 2026-05-04

### 改善

- **文件去識別化「LLM 補偵測」說明文案台灣化**：人名（含「先生 / 經理」前綴）→「等稱謂」，「前綴」這個用法在台灣比較硬，「稱謂」更貼近自然中文。LLM prompt 內的同樣字串一併改
- **左側搜尋列範例文字加入中文示範**：之前只有英文「(form fill, stamp…)」，使用者看不出來能用中文搜尋。改成「(例：填表 / form fill、用印 / stamp)」

---

## [1.4.41] - 2026-05-04

### 新增

- **角色管理：每個角色 (除 admin 外) 多了「複製」按鈕**：之前 hint 文案說「需要時複製預設角色再客製」但根本沒按鈕。現在按複製會跳輸入框，輸入新 id 後即建立同樣權限的副本，display name 自動加「（副本）」、description 註明複製來源

### 修正

- **權限矩陣「清除」按鈕比同列其他按鈕小**：之前 CSS 加 `.picker-clear { padding:3px 10px !important; font-size:11px !important; }` override 了 `.btn-small` 的尺寸；移除 override 讓四個按鈕（全選 / 取消全選 / 反向選取 / 清除）視覺統一
- **註解平面化頁面 AcroForm 提示框與下方上傳區無間隔**：之前 `.af-info` 只設 margin-top；改成 `margin: 10px 0 18px` 讓提示與下方 panel 有合理留白

---

## [1.4.40] - 2026-05-04

### 新增

- **OxOffice / LibreOffice 執行時依賴一次裝齊**（客戶 v1.4.39 回報「javaldx: Could not find a Java Runtime Environment! / libX11-xcb.so.1: cannot open shared object file」）：
  - 加入 `libx11-xcb1`、`libxcomposite1`、`libxdamage1`、`libxfixes3`、`libxkbcommon0`、`libfontconfig1`、`libfreetype6`、`libcairo2`、`libpango-1.0-0`、`libpangocairo-1.0-0`、`libgdk-pixbuf-2.0-0`、`libnss3` 到自動安裝清單
  - 新增 Java JRE (`default-jre-headless` / RHEL `java-21-openjdk-headless`) 自動安裝
  - install.sh + jtdt update + sys_deps probe 三處同步維護同一份清單；客戶不用再一個一個補
- **`_probe_java_runtime` 系統依賴探針**：admin 系統依賴頁可看到 Java JRE 安裝狀態與版本

### 修正

- **頁面轉向：選擇全頁套用方式後個別頁殘留覆寫導致只有第一頁變更**：
  - 之前 per-page override 永遠優先於全頁設定，使用者切「左右鏡向」後若第 2 頁有舊的 180° 個別覆寫，第 2 頁不會跟著變
  - 改成：切「套用方式」或「套用頁面」時自動清掉所有 per-page override，全頁設定真正生效。要再個別調整可再點縮圖工具列

---

## [1.4.39] - 2026-05-04

### 改善

- **逐句翻譯譯文字數即時更新**：之前 header 的「譯文（繁體中文） 0 字」要等全部翻完才會跳到實際數字 — 翻譯到 90% 還顯示 0 字體感很怪。改成每完成一句就重新計算譯文總字數並 patch header，跟左邊原文字數一樣即時

---

## [1.4.38] - 2026-05-04

### 改善

- **逐句翻譯譯文欄右側也可拖曳加寬**：之前只有 # / 原文 欄有 resize handle；新增譯文欄右側 handle，往右拖會讓整張表變寬、面板水平捲動。長譯文不夠看時可以拉開來看完整內容

---

## [1.4.37] - 2026-05-04

### 修正

- **`jtdt update` 拒絕降版時實際還是降版了**（v1.4.36 之前長期 bug，使用者 v1.4.36 部署時觸發）：
  - `svc_update` 偵測到 origin/main 比目前舊時，會印 warning 並嘗試 `git reset --hard v{cur}` 還原 — 但如果本地 VERSION 沒對應的 git tag（例如 dev 環境只 bump VERSION 不 git tag），restore 靜默失敗、code 繼續往下跑，最後仍然降版且服務以舊版重啟
  - 修法：在 `git reset --hard origin/main` 之前先用 `git rev-parse HEAD` 抓 SHA，downgrade abort 時先用 SHA 還原（一定存在），SHA-restore 失敗才 fallback 到 tag 還原
- **逐句翻譯停止後再翻譯時停止按鈕有時消失**：
  - Race condition：使用者按下停止 → 開始新翻譯時，前一輪的 worker promise 還沒完全 resolve → finally 慢半拍執行 → 把新翻譯剛 set hidden=false 的 btnStop 改回 hidden=true
  - 修法：在 btnTranslate handler 開頭把 `_translateAbortCtl` 拷貝成 `myCtl`，finally 只在 `_translateAbortCtl === myCtl` 時才清 UI（== 「我還是當前的翻譯」），新翻譯啟動後舊翻譯的 finally 不再動 UI

---

## [1.4.36] - 2026-05-04

### 改善

- **逐句翻譯解析中提示顯示引擎名稱**：上傳 .docx/.odt/.ods/.odp/.doc/.rtf 時顯示「OxOffice 解析中…」或「LibreOffice 解析中…」（依 server 實際裝的 binary），PDF 顯示「PyMuPDF 解析中…」。讓使用者知道後端是用哪條路在處理
- **逐句翻譯文案精簡**：頁首跟工具列描述拿掉「PDF / DOCX / TXT」格式列表，統一寫「上傳文字或文件檔」 — 之前漏更新成 ODT/DOCX/RTF 都支援後字串就過時了

---

## [1.4.35] - 2026-05-04

### 新增

- **逐句翻譯：DOCX / DOC / ODT / ODS / ODP / RTF 統一走 soffice 文字匯出**：
  - 等同「OxOffice/LibreOffice 開檔→另存為純文字」，跟使用者複製貼上看到的段落結構完全一致 — 列表編號、表格、註腳都正常
  - 新增 `office_convert.convert_to_text()` 走 `--convert-to txt:Text (encoded):UTF8`，跟 PDF 轉檔共用同一個 soffice 鎖
  - PDF 維持 PyMuPDF 直接抽，但加上段落重組（折回段內換行、保留段間空行）
- **逐句翻譯加「停止翻譯」按鈕**：
  - 翻譯中按下立刻 abort 所有 in-flight fetch + 設 cancel flag 讓 worker 停拉新句
  - 統計列顯示「已中止 — 完成 N/總數 句」並保留已翻好的部分
- **逐句翻譯表格欄寬可拖曳**：滑鼠拖 # / 原文 欄右側細長 hit-area 即時調整欄寬，譯文欄自動吃剩餘空間。最小 80 px 防止欄被拖到消失
- **逐句翻譯偵測「填寫位」短路不送 LLM**：純底線 / 短橫線 / 點 / 等號等占位符（例如合約裡的 `_______________` 簽名線、`...........` 填寫位）直接 echo 原文、譯文欄顯示「（填寫位）」灰字。省 LLM call、保留版面對齊
- **長 token 強制折行**：給 src / tgt cell 加 `overflow-wrap: anywhere`，超寬底線 / URL 不再衝出表格右邊界

### 移除

- 廢除前端 `_extract_text_from_odf` 直接 zipfile + ElementTree 解析路徑（被 soffice 走法取代，留 helper 在原檔但已不被呼叫，未來可清掉）

---

## [1.4.34] - 2026-05-04

### 新增

- **逐句翻譯支援 ODF 檔案上傳**（ODT / ODS / ODP）：
  - 直接 unzip + 解析 `content.xml` 內 `<text:p>` / `<text:h>`，不走 soffice、不需轉成 PDF — 比走 office_convert 快、保留段落結構
  - 上傳對話 accept 加入 `.odt,.ods,.odp`，提示文字也更新

---

## [1.4.33] - 2026-05-04

### 修正

- **逐句翻譯所有結果都顯示「no result」**（v1.4.32 引入）：
  - 根因：v1.4.32 把前端從 `/translate-batch` 切換到 `/translate-one` per-sentence 並行池，但用了錯誤的 response shape — `/translate-batch` 回 `{results: [...]}`，`/translate-one` 直接回 `{src, translated, error}` 單個 dict。前端 `j.results[0]` 永遠 undefined → 顯示 fallback 字串「no result」
  - 修法：前端解 `{translated, error}` 直接欄位

---

## [1.4.32] - 2026-05-04

### 新增

- **逐句翻譯加進度條**：改用前端 4-worker 並行池呼叫 `/translate-one`，每完成一句立刻刷新該列譯文，頂端有進度條 + 「N/總數 句、已花 X 秒、預估剩 Y 秒」即時更新。長文不再一片黃 shimmer 直到結束才出結果
- **逐句翻譯表格加「編號」欄**：左側 44px 欄顯示句序 (1, 2, 3 …)，方便溝通「第幾句翻錯」；hover 時編號變紫加深
- **逐句翻譯 prompt 加台灣 IT 術語對照**：translate-doc 後端在目標為繁中時，prompt 額外塞入 ~40 條對照（kernel→核心、software→軟體、network→網路 …），避免 LLM 自動套用大陸用語

### 修正

- **逐句翻譯來源面板版面緊湊**：「或上傳檔案」拖曳區改成 36px 單行高度，跟左側兩個下拉同高，不再下拉下方一大片留白
- **逐句翻譯結果表頭語言名稱雙層括號**：原本顯示「原文 (English (英文))」，因前端 `LANG_NAMES` 已含 `(English)` 又被 header 包了一層 `（…）`。改成純中文鍵值「英文 / 日文 / 法文 …」消除雙括號

---

## [1.4.31] - 2026-05-04

### 修正

- **登入頁 Safari 認證領域下拉框高度與帳號 / 密碼欄位不一致**：
  - 根因：Safari 對原生 `<select>` 套自家 chrome，使 padding 計算結果矮於 `<input>`
  - 修法：`appearance:none` 抹掉系統樣式 + 自繪 SVG 下拉箭頭 + 跟 input 共用同一條 padding/border/line-height 規則 → 三個欄位高度完全一致

---

## [1.4.30] - 2026-05-04

### 修正

- **PDF 密碼解除滑桿邊緣標籤偏移**：
  - 之前左右兩端會用 `translateX(0)` / `translateX(-100%)` clamp 把 label 推到 thumb 邊緣 → 視覺上 label 跟方塊不同心
  - 改成永遠 `translateX(-50%)` 中心對齊 thumb；label 約 50px 寬，邊緣最多 overflow 13px (剛好抵銷父容器 padding)，視覺上完全居中

---

## [1.4.29] - 2026-05-04

### 修正

- **admin LLM 設定模型區提示文字過大、卡片改一欄**：
  - 「3. 模型」下方說明 (清單會在/建議/⚠) 之前用 `<p class="model-hint-line">` 但沒對應 CSS，繼承 default `<p>` 樣式 → 字大、間距太鬆
  - 加 `.model-hint` styling：12px、緊湊行距、左側 208px 對齊下拉欄位
  - 各工具個別模型卡片從 auto-fill 兩欄改成單欄（1fr），文字 / 下拉一行一張，閱讀更舒適

---

## [1.4.28] - 2026-05-04

### 修正

- **admin LLM 設定 → 各工具個別模型版面排版亂掉**：
  - HTML 用 `.ptc-*` (card) 但 CSS 只定義 `.ptt-*` (table) — 之前 refactor 漏改 CSS，導致每個工具都變成沒框、沒間距、文字擠成一團
  - 改成 responsive grid of cards：320px 最小寬度 auto-fill，每張卡片有 icon + 工具名 + 視覺/文字 tag + tool-id chip + 一句話用途 + 模型下拉

---

## [1.4.27] - 2026-05-04

### 新增

- **3 個工具新增 LLM 加值功能**（皆預設關閉、需 admin 啟用「LLM 設定」）：
  - **字數統計**：勾選「LLM 摘要 + 關鍵字」可在統計結果下方額外顯示 3-5 句重點摘要與前 10 大關鍵字
  - **註解整理**：勾選「LLM 自動分組」依註解內容主題自動歸類（例：『需修改文字』『格式問題』『詢問疑點』『已確認』）
  - **文件差異比對**：勾選「LLM 變動摘要」比對完成後產出整體變動的中文摘要與 3-7 個重點清單
  - 全部使用統一的 `llm_gate(augment)` 元件，沒啟用時顯示 disabled checkbox 並提示聯絡管理員
- **`KNOWN_LLM_TOOLS` 註冊新工具**：admin LLM 設定頁的 per-tool model override 下拉清單會自動包含這 3 個新項目

### 修正

- **PDF 密碼解除高鐵模式同名檔案編號規則**：
  - 之前：兩張票同一天時，第一張叫 `20260127.pdf`、第二張才有 `_2` 後綴 → 不一致很醜
  - 改成：偵測到同 base 兩個（含）以上時，**全部**加 `_NN` zero-padded 後綴（`20260127_01.pdf`、`20260127_02.pdf`）。位數依該組數量決定（≥10 張會自動補成 3 位）
- **PDF 密碼解除月份滑桿浮動標籤偏移**：
  - 浮動的「YYYY/MM」標籤之前對不齊滑桿方塊（左右兩端各偏移 ~10 px）
  - 根因：之前用 `12px + idx/13 × (100% - 24px)` 算 thumb 位置，沒算到 thumb 本身 20 px 寬與 inset 12 px
  - 改用瀏覽器實際 thumb 中心公式 `22px + idx/13 × (100% - 44px)`，標籤、fill bar、底部 anchor ticks 三者完全對齊

---

## [1.4.26] - 2026-05-05

### 修正

- **PDF 密碼解除月份滑桿 fill / thumb 對齊**：
  - fill bar 不再 overshoot — 從「container 寬」改算「thumb 實際位置」(`calc(12px + idx/13 × (100% - 24px))`)，跟 thumb 中心 100% 對齊
  - thumb 垂直置中：runnable-track 高度設成跟 input 同高 (24px)，thumb 自然居中，不用 margin-top hack
- **取消高鐵模式 6 個月上限**：依使用者選取範圍計算多少個月就用多少個月（slider 自然限在 14 個月內）

---

## [1.4.25] - 2026-05-05

### 改善

- **PDF 密碼解除月份滑桿視覺優化**：
  - thumb 上方加 floating label（橘底白字 + 三角指標），即時顯示「目前拖到的月份」
  - 固定錨點 ticks（時間軸最早 / 中段 / 最晚）放下方，不再跟 thumb 重疊
  - 邊緣 tick / label 對齊修正：最左用 `translateX(0)`、最右用 `translateX(-100%)`，文字不再超出 container 邊界
  - 軌道加粗（6→8 px）+ 加深陰影，看起來比較有質感

---

## [1.4.24] - 2026-05-05

### 改善

- **PDF 密碼解除「密碼模式」採用跟「高鐵模式」一致的 row 排版**：「密碼」label + 密碼輸入框並排，下方提示訊息對齊；三個模式視覺風格統一。

---

## [1.4.23] - 2026-05-05

### 改善

- **PDF 密碼解除「高鐵模式」改用雙把手月份範圍滑桿**：原本系統 month picker（要開行事曆）→ 兩個 dropdown（4 個欄位）→ 都不夠順手。改成單一橫向時間軸（13 格刻度，最近 12 個月 + 當月 + 下個月）+ 兩個拖曳把手，**拖一次搞定**。橘色 fill 條視覺化選取範圍，summary 即時顯示「2025/12 ～ 2026/02（共 3 個月）」。
- **預設範圍最近 3 個月**（原本 2 個月）。
- **「解除並下載」按鈕改名「解除密碼」**（更貼近動作本身、不囉嗦）。

---

## [1.4.22] - 2026-05-05

### 修正

- **PDF 密碼解除模式卡片：標題與 checkbox 對齊**：原本 `align-items: baseline` 在 checkbox 旁的中文文字基線對不上，看起來高低錯位。改 `align-items: center` + 重置 checkbox margin。
- **高鐵模式月份範圍上限放寬到 6 個月**（原本 3 個月，後端 100 天 → 200 天）。

---

## [1.4.21] - 2026-05-05

### 改善

- **PDF 密碼解除：三模式獨立卡片（複選）**：原本「密碼欄位 + 兩個 checkbox 散在底下」太亂、文字溢出 panel。改成三張獨立卡片：
  - **密碼模式**（預設勾）：手動輸入密碼欄位摺疊在卡片內
  - **檔名模式**：勾選即生效，無額外欄位
  - **高鐵模式**：勾選後卡片內展開「月份範圍 + 日期格式 + 即時預覽」
- 卡片用 grid layout 確保長文字 wrap（不再溢出 panel）；勾選的卡片有藍色邊框 + 淺藍底高亮。
- 至少要勾一個模式才允許送出。
- 全域 `jtdtError(response)` helper：解析 FastAPI `{"detail": "..."}` JSON 顯示乾淨訊息（不再露出 raw `{"detail":"..."}`）。

---

## [1.4.20] - 2026-05-04

### 改善

- **PDF 密碼解除「高鐵模式」改用月份選擇器**：原本是日期選擇器（兩個 date input）— 改成 `<input type="month">`，使用者只挑「起月份 / 迄月份」更省事。後端自動轉「起 = 該月 1 日 / 迄 = 該月最後一日」。最大範圍 90 天 → 100 天（剛好涵蓋連續 3 個月最大值）。
- **修選項卡片溢出 panel 邊界**：「以檔名為密碼」/「高鐵模式」說明文字超過寬度衝出 panel，改用統一 `.opt-card` class（`flex:1; min-width:0; overflow-wrap:anywhere`），文字正確 wrap。

---

## [1.4.19] - 2026-05-04

### 新增

- **PDF 密碼解除：「以檔名為密碼」勾選**：每個檔案用自己的主檔名（無 `.pdf`）當作密碼。同時填了上方密碼則先試檔名失敗再試手動，先成功的用。多份檔不同密碼批次解密很方便。
- **PDF 密碼解除：「高鐵模式」**：台灣高鐵電子車票 PDF 的開啟密碼是出發日期。勾選後挑選日期範圍（預設最近 2 個月、最多 90 天），對每個檔嘗試該範圍內每一天的日期作為密碼；成功後輸出檔名自動改為「<該日期>.pdf」（直接看出搭乘日期）。
- **高鐵模式：日期格式可選 / 自訂**：預設 `YYYYMMDD`（高鐵真正用的格式），下拉可選 `YYYY-MM-DD` / `YYYY/MM/DD` / `DDMMYYYY` / `DD-MM-YYYY` / `MMDDYYYY` / `YYMMDD`，或選「自訂…」自填任意 `Y/M/D` 與分隔符組合（- / . _ 空白）。即時預覽今天的日期看起來怎樣。

### LLM UX

- 新建 `components/llm_gate.html` jinja macro：兩種模式 `only`（LLM 是工具唯一功能、未啟用就大資訊卡擋住）、`augment`（LLM 是加值、checkbox 灰色 disabled + 提示）。所有 LLM-using 工具未來統一接這個 macro，改 UX 一處改全部。
- `translate-doc` 改用 `llm_gate(only)` — 未啟用 LLM 時整個工具 UI hidden，避免使用者貼字按按鈕才發現失敗；資訊卡內含「強烈建議地端 LLM」隱私說明 + admin 連結（非 admin 看到「請聯絡管理員」）。

---

## [1.4.18] - 2026-05-04

### 新增

- **權限矩陣（admin/permissions）右側「角色」「進階：直接 grant 工具」兩段都加全選 / 取消全選 / 反向選取按鈕**：跟「角色管理」頁一致的批次操作 UX。bulk 操作只影響搜尋結果可見的項目（避免誤勾被過濾隱藏的工具）。

### 改善

- **批次選取按鈕用語統一為「全選 / 取消全選 / 反向選取」**：權限矩陣與角色管理兩頁同一套用語。
- **CHANGELOG 拿掉所有裝飾性 emoji**（一致風格、grep 友善）。

---

## [1.4.17] - 2026-05-04

### 改善

- 工具更名：「轉向」→「**頁面轉向**」、「分拆」→「**頁面分拆**」（避免太短不知道在轉什麼 / 拆什麼）。
- README / docs 同步更新。

---

## [1.4.16] - 2026-05-04

### #6 pdf-editor 文字物件變空白 — 真正根因 + 修復

從 v1.4.0 ~ v1.4.15 多次嘗試都沒解。今天透過 backend log + 直接讀 PNG 預覽，**確認 backend 完全正確**：redact + insert text 都成功，PDF 內含正確文字、PNG render 也清楚顯示文字。問題在**前端的 redact marker**：

- `addRedactMarker()` 建立的 `fabric.Rect` 用 `fill: '#ffffff'`（**完全不透明白色**）
- 這個 marker `_peMarker=true`，永遠不會被 fade，也不會被移除
- 物件 baked 後，BG 已經有新文字，但白色 marker 蓋在 BG 上 → 把 BG 的新文字整個遮住 → 使用者看到「白色 + 紅虛線框」

**修法**：savePdf 結束、把物件標 `_peSaved=true` 時，同時找出該物件對應的 marker（用 `_ownerId` 配對 `_peId`），把 fill 改成 `rgba(255,255,255,0)` 透明 — 紅虛線框保留（讓使用者知道這是 redact 區），但讓 BG 的烙進文字看得到。

> 教訓：每次「視覺看不到」的 bug，要分清楚是 backend 沒寫 / PDF 沒寫 / PNG 沒 render / 還是前端 layer 蓋住。`curl preview_url` 看 PNG 一刀切，比一直猜 backend 邏輯有效率很多。

---

## [1.4.15] - 2026-05-04

### #6 偵錯強化

- pdf-editor backend Pass 2 text insert log 多印 `page.rotation` / `page.mediabox` / `page.rect`，以便診斷「文字消失」是不是被 PDF page rotation 雷到（rotated page 用 insert_text 時座標系跟 unrotated 不一樣）。

---

## [1.4.14] - 2026-05-04

### 改善

- README 用語：「反代地雷」→「反向代理避坑」（更口語、不嚇人）。

---

## [1.4.13] - 2026-05-04

### 緊急修正

- **逐句翻譯阻塞整個 server**（客戶 / 同事回報）：`translate-doc` 的 `_translate_sentences` 是同步函數（內部用 ThreadPoolExecutor + 阻塞 `.map()`），但被 `async def` 路由直接呼叫 → 翻譯期間整個 async event loop 被卡住 → 使用者開新分頁進其他工具完全沒回應。修法：3 個 endpoint（`translate-batch` / `translate-one` / `api/translate-doc`）全部改用 `await asyncio.to_thread(...)` 把翻譯送到 default executor 跑，event loop 立刻可以服務其他請求。

### 新增

- **API 使用手冊**（`API.md`）：完整記錄所有 `/api/*` 對外 endpoint、認證方式（Bearer token）、即時回應 vs job 模式、整合範例（GitLab CI / Python / Shell / Node.js），以及反向代理 / 速率限制建議。
- **網站新增「11. 逐句翻譯（接地端 LLM）」showcase**：用實際翻譯介面截圖展示，強調「不上雲、文件內容絕不外傳」的 on-prem LLM 賣點。

### 改善

- **網站 / README / 工具描述：「接 LLM」→「接地端 LLM」**：明確強調建議用本機 Ollama / vLLM / LM Studio 等，避免雲端 API 把文件內容外傳。
- **pdf-editor #6 後續偵錯**：backend Pass 2 text insert 加入詳細 INFO log（page / rect / text / font / has_orig_bbox），日後若有「文字消失」客訴可從 log 直接看到實際送進 PyMuPDF 的內容。

---

## [1.4.12] - 2026-05-04

### 新增

- **浮水印支援個人臨時資產**（與 pdf-stamp 相同模式）：在「浮水印」工具下方新增「臨時上傳一張（僅本次）」按鈕。圖片只放在使用者瀏覽器 sessionStorage，**不會存到伺服器**，別人也看不到；產製送出時才隨 request 上傳。每次使用會寫一筆 `event_type=temp_asset_used`/`pdf-watermark` 稽核記錄（含使用者、IP、檔名、size、sha256 前 16 字），admin 在稽核記錄頁可查。Backend 用同樣的 `_resolve_watermark_source` helper 處理；preview-watermarked + submit 兩個 endpoint 都接 `temp_asset_file` form field。

---

## [1.4.11] - 2026-05-04

### 緊急修正

- **`/setup-admin` 500 Internal Server Error**：v1.4.2 加「沿用既有 admin」reuse 路徑時 `setup_admin.html` 漏了一個 `{% endif %}` 對應 `{% if has_existing %}`。客戶按「啟用認證」直接撞 500。Hotfix 補上。

---

## [1.4.10] - 2026-05-04

### 改善

- **LLM 設定模型說明的「測試連線」更醒目**：原本「清單會在『測試連線』後從 server 抓取」的提示是純文字，使用者常忽略。改成黃底膠囊狀 inline 按鈕「↻ 點此測試連線」，點下去直接觸發測試 + 高亮上方真正的「測試連線」按鈕 + 滑動到視野中央。

---

## [1.4.9] - 2026-05-04

### 改善

- **企業 Logo 裁切框超出圖片邊界時，顯示區會自動放大**：原本拖出邊界看不到選框實際位置；現在 wrapper 動態擴張到包住「圖 + 選框」整個 bounding box，圖片自動 shift 到正確相對位置，超出區用 checkerboard 背景表示「空白 padding」。所見即所得。

---

## [1.4.8] - 2026-05-04

### 改善

- **各工具個別模型下拉：視覺工具自動 disable 純文字模型**：例如 `pdf-fill`（標 vision）的下拉打開時，`deepseek-r1:70b` / `gpt-oss:120b` 等純文字模型整組變灰、不可選，前面加禁用標示，optgroup label 改為「文字 / 其他模型（此工具需視覺模型，無法使用）」。避免 admin 誤選導致 LLM 校驗永遠失敗（純文字模型看不到圖）。原本選的值若被 disable，自動 fallback 到「（用上方預設）」。

---

## [1.4.7] - 2026-05-04

### 改善

- **LLM 設定「4. 各工具進階設定」分組**：原本「4. 校驗行為」段把 pdf-fill 校驗用的 4 個設定（審查輪數 / Confidence 門檻 / 連續同錯 / 整體 timeout）跟 translate-doc 用的「翻譯並行數」混在一起，使用者看不出哪個設定影響哪個工具。改為按工具分組：
  - 「**表單自動填寫 · LLM 校驗**」（pdf-fill）— 4 項 review 設定
  - 「**逐句翻譯**」（translate-doc）— 翻譯並行數
- 各組標題加 tag chip + tool id `<code>`，與「3. 模型 → 各工具個別模型」段的 vision/text tag 一致。

---

## [1.4.6] - 2026-05-04

### 新增

- **逐句翻譯：並排對照表頭列**：原文與譯文上方常駐表頭顯示「原文（English）」「譯文（繁體中文）」+ 各自字數統計；sticky 跟著捲動。
- **企業 Logo 裁切框可超出原圖邊界**：超出區域自動以透明 padding 補滿，方便用小圖製作正方形 logo。

### 修正

- **pdf-rotate「逆時針 90°」改名「270°」**，與其他角度標籤一致。
- **pdf-rotate 縮圖小工具列**：圖示按鈕改成數字角度（0° / 90° / 180° / 270°）+ mirror 圖示，新增「0°」明確表達「此頁不轉」。
- **LLM 設定「3. 模型」段落說明文字凸出 panel 左邊**：`margin-left:160px` 跟不上 v1.4.2 之後改為 200px label 寬度，調整為 208px 對齊 field 欄起點。

---

## [1.4.5] - 2026-05-04

### 安全強化

- **逐句翻譯：非 admin 不再看到 LLM server URL**：原本「使用模型：xxx @ http://192.168.x.x:11434/v1」對所有使用者顯示，內網 IP 對一般使用者屬敏感資訊。現在只 admin 看得到完整 server URL，一般使用者只看到模型名稱 + 「如要更換模型請聯絡管理員」。

---

## [1.4.4] - 2026-05-04

### 修正

- **企業 Logo 裁切框看不見**：`cropPanel` 還在 `hidden` 時讀 `cropImg.clientWidth` 回 0 → 後面所有計算 NaN → 藍色拖曳框 `width:0` 看不見。修法：先 unhide 再用 `requestAnimationFrame` 等 layout 完成才量。順便處理 cached image 不觸發 onload 的 corner case。
- **註解清除頁警告 banner 與「上傳 PDF」區塊太貼**：`.as-warn` 加 `margin-bottom:18px`。

---

## [1.4.3] - 2026-05-04

### 新增 — 企業 Logo 上傳支援裁切

- 上傳非正方形圖片時，裁切面板自動出現：可拖曳藍色方框 + 四角調整裁切範圍。
- 三個快捷按鈕：「正方形」（鎖 1:1，預設）/「自由」（自由比例）/「全圖」（不裁切）。
- 客戶端用 `<canvas>` 直接裁好再上傳，不增加 server 負擔。
- 即時顯示裁切後尺寸（自然像素）。

---

## [1.4.2] - 2026-05-04

### 大改版 — 客戶慘案修復 + 升級安全 + 多項 UX 強化

#### 重大修正 — 升級流程不准弄壞既有設定

- **`auth_settings.json` 變 root:root mode 600 → 服務讀不到、客戶以為 LDAP 設定消失**：根因是 `_run_auth_helper` 跑 sudo 寫檔後沒 chown 回 service user。修法：
  - `_run_auth_helper` 結尾固定呼叫 `_chown_data_files_back()` 把整個 data dir 還給原 owner
  - `svc_update` 結尾也跑一次 — **既有客戶機只要 `jtdt update` 一次就會 self-heal**，不必手動 chown
  - 新加 memory rule「客戶升級版本，原有設定必需留存」永久遵循
- **`/setup-admin` 偵測既有 user 時提供「沿用既有 admin」恢復路徑**：避免「停用認證 → 再啟用 → 撞既有 user → 報資料庫狀態異常」這個無路可走的死局。新 endpoint `POST /setup-admin/reuse-existing` 直接 flip backend=local 不建新帳號、清舊 sessions。

#### GitHub issue #1 — Windows install 卡 NSSM 下載

- **NSSM bundled 在 repo 內**：`packaging/windows/nssm.exe`（NSSM 2.24 win64 官方版，BSD 授權允許 redistribute）。`install.ps1` 在 `Fetch-Code` 之後執行 `Install-Nssm`，優先使用 bundled，網路下載成 fallback。
- **SHA-256 校驗**：寫死 `f689ee9af94b00e9e3f0bb072b34caaf207f32dcb4f5782fc9ca351df9a06c97` 在 install.ps1，被改過就拒絕。任何人可獨立用 `Get-FileHash` 驗證。
- **網路 fallback 改用 `Invoke-WebRequest -TimeoutSec 20`** 取代老 `Net.WebClient.DownloadFile`（沒 timeout，公司 firewall 擋會卡好幾分鐘才出錯）。
- **AV 誤判處理**：詳細文件 `packaging/windows/README.md` + `THIRD-PARTY-NOTICES.md` 說明 NSSM 來源、授權、SHA-256、誤判處理路徑。

#### 友善錯誤頁

- 預設 FastAPI 把 401 / 403 / 404 渲成 raw JSON `{"detail": "..."}`，使用者看到光禿一行 JSON 像系統壞掉。新增 exception handler — 只攔瀏覽器導航 (Accept: text/html) 改成友善 HTML 頁（含「回首頁」/「去登入」按鈕）；API client (Accept: application/json) 維持 JSON 行為不變。

#### 逐句翻譯增強

- **並行翻譯 (k=4 預設)**：`_translate_sentences` 改用 `ThreadPoolExecutor`，10 句翻譯時間從 ~30s → ~8s（4 並行、本機 Ollama）。並行數可在 admin LLM 設定調整（1-16），高 VRAM 可拉到 12+，雲端 API 設小避免 rate-limit。
- **顯示使用模型**：頁面上方藍底 banner 顯示「使用模型：xxx @ url」；翻譯中按鈕、結果 meta 也顯示。
- **整列 hover 光棒**：滑鼠移到並排對照任一列，左原文 + 右譯文 + 中央按鈕欄整列流動高光（CSS shimmer）。
- **每格小複製按鈕**：hover cell 時右下浮現半透明複製按鈕，點下變綠 0.9s 表示複製成功。
- **drag-drop 檔案上傳美化**：替代醜醜的 `<input type="file">`，改成大 icon + 「點此挑選或拖曳檔案到此」zone；拖檔變綠、選好顯示檔名 + 解析統計。
- **語言下拉套平台 `.field` 樣式**：跟其他工具頁一致；解決三欄 label 重疊問題。
- **LLM 設定頁加隱私 banner**：強烈建議接地端自架 LLM Server（Ollama / vLLM / LM Studio）— 雲端 API 會把所有送 LLM 的原始文件內容外傳，違反個資法 / 營業秘密 / NDA。
- **非 admin 看不到 LLM 設定連結**：`is_admin(request)` jinja global gate；非 admin 改顯示「如要更換模型請聯絡管理員」。

#### 各工具個別 LLM 模型

- admin 在 LLM 設定頁可為 `translate-doc` / `pdf-extract-text` / `pdf-fill` 各自指定不同模型（例：純文字翻譯用 qwen3:32b、視覺校驗用 gemma4:26b）。`llm_settings.get_model_for(tool_id)` 統一解析；新加 LLM-using tool 加進 `KNOWN_LLM_TOOLS` 即可自動出現在 UI。
- LLM 設定欄寬統一（短輸入 100px、左 label 200px），整面對齊。

#### 「文書內容」分類併入「內容處理」

- 原 v1.4.0 為了放逐句翻譯新開的「文書內容」分類只有一個工具，太單薄。重新命名「內容擷取」→「**內容處理**」（語意更廣），把 6 個工具（擷取文字 / 圖片 / 附件 / 字數統計 / 註解整理 / 逐句翻譯）放在一起。從 7 大類回到 6 大類。

#### pdf-rotate 預覽個別轉向 UX

- 點同一方向不再 toggle off（反直覺）；每個按鈕都是「設成那個方向」，要清掉用「─」。
- 縮圖改用 server-side 預先 render（PIL transpose）取代 CSS `transform`，視覺直接顯示旋轉後結果，跟 lightbox 一致。

#### pdf-editor 文字物件變空白 deeper fix (#6)

- 客戶端 safety net：若 IText 是從原 PDF 擷取（有 `_origBbox`）但 `text` 變空，**不送上 backend** — 否則會 redact 原文留白，看起來像「文字消失」。
- Backend 同樣 safety net：empty text + original_bbox 直接跳過 redact，原文保留。
- `_insert_mixed_text` 多層 CJK font fallback — 不再直接掉到 helv（Helvetica 沒 CJK glyphs，會渲成 .notdef tofu 或完全不顯示）。失敗時 log warning。

---

## [1.4.1] - 2026-05-04

### 新增 — 使用者後續回饋整合

- **pdf-rotate 縮圖個別轉向 UX 改善**：點同一方向不再「toggle off」（使用者點 ↻ 期待轉，但二次點變不轉是反直覺）。每個方向按鈕都是「設成那個方向」；要清掉個別覆寫請點「─」（明確不轉）。縮圖也改用 server-side 預先 render（PIL transpose）取代 CSS `transform: rotate()`，視覺直接顯示旋轉後結果，跟 lightbox 一致，沒有 aspect ratio 雷。
- **逐句翻譯 UI 強化**：
  - 上方加藍底 banner 一直顯示「使用模型：{model name}」，翻譯中也在按鈕與 meta 顯示
  - 翻譯進行中的列加 shimmer 光棒效果（流動高光），左 src + 右 tgt + 中央按鈕欄都吃
  - 每一格右下加小複製按鈕，hover cell 才浮現（半透明），點下變綠表示成功；可單獨複製某句原文 / 譯文
  - 語言下拉與檔案 input 套平台 `.field` 樣式，跟其他工具頁一致
- **LLM 設定支援各工具個別模型**：admin 「LLM 設定 → 模型」段下方新增「各工具個別模型」清單，可為 `translate-doc` / `pdf-extract-text` / `pdf-fill` 各自指定不同模型（例：純文字翻譯用 qwen3:32b、視覺校驗用 gemma4:26b）。留空就跟隨上方預設。`llm_settings.get_model_for(tool_id)` 統一解析；新加 LLM-using tool 時加進 `KNOWN_LLM_TOOLS` 即可自動出現在 UI。
- **LLM 設定欄位寬度統一**：所有短輸入（timeout / 輪數 / threshold）統一 100px、左側 label 統一 200px，整面對齊。`base_url` / `api_key` 走 `field-wide` class 維持寬版。
- **「文書內容」分類併入「內容處理」**：原 v1.4.0 為了放逐句翻譯新開的「文書內容」分類只有一個工具，太單薄。重新命名「內容擷取」→「**內容處理**」（語意更廣，未來 LLM 摘要 / Q&A 也能進），把 6 個工具（擷取文字 / 圖片 / 附件 / 字數統計 / 註解整理 / 逐句翻譯）放在一起。從 7 大類回到 6 大類。

---

## [1.4.0] - 2026-05-04

### 大改版 — 11 項使用者建議全部到位

#### 新工具

- **逐句翻譯**（`/tools/translate-doc`）：接 admin 設定的 LLM server，左原文右譯文逐句並排。可貼文字或上傳 PDF / DOCX / TXT；目標語言預設繁中可選；每句可單獨重新翻譯。LLM 未啟用時頁面顯示提示，不擋其他工具。對外 API：`POST /tools/translate-doc/api/translate-doc`。

#### 系統依賴自動安裝（修文件轉檔失敗）

- 修 `office-to-pdf` / `pdf-to-image` / `doc-diff` 在 minimal Linux 起不來（OxOffice oosplash 缺 X11 client lib：`libXinerama.so.1: cannot open shared object file`）。`install.sh` + `jtdt update` 都自動補裝完整 X11 runtime（`libxinerama1 libxrandr2 libxcursor1 libxi6 libxtst6 libsm6 libxext6 libxrender1 libdbus-1-3 libcups2`）；admin「相依套件檢查」頁面新增 X11 lib 偵測項目。

#### 企業識別

- 新 admin 子頁「企業 Logo / 識別」（`/admin/branding`）：上傳一張企業 logo（PNG / JPG / WEBP，自動 resize 到 256 px、轉 PNG），自動套用到左側 sidebar、瀏覽器 favicon、首頁 hero、登入頁。「還原預設」按鈕一鍵 rollback。

#### 設定備份 / 搬遷

- 新 admin 子頁「設定備份 / 匯入」（`/admin/settings-export`）：把所有 admin 設定（assets / branding / fonts / profile / synonyms / templates / api tokens / llm settings / office paths / auth settings / font settings）打包成單一 zip 給備份 / 跨機搬遷。匯入時舊檔自動備份成 `.bak.<timestamp>`，失敗可手動 rollback。歷史記錄目錄（fill / stamp / watermark history）為可選匯出項。

#### 個人臨時資產（用印與簽名）

- 「用印與簽名」可在 admin 沒有預先建好印章時，使用者「臨時上傳」一張圖片自己用。圖只放在瀏覽器 sessionStorage，**不存到伺服器**，別人也看不到；蓋章送出時才隨 request 上傳。每次使用會寫一筆 `event_type=temp_asset_used` 稽核（含使用者、IP、檔名、size、sha256 前 16 字），admin 在稽核記錄頁可查。

#### UX 改善

- **每頁右上「回首頁」浮動按鈕**：所有工具 / admin 頁右上角加一個圓角按鈕，一鍵回到工具總覽（首頁本身會自動隱藏）。手機上自動縮成只有圖示。
- **角色管理權限矩陣加「全選 / 全不選 / 反選」**按鈕 + 即時計數（已選 X / Y），編輯角色時不用一個一個點。
- **「轉向」工具預覽頁可個別轉向**：每張縮圖下方加 `↻ ↺ 180° ⇆ ⇅ ─` 工具列；點任一個 = 此頁個別覆寫（綠色徽章 ★ 標示）；再點同一個 = 取消覆寫回到全頁設定。後端 `/submit` 新增 `per_page` JSON 參數（公開 API 一樣可用）。

#### Bug 修正

- **PDF 編輯器**：選定文字物件後離開選取，物件變空白的 bug。根因 — `selection:cleared` 把所有 `_peSaved=true` 的物件 fade 到 opacity 0.01，但被「跳過 bake」的 active 物件不該標 `_peSaved`，否則背景沒燒入物件文字、overlay 又變透明，使用者看到一片空白。
- **PDF 轉向預覽**：lightbox 點放大方向不對。`transform: rotate()` 視覺旋轉但 layout box 沒變，導致 max-width / max-height 算錯方向（縮圖剛好被 `aspect-ratio` 容器 mask 住所以正常）。改成 server-side 預先用 PIL transpose 燒進 PNG，`/thumb` endpoint 新增 `?mode=` query param。

#### 文件去識別化（doc-deident）精準度提升

- 新增規則：駕照號碼、出生日期 / 生日（含民國格式 `民國 70 年 3 月 21 日`）。
- 強化規則：手機號（含 `+886-9XX-XXX-XXX` 國際格式）、市話（含分機 `#123` / `ext 123`）、地址（支援 `之 N` / `N 樓` / `N 樓之 N` / `Section N` / 英文 `No. X, Sec. Y, Lane Z, Floor N`）、車牌（要求前後標點，避免吃到 `FROM 123` 之類雜訊）。
- 護照規則改為「需 label」（`護照 / Passport No.` 才認），從原本任意 9 位數字（false positive 大）改為 label-anchored，整體誤判大幅下降。

#### 資料庫 migration

- v5 migration：新工具 `translate-doc` 自動授權給已有 `text-diff` 的 role / subject。既有客戶升上來 sidebar 看得到、點得開，不會 403。

---

## [1.3.14] - 2026-05-03

### 修正（認證設定 UI：未啟用時鎖住 LDAP/AD backend 設定）

- 認證未啟用時，下方「認證 backend / LDAP 設定」面板與「驗證測試」區塊整段鎖定（`inert` 屬性 + 灰階 + `pointer-events:none`），並在面板頂端加黃底警示 banner：「請先啟用認證才能設定 backend」。避免使用者「先設好 AD 再啟用」這個常見的踩雷情境 — 因為一旦 admin 帳號還沒建好就切換到 AD backend，就會被永久鎖在外面。
- backend 同時加防線：`POST /admin/auth-settings/ldap-save` 在 auth 未啟用狀態下直接回 409，提示「先去 /setup-admin 啟用認證」。即使有人繞過 UI 用 curl 也鎖不死自己。

---

## [1.3.13] - 2026-05-02

### 修正（相依套件檢查：剝掉 Office build hash）

- `_probe_office()` 取出的版本字串含 OxOffice / LibreOffice 的長 build hash (例：`OxOffice 11.0.4.1 855623c6c181122c9b97d204c8c74172e167cf75`)，把表格版本欄撐得很寬。剝掉 20+ 字 hex 字串只保留版本號 (`OxOffice 11.0.4.1`)。要查 hash 可從表格內的 binary 路徑自行 `--version`。

---

## [1.3.12] - 2026-05-02

### 變更（README 用語修正）

- 「文字差異**標紅**」→「文字差異**以紅色標示**」（標紅是中國 IT 圈簡稱，台灣文件用全寫）。
- 同步驗證：Win11 x64 .154 + Win11 ARM64 .64.3 兩台 jtdt update 升 v1.3.11 全 pass、healthz OK。

---

## [1.3.11] - 2026-05-02

### 新增（install.ps1：缺 git 自動 winget install）

- Windows 沒裝 git 的客戶機之前 install.ps1 會 fallback 到 tarball 下載，沒 .git → 日後 `jtdt update` 直接 fail（「not a git repo, can't git pull」），客戶得手動裝 git + 重跑 installer。本版加 `Install-Git` 函式：偵測缺 git → 試 `winget install Git.Git -e --silent` → refresh PATH → 後續 `Fetch-Code` 走 git clone path → `.git` 就位 → 日後 `jtdt update` 直接可用。soft-fail：winget 不可用 / install 失敗只 warn，仍可走 tarball mode 完成首次安裝。

---

## [1.3.10] - 2026-05-02

### 修正（jtdt update 結尾 bullet 字元 Win11 console 顯示亂碼）

- v1.3.9 已全英文，但 `_print_system_deps_summary` 內的 bullet `•` (U+2022) 在 Win11 console（cp950 codepage）渲染為 `�E`。改用純 ASCII `-` (hyphen) bullet 完全避免編碼問題。
- 同步驗證：Win11 x64 (.154) + Win11 ARM64 (.64.3) 兩台都升到 v1.3.x 並 jtdt update 成功。

---

## [1.3.9] - 2026-05-02

### 修正（jtdt update 結尾系統相依摘要表也英文）

- v1.3.8 已把 cli.py 內 67 處中文 print 翻成英文，但結尾的 `_print_system_deps_summary` 內 hardcoded 的相依清單仍是中文（「pdf-editor 自動文字辨識…」、「office-to-pdf / pdf-to-office 工具」），Win11 console 顯示亂碼。本版翻譯這份 hardcoded list；同時 `app/core/sys_deps.py` 加 `impact_en` 欄位 + `collect_sys_deps(lang='en')` 切換，admin web 頁仍用台灣繁中。
- 預期完成 .154 / .64.3 兩台 Win11 機器升級驗證 (一台 x64、一台 ARM64)。

---

## [1.3.8] - 2026-05-02

### 修正（jtdt update Windows uv 偵測 + CLI 全英文 + 分類更名）

- **Windows `jtdt update` 找不到 uv binary**：`shutil.which("uv") or str(root/bin/uv)` 在 Windows 上會錯，因為 uv 是 `uv.exe`。改成依平台組正確檔名 (`uv.exe` vs `uv`) 並用 `Path.exists()` 驗證。同類問題其他平台也順便加固。
- **`jtdt` 所有 print 訊息一律英文**：v1.3.6 只改了 no-args 的 friendly help，update / uninstall / bind / auth / reset-password 等 verb 內仍是中文，Windows console / minimal TTY 都顯示亂碼。本版批次翻譯 cli.py 內 67 處中文 print（含 svc_update、svc_uninstall、svc_bind、svc_auth_*、svc_reset_password、_print_system_deps_summary、_ensure_tesseract、降版警告）為英文。GUI / web UI 仍維持台灣繁中。
- **分類「填單與用印」→「填單用印」**：tool metadata 與 README / docs 同步。

---

## [1.3.7] - 2026-05-02

### 變更（README / 公開文件 / CLI 文案調整）

- **`jtdt` 無參數印的指令清單改用英文**：純文字 TTY / minimal container / Windows console 沒切 UTF-8 codepage 都渲染不出 CJK，CLI 訊息一律英文 ASCII 比較通用。argparse 的 description / usage 同步改英文。GUI / web UI 仍維持台灣繁中。
- **README 新增「圖片轉 PDF」到「格式轉換」段**，並修正其他 22 個工具的計數 (原 21)。
- **README 拿掉所有 emoji**：「Office 引擎相依」標記改成文字 `[需 OxOffice/LibreOffice]`，更明確且 grep 友善。
- **landing page (`docs/index.html`) 拿掉模式說明卡的 home / office building emoji**。
- **landing page 「自架」slogan 文案調整**：「所有檔案處理只發生在你的伺服器，原始碼公開」→「所有檔案處理只發生在你的伺服器，**且原始碼完全公開**」更強調。

---

## [1.3.6] - 2026-05-02

### 改進（jtdt 無參數時印分組指令清單）

- 直接執行 `jtdt`（無參數）原本印 argparse 預設的單行 usage 含所有 verb，視窗窄一點就會擠在一起難讀。改成印分組漂亮的清單：「服務控制 / 升級與維護 / 緊急復原」三組，每組有縮排對齊的指令名稱與一行說明。`jtdt -h` 與 `jtdt --help` 走同一支 friendly help。

---

## [1.3.5] - 2026-05-02

### 修正（jtdt update 加降版保護）

- 慘案：客戶機 origin 設成過期的 file:// 本地鏡像，`jtdt update` 從那裡 pull 結果**直接降版** v1.3.3 → v1.1.93，丟失新功能、DB migration 不可逆，極度危險。修法：reset --hard 完成後，立即比較新 VERSION 與升級前 VERSION，若新版 < 舊版即視為「降版」直接 abort + 還原 + 啟動原服務 + 印出 git remote 檢查指令給使用者。
- 提醒：正式 install 應走 `https://github.com/jasoncheng7115/jt-doc-tools.git`；`JTDT_REPO_URL=file://...` 只能用於開發測試，切勿留在客戶機上。

---

## [1.3.4] - 2026-05-02

### 新增（pdf-editor 點選文字若需 OCR 即時提示）

- 點選原文字若 backend 在 500 ms 內沒回（幾乎一定是字型缺/壞 ToUnicode 走 OCR fallback），自動把訊息升級為「辨識中…（原文字字型無 Unicode 對應表，正在 OCR 重建文字）」，使用者不會以為當掉。

### 修正（圖片轉 PDF 設定列說明文字歸屬不清）

- 「頁面大小」下方的說明文字 (「非『原始』時會自動依圖片比例旋轉…」) 視覺上看起來比較靠近下一列「邊距」，使用者搞不清是哪一列的說明。修法：列與列之間加分隔線、列內 label 與說明文字字級 / 顏色 / 間距分明，每列垂直內間距加大；說明文字緊貼上方 input、字級 11px、灰色。

---

## [1.3.3] - 2026-05-02

### 修正（pdf-editor 既有圖片擷取保留透明背景）

- 點選原 PDF 上有透明背景 + 陰影的圖片時，擷取出來變成黑底（透明區變黑）。原因：PyMuPDF 把透明 PDF 圖儲存成「base RGB stream + 獨立 SMask xref（alpha mask）」，`fitz.Pixmap(doc, xref)` 只抓 base 不抓 SMask → alpha 全失，被當不透明圖渲染。修法：先試 `doc.extract_image()` 取原始 PNG bytes（自帶 alpha）；若該 image 有 SMask xref，組合 base pixmap + mask pixmap 成 RGBA pixmap 再存。

### 改進（相依套件檢查 UI 排版升級）

- 總覽改用 stat cards：3 張卡片（就緒 / 必要相依缺 / 選用相依缺）並排，數字大字 + 顏色明確 + 卡片背景配色。
- 表格升級：cell padding 加大、status pill 等寬對齊、optional badge 跟套件名稱同行不換行、binary 路徑 monospace 灰階、版本號用 monospace pill、安裝指令區塊加標題與背景。
- hover 列高亮，視覺層次更分明。

---

## [1.3.2] - 2026-05-02

### 修正（圖片轉 PDF：縮圖刪除鈕一直顯示 + 頁面設定排列整齊）

- 縮圖右上紅色 × 刪除鈕原本只在 hover 時 fade-in，使用者覺得「找不到刪除鈕」。改成一直顯示，加陰影與 hover 放大效果，更醒目。
- 「頁面設定」面板裡 label 與 field 對齊修正：原本 `align-items: center` 在某些 row 含 help text 把 label 推到垂直中間，看起來不齊。本面板改 `flex-start`，所有 label 一律對齊 field 第一行頂端。背景色那列也統一用 `inline-row` 排版。

---

## [1.3.1] - 2026-05-02

### 修正（pdf-editor OCR 對純英文字型用 eng-only）

- 「Proxmox VE」(用 OpenSans-Bold 字型) 透過 `chi_tra+eng` OCR 變成「ProXimoxX VE」 — tesseract 在雙語模式下偶爾會把英文 glyph 誤判到中文字。修法：用 PDF span 的字型名稱判斷主語言：含 `helvetica` / `arial` / `opensans` / `times` / `roboto` 等西文字型 hint → OCR 用 `eng` only；含 `pingfang` / `notosanscjk` / `+TC` / `+SC` 等 CJK hint → 用 `chi_tra+eng`。

---

## [1.3.0] - 2026-05-02

### 新增（圖片轉 PDF 工具）

- 全新工具「**圖片轉 PDF**」(`/tools/image-to-pdf/`)：
  - 拖入多張圖片（PNG / JPG / GIF / TIFF / WebP / HEIC，單檔上限 50 MB），可隨時再加。
  - 縮圖網格顯示，**拖曳重新排序**、**逐頁旋轉**（90° 增量）、**逐頁刪除**、**全部清除**、**全部順時針旋轉**。
  - 點縮圖開 lightbox 看大圖。
  - 頁面大小可選：原始（每頁等於圖片大小）、A3 / A4 / A5 / A6 / B5 / Letter / Legal / Tabloid。
  - 邊距可選：0 / 5 / 10 / 20 mm。
  - 背景色可自訂（非「原始」尺寸時用於 letterbox 留白處）。
  - 圖片置中，依比例自動旋轉頁面方向（橫圖 → 橫向頁面）。
  - 智能編碼：照片走 JPEG quality 85（大幅省空間），線稿 / 截圖走 PNG（保留銳利邊緣）。
  - EXIF orientation 自動正向化（手機照片不會躺著）。
  - 配套 `POST /tools/image-to-pdf/api/image-to-pdf` 給 API token / 自動化呼叫使用（form-data 多檔上傳，回 PDF 直接下載）。
- 工具總數從 27 → **28 個**；分類「格式轉換」現在含 3 個工具（文書轉 PDF / 文書轉圖片 / 圖片轉 PDF）。
- 既有客戶 DB migration v4：自動把 `image-to-pdf` 授權給已有 `pdf-to-image` 權限的 role / subject — 升級後 default-user / clerk 自動有權使用，不會出現「看得到但點了 403」。

---

## [1.2.5] - 2026-05-02

### 修正（pdf-editor OCR padding 改小避免抓到鄰近文字）

- v1.2.4 用 25% padding 解決短標題 OCR 失敗，但太大 — 把鄰近 span 也抓進去，「網路基本設定」變成「VE 網路基本設定一」（左邊「VE」是隔壁 span 的 Proxmox VE 末尾、結尾「一」是標題下方的橫線）。改成水平固定 2pt、垂直 10% 或 2pt 取大者：水平不夠抓到隔壁文字，垂直只夠 descender 不夠抓到下方裝飾線。

---

## [1.2.4] - 2026-05-02

### 修正（pdf-editor OCR 短中文標題回空字串）

- v1.2.3 在實機測試時短標題（如「網路基本設定」這類 28pt 高 bbox）OCR 回空字串，前端顯示「沒裝 tesseract」訊息但其實 tesseract 是有裝的，只是辨識失敗。修法：
  - bbox 上下左右各加 25% padding 給 tesseract 更多 glyph context（緊湊 bbox 常會讓 OCR 失敗，因為 descenders / accents / kerning 被切掉）。
  - 短標題用 400 DPI 渲染（高度 < 40pt 時），其他用 300 DPI。
  - 試多種 PSM 模式（7=單行、6=均勻區塊、8=單字、11=稀疏文字），取最長有效結果。

### 變更（術語：依賴 → 相依，台灣用詞）

- 程式碼 / UI / CHANGELOG 內的「依賴」一律改成「相依」（dependency 的台灣用法）。「軟依賴」改「選用相依」、「硬依賴」改「必要相依」。「系統依賴檢查」工具改名為「系統相依套件檢查」。

---

## [1.2.3] - 2026-05-02

### 新增（系統依賴檢查工具）

- 設定區新增第一個工具「**系統依賴檢查**」(`/admin/sys-deps`)，列出所有系統套件（tesseract / OxOffice / LibreOffice / CJK 字型 / pytesseract / Pillow 等）的安裝狀態、版本、影響說明，與每個平台對應的手動安裝指令。軟依賴 (optional) 缺失只顯示黃色警告，硬依賴缺失顯示紅色嚴重狀態，使用者一眼看到缺什麼。
- 配套 `GET /admin/api/sys-deps` JSON API 給外部監控 / 自動化呼叫使用。
- `app/core/sys_deps.py` 是單一資料來源 — `jtdt update` 結尾的依賴 summary 與 admin 頁面共享同一份 registry，避免兩處 drift。

### 變更（jtdt update 自動補裝系統依賴）

- 自 v1.2.2 起 `jtdt update` 在 `uv sync` 之後新增 `_ensure_system_deps_for_update()` 步驟，自動 best-effort 補裝新版需要的系統套件 (Linux apt / macOS brew / Windows winget)。任何失敗只 warn 不阻擋升級。升級結尾印「系統依賴狀態」表，缺什麼明確列出。
- 規矩：往後新加任何系統依賴必須**同時**處理 `install.sh` (fresh install) + `install.ps1` (Windows fresh install) + `cli.py:_ensure_system_deps_for_update()` (既有客戶 update) 三處，否則既有客戶升級後該功能無法使用。

### 修正（git 升級用 reset --hard 處理 force-pushed remote）

- `jtdt update` 從 `git pull --ff-only` 改用 `git fetch + git reset --hard origin/main`。原作法在 remote 被 force-push (歷史重寫) 時會 abort「Not possible to fast-forward」，新作法強制對齊 origin/main，符合「install dir 不做開發 commit」的設計前提。
- UI / CLI 用詞修正：「回滾」(中國用語) → 「還原」(台灣用詞)。

---

## [1.2.2] - 2026-05-02

### 新增（pdf-editor 自動 OCR 重建文字）

- pdf-editor 偵測到既有文字無法可靠擷取（字型缺/壞 ToUnicode CMap 導致 PyMuPDF 取出亂碼）時，自動把該 bbox 區域用 tesseract OCR (`chi_tra+eng` 訓練檔，300 DPI 渲染，PSM 7 單行 / PSM 6 多行) 重建文字，回傳給前端建立可編輯文字框。**使用者完全不用手動重打。** 純軟依賴：tesseract / pytesseract 沒裝就退到原本的「請手動重打」訊息，本體運作完全不受影響。
- `install.sh`：fresh install 時自動 apt/dnf install `tesseract-ocr` + `tesseract-ocr-chi-tra` + `tesseract-ocr-eng`（Linux），或 `brew install tesseract tesseract-lang`（macOS）。任何錯誤都只 warn 不 die，不阻擋安裝流程。
- `install.ps1`：fresh install 時用 winget 裝 UB-Mannheim 版 tesseract。失敗只 warn，不阻擋流程。
- `pyproject.toml` / `requirements.txt` / `uv.lock`：加 `pytesseract>=0.3.10,<0.4` 作 runtime 依賴。
- `jtdt update`：升版完若偵測 tesseract 不存在，印提示告知使用者如何手動安裝。**不主動 apt install** 以免改動既有客戶系統 apt state。

---

## [1.2.1] - 2026-05-02

### 修正（pdf-editor 亂碼偵測別自動蓋白底）

- v1.1.98 ~ v1.2.0 的修法是「偵測到亂碼 → 自動建白底 + 空文字框」，但白底 Rect 直接 push 進 Fabric overlay，瞬間就把 BG 上的原文字蓋掉，使用者連看清原文都來不及就被覆蓋。改成「只跳訊息提示，不主動建任何物件」— 使用者可目視原文字後，自己決定要不要用 W (白底) + T (文字框) 工具手動覆蓋。

---

## [1.2.0] - 2026-05-02

### 變更（小版本進版，patch 號重整）

- patch 號累積到三位數 (1.1.100) 不利閱讀，本版進到 1.2.0，patch 重新從 0 編。本身行為等同 1.1.100；後續仍以 1.2.x 累積 patch，待累積大功能再進 1.3.x。
- 累積本日（1.1.93 ~ 1.1.100）的修正：pdf-editor 下載按鈕回到純 anchor design、存檔重影修正、undo 不再誤把擷取物件記成「使用者要刪除」、中文亂碼 (Identity-H 缺/壞 ToUnicode CMap) 偵測 + 自動建白底 + 空文字框引導使用者重打。

---

## [1.1.100] - 2026-05-02

### 修正（pdf-editor 中文亂碼偵測加 heuristic 後援）

- v1.1.98 用「字型有無 /ToUnicode CMap」偵測，但有些 PDF 其實附了 CMap 但 mapping 是 identity（GID→GID），結果 ToUnicode 存在但取出的還是亂碼（例：「登入系統」→「翕⊕ㄱ 戔ㄱ」）。本版加兩個 heuristic 後援：①偵測文字含「不該出現的符號」(數學運算子 ⊕、技術符號、box drawing、注音、韓文相容字母 ㄱ、PUA 等) — 這些是 GID 被當 Unicode 解讀的典型徵兆；②若是純 CJK 字串且不含任何台灣繁中常用字（的/是/在/了 等 ~600 字白名單），視為亂碼。任一條成立就 flag `extracted_text_unreliable=true`，前端不塞亂碼，自動建白底遮罩 + 空文字框讓使用者重打。

---

## [1.1.99] - 2026-05-02

### 修正（pdf-editor undo 到最早會把既有物件 redact 掉）

- 一路 undo 回到最早 snapshot 時，原 PDF 既有文字應該完整顯示，但實際變空白。根因：`restoreSnapshot` 內 `loadFromJSON` 會把現有 Fabric 物件 remove 再 load 新的；`object:removed` handler 對於有 `_origBbox` 的物件會自動 push 到 `deletedOrigs` (使用者「刪除既有物件」的 intent 收集)。這個 handler 沒被 `suppressHistory` 守護，導致 undo 時誤把「正在被 tear down 的擷取物件」記成「使用者要刪除的既有物件」，下個 doAutoSave 把那區 redact 掉 → BG 變空白。修法：handler 開頭直接 `if (suppressHistory) return;`。

---

## [1.1.98] - 2026-05-02

### 修正（pdf-editor 中文擷取亂碼 → 改提示使用者重新輸入）

- 部分 PDF (如 Proxmox VE 手冊) 的中文字型用 Identity-H subset 但缺 `/ToUnicode` CMap，PyMuPDF 取出時把 GID 當 Unicode codepoint，「登入系統」變成「猞狝狘」之類罕見 CJK 亂碼。Scribus / LibreOffice 因為只做視覺 render 不需 Unicode mapping 所以看不出問題；做 overlay editing 必須拿真 Unicode 才行。修法：backend 直接從 PDF font dict 驗該字型有無 `/ToUnicode`（比啟發式判字頻精準），沒有就把 text 留空 + 加 `extracted_text_unreliable` flag；前端收到 flag 不塞亂碼，自動建白底遮罩 + 空文字框，提示使用者直接輸入要替換的內容。

---

## [1.1.97] - 2026-05-02

### 修正（pdf-editor 存檔後既有物件重影）

- 編輯擷取自原 PDF 的文字物件，存檔後 BG 已燒入新文字，但 Fabric overlay 物件仍保持完全可見 → 兩者疊出視覺重影（位置因 PyMuPDF render 與 Fabric render 字型 metrics 微差而錯開）。原本為了「避免使用者以為物件消失」刻意保留 _origBbox 物件 opacity 1，但這代價是重影。改為跟其他疊加物件一樣 fade 到 0.01；物件本體仍存在 Fabric scene，點擊原位置仍可選取再編輯。

---

## [1.1.96] - 2026-05-02

### 整理（pdf-editor 下載：退回純 anchor design）

- v1.1.93~95 加的下載 click handler workaround (target=_blank → programmatic anchor click → location.assign) 全部退回。事後確認問題是使用者 Chrome 上某個擴充功能攔截 download，重啟 Chrome 視窗讓擴充 reload 即解決，跟程式無關。回到 v1.1.88 之前最簡單最 idiomatic 的設計：純 `<a href="{download_url}" download="{filename}">` anchor + 後端回 `Content-Disposition: attachment` header，由瀏覽器 native 處理。

---

## [1.1.95] - 2026-05-02

### 修正（pdf-editor 下載：Chrome 改用 location.assign）

- v1.1.94 用 programmatic anchor click() Edge OK 但 **Chrome 仍不下載** — Network tab 完全沒看到 `/download/...` request。Chrome 對某些情境的 anchor click 有額外擋（download-bomb 防護或擴充攔截）。改用最直接的 `window.location.assign(url)`：因為 server 回 `Content-Disposition: attachment`，Chrome 會觸發下載而不真的 navigate，當前頁面 state 完整保留。

---

## [1.1.94] - 2026-05-02

### 修正（pdf-editor 下載：Chrome 用 programmatic anchor click 而非 iframe）

- v1.1.93 改用隱形 iframe 觸發 attachment download，Edge OK 但 **Chrome 觸發不了** — Chrome 對 iframe-attachment download 有 download-bomb 防護機制會默默吃掉。改回最跨瀏覽器穩的方式：建一個臨時 `<a>` 元素 + `download` attribute + `.click()`。Chrome / Edge / Firefox 都認 programmatic anchor click + same-origin attachment URL 觸發下載。

---

## [1.1.93] - 2026-05-02

### 修正（pdf-editor 下載：anchor + target=_blank + iframe fallback）

- v1.1.92 退回純 anchor 後使用者實測仍未跳出存檔對話框（瀏覽器把 `application/pdf` 認成可內嵌就直接 inline 顯示，或被擴充攔掉 download attribute）。本版改成：`<a target="_blank" rel="noopener" download>` + click handler `preventDefault` 後用隱形 iframe 載入 download URL。Server 已回 `Content-Disposition: attachment` → iframe 不會 navigate，瀏覽器直接觸發下載對話框，且不離開當前頁。同時保留 anchor 的 href / download 屬性，讓「右鍵另存新檔」fallback 仍可用。

---

## [1.1.92] - 2026-05-02

### 修正（pdf-editor 下載 — 回到純 anchor 原生行為）

- v1.1.89/.90/.91 加的下載 click handler（fetch + blob → iframe → window.location.href）反而都被 Chrome 安全機制 / 擴充功能攔掉，使用者只看到「已下載」訊息但實際沒下載。本版**徹底移除** click handler，回到 v1.1.88 之前最簡單的 design：純 `<a href="{download_url}" download="{filename}">` anchor。textBaseline typo 修掉後 save 流程正常 → savePdf() 完成自動 set anchor href + download attr → 點擊由瀏覽器原生處理，最穩。

---

## [1.1.91] - 2026-05-02

### 修正（pdf-editor 下載真正觸發 save dialog）

- v1.1.89/.90 用 fetch + blob + 動態 anchor click() 雖然 status 顯示「已下載」，但某些瀏覽器設定下 programmatic click 不會跳 save dialog，使用者沒拿到檔案。改用 hidden iframe `iframe.src = url`，靠後端 `Content-Disposition: attachment` header 觸發瀏覽器原生下載 dialog — 最穩、最少瀏覽器特殊處理。anchor href 也照樣設好給「右鍵另存」fallback 用。

---

## [1.1.90] - 2026-05-02

### 修正（pdf-editor 真正根因 + undo BG 強制 refresh）

- **Fabric.js 5.x typo `'alphabetical'` 害 IText 寬度計算錯誤**：Fabric 把 textBaseline 設成 typo 字串 `'alphabetical'`（正確是 `'alphabetic'`），新版 Chrome 拒絕並 console warn，更糟的是 `_setTextStyles` / `_measureChar` / `calcTextWidth` / `initDimensions` 整條 chain 都用 fallback 算錯結果。最終症狀：擷取既有文字後寬度 / 位置歪掉、文字渲染偏移、undo 還原時 IText 重建也走錯流程。本版在 fabric 載入後立刻 monkey-patch `CanvasRenderingContext2D.prototype.textBaseline` setter，把 `'alphabetical'` 翻譯成正確的 `'alphabetic'`，根治整條 chain。
- **Undo 還原後 force save（不走 800ms debounce）**：原本 `scheduleAutoSave` 800ms 後才送，使用者按 undo 看不到 BG 立刻變回原始狀態。本版 restoreSnapshot 完成後 clearTimeout + 直接 doAutoSave，BG 立刻重抓。

---

## [1.1.89] - 2026-05-02

### 修正（pdf-editor 三個 bug）

- **Undo 回到開始 PDF 既有物件處變空白**：`canvasSnapshot()` 之前只存 fabric canvas JSON、沒存 `deletedOrigs`（標記為刪除的原物件 bbox 列表），undo 還原物件後 deletedOrigs 還停在「全部刪掉」的狀態，下一次 auto-save backend 還是把那些區塊 redact 掉，BG 重抓回來自然空白。本版 snapshot 同時存 `{pages, deletedOrigs}`，restoreSnapshot 還原時一併 restore + 立即 scheduleAutoSave 讓 BG 用還原後狀態重 render。propertiesToInclude 也補上 `_origBbox / _existingSrc / _peFont / _noteText`。
- **下載按鈕按了不下載**：原本靠 `<a href="…" download="…">` anchor，但在某些情境（aut o-save URL 還沒 set / 瀏覽器擴充攔截 / Safari quirks）會失效。改用 fetch blob → `URL.createObjectURL` → 動態 click `<a>` 強制下載，並在 url 還空時跳提示。
- **資產選擇器「上傳新圖片」拖放區無反應**：之前只綁了 `<input type=file>` 的 change，沒處理拖放。本版補上 `dragenter/over/leave/drop` 並 hover 時藍框視覺回饋，把 `dataTransfer.files[0]` 走原本的 `_doImageUpload()` 共用流程上傳。
- 順便：之前部署 tarball 漏拷 `static/js/toast.js / job_progress.js`，rsync `--delete` 把線上版也清掉造成 console 兩條 404；本版補回。

---

## [1.1.88] - 2026-05-02

### 修正（pdf-editor 擷取既有文字後 fade 害人錯亂）

- **擷取自 PDF 的物件（有 `_origBbox`）auto-save 後不再 fade 到 0.01**：之前所有疊加物件（含使用者剛擷取出來、還在編輯中的）都會 fade，使用者以為「我擷取的物件不見了」於是去點空白處新增 → 結果在錯誤位置產生新文字（例如只剩 "333" 在原文字右邊）。本版只 fade 沒有 `_origBbox` 標記的「新增疊加物件」，擷取物件持續可見，使用者連續編輯不被打斷。

---

## [1.1.87] - 2026-05-02

### 修正（pdf-editor 編輯既有文字後預覽顯示不完整）

- **`text:editing:exited` 強制重算 IText 維度 + 觸發 auto-save**：擷取既有文字後在 IText 內直接雙擊編輯（典型情境：「客戶地址」改成「客戶地址測試」），blur 時 Fabric IText 內部 dirty 旗標 / width 沒主動 recompute，scheduleAutoSave 收到的 `o.text` 雖然正確、但 IText 的視覺 width / coords 還停在舊狀態，背景圖 re-render 完後 IText fade 到 0.01 opacity，使用者只看到「殘留視覺」覺得文字不見。本版加 `text:editing:exited` listener，blur 後立即 `_clearCache + initDimensions + setCoords` + 排隊 auto-save，preview 與下載結果一致。

---

## [1.1.86] - 2026-05-02

### 修正（版本號回到 brand 文字右緣）

- v1.1.85 把收起按鈕放進 brand-block 內，導致 brand-block 的 intrinsic width 被撐到包含按鈕，version 文字 right-align 跑到很右邊。本版把按鈕移出 brand-block，brand-block 維持「inline-flex column 包 brand+version」、按鈕變 brand-row 的兄弟（`margin-left:auto` 貼最右）。version 重新對齊 brand 文字右緣，跟收起按鈕無關。

---

## [1.1.85] - 2026-05-02

### 修正（兩個按鈕都不再吃內容空間）

- **收起按鈕 ‹**：之前 `flex-direction: column` 撞既有規則害按鈕單獨占一整行。改用 `.brand-row` flex row（brand 連結 `flex:1` 撐開、按鈕貼右）+ `margin-left: auto`，brand 跟按鈕同列、版本號照常在下面。
- **展開按鈕 ☰**：之前無論放上面還是放左邊都吃 60px 空間。改成左邊緣垂直 tab：22px 寬、80px 高、垂直置中、`writing-mode: vertical-rl` 文字直書，像是抽屜把手。內容區 `padding-left: 24px` 已自然避開，h1 標題從 `left:24px` 開始排，不重疊。

---

## [1.1.84] - 2026-05-02

### 修正（展開按鈕往左不往上，不擠掉內容空間）

- v1.1.83 用 `padding-top:60px` 給 ☰ 按鈕讓位，造成 sidebar 收起時內容整個下移浪費上面空間。改成 `padding: 24px 24px 64px 60px`（左 padding 60px，上 padding 不變）— ☰ 按鈕固定在左邊，內容自然從按鈕右邊開始排，h1 標題不再被往下推。

---

## [1.1.83] - 2026-05-02

### 修正（v1.1.82 兩個按鈕位置撞到字）

- **「收起」按鈕（‹）**：原本 absolute top-right 蓋到 brand 名稱與 v1.1.82 版本字。改放進 brand-block 的 flex row 內，brand 連結 `flex:1` 撐開，按鈕在右端不重疊。
- **「展開」按鈕（☰）**：sidebar 收起後 main 沒留空間給左上的浮動按鈕，蓋到 h1 標題（如「文書轉圖片」）。改：`body.sidebar-collapsed .container.with-sidebar` 加 `padding-top:60px`，按鈕跟標題自然分開。

---

## [1.1.82] - 2026-05-02

### 變更（側邊選單可手動收起 / 展開）

- 之前 sidebar 只有「螢幕 < 900px 時自動隱藏」一種模式，桌機沒辦法暫時收起。本版加兩個按鈕：
  - **收起按鈕**（sidebar header 右上的 `‹`）：把 sidebar 滑出視窗、main 區域占滿全寬。狀態存 `localStorage`，重新開頁仍記得。
  - **展開按鈕**（畫面左上 `☰`）：sidebar 收起時才出現，按一下展開回去。
- 手機版（< 900px）行為不變 — sidebar 預設收起、`☰` 開啟、點導航或背景關閉。

---

## [1.1.81] - 2026-05-01

### 修正（install.ps1 Win11 全套通了）

驗證在 Win11 x64 一行 install 從 0 到健康檢查全綠：service running、`jtdt.cmd` 建好、`.venv/pyvenv.cfg` 存在、ldap3 2.9.1 可 import、healthz `{"ok":true}`。

- **`$ErrorActionPreference = 'Continue'`**：原本 'Stop' 會把 nssm / git / uv 任何寫一行 stderr 當成 fatal error 結束 install.ps1。改 Continue 後仍以 `$LASTEXITCODE` 顯式判斷失敗，但 stderr 寫入不再致死。
- **`uv sync --reinstall`**：之前如果 base managed Python 因為其他安裝有殘留 `__editable__.jt_doc_tools.pth`，新 venv 跑 `uv sync` 會 cache hit 認為「已裝過」只裝 jt-doc-tools 本身、其他 44 個依賴一個都不裝。`--reinstall` 強制全重裝。
- **`uv sync` 不要加 `--python 3.12`**：加了會讓 uv 挑 base managed Python 而非剛建的 .venv，所有 package 跑進 `Roaming\uv\python\Lib\site-packages` 導致 venv 空白。
- **`setup-python.cmd` 純 ASCII + CRLF**：之前含 em-dash UTF-8 字元，cmd.exe 解析時把 byte sequence 當奇怪命令丟錯。

---

## [1.1.80] - 2026-04-30

### 修正（setup-python.cmd 純 ASCII + CRLF）

- 原本 setup-python.cmd 含 em-dash (`—`) UTF-8 字元，cmd.exe 解析時把 byte sequence `e2 80 94` 當成奇怪命令丟錯，setup_python 一進去就死 exit 255。本版重寫成純 ASCII（dash 用 `-`）+ Windows CRLF 行尾。同時 install.ps1 加 debug Write-Output 印 `$InstallDir` / `$setupBat`，方便客戶遇到問題時 attach log 給我們看。

---

## [1.1.79] - 2026-04-30

### 修正（install.ps1 完全棄用 PowerShell 跑 uv，改純 cmd 批次檔）

- v1.1.66~v1.1.78 試了七種寫法都救不了 PowerShell 在 elevated `Start-Process -Verb RunAs` + `*>&1 | Out-File` redirect 環境下對 native command 的詭異行為（Out-Host 吞輸出、`$Args` 是保留字、`Stop` 把 stderr 當 fatal、`-RedirectStandardError` 不可靠等）。本版徹底投降：把 venv 建立 + uv sync + import smoke test 全部寫成純 cmd 批次檔 `setup-python.cmd`，install.ps1 只 `cmd /c` 呼叫它並把 exit code 對應到 Die 訊息。pure cmd shell 沒有 PowerShell 的奇怪行為，輸出穩定可預測。

---

## [1.1.78] - 2026-04-30

### 修正（install.ps1 真正解：`$Args` 是 PowerShell 保留字）

- v1.1.77 的 `Invoke-Uv` 函式 `param([string]$Args, ...)` 用了 PowerShell 自動變數 `$Args` 當 parameter 名，PS 會默默把它當外層 `$args` 吃掉，function body 內 `$Args` 始終為空 → cmd /c 跑了空指令 → 立刻 fall through 到 venv 檢查死掉，且我的 Write-Output 也根本沒執行（因為 PowerShell 在參數綁定時就出錯但靜默吞掉）。本版改名 `$UvArgs` 並用顯式 `-UvArgs/-Label` 命名引數呼叫。

---

## [1.1.77] - 2026-04-30

### 修正（install.ps1 改用 cmd /c 跳脫 PowerShell）

- v1.1.66 起在 elevated `Start-Process -Verb RunAs` + `*>&1 | Out-File` redirect 環境下，PowerShell 的 native-command 處理機制怎麼改都不對：`& $UvExe`、`Start-Process` + `-RedirectStandardError`、`Write-Output`、`Write-Host` 全試過，連最簡單的 `Write-Output "==>"` 都印不到 log file。本版改成把 uv 路徑寫成一個 `.cmd` 批次檔，再用 `cmd /c batPath args 2>&1` 呼叫 — cmd 是純 shell，沒有 PowerShell 的 stderr-as-error 問題，輸出穩定可預測。

---

## [1.1.76] - 2026-04-30

### 修正（install.ps1 inline + Write-Output 取代 Run-Uv 函式）

- v1.1.75 用 nested function `Run-Uv` 包 Start-Process，但 elevated session 的 `*>&1 | Out-File` redirect 對 `Write-Host` 的 information stream 捕捉不穩，連 `==> uv python install 3.12` 開頭訊息都印不出來，使我們完全看不到 install 在哪個步驟死。本版改成 inline 三段呼叫 + `Write-Output`（去 stdout 主流，必被 `*>&1` 捕捉），並印出每段 exit code，方便排查。

---

## [1.1.75] - 2026-04-30

### 修正（install.ps1 改用 Start-Process 隔離 uv）

- **uv 跑在 child process，徹底繞開 PowerShell native-command 詭異 throw**：v1.1.66~v1.1.74 一直在 `& $UvExe sync` 那行死，但任何 EAP / pipe / redirect 設定都救不了 — 真正原因是 PowerShell 在 elevated `Start-Process -Verb RunAs` 啟的 session 對 `&` 呼叫的 native command 寫 stderr 行為極端不可預期。本版改用 `Start-Process -NoNewWindow -Wait -PassThru -RedirectStandardError $tmp` 把 uv 完全隔離成 child process，stdout / stderr 各自寫到 temp file，跑完統一印 log + 看 ExitCode。最後 venv / ldap3 / jtdt.cmd 全部建好。

---

## [1.1.74] - 2026-04-30

### 修正（install.ps1 強制 uv venv 建立 .venv）

- **顯式呼叫 `uv venv` 強制建立 .venv**：v1.1.66 起 install.ps1 在 elevated + *>&1 redirect 環境下，`uv sync` 偶爾不會自動建立 `.venv`，導致後面整套失敗（沒 ldap3 / 沒 jtdt.cmd）。本版加一行 `& uv venv --python 3.12 .venv` 在 sync 之前先把 venv 鋼架建好，再讓 sync 填入依賴。

---

## [1.1.73] - 2026-04-30

### 修正（install.ps1 真正最後一里）

- **不要對 uv 加 pipe / redirect**：v1.1.72 改成 `2>&1 | ForEach-Object { Write-Host $_ }` 反而觸發 uv 偵測 non-tty → 不建 .venv → install 死在「Python venv creation failed」。本版 setup_python 直接呼叫 `& $UvExe sync --python 3.12`，不加 pipe 也不加 redirect；外層 `*>&1 | Out-File` 已會捕捉所有輸出含 stderr。

---

## [1.1.72] - 2026-04-30

### 修正（install.ps1 sterr → fatal 真根因）

- **`$ErrorActionPreference = 'Stop'` 把 uv 寫到 stderr 的訊息變成 fatal**：v1.1.66 起 install.ps1 在 setup_python 階段不管怎麼改都死，logs 結束在「Setting up isolated Python environment」之後；root cause 是 uv 對「Python 3.12 已裝」這類訊息寫 stderr，PowerShell 在 `Stop` 模式下會把任何 stderr 寫入當成 terminating error。本版在 setup_python 段暫時改成 `Continue` 並把 stderr 合併到 stdout，讓 uv 順利跑完，全套 venv / ldap3 / jtdt.cmd 才會建好。
- 跑 `Setup-Python` 後立刻就會 `Install-Cli`，所以 `jtdt.cmd` 也會被建立。

---

## [1.1.71] - 2026-04-30

### 修正（v1.1.66 起一直存在的 Windows 重裝 bug）

- **install.ps1 重裝前必須先停服務**：之前的安裝流程在「cleaning non-bin files」階段嘗試刪掉舊的 `.venv`，但服務若還在跑，`.venv\Scripts\python.exe` 是 file-locked 的，`Remove-Item -ErrorAction SilentlyContinue` 靜默失敗 → 殘留半個 `.venv`（沒 pyvenv.cfg / 沒 site-packages，只有 47KB shim python.exe）→ uv sync 看到「壞掉的 venv」既不重建也不報錯 → 結果 ldap3 沒裝、jt-doc-tools 自動 register 成 1.1.47 editable install。本版在 cleanup 前先 `Stop-Service jt-doc-tools` + 釋放 file handle 等 2 秒，並在 cleanup 後驗 `.venv` 真的不存在才往下走，確保 `Setup-Python` 從乾淨狀態建 venv，並且 `Install-Cli` 一定會跑到、`jtdt.cmd` 一定會被建立。

---

## [1.1.70] - 2026-04-30

### 修正（v1.1.69 配套：Windows install.ps1 卡死）

- **install.ps1 不再因 `Out-Host` 卡死**：v1.1.69 引入的 `& uv python install 3.12 2>&1 | Out-Host` 在以「系統管理員身分」啟動的 elevated PowerShell session 沒有附加 host，pipe 會吞掉輸出 + 可能 hang，導致 install.ps1 在「Setting up isolated Python environment」之後沒任何 log 卡死，最後 venv 沒建好、ldap3 沒裝、jt-doc-tools 自動 register 成 1.1.47 editable install。本版改成不 pipe，直接呼叫 + 手動把 `$LASTEXITCODE` 歸零跳過「already installed」訊號。

---

## [1.1.69] - 2026-04-30

### 修正（v1.1.68 配套：讓舊版客戶也能升級）

- **install.sh 在任何 git 操作前先設 `git config --system --add safe.directory /opt/jt-doc-tools`**：
  v1.1.68 修了「新版 cli.py」的 update flow，但既有客戶用的還是舊版 cli.py — 跑 `sudo jtdt update` 仍會撞 dubious ownership 失敗，重跑一行 install.sh 也會在 `git fetch` 那行死。本版讓 install.sh 先把 install dir 加入 git 系統級白名單，新舊兩版的 update / re-install 都能通過。
- 客戶若已被卡住，重跑一行 install.sh 即可一次解決所有問題（含 ldap3 補裝、cli.py 升 v1.1.68+、git safe.directory 設好）。

---

## [1.1.68] - 2026-04-30

### 修正（嚴重 — 客戶啟用 AD 後鎖死無法登入）

- **uv.lock 漏 `ldap3` → 安裝後 LDAP/AD 認證壞掉**：v1.1.66 之前的 `uv.lock` 沒含 `ldap3`，但 `pyproject.toml` 有；安裝腳本用 `uv sync --frozen` 盲信 lockfile，回傳成功但實際少裝 `ldap3`。客戶啟用 AD 認證後，登入頁顯示「ldap3 套件未安裝；請聯絡管理員」整個系統鎖死。本版重新生成 `uv.lock`（含全部依賴），並把 `install.sh` / `install.ps1` / `jtdt update` 一律改成不用 `--frozen`，最後追加「驗 import」smoke test，少裝任何關鍵 package 就 fail-fast。
- **`sudo jtdt update` 撞 git「dubious ownership」**：Linux install.sh 把 `/opt/jt-doc-tools` chown 給 `jtdt` 服務帳號；`sudo jtdt update` 以 root 跑 `git pull` 時，git 2.35.2+ 會拒絕操作非當前用戶擁有的 repo。本版在 update 流程加 `safe.directory=<root>` 環境變數讓 git 通過，並在 git pull / uv sync 完成後 `chown -R` 回原擁有者。
- **新增 `jtdt auth` 子命令**：當 LDAP/AD 設定錯把自己鎖在外面時，可以用 CLI 緊急復原：
  - `sudo jtdt auth show` — 看目前認證 backend
  - `sudo jtdt auth disable` — 切回未啟用認證
  - `sudo jtdt auth set-local` — 切回本機帳號
  - 配合 `sudo jtdt reset-password jtdt-admin` 可重設管理員密碼

### 影響範圍

- v1.1.50 ~ v1.1.67 安裝 / 升級的所有 Linux + Windows 環境，啟用 LDAP / AD 認證會壞。
- 已啟用認證鎖在外面的客戶，跑 `sudo jtdt auth disable` 即可解封。

### 驗證
- 在 Ubuntu 24.04 跑 `sudo bash install.sh` → 安裝後 `python -c "import ldap3"` 通過。
- 啟用 AD 認證 → 登入頁不再顯示「ldap3 套件未安裝」。
- `sudo jtdt update` 從 v1.1.65 升 v1.1.68 不再撞 dubious ownership。
- `sudo jtdt auth disable` 切回 off backend，重啟後登入頁消失。

---

## [1.1.67] - 2026-04-30

### 變更（「我的帳號」對話框排版）

- **「我的帳號」改用 grid 兩欄對齊**：原本帳號／顯示名稱／認證來源／角色／可用工具五個欄位的 label 寬度不一，後面的值看起來歪一邊。改成 `display:grid; grid-template-columns:max-content 1fr` 兩欄對齊，label 統一靠右、值統一靠左、上下 row gap 一致。
- 純樣式微調，無功能改動。

---

## [1.1.66] - 2026-04-30

### 修正（Windows ARM64 浮水印中文方框）

- **`pdf-watermark` Windows CJK fallback 補 simsun**：v1.1.60 加了 CJK glyph 偵測，但 fallback 字型清單只列 `msjh.ttc / mingliu.ttc`，這兩個是繁中 Windows 才會內建的 Microsoft JhengHei / 細明體；簡中或國際版（含 Win11 ARM64）只有 `simsun.ttc`，結果仍 fall-through 到 Arial 變方框。本版把 `simsun.ttc / simhei.ttf / msyh.ttc / msyhbd.ttc` 一併加進 regular + bold 清單。
- **影響範圍**：浮水印工具用「文字模式」打中文時。
- **驗證**：在 Win11 ARM64（無 msjh）跑 `text-png?text=機密文件 RESTRICTED` 取得正常字體 PNG，不再 .notdef tofu。

---

## [1.1.65] - 2026-04-29

### 變更（text-diff 加拖檔）

- **textarea 支援拖檔**：把 `.txt / .csv / .md / .log / .json / .yaml / .conf / .env / 程式碼` 等任何文字檔拖到舊版 / 新版輸入框，FileReader 用 UTF-8 讀進來自動填入；原本「貼純文字」的用法不變。
- **不用 extension 白名單**：「文字檔」是內容問題不是檔名問題（`.env` / `.gitignore` / 沒副檔名的 conf 都很常見）。改用 ① 1 MB 大小上限（同 backend）+ ② 看內容前 8 KB 有沒有 NUL byte 偵測二進位檔，有就拒絕並顯示提示。
- **拖入時視覺提示**：textarea 顯示藍色虛框 + 淺藍背景；放下後 meta 列印出檔名 (`已載入 X.md`)，太大或二進位則紅字錯誤。
- 純前端改動，backend 跟 API 不動。

---

## [1.1.64] - 2026-04-29

### 修正（diff 對齊 + emoji → icon）

- **左右兩欄文字對齊壞掉**：text-diff / doc-diff 兩邊原本是獨立的 `.df-col`，當一邊文字長到換行（visual wrap）時，另一邊的對應行高度沒跟著漲，後面整段就垂直歪掉。改用「整個 diff 是一張 2-column grid，每行 = grid 的一個 row」結構，row height 自動取 max(left, right)，wrap 後仍對齊。
- **emoji 換成 SVG icon**：`memo / page / swap` 三個 emoji 改成 icon macro `edit / page / swap`。`swap` 是新加的 icon，雙箭頭 left↔right。

---

## [1.1.63] - 2026-04-29

### 變更（doc-diff 加字數統計 + 新工具 text-diff）

- **doc-diff 統計區塊新增「字數差異」**：除原本的頁數 / 行數統計，再加一組 — 舊版總字、新版總字、差 ±N 字、新增字、刪除字、修改字。`replace` opcode 內部再跑一輪 char-level SequenceMatcher 算 edit distance，所以 1 字微調不會跟整段重寫顯示同一數字。
- **新工具：文字差異比對 `text-diff`**：直接貼兩塊文字立即比對，不用上傳檔案。給 log 片段、code 片段、改稿前後段落的快速 diff 用。共用 doc-diff 的 SequenceMatcher pipeline 確保結果一致；行數 + 字數雙重統計；含交換左右、清空、行數即時計數；單側 1 MiB 上限。
- **工具總數 26 → 27**：text-diff 列入 default-user 預設角色（非 Office 工具，不需 OxOffice）。
- 新增 8 條 pytest（text-diff 完整 endpoint + 邊界）。

---

## [1.1.62] - 2026-04-29

### 修正（v1.1.61 改名 doc-diff 留下的 4 個漏網之雷）

- **template JS 還寫死 `/tools/pdf-diff/compare`**：使用者按「開始比對」直接 404 `{"detail":"Not Found"}`。改成 `/tools/doc-diff/compare`。
- **`app/core/roles.py` 內建角色定義裡還是 `pdf-diff`**：原本只改了 metadata、route、JS，role seed 表沒改，**新使用者建出來的角色完全沒有 doc-diff 權限**。default-user / legal-sec 兩個內建角色都修正。
- **DB migration 補上**：v3 `_m3_rename_pdf_diff_to_doc_diff` — 既有安裝升級時自動把 `role_perms` / `subject_perms` 表內 `pdf-diff` 改成 `doc-diff`。沒這條 migration 老用戶升級後會**失去工具存取權**（admin-edited 的角色也保住）。`INSERT OR IGNORE … DELETE` 寫法保證 idempotent。
- **redirect 改 308 + 包所有方法 + 包子路徑**：原本 301 + 只接 GET，POST 到 `/tools/pdf-diff/compare` 的舊 API 客戶端會 404。改成 308 + `api_route` + `{rest:path}` wildcard，整個 `/tools/pdf-diff/*` 全部轉。308 不像 301 會把 POST 降級成 GET。
- **`CLAUDE.md` 法務資安角色表 + `TEST_PLAN.md` 標題改 doc-diff**。
- 新增 2 條 pytest（migration 改名 / migration idempotent 含預先存在 doc-diff 的情境）。

---

## [1.1.61] - 2026-04-29

### 變更（PDF 差異比對 → 文件差異比對，加 Office / ODF 支援）

- **`pdf-diff` 重新命名為 `doc-diff`，顯示名稱「文件差異比對」**：因為現在不只能比 PDF。route id 跟著改 `/tools/pdf-diff` → `/tools/doc-diff`。
- **接受 Office / ODF 檔案**：除 PDF 外也吃 `.doc / .docx / .xls / .xlsx / .ppt / .pptx / .odt / .ods / .odp`。非 PDF 檔會在比對前先用 OxOffice / LibreOffice 轉成 PDF（共用 `office_to_pdf` 既有 helper）。失敗會 500 + 「找不到 Office 引擎」訊息。
- **舊網址 301 redirect**：`/tools/pdf-diff` 跟 `/tools/pdf-diff/` 自動轉新網址，舊書籤 / 舊 API 呼叫不會 404。
- **template 上傳元件 accept 屬性更新**：`.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.odt,.ods,.odp` 一次列上去，瀏覽器選檔器自動過濾。
- **搜尋關鍵字補上 Office / Word / Excel / PowerPoint / ODF**：sidebar 搜尋這些字也找得到此工具。
- 新增 5 條 pytest（PDF×PDF / Office×PDF / 不支援副檔名 / 舊網址 redirect / 新名稱出現於頁面）。

---

## [1.1.60] - 2026-04-29

### 修正（三個 Windows 端 bug）

- **`jtdt uninstall --purge` 結尾出現「找不到批次檔。」**：原本 `shutil.rmtree(InstallDir)` 把 `jtdt.cmd` 當場砍掉，但 cmd.exe 還在執行那個 .cmd，下一行讀不到就吐錯。功能其實成功，只是噪音。改成 Windows 上發 detached cleanup 子行程（`timeout /t 2` 後再 `rd /s /q`），讓 cmd.exe 先正常結束。
- **浮水印中文顯示成方框（Windows）**：原本 `_load_font` 不檢查字型是否真的有 CJK glyph，使用者選 Helvetica / Arial 之類純英文字型畫中文 watermark 時 Pillow 直接畫 .notdef（豆腐）。新增 `_has_cjk()` + `_font_covers_cjk()` 檢查；CJK 文字會自動 fallback 到 CJK 字型清單（Windows: msjh / mingliu）。
- **Ghostscript 提示語太像錯誤**：「本機未偵測到 Ghostscript — Mac brew install...」字面看起來像壞了。改成「**選用：**裝了 Ghostscript 可再多擠 20–50% 壓縮率（內建 PyMuPDF 已可用，本選項非必要）」+ 直接附 Windows 下載連結 `ghostscript.com/releases/gsdnld.html`。
- 新增 watermark CJK fallback 4 條 pytest 防回歸。

---

## [1.1.59] - 2026-04-28

### 修正（pdf-compress 把透明背景變黑）

- **PDF 壓縮會把透明 PNG 變黑底的 bug**：原本 `fitz.Pixmap(doc, xref)` 只抓到圖片的 RGB base，**不會帶 PDF 內獨立 xref 的 SMask（alpha mask）**。所以 `pix.alpha` 是 0、被當成不透明圖重編成 JPEG → 透明區整個變黑。更糟的是 `replace_image` 只換 base，原本的 SMask 還在繼續被 reader 套用，size 跟新圖不一定 match。
- **修法**：在 recompress 前用 `doc.extract_image(xref)` 偵測 SMask，有的就 **直接跳過** 那張圖（保住資料完整性，不冒險合成 RGBA + 改寫 SMask 引用）。純 RGB 圖照壓不受影響。
- **stats 多回 `skipped_smask` 欄位**：admin 與 UI 可看到「有幾張因為含透明所以沒壓」。
- 新增 3 條 pytest 防回歸（含透明 PNG 的 PDF 跑 compress + analyze + 驗 SMask 仍存活）。

---

## [1.1.58] - 2026-04-28

### 變更（安裝腳本網路 fail-fast）

- **Windows 一行安裝指令改用 `Invoke-WebRequest`**：原本 `(New-Object Net.WebClient).DownloadFile(...)` 沒預設 timeout，網路不通會卡 2 分鐘以上才出錯（VPN 沒開連到內網的情境踩到）；新版 `Invoke-WebRequest -TimeoutSec 15` 加 try/catch，連不上 15 秒內紅字喊「下載安裝腳本失敗」+ 故障排除提示（VPN？防火牆？DNS？）。
- **`install.ps1` / `install.sh` 開頭加網路 preflight**：跑任何下載動作之前先 HEAD `github.com` / `cdn.jsdelivr.net` / `astral.sh` 三個 host，全失敗就在 8 秒內 die，避免後面 uv / python / git tarball / OxOffice 各自慢慢 timeout。
- **修復 `docs/index.html` 內 `<pre><code>` 區塊被全形標點轉換誤傷**：先前的全形化 script 沒避開 `<pre>` / `<code>`，把安裝指令的 `;` `()` 都吃掉了；補上 reverse pass。
- **修復 markdown link 語法 `](url)` 的 `()` 被誤轉成全形**：`[Keep a Changelog]（https://...)` 這類連結還原成半形。

---

## [1.1.57] - 2026-04-28

### 變更（pdf-annotations-strip 加上註解明細預覽）

- **「註解清除」上傳後立即列出每一條註解**：跟「註解整理」一樣顯示頁碼、作者、類型、內容與該頁縮圖；點縮圖可放大。原本只顯示總數 / 頁數兩個數字，使用者要刪之前完全看不到內容。
- **紅底高亮標記「會被刪掉的註解」**：模式 = 全部刪除時整份標紅；模式 = 依篩選刪除時跟著勾選的類型 / 作者即時更新，下手前一目了然。
- **改走 analyze + strip 兩段式**：`/analyze` 會把 PDF 暫存 + 寫 sidecar JSON，`/strip` 用 `upload_id` 取快取，不需要重新 upload；和「註解整理」採同一 pattern。
- **按鈕處理中 disable + spinner**：與其他長操作按鈕一致。
- **README + 文案標點全形化**：README、CHANGELOG 中文相鄰的逗號 / 句號 / 括號統一全形（先前漏網的 `,` `(` `)` 約 30 處）。
- **API endpoint 維持原行為**：`POST /api/pdf-annotations-strip` 公開 API 仍是單次 upload + 直接回 PDF，對外接口不破壞。

---

## [1.1.56] - 2026-04-28

### 變更（pdf-annotations-flatten 改名 + 預覽 + spinner）

- **「註解固定化」改名為「註解平面化」**：與 Adobe Acrobat 繁中正式翻譯一致（「平面化圖層 / 平面化透明度」）。`固定化` 太像直譯日文/中文不夠在地.route id `pdf-annotations-flatten` 不變。
- **平面化結果預覽**：`/flatten` 不再立刻回傳 PDF，而是回 `{baked_uid, page_count, baked_count}`；UI 顯示每頁縮圖（lazy-load via `/baked-preview/{uid}/{page}`），點縮圖開 lightbox 看大圖；確認後才按下「下載平面化後的 PDF」呼叫 `/baked-download/{uid}`。
- **按鈕處理中 disable + spinner**：「執行平面化」「下載」按鈕在處理時變成 disabled、顯示旋轉的 spinner、文字改成「處理中… / 下載中…」；完成後還原。
- **API endpoint 維持原行為**：`POST /api/pdf-annotations-flatten` 公開 API 仍直接回 PDF（不走預覽流程），對外接口不破壞。

---

## [1.1.55] - 2026-04-28

### 變更（pdf-annotations 大改 + 網站文案修正）

- **每頁預覽縮圖**：註解明細列表每筆左側顯示該頁 PNG 縮圖（lazy-load，PyMuPDF 渲染時自動把註解烤上去），點縮圖開 lightbox 看完整大圖。靠新 endpoint `GET /preview/{upload_id}/{page}` 按需渲染。
- **下載速度大幅提升**：原本每按一次「下載 CSV / JSON / 待辦」都要重新上傳 + 重跑全文 highlight text recovery（大 PDF 等很久）。改成 analyze 階段把分析結果寫成 sidecar JSON（`annot_{uid}_data.json`），下載時按 `upload_id` 直接取快取，秒回。
- **修 export 端點 signature 漏接 bug**：`_save_upload` 從 2-tuple 改成 3-tuple 時，4 個 export 端點的呼叫端沒同步更新，造成「按下載沒反應」。
- **網站「伺服器模式」加 `jtdt bind 0.0.0.0` 說明**：之前面板只說 127.0.0.1，沒交代怎麼對外開放。
- **網站 / README 區分 `sudo`（Linux/macOS）vs「以系統管理員身分執行」（Windows）**：原本一律寫 `sudo jtdt update / uninstall`，Windows 沒 `sudo`。
- **網站移除 Windows「⚠ 尚未完整測試」徽章**：客戶端 + 內部 Win11 x64 測試機多次驗證 OK，可拿掉警告。

---

## [1.1.54] - 2026-04-28

### 變更（install.ps1 借鑑客戶 self-fix 重構）

- **`git fetch` / `git reset` 加 `$LASTEXITCODE` 檢查**：升級流程任一指令失敗會立刻 `Die`，錯誤訊息更清楚而不是默默繼續到下一步才崩。
- **tarball fallback 流程少一次拷貝**：原本「解壓 → 複製到 stage → 再 merge 到 InstallDir」兩跳，改成「解壓 → 直接複製到 InstallDir」一跳。
- **auto-clean 條件移除**：原本只在「有 bin 以外的子項」才清，現在無條件清非 bin 檔；本來 gate 條件就是冗餘（無項目時也是 no-op）。
- 已在內部 Win11 x64 測試機重現「`bin/` 既存、無 .git」失敗情境並驗證新版可成功安裝。

---

## [1.1.53] - 2026-04-27

### 變更（UI 樣式 + 文字規範）

- **網站連結 / 按鈕 cursor 修正**：`<a href>` 與 button hover 不會變成手指的問題，在 `style.css` 加 `a[href], a[href] * { cursor: pointer }` 與 `button { cursor: pointer }` 明確規則。
- **中文字旁的逗號統一改全形「，」**：README、CHANGELOG、網站、UI templates、Python docstring 等共 27 個檔案，84 處半形 `,` （CJK 旁邊）替換成全形「，」，符合台灣繁體出版業標準。結構性的 CSV / URL / code syntax 維持半形不動。

---

## [1.1.52] - 2026-04-27

### 新增（三個 PDF 註解相關工具）

- **註解整理 `pdf-annotations`**（內容擷取）：擷取 PDF 中所有註解（螢光筆 / 文字註解 / 圖章 / 自由文字 / 手繪 / 底線 / 刪除線 / 檔案附件等），提供三種輸出模式：
  - **完整清單** — CSV / JSON，含頁碼、類型、作者、subject、內容、建立 / 修改時間、座標
  - **審閱報告** — Markdown，可依頁碼 / 作者 / 類型分組（給主管 / 客戶 / 法務看）
  - **待辦清單** — Markdown checkbox 或 CSV（status / page / todo / assignee / priority / type / notes）
  - 螢光筆 / 底線等 content 通常為空，本工具會用 quad rect 從原文 reverse 出實際標註的文字
  - 類型 / 作者 chip 篩選，可即時 redraw 預覽列表
- **註解清除 `pdf-annotations-strip`**（資安處理）：刪除 PDF 中的註解。兩種模式 — 全部刪除或依類型 / 作者篩選刪除；輸出乾淨副本。
- **註解平面化 `pdf-annotations-flatten`**（檔案編輯）：用 PyMuPDF `doc.bake(annots=True, widgets=False)` 把註解燒進頁面內容流，收件方無法移除或編輯。表單欄位 （AcroForm widgets） 保留可填。
- 共 32 條 pytest：類型 / 作者篩選、CJK 檔名、empty PDF、API endpoint、bake 後 annot count = 0 等。
- 新加 `sticky-note` 與 `layers` 兩個 SVG icon。

---

## [1.1.51] - 2026-04-27

### 變更（pdf-wordcount UI 細修）

- **統計總覽 8 張卡片各自配色**：之前全部一樣的灰藍很單調，改成 8 個獨立色系 （藍 / 青 / 綠 / 紫 / 橙 / 粉 / 黃 / 紅），一眼分得出每類數據。
- **「段落 / 句子」值不再被斷行**：`349 / 1,526` 之類的值現在 `white-space: nowrap`，卡片寬度不夠時用省略號而不是換行。同時把 grid `minmax(140px → 160px)` 拓寬基本欄位寬度。
- **上傳區與統計總覽間距修正**：`#wcResults` wrapper 把兩個 `.panel` 切斷了 sibling 鏈，全域 `.panel + .panel` 規則失效；加 explicit `margin-top` 修補。

---

## [1.1.50] - 2026-04-27

### 變更（pdf-wordcount UX 改版）

- **高頻詞改成三欄並列**：原本中文單字 / 中文雙字 / 英文 是 tab 切換，使用者抱怨切換不方便。改成三個獨立卡片並排，一次看完三類；螢幕窄時自動 collapse 成 2 欄 / 1 欄。每類各自配色：中文單字藍、中文雙字綠、英文紫，視覺好區分。
- **移除「累積字數曲線」圖**：大多 PDF 每頁字數差不多，累積曲線就是一條斜直線，跟「每頁字數直條圖」資訊重複，沒提供新洞察。空間讓給三個高頻詞圖。
- **空態提示**：純英文 PDF 會在中文卡片顯示「（此 PDF 無中文）」；純中文 PDF 在英文卡片顯示「（此 PDF 無英文）」，而非空白圖。

---

## [1.1.49] - 2026-04-27

### 新增（pdf-wordcount 字數統計工具）

- **新工具：字數統計**（`/tools/pdf-wordcount/`，分類為「內容擷取」）。上傳 PDF 即得：總頁數、總字數、CJK 中文字、英文 word、字元含/不含空白、段落、句子、平均每頁字數、平均句長、預估閱讀時間（中 300 字/分、英 200 word/分）。
- **四張精緻互動圖表**：每頁字數直條圖（漸層 + hover tooltip）、字元類型環圈圖（CJK / 英文 / 數字 / 標點 / 空白 / 其他）、Top 20 高頻詞水平條圖（中文單字 / 中文雙字 bigram / 英文三種模式可切換，英文有 stopwords 過濾）、累積字數面積線圖。全部 inline SVG 自繪，零依賴 / air-gap 友善。
- **匯出**：每頁明細 CSV（UTF-8 BOM，Excel 友善）、完整 JSON、Markdown 報表。
- **公開 API endpoint**：`POST /tools/pdf-wordcount/api/pdf-wordcount` 回 JSON，符合「所有功能必須有 API」規矩。
- **掃描檔友善提示**：偵測無文字層 PDF 時顯示 banner 提示先做 OCR。
- **測試**：14 條 pytest 案例（分類器/字數統計/句子切分/閱讀時間/詞頻 stopwords + bigram / 4 endpoint / CJK 檔名 RFC 5987）。

### 文件

- **README + 介紹網站新增 Office 引擎相依說明**：標 🔧 的工具（文書轉 PDF / 文書轉圖片 / 表單自動填寫 / 文件去識別化 / 擷取文字）需要 OxOffice 或 LibreOffice；其餘 17 個工具只處理 PDF，不需 Office 引擎。安裝腳本本來就會自動偵測 / 補裝 OxOffice，但之前文件沒寫清楚哪些工具會用到。

### 修正

- **`app/tools/__init__.py` 被誤覆蓋導致 linux 服務無法啟動**：之前 deploy 用 `cp -r app/tools/pdf_metadata/ /dest/app/tools/` 模式，結尾 `/` 讓 cp 把 pdf_metadata 自己的 `__init__.py` 倒進 `app/tools/__init__.py`，變成 `from ..base import` 指向不存在的 `app/base`，服務無法啟動。修復檔案 + 加 memory 規則永遠不再用該模式。
- **Windows 安裝腳本： 已存在 `bin/` 子目錄時 `git clone` 失敗**：install.ps1 在已裝 uv/nssm 後 `bin/` 已存在，但 `git clone` 要求目標必須是空目錄，導致 `fatal: destination path ... already exists and is not an empty directory` + 後續 `uv sync` 找不到 `pyproject.toml`。改成 clone 到 temp 目錄再合併進 `$InstallDir`，保留 `bin/`。

---

## [1.1.48] - 2026-04-27

### 新增（pdf-stamp 編輯模式分頁預覽）

- **編輯模式現可切換頁面**：原本只顯示第一頁，多頁 PDF 中無法驗證印章位置在每頁是否合適。現在加上 `‹ 上一頁 / 下一頁 ›` 按鈕，背景換成所選頁面的實際內容；可用左右鍵切換。印章位置仍是統一套用（per page_mode 設定），切頁只是換背景驗證。
- **後端**：`/preview` 多回 `page_count` 與 `pages_dims`；新增 `GET /preview-bg/{upload_id}/{page_idx}` lazily render 指定頁背景。
- **異質頁面尺寸**：切頁時 editor 的 paper 尺寸會跟著該頁實際 mm 尺寸更新（混合直橫向 PDF 的位置才會對）。

### 文件

- **Windows 安裝腳本全 ASCII 化**：之前 install.ps1 含中文字串，PS 5.1 在系統 codepage 不是 UTF-8 的 Windows 上會 mangle 編碼或印 BOM 警告（無論加不加 BOM 都會出問題）。改成純英文後在 cp950 / cp936 / cp932 系統都不會有任何 parser 警告。
- **Windows 安裝指令改用 jsdelivr CDN**：GitHub raw 的 Fastly cache 不認 query string 當 cache key，腳本更新後最久要等 5 分鐘才生效。jsdelivr 反應快得多。
- README + 介紹網站新增**免責聲明**段：AS IS、不承擔資料 / 商業損失、個資法 / 營業秘密法合規責任在使用者、LLM 啟用後資料傳輸風險自負、輸出僅供輔助參考、與 Adobe / Microsoft / OSSII / TheDocumentFoundation 無附屬關係。

---

## [1.1.47] - 2026-04-26

### 修正

- **擷取圖片卡片左上勾選 / 右上下載按鈕看不清楚**：之前白底淺紫邊在綠色 picked halo 上太淡，幾乎隱形。改成深底高對比 — 勾選預設白底深灰邊，picked 後變實心綠 + 白勾；下載按鈕改深色 （#0f172a） 背景 + 白色 icon，hover 變紫色.z-index:2 確保穩定在最上層。

---

## [1.1.46] - 2026-04-26

### 變更（pdf-stamp 合成模式多頁切換）

- **預覽從垂直堆疊改成單頁切換式**：頁面太多時垂直堆疊難看（捲半天），改成「單頁顯示 + ‹ 上一頁 / 下一頁 › + 鍵盤左右鍵切換」。caption 標明「第 N / 共 X 頁」+「已蓋章 / 此頁未蓋章」。
- **切換模式時保留當前頁碼**：如果目前看的頁仍存在就保留，否則跳到第一個有蓋章的頁（比固定回第 1 頁更實用）。
- **強化 override 同步**：refreshSim（） 每次都拿 `editor.getValue()` 最新值送給後端，注解清楚說明為何不快取。

---

## [1.1.45] - 2026-04-26

### 變更（剩餘工具全切換到 fu.upload）

- 把以下 17 個工具的 `fetch(url, {method:'POST', body:fd})` 替換為 `fu.upload(url, fd)`：aes_zip、doc_deident、office_to_pdf、pdf_attachments、pdf_compress、pdf_decrypt、pdf_diff、pdf_editor、pdf_encrypt、pdf_fill、pdf_hidden_scan、pdf_merge、pdf_metadata、pdf_nup、pdf_pageno （3 處）、pdf_pages （2 處）、pdf_rotate （2 處）、pdf_split、pdf_stamp （3 處）、pdf_watermark （3 處）。
- 每個都帶上對應 `processingLabel`（「排隊加密中…」「掃描附件中…」「產生預覽中…」等），上傳階段顯示真實 byte 進度條，100% 後切到紫藍 stripes 動畫表示「伺服器處理中…」。
- 至此 22 個工具的上傳流程全部有真實上傳進度 + 處理中視覺回饋。

---

## [1.1.44] - 2026-04-26

### 新增（共用上傳進度 helper）

- **`fu.upload(url, fd)` 加進 `FileUpload` class**：自動在 drop-zone 底部 render 進度條 overlay （label + bar + %），上傳階段顯示 byte-level 進度，100% 後切換到 indeterminate 紫藍 stripes 動畫表示「伺服器處理中…」。
- **CSS `.fu-progress`**：進度條 overlay 樣式（白底 + 圓角 + label/bar/% 三段）+ `jtdt-stripes` 動畫。
- **pdf-extract-text 改用 `fu.upload(...)`** 取代 fetch — 上傳大 PDF 看得到實際 byte 進度。
- **pdf-extract-images 改用 `fu.upload(...)`**。
- 其餘 19 個工具的 fetch 切換為 `fu.upload` 是 mechanical 一行替換（`fetch(url, {method:'POST', body:fd})` → `fu.upload(url, fd)`），下個 batch 補。
- pdf-to-image 因為已有客製 progress UI，繼續用獨立 `window.uploadWithProgress` 不動。

---

## [1.1.43] - 2026-04-26

### 新增（上傳檔案記錄頁）

- **新設定頁 `/admin/uploads`**：列出所有透過工具上傳的檔案 — 從 `audit_events` 表 SELECT `event_type='tool_invoke' AND details_json LIKE '%filename%'` 過濾出來（不另外建表）。
- **欄位**：時間 （yyyy/MM/dd HH:mm:ss） / 使用者 / IP / 工具 （pill） / 檔名 （icon + action） / 大小 （KB/MB/GB human-formatted， 右對齊） / 狀態 （HTTP code 著色：2xx 綠 / 4xx-5xx 紅）。
- **篩選**：使用者下拉、工具下拉、檔名包含關鍵字、起訖時間範圍。共筆數 + 本頁總大小顯示。
- **保留**：跟稽核記錄共用 `audit_events` 表，90 天自動清除（在「檔案保留 / 清理」可調）。
- Sidebar 加 nav 項目「上傳檔案記錄」（icon=upload，需 auth）。

至此 4 大要求 （#1-#4） 中：
  - #1 ✓ 上傳檔案清單頁
  - #2 ✓ 全部工具 thread pool 改完
  - #3 ✓ middleware 自動撈 filename
  - #4 部分（pdf-to-image 已用真實 upload %），其他工具可直接套 `window.uploadWithProgress` helper（共用 helper 已上）

---

## [1.1.42] - 2026-04-26

### 修正（thread pool 第三批 — sync block 全部清掉）

- **pdf_editor / load**：每頁 `pdf_preview.render_page_png` 移到 thread
- **pdf_editor / replace-all-fonts**：抽出 `_replace_all_fonts_sync()` 模組級函式，整段 redact + re-insert + save + re-render 移到 thread
- **pdf_metadata / clean**：metadata + XMP + TOC + annotation/widget 清除 + save 移到 thread
- **pdf_hidden_scan / clean**：抽出 `_clean_sync()`，JS / 嵌入檔 / 連結 / 隱藏文字 redaction 移到 thread
- **aes_zip / submit**：先 async 讀檔，AES + LZMA 加密寫入 zip 移到 thread

加上之前（v1.1.29 / v1.1.30 / v1.1.31 / v1.1.41 / 本版），現在 22 個工具裡所有會吃 CPU 的端點都不再 block event loop。剩下用 BackgroundJob 的（pdf_fill / pdf_stamp / pdf_watermark / pdf_compress / pdf_encrypt / pdf_decrypt / office_to_pdf / pdf_merge / pdf_split / pdf_rotate / pdf_pages / pdf_pageno）本來就跑在 worker thread 不需改。

---

## [1.1.41] - 2026-04-26

### 修正（thread pool 第二批）

- **pdf_attachments / scan**：`fitz.open` + `embfile_names` 移到 `asyncio.to_thread`
- **pdf_diff / compare**：兩份 PDF 開啟 + 全頁 diff 計算移到 thread
- **pdf_hidden_scan / scan**：JS / 嵌入檔 / 隱藏內容掃描移到 thread
- **pdf_nup / preview， generate**：`impose()` 整段（PyMuPDF 排版）移到 thread

剩 pdf_editor / pdf_metadata/clean / pdf_hidden_scan/clean / aes_zip 還沒包，下次 batch。

### README 整體更新

- **github/README.md 簡介改寫**：22 個工具完整列出 + 多人 / 企業環境段落（認證、RBAC、稽核、Log 轉發、檔案保留、API tokens、字型管理）
- **github/README.md 隱私段加 audit / Log forward**
- **README.md（root）功能總覽改寫**：跟 github/README.md 一致；補上 PDF 編輯器 / N-up / 文件去識別化 / 加密 / Metadata 清除 / 隱藏內容掃描 / 差異比對 / AES Zip 等之前漏掉的工具，加企業版段落，refs 指向 github/README.md
- **設定檔位置 table 更新**：補上 stamp/watermark history、auth.sqlite、audit.sqlite、auth_settings.json、api_tokens.json、fonts/
- **「合併」→「檔案合併」** 同步到鍵盤搜尋範例
- 全文確認無中國用語（圖像/軟件/字體/打印 等已無）

---

## [1.1.40] - 2026-04-26

### 新增

- **Filename middleware（自動）**：新加 `_capture_upload_filename` middleware 攔截所有 `/tools/*` 的 multipart POST，sniff 前 16KB 找 `filename="..."`，自動塞 `request.state.upload_filename` / `upload_filenames` / `upload_count`。所有 19 個有 upload 的工具一次受惠，audit / GELF / syslog message 都會帶上實際檔名，不需各自改 router.content-length > 500MB 跳過避免吞 RAM。
- 之前 v1.1.39 在 pdf-to-image / pdf-extract-text / pdf-extract-images 手動加的 `request.state.upload_filename = ...` 還在當 fallback，跟 middleware 並存無衝突。

---

## [1.1.39] - 2026-04-26

### 新增（稽核 / Log 轉發包含上傳檔名）

- **`tool_invoke` audit event 加入 `filename` 欄位**：
  - Auth middleware 改成 handler 跑完才 log（之前是跑前 log），這樣 handler 可以把 `request.state.upload_filename = file.filename` 透過 request.state 傳給 middleware。
  - 新增 status_code 也一起記。
  - pdf-to-image / pdf-extract-text / pdf-extract-images 三個主要上傳工具已加 annotation。其他工具陸續補（middleware 對未 annotate 的 handler 完全相容，只是少了 filename）。
- 結果：Graylog / Splunk 收到的 GELF / syslog message 的 `full_message` 現在會看到 `"filename": "X.pdf"`，admin 一眼知道誰對哪個檔做了什麼。

---

## [1.1.38] - 2026-04-26

### 變更（稽核記錄頁）

- **欄位寬度用 `<colgroup>` 固定**：時間 160 / 使用者 110 / IP 120 / 事件 140 / 目標 160 / 詳細吃剩餘。原本用 `table-layout:fixed` 但只有時間欄定寬，其他欄等分擠在一起，導致時間欄文字溢出蓋到使用者欄。
- **時間格式改成 `yyyy/MM/dd HH:mm:ss`**：原本用 `toLocaleString('zh-TW')` 出來是「2026/4/26 下午9:23:45」（12 小時、無零補位）。改 client-side 手寫 `pad()` 強制 24 小時 + 零補位 + monospace 字型對齊。
- **JSON 詳細展開後格式化 + 語法上色**：原本是 server 寫一行 raw JSON，現在 client-side `JSON.parse + JSON.stringify(obj, null, 2)` 重新縮排，並用小 regex 上色（key 淺藍 / 字串綠 / 數字琥珀 / bool 粉 / null 灰斜體），背景改深色（`#0f172a`）對比明顯。

---

## [1.1.37] - 2026-04-26

### 變更

- **「高清」→「高畫質」**：「高清」是中國用語，台灣 HD 用「高畫質」.pdf-to-image DPI 200 預設選項的副標改為「螢幕高畫質 · 預設」。Taiwan terminology memory 補一條。

---

## [1.1.36] - 2026-04-26

### 變更

- **pdf-to-image DPI 選擇器改用 option-card 卡片版面**：跟轉向 / 多頁合併等其他工具一致 — icon + 大數字 + 副標說明。從一行擠成 5 個的 radio chips 改成 5 張獨立卡片。

---

## [1.1.35] - 2026-04-26

### 新增（pdf-to-image 大改版）

- **DPI 解析度可選**：5 段預設（100 草稿 / 150 螢幕一般 / **200 螢幕高清預設** / 300 印刷 / 400 高 DPI 印刷）。後端 clamp 到 72-600，避免 runaway 記憶體。
- **真正的上傳進度**：fetch 不支援 upload progress event，改用新的 `window.uploadWithProgress()`（XHR-based，回傳 fetch-like Response wrapper）。上傳階段顯示「上傳中… 12.3 MB / 50.1 MB」+ 真實 % 進度條。
- **上傳 100% 後切到 indeterminate 條紋動畫**：因為後端轉檔還在跑（asyncio.to_thread），但目前沒 stream progress channel，UI 標示「伺服器轉檔中…（50.1 MB）」+ 紫藍漸層 stripes 動畫，比靜止 spinner 明顯很多。
- **顯示每張圖大小 + 總計**：每張卡片顯示「第 N 頁 · WxH · 1.2 MB」，標題區顯示「預覽（10 頁，總 12.5 MB）」，下載 ZIP 按鈕顯示「下載全部 ZIP（10 頁 · 約 12.5 MB）」。
- 後端 `/convert` 接 `dpi` form field、return `size_bytes` per page + `total_bytes`。

### 共用 helper

- **`window.uploadWithProgress(url, formData, onProgress)`** 加進 `static/js/file_upload.js`：所有未來工具都可一行切換到「真實上傳進度」模式，不必各自寫 XHR。

---

## [1.1.34] - 2026-04-26

### 修正

- **pdf-to-image 完整重寫 inline script**：
  - 整段包進 IIFE 隔離 scope，不再 leak `const $` 到 global
  - 所有元素 ID 改 `p2i*` 前綴：`#status` → `#p2iStatus`、`#grid` → `#p2iGrid`、`#result-panel` → `#p2iResultPanel`、`#pageCount` → `#p2iPageCount`、`#btnDownload` → `#p2iBtnDownload`。原本 `#status` / `#grid` 太通用容易跟未來 base layout id 撞
  - 用字串拼接取代 template literal 避開 Jinja `{{ }}` 跟 backtick 互動的潛在風險
  - 確保 `new FileUpload()` 是腳本第一件事（在 grid click handler 之前），就算後續任何 listener 註冊失敗也不影響上傳功能
  - 加上 `console.log('[p2i] FileUpload bound OK')` 確認綁定成功
  - spinner 改用 `.jtdt-spinner` / `.jtdt-loading` 共用 class

---

## [1.1.33] - 2026-04-26

### 修正（pdf-to-image 拖檔/點選都失效）

- **`pointer-events: none` 從 `.drop-zone.uploading` 拿掉**：v1.1.30 引入的 busy overlay 用 `pointer-events: none` 防重複上傳，但若上一次上傳因為 server 卡（v1.1.30 前的 sync 問題）Promise 永遠不 resolve，drop-zone 就**永久失去 pointer 事件**，連點擊和拖曳都進不去。改成只用 opacity 視覺降淡，不擋事件 — 重複上傳是可恢復的，鎖死不行。
- **`pageshow` 事件清除遺留 `.uploading`**：bfcache 從前/後退按鈕回到頁面時，舊的 busy state 會殘留。`FileUpload._installPageShowReset()` 在每個 `pageshow` 主動清掉所有 `.drop-zone.uploading`。

---

## [1.1.32] - 2026-04-26

### 變更（pdf-extract-images）

- **xref dedupe**：同一個 Image XObject 被多頁引用（最常見就是公司 logo），原本每頁都抽一份，57 頁的簡報抽出 50 個重複 logo。改成 dedupe by xref，每張獨立圖片只存一份，記錄 `pages` 陣列列出出現在哪幾頁。
- **卡片左上角「勾選」改大白底框**：原本 22px 黑底圓角看不出是 checkbox。現在 26px 白底 + 灰邊，勾選後綠底 + 白勾。
- **卡片右上角「下載」改 icon 按鈕**：原本「下載」黑底膠囊太擠，改成方形 icon 按鈕（下載箭頭 SVG），hover 變紫底白字，tooltip「下載這張」。
- **進度提示 spinner**：擷取進行中時，結果區顯示置中大 spinner + 「正在擷取嵌入圖片，請稍候…」placeholder block，狀態列也加小 spinner。
- **共用 `.jtdt-spinner` / `.jtdt-loading` class** 加進 `platform.css`，未來其他工具可直接套用。

### 除錯

- **pdf-to-image 拖檔無反應 — 加診斷 console.log**：v1.1.30+v1.1.31 改了 `file_upload.js` 但仍無效，原因不明。在 pdf-to-image 的 inline script 加 `console.log('[p2i] uploadRoot=...')` 等，並把 `getElementById('grid')` 改 null-safe。下次使用者開 DevTools 截圖即可定位。

### 釐清

- **向量圖出來是 PNG**：是。PyMuPDF `page.get_images()` 只回傳 PDF 內的 raster Image XObject。即使原始 image stream 是向量 （PDF Form XObject），本工具透過 `Pixmap.tobytes("png")` rasterize 後存成 PNG。**純向量繪圖（paths / strokes，不是 Image XObject）這個工具完全抓不到**，那需要另寫 SVG 抽取邏輯。

---

## [1.1.31] - 2026-04-26

### 變更

- **「擷取圖片」UX 重做 + 同樣移到 thread pool**：
  - 移除「先看頁面預覽再點開始擷取」的兩步流程（使用者反映看到頁面以為工具壞掉）。改成上傳即自動擷取嵌入圖片，直接顯示結果。
  - 頁首加說明：「這不是把 PDF 每一頁轉成圖片，那是<a>文書轉圖片</a>工具」。
  - `/extract` 端點改 `asyncio.to_thread`，PyMuPDF 工作不再 block event loop。

---

## [1.1.30] - 2026-04-26

### 修正

- **pdf-to-image convert 同樣 block 整站**：`office_convert.convert_to_pdf` （subprocess wait） + `pdf_preview.render_page_png` （PyMuPDF） 全部 sync。改用 `asyncio.to_thread`。
  - 副作用：之前如果 server 卡住，pdf-to-image 拖檔表面像「沒反應」其實是 fetch 一直在 queue。現在 server 不卡了，drag-drop 會正常觸發。

### 新增

- **所有上傳工具自動加 spinner overlay**：`file_upload.js` `_pick()` 後若 `onFile()` 回傳 Promise，就 toggle `.drop-zone.uploading`，CSS 顯示右上角小 spinner + 「上傳/處理中…」字樣，並 disable pointer-events 避免重複上傳。零侵入：所有寫成 `async function handleUpload` 的 tool 都自動受惠，不用改一行 code。

---

## [1.1.29] - 2026-04-26

### 修正（重大）

- **pdf-extract-text 上傳大檔會卡住整站**：`_extract_structured / _render_*` 是同步 PyMuPDF / python-docx / soffice 呼叫，跑在 async route handler 主執行緒會把 asyncio event loop 整個 block 住，**所有使用者的所有請求**（含 sidebar 切工具、healthz）全部 stall。改成 `await asyncio.to_thread(...)` 把整段 CPU-bound 工作丟到 thread pool，event loop 維持暢通。實測 100MB PDF 會讓單核 CPU 飆 99% 並讓全站不能用，修正後其他使用者可同時繼續操作。

### 變更（權限矩陣 UI 大改）

- **改成 master-detail 兩欄式**：左側 subject 清單（搜尋 + 全部/使用者/群組 tab + 計數），右側選中後即時編輯角色 / 直接 grant 工具，不再是「N 個 panel 一直往下捲」。
  - 左側每一筆顯示：圖示 + 名稱 + SEED badge + username/群組標籤 + 來源 badge + 角色數量 chip
  - 右側分兩個 fieldset 卡片：「角色」「進階：直接 grant 工具」，都用 picker（搜尋 + 已選計數 + 清除）
  - 切換 subject 時若有未存變更會跳 in-app modal 確認
  - 儲存後 in-place 更新左側 row 的角色數量 badge，不用 reload
- 後端 `permissions_page` enrichsubject 多帶 `name` / `username` / `source` / `is_admin_seed` 給 UI 用

---

## [1.1.28] - 2026-04-26

### 變更

- **擷取文字加 spinner 進度提示**：上傳後 status 顯示「⟳ 擷取中…」（小 spinner），預覽區改放置中大 spinner + 「正在解析 PDF，請稍候…」placeholder block，避免按下後沒回饋讓人以為壞掉。失敗 / 錯誤時改成紅底錯誤框，不再只是一行小灰字。CSS-only 動畫，無 JS 依賴。

---

## [1.1.27] - 2026-04-26

### 變更

- **「合併」工具改名為「檔案合併」**：跟「多頁合併」明確區分，避免使用者混淆。同步更新 tool metadata、page title、h1。
- **Sidebar 版本號更貼近標題**：`brand-version` margin-top 從 -4px 拉到 -10px，視覺更緊湊。

---

## [1.1.26] - 2026-04-26

### 變更（群組頁 + 稽核 + 措辭）

- **群組頁角色 / 成員選擇器改 in-app modal**：原本兩顆按鈕都跳 `prompt()` 要使用者手打 role id 或逗號分隔的 user id；改成 picker modal（搜尋 / 計數 / 清除），現有指派預先勾選；list_groups 多帶 `member_ids` 給前端對齊已選狀態。
- **群組頁來源欄改用彩色 badge / 角色用 chip 顯示**，跟使用者管理頁一致。
- **稽核 `tool_invoke` 詳情豐富化**：原本只記 `method` + `path`，新增 `action`（最後一段路由如 `extract` / `merge` / `save`）、`size_bytes`、`content_type`，方便看出「誰對哪個工具做了什麼大小的請求」。檔名擷取規劃 v1.1.27 上。
- **稽核詳情 JSON 不再溢出右邊**：詳情欄 `max-width:0` + `<pre>` `white-space:pre-wrap; word-break:break-all`，長 path 會自動 wrap，列高自動撐開但不會破版。
- **首頁 hero pill「本機運作」→「不上雲，資料留在內網」**：澄清可在 Linux 架站給內網用，不只本機。README 隱私段也同步。

---

## [1.1.25] - 2026-04-26

### 變更

- **首頁 hero 副標題與三個 pill 標籤更新**：原本只列 PDF 表單填寫 / 蓋章 / 浮水印 / 合併分拆，已不符現況。副標展開為「整合式 PDF / Office 文件處理平台 — 表單填寫、用印簽名、浮水印、N-up、合併分拆、轉檔、文字 / 圖片擷取、敏感資料去識別化、加密 / 解密、Metadata 清除、隱藏內容掃描、差異比對、頁面編輯…可選 LDAP / AD 認證 + 角色權限 + 稽核 + Log 轉發」。三顆 pill 改成「全方位文件處理 / 帳號權限與稽核 / 本機運作，資料不外傳」。

---

## [1.1.24] - 2026-04-26

### 變更（使用者清單欄位顯示）

- **「最後登入」改人類可讀格式**：原本是 raw unix timestamp（`1777176362`），改成 `2026-04-26 10:42` + 第二行 `5 分鐘前 / 2 小時前 / 3 天前`；從未登入顯示「從未登入」.client-side JS 算（用瀏覽器時區）。
- **「角色」改顯示中文 + chip**：原本是 `default-user` slug，現在顯示 `一般使用者` 並用淺灰 pill 包，hover tooltip 顯示原 id slug 給管理員辨識；多角色會自動斷行。後端 `users_page` 多帶 `roles_display` 欄位（不動原 `roles` slug list 的 backend 契約）。

---

## [1.1.23] - 2026-04-26

### 修正

- **編輯使用者 modal picker 名稱仍被 `…` 截掉**：把 `.picker-name` 從 `nowrap + ellipsis` 改成 `word-break:break-word`，名字長就 wrap 到第二行（每筆稍高一點）但保證看得完整；checkbox 用 `align-items:flex-start` 對齊頂部.modal max-width 從 520px 拉到 600px 給更多橫向空間。

---

## [1.1.22] - 2026-04-26

### 修正

- **編輯使用者 modal 角色 / 群組名稱被截斷**：原本每筆會在名稱旁顯示 id（角色 slug 或群組 DB pk），擠掉名字導致 `管...`、`Domai...` 這種爆版。改成：角色的 id 移到 hover tooltip（`title=`），群組的 id 是 DB 整數對人類無意義直接拿掉。中文名跟群組名現在拿到全部 row 寬度。

---

## [1.1.21] - 2026-04-26

### 變更（使用者管理）

- **使用者清單加「來源」篩選 pill-bar**：全部 / local / ldap / ad 四顆按鈕，選一個就只顯示對應 realm 的帳號；跟搜尋字串並用。
- **編輯使用者 modal 的角色 / 群組選擇器升級為 picker**：每組 picker 內含 toolbar（搜尋框 + 已選 X / Y 計數 + 「清除」按鈕）+ scrollable list。每筆 item 用 ellipsis 截斷防超出右邊（之前 `default-us...` 那種爆版），勾選會反白，未來角色 / 群組變多也搜得到。

---

## [1.1.20] - 2026-04-26

### 變更（使用者管理頁）

- **「來源」欄改成彩色 badge**：`local` 灰、`ldap` 藍、`ad` 紫，pill 形狀；同名不同 realm 的兩筆 jason 一眼就區分得開。
- **欄位標題可點排序**：帳號 / 顯示名稱 / 來源 / 狀態 / 角色 / 最後登入都可點，第一次點昇冪 ▲、第二次點降冪 ▼，未排序顯示 ⇅；中文用 `localeCompare('zh-Hant')`。最後登入是數值排序。狀態用 `data-sort-key` 帶 0/1 排，避免「啟用/停用」中文字面排序奇怪。

---

## [1.1.19] - 2026-04-26

### 變更

- **每個分區標題加 icon**：Backend 模式 / 連線 / 搜尋 / 屬性對應 各加對應 icon（gear / globe / search / clipboard）。

---

## [1.1.18] - 2026-04-26

### 變更

- **認證設定頁四個分區改用 `<fieldset>` 卡片**：Backend 模式 / 連線 / 搜尋 / 屬性對應 各自獨立的圓角白卡，標題用淺紫底色 pill （`legend`） 嵌在卡片邊框上 — 視覺一眼分得清。
- **「試登入」→「測試登入」**：跟「測試伺服器連線」用詞一致。

---

## [1.1.17] - 2026-04-26

### 變更（認證設定頁排版第二輪）

- **整個 form 改用自訂 `.auth-form` 結構，不再借用全域 `.form-row`**：原本 form-row 是 flex + label width 96px，跟欄位 layout 衝突導致 label/輸入框並排亂跑。新結構每筆 field 改用 block 排版（label 在上、input 在下、hint 跟在後面）。
- **欄位分區**：用「連線」「搜尋」「屬性對應」三個小標題把 LDAP 設定切成清楚的三段。
- **Backend 卡片 minmax 從 220px 拉到 240px**：`OpenLDAP / UCS / FreeIPA` 跟 `Microsoft AD / Samba AD` 不再被截斷。
- **input 一律 `width:100%; box-sizing:border-box`**：不再有些 400px、500px、520px 寫死的 inline width，桌機/筆電/手機都自動填到 form 寬。

---

## [1.1.16] - 2026-04-26

### 變更（認證設定頁排版整理）

- **Backend 卡片重做**：改用獨立 `.backend-card` （CSS Grid `auto-fit, minmax(220px,1fr)`），不再借用工具用的 `.option-cards` flex 結構，文字不會被切。每張卡 icon + 中文名 + 英文 sub。
- **filter 範例改 `<details>` 收合 + table**：原本一大坨 6-7 行範例平鋪展開，現在改成依當前 backend 顯示對應 backend 的範例 table（左欄場景、右欄 filter），預設收合，要看才展開。
- **驗證測試分兩張卡**：`.test-grid` （auto-fit 320px+） 並排顯示「連線測試」「帳號測試」兩張獨立面板，各有自己的 header / 說明 / 操作區。
- **頂部說明縮短**：4 句話的 wall of text 壓成 2 句重點。

---

## [1.1.15] - 2026-04-26

### 修正

- **LDAP 登入 500 （KeyError: 'id'）**：v1.1.13 重構 `_sync_user` 把 return dict key 統一成 `user_id`，但 `authenticate()` 還在用 `user_row["id"]`。改成 `user_row["user_id"]` 與其他呼叫者一致。新增一個鎖契約的 regression test：`_sync_user` 必須 return `user_id` 且**不能**有 `id`，這樣未來改 key 會在 CI 立刻爆而不是 runtime 才發現。

---

## [1.1.14] - 2026-04-26

### 變更

- **Sidebar 帳號顯示加 realm 後綴**：`jason` → `jason@local` / `jason@ldap` / `jason@ad`，方便分辨同名不同領域帳號；單機模式（auth off）仍顯示純名。

---

## [1.1.13] - 2026-04-26

### 變更（多領域帳號並存）

- **`UNIQUE(username)` → `UNIQUE(username, source)`**：同名 `jason` 可同時存在於 `local` 與 `ldap` 兩種 realm，互不衝突 — 跟 Proxmox VE 的 `username@realm` 概念一致。登入頁的「認證領域」下拉決定走哪一條。
  - 新增 `_m2_username_source_unique` migration（rebuild users 表，資料完整保留）。
  - 移除 `_sync_user` 裡誤導的「本機已有同名帳號」錯誤訊息（已不再會發生）。
  - 仍然保留 **同 backend 內 username 撞 DN** 的拒絕邏輯（避免身分覆蓋）。
- 測試從 collision-fails 改為 coexist-succeeds，4 個 case 全綠。

---

## [1.1.12] - 2026-04-26

### 修正

- **LDAP 登入時若同名 local 帳號已存在會 500 （UNIQUE constraint failed: users.username）**：`auth_ldap._sync_user()` INSERT 前先檢查同名衝突，碰到 local 帳號 → `AuthError("本機已有同名帳號「X」...")`；碰到不同 LDAP DN 同名 → 拒絕避免身分覆蓋。新增 4 個直接呼叫測試（first-time / same-DN-update / local-collision / cross-DN-collision），不需真 LDAP 伺服器。

---

## [1.1.11] - 2026-04-26

### 新增（認證設定 UX 大改）

- **「測試伺服器連線」按鈕**：用表單上的設定（不必先儲存）試 service-account bind，回傳 elapsed_ms / whoami / vendor。
- **「測試帳號登入」區塊**：填使用者名稱 + 密碼，跑完整 service bind → search → user bind 流程，但**不寫本機資料庫、不發 audit、不建 session**，純驗證 filter / base DN / user bind 是否都對。成功會顯示 user_dn / display_name / 群組清單；失敗會顯示具體錯誤。
- **新後端 API**：`POST /admin/auth-settings/ldap-test-connection`、`/ldap-test-login`，admin 限定。
- **`auth_ldap` 抽出 `_build_server()` / `test_connection()` / `test_user_login()` helper**，與 `authenticate()` 共用設定處理。

### 變更

- **Backend 改為 option-card 卡片式選擇**（取代過去的 radio 列 / 三顆儲存按鈕的奇怪 UX）：頂部三張卡片（本機 / LDAP / AD）擇一，下方統一一顆「儲存設定」；只有真的切 backend 才彈確認對話框。
- **filter / username / 群組屬性 加 backend-aware hint**：選 LDAP 時提示 `uid` / `(uid={username})`，選 AD 時提示 `sAMAccountName` / `(sAMAccountName={username})`；「群組屬性」明確標示 `memberOf` 並警告**不是** `member`（方向相反，是常見地雷）。
- **filter 範例寫多一點**：列出常用 / 用 email / 限啟用帳號 / 限定群組 / 巢狀群組 等多種情境，AD / LDAP 各有完整範例。
- **「使用者搜尋 base DN」如果含 `(` 或 `)` 會直接回傳 `「使用者搜尋 base DN」不能包含 ( 或 )；那是 filter 語法...` 而非 LDAP3 的 `LDAPInvalidDnError: character '(' not allowed`** — 把過濾語法寫進 base DN 是常見錯誤，直接攔下來提示寫到 filter。
- **登入失敗訊息曝露真因**：原本一律顯示「無法連線到 LDAP 伺服器」，現在會帶 exception class + message（例：`InvalidCredentials: invalidCredentials`、`LDAPSocketOpenError: ...`），方便管理員自行排錯。Service password 不在 exception 字串裡，沒洩漏風險。
- **更新「Backend 模式」說明**：原本寫「切到 LDAP 後 local 帳號無法登入」已過時 — 現在登入頁有「認證領域」下拉，本機帳號（含 jtdt-admin 救援帳號）仍可從本機領域登入。

---

## [1.1.10] - 2026-04-26

### 變更（擷取文字 LLM 重排 UX）

- **預覽加「原始 / LLM 重排」切換**：原本 LLM 重排完直接覆蓋預覽，使用者看不出差別。現在保留原始版本，提供分頁切換比對；預設顯示重排版。
- **差異提示**：標題旁顯示「共 N 字元差異，可切換比對」；如果 LLM 完全沒改字元（剛好都已經是完整段落）會顯示「內容與原始相同 — LLM 未修改任何字元」，避免使用者以為功能壞掉。

---

## [1.1.9] - 2026-04-26

### 修正

- **pdf-extract-text 500 Internal Server Error**：`from ...core import llm_settings` 是 import 模組，但程式呼叫的是 `LLMSettingsManager` 實例上的 `is_enabled()`。修正為 `from ...core.llm_settings import llm_settings`（其他工具的寫法）。

### 測試

- **`test_smoke_routes.py` 從 registry 動態列出所有工具**：寫死清單時新工具加進來會漏掉（這次 pdf-extract-text 就是這樣破）。改成 `for t in app_main.tools` 自動產生 `/tools/<id>/` 路徑。共 19 個工具 + 10 個 admin 頁全測 GET 200.
- **總 test：177 → all green**。

---

## [1.1.8] - 2026-04-26

### 變更（Log 轉發 UX）

- **「名稱」欄位加 tooltip**：說明此為自訂識別名稱，留空會自動帶入 `{format}://{host}:{port}`。
- **「Host」欄位標 `*` 必填**：表頭加紅色星號，避免使用者以為跟「名稱」一樣可留空。
- **儲存前 client-side 檢查**：Host 留空時不送 request，直接 in-app modal 提示「Host 是必填欄位」並 focus 到該欄位、紅框標記。
- **後端錯誤訊息中文化**：`host required` / `port out of range` / `port must be int` 等翻成中文，改用 `showAlert` 顯示而非 sidebar 角落小字。

---

## [1.1.7] - 2026-04-26

### 新增

- **點擊 sidebar 帳號名稱顯示「我的帳號」**：modal 列出帳號 / 顯示名稱 / 認證來源 / 角色 / 可用工具清單。管理員顯示紅色「管理員」標籤；無權限會提示去找管理員。新後端端點 `GET /whoami`（cookie 驗證，回傳 JSON）。

---

## [1.1.6] - 2026-04-26

### 變更

- **使用者管理：「內建」改成 disabled 按鈕**：原本 `[內建]` 是 inline span，跟其他列的按鈕不對齊。改成 `<button disabled>`，跟「編輯」「重設密碼」「刪除」一致排列。
- **登出確認改用 in-app modal**：原本用 `window.confirm()` 跳瀏覽器原生對話框（與專案規範「所有對話框走 in-app」抵觸）。改走 `window.showConfirm()`（`static/js/modal.js`），跟其他二次確認一致。

---

## [1.1.5] - 2026-04-26

### 變更

- **Sidebar 依權限隱藏**：啟用認證後，非管理員看不到的「設定」項目全部從 sidebar 隱藏（不只是後端擋 403）；「工具」清單也只顯示該使用者有權使用的工具。首頁 tile 同步過濾。Auth OFF（單機模式）行為不變。
- **登出確認**：sidebar 的「登出」按鈕加上 `confirm()` 對話框，避免誤觸把工作中的草稿丟掉。

### 修正

- **重設密碼 / 編輯使用者 modal 標籤被擋**：modal 內 `.form-row` 沿用全域 flex 樣式（label 固定 96px + input flex），在 380–520px 寬的 modal 裡會把標籤切掉（例如「新密碼（至少 8 字元」尾字消失）。Modal 內改成 block 排版，label 自成一行、input 100% 寬。

---

## [1.1.4] - 2026-04-25

### 變更

- 用詞統一：「**紀錄**」→「**記錄**」全專案 19 處（CHANGELOG / CLAUDE.md / main.py 的 nav_settings + 搜尋 alias / admin_history / admin_audit / admin_log_forward / admin_retention / pdf_hidden_scan）。「紀錄」較偏向「世界紀錄／體育紀錄」這類名詞用法；操作日誌、歷史、稽核都用「記錄」。

---

## [1.1.3] - 2026-04-25

### 變更

- **沒啟用認證 = 單機模式**：sidebar 自動隱藏 9 個進階管理項目（使用者管理 / 群組管理 / 角色管理 / 權限矩陣 / 稽核記錄 / Log 轉發 / 表單填寫歷史 / 用印簽名歷史 / 浮水印歷史）。「認證設定」「檔案保留 / 清理」「資產 / 公司 / 同義詞 / 範本 / 轉檔 / LLM / API Token / 字型」等核心設定維持顯示。
  - 隱藏只動 sidebar，URL 仍可直接訪問（避免啟用認證後既有 bookmark 失效）
  - 內部以 `requires_auth: True` 標記、Jinja global 函式 `nav_settings()` 每 request 過濾
- **檔案保留 / 清理頁加上「清理時機」說明**：講清楚 daemon thread 在服務啟動時 + 每 6 小時跑一次、立即清理按鈕、服務沒在跑就不會清等。

---

## [1.1.2] - 2026-04-25

### 新增

- **`jtdt reset-password <username>`**：管理員忘記密碼時的緊急救援指令。在主機上跑 `sudo jtdt reset-password jtdt-admin` 互動輸入新密碼，會直接更新 DB、重設 lockout 計數、清掉所有 session。LDAP/AD 使用者拒絕（密碼由目錄端管）。
- **登入頁認證領域選擇**：啟用 LDAP/AD 後，本機帳號仍能登入（rescue path）。登入頁多一個下拉選單，預設選外部目錄，使用者可切「本機帳號」用本機密碼登入（jtdt-admin 永遠走得通）。
- **左上角顯示登入帳號 + 登出按鈕**：base.html sidebar 頂端，登入後就出現使用者名稱 + 一鍵登出。
- **使用者管理：搜尋框 + In-page 編輯 modal + 重設密碼 modal**：取代瀏覽器 prompt。編輯 modal 含顯示名稱、啟用、角色多選 （checkboxes）、群組多選。
- **內建管理員 （jtdt-admin / `is_admin_seed=1`） 不可被編輯角色或停用**：UI 隱藏編輯按鈕、顯示「內建」標記，後端也 raise 拒絕。

### 修正

- LDAP/AD 設定頁 username/displayname/group 屬性三個欄位在窄 viewport 重疊；改用 grid layout。
- 「使用者搜尋 filter」hint「{username} 會被代入」原本 inline 跟 input 擠在一起；改成新行。
- setup-admin 警告文字補上 `sudo jtdt reset-password` 救援指令說明。

---

## [1.1.1] - 2026-04-25

### 修正

- v1.1.0 新增的 11 個 admin 頁的 `<input>` / `<select>` 沒有套上 `class="field"`，造成沒有邊框、沒有樣式（plain HTML）。批次補上 （admin_users / groups / roles / permissions / audit / log_forward / retention / history / auth_settings）。
- 主要動作按鈕（編輯 / 重設密碼 / 刪除 / 成員 / 角色 / 儲存 / 清除 / 原檔 / 結果）補上對應 icon （edit / lock / trash / user / shield / save / back / download），跟既有頁面風格一致。

---

## [1.1.0] - 2026-04-25

大改版：認證、權限、稽核、Log 轉發、檔案保留全部到位。**升級不會自動啟用認證**——預設仍 backend=off，原本的使用方式不變；admin 想啟用就到「認證設定」打開。

### 新增

#### 認證 （auth）
- **三種 backend**：`local`（本機帳號 + scrypt 密碼）、`ldap`、`ad`（簡單 bind 驗證 + 屬性同步）
- 第一次啟用走 `/setup-admin` 表單建立 jtdt-admin
- Cookie session：7 天，「30 天免登入」可選
- 失敗 5 次鎖 15 分（per-user + per-IP）
- 帳號 / 密碼錯誤訊息一致（防 username enumeration），timing-uniform 驗證
- Session token 存 sha256 不存 raw（DB 洩漏不會直接被冒名）

#### 權限 （permissions）
- 6 個內建角色：`admin` / `default-user` / `clerk` / `finance` / `sales` / `legal-sec`，新使用者預設 `default-user`（除 pdf-fill / pdf-stamp 外的工具都能用）
- 三種 subject：user / group / OU（OU 從 AD/LDAP DN 自動推導所有上層）
- 純白名單，無 deny；effective = union（直接 grant + 各 role grant）
- admin role short-circuit 到 ALL
- middleware 統一 gating（路徑 `/tools/<tool_id>/*` 自動檢查），403 帶友善訊息
- in-memory cache + 變更時 invalidate

#### 稽核 （audit）
- `audit_events` 表存 login / logout / 帳號 CRUD / 群組 CRUD / 角色變更 / 權限變更 / 工具呼叫 / 設定變更 / log 轉發失敗
- async 寫入 queue（1000 events/0.06s 實測），不阻塞 request
- `/admin/audit` 分頁列表 + 篩選（user / event_type / 時間）+ CSV 匯出（UTF-8 BOM）
- 預設保留 90 天，超過 5GB banner 提醒

#### Log 轉發
- 多 destination 並行：`syslog` （RFC 5424） / `cef` （ArcSight） / `gelf` （Graylog） over UDP/TCP
- 失敗 retry 3 次後寫 `audit_forward_failed` 進本機 audit
- 背景 worker bookmark 保證不漏不重複

#### 歷史 + 自動清理
- pdf-fill 既有歷史 + 新增 pdf-stamp / pdf-watermark history
- 三種歷史 admin 頁 （`/admin/history/{fill,stamp,watermark}`）
- 6 種清理項目（fill_history / stamp_history / watermark_history / temp / jobs / audit）獨立保留設定，預設 365 天（audit 90 天）
- `-1` = 永久保留
- 背景 scheduler：啟動時 + 每 6 小時跑一次

#### Admin 頁
- 新增 11 個：認證設定、使用者管理、群組管理、角色管理、權限矩陣、稽核記錄、Log 轉發、檔案保留 / 清理、表單填寫歷史、用印簽名歷史、浮水印歷史

#### API token
- 啟用 auth 後，每張 token 必須指派 owner（user_id），呼叫時依該使用者的 effective perms 過濾
- 沒指派 owner 的 token 在 auth on 時直接 403

### 內部
- 新增 SQLite 層 （`app/core/db.py`）：WAL + busy_timeout + foreign_keys + 短交易 helper + migrate by user_version
- 兩個 DB：`auth.sqlite` （users / groups / roles / permissions / sessions / lockouts） + `audit.sqlite` （audit_events / forward_state）
- 73 個新 pytest 涵蓋 db / passwords / sessions / auth_local / auth_routes / auth_middleware
- 33 個新 pytest 涵蓋 roles / user_manager / group_manager / permissions
- 全測試 146 pass

### 安全 checklist 全過
參數化 SQL、constant-time compare、scrypt N=2^16 密碼 hash、HttpOnly + SameSite=Lax + Secure cookies、open-redirect 防護、CSRF via SameSite、enum CHECK constraints、`chmod 600` 設定檔與 secret、async audit 防 burst DoS、token sha256 入庫、LDAP filter escape、預設 LDAPS + verify cert、token-not-owned 預設 deny、admin role 路由級 dependency、權限 cache 變更 invalidate、敏感設定不寫 audit details

### 新 dependency
- `ldap3>=2.9.1,<3` (Apache 2.0)

---

## [1.0.16] - 2026-04-25

### 新增

- **字型管理頁可隱藏不要的字型**：每個字型旁加「隱藏」/「顯示」按鈕。隱藏後 PDF 編輯器的字型下拉選單不會出現該字型，**檔案保留**隨時可取消隱藏。隱藏狀態存在 `data/font_settings.json`（`hidden: [...font ids...]`）。
- 字型清單頁標題顯示「總計 N 個（X 顯示、Y 隱藏）」
- 後端 `font_catalog.list_fonts(include_hidden=False)`：預設過濾隱藏的；admin 頁傳 `True` 看完整清單 + 每筆帶 `hidden` flag
- API：`POST /admin/fonts/toggle-hidden` (`{id}` → `{ok, id, hidden, hidden_count}`)
- pdf-fill / pdf-watermark / pdf-stamp 文字模式有自己獨立的字型清單（不走 font_catalog），暫不受此功能影響；如有需求再擴

---

## [1.0.15] - 2026-04-25

### 變更

- README + pyproject.toml 描述：「**一站式**」→「**整合式**」（前者是近年從對岸滲入的行銷用語，後者是台灣自然講法）

---

## [1.0.14] - 2026-04-25

### 修正

- **資產匯入失敗（找不到 assets.json）**：v1.0.13 的匯入 endpoint 預期 `assets.json` 在 zip root，但使用者用 `zip -r assets/` 手動打包的 zip 會把所有檔案放在 `assets/` 子資料夾下。改成自動偵測 `assets.json` 在 zip 內的位置，剝掉前綴後依此找對應的 `<prefix>files/<id>.png`。也忽略 macOS 自動產生的 `__MACOSX/` 噪音。

---

## [1.0.13] - 2026-04-25

### 新增

- **資產匯出 / 匯入**（管理 → 資產管理）：
  - **匯出 ZIP**：把 `assets.json` + 全部 PNG（原圖 + thumb）打包成單一 ZIP，檔名 `assets_export_<時間戳>.zip`
  - **匯入（合併）**：保留現有資產，把 ZIP 內的資產通通新增進來；id 撞到既有的會自動分配新 id（不會蓋掉原本的）
  - **匯入（取代）**：清掉現有所有資產（含 PNG 檔），整個換成 ZIP 內容（不可還原，有確認對話框）
  - API：`GET /admin/assets/export`、`POST /admin/assets/import` (form: `file`, `mode=merge|replace`)
- **去識別化支援英文公司名 / 英文人名**：
  - 公司：`RE_COMPANY` 加入英文後綴匹配 `Co., Ltd. / Co.,Ltd. / Inc. / LLC / Corp(oration). / Limited / Company`，能抓到「(vendor) Co。， Ltd。」「Apple Inc。」「Acme Corporation」等
  - 人名：`RE_PERSON` 的 label 加上英文版（Name / Contact / Owner / Manager / Sales Rep / Signed by …），value 也支援英文姓名（首字大寫的 2-4 個詞）。Label 用 `(?i:...)` inline flag 設為 case-insensitive，但 value 仍要求首字大寫（避免 "name: john doe" 這類日常字串誤觸）。

---

## [1.0.12] - 2026-04-25

### 新增

- **欄位同義詞匯出 / 匯入**：`管理 → 欄位同義詞` 頁加上三個按鈕：
  - **匯出 JSON**：下載目前所有同義詞，檔名 `label_synonyms.json`，格式 `{"_kind": "jt-doc-tools synonyms", ..., "synonyms": {key: [...]}}`
  - **匯入（合併）**：保留現有條目，新檔案的 key 補上去；同 key 兩邊的同義詞做聯集（不丟資料）
  - **匯入（取代）**：清掉現有所有同義詞、整個換成匯入檔內容（不可還原，有確認對話框）
- API endpoints：`GET /admin/synonyms/export`、`POST /admin/synonyms/import` (form: `file`, `mode=merge|replace`)
- 匯入 endpoint 同時支援兩種格式：（1） 我們自己的匯出格式，（2） 直接給 `{key: [同義詞...]}` 的最小 dict（手寫 / 從別處來的）

---

## [1.0.11] - 2026-04-25

### 變更

- **沒啟用 LLM 時，「擷取文字」頁的 LLM 重排提示與按鈕完全不顯示**：之前 admin 沒勾「啟用 LLM」也會顯示一個 hint 卡 +「交給 LLM 重排」按鈕（按下去才被擋）。改成 `index()` 把 `llm_settings.is_enabled()` 傳進 template，整段 `{% if llm_enabled %}` 包起來。JS 端對應 listener / DOM 也加 `if (document.getElementById('btnLlmReflow'))` guard，避免沒這 element 時 null pointer。

---

## [1.0.10] - 2026-04-25

### 變更

- **更多用詞台灣化**：
  - 文件去識別化：「黑條覆蓋」→「**塗黑覆蓋**」（個資法、政府公文用語）
  - README：「Logo 圖像」→「Logo 圖片」
  - PDF 表單填寫歷史頁、API token 提示、表單填寫錯誤提示：「保存」→「保留」

### 修正

- **擷取文字頁的 4 個下載按鈕（TXT / Markdown / Word / ODT）沒有顏色**：少了 `btn-primary` class，跟其他工具頁的下載按鈕視覺不一致。補上後也是藍色 primary 樣式。

---

## [1.0.9] - 2026-04-25

### 新增

- **`jtdt bind <addr>[:port]`**：安裝後可單指令改變監聽位址 / port，跨平台處理（Linux 改 systemd unit + daemon-reload、macOS 改 .app launcher 後重啟、Windows 顯示 NSSM 指令）。例如 `sudo jtdt bind 0.0.0.0`、`sudo jtdt bind :9999`、`sudo jtdt bind 0.0.0.0:9999`。

### 修正

- **文書轉圖片：v1.0.7 補的「下載 PNG」icon 比其他按鈕大兩倍**：JS 動態插入的 SVG 沒帶 `width="16" height="16" class="ic"`，所以用 viewBox 自然 size 把整個按鈕撐起來。對齊 `{{ icon('download') }}` macro 的屬性。

---

## [1.0.8] - 2026-04-25

### 修正

- **OxOffice 已裝但 jt-doc-tools 仍跑 LibreOffice**：`app/core/conv_settings.py:BUILTIN_PATHS` 把 `/usr/bin/soffice` 排在 OxOffice 路徑之前 → app 永遠先抓到系統 LibreOffice。改成 OxOffice 路徑（`/usr/bin/oxoffice`、`/opt/oxoffice/program/soffice`、Windows 的 `C:\Program Files\OxOffice\...`）一律排在 LibreOffice 之前。
- **install.sh 顯示「OxOffice 安裝完成：LibreOffice 7.3.7.2」**：OxOffice 是 LibreOffice fork，`soffice --version` 字串沒改（仍說 LibreOffice）。改用「路徑或專屬 binary `oxoffice`」判斷是不是 OxOffice，避免拿錯 version 字串誤導。

---

## [1.0.7] - 2026-04-25

### 修正

- **install.sh 在 Linux 從來沒成功裝過 OxOffice**：原本程式 grep `\.deb$` / `\.rpm$` 找 OSSII GitHub release asset，但實際 asset 是 `OxOffice-<ver>-deb.zip` / `-rpm.zip`（zip 包著 30+ 個 .deb / .rpm），副檔名是 `.zip` 直接 miss。改成 grep `OxOffice[^"]*-deb\.zip`，下載後 unzip 再 `apt-get install ./*.deb`。
- **`ensure_office()` 看到既有 LibreOffice 就不裝 OxOffice**：違反「OxOffice 優先」原則。新邏輯：偵測到 LibreOffice 仍嘗試補裝 OxOffice（OSSII 台灣 fork，CJK 支援更好），失敗才保留 LibreOffice。
- **文書轉圖片：「下載 PNG」按鈕沒有 icon**：JS `dl.firstChild.textContent = ''` 把初始 Jinja 渲染出來的 SVG path 給清掉了（SVG element 還在但路徑沒了 → 看起來空白）。改成直接用 JS 裡定義的 `dlIcon` 重渲染。

---

## [1.0.6] - 2026-04-25

### 新增

- **install.sh 加入監聽位址 / port 的可設定性**：
  - CLI flag：`--bind <addr>` / `--port <port>`，例如 `sudo bash install.sh --bind 0.0.0.0`
  - 環境變數：`JTDT_HOST=0.0.0.0 sudo bash install.sh`（適合 `curl ... | sudo JTDT_HOST=0.0.0.0 bash`）
  - 互動式：終端機跑 `sudo bash install.sh` 會跳選單問「1） 127.0.0.1 / 2） 0.0.0.0 / 3） 自訂」
  - `--no-prompt` / `-y`：強制走預設不問
  - `--help` 顯示完整用法
- 安裝完成提示的 URL 改顯示**機器實際 IP**（用 `hostname -I`），而非 `0.0.0.0`（後者讓人看不懂要連哪裡）
- BIND_HOST 是 `0.0.0.0` 時額外提示要設防火牆 / 反向代理

### 修正

- 之前 `127.0.0.1` 與 port `8765` 寫死在 systemd unit / macOS launcher / health check / 完成提示 5 處，未受 `JTDT_HOST` env 控制；本版改成全程使用安裝時決定的 `BIND_HOST` / `BIND_PORT`。

---

## [1.0.5] - 2026-04-25

### 修正

- **`jtdt` 指令會載到錯的 `app/cli.py`**：shim 用 `python -m app.cli` 執行，但 `python -m` 會把當前目錄塞進 `sys.path[0]`。如果使用者在含有 `app/` 子目錄的地方（如 git clone 後的 source dir）跑 `jtdt`，就會載到那裡的 cli.py，`_install_root()` 也會回到那條路徑，導致 `jtdt status` / `jtdt update` 都認錯目錄。修法：shim 加 `cd "$INSTALL_DIR" &&` 確保載入正確路徑的模組。
- **`jtdt uninstall --purge` 沒清乾淨**：
  - 原本只 rmtree `data/`，沒處理同層的 `data.backup-*`（`jtdt update` 留下的最近 3 份備份）→ 會殘留。
  - Linux 沒移除安裝時建立的 `jtdt` 系統使用者 → 帳號殘留。
  - 修法：--purge 一併清備份目錄、（若空）清父目錄、（Linux）`userdel jtdt`（先 `find` 確認沒留檔案）。

---

## [1.0.4] - 2026-04-25

### 變更

- **文件去識別化用語台灣化**：
  - 「真遮蔽」→「**編修**」（對應 Redaction，台灣個資法 / 政府公文用詞）
  - 「脫敏」→「**資料遮罩**」（對應 Masking，台灣資安圈用詞；「脫敏」是源自對岸的技術用語，台灣官方文件較少用）
  - 影響範圍：工具頁說明、模式選擇卡、確認對話框、處理結果摘要、表格欄位標頭、tool description、README。
  - 搜尋關鍵字（`_TOOL_ALIASES`）兩種寫法都保留，舊使用者用「脫敏」搜尋仍找得到。

---

## [1.0.3] - 2026-04-25

### 修正

- **macOS：`sudo jtdt update` 在重啟服務時會撞到 LaunchServices `-600` 錯誤**——`sudo open -a` 是 root 身份，LaunchServices 是 per-user，無法把 .app 拉進使用者的 GUI session。改成偵測 sudo 後 `sudo -u <real_user> open -a` 切回原使用者啟動。

## [1.0.2] - 2026-04-25

### 修正

- **`jtdt update` 顯示「升級完成：v1.0.0 → v1.0.0」**：`_read_version()` 用 `from .main import VERSION`，被 `sys.modules` cache 住，git pull 後仍讀到舊值。改成直接讀 `app/main.py` 文字。
- **`jtdt update` 跑完服務沒有真的 reload 新版**：macOS svc_stop 用 `pgrep -f .venv/bin/python` 偵測 PID，但 `.venv/bin/python` 是 brew/系統 python 的 symlink，ps 印的是 resolved 路徑（`Cellar/...`），pgrep 抓不到。改用 `lsof -tiTCP:8765 -sTCP:LISTEN` 認 port owner，跨 venv / brew / uv 都穩。
- **svc_stop SIGTERM 後立刻 return → svc_start race**：python 還沒斷乾淨，新 launcher curl healthz 還通就跳過 `exec python`。加上「等 port 真的釋放（最多 4 秒）+ SIGKILL fallback」。

## [1.0.1] - 2026-04-25

### 修正

- **macOS：服務跑久了會跳「OxOffice unexpectedly quit while reopening windows」對話框，soffice 持續 SIGABRT**：
  - 修 launcher 架構：用 `exec python` 取代 `nohup python & disown`。`nohup` 把 python re-parent 到 launchd PID 1，孫行程 osascript→soffice 拿到的 Aqua bootstrap 是斷的，AquaSal 在 NSApplicationMain crash。`exec` 讓 python 成為 .app 本體 process，子行程繼承完整 GUI session。
  - 每次轉檔用 fresh per-call `UserInstallation` profile + `--safe-mode`，避免 stale recovery state 卡住下次啟動。
  - 安裝時清乾淨 macOS reopen-windows 的 trigger 路徑（`Saved Application State` / `CrashReporter` cache）+ 寫入三個壓制 key：`ApplePersistenceIgnoreState=1`、`NSDisablePersistentState=1`、`NSQuitAlwaysKeepsWindows=0`。
  - office_convert.py 預設 timeout 30s → 60s（safe-mode 第一次 init 慢）。
- **API endpoint 下載中文檔名爆 UnicodeEncodeError**：HTTP header 是 latin-1，`Content-Disposition: filename="廠商.pdf"` 直接炸。新增 `app/core/http_utils.content_disposition()` helper，輸出 RFC 5987 `filename*=UTF-8''<percent>` + ASCII `filename=` fallback。修 5 處：`/api/convert-to-pdf`、`pdf-attachments` 單檔/zip 下載、`aes-zip`、`pdf-extract-images` zip、admin profile export。
- **install.sh：office 偵測訊息只說「已偵測到 Office 引擎」**，不知道找到哪一套。改成顯示引擎名 + 版本（如「OxOffice 11.0.1.6」）。
- **install.sh：登入項目註冊時印出雜訊「login item UNKNOWN」**：osascript `make login item` 回傳 reference 印到 stdout，無視即可。改成 `>/dev/null 2>&1`。

## [1.0.0] - 2026-04-25

首次正式發行於 GitHub。

### 新增

- **22 個工具**，分為 5 大類：
  - **填單與用印**：表單自動填寫 / 用印與簽名 / 浮水印
  - **檔案編輯**：PDF 編輯器 / 多頁合併 （N-up） / 壓縮 / 合併 / 分拆 / 轉向（含鏡射）/ 頁面整理 / 插入頁碼
  - **內容擷取**：擷取文字（可選 LLM 重排）/ 擷取圖片 / PDF 附件萃取
  - **格式轉換**：文書轉 PDF / 文書轉圖片
  - **資安處理**：文件去識別化 / PDF 密碼保護 / 解除 / Metadata 清除 / 隱藏內容掃描 / 差異比對
- **8 個管理頁**：資產管理 / 公司資料 / 同義詞 / 表單範本 / 轉檔設定 / LLM 設定 / API Token / 字型管理
- **三平台一鍵安裝**：Linux / macOS / Windows，需系統管理員權限
- **`jtdt` CLI**：start / stop / restart / status / logs / open / update / uninstall
- **自動升級**：`jtdt update` 自動備份資料、git pull、uv sync、健康檢查
- **獨立 Python 環境**：透過 uv 管理，完全不影響使用者系統 Python
- **服務化運行**：systemd / launchd LaunchDaemon / Windows Service （NSSM）
- **多使用者安全**：上傳檔 UUID 隔離、temp dir 2h TTL 自動清理
- **可選 LLM 整合**：預設關閉的視覺 LLM 校驗附加功能（Ollama / 自架）
- **API 全覆蓋**：每個工具都有對應的 REST endpoint，可程式化呼叫
- **API Token 認證**：`/api/*` 走 bearer token

### 內部

- pyproject.toml 鎖定依賴版本
- pytest 40 個自動化測試（路由 smoke / PDF 工具 / 欄位偵測 / Admin API / 資產處理）
- 跨平台 office 偵測：自動找 OxOffice / LibreOffice，可指定路徑
- 字型管理：內建 Noto Sans/Serif TC，掃描系統字型，可上傳自訂字型
- 中英雙語搜尋（每個工具都有 `_TOOL_ALIASES` 中英關鍵字）
- 台灣繁體用詞優先（圖片 / 軟體 / 字型 / 列印 / 檔案 …）

---

## 內部開發記錄（v1.0.0 之前）

v1.0.0 之前的內部開發版（v0.1.x ~ v0.2.189）未公開發行，僅作為內部記錄。

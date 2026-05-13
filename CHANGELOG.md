# 更新記錄

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號採 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

---

## [1.7.69] - 2026-05-13

### 改進

- **`text-list` 跨列連線改從卡片左側進入**：v1.7.68 跨列箭頭從卡片頂部進入，與後續同列線從右側出 / 左側入的位置不一致，視覺斷裂。改為「左下→左→上到 head row → 右進左側」，與同列線一樣都在 head row 高度（top + 18），下一張卡片接續的水平線就能無縫對齊。

---

## [1.7.68] - 2026-05-13

### 改進

- **`text-list` 連線統一走 SVG（同列也有線）+ 連線藏到卡片背後**：
  - 移除 CSS `::after` 三角箭頭，所有 .on→.on 連線都用 SVG overlay 畫
  - 同列：head row 高度（top: 18px）一條水平直線 + 箭頭
  - 跨列：從上一張卡底中央 → 行間中點 → 下一張卡上方 → 進其頂端
  - SVG `z-index: 0`，卡片 `z-index: 1`：line 若進入卡片邊界會被卡片擋住，視覺乾淨

---

## [1.7.67] - 2026-05-13

### 改進

- **`text-list` 卡片 gap 加大 + 換行連線改從卡底中央**：
  - gap 從 8px 改 14px×16px，給連線轉折足夠視覺空間
  - 跨行 SVG 連線改成「從前一張卡片底部正中往下 4px → 往左到下一張卡片正上方 → 往下進其頂端」(原本是先右走到容器牆再下、再左)，更直覺像流程圖

---

## [1.7.66] - 2026-05-13

### 改進

- **`text-list` 跨行 pipeline 用 SVG 連線**：v1.7.65 同列卡片之間有 CSS 三角形箭頭，但跨行（例如 4 → 5 從第一列尾跳第二列頭）就斷了視覺。新增 `<svg class="tl-connectors">` overlay 蓋在 `.tl-ops` 上，每對跨行連續的 `.on` 卡片之間畫一條 L 形藍線：右移到容器右牆 → 下到行間中點 → 左移到下一張卡片正上方 → 下進卡片頂端，箭頭指下。`requestAnimationFrame` 等 layout 完成才量；window resize 也會 redraw。

---

## [1.7.65] - 2026-05-13

### 改進

- **`text-list` pipeline 箭頭對齊卡片頂部 head row**：v1.7.64 用 `top: 50%` 垂直置中，當卡片展開子選項變高時箭頭跑到中段不好看。改 `top: 18px` 固定對齊 head row，跟 checkbox / 順序編號同水平線；箭頭略放大 (5/5/6 → 6/6/8) 更醒目。

---

## [1.7.64] - 2026-05-13

### 改進

- **`text-list` 勾選卡片自動排到前面 + 視覺串接箭頭**：
  - 勾選後卡片自動移到 grid 開頭，按 pipeline 順序排列；取消勾選回到原位
  - 相鄰勾選卡片之間畫藍色三角形箭頭（用 CSS `::after`）視覺串接成 pipeline
  - 箭頭位於 grid gap 內（right: -10px），最後一張與換行邊界自動隱藏
  - resize 時 `requestAnimationFrame` 重算換行邊界

---

## [1.7.63] - 2026-05-13

### 改進

- **`text-list` 處理順序改用拖曳調整**：v1.7.62 的 `↑` `↓` 箭頭按鈕不直覺。改成 HTML5 native DnD：勾選的卡片右側顯示拖曳把手（六點圖示），按住把手拖到目標卡片即重排。drop 位置依滑鼠在卡片上半 / 下半決定插在前 / 後，藍邊框 hint 提示位置；拖曳中原卡片半透明。`mousedown` 在 handle 才開啟 `draggable=true` 避免拖到 input / select 起反應。

---

## [1.7.62] - 2026-05-13

### 新增

- **`text-list` 操作可調整處理順序**：勾選後的卡片會在 checkbox 旁顯示「順序編號 + 上下箭頭」，按箭頭調整 pipeline 執行順序。只有 `.on` 卡片之間 swap DOM 位置（未勾選的卡片留原位不亂跳）。第一筆 `↑` / 最後一筆 `↓` 自動 disabled。`collectOps()` 走 DOM 順序，所以 reorder 後送到後端的 `ops[]` 陣列會反映新順序。

---

## [1.7.61] - 2026-05-13

### 改進

- **`text-list` 卡片內子選項字體放大**：v1.7.60 子選項字體 11.5px、padding 1px 太小，select 下拉跟 input 字幾乎看不清楚。改 12.5px、padding 3px 6px、checkbox 14×14、input 加 focus ring，整體行距 8px×10px 更舒服。

---

## [1.7.60] - 2026-05-13

### 修正

- **`text-list` 工具圖示與「擷取文字」撞名**：兩個工具都用 `paragraph` 圖示在側邊欄分不出來。新增 `list` 圖示（橫線清單樣式）到 `components/icons.html`，`text-list` 改用 `list`，與 `pdf-extract-text` 視覺區分。

---

## [1.7.59] - 2026-05-13

### 改進

- **`text-list` 操作卡片改回 grid 並摺疊未勾選的子選項**：v1.7.58 的 masonry-like 多欄反而視覺亂。改回 grid 整齊排列，預設所有卡片同樣短高度（只有 checkbox + 圖示 + 名稱）。子選項用 `display: none → flex on .on` 摺疊，**勾選時才展開**。整齊優先，互動才膨脹，讀寫都直觀。

---

## [1.7.58] - 2026-05-13

### 改進

- **`text-list` 操作卡片改 CSS 多欄排版**：v1.7.57 雖把卡片高度自動化但 grid row 仍預留同列最高的高度，造成短卡片下方留白。改用 `column-width` 多欄 column-flow（masonry-like），每張卡片依自身內容自然高度從上往下流，視窗變寬時欄數自動調整，沒有空白浪費。

---

## [1.7.57] - 2026-05-13

### 改進

- **`text-list` 操作卡片簡化為專案統一風格**：v1.7.56 的彩色直條 + 圓形圖示底色不符專案其他工具的風格。改回專案統一語言：白底細灰邊、勾選後變淡藍底 + 藍邊、圖示用 14px 灰色 inline SVG（勾選變藍）、字體勾選後變粗。同時 `align-items: start` 讓沒子選項的卡片自動縮小，不再被同列高度撐出大片空白。

---

## [1.7.56] - 2026-05-13

### 改進

- **`text-list` 操作卡片加圖示與顏色**：v1.7.55 11 個操作卡片只有純文字看起來太樸素。現在每個卡片左側加 4px 彩色直條（hover 時整卡背景變淡色）、checkbox 旁加圓形圖示。同類操作同色：整理（trim/drop_empty）灰、去重綠、排序 / 反轉藍、隨機紫、大小寫粉、篩選橘、取頭尾青、加綴黃。一眼可分群，視覺更友善。

---

## [1.7.55] - 2026-05-13

### 新增

- **新工具：清單處理（`text-list`）** — 把每行文字當一筆資料做整理。支援貼上文字或上傳 .txt / .csv / .tsv / .md / .log / .json / .xlsx / .ods / .docx / .odt / .pdf（萃取為一行一筆）。操作可勾選組合依序執行：Trim 前後空白 / 移除空行 / 去重複（保留首次 / 末次 / 改列出次數）/ 排序（升降、可選數字感知與不分大小寫）/ 反轉 / 隨機洗牌 / 大小寫轉換 / 篩選（包含/不包含、可 regex）/ 取前 N / 取後 N / 加前後綴。結果可一鍵全部複製到剪貼簿（含 toast 提示），或下載 .txt / .csv（含 BOM 給 Excel）/ .xlsx（freeze pane + 欄寬）。設計成通用 line-pipeline，未來可繼續加新 op 不破壞現有 UI。預設權限：default-user 即可使用。

---

## [1.7.54] - 2026-05-13

### 修正

- **`translate-doc` 匯出 PDF 短列空白**：v1.7.53 修了 CJK 字型載入後，使用者反映多行的列正常但單行短列（`Login` / `Secure` / `*Email` 等）仍空白。根因：`page.insert_textbox` 對 `fontsize=10` 需要 rect 內高 ≥ 18pt 才會渲染，rect 不足時靜默回負數（`h=13` 回 `-4.36`，`h=16` 仍回 `-1.36`）。原 `line_h=13` → 單行 row 內高只有 13pt → insert_textbox 拒絕。修法：`line_h` 提到 20，單行 row_h = 28，內高 20pt 留足餘量。同時 `_est_lines()` 改估 CJK 字寬 1.6x ASCII，避免長 CJK 譯文被擠超出 rect。

---

## [1.7.53] - 2026-05-13

### 修正

- **`translate-doc` 匯出 PDF 中文亂碼**：v1.7.52 寫的 `_build_pdf` 呼叫 `font_catalog.best_cjk_path("sans", True)` ── 第二參數 `cjk` 應該是 `"traditional"` / `"simplified"` 字串，傳 `True` 永遠 lookup miss → 回 `None` → 落到 `helv` 字型 → 中文全變方框 / 缺字。修法：傳 `"traditional"`，並正確解包回傳的 `(Path, ttc_idx)` tuple。TTC 字型用 `set_simple=False`（PyMuPDF CID 字型）。.30 上實測 NotoSansCJK 載入成功，原文 + 譯文 CJK 都正確顯示。

### 改進

- **`translate-doc` 匯出試算表原文 / 譯文兩欄不同色系**：之前 xlsx / ods 只有交替橫條淡灰；現在原文用暖黃（`#FFFBEB` / 偶數列 `#FEF3C7`），譯文用冷綠（`#ECFDF5` / 偶數列 `#D1FAE5`）。色系區分讓使用者一眼看出哪欄是原文、哪欄是譯文，配合交替列也保留可讀性。

---

## [1.7.52] - 2026-05-13

### 改進

- **`translate-doc` 並排對照面板 summary 簡化**：summary 只放「2. 並排對照」標題，meta 文字 + 5 顆按鈕（複製譯文 / 對照 + 3 個匯出 dropdown）改放進面板內第一條 toolbar，標題列乾淨清爽。
- **`translate-doc` 5 種匯出格式加樣式 + 頁首 meta**：之前 docx / odt / pdf / xlsx / ods 都是純內容、無樣式，標題沒突出、欄寬參差、沒對齊。改進：
  - **docx**：藍底白字粗體標題列、表格框線、欄寬均分 8cm、交替橫條淡灰底、文件最上方加 heading「逐句翻譯對照」+ meta（原檔名 / 翻譯時間 / 共 N 對）
  - **odt**：同 docx 用 ODF Style — 標題列藍底白字、淡灰交替列、欄寬 8cm、頁首 heading + meta 文字
  - **pdf**：A4 兩欄、藍底白字標題列、交替列淡底、外框線、首頁標題 + meta；CJK 字型自動載入
  - **xlsx**：藍底白字粗體 freeze 標題列、A/B 欄寬 60、儲存格 wrap_text、交替淡底、A1 起兩列 meta
  - **ods**：同 xlsx 用 ODF Style — 藍底白字、欄寬 8cm、交替列、頁首 meta
- **`translate-doc` 匯出檔名帶原檔 stem**：例「公司簡介_2026.pdf」→ 匯出 docx 變「公司簡介_2026_translated.docx」（沒上傳檔則 fallback「translated.{fmt}」）。Filename stem sanitize 過濾 `/ \ ..` 防 traversal。
- **`translate-doc` 匯出 metadata（檔名 + 翻譯時間）**：5 種文件 / 試算表格式頁首加「原檔：xxx ・ 翻譯時間：YYYY-MM-DD HH:MM ・ 共 N 對」；文字檔（txt / md / csv）保持純內容不加 meta。

---

## [1.7.51] - 2026-05-13

### 新增

- **`translate-doc` 匯出 8 種格式**：「複製譯文」「複製對照」按鈕旁加 3 顆下拉按鈕，總共 8 種格式：
  - **匯出文字檔**：`.txt`（原譯對照空行分隔）、`.md`（Markdown 表格）、`.csv`（兩欄 source,target 帶 UTF-8 BOM）
  - **匯出文件檔**：`.docx`（Word 兩欄表格）、`.odt`（OpenDocument 文字）、`.pdf`（兩欄表格 A4，CJK 字型自動載入）
  - **匯出試算表**：`.xlsx`（Excel）、`.ods`（OpenDocument 試算表）

  後端 `POST /tools/translate-doc/export` 接 `{format, pairs}` 回對應檔。新增 `openpyxl>=3.1` 依賴（Excel 用）。每種格式都跑 `asyncio.to_thread` 不阻塞 event loop。1 萬對上限保護。

### 改進

- **`translate-doc` 單格複製按鈕加 toast 訊息**：之前點原文 / 譯文右下角複製按鈕只有按鈕短暫變色，使用者不確定有沒有複製到。改為彈 toast「已複製原文 / 譯文（N 字）」；複製失敗（瀏覽器禁剪貼簿）也會明確提示。

---

## [1.7.50] - 2026-05-13

### 安全

清空 GitHub CodeQL 在我們代碼裡的 alerts（v1.7.46 推完累積 22 個 open，扣除第三方 vendor library）：

- **`app/core/tessdata_manager.py` 11 個 path traversal alert**：`_variant_path` / `_active_path` 用 `tessdata / f"{code}.{variant}.traineddata"` 拼路徑；雖然入口（`install_lang` / `switch_active_quality`）有 `is_valid_lang_code` 白名單檢查，但 CodeQL 靜態分析追不過函式呼叫鏈。修法：兩函式內加嚴格 input validation（`_LANG_CODE_RE` + `_VARIANT_WHITELIST = {fast, best}`）+ `safe_paths.safe_join` 確保檔案路徑落在 tessdata 內（defense in depth + clear analyzer）。
- **`app/tools/pdf_ocr/templates/pdf_ocr.html:571` DOM text XSS alert**：`summaryEl.innerHTML = '...' + list` 把 chip textContent 拼進 HTML。雖然來源是我們 server-rendered 信任資料，但 CodeQL flag DOM-text-as-HTML pattern。改用 DOM API（`createElement` + `textContent` + `appendChild`）一個 node 一個 node 組，避免 innerHTML 注入。

### 新增

- **壓力測試框架 `tests/stress/run_stress.py`**：模擬 N 個使用者並行打 7 個工具 API（5 輕型 + 2 重型），量化吞吐 / latency p50/p95/p99 / 錯誤率。weight 機制（輕 3× / 重 1×）模擬真實混合負載。詳見獨立文件 [STRESS_TEST.md](STRESS_TEST.md)。

### 已知議題（CodeQL alerts 待 dismiss）

下列為第三方 / 測試程式 alert，技術上非真實風險，留待 GitHub 上 dismiss：
- **PDF.js vendored 5 個 alert**（`static/vendor/pdfjs/build/pdf.worker.mjs` / `web/viewer.mjs`）：Double escaping / Overly permissive regex range / Client-side URL redirect — 第三方 library 不修
- **`tests/test_template_js_syntax.py` 1 個 alert**：「Bad HTML filtering regexp」用 regex 抽我們自己 templates 內的 `<script>` 跑 syntax check，不處理 user input，非安全邊界

---

## [1.7.49] - 2026-05-13

### 修正

- **Win11 winget 裝完 Java 後 admin 仍報「找不到 java 執行檔」**：根因兩件事 ① service process 啟動時繼承 PATH，winget 裝完 Java 改了系統 PATH 但**舊 process 還是舊 env**，`shutil.which("java")` 找不到；②Eclipse Temurin / Microsoft OpenJDK / Zulu / Corretto 等發行版預設裝在 `C:\Program Files\Eclipse Adoptium\jre-21.x\bin\java.exe` 之類路徑，沒 PATH 也應該能偵測到。修法：跟 tesseract issue #4 同模式，新增 `_find_java_binary()` helper：先 `shutil.which`，找不到就 glob `C:\Program Files\Eclipse Adoptium\*\bin\java.exe` / `Microsoft\jdk-*` / `Java\*` / `Zulu\zulu-*` / `AdoptOpenJDK\*` / `BellSoft\LibericaJDK-*` / `Amazon Corretto\*` 等常見位置；macOS / Linux 也順手加 fallback。客戶 winget 裝完不用重啟服務也能立刻偵測到。

---

## [1.7.48] - 2026-05-13

### 改進

接續 v1.7.47 issue #17 修復的三道防禦，涵蓋更慢機器：

- **Frontend `/admin/api/sys-deps` 拉長 timeout 90s + 顯示等待秒數**：之前用瀏覽器預設 fetch timeout（各家不一），慢機器 5-10s 可能撐不到。改用 `AbortController` 顯式 90s 上限，loading 文案多一個秒數計時 → 使用者知道後端還在跑沒當掉。錯誤訊息也分流：AbortError → 「檢查逾時」、Failed to fetch → 「無法連到後端」、其他 → 顯示 status code。
- **Backend probe 結果記憶體快取（60s TTL）**：`/admin/api/sys-deps` 回應加入 cache 機制，慢機器多 tab 開或頁面 refresh 不重跑全套 5-10s 的 probe。`?force=1` 跳過快取（重新檢查按鈕用）。回應多 `cached` / `cache_age` 欄位給除錯用。
- **`collect_sys_deps` 包 `asyncio.to_thread`**：之前 sync function 直接 await 會阻塞 event loop（FastAPI workers 共用 loop，此期間其他 request 無法處理）。改丟到 worker thread 跑，不阻塞主 loop。

---

## [1.7.47] - 2026-05-13

### 修正

- **Win11 相依套件檢查「Failed to fetch」**（issue #17）：實測 Win11 上 `/admin/api/sys-deps` 響應 23.6 秒，瀏覽器 fetch 撐不到。雙重根因：①`_probe_python_pkg("easyocr")` 直接 `__import__("easyocr")` 會觸發 PyTorch 載入（~700MB，5-15 秒），CLAUDE.md 早就警告過；②所有 9 個 probe 串行跑，office (5s) + java (5s) + tesseract (3s) 累計 13s+。修法：①`_probe_python_pkg` 加 `heavy=True` 模式，用 `importlib.util.find_spec` 純檔案系統判定 + `importlib.metadata.version` 讀 distribution metadata（不執行 module）；easyocr probe 套用；②`collect_sys_deps` 改用 `ThreadPoolExecutor(max_workers=8)` 平行跑 probes，總時間 = max(probe time) 而非 sum，預期從 23s → ~5s。

---

## [1.7.46] - 2026-05-13

### 改進

- **`pdf-editor` 屬性面板改字型 / 字級 / 顏色立刻看到變化**：之前 IText 是 `opacity:0.01` 隱形，使用者改屬性看不到 IText，要等 BG 重畫完才在 BG 看到新樣式 (~2 秒)。修法：①rerender() 已有 `_peSaved=false + opacity:1` un-fade 邏輯（v1.7.x 既有）；②再加 v1.7.45 的 cover 機制 — 蓋住 BG 舊樣式那一片 → 使用者立刻在 IText 上看到新樣式生效，BG 舊樣式被 cover 遮住。改字型 / 字級 / 顏色 / B / I / U 立即生效。
- **新增物件（T/I/R 等工具）也享有 cover-on-modify 視覺**：v1.7.45 把 `_peId` 改為「第一次 save 時自動補」（之前只有 picked 物件有 _peId）+ `addModifyCover` helper 統一處理。新建文字 / 圖片 / 矩形 / 線段 / 箭頭 / 橢圓 / 手繪 / 螢光筆 / 便箋等，第一次 save 後都自動有 _peId / _lastFlattenedBbox，後續拖曳 / 縮放 / 改屬性都套用 cover。
- **重構 `addModifyCover` helper**：把 v1.7.45 寫在 `object:modified` 內的 cover 邏輯抽成 helper（`opts.force` 控制「跳 dx/dy 過濾」），讓 `object:modified`（拖曳）跟 `rerender`（屬性面板）共用。維護單一 cover-add 邏輯。

---

## [1.7.45] - 2026-05-13

### 改進

- **`pdf-editor` 拖動後 OLD 位置即時淡化提示**：v1.7.44 雖然拖完立刻在新位置看到 IText，但 OLD 位置 BG 還沒重畫前仍是「原文字」狀態 — 同時看到舊新兩個位置都有文字，視覺上不夠明確「移動了」。修法：`object:modified` 觸發時在 `_lastFlattenedBbox`（或新物件的 `_origBbox`）加一個半透明白色 cover（`rgba(255,255,255,0.7)`）蓋住 OLD 位置 → 讓使用者立刻看到「OLD 淡化、NEW 出現」。BG 重畫完 savePdf 在 `bgLoadedPromises.then` 內清掉所有 `_peCover`。
- **嚴格按 obj 清理 cover 不累積**（v1.7.10–14 的 `_peCover` 累積 bug 教訓）：①add 新 cover 前先 remove 該 obj 的舊 cover（per-obj 嚴格 1:1）；②加新 cover 後也跑 `ownerIds` check 清掉「owner 已不存在」的孤兒 cover；③save 完成 / 失敗都統一清掉所有 `_peCover`；④只在「真的移動 >2px」才加 cover，避免單純點選也加。
- **`_peId` 一律設**：之前只有 picked 物件有 `_peId`（addRedactMarker 給的），新建 T/I 物件沒有。改為 savePdf 第一次平面化某物件時若 `!o._peId` 就補 `auto_xxxxxx` ID，給 `_peCover` 對應 obj 用。

---

## [1.7.44] - 2026-05-13

### 改進

- **`pdf-editor` 拖曳文字框後立刻看到新位置**（不用等 ~2 秒重繪）：之前 v1.7.42 把 IText 設 `opacity:0.01` 隱藏避免雙影，但拖曳後 IText 仍是 0.01 → 使用者要等 1.5s debounce + ~500ms BG 重繪才看到變化。修法：①`object:modified` 觸發時把 IText 還原 `opacity:1`，使用者**立刻**看到文字「跑到新位置」；②savePdf 把 `opacity:0.01` 淡化操作從 backend 回應後移到 `bgLoadedPromises.then(...)` 內 — 等 BG 真的有新位置內容了，IText 才隱形交棒給 BG。整個過渡無雙影也無延遲感。

---

## [1.7.43] - 2026-05-13

### 修正

- **`pdf-editor` 移動文字壓在 logo 上時 OLD 位置變白方塊**：v1.7.29 把 `apply_redactions` 的 `images` 從 `IMAGE_NONE` 改回預設 `IMAGE_PIXELS`（為了讓「移動既有圖片」OLD 位置消失），但這會把**文字 redact rect 範圍內的圖片像素也清掉** → 文字壓在 logo 上，移走後 logo 那塊變白。修法：分兩階段 `apply_redactions`：①Pass 1A 處理文字 / drawing / widget redact，用 `IMAGE_NONE` 保留圖片像素；②Pass 1B 處理 image 物件 redact，用 `IMAGE_PIXELS` 清圖片像素。文字移動不再傷害底下圖片，圖片移動仍正常清除 OLD 位置。

### 修正

兩件 pdf-editor 「pick 完仍見雙影 + 拖不動」根因修正：

- **pick 完 IText / Image 直接 `opacity: 0.01` 隱藏**：原 PDF 文字仍在 BG 上、Fabric overlay 同位置同內容 → 不論 overlay 多透明都會看到雙影。修法：IText 一建出來就 opacity 0.01，使用者只看到 BG（內容相同），但 Fabric selection handles 仍可見不丟選取。雙擊進文字編輯模式 (`text:editing:entered`) 自動還原 opacity 1 給使用者看打字。完全沒視覺雙影。
- **移除 pick 路徑的 `lockUntilMouseUp`**：這個 helper 假設「click → 同步建物件 → 同次 mouse:up 解鎖」。但 pick 是 async (要等 detect-objects + OCR 回來)，IText 建好時原 click 早就 mouse:up 過了 → 鎖被延後到「使用者下次拖完放開」才解 → 整段拖曳被鎖住沒反應，要點空白處再回來才正常。pick 是 async 不需要這個保護，整段拿掉。

### 修正

- **`pdf-editor` 無 OCR 的 pick 仍看到 1-2 秒雙影**：v1.7.40 加的 `_keepLoadingUntilBgReady` 只在 `pickOverlayShown=true` 時才生效（OCR 過了 500ms 才設 true）。但無 OCR 情境（字型 Unicode 對應表完整，detect-objects 100ms 內就回）overlay 從未顯示 → 雙影 1-2 秒視覺很差。修法：pick 成功建出 IText / Image 後**一律** `showLoading('重繪中…')` + 設 flag，不論之前有沒有顯示過。savePdf BG 重畫完才 hideLoading。任何 pick 後雙影都被遮罩擋住直到 BG 真的乾淨。

---

## [1.7.40] - 2026-05-12

### 改進

- **`pdf-editor` OCR loading overlay 保留到 BG 真正重畫完才收**：v1.7.38 把雙影時間從 1.5s 壓到 ~300-500ms，但這段時間 loading overlay 已收 → 使用者可能在 BG 還沒清空、雙影仍在的狀態下誤觸（雙擊、拖、按鍵）。修法：新增 `_keepLoadingUntilBgReady` flag，pick 確定建出 IText / Image 後 set true，savePdf 在 `bgLoadedPromises` 全部 resolve 後才 `hideLoading()` + reset flag。BG 還沒重畫完 = loading 持續顯示 = 使用者不會誤觸。偵測失敗 / 沒擷取到物件等非 IText 路徑不受影響（cleanupPickOverlay 立刻收）。

---

## [1.7.39] - 2026-05-12

### 修正

- **`pdf-editor` 選既有物件後編輯區位移（toolbar 跑到視野外）**：客戶截圖顯示 OCR 完成、IText 建好後，整個編輯區莫名往上 / 往左捲了一段，頂端 toolbar 不見了。根因：`fabric.IText` 加進 canvas 時內部會建一個 `hiddenTextarea` element 並 append 到 `document.body`，瀏覽器在這個 input 進入 DOM 時可能 auto-scroll 把它「捲進視野」。修法：pick 開始前快照 `window.scrollX/Y` + `canvasWrap.scrollLeft/Top`，finally 區塊強制還原（並在下一個 frame 再還原一次以涵蓋 fabric next-tick 的 focus）。

---

## [1.7.38] - 2026-05-12

### 修正

- **`pdf-editor` 第一次選既有物件後文字雙影**：v1.7.37 把 autosave debounce 拉長到 1500ms 之後，pick 完原 PDF 文字（BG）跟 Fabric IText overlay 同位置疊著看到雙影 1.5 秒（例：「NXLog」糊成兩層）。修法：新增 `triggerPickSave()` helper — pick 完一律 150ms 後 save，覆蓋掉 `object:added` 的 1500ms debounce。BG redact 在 ~300ms 內套上、IText 淡到 0.01，雙影只持續一瞬。文字 / 圖片兩種 pick 都套用。

---

## [1.7.37] - 2026-05-12

### 改進

- **`pdf-editor` autosave 用輕量序列化大幅加速 (A)**：autosave 高頻觸發但每次都跑 `doc.save(garbage=4, deflate=True)` 整理乾淨太慢。修法：①save 端點接收 `is_auto` 旗標；②autosave 用 `garbage=1` + 不 deflate（~2-3x 快），允許暫時殘留未引用物件；③manual save（按「儲存並預覽」/「下載」）用 `garbage=4 + deflate=True` 整理乾淨。下載按鈕的 forced save 從 isAuto=true 改成 isAuto=false 確保最終下載 PDF 內部一定乾淨。
- **`pdf-editor` autosave debounce 拉長 (B)**：之前打字 300ms / 拖拉 30ms 太短，拖完放開幾乎立刻 save，連續操作會打斷重繪。改成打字 800ms、拖拉 / 縮放 / 加 / 刪 1500ms。是 true debounce — 使用者連續操作期間 timer 不斷被 reset，**只有停手後**才 fire 一次 save，把連續多次操作壓縮成一次。

---

## [1.7.36] - 2026-05-12

### 改進

- **`pdf-editor` 高解析 PDF 重繪 / 自動儲存加速**：使用者反應海報級 PDF（單頁 24"+ 寬）每次重繪要好幾秒。根因：所有預覽 PNG 一律 120 DPI，超大頁面渲染像素量爆炸（24" 寬 @ 120 DPI = 2880px → 14M pixels 一張）。修法：`pdf_preview.render_page_png()` 加 `adaptive=True`（預設）+ `compute_preview_dpi(page_w_pt, page_h_pt)` helper，限制預覽 PNG 最長邊 1800px，超大頁面自動降 DPI 達到此上限（24" 寬 → 75 DPI，2880×1920 → 1800×1200，render 速度約 2.5x）。下載出來的 PDF **完全不受影響** — PDF document 修改後直接 save，從來沒走 PNG render path。pdf-stamp / pdf-watermark / pdf-rotate 等其他預覽工具一併受惠。

---

## [1.7.35] - 2026-05-12

### 改進

- **檔案上傳 spinner 與「上傳 / 處理中…」標籤對齊修正**：原本 `.drop-zone.uploading::before` 跟 `::after` 兩個 pseudo element 用 `top:14px` 排在右上角，但 ::before 文字沒設 `white-space:nowrap`，drop-zone 較窄時會換行 → 文字跑到上方、spinner 在右下方對不齊。加 `white-space:nowrap` + 統一 18px 高度與 line-height，spinner 縮到 18px 與文字基線對齊。
- **`pdf-editor` 內容區加邊框**：高解析 PDF 沒水平捲軸時，右側內容是「正好對齊邊界」還是「超出」分不出來。`.pe-canvas-wrap` 加 1px 灰邊框 + inset shadow 把可視範圍框出來。
- **`pdf-editor` 頂列 chip 高度統一**：原本訊息列、整份換字型、設定、自動預覽、zoom-ctl、頁碼區、退出全視窗按鈕各自 padding 算出不同高度，視覺參差。改為 `.pe-top > *` 一律 `min-height:30px + box-sizing:border-box`，所有 chip 同高 — 包括 wrap 到下一行的「退出全視窗」也跟左邊訊息列等高。
- **`pdf-editor` 拿掉「選既有文字」黃色底色提示**：原本擷取出來的文字加 `backgroundColor: 'rgba(255, 230, 100, 0.25)'` 想當「已擷取」視覺提示，但 PDF 本身是透明 / 深色背景時黃色變灰塊蓋住原內容，視覺干擾大。紅虛線 redact marker 已能標示「原位置會被刪掉」，足夠識別。
- **`pdf-editor` redact marker 改透明 fill**：marker 原本用 `fill: '#ffffff'` 想預覽 redact 後白底，PDF 是深色 / 圖片背景時 opaque 白塊看起來像灰色方塊（客戶踩到 ── 公司簡介標題在深色 hero image 上被白塊蓋住）。改為 `fill: 'rgba(255,255,255,0)'`，紅虛線邊框仍標示 redact 範圍，使用者保留看見原 PDF 背景。save 完成後 BG 烘焙的 redact 才真正生效（fill=None 不畫白底，底層 vector 線條 / 顏色保留）。
- **`pdf-editor` OCR 訊息更明確**：`「OCR 辨識中…」` → `「原文字物件已平面化（字型 Unicode 對應表不完整），需經 OCR 辨識後重建可編輯文字…」`。讓使用者知道：①不是因為 PDF 是 scan ②是 PDF 內字型編碼表的問題 ③OCR 是還原可編輯文字的手段。
- **`pdf-editor` OCR 大標題加速**：之前所有需 OCR 的文字一律 300 DPI（高於 60pt 標題其實 200 DPI 足夠 95%+ 辨識率）。改為分級：bh<24 → 400 DPI、bh<60 → 300 DPI、bh≥60 → 200 DPI。大標題 OCR 速度約 2x。

---

## [1.7.34] - 2026-05-12

### 改進

- **`pdf-editor` 全視窗按鈕拆出來固定右側**：之前「全視窗」緊貼「符合視窗」黏在 `.pe-zoom-ctl` 內，視窗縮窄時跟 zoom slider 一起被擠。改成獨立按鈕放在 pe-top 最尾端，`margin-left:auto` 把它固定推到右邊界，狀態列在中間自然撐開。`flex:0 0 auto` 確保不會被壓縮。
- **`pdf-editor` 高解析 PDF 按「符合視窗」仍超出畫面**：客戶載入海報級 PDF（每頁 1500pt+），50% 縮放仍超出 → 之前 zoom 下限寫死 0.5，符合視窗算到 0.3 也被夾回 0.5。修法：zoom slider min 從 50 → 10、step 5；`fitPageToView()` clamp 下限從 0.5 → 0.1。海報、工程圖、高解析掃描檔都能縮進來。
- **`pdf-editor` Space 拖曳改綁 document capture phase**：v1.7.33 綁在 `canvasWrap` 的 capture-phase pointer event 偶爾被 Fabric upperCanvas 攔走 → 拖曳沒反應。改為 mousedown/move/up 全綁在 `document` capture phase + `stopImmediatePropagation`，比 Fabric 自己的監聽更早觸發，穩定攔下 Fabric 的 mouse:down。同時拿掉 `pe-can-pan` 條件 — Space 一律進 pan-mode 顯示 grab，沒捲軸時拖曳是 no-op（cursor 一致回饋）。

### 修正

- **`pdf-editor` 選既有文字框誤觸發 OCR 跑超久**：客戶踩到 ── 高解析 CJK PDF 上點純文字標題（如「採用自由與開源軟體」、「節省工具箱發展歷程」、「正式登記成立合作夥伴」），明明 PyMuPDF 可以直接抽出文字，卻跑了 5-15 秒 OCR。根因：`_looks_garbled()` 內 signal b) 「`cjk_count >= 8 AND common_hits == 0`」太激進 ── 真標題很常 8+ 字但完全沒 common particle（的 / 是 / 在 / 了 / 一），全被誤判 garbled 丟去 OCR。修法：撤回 signal b)，靠剩下三條（suspicious 符號、長字元重複、短週期模式）即可可靠偵測 Identity-H ToUnicode 壞掉的 garbage。
- **`pdf-editor` 工具列按鈕縮小省空間**：左排 V/S/T/I/W/R/L/A/O/P/H/N + 快捷鍵的圓鈕原本 40×40 / icon 22×22，改成 30×30 / icon 18×18，gap 從 4→3、padding 6/8 → 4/6。整條工具列高度省約 12px 給 canvas 用。

---

## [1.7.33] - 2026-05-12

### 新增

- **`pdf-editor` 空白鍵 hand-pan**：繪圖軟體慣例 ── 按住 `Space` → 內容區游標變手掌（grab），左鍵拖曳 → 捲動內容區（grabbing）。內容已完整顯示沒捲軸時不會啟用（游標仍是箭頭）。輸入欄 / Fabric IText 編輯模式中按 Space 視為正常輸入不會誤觸發。pointer event capture-phase 攔截 → 不會誤觸 Fabric 的選取 / 建物件。Esc / 切走視窗自動釋放。快捷鍵 popup 內加一行「Space＋拖曳 拖動內容區」說明。

### 改進

- **`pdf-editor` 後端異常時不再把 HTML / stacktrace 原文塞到訊息列**：客戶截圖顯示 `儲存失敗：<html><head><title>502 Bad Gateway</title></head>…<center>nginx/1.18.0</center>…` 整段塞滿訊息列。修法：新增 `friendlyServerError(response)` helper — ①按 HTTP status 對應到中文提示（502 → 後端服務無回應、504 → 後端逾時、503 → 服務暫時不可用、413 → 檔案太大、500 → 伺服器內部錯誤 等）；②JSON 回應抽 `detail/error/message` 欄位補充；③HTML / 長文字 / stacktrace 一律抽到 `console.error`，訊息列只放簡短描述 + status code（例：「後端服務無回應（502）」）。涵蓋上傳 / 儲存 / 整份換字型 / 復原 / 公開上傳 5 個 fetch 失敗點。

---

## [1.7.32] - 2026-05-12

### 修正

- **`pdf-editor` 切頁後在新頁建物件，右屬性區顯示舊頁殘留的物件屬性**：客戶踩到 ── 在 P1 選了文字框「Subscription Key:」後切到 P2 加矩形，畫面選的是矩形但屬性區仍顯示「文字框」+「Subscription Key:」內容。根因：每個 PDF page 有獨立 Fabric canvas，`getActiveObject()` 迭代所有 canvas 找到第一個 active 就回 → P1 殘留的 active 永遠勝出。修法：①`selection:created/updated` 時把「非來源 canvas」的 active 全部 `discardActiveObject` ②`gotoPage` 切頁時清掉所有 canvas 的 active 並重置屬性面板。矩形 / 橢圓 / 線段 / 箭頭 / 圖片等所有非文字物件都受惠。

---

## [1.7.31] - 2026-05-12

### 改進

- **`pdf-editor` 已被「選既有」抓過 / 標記為刪除的原 PDF 位置不再可重覆選取**：之前若使用者用「選既有」抓出文字後拖到別處，又對舊位置點「選既有」會再呼叫後端 `/detect-objects`，後端從原 PDF 看那位置「還有文字」（save 還沒套用 redact）→ 生出第二份擷取 + 第二個 redact marker，產生重複 redact / 重複擷取的混亂。修法：新增 `_isOrigPicked(fc, xPt, yPt)` helper，掃描當前頁面所有帶 `_origBbox` 的 Fabric 物件 + `deletedOrigs[pageIndex]` 累積的刪除清單，落在任一已選範圍 → ①hover frame 不亮（_findPickableUnder 直接回 null）、②點擊時 `pickExistingObject` 早退 + 提示「此位置的原 PDF 物件已被選取過」。

---

## [1.7.30] - 2026-05-12

### 改進

- **`pdf-editor` 術語統一：「烘焙 / bake」→「平面化 / flatten」**：原本程式碼註解、JS 函式名、CSS class、CHANGELOG 大量混用「烘焙 / bake / baked」（從英文 bake 直譯，非台灣 / Adobe Acrobat 繁中慣用語）。一次替換完：①CHANGELOG 全文 13 處 中文「烘焙」→「平面化」；②`pdf_editor.html` 13 處中文 + 英文註解；③CSS class `pe-baking` → `pe-flattening`；④JS 函式 `bakeStart/bakeEnd` → `flattenStart/flattenEnd`、`_bakeRefcount` → `_flattenRefcount`；⑤JS 變數 `_lastBakedBbox` → `_lastFlattenedBbox`、`bakedOwnerIds` → `flattenedOwnerIds`；⑥`router.py` 內 closure 名 `_do_bake` → `_do_flatten`。對外行為零變化，純語意一致性。PyMuPDF 自身 API `doc.bake()`、`pdf_annotations_flatten` URL 路由 `baked_uid` 等綁定第三方 / URL 穩定性的識別字保留不動。

---

## [1.7.29] - 2026-05-12

### 改進

- **`pdf-editor` 「選既有物件」期間加半透明遮罩 + spinner**：`pickExistingObject` 後端要走字型解析、OCR fallback、圖片擷取等，慢的話 1-2 秒。期間用 `showLoading('物件轉換中…')` 全螢幕遮罩 + spinner，OCR 階段升級訊息為「OCR 辨識中…」。300 ms 內回來不顯示，避免閃爍。其他工具（新增 T/I/R 等）不受影響。

### 修正

- **`pdf-editor` 拉動既有物件後 OLD 位置底下的頁框 / 表格線 / 背景色仍被白塊蓋掉**：v1.7.26 加 `graphics=LINE_ART_NONE` 想保留 vector，但 `add_redact_annot(fill=(1,1,1))` 在 apply 時還是會畫白底蓋住所有 redact 範圍 — 蓋過 line-art 跟有色 cell。修法：所有 `original_bbox` / `deleted_originals` redact 改成 `fill=None`（只移除矩形內的文字 / 圖片內容項，不畫覆蓋），底層 vector 線條 / 顏色保留原樣。配套：`apply_redactions` 移除 `images=PDF_REDACT_IMAGE_NONE` 改用預設 `PDF_REDACT_IMAGE_PIXELS`，這樣拉動既有圖片時 OLD 位置的圖才會清掉（之前 IMAGE_NONE 設定讓 OLD 圖留住 → 客戶看到雙影）。
- **`pdf-editor` 內容區頁面已完整顯示，捲軸仍可往右 / 往下拖動**：`#zoomStage` 用 CSS `transform: scale(zoom)`，但 transform 不影響 layout-box 尺寸 → stage 仍以子元素「未縮放」的原始尺寸佔位 → 父層 `.pe-canvas-wrap` 用原始尺寸畫捲軸，視覺已縮小但捲軸範圍沒同步。修法：`applyZoom()` 額外設 `stage.style.width/height = pageDim * zoom`，layout-box 縮成「縮放後可見尺寸」；子元素 transform 投影到該 box 內（origin 0,0）。切頁時也重套 `applyZoom()` 以更新尺寸。

---

## [1.7.28] - 2026-05-12

### 修正

- **`pdf-editor` 移動既有文字後，OLD 位置 redact 範圍跨越鄰行把別的內容也清掉**：客戶截圖顯示移動大號「Subscription」文字後，下一行「End Customer: JAIE HAOUR INDUSTRY CORPORATION」整段中間被剖開，只剩「End Cu」與「N」。根因：v1.7.23 為 partial overlap 案例做 union(OLD, NEW)，但 client 端 `wSafe = max(w, fontPt*2)` 安全墊讓 NEW bbox 比實際視覺寬一截（66pt 字 → 132pt 寬），union 一拉就跨到鄰行。**修法：永遠只 redact `original_bbox` + 2pt 邊距**。NEW 位置不需要 redact — insert_text 後續會把新內容寫上去，目標位置的 BG 本來就是 redact 之外的原內容。歷史包袱（v1.x 為「原地放大」加 union、v1.7.23 為「拉動」加 disjoint 檢查）一併捨棄。

---

## [1.7.27] - 2026-05-12

### 修正

- **`pdf-editor` 拖動既有文字後 NEW 位置出現「兩個重疊」殘影**：v1.7.21 修「OLD 復活」時改成「全部 include 平面化」，但平面化完還保留 `if (o === stillSelected) opacity:1` 給選中物件 → BG 已有平面化 + Fabric overlay 也滿不透明顯示 → 視覺重疊兩份。修法：選中與否一律 `opacity:0.01`，Fabric selection handles 仍會獨立繪製不丟選取。雙擊進編輯模式新加 `text:editing:entered` handler 自動 `opacity:1` 還原讓使用者能看到打字內容。
- **`pdf-editor` OLD 位置殘留紅虛線框**：之前 marker 平面化完只清白底 fill 保留紅虛線提示「redact 範圍」；使用者覺得殘留視覺干擾。改為平面化完成直接 `fc.remove(marker)` — OLD 位置原內容已被 redact 進 BG，marker 任務完成可移除。

---

## [1.7.26] - 2026-05-12

### 修正

- **`pdf-editor` 拉動既有圖片 / 文字後，OLD 位置底下的頁面框線 / 表格線被截斷**：客戶截圖顯示移動 logo 圖後，原位置橫穿的頁面邊框線在 image bbox 範圍內被切了一段。根因：`page.apply_redactions(images=PDF_REDACT_IMAGE_NONE)` 雖然保留了 redact 矩形外的圖內容，但**矩形內的 vector line art**（線條、邊框、表格線等）預設仍會被清掉。修法加 `graphics=fitz.PDF_REDACT_LINE_ART_NONE` flag — 只移除「使用者明確拉走的內容」（文字、圖片），背景線條一律保留。舊版 PyMuPDF（< 1.23）無此 flag → fallback 原行為。
- **`pdf-editor` 全視窗 + sidebar 收折時，左上殘留 `<h1>PDF 編輯器</h1>` 標題穿透**：`#editor-panel` 之外的 h1 / 提示文字 / 上傳區 `<div class="panel">` 仍在 DOM 內被部分視覺穿透。全視窗時用 CSS `display:none !important` 統一隱藏這些非 editor-panel 元素。

---

## [1.7.25] - 2026-05-12

### 修正

- **`pdf-editor` 全視窗時整個視窗仍可往右 / 往下捲動**：v1.7.24 改完內容區後仍見此問題；根因 `#editor-panel` 雖然 `position:fixed` 蓋滿視窗，但底下 body 與 main 維持原內容的自然高度（一堆未隱藏的 panel + 上傳區），導致瀏覽器仍認為頁面內容有溢出 → 右側 / 底部出現額外捲軸把使用者拉到「空白下方」。修法：`body.pe-max-window` 與 `html` 直接鎖 `overflow:hidden + height:100vh`（CSS `:has()` + JS 雙重保險，避免不支援 `:has()` 的瀏覽器漏掉）；退出全視窗自動還原。

---

## [1.7.24] - 2026-05-12

### 改進

- **`pdf-editor` 全視窗模式排版修正**：v1.7.23 全視窗時 `editor-panel` 用 `padding:12px 16px` 加 `overflow:auto`，導致 ①頂端有 12px 空白沒貼齊視窗、②縮圖欄 / 內容區 / 屬性面板總高超過 100vh → 出現外層橫捲軸把右側內容遮住。修法：改用 `display:flex; flex-direction:column`，`pe-top`/工具列 `flex:0 0 auto`，`pe-wrap` `flex:1 1 auto; min-height:0`，內部三欄各自 `overflow:auto; max-height:100%` 在自己 column 內捲。整個 panel 不再有外層捲軸，貼齊視窗頂部。
- **`pdf-editor` 快捷鍵按鈕移到工具列右側**：原本獨立佔一條的「快捷鍵」按鈕，移進水平工具列最右端（margin-left:auto 推到底），跟 V/T/I/W 等工具按鈕同高同風格；popup 改靠右對齊（`right:0`）避免超出右邊界。再省一條垂直空間。
- **`pdf-editor` 重繪改用游標 + 訊息列提示，移除半透明遮罩**：v1.7.22 加的 canvas 半透明白色 overlay + 「重繪中…」pill 移除；改成 ①body 加 `cursor:progress`（OS 原生 spinner 游標）+ ②上方綠色訊息列顯示「重繪中…」/「儲存並重繪中…」，取代視覺遮罩。canvas 整片無遮蔽、視覺更清爽，重繪狀態仍清楚。

---

## [1.7.23] - 2026-05-12

### 修正

- **`pdf-editor` 拉動既有 PDF 文字到頁面別處 → 中間出現一大塊白色誤蓋**：v1.x 的修法把 redact 範圍取 `union(原 bbox, 新 bbox)`（原意是「原地放大字型也要 redact 到新邊界」），但**沒考慮拉動 case** — 新舊 bbox 完全不重疊時，union 的 rect 從 OLD 位置橫跨到 NEW 位置，把中間所有原 PDF 內容全部 redact 掉。修法：先檢查兩 bbox 是否真的有重疊；不重疊（拉動）→ 只 redact OLD；有重疊（原地縮放 / 換字型）→ 沿用 union。下載出來的 PDF 跟視覺一致。

### 新增（接續上一版）

- **`pdf-editor` 快捷鍵列改 popup 按鈕**：原本一長條鍵盤對照表佔頂端空間；改成單顆「快捷鍵」按鈕，按下彈出 popup（含完整快捷鍵 + 層次 + 全視窗 + 翻頁鍵）。

---

## [1.7.22] - 2026-05-12

### 改進

- **`pdf-editor` 重繪期間加半透明 overlay + spinner**：使用者操作後 Fabric 立刻有反應（即時生效），但後端平面化與 BG 重繪仍需要 ~100-500 ms。期間在 canvas 上覆蓋 45% 半透明白色 + 中央深色「重繪中…」pill + 旋轉 spinner，告訴使用者「正在處理」，不擋互動（pointer-events:none，可繼續編輯，後續操作自動排隊）。BG 圖實際載完才收 overlay（不只是 backend 完成 response，而是新 PNG 也 onload）。

---

## [1.7.21] - 2026-05-12

### 修正

徹底檢查 pdf-editor 移動 / 重繪 / 殘影 / 誤蓋 等情境後三項根本修正：

- **移動既有 PDF 物件時 OLD 位置原文「復活」殘影**：active 物件被 save skip → backend 沒收到該物件的 redact 資訊 → BG 重新平面化時 OLD 位置回到 pristine（原 PDF 內容跑出來）→ 直到 deselect 才修正。修法：只 skip 「全新未平面化」的物件（既無 `_peSaved` 也無 `_origBbox`）；已平面化物件或從 PDF 擷取的，一律 include 到 save model 裡。BG 平面化跟 Fabric overlay 同位置同內容，無 ghost。
- **手動「儲存並預覽」改強制 includeActive**：之前 user 新加文字框 → 沒先 deselect → 按「儲存並預覽」→ 拿到的預覽缺那個文字框（save 跟 auto-save 同邏輯，skip active）。現在按鈕 click handler 一律帶 `{includeActive:true}` → 強制把 active 也平面化進 BG。
- **「下載」按鈕補先強制 save**：原本是純 `<a href>`，user 編輯到一半按下載 → 拿到的 PDF 缺最新 active 內容（auto-save 一直 skip active）。現在 click 攔截，先 `await savePdf({includeActive:true})` → BG / 下載檔 同步最新 → 再開新 anchor 觸發下載。

---

## [1.7.20] - 2026-05-12

### 改進

- **`pdf-editor` 工具列從左側直排改為上方橫排**：原本左側 60 px 寬豎排，新版改成編輯區頂端水平排列，騰出更多橫向空間給編輯區 + 左縮圖欄。Tooltip 也從「按鈕右側」改為「按鈕下方」。

---

## [1.7.19] - 2026-05-12

### 改進

- **`pdf-editor` 改成左邊頁面縮圖、右邊單頁編輯**：原本所有頁直向堆疊，多頁文件畫面雜亂。改成左側 132 px 寬縮圖欄（每頁一個 mini PNG + 頁碼），點縮圖切換要編輯的頁；右側編輯區只顯示當前頁。
  - 縮圖隨 save 自動刷新（與 BG image 同步）— 利用 single-page incremental bake，只更新有變動的頁
  - 鍵盤導頁、頁碼輸入框照常運作
  - 雙擊縮圖 / 用箭頭按鈕切換時，縮圖捲動到視野內
  - 視覺更聚焦：每頁佔滿編輯區，無須上下捲動

---

## [1.7.18] - 2026-05-12

### 改進

- **`pdf-editor` single-page incremental bake**：多頁 PDF 改一頁時，後端只重新渲染那一頁的 preview PNG，其他頁沿用上次的。
  - 50 頁 PDF 改第 3 頁：bake 時間從 ~3-10 秒降到 ~0.1-0.3 秒（**~50x 快**）
  - 前端用 `dirtyPages` Set 追蹤哪頁有變動（`object:added/modified/removed/text:changed` 觸發時記錄），savePdf 把這個 set 送給後端
  - 後端拿到 `dirty_pages` 陣列：只 `render_page_png` 那些頁，其他頁的 PNG 檔保留
  - 前端也只 cache-bust + 重載 dirty 頁的 `<img>` src（避免無謂網路 request）
  - **向後相容**：前端不送或送空陣列 → 維持舊行為（重新渲染全部）。第一次 save / PNG 檔不存在 → 強制渲染。
  - 視覺、正確性零改變：仍是同一個 PyMuPDF 平面化，只是「沒改的頁不重做」。

---

## [1.7.17] - 2026-05-12

### 新增

- **`pdf-editor` 後端 save 加 per-upload queue + global semaphore**：解決多 user 同時編輯時 PyMuPDF 平面化撞 CPU 的瓶頸。
  - **Per-upload Lock**：同份 PDF 同時間最多一個 bake；同一 user 連續拖拉產生的 /save 自動排隊（不會 10 次拖拉開 10 個並行 bake）
  - **Global Semaphore**：全域同時最多 N 個 bake，N 預設 = `min(8, CPU×2)`，可用環境變數 `JTDT_SAVE_CONCURRENCY` 覆寫；保護 backend 不被突發流量打爆
  - 新 module `app/core/save_queue.py`；`tests/test_save_queue.py` 7 案覆蓋
  - 視覺 / 正確性零變化：仍是同一個 PyMuPDF 平面化，只是少做重複工 + 限總量

---

## [1.7.16] - 2026-05-12

### 改進

- **`pdf-editor` 自動預覽改「動作完成幾乎立刻重繪」**：拖拉 / 縮放 / 新加 / 刪除 / 手繪結束等「明確結束」的事件 debounce 從 800 ms 降到 30 ms，使用者拖完位置不用點別處就會立刻看到 BG 重繪。打字（text:changed）仍保留 300 ms（從 800 ms 降）防止連按時頻繁 thrash。

---

## [1.7.15] - 2026-05-12

### 修正

- **`pdf-editor` 撤回 `_peCover` 白罩機制**：v1.7.10 到 v1.7.14 嘗試用「立刻插白罩遮蓋舊平面化位置」加速視覺反饋，但這個 hack 連續產生問題：白罩累積、白罩比新物件大、看起來像永久 redact 把使用者搞糊塗。完全移除白罩、改回讓 BG image 自然非同步替換。短暫的「BG 舊內容 + Fabric 新內容」overlap (~300-500 ms) 是可接受的代價。`object:modified` 內保留「清掉任何遺留 _peCover」的防呆，舊版客戶升上來不會有殘留白罩。
- **發版前必跑 inline JS 語法檢查**：v1.7.14 慘案 — `pdf_editor.html` 改 catch block 多敲一個 `}` → SyntaxError → drag-drop 整個失靈。新加 `tests/test_template_js_syntax.py` 自動掃所有 HTML template 的 inline `<script>`，用 `node --check` 驗證；CI 與本機 pytest 都會跑到。CLAUDE.md 同步補上 SOP 條目。

---

## [1.7.14] - 2026-05-12

### 修正

- **`pdf-editor` 反覆移動物件後，原 PDF 框線被白罩永久蓋斷**：v1.7.11 加的 `_peCover` 白罩本意只是視覺暫時掩蓋舊平面化位置，等 BG 重載完移除。但只有 `includeActive=true` 路徑會清，一般 move 的 save 不清 → 多次移動後白罩在 canvas 上累積永久蓋住底層 PDF 線條（下載出來的 PDF 沒此問題，僅前端視覺）。修法：①`object:modified` 加新 cover 前先清掉舊 cover（防累積）；②任何 save 完成後（不論 includeActive）都清 cover；③save 失敗時也清，避免卡在畫布。

---

## [1.7.13] - 2026-05-12

### 新增

- **`translate-doc` 文件領域常用 chips**：8 個常用領域（法律合約 / 醫療報告 / 軟體技術文件 / 財務報表 / 學術論文 / 新聞稿 / 商業合約 / 網路 / IT 維運文件）一鍵點亮，右側保留 240 px 文字框可自填。
- **`translate-doc` 兩個面板都可摺疊**：「1. 來源文字」「2. 並排對照」改用 `<details>` + 標題列點擊收折 / 展開（與 OCR 結果頁一致風格）。
- **`translate-doc` 自製語言下拉**（取代瀏覽器原生 `<select>`）：跟系統其他下拉視覺一致；鍵盤 Up/Down/Enter/Esc 支援；點外部自動關閉。

### 修正

- **`pdf-editor` 文字框拖拉縮放後字級沒跟著放大，BG 平面化小字 + Fabric 顯示大字殘影**：Fabric 預設縮放 IText 只改 `scaleX/Y`，`fontSize` 不變 → backend 在拉大的 bbox 內仍用原 fontSize 平面化 → 視覺「大框內小字 + 旁邊有大字殘影」。修法：`object:modified` 事件偵測到文字物件 scale ≠ 1 時，把 scale 吸收進 `fontSize`（與 `width`），scale 還原為 1，立即重算維度。屬性面板字級欄位也同步刷新。
- **`pdf-editor` 全螢幕按鈕改全視窗模式**：用詞修正 — OS 級全螢幕（Fullscreen API）改成 in-app 全視窗（編輯區擴到側欄右側整個 main 區域），保留左側功能列方便切回別的工具。Esc 退出。
- **`translate-doc` 純標記行送 LLM 會收到雜訊**：例 ` ``` ` （markdown code fence）、`---` `***`（horizontal rule）、純 URL、表格分隔線等送 LLM 會回「Please provide the text to translate」。新加 `_is_no_translate()` 過濾，這類行直接 passthrough、譯文欄留空。
- **`translate-doc` markdown 列表行首符號遺失**：`- 裝置雙向同步` 翻譯後變 `Two-way device synchronization`（少了「- 」）。修法：`_split_line_prefix()` 抽出行首符號（`- ` `* ` `+ ` `1. ` `> ` `## ` 與縮排、checkbox 等），只送內文給 LLM，譯文回來再補回行首符號；防 LLM 自己又加同樣符號造成重複。
- **`/admin/sys-deps` 標籤精簡**：「OxOffice / LibreOffice 執行時依賴 X11 lib」→「OxOffice / LibreOffice X11 函式庫」。

---

## [1.7.12] - 2026-05-12

### 新增

- **`pdf-editor` 物件層次（z-order）控制**：屬性面板加四個按鈕 — 置頂 / 上一層 / 下一層 / 置底（Adobe Illustrator icon 風格）。鍵盤捷徑：`]` 上一層、`[` 下一層、`Cmd/Ctrl+]` 置頂、`Cmd/Ctrl+[` 置底。Markers 永遠強制保持在最底層。
- **`pdf-editor` 全螢幕模式**：頂端工具列加「全螢幕」按鈕，按了把整個編輯區（工具列 + canvas + 屬性面板）擴到整個視窗；Esc 或再點按鈕退出。退出時自動 fit 一次保持整頁可見。

### 改進

- **`pdf-editor` 警語「不會 reflow」改純中文「不會自動斷行重排」**：避免英文夾雜。
- **`/admin/sys-deps` 改 AJAX 載入 + spinner**：之前一進頁要等 ~4 秒（subprocess 探 binary、import pytesseract 等），現在頁面框架先秒開、表格內顯示「正在檢查系統相依套件…」+ spinner，背景 fetch `/admin/api/sys-deps` 完才填內容。重新檢查按鈕也走同樣 flow，不需重新整理整頁。
- **套件名稱有括號說明時自動斷行**：例「Office engine (OxOffice / LibreOffice)」、「Java Runtime (OxOffice / LibreOffice 部分匯入需要)」括號部份顯示為第二行 muted 小字，第一行更短更易掃過。
- **狀態 pill 改純符號**：「✓ 就緒 / ⚠ 缺 / ✗ 缺」改成只顯示符號 ✓ / ⚠ / ✗，pill 寬度從 90 px 縮到 60 px，省掉橫向空間。
- **OCR 引擎描述用語潤飾**：把「CJK 強」「CJK 識別率強」改成「中日韓辨識準確度高」（README / docs/index.html / admin/ocr-langs / sys-deps）。
- **介紹網站「稽核員角色 + 強制 2FA」表格重新設計**：表頭 indigo 漸層底、圓角邊框、隔行 striped、ok / no 改 pill 樣式（綠 / 紅淺底圓角），整體更專業一致。
- **少用「—」，改用「：」做標題分隔**：「稽核員角色 + 強制 2FA — 郵件歸檔風格的合規分離」改成「稽核員角色 + 強制 2FA：郵件歸檔風格的合規分離」；docs/index.html meta description / footer-tag / 「文件去識別化 — 編修結果預覽」h3 同步調整。鋪陳性的破折號保留。
- **`/admin/sys-deps` 套件名稱說明排版**：括號描述改顯示為 muted 小字（不重複「（）」符號），與套件名靠近 (margin 2 px)；下方 binary 路徑加 14 px 空行間隔，視覺層次更清楚。

---

## [1.7.11] - 2026-05-12

### 修正

- **`pdf-editor` 移動既有圖片 / 文字後舊位置短暫殘影**：前次 save 把物件平面化在 BG 的 OLD 位置；user 把物件拖到 NEW 位置後，要等 ~1 秒 save 完成 + BG 重載才看到 OLD 位置消失。改成 `object:modified` 觸發時立刻用白罩（`_peCover`）蓋住 OLD 平面化位置（`_lastBakedBbox`，由 savePdf 在每次成功平面化後設定；剛擷取的物件 fallback 用 `_origBbox`）；BG 載完 savePdf 自動移除白罩。視覺上即時生效，無 ghost。

---

## [1.7.10] - 2026-05-12

### 修正

- **`pdf-editor` 改字型仍有 0.x 秒視覺延遲**：v1.7.9 改成等 BG `<img>.load` 才淡掉 IText，但這會等網路 + bake 來回 ~200-500 ms。本版改成「立刻插白色矩形」蓋住舊 BG 的平面化文字（`_peCover` marker），上面浮新字型的 IText，使用者選完字型即時看到新字型；BG 載完 savePdf 移除白罩 + 淡掉 IText。Save 失敗也會把白罩清掉以免卡在畫布。

---

## [1.7.9] - 2026-05-12

### 修正

- **Windows `jtdt bind 0.0.0.0` 失敗 `name 're' is not defined`**（GitHub issue #16）：`app/cli.py:svc_bind` Windows 分支用 `re.search` 解析 WinSW XML 但 `re` 模組未匯入。改在分支內 `import re` 修正。感謝 @nullkings 回報並提供精準診斷。
- **`pdf-editor` 改字型瞬間視覺重疊**（新舊字並存，要點空白才刷新）：autoSave 把新字 bake 進 PDF 後，背景 PNG 還沒載入完，前景 IText 已經顯示新字 → 舊 PNG（舊字）+ 新 IText（新字）兩層疊加。現等 BG `<img>` 真的 load 完才把 active object 淡掉（保留選取與屬性面板，仍可繼續調整其他屬性）。

---

## [1.7.8] - 2026-05-11

### 改進

- **`pdf-ocr` 結果頁兩個摺疊區塊統一風格**：「各階段詳細結果」與「怎麼確認是這個工具 OCR 的」改用同一個 `.po-stages` style，視覺一致。
- **介紹網站新增 OCR 文字辨識展示**（#08）：強調「掃描 PDF / 圖片變可選取文字」，配上實際 OCR 完成 + PDF.js 內嵌預覽的截圖。後續 #09-#12 順序調整。

---

## [1.7.7] - 2026-05-11

### 修正

- **`pdf-ocr` viewer iframe 載入 0 頁**：PDF.js 的 `?file=` 解析相對於 `viewer.html` 自身路徑（`/static/vendor/pdfjs/web/`），不是 parent 頁面路徑。改傳絕對路徑 `/tools/pdf-ocr/preview/<uid>.pdf` 修正。
- **「怎麼確認是這個工具 OCR 的」說明預設摺起**：用 `<details>` 包起來，點開才展開；OCR 結果頁更清爽。

### 測試

- 新增 `tests/test_pdf_ocr_preview_acl.py`（7 案）端到端驗證 `/preview/{uid}.pdf` ACL：owner / 跨 user / 缺 record / admin override / 格式錯 / 檔案缺 / auth OFF 全部覆蓋。
- pytest 由 472 → 479 passed。

---

## [1.7.6] - 2026-05-11

### 新增

- **`pdf-ocr` 完成後直接在頁內預覽**：OCR 結果區下方新增「📄 預覽結果（直接拖選文字試試看）」嵌入式 PDF viewer，不用先下載再開啟。
  - 內建 [PDF.js](https://github.com/mozilla/pdf.js) v5.7.284（Apache-2.0），完整 vendored 在 `static/vendor/pdfjs/`（~9 MB），**不走 CDN、純地端運作**
  - 自動高度（80vh，min 520 px），「在新分頁開啟」按鈕保留
  - 隱藏 PDF.js 不必要的工具列項目（編輯器、開啟其他檔、列印、書籤），保留 sidebar 切換 / 縮放 / 搜尋 / 旋轉 / 下載；客製 CSS 從 parent 注入，不修改 vendor 內 `viewer.html`，方便日後升級
  - 新後端 endpoint `GET /tools/pdf-ocr/preview/{upload_id}.pdf`（與 `/download/` 並列），inline 串流給 iframe 用；走既有 `upload_owner.require()` ACL，跨 user 隔離不破
  - CJK cmaps + standard fonts 都附帶，中文 PDF 顯示無方框
- **admin/sys-deps 新增「PDF Viewer (PDF.js)」條目**：顯示 vendor 版本、必要檔完整性、CJK cmaps 與標準字型狀態
- **`install.sh` / `install.ps1` / `jtdt update` 自動驗證 vendor 完整性**：缺檔時 warn（pdf-ocr viewer 會壞但下載仍可），通常表 git clone 中斷，re-clone 即修

---

## [1.7.5] - 2026-05-11

### 改進

- **`pdf-ocr` 語言 picker 統計列改放「中日韓 CJK」標題行右側**：原本「已選 N 種、列出 18 種」獨佔一整列且左側留白浪費；改成與 CJK 群組標題同列（標題靠左、統計靠右），整體更緊湊。

---

## [1.7.4] - 2026-05-11

### 修正

- **`pdf-ocr` 語言群組摺起邏輯**：預設摺起的群組（西方語言、東南亞 / 其他）若群組內有預設勾選的語言（例如「英文」預設勾選屬於西方語言群組）則自動展開，避免使用者看不到已勾選但被藏起來的項目。

---

## [1.7.3] - 2026-05-11

### 改進

- **`pdf-ocr` 語言挑選 UI**：不常用語言群組「西方語言」（英 / 德 / 法 / 西 / 義 / 葡 / 荷 / 俄）與「東南亞 / 其他」（越 / 泰 / 印尼 / 阿拉伯 / 希伯來 / 印地）預設摺起，使用者要勾選時點開即可；常用的「中日韓 CJK」群組保留展開。減少視覺雜訊，常用語言一眼到位。

---

## [1.7.2] - 2026-05-11

### 新增

- **新 admin 頁：`/admin/ocr-langs`（OCR 訓練檔管理）** — admin 可從 web UI 安裝 / 移除 tesseract 語言訓練檔。
  - 內建 18 種常用語言（繁中 / 簡中 / 日 / 韓 + 英 / 德 / 法 / 西 / 義 / 葡 / 荷 / 俄 + 越 / 泰 / 印尼 / 阿拉伯 / 希伯來 / 印地）以及自動偵測「其他已安裝訓練檔」
  - 安裝來源：[`tesseract-ocr/tessdata_fast`](https://github.com/tesseract-ocr/tessdata_fast) GitHub repo（Apache 2.0）
  - 自動偵測 tessdata 目錄（跨 Linux apt / macOS brew / Windows UB-Mannheim 三平台），跑 `tesseract --list-langs` 解析它印的路徑最可靠
  - **權限預檢**：service 帳號無寫入權限時（典型 Linux apt 場景），UI 上方紅字告知，按下「安裝」會跳出**該平台的具體 sudo 指令**（apt / curl / Invoke-WebRequest）讓 admin 直接複製執行；不會等下載跑完才失敗
  - eng / chi_tra 列為「核心語言」不可移除（避免被誤刪後 OCR 整組壞）
  - 安裝 / 移除走 atomic rename（`.part` tmp file 先寫完才 rename），避免半成品污染 tessdata
  - **upgrade-safe**：不需 DB migration，既有客戶 `jtdt update` 後自動有新頁；既有 chi_tra 自動下載機制（cli.py）共用同一 catalog，行為不變
- **`pdf-ocr` 語言 picker** 加 hint 連結「需要其他語言請至 OCR 訓練檔管理 安裝」
- **共用 catalog**：`app/core/tessdata_manager.py` 集中 18 種語言定義 + 安裝 / 偵測 helper，pdf-ocr router + admin/ocr-langs + cli.py chi_tra 三處共用

### 安全

- `tessdata_manager.is_valid_lang_code()` 嚴格白名單 + regex `^[a-z]{2,4}(_[a-z]{2,4})?$` — 擋住 path traversal（`../etc/passwd` 等）
- 寫檔目標路徑用 `Path / f'{code}.traineddata'`，code 已經白名單檢過

## [1.7.1] - 2026-05-11

### 變更

- **`pdf-ocr` 中文名改短**：「PDF 文字層補建」→「**OCR 文字辨識**」。原本太長 sidebar 顯示換行，h1 / sidebar / 下載按鈕「下載文字辨識後 PDF」全英→中。
- **加入「驗證是本工具 OCR 的」三招**：解決客戶測試時「macOS 預覽程式自動 OCR 過了，怎麼確認下載的 PDF 是本工具產生的而不是被預覽程式偷做的？」場景。
  - PDF metadata Producer 蓋章：`jt-doc-tools pdf-ocr v<版本>`，Acrobat / Preview「檔案 → 內容/簡介」可看到
  - PDF metadata Keywords 加 `OCR:tesseract-<版本> langs:<語言>`
  - 每處理過的頁左下角埋一個 `jtdt-pdf-ocr` 透明 marker word，Cmd+F 就能搜到
  - 完成畫面會列出三招驗證方式說明，user 直接照做
- **`pdf-ocr` 支援圖檔上傳**：新增接受 `.jpg / .jpeg / .png / .tif / .tiff / .bmp / .webp`，上傳後自動包成單頁 PDF 再走 OCR pipeline。
  - 解決使用者場景：macOS 掃完 PDF 後預覽程式自動 OCR，使用者拿不到「真的沒文字層的 PDF」沒辦法測；改成可直接丟掃描圖檔進來。
  - UI hint 加註「圖片會自動包成單頁 PDF」；上傳完成提示「已從 .png 自動轉成 PDF」。
  - 上限沿用 200 MB；圖片 → PDF 用 PyMuPDF 同尺寸頁包圖（一張圖一頁）。
- **`pdf-ocr` 語言選擇器全面美化**：
  - 原本 checkbox 太陽春、只列已安裝的少數語言碼。改成 chip / 標籤式設計：圓角、hover 抬升、選中藍底白字 checkmark
  - 內建 18 種常用語言（**繁中 / 簡中 / 日 / 韓** + 英 / 德 / 法 / 西 / 義 / 葡 / 荷 / 俄 + 越 / 泰 / 印尼 / 阿拉伯 / 希伯來 / 印地）分三群（CJK / 西方 / 其他）顯示，每個 chip 同時秀「中文名 + 語言碼 + hover tooltip 用途」
  - 未安裝的語言以虛線框 + 淡灰色 + 「(未安裝)」紅字標示，hover 提示如何安裝
  - 加 **快速套用按鈕**：「繁中 + 英文」/「繁中 + 簡中 + 英文」/「全部取消」
  - 即時摘要：「將使用 N 種語言：繁體中文 chi_tra、英文 eng」
  - 已安裝但不在 catalog 的訓練檔（如 osd 以外的冷門檔）也會列出，不丟掉

## [1.7.0] - 2026-05-11

### 新增

- **新工具：PDF 文字層補建（`pdf-ocr`）** — 對掃描 PDF / 影像 PDF 補上透明可選取的文字層。視覺跟原檔一樣，但變成可搜尋（Cmd+F）、可滑鼠選取複製、可送進其他文字抽取工具的「searchable PDF」— 同 macOS Preview Live Text 概念。
  - tesseract 多語（chi_tra+chi_sim+eng 自動偵測已裝的）+ word-level bbox 對齊
  - PyMuPDF `render_mode=3` 透明文字層
  - 可選跳過已有文字層的頁（避免重複 OCR）
  - DPI 可調（72-600，預設 300）
  - 背景 job + SSE 進度
- **LLM 加值（選填）**：tesseract 結果送 LLM 校正 typo / 字符混淆，再寫進文字層。`submission-check` 同款 opt-in 模式，未設 LLM 自動跳過。加進 `KNOWN_LLM_TOOLS` 清單，admin 可在 `/admin/llm-settings` 為此工具獨立指定 model。
- **default-user 角色預設可用**（同其他多數工具）

## [1.6.10] - 2026-05-11

### 修正

- **pdf-editor S 模式 filler 偵測過頭（誤擋 Q&A...... 等混合 span）** — v1.6.9 用 70% filler 占比判定，但「常見問題  Q&A........」這類 TOC 行整段 1 個 span，dots 占比也 > 70% 結果誤標 filler 點不了。改回保守規則：只有完全空白才視為 filler。其他內容（純 dots / dots+頁碼 / 文字+dots 混合）一律當可選文字，user 可選來編 / 刪 / 移；hover 浮現選取框也跟著恢復。
- **filler span 點下後改用 redact-only marker（紅虛線框）而非 IText overlay** — IText 重建會「點點消失」是因 sans-serif 沒 leader-fill 排版能力。改用 marker：點下標「將刪除」，可拖移位 / Delete 取消，儲存時 redact。User 想換內容請手動加文字框。

## [1.6.9] - 2026-05-11

### 修正

- **pdf-editor S 模式選 leader dots + 頁碼混合 span 仍誤選** — 「.................2」這種混合 span 因含「2」未通過 v1.6.7 的純 filler regex；改邏輯：純 filler **或**（filler 字元 ≥ 5 且占比 > 70%）都視為填充，hover 不亮、點下去顯示提示不 redact。同步套到 `/list-objects` 與 `/detect-objects`。

## [1.6.8] - 2026-05-11

### 修正

- **pdf-editor S 模式選 leader dots 變超寬 drawing** — TOC 的 dots 實際是 PDF 內的「薄橫線 vector drawing」(tab leader)，detect-objects 抓到後給 wide drawing bbox（覆蓋整行），redact 後變超長糊掉。修：detect-objects 加薄線過濾 — 寬高比 > 30 且高 < 3pt 的 drawing 跳過不選（同樣對直線）。

## [1.6.7] - 2026-05-11

### 修正

- **pdf-editor S 模式選 leader dots 後文字消失** — TOC 排版的 leader dots（「............」+ 頁碼）是獨立 span，user 無意中點到 → 被 redact + 重建 fabric 文字框時 dots 顯示失敗 → 視覺上「點點消失」。修：
  - `/list-objects` (hover frame source) 過濾掉純 dots / 純空白 / 純底線等 filler span，hover 不浮現
  - `/detect-objects` 加 `is_filler` flag；FE 偵測到改顯示提示「此位置是 TOC / 表格排版的填充字元，請改點該行的標題文字」，不執行 redact

## [1.6.6] - 2026-05-11

### 新增

- **pdf-editor 選取 / 選既有物件 模式 hover 浮現選取框** — 進入 V (選取) 或 S (選既有 PDF 物件) 工具時，游標 hover 在可選物件上方浮現虛線框：
  - V 模式：cyan 框（Fabric overlay 物件）
  - S 模式：text=藍 / image=綠 / widget=紫 / drawing=橘 — 顏色區分物件類型
  - 進入 S 模式自動 prefetch 該頁所有可選物件 bbox（新 endpoint `/list-objects`）
  - mouse:out 自動清除 hover frame

## [1.6.5] - 2026-05-11

### 修正

- **pdf-editor 文字框 ghost 真正完整修** — v1.6.4 只修自訂字型，但「自動 (china-t)」「Courier」等內建字型 `fitz.get_text_length` 對 CJK 也算錯（fallback Helvetica 寬度）。改規則：**任何 CJK run 一律 `font_size × len` 估算**，不再呼叫 `get_text_length`；ASCII run 內建字型才用 `get_text_length`、自訂字型用 `× 0.55` 估算。

## [1.6.4] - 2026-05-11

### 修正

- **pdf-editor 文字框 CJK + ASCII 混合內容字疊在一起 ghost** — 真正根因：`_insert_mixed_text` 用 `fitz.get_text_length(fontname=<custom>)` 算寬度推進 x 座標，但 `get_text_length` 不認 `page.insert_font` 註冊的自訂 NotoSansCJK / 微軟正黑體 → 默默 fallback 用 Helvetica 算寬度 → CJK 字寬被當成窄 ASCII → x 推進不夠 → 下一個 ASCII run（例「111」）疊在前一個 CJK run（例「請輸入文字」）上面。修：自訂字型走估算（CJK = font_size、ASCII = font_size×0.55），不再被錯誤的 Helvetica 寬度誤導。

## [1.6.3] - 2026-05-11

### 偵錯

- **pdf-editor /save 加 text obj 詳細 log** — 列出每次 save 收到的所有 text 物件 (id / 位置 / 字型 / 字級 / original_bbox / 文字)，方便排查字型變更後 ghost 殘留的根因（v1.6.2 修法不夠）。

## [1.6.2] - 2026-05-11

### 修正

- **pdf-editor 字型變更後文字殘留 / 重疊 ghost** — 用戶換字型 / 字級後 save，舊文字位置在 redact 範圍外有部分殘留，視覺上看到雙重疊文字。Pass 1 redact 範圍改用 `union(original_bbox, current x/y/w/h) + 2pt 邊距`，新舊位置都覆蓋 + 吃 anti-aliased 邊緣。

## [1.6.1] - 2026-05-10

### 新增

- **submission-check 升 6 層架構** — L1 規則 / L2 文字抽取 / L3 全頁 OCR / L4 嵌入圖 OCR / L5 LLM 文字 / L6 LLM 視覺
- **L4 嵌入圖 OCR** — PDF 內每張嵌入圖抽出來逐張 OCR（章 / 印 / 截圖型證書）
- **L6 LLM 視覺改一頁一次 call**（前 10 頁），加 60s timeout 防 hang
- **階段燈號 LED**（6 站）滿版顯示 + spinner + 細化 skip 原因
- **案件名稱欄位** — top-level，UI / 清單 / 詳情都顯示
- **案件詳情頁進行中自動 poll** + inline 進度燈號 + 中斷偵測（job 不見 → 標 error）
- **案件刪除 / 標記 / 重新檢核改用 showConfirm/showAlert/showPrompt**（不再用瀏覽器原生 prompt）
- **案件清單**：加檔名欄、人類可讀時間、表頭可排序、刪除 icon 化（owner 軟刪除、admin/auditor 仍可看歷史）
- **跨檔發現預覽按鈕** — 從 evidence.files 顯示檔名 + 跳對應頁預覽
- **L5 / L6 訊息含 model 名**（user 知道用哪個模型）
- **加 .xls / .xlsx / .ppt / .pptx / .odt / .ods / .odp 自動轉 PDF 處理**（同 .doc 自動轉 .docx）
- **incremental update 偵測改用 EOF marker 計數**（不再因 xref 多誤判）

### 修正

- 「Ground Truth」改「案件基準資訊」、「當前」改「目前」（台灣繁中用語）
- panel summary 內嵌 h2 致標題高度不一致 → 改成標題直接放 summary
- 各 layer 高度對齊（L1 補 extra 文字 + min-height reserved）
- 各 layer 用標準 spinner 動畫 + done 顯示勾勾、skipped 橫線、error 叉

## [1.6.0] - 2026-05-10

### 新增

- **新工具：送件前檢核（`submission-check`）— Sprint 1 + 2** — 一批文件送出去前的最後一關自查。設計三層檢核：L1 規則 / L2 OCR / L3 LLM。本版含 **L1 規則層** + **跨檔身分一致性核心（文字層）**，end-to-end 跑得通：
  - 案件 (case) 概念：每次檢核以 case 為單位，多檔混格式（PDF / DOCX / JPG / PNG）+ Ground Truth（主角 / 對方 / 案號 / 截止日）+ 多版本 snapshot。
  - L1 規則檢查：PDF metadata 殘留偵測、JS / OpenAction、嵌入檔、incremental update（修訂歷史）、表單空白欄位、跨檔 SHA-256 重複；DOCX track changes、comments、macro、core.xml 作者洩漏。
  - 跨檔身分一致性的 ground truth 抽出（公司名 / 統編 8 碼 + 校驗碼）。
  - 案件儲存於 `<data>/submission_check/<case_id>/`，包含 `case.json` + 原檔 + 多版本 reports。
  - Endpoints：`/tools/submission-check/`（上傳頁）/ `/cases`（清單）/ `/case/{id}`（詳情）/ `/upload` / `/run/{id}` / `/result/{id}/{ver}` / `/file/{id}/{file_id}`。
  - 案件 ACL：重用既有 `upload_owner.py`，auth ON 時 case owner / admin 可存取。
  - 適用場景（規劃支援，目前 L1+ 文字層 L2 範圍）：投標、KYC、HR 到職、報帳、訴狀附件、申請件、工程交付、保險理賠等。
  - **跨檔身分一致性（Sprint 2）** — 抽公司 / 政府機關 / 學校 / 法人 / 統編 / 案號 / 日期等實體做跨檔比對：
    - 模式 1（user 填 ground truth）：抓所有「跟主角不符 + 不是對方」的實體出現，標出「疑似漏改範本」
    - 模式 2（沒 ground truth）：頻率分析，outlier（出現檔案數 ≤ 主流的一半）標警
    - 統編 8 碼校驗碼錯誤直接 fail
    - 政府機關預設視為「對方候選」不誤標

- **Sprint 3**：L2 真 OCR pipeline — 對掃描 PDF / JPG / PNG / TIFF 跑 tesseract（chi_tra+chi_sim+eng），抽出來的文字再送 entity extraction 補抓章 / 影像證書內字。PDF 有文字層自動跳過（避免重複）。每檔上限 30 頁防大檔卡住。
- **Sprint 4**：L3 LLM 文字層 — `submission-check` 列入 `KNOWN_LLM_TOOLS`：(a) **fuzzy 變體合併**（公司簡稱 ↔ 全名 ↔ 英文名自動聚成一群避免誤標 mismatch），(b) **修改範本痕跡推論**（讀前 500 字判斷哪些檔疑似從別案範本沿用未改）。沒設定 LLM 自動 fallback 不影響整體運作。
- **Sprint 5**：User override 註解 — 對任何 finding 可標「誤報」/「已確認 OK」/「不修」+ 原因 + user。重跑時自動套用 override，被標的 finding 降級為 info。`POST /override/{case_id}` + `DELETE /override/{case_id}/{finding_key}` endpoints。
- **Sprint 6 部分**：E 類重要檢查項：
  - **金額一致性**（標單 / 報價 / 估價 / 合約位數錯位偵測，例：3,500,000 vs 35,000,000 標 warn）
  - **日期合理性鏈**（任何證書 / 證明日期早於案件截止日 → 過期警示）
  - **附件清單對應**（標單聲明附件 N 件，實際上傳 M 份不符 → 警示）

- **Sprint 7**：跟既有工具 chaining — 每個 finding 標註對應修正工具（metadata 殘留 → metadata-clean、JS / 嵌入物 → pdf-hidden-scan、漏改範本 / 統編錯 → pdf-editor、章缺漏 → pdf-stamp 等）。報告每項顯示「→ tool」按鈕直接跳對應工具。
- **Sprint 7**：案件清單搜尋 — 支援按主角名 / 統編 / case_id 模糊搜 + status 篩選。
- **Sprint 8**：Admin 儀表板 `/tools/submission-check/admin-stats` — 跨案件 stats（30 / 90 / 365 天區間）：案件總數、檔案數、平均就緒度、狀態分布、三層使用率、Top 10 失敗類型、嚴重度分布。

### 後續未做

- L3 vision 偽造偵測（章 / 印 / PS 痕跡，需 vision LLM）
- 完整鑑識報告 PDF 輸出格式
- 實體字典 R 階段（11 天獨立子系統）
- 案件批次上傳 / 匯出 (v2 backlog)

## [1.5.18] - 2026-05-10

### 變更

- **網站 / 說明 / 程式註解移除 `gemma3:27b` 引用** — `gemma4:26b` 視覺 + 文字皆可，足夠涵蓋既有部署場景；`LLM.md` 部署選項表、`docs/index.html` LLM 部署選項段、`translate_doc/router.py` 與 `llm_settings.py` 註解內的範例都改成 `gemma4:26b`。

### 修正

- **多個 `.md` 文件半形標點批次清理** — `CLAUDE.md` / `TEST_PLAN.md` / `github/CHANGELOG.md` / `github/README.md` / `github/TEST_PLAN.md` / `github/SECURITY.md` 共 6 檔 ~53 行內中文旁的 `,` `:` `;` 改全形「，：；」（規則：括號可半形、逗號句號分號冒號必為全形）。

## [1.5.17] - 2026-05-10

### 修正

- **字型管理頁無法上傳 / 操作（issue #15）** — `app/admin/templates/fonts.html` 第 180 行 v1.5.4 加 XSS 防護時寫的範例註解 `// (惡意檔名 \`<script>alert(1)</script>.ttf\` 之前會被當 HTML 渲染)` 內含字面 `</script>`，瀏覽器 HTML parser 不在乎是不是在 JS 註解內，看到就提前關 script 標籤 → 後面 109 行 JS 全變純文字，上傳 / 隱藏切換 / 刪除自訂字型按鈕全失效。改為純中文敘述，移除字面 `</script>`。
- 全面掃描所有 template 內 script 區塊，確認 fonts.html 是唯一一處此類雷。

## [1.5.16] - 2026-05-10

### 變更

- **LLM 文件 / UI / 程式碼移除 qwen3-vl 引用，預設 vision 模型統一為 `gemma4:26b`** —
  - `LLM.md`、`docs/index.html` 部署選項、admin LLM 設定頁建議區、pdf-fill JSON 解析錯誤 hint 都不再提 qwen3-vl
  - 程式註解（`llm_settings.py` / `llm_client.py` / `llm_review.py` / `llm_review_per_field.py`）移除 qwen3-vl 字樣，改用通用「vision model」描述
  - `get_review_prompt()` 內仍保留 qwen 分派分支供既有 qwen 使用者向下相容，僅移除推薦
- **中文文案半形標點全面清理** — LLM.md / docs/index.html `#llm` 區 / translate-doc `domain_hint` prompt 內所有中文旁的 `,` `:` `;` 改全形「，：；」（規則：括號可半形、逗號句號分號冒號必為全形）

## [1.5.15] - 2026-05-10

### 新增

- **逐句翻譯加「文件領域」hint** — UI 在原文 / 目標語言下方多一個輸入框「例：法律合約 / 醫療報告 / 軟體技術文件 / 財務報表 / 學術論文 / 新聞稿…」
  - 選填，留空跟以前一樣
  - 填了會進 LLM prompt：「**文件領域**：〈user 輸入〉。請依此領域的慣用術語、文體與專業用詞翻譯，縮寫保持原貌（SLA / API / GDPR），技術名詞不要過度本地化。」
  - 同時通過 `/translate-batch` / `/translate-one` / `/api/translate-doc` 三個 endpoint
  - 截斷 80 chars + strip CR/LF 防 prompt injection

### 修正（文案準確性）

- **LLM 工具描述全面 audit** — 移除誇大 / 不存在的功能宣稱：
  - 「批次填 50 份廠商表」→ pdf-fill 實際是單份單次，改為「30 欄的廠商表免逐欄校對」
  - 「法務 / 合規清舊合約批次」→ doc-deident 是單份，改為「regex 抓不到的 context-sensitive 欄位 LLM 補一網」
  - 「保留 Word/ODT 排版重新輸出」→ translate-doc 實際輸出 web 並排對照表，移除錯誤宣稱
  - 「表格內容被 PyMuPDF 拆得亂七八糟時 → LLM 重組回 markdown 表格」→ pdf-extract-text 沒此功能，移除
  - 「自動分『重大 / 一般 / 提問』三類」→ pdf-annotations 實際是 LLM 動態分群，改為「依註解內容主題自動分群」
  - 所有「省時感：X 分鐘 → Y 分鐘」具體數字移除，改為描述性效果
- 同步更新 `LLM.md` 與 `docs/index.html` 8 張 LLM 卡片描述

## [1.5.14] - 2026-05-10

### 修正

- **「效果」框內「效」「果」字被拆成兩行**(v1.5.13 慘案)— `.llm-effect` 加 `display: flex; align-items: center` 後，`<b>效果</b>` 變成獨立 flex item 被 shrink 到「效 \\n 果」垂直排列。拿掉 flex 改純 padding + min-height。
- **TEXT / VISION 標籤位置** — 從右上角絕對定位改回跟 icon 同一橫排 flex，中軸對齊，固定 height 22px line-height 1 避免 baseline 飄移。

## [1.5.13] - 2026-05-10

### 介紹網站微調

- **AI 加值卡片視覺對齊** — 描述段(`.llm-card p`)加 `min-height: 4.95em`(~3 行)、效果框(`.llm-effect`)加 `min-height: 3.2em`(~2 行)+ flex center，所有卡片標題、描述、效果框 y 軸位置統一，不再因內容長短不一而高低錯落
- `.llm-grid` 加 `align-items: stretch` 強制同列等高

## [1.5.12] - 2026-05-10

### 介紹網站微調

- **AI 加值區段位置調整** — 從 `#features` 後移到 `#screenshots` 後（畫面 → AI 加值 → 企業管理 順序更合理）
- **TEXT / VISION 標籤改右上角絕對定位** — 之前跟 icon 並排高度差怪怪，改 absolute top-right 視覺乾淨
- icon 從 36px → 40px 提升存在感

## [1.5.11] - 2026-05-10

### 介紹網站微調

- **LLM 區「效果」綠底框** 對比加強：背景 #ecfdf5 / border #059669 / 文字 #064e3b（深綠 on 淺綠），原本 #15803d 在淺綠背景對比不足
- **每張 LLM 卡片加 icon** — 8 個 Lucide-style SVG icon：翻譯、文字段、表單校驗、文件 deident、文字 deident、bar-chart、訊息泡泡、git-compare
- text 卡片配 #eef2ff/#4338ca 藍紫色，vision 卡片配 #fdf2f8/#be185d 粉紅色

## [1.5.10] - 2026-05-10

### 文件 / 介紹網站

- **新增 `LLM.md`** — 列出目前支援 LLM AI 加值的 8 個工具：
  - 逐句翻譯、擷取文字段落重排、表單自動填寫(視覺校驗)、文件 / 文字去識別化補偵測、字數統計摘要、註解整理自動分組、文件差異比對變動摘要
  - 每個工具寫清楚 LLM 做什麼、可省多少時間、是否需要 vision 模型
  - 部署選項(Ollama / vLLM / DGX Spark)+ 安全 / 隱私說明
- **README** 在 30 工具速覽下方加 LLM AI 加值小節 + link 到 LLM.md
- **介紹網站(docs/index.html)** 加新 section `#llm`「8 個工具支援 LLM AI」
  - 8 張卡片(text / vision tag 區別)
  - 每張卡片有「效果」綠底框寫實際省時感
  - 部署選項框
  - 頂端 nav 加「AI 加值」連結
- 對應 CSS 加在 `docs/style.css` 末尾

## [1.5.9] - 2026-05-09

### 文件

- **README 大瘦身** 470 行 → 144 行 — 留 entry-level 內容(intro / 一行安裝 / 30 工具速覽 / 文件導覽 / 隱私要點)。詳細內容拆到專題文件：
  - **`INSTALL.md`** — 三平台詳細安裝、必要工具、安裝位置、系統需求、解除安裝
  - **`OPS.md`** — `jtdt` 指令、升級、反向代理(nginx/Caddy)、監聽位置、備份還原、排程清理
  - **`AUTH.md`** — 認證 / RBAC / 內建帳號 / 2FA / 帳號鎖定 / 緊急復原(從 README 完整搬出)
  - 既有的 `API.md` / `SECURITY.md` / `CHANGELOG.md` / `TEST_PLAN.md` 不變
- 第一次進來的人不再被淹沒，直接看 README 知道要點再點對應文件深入。

## [1.5.8] - 2026-05-09

### 資安

- **CodeQL alerts 47 → 3 unique（本地實測）**:
  - 把 `_safe_next` 跟 `_validate_llm_base_url` 從 auth_routes.py / llm_client.py 內 private function 搬到 `app/core/url_safety.py` 模組。同檔 private function CodeQL API graph 不認得，獨立模組才能透過絕對 import 走 barrierModel。
  - 4 個 `str(exc)` user-facing 錯誤改成 fixed-string mapping（admin_router LLM endpoints、branding_upload、branding_site_name、import_settings、change_password）— 真 stack trace 寫 server log,user 看見的是控制過的訊息。
  - `pdf_editor/router.py:994` 的 `is_cjk` 包 `bool(...)` 顯式 cast，讓 CodeQL 知道是 bool 不是 user data。
  - `modal.js` `if (html)` opt-in 分支用 `DOMParser.parseFromString` 取代 `innerHTML` 直接賦值。
  - `sys_deps_api` 加 try/except 防 stack-trace 漏。
- **MaD pack 更新對應 sanitizer 路徑** — `Member[core].Member[url_safety].Method[validate_llm_base_url]` / `Method[safe_next]` 取代舊的同檔路徑。
- **新加 `app/core/log_safe.safe_user_error()`** — helper 把 controlled exception 訊息映射為 user-safe message，內部給其他開發者用。

### 重構

- **15 個 router 改絕對 import**（v1.5.6 開始，v1.5.8 完整對齊): `from app.core.X import ...` 取代 `from ...core.X import ...`，讓 CodeQL API graph 認得 sanitizer 呼叫站點。
- 全 pytest 470/470 通過。

## [1.5.7] - 2026-05-09

### 修正

- **CodeQL workflow 失敗 (configuration error)** — v1.5.6 在 `codeql-config.yml` 用 `packs:` 引用 MaD pack `jasoncheng7115/jt-doc-tools-extensions`，但 codeql-action init 把 `packs:` 當 query pack 處理 → 嘗試從 GHCR 下載 → 找不到 → init 步驟 fail → 整個 CodeQL run 變紅。
  - 修法：① 拿掉 `codeql-config.yml` 內的 `packs:` 引用，② 把 MaD pack 從 `.github/codeql/extensions/qlpack.yml` 搬到 `.github/codeql/extensions/jt-sanitizers/qlpack.yml`(GitHub Actions auto-discover convention)。

## [1.5.6] - 2026-05-09

### 資安

- **CodeQL Models-as-Data extension** — 教 CodeQL 認得 jt-doc-tools 自訂 sanitizer:
  - `app.core.safe_paths.{safe_join, sanitize_filename, require_uuid_hex, is_safe_name}` 標為 `path-injection` barrier
  - `app.core.llm_client._validate_llm_base_url` 標為 `request-forgery` barrier
  - `app.web.auth_routes._safe_next` 標為 `url-redirection` barrier
  - `app.core.log_safe.safe_log` 標為 `log-injection` barrier
  - 檔案位置：`.github/codeql/extensions/jt-sanitizers.model.yml`（pack name `jasoncheng7115/jt-doc-tools-extensions`）
  - 用 `codeql` CLI 本地驗證 syntax + run 確認 barrier 真有減 alert
- **15 個 router 改用絕對 import** — `from ...core.safe_paths import X` → `from app.core.safe_paths import X`，讓 CodeQL 的 API graph 正確識別 sanitizer 呼叫站點：
  - pdf_stamp / pdf_watermark / pdf_extract_text / pdf_attachments / pdf_nup / pdf_metadata / pdf_fill / pdf_pages / pdf_editor / pdf_hidden_scan / pdf_rotate / pdf_to_image / pdf_pageno / pdf_extract_images / doc_deident
  - 沒這個改動，CodeQL 看不見相對 import 的呼叫，MaD extension 對它們無效
- **CodeQL alerts 預期下降**: 47 → ~32（path-injection 17 → ~7）

## [1.5.5] - 2026-05-09

### 改善

- **pdf-editor 加 4 個翻頁按鈕** — 在頁碼指示器旁加「第一頁 / 上一頁 / 下一頁 / 最後一頁」工具列按鈕，符合一般 PDF reader 的操作慣例。每個按鈕：
  - 帶 SVG 圖示（雙箭頭 / 單箭頭）
  - 在邊界（已在第 1 頁 / 最後一頁）時自動 disable
  - 鍵盤捷徑：Home / End / PageUp / PageDown(在 input/textarea 內不會攔截)
- **修 CodeQL `js/xss-through-dom` #52**：`drag_position_editor.js:45` 拿掉「safeImgSrc 不存在時 fallback 用未檢查 url」分支(safe_url.js 在 base.html 一律載入，不需 fallback)

## [1.5.4] - 2026-05-09

### 資安（重大，GitHub native scan 結果一波清理）

- **Dependabot 22 alerts 全清** — bump 5 個套件到底線：
  - `Pillow>=12.2.0,<13`：6 個 high CVE（FITS GZIP / PSD OOB / PSD Tile Integer / PDF Trailer InfLoop / Font Integer / Nested coords heap）
  - `Jinja2>=3.1.6,<4`：3 個 sandbox breakout
  - `starlette>=0.49.3,<0.53`：Range header O(n²) DoS in FileResponse
  - `fastapi>=0.124.0,<0.140`：跟著 starlette 升（fastapi 0.119 不支援 starlette 0.49+）
- **CodeQL High `js/xss-through-dom` 12 個** 全修：
  - `static/js/modal.js`：refactor 從 `card.innerHTML = template` 改純 createElement
  - `static/js/drag_position_editor.js`：`.src = url` 改透過新加的 `window.safeImgSrc()` 驗 URL
  - `pdf_watermark.html` / `pdf_stamp.html`：3 個 `editor.$assetImg.src = url` 走 safeImgSrc
  - `pdf_rotate.html` / `doc_deident.html` / `pdf_annotations*.html`（5 個 lightbox）：`lb.innerHTML = '<img>'` 改 createElement
  - `admin/templates/fonts.html`：drop-zone label `text.innerHTML = '<b>已選擇</b>' + filename + ...` 改 createElement（filename 是 user-controlled 真 XSS bug）
  - 新加 `static/js/safe_url.js` — `safeImgSrc()` allowlist 只允許 `/` `./` `blob:` `data:image/` `http(s)://`
- **CodeQL High `py/polynomial-redos` #13** — `RE_AD_DN` regex 加長度上限 + 去掉重複的 UID alternative + 收進 char class,5 個 ReDoS regression test 通過
- **CodeQL Medium `py/stack-trace-exposure` 7 個** — `app/main.py:873` 真漏（改 logger.exception + generic 訊息）；`doc_deident` / `text_deident` 兩個 LLM augment fail 同樣處理；`auth_routes.py` 改密碼錯誤訊息加 codeql FP 標記（user-facing 訊息必要）；`admin/router.py` LLM endpoint 兩個 ValueError FP 同樣標記
- **CodeQL Medium `py/url-redirection` 2 個** — `_safe_next()` 加嚴：urllib.parse 驗 scheme + netloc、reject CRLF / NUL / 反斜線、reject 非 string、27 個 regression test 通過
- **CodeQL Medium `py/log-injection` 9 個** — 新加 `app/core/log_safe.py:safe_log()` helper（strip CR/LF/NUL,bound 200 char）；在 `pdf_editor` / `pdf_extract_text` / `auth_settings` / `auth_ldap` 9 個 logger 呼叫站點 wrap user-supplied 變數

### 工具流程

- **新加 `tools/check_version_consistency.py`** — 驗 `app/main.py:VERSION` / `pyproject.toml` / `uv.lock` / `github/README.md` 標題 / `github/CHANGELOG.md` 最新一筆 5 處版號完全一致；不一致直接 exit 1 印 diff 表
- **`tests/test_version_consistency.py`** — pytest case 包裝上面，wired 進 `TEST_PLAN.md §5` 發版前必跑
- **`tests/test_redos_ad_dn.py`** + **`tests/test_open_redirect.py`** + **`tests/test_llm_url_ssrf.py`** + **`tests/test_path_traversal_audit.py`** 都列入「每次發版必跑」
- 全 pytest 從 437 → 470（新加 33 個 OWASP regression）

## [1.5.3] - 2026-05-09

### 資安（重大）

- **GitHub Dependabot 41 alerts 全清** — 5 個 unique CVE：
  - `python-multipart>=0.0.18`：修任意檔案寫入 + 畸形 boundary DoS
  - `Pillow>=11.3.0,<12`：修 FITS GZIP decompression bomb + PSD OOB write
  - `starlette>=0.47.2,<0.50`：修 multipart/form-data DoS（GHSA-2c2j-9gv5-cj73）
  - `fastapi>=0.115.0,<0.120`、`uvicorn>=0.32.0,<0.40`、`jinja2>=3.1.4,<4` 一併放寬上限
- **GitHub CodeQL Critical：Partial SSRF** in `app/core/llm_client.py:_validate_llm_base_url`
  - 新增 URL allowlist：只允許 http / https scheme，黑名單雲端 metadata host（AWS/GCP/Azure/Alibaba/OCI 169.254.169.254、metadata.google.internal 等）
  - 私網 IP（10/8、172.16/12、192.168/16、127/8）仍允許 — 本機 / LAN Ollama 是常見部署
  - admin 設定保存與測試連線兩個入口都驗證
  - 補 27 個 regression test (`tests/test_llm_url_ssrf.py`)
- **GitHub CodeQL High：Path Injection 12 個 endpoint 補 ACL**
  - `pdf_pageno/thumb` + `preview-thumb`、`pdf_pages/thumb` + `submit-from-upload`、`pdf_rotate/thumb` + `finalize` + `finalize-png`、`pdf_extract_images/page-thumb` + `file/{batch_id}/{name}`、`pdf_attachments/stripped`、`image_to_pdf/thumb` + `full` + `delete`
  - 全部加上 `safe_paths.require_uuid_hex(upload_id)` + `upload_owner.require(upload_id, request)` 雙層檢查
  - 客戶啟用認證後不再有「拿到別人 upload_id 就能下載 / 看預覽 / 刪檔」的跨 user 漏洞
- **新增 `tests/test_path_traversal_audit.py`** — AST 結構審計：每個 router endpoint 帶 user-input 參數且做檔案存取的，必須能追蹤到 `safe_join` / `sanitize_filename` / `require_uuid_hex` / `upload_owner.require` 之一；missing 即測試 fail，避免未來 endpoint 又漏 ACL
- **`SECURITY.md` OWASP Top 10 (2025) 改為 H3 區塊 + bullet 列表** — 之前的單欄表格被 inline code 撐到斷行不好讀；現在 10 個分類各自獨立段落，「我們的對策」改稱「防護機制」（更正式）
- **CodeQL 排程改為每週一台北時間 09:00**（cron `0 1 * * 1` UTC = 09:00 Asia/Taipei）— 與 dependabot 一致；之前 codeql.yml 是「每週日 UTC 19:00」、dependabot 是「每週一台北 09:00」格式不統一
- **`.github/codeql/codeql-config.yml`** 新增 — 之後可加 sanitizer model pack 進一步減少 CodeQL false positive

### 改善

- **pdf-editor 頂端工具列** — 「已自動儲存」狀態文字之前在工具列下方獨立一行（flex 100%），現在改為 toolbar 末端的固定 chip：
  - 預設 #f9fafb 灰底框，存檔成功變淺綠（`is-saved`）、錯誤變淺紅（`is-error`）
  - 空狀態自動顯示「就緒」placeholder，不會空一塊
- **pdf-editor 加頁碼指示器 + 跳頁輸入** — toolbar 「符合視窗」按鈕後新增 `頁碼 [N] / [總頁數]`：
  - 滾動時自動偵測當前可視頁面（IntersectionObserver、root=canvasWrap），更新輸入框
  - 輸入頁碼按 Enter 直接 `scrollIntoView` 跳到目標頁
  - 與既有縮放、自動預覽、復原 / 重做按鈕並列，方便長 PDF 翻頁

## [1.5.2] - 2026-05-09

### 修正

- **pdf-editor「選既有物件」對某些 PDF 顯示一排亂碼 `eeoeeoeeo...`**（客戶 Action1_GettingStarted.pdf p.2 目錄頁）
  - 根因 1：`_looks_garbled()` Signal d) 把合法的 leader dots `..........` 當成週期重複 → 觸發 OCR fallback
  - 根因 2：OCR (tesseract) 把成排小點認成 `eeeeeee...` 回傳前端
  - 根因 3：原邏輯只要字型沒 `/ToUnicode` CMap 就視為 unreliable，但 PyMuPDF 從字型名稱（MS JhengHei 等）已能猜對 Unicode，那條檢查多餘
  - 修法：①Signal d) cycle 必須含字母/數字才 flag（純標點 cycle 是合法排版元素）②拿掉 `_font_has_tounicode` 檢查，只信 `_looks_garbled` 的文字內容判斷 → 信任 PyMuPDF 抽出來的乾淨原文，不再對 leader dots 觸發 OCR

### 改善

- **pdf-editor 工具列每個按鈕對應游標形狀** — V 選取（箭頭）、S 選既有（食指）、T 文字（I 字）、W/R/O/L/A/P 形狀類（十字）、I/N 圖片+便箋（複製游標 帶 + 號）、H 螢光筆（I 字），點選工具後游標立刻反映即將進行的操作
- 新增 32 個 pytest case `tests/test_looks_garbled.py`：4 種 garbled signals 完整 coverage + leader dots / 分隔線 / `***` 等合法 punctuation cycle 不誤判

## [1.5.1] - 2026-05-09

### 修正

- **Windows OxOffice / LibreOffice 轉檔全部卡 60s timeout**（[GitHub issue #5](https://github.com/jasoncheng7115/jt-doc-tools/issues/5)，客戶 Win11 / Server 2025 都踩到）
  - 根因 1：`-env:UserInstallation` 的 `file://` URL 用字串 concat，Windows path 帶 backslash 會變成 `file://C:\Users\...\profile`（無效 URI）→ soffice fallback 到 LocalSystem 預設 profile → 在 service Session 0 卡住
  - 根因 2：Windows Service (Session 0) 跑 soffice 沒給 `CREATE_NO_WINDOW` + `DETACHED_PROCESS` flag，soffice 嘗試 attach console 卡住
  - 根因 3：LocalSystem service env 缺乾淨的 `TEMP` / `TMP` 變數
  - 修法：新增 `_profile_uri()` helper 用 `Path.as_uri()` 產正確 URI（`file:///C:/Users/...`）；新增 `_build_soffice_cmd()` helper 統一 cross-platform args + Windows creationflags + 乾淨 env；`convert_to_pdf()` 與 `convert_to_text()` 兩個 entry point 都改用新 helper

### 新增 — 資安強化

- **CSP (Content-Security-Policy) header**：之前刻意沒設，現補上（`default-src 'self'` + `connect-src 'self'` 阻 SSRF-via-browser + `object-src 'none'` + `frame-ancestors 'self'` + `base-uri 'self'` + `form-action 'self'`）
- **OWASP Top 10 (2025) 完整對照** + `tests/test_owasp_top10.py` 15 個 regression case（每次發版必跑）
- **GitHub 平台層自動掃描**：
  - `.github/dependabot.yml`：每週掃 Python deps + GitHub Actions 已知 CVE
  - `.github/workflows/codeql.yml`：每次 push + PR + 週日跑 CodeQL SAST（Python + JavaScript，security-extended 規則組）
  - 配合 repo Settings → Code security 開啟 secret scanning + push protection + private vulnerability reporting
- 新 `SECURITY.md` 文件：說明資安政策、OWASP Top 10 (2025) 對照、漏洞回報管道

### 文件

- README 加目錄（內容已成長，導覽方便）
- README + docs/index.html 完整解釋稽核員角色 + jtdt-admin / jtdt-auditor 內建帳號用法與分別

## [1.5.0] - 2026-05-07

### 重要行為變更 — admin 看不到 user 隱私資料的 4 頁（職責分離強化）

升級到 v1.5.0 後，**admin 看不到**：

- 上傳檔案記錄 (`/admin/uploads`)
- 表單填寫歷史 (`/admin/history/fill`)
- 用印簽名歷史 (`/admin/history/stamp`)
- 浮水印歷史 (`/admin/history/watermark`)

這 4 頁含 user 真實上傳內容 / 填寫資料，**只有稽核員可看**。設計理由：合規上 admin 雖管系統，但不該偷看 user 的真實檔案。原本 admin 慣用「上傳檔案記錄」查 user 行為的 → 改用 `/admin/audit`（稽核紀錄）查事件流。

**admin 仍看得到**：稽核紀錄 (`/admin/audit`) + 系統狀態 (`/admin/system-status`) + 其他所有設定區，以及自己的設定 / 工具 / 服務管理。

**升級指引**：
- 升級後 admin sidebar **自動隱藏**這 4 頁（`_nav_settings_visible` filter 自動處理，不會有「按進去 403」的死連結）
- 想看那 4 頁需要用稽核員帳號（`jtdt-auditor` 或 `jtdt audit-user create <name>` 自建）
- 不希望這個變更 → admin 可在 admin/permissions 把自己加入 auditor 角色（會自動轉為純稽核員、失去 admin 權限，所以不建議；正解是另開稽核員帳號）

### 新增 — 內建稽核員帳號 `jtdt-auditor` 自動建立

- 啟動時若認證已啟用且本機沒有 `jtdt-auditor` 帳號 → 自動建立內建稽核員帳號
  - **密碼留空**（`password_hash=NULL`），無法直接登入；admin 必需執行
    `sudo jtdt reset-password jtdt-auditor` 設定第一次密碼
  - 強制 2FA（`totp_required=1`），第一次登入導向 `/2fa-verify` 設定 TOTP
  - 自動指派 `auditor` 角色（admin 不慎移除後，下次啟動會自動補回）
  - `is_audit_seed=1` 旗標保護，UI / CLI 拒絕刪除
- 新 schema migration v7：`users.is_audit_seed` 欄位
- admin 仍可用 `sudo jtdt audit-user create <username>` 建額外稽核員
- 升級無痛：既有客戶升上 v1.5.0 → 啟動瞬間補帳號，`audit_seed_create` 寫進 audit log

### 修正 — v1.4.99 上線後實機 e2e 測試暴露的三個 bug

- **`/me/2fa/start`、`/me/2fa/verify`、`/me/2fa/disable` 三個 endpoint 之前會回 500**（`NameError: JSONResponse not defined`）。`auth_routes.py` 的 `JSONResponse` 與 `HTTPException` import 補進 module-level；TOTP 自助管理頁面現在正常運作。
- **`jtdt audit-user create` SQL 錯誤** — `INSERT INTO subject_roles ... assigned_at` 寫到不存在的欄位（schema 只有 `(subject_type, subject_key, role_id)`），改成不帶 `assigned_at` 的版本。
- **`jtdt auth disable` / `jtdt auth set-local` 印出 SyntaxError** — 內嵌 Python 片段的單引號字串裡又有單引號（`'Already 'off'; ...'`），被 Python parser 切成三段。改成不帶內嵌引號的訊息。
- **admin/roles 頁面稽核員角色不應顯示工具勾選方塊** — 稽核員職責就是不能用工具（職責分離），UI 不該讓 admin 誤勾。改與 admin 角色一樣顯示「無工具權限」說明區。後端 `roles.update()` 也擋住對 auditor role 的 tools 寫入。
- v1.4.99 新功能補上完整 pytest 涵蓋（36 個新 test case，含 v1.5.0 內建稽核員）：TOTP 模組、schema migration v6/v7、auditor seed top-up、is_auditor 邏輯（含 group 成員）、require_admin 白名單、2FA 登入流程、`/me/2fa` 自助、auditor 不可自停 2FA、**全新安裝 + 從 v5 schema 升級**兩種情境驗證資料保留、jtdt-auditor 自動建立 + 不可刪 + role 自動補回 + 無密碼時 login 拒絕。

## [1.4.99] - 2026-05-07

### 新增 — 合規 / 稽核員角色（重大）

啟用認證後，原本只有 admin / 一般使用者兩層；admin 看得到所有稽核紀錄與檔案歷史，違反職責分離（separation of duties）原則。本版引入 **稽核員 (`auditor`) 角色**，類似郵件歸檔（mail archive）的合規分離設計：

- **新本機角色 `auditor`** — 自動加入既有客戶 DB 的 `roles` 表（`seed_builtin_roles()` top-up，**升級無痛、admin 完整權限不變**）
- 稽核員**唯讀**存取：`/admin/audit*` `/admin/history/*` `/admin/uploads` `/admin/system-status`
- **不可使用任何工具**、不可改設定、不可建 user / role
- **強制 TOTP 2FA** — 第一次登入自動導 `/2fa-verify` 顯示 QR，掃 + 輸 6 碼才能進去；稽核員自己不能停用、admin 也無權替稽核員角色用戶取消強制
- 稽核員每次 view 寫一筆 `auditor_view` audit event（含 path / method / query），**admin 看得到稽核員看了什麼、稽核員自己無法刪除**（UI 無刪除端點）
- 多個稽核員可並存
- **CLI 新指令**：`sudo jtdt audit-user create <username>` — 一次完成建立本機帳號 + 指派 `auditor` 角色 + 強制 2FA

### 新增 — TOTP 2FA 自助啟用（所有角色）

- 新頁 `/me/2fa`：任何登入 user 可自助啟用 / 重新生 / 停用 TOTP（auditor 角色除外，永遠強制）
- 支援 Google Authenticator / Microsoft Authenticator / Authy / 1Password 任一 TOTP App
- DB schema migration `_m6_totp_columns` ADD `users.totp_secret` / `totp_enabled` / `totp_required`（default 0，**升級不會強迫既有 user 啟用**）
- 登入流程：password OK 後若 `totp_enabled=1` 或 `totp_required=1` 才導去 `/2fa-verify`，其他不變

### 升級指引

- **既有客戶升級無痛** — admin 仍看所有稽核 / 歷史，既有 user 行為不變（沒設 2FA 不會被強制）
- 想啟用合規分離，三步：
  1. `sudo jtdt audit-user create <name>` 建立本機稽核員帳號
  2. 該員首次登入自動掃 QR 設定 2FA
  3. 視需求把 admin 從稽核 / 歷史頁的角色拿掉（手動，看 admin 是否還想要看）— v1.4.99 暫不自動限制 admin
- 新依賴：`pyotp` `qrcode`（自動裝）；schema migration `_m6` 自動套用

### 新增 audit event 類型

- `auditor_view` — 稽核員 view audit/history/uploads/system-status 的紀錄
- `2fa_enabled` / `2fa_disabled` / `2fa_success` / `2fa_fail` / `2fa_setup_fail`

---

## [1.4.98] - 2026-05-07

### 修正

- **圖片轉 PDF：多頁圖（multi-page TIFF / 動畫 GIF / APNG / 動畫 WEBP / HEIC 連拍）只轉出第一頁**：根因 — `Image.open()` 預設只開第一個 frame，後端沒呼叫 `n_frames` + `seek(i)` 拆頁。修法：偵測 `n_frames > 1` 時逐 frame 拆成獨立 file_id，filename 自動加 `(2/3)` 編號方便辨識。前端同步處理 — 上傳結果是 array 時把 placeholder 替換成第一張 + 把剩下的插在後面（單頁回 dict 維持向下相容）

---

## [1.4.97] - 2026-05-07

### 新增

- **首頁也可釘選工具**：螢幕小、sidebar 收起來時也能用同樣的星星按鈕釘選 / 取消（hover 顯示金色星星按鈕）。釘選的工具會在首頁最上方「釘選」區塊鏡像出現。資料同 sidebar 共用 `localStorage['jtdt:pinned']`，sidebar 與首頁的釘選操作互相同步（透過 `jtdt:pins-changed` custom event + `storage` event 跨 tab 同步）

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

驗證在 Win11 x64 一行 install 從 0 到健康檢查全數綠燈：service running、`jtdt.cmd` 建好、`.venv/pyvenv.cfg` 存在、ldap3 2.9.1 可 import、healthz `{"ok":true}`。

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

- **「註解固定化」改名為「註解平面化」**：與 Adobe Acrobat 繁中正式翻譯一致（「平面化圖層 / 平面化透明度」）。`固定化` 太像直譯日文/中文不夠在地。route id `pdf-annotations-flatten` 不變。
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

- **擷取圖片卡片左上勾選 / 右上下載按鈕看不清楚**：之前白底淺紫邊在綠色 picked halo 上太淡，幾乎隱形。改成深底高對比 — 勾選預設白底深灰邊，picked 後變實心綠 + 白勾；下載按鈕改深色 （#0f172a） 背景 + 白色 icon，hover 變紫色。z-index:2 確保穩定在最上層。

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

- **編輯使用者 modal picker 名稱仍被 `…` 截掉**：把 `.picker-name` 從 `nowrap + ellipsis` 改成 `word-break:break-word`，名字長就 wrap 到第二行（每筆稍高一點）但保證看得完整；checkbox 用 `align-items:flex-start` 對齊頂部。modal max-width 從 520px 拉到 600px 給更多橫向空間。

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
- 測試從 collision-fails 改為 coexist-succeeds，4 個 case 全數綠燈。

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
  - 公司：`RE_COMPANY` 加入英文後綴匹配 `Co., Ltd. / Co.,Ltd. / Inc. / LLC / Corp(oration). / Limited / Company`，能抓到「Merida Industry Co。， Ltd。」「Apple Inc。」「Acme Corporation」等
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

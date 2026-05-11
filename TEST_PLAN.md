# jt-doc-tools 測試計畫

每次發版前都跑 `pytest`。覆蓋以下面向：

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

## 2. 手動驗收清單（每個版本）

### 2.1 填單用印

#### PDF 表單填寫 (pdf-fill)
- [ ] 上傳廠商 PDF（華儲 / Macpower / momo / Tigerair）
- [ ] 自動偵測欄位且公司資料正確帶入
- [ ] 切換第二公司不會 500
- [ ] 拖曳藍框微調位置 → 套用新位置
- [ ] 編輯模式 ↔ 合成模式切換
- [ ] 下載 PDF / 下載 PNG 都可用
- [ ] Office 來源（docx/xlsx/odt）自動先轉 PDF 再偵測

#### PDF 用印與簽名 (pdf-stamp)
- [ ] 同時看得到 印章/簽名/Logo 三類資產
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

#### 分拆 (pdf-split)
- [ ] 每頁一份 / 範圍模式都可用

#### 轉向 (pdf-rotate) 🆕 加入鏡射
- [ ] 整份 90/180/270 旋轉
- [ ] 指定頁面旋轉
- [ ] **水平鏡射**（flip-h）內容左右翻轉
- [ ] **垂直鏡射**（flip-v）內容上下翻轉
- [ ] 向量品質保留（非 raster 重繪）

#### 頁面整理 (pdf-pages)
- [ ] 刪除指定頁面
- [ ] 重新排序頁面

#### 插入頁碼 (pdf-pageno) 🆕 視覺選位
- [ ] **2×3 位置選擇格**點擊直接換位置
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

#### PDF 附件萃取 (pdf-attachments) 🆕
- [ ] 列出 EmbeddedFiles 清單（含檔名 / 大小）
- [ ] 單檔下載 / 全部打包 ZIP
- [ ] 沒附件時顯示空狀態

#### OCR 文字辨識 (pdf-ocr) 🆕 v1.7.x 雙引擎
- [ ] **EasyOCR 引擎**（預設）：上傳掃描 PDF 或圖片（PNG/JPG）→ 跑 OCR → 回傳含文字層的 PDF；首次跑某語言會下 ~150MB 模型
- [ ] **Tesseract 引擎**：admin/ocr-langs 切換 → 重跑同一檔案，文字層可選取
- [ ] **自動 fallback**：選 easyocr 但實際不可用時自動 fallback tesseract（log `EasyOCR not available...`）
- [ ] **語言群組摺起 / 展開**：CJK 預設展開；西方語言、東南亞 / 其他 預設摺起；若有預設勾選的語言（eng）所在群組會自動展開
- [ ] **語言碼對應**：easyocr 模式下繁中顯示 `ch_tra`、英文顯示 `en`；tesseract 模式下顯示 `chi_tra`、`eng`
- [ ] **統計列位置**：「已選 N 種、本頁列出 18 種」與「中日韓 CJK」標題同列
- [ ] **highlight 寬度**：選取生成的文字層用 macOS 預覽程式 / Adobe Reader 開啟，選取框與可視文字寬度對齊（per-char 平均分散）
- [ ] **圖片 input**：直接上傳 .png / .jpg → 包成單頁 PDF + 文字層
- [ ] **LLM 校正** 預設關閉；開啟後僅在 word count 完全相同時套用
- [ ] **API**：`POST /api/pdf-ocr` 帶 bearer token → 回 job_id → 拉結果

#### 送件前檢核 (submission-check) 🆕
- [ ] 上傳 PDF（或 Office 自動轉 PDF）→ 跑全套檢查項目
- [ ] L1 結構：頁面尺寸 / 字型嵌入 / PDF 版本 / Linearized
- [ ] L2 OCR-based：含掃描頁時自動跑 OCR 並抓文字檢查（走 admin 設定的預設 OCR 引擎）
- [ ] L3 敏感資料殘留：身分證 / 銀行帳號 / 統編 等
- [ ] L4 隱藏內容：JavaScript / 外部連結 / 隱藏文字
- [ ] 報告輸出：含 PASS / WARN / FAIL 三級分類，可下載 Markdown / JSON

### 2.4 格式轉換

#### 文書轉 PDF (office-to-pdf)
- [ ] .docx / .xlsx / .pptx / .odt 各轉一份
- [ ] OxOffice 優先（`find_soffice` 命中 OxOffice）

#### 文書轉圖片 (pdf-to-image) 🆕 擴充 Office
- [ ] PDF 每頁 → PNG
- [ ] **Office 檔案（docx/xlsx/pptx/odt）先自動轉 PDF 再轉圖**
- [ ] 單頁直接下 PNG、多頁自動 ZIP

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

#### OCR 引擎管理 🆕 v1.7.x
- [ ] `/admin/ocr-langs` 兩區：上方 EasyOCR、下方 Tesseract，預設展開符合當前選擇引擎
- [ ] 兩區標題列僅一個三角形（瀏覽器原生 marker，無自繪 ▶ 重疊）
- [ ] 引擎切換：選圓圈內顯示 ✓；切換後 `data/ocr_settings.json` 寫入；pdf-editor / submission-check 同步
- [ ] EasyOCR 內建 80+ 語言不需安裝；Tesseract 18 種訓練檔可一鍵安裝 / 移除
- [ ] tessdata fast / best 雙變體切換

### 2.7 介面

- [ ] 側欄品牌顯示 logo（深底）
- [ ] 首頁 hero 顯示淺底 logo + 三個特色 pill
- [ ] favicon 顯示
- [ ] 工具卡片依分類分組
- [ ] **每個工具有獨一無二的 icon 與顏色**（首頁與側欄一致）
- [ ] **側欄 active tile 白底延伸到右邊內容區**（無紫色縫隙）
- [ ] **側欄捲軸浮動**（只在 hover / 滾動時顯示）
- [ ] **搜尋支援中英文**（輸入 `form` 或 `填寫` 都能找到 pdf-fill）
- [ ] 視窗縮窄到 ≤ 900px：側欄收起、漢堡按鈕展開、項目正確點擊

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

## 4. API 覆蓋檢查 🆕

每個工具都必須有可呼叫的 API endpoint（非只網頁 form）：

- [ ] pdf-merge `/api/pdf-merge` 或對等
- [ ] pdf-split / rotate / pages / pageno / compress
- [ ] pdf-extract-text / extract-images / attachments
- [ ] pdf-encrypt / decrypt / metadata / hidden-scan / diff
- [ ] doc-deident `/detect` + `/process`
- [ ] pdf-editor `/load` + `/save`
- [ ] pdf-stamp / watermark / fill

## 5. 發版前最終檢查

1. `git status` 沒有未追蹤的暫存檔
2. `pytest` 全數綠燈
3. **OWASP regression 全數綠燈**（v1.5.3 起列為發版必跑）：
   ```bash
   uv run pytest -v \
       tests/test_owasp_top10.py \
       tests/test_llm_url_ssrf.py \
       tests/test_path_traversal_audit.py
   ```
4. 重啟 server，所有路由 200（以 curl 跑 1.1 列表）
5. 手動跑一輪 2.x 清單
6. 跑完 §6 「歷史回歸案例」清單
7. 更新 `app/main.py` `VERSION` + `pyproject.toml` `version` + `github/CHANGELOG.md` 加一筆 + `github/README.md` 標題版號
8. 重啟，確認 footer 顯示新版本號
9. 確認停用的工具（`aes-zip`）仍保留程式碼但未顯示於側欄／首頁
10. **推 GitHub 後 5–15 分鐘**檢查 GitHub native scan：
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

### 6.14 v1.7.x — 雙 OCR 引擎（每次發版必過）

#### 6.14.1 EasyOCR 安裝 / 引擎切換

- [ ] Fresh install：install.sh / install.ps1 跑完 `python -c "import easyocr"` 不報錯
- [ ] PyTorch ~700MB 入到 .venv（`du -sh .venv | grep torch`）
- [ ] Windows：安裝過程有跑 `Ensure-VCRedist`（vc_redist.x64.exe），torch 不報 WinError 1114
- [ ] `jtdt update` 跑完同樣 import 檢查通過
- [ ] /admin/ocr-langs 頁載入 < 2 秒（用 `importlib.util.find_spec` 不真正 import easyocr）
- [ ] 切換引擎 → `data/ocr_settings.json` 寫入 `{"engine": "easyocr" | "tesseract"}`
- [ ] 重啟服務後維持選擇

#### 6.14.2 跨工具一致性

- [ ] `pdf-ocr` 跟 admin 預設引擎走
- [ ] `pdf-editor` `_ocr_bbox()` 走 `ocr_engine.recognize_text()`
- [ ] `submission-check` `_ocr_image_bytes()` 走 `ocr_engine.recognize_text()`
- [ ] 切引擎後重跑這 3 工具的 OCR → 結果引擎 = 新選擇

#### 6.14.3 highlight 寬度（v1.7.2）

- [ ] OCR 完的 PDF 在 macOS 預覽程式 / Adobe Reader 開啟
- [ ] 用滑鼠拖選一段中文 → 選取框寬度與可視文字寬度對齊（不會短一截）
- [ ] 跨多行也能正確選到（不會跑到別行）

#### 6.14.4 語言群組摺起（v1.7.3-7.4）

- [ ] CJK 群組預設展開
- [ ] 西方語言、東南亞 / 其他 預設摺起
- [ ] 但若預設勾選的 `eng` 在西方語言群組 → 該群組 `<details open>` 自動展開
- [ ] 統計列「已選 N 種」與「中日韓 CJK」標題同列（v1.7.5）

#### 6.14.5 chi_tra 訓練檔（fast / best）

- [ ] Fresh install 後 `<install>/tessdata/` 含 `chi_tra.fast.traineddata` + `chi_tra.best.traineddata` + `chi_tra.traineddata`（active link）
- [ ] admin/ocr-langs 切 fast / best → active 檔換符號連結 / rename
- [ ] Windows install 過程 `Ensure-TesseractChiTra` 從 `tessdata_fast` GitHub repo 抓 ~12MB 入 `<install>/tessdata/`

#### 6.14.6 pdf-ocr 內嵌 PDF.js viewer（v1.7.6）

- [ ] OCR 跑完後結果區下方自動展開「📄 預覽結果（直接拖選文字試試看）」viewer 區
- [ ] iframe 載入 `/static/vendor/pdfjs/web/viewer.html?file=/tools/pdf-ocr/preview/<uid>.pdf#pagemode=none&zoom=auto`（**絕對路徑**，不能用相對路徑否則 PDF.js 解析錯誤導致 0 頁）
- [ ] 用滑鼠拖選 PDF 內中文 → 可選取、可 Cmd+C / Ctrl+C 複製
- [ ] 工具列只有：sidebar toggle、find、zoom、page nav、rotate、download（其他編輯 / 開檔 / 列印 / 書籤已用 CSS 隱藏）
- [ ] 「在新分頁開啟 ↗」按鈕點下去 viewer 在新分頁正常開啟
- [ ] CJK 文字無方框（cmaps 載入 OK）
- [ ] 「怎麼確認是這個工具 OCR 的」說明區塊預設摺起（`<details>`），點開才展開
- [ ] **跨 user ACL（`/preview/{uid}.pdf` endpoint）**：以下 7 個情境覆蓋於 `tests/test_pdf_ocr_preview_acl.py`，CI 必過
  - [ ] auth ON + owner 自己 → 200（content-type: application/pdf, content-disposition: inline）
  - [ ] auth ON + 其他 user → 403
  - [ ] auth ON + 沒 owner record（猜 uid）→ 403（fail-secure）
  - [ ] auth ON + admin → 200（override）
  - [ ] uid 格式錯（`../etc/passwd` / `not-a-uuid` / 大寫 `g*32` / `a*31`）→ 400 / 404（require_uuid_hex 在 ACL 之前攔截）
  - [ ] auth ON + owner 對但檔案沒準備 → 404
  - [ ] auth OFF（單機家用）→ 200（直接通過）
- [ ] `admin/sys-deps` 顯示「PDF Viewer (PDF.js)」條目，版本「5.7.284」、CJK cmaps OK、標準字型 OK
- [ ] 故意刪 `static/vendor/pdfjs/build/pdf.mjs` → `jtdt update` 結尾 WARNING「PDF.js vendor 不完整」+ admin/sys-deps 顯示缺檔

### 6.12 機密 / 內網檢查（push 前必跑）

```bash
grep -rnE "192\.168\.|10\.[0-9]+\.[0-9]+\.[0-9]+|親測|OSSII 內部" \
  github/ --include='*.md' --include='*.html' --include='*.py' \
  | grep -vE "10\.0\.0\.|192\.168\.1\.10[^0-9]"
```

- [ ] 無真實內網 IP（test fixture 用 `10.0.0.x` / `192.168.1.10` placeholder OK）
- [ ] 無「親測」「內部」之類用語


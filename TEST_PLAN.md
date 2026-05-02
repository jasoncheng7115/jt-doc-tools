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
2. `pytest` 全綠
3. 重啟 server，所有路由 200（以 curl 跑 1.1 列表）
4. 手動跑一輪 2.x 清單
5. 跑完 §6 「歷史回歸案例」清單
6. 更新 `app/main.py` `VERSION`
7. 重啟，確認 footer 顯示新版本號
8. 確認停用的工具（`aes-zip`）仍保留程式碼但未顯示於側欄／首頁

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

### 6.5 升級流程 (含 DB migration)

- [ ] 從 v1.0.x 升到目前版本，所有 migration 跑完不報錯
- [ ] v3 migration: pdf-diff → doc-diff 既有 perms 遷移
- [ ] v4 migration: 既有 pdf-to-image 權限自動授予 image-to-pdf
- [ ] 升級後 default-user / clerk role 含新工具權限
- [ ] 升級後 service user 仍能讀 .venv 內檔案（chown 還原正確）

### 6.6 用詞檢查（push 前 grep）

```bash
# 不應出現的中國用語：
grep -rnE "回滾|軟依賴|硬依賴|系統依賴(?!\s*$)|圖像(?![幾何])|軟件|字體|打印|文檔|信息|視頻|網絡|服務器|菜單|屏幕|保存|默認|設置" \
  app/ static/ github/CHANGELOG.md github/README.md --include='*.py' --include='*.html' --include='*.md'
```

- [ ] grep 結果應為空（除了 memory / to_github.md 的解釋脈絡）
- [ ] 「依賴」→「相依」、「回滾」→「還原」、「硬刷」→「強制重新整理」

### 6.7 landing page (`docs/`)

- [ ] 「線上 PDF 工具的隱憂」/ 「地端自架 + 開源 才能安心」字級 24px / 字重 800
- [ ] 工具總數 / 「N 個工具」與 README hero 一致
- [ ] 截圖無內網 IP / browser chrome
- [ ] hero / 安裝指令 tab 切換正常

### 6.8 機密 / 內網檢查（push 前必跑）

```bash
grep -rnE "192\.168\.|10\.[0-9]+\.[0-9]+\.[0-9]+|親測|OSSII 內部" \
  github/ --include='*.md' --include='*.html' --include='*.py' \
  | grep -vE "10\.0\.0\.|192\.168\.1\.10[^0-9]"
```

- [ ] 無真實內網 IP（test fixture 用 `10.0.0.x` / `192.168.1.10` placeholder OK）
- [ ] 無「親測」「內部」之類用語


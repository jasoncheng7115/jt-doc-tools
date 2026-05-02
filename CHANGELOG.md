# 更新記錄

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號採 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

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
- **emoji 換成 SVG icon**：`📝 / 📄 / ⇄` 改成 icon macro `edit / page / swap`。`swap` 是新加的 icon，雙箭頭 left↔right。

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
- **欄位**：時間 （yyyy/MM/dd HH:mm:ss） / 使用者 / IP / 工具 （pill） / 檔名 （📄 icon + action） / 大小 （KB/MB/GB human-formatted， 右對齊） / 狀態 （HTTP code 著色：2xx 綠 / 4xx-5xx 紅）。
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

- **每個分區標題加 icon**：Backend 模式 ⚙、連線 🌐、搜尋 🔍、屬性對應 📋。

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

- **使用者管理：「內建」改成 disabled 按鈕**：原本 `🔒 內建` 是 inline span，跟其他列的按鈕不對齊。改成 `<button disabled>`，跟「編輯」「重設密碼」「刪除」一致排列。
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
- **內建管理員 （jtdt-admin / `is_admin_seed=1`） 不可被編輯角色或停用**：UI 隱藏編輯按鈕、顯示 🔒 內建標記，後端也 raise 拒絕。

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

- **字型管理頁可隱藏不要的字型**：每個字型旁加「🚫 隱藏」/「👁 顯示」按鈕。隱藏後 PDF 編輯器的字型下拉選單不會出現該字型，**檔案保留**隨時可取消隱藏。隱藏狀態存在 `data/font_settings.json`（`hidden: [...font ids...]`）。
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

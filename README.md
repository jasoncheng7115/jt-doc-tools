**繁體中文** ｜ [English](README_en.md)

# Jason Tools 文件工具箱 v1.15.32

> 整合式 PDF / Office 文件處理平台，47 個工具整合解決：**填單用印**、**浮水印**、**多頁合併 / 拆分 / 旋轉 / 整理**、**轉檔**、**掃描拼合**、**去識別化**、**字數統計**、**註解整理**、**差異比對**、**逐句翻譯**、**清單處理**、**電子發票處理**、**統編查詢**、**頁面編輯器**、**加密 / 解密**等。
>
> 企業功能：**本機 / LDAP / AD 多領域認證**、**SSO 單一登入**(OIDC + SAML，可接 M365 / Google / Keycloak)、**RBAC 角色權限**、**稽核記錄**、**SIEM 轉送**(syslog / CEF / GELF)、**字型管理**、**使用者工作區**、**背景作業與完成通知**、**REST API**。
>
> **不上雲，資料留在自己手中。** Linux / macOS / Windows 三平台都可單機跑或內網架站給多人用。

完整介紹網站：<https://jasoncheng7115.github.io/jt-doc-tools/>

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![CodeQL](https://github.com/jasoncheng7115/jt-doc-tools/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/jasoncheng7115/jt-doc-tools/actions/workflows/codeql.yml)
[![OWASP Top 10 (2025)](https://img.shields.io/badge/OWASP%20Top%2010%20(2025)-A01--A10%20covered-success?logo=owasp)](SECURITY.md)
[![Tests](https://img.shields.io/badge/pytest-6013%20passed-brightgreen?logo=pytest)](tests/)
[![Dependabot](https://img.shields.io/badge/Dependabot-enabled-success?logo=dependabot)](.github/dependabot.yml)
[![Python](https://img.shields.io/badge/python-3.12+-blue?logo=python&logoColor=white)](pyproject.toml)
[![Platforms](https://img.shields.io/badge/platforms-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](INSTALL.md)

---

## 一行安裝

### 系統需求

| 項目 | 最低 | 建議 |
|---|---|---|
| 作業系統 | Ubuntu 20.04+ / Debian 11+ / macOS 12+ / Windows 10 1809+ | 任一最新版 |
| 磁碟空間 | **12 GB** 整機 / VM / LXC 容量（最低）| **20 GB+**（含資料目錄成長空間） |
| 記憶體 RAM | 2 GB 可用 | 4 GB+ |
| CPU | x86_64 / arm64（Apple Silicon、Win11 ARM 都可）| 4 核心+ |
| 網路 | 安裝時可連 GitHub / PyPI（之後純內網運作）| — |
| Python | 3.10+（安裝腳本會自動處理 uv-managed Python） | — |

> **磁碟用量大解析**（為什麼底線抓 12 GB 而非看似夠的 5-8 GB）：
> - **OS 基底**：Debian / Ubuntu 最小裝 ~1.5-2 GB；其他 distro / 含桌面更大。
> - **安裝期間峰值 ~6-8 GB**：apt 暫存 .deb 套件 ~1 GB（OxOffice / LibreOffice 相依）+ uv wheel cache ~1-2 GB（PyTorch 700 MB + 其他）+ 解壓中間檔。安裝腳本會自動 `apt-get clean` + `uv cache clean` 釋放，但**峰值期間**就是要這麼大。
> - **安裝完成後常駐 ~3 GB**：Python 環境 ~1.5 GB（含 PyTorch / EasyOCR 主 OCR 引擎）+ tesseract trained data ~80 MB（chi_tra fast+best 雙變體 + eng）+ OxOffice/LibreOffice ~1 GB。EasyOCR 模型首次 OCR 時再下載 ~150 MB。
> - **資料目錄成長**：使用者上傳檔案 + 稽核記錄 + 歷史會持續累積。如資料磁碟吃緊，可用 `JTDT_DATA_DIR=/mnt/big-disk/jtdt curl ... | sudo -E bash` 改裝到別處。
>
> **LXC / VM 配置建議**：12 GB 是會通過的底線（OS 2 GB + 峰值 8 GB + 緩衝 2 GB），正式使用至少給 20 GB 才不會 3 個月後再爆。**8 GB LXC 一定裝不下**（已有客戶踩到）。

### 一行指令

**Linux / macOS**:
```bash
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
```

**Windows 10 / 11 — 按兩下安裝（建議，不必開 PowerShell）**：

到 [GitHub Releases](https://github.com/jasoncheng7115/jt-doc-tools/releases/latest) 下載
`jt-doc-tools-x.y.z-setup.exe`，按兩下執行即可。安裝精靈為繁體中文，內含解除安裝功能。

> **檔名上的版本比較舊沒有關係。** 這支安裝程式只是個引導程式（約 6 MB），
> 實際的程式碼是**安裝當下才從 GitHub 下載**的，所以不論你手上那支 .exe 是哪一版，
> 裝起來都會是最新版。安裝程式本身只有在需要改安裝流程時才會重新發佈。

**Windows 10 / 11 — PowerShell 一行指令**（以系統管理員身分執行）:
```powershell
$f="$env:TEMP\jtdt-install.ps1"; try { Invoke-WebRequest 'https://cdn.jsdelivr.net/gh/jasoncheng7115/jt-doc-tools@main/install.ps1' -OutFile $f -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop; powershell -NoProfile -ExecutionPolicy Bypass -File $f } catch { Write-Host "[X] 下載安裝腳本失敗：$($_.Exception.Message)" -ForegroundColor Red }; Read-Host '按 Enter 關閉'
```

裝完開瀏覽器到 **<http://127.0.0.1:8765/>** 即可使用。

> 安裝時長約 5-15 分鐘（依網速 — PyTorch 700MB 是大頭）。網速慢的環境建議先 `screen` / `tmux` 開背景再跑，避免斷線。

詳細安裝說明見 **[INSTALL.md](INSTALL.md)**（含必要工具、平台差異、解除安裝）。

> **程式碼簽章 / Code signing**：Free code signing on Windows provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org).<br>（Windows 安裝程式由 SignPath.io 提供免費程式碼簽章，憑證由 SignPath Foundation 核發。）

---

## 47 個工具速覽

### 填單用印
- **表單自動填寫** — 自動偵測欄位 + 模板填值
- **用印與簽名** — 拖放套用印章 / 簽名
- **浮水印** — 文字 / 圖片浮水印，支援多檔批次

### 檔案編輯
- **頁面編輯器** — 文字框 / 形狀 / 白底 / 螢光筆 / 簽名 / 註解 / 真刪物件
- **頁面整理 / 旋轉 / 頁碼 / 多頁合併 (N-up)**
- **頁面加框** [需 OxOffice/LibreOffice] — 每頁加上框線，可設粗細 / 顏色 / 線型 / 圓角 / 內外雙框 / 陰影，可自頁緣內縮或貼齊內容、指定頁面範圍與首頁排除；主打投影片加外框，收 PDF 與文書檔（文書檔輸入時需引擎）
- **騎縫章** [需 OxOffice/LibreOffice] — 一個印章切成數片蓋在連續頁面上，**抽換或掉頁一眼看得出來**（那一片對不起來）；支援側邊騎縫與對開跨頁，可設一個章跨幾頁、位置與角度可固定或亂數；印章可從資產庫選、自己上傳、或由系統依文字產生（文書檔輸入時需引擎）
- **頁面尺寸統一** [需 OxOffice/LibreOffice] — 把混合尺寸的頁面統一成同一種紙張（A4 / A3 / 自訂）；可選縮放留白、置中不縮放或裁切填滿，直橫混排會自動跟著轉向，**內容維持向量、文字仍選得到**（不是轉成圖片）。標案常見 A3 圖說混 A4 內文，送印裝訂前先統一（文書檔輸入時需引擎）
- **檔案合併 / 頁面分拆**
- **書籤與目錄** [需 OxOffice/LibreOffice] — 替 PDF 加書籤（閱讀器左側的導覽）與可點的目錄頁；**一次選多個檔案會自動串接，並以檔名建立第一層書籤**，子文件原有的書籤降一層保留，標案 / 年報這種十幾個檔合成幾百頁的文件最有感。也可自動偵測標題或貼上現成目錄清單（文書檔輸入時需引擎）
- **掃描拼合** — 拉入多張掃描，自動抓出有內容的區塊、保留原彩色，依原位置合成到同一張 A4 白底；主打證件正反面，可拖曳微調、淡灰底自動淨白

### 內容處理
- **擷取文字 / 圖片 / 附件** — 含 LLM 段落重排選項
- **字數統計** [需 OxOffice/LibreOffice] — 表格 + 圖表 + LLM 摘要；收 PDF / 辦公文件 / 純文字
- **註解整理 / 清除 / 平面化**
- **OCR 文字辨識** — 掃描 PDF / 圖片跑 OCR 後變可搜尋、可滑鼠選取複製（同 macOS 預覽程式 Live Text 概念）；雙引擎（**EasyOCR** 預設，中日韓辨識準確度高；**Tesseract** 備援），可選 LLM 校正 typo。**支援外部 GPU 識別伺服器**（DGX Spark / H100 / 4090 等），管理介面下載 `install.sh` 即可一鍵部署，每頁辨識時間從 CPU 上的 8-15 秒降到 GPU 上的 0.3-0.8 秒（**速度 10× 以上**）。
- **送件前檢核** — 批次驗收：頁面尺寸、字型嵌入、欄位完整、敏感資料殘留、隱藏內容
- **清單處理** — 貼文字 / 上傳 .txt / .csv / .xlsx / .docx / .pdf 等檔案，一行一筆做排序 / 去重 / 篩選 / 取頭尾 / 大小寫轉換等，可組合多種操作；結果一鍵複製或下載 .txt / .csv / .xlsx
- **電子發票處理** — 掃台灣電子發票 QR Code 解出發票號碼 / 日期 / 金額 / 統編，自動帶賣方公司名、行業、會計科目（規則 + 可選 LLM 判讀），支援報帳檢查 + 當期發票檢查，匯出 .xlsx / .ods / .csv / .json / .xml / .txt / .md（標題可自訂）
- **乘車證明整理** — 拉一批台鐵購票證明 / 高鐵電子車票證明 PDF，自動整理日期 / 交通工具 / 來源-目的 / 費用成表格，欄位可自訂，匯出 .xlsx / .ods / .csv / .json / .xml / .txt / .md 報帳
- **統編查詢** — 輸入 8 位統一編號反查，或公司 / 機關 / 學校名稱、地址、行業關鍵字模糊搜尋（標示命中字）；含類別篩選 + 批次查詢 + CSV 匯出

### 格式轉換 [需 OxOffice/LibreOffice]
- **辦公文件轉 PDF** — 把辦公文件批次轉成 PDF
- **辦公文件格式互轉** — 同一類文件之間互轉格式：文書檔（.odt / .docx / .doc / .rtf / .txt）、試算表（.ods / .xlsx / .xls / .csv）、簡報（.odp / .pptx / .ppt）各自互換；`.docx` / `.xlsx` / `.pptx` 還可以指定版本（Word 2007 或 Word 2010–365 等）
- **辦公文件轉圖片** — PDF 或辦公文件每頁轉成 PNG；多頁自動打包 ZIP
- **圖片轉 PDF**
- **PDF 轉 Markdown** — PDF 轉結構化 Markdown，保留標題 / 表格 / 粗體，適合餵 LLM、RAG 預處理
- **Markdown 轉辦公文件** [需 OxOffice/LibreOffice] — 貼上或拖入 Markdown，套用主題後輸出 PDF 或文書檔（.docx / .odt），含所有頁面預覽
- **PDF 轉文書檔（Beta）** — PDF 反轉成文書檔（.docx / .odt），三引擎可選：pdf2docx（經典穩定）、自家 jtdt-reform（幾何規則重組成可編輯內文）、自家 jtdt-layout（版面最忠於原稿：頁面錨定文字方塊，位置 / 圖片 / 框線近 1:1 保留）
- **PDF 轉簡報** — PDF 反轉成 PowerPoint (.pptx) / OpenDocument 簡報 (.odp)，**一頁對一張投影片**，投影片尺寸沿用原稿（直向 PDF 也照樣還原）；用 jtdt-layout 版面重現引擎

### 資安處理
- **文件去識別化 / 文字去識別化** — 身分證 / 電話 / 銀行帳號 / 統編 / AD DN 等 14+ 種敏感資料。
  三種處理方式：**編修**（塗黑真刪）、**遮罩**（`0912****678`）、**替換**（換成看起來正常但不是真的值，適合拿去測試系統 / 給外部看的報表）
- **PDF 加密 / 解密**
- **中繼資料清除**
- **隱藏內容掃描**
- **文件差異比對 / 文字差異比對**
- **逐句翻譯**
- **文件翻譯** [需 OxOffice/LibreOffice] — 整份辦公文件翻成另一種語言，產出**同格式、同版面**的檔案（只換文字，不重排版面）。支援 .doc / .docx / .odt、.xls / .xlsx / .ods、.ppt / .pptx / .odp；翻完附前 6 頁預覽
- **壓縮**

> 標 [需 OxOffice/LibreOffice] 的工具會用到 OxOffice / LibreOffice（OxOffice 優先，OSSII 維護的台灣本地化 fork，CJK 支援更好）。其他 28 個工具只處理 PDF / 純文字 / 圖片，不需要 Office 引擎。安裝腳本會自動處理。

---

## 使用者工作區（選用，管理員可開關）

把各工具輸出的 PDF / PNG / Word (.docx) / OpenDocument (.odt) 暫存在伺服器、跨工具接力使用，不必在工具之間來回下載再上傳。

- **存至工作區** — 各工具輸出的 PDF / PNG / Word / ODT 一鍵保留在伺服器，綁帳號隔離，只有自己看得到。
- **從工作區載入** — 任何工具的上傳區一鍵取回（OCR → 蓋章 → 去識別化 …），免重新找檔。
- **我的工作區頁** — 首頁縮圖預覽（PDF 渲染第一頁）、容量條與保留期限、下載 / 重新命名 / 刪除、多選批次刪除、直接拖曳上傳。
- **管理員控管** — 啟用 / 停用整個功能（停用即完全隱藏）、統一每人容量額度、單檔上限、保留時數，可清空使用者佔用。
- **隔離與安全** — 每人檔案僅自己可見；認證關閉時為單機共用工作區；保留時數到期由排程自動清理。

預設啟用，管理員可於「設定 → 工作區設定」隨時關閉。

---

## 背景作業與完成通知

耗時的工作（轉檔、OCR、逐句翻譯、大檔壓縮…）送出後就交給伺服器跑，**可以直接關掉分頁**，不必守著進度條。

- **送出即背景執行** — 26 個工具走作業系統，包含 PDF 轉文書檔 / 轉簡報檔、格式互轉、OCR 文字辨識、逐句翻譯、辦公文件轉 PDF、壓縮、合併、分拆、浮水印、用印、騎縫章、送件前檢核等。
- **我的作業** — 進度、佇列位置、已過時間、結果下載都在同一頁；跑一半可取消。逐句翻譯這類「產出不是單一檔案」的工具，點回去會接回原本的頁面繼續看對照表。
- **重開機不會憑空消失** — 作業狀態存在資料庫裡，服務重啟後未完成的會標示為中斷，而不是無聲無息不見。
- **不會把機器打爆** — 派送前先估算這個作業要用多少記憶體，不夠就讓它留在佇列排隊；同時處理數與 Office 轉檔併行上限都可在管理區調整。
- **網頁回應優先** — 轉檔行程降優先權並限制可用核心數（預設保留一顆給網頁），轉大檔時操作介面不會卡住。
- **完成後自動存入工作區** — 只在你已經離開頁面時才存，避免和你自己按的「存至工作區」重複。
- **優先派送（插隊）** — 管理員可指定少數幾位使用者（高階主管、有時效性的重要工作），他們送出的作業直接排到佇列最前面。**不會中斷正在執行的作業** —— 效果是「下一個換你」，不是「現在就換你」；名單內的人彼此仍照先來後到，記憶體不足時一樣要排隊。
- **看得出在等什麼** — 作業清單標示它會用到哪些共用資源（Office 轉檔 / OCR / 外部服務），三者各有自己的併行上限，「為什麼排這麼久」一眼看得出卡在哪一個。

管理員在「設定 → 背景作業與併行度」還有這些：

- **全站作業一覽** — 誰送的、哪個工具、哪個檔、排第幾位、跑多久，可只看進行中；每一筆都能直接取消。
- **暫停派送** — 維護前讓手上的跑完但不再開新的。（已在執行的轉檔是獨立子行程，凍結不了，只能取消該筆 —— 介面照實說明。）
- **效能與歷史** — 執行中 / 排隊中數量、Office 轉檔佔用、CPU 與記憶體用量，點一下看歷史趨勢圖。記憶體是**實測子行程**的數字（真正吃記憶體的是 soffice，不是我們的執行緒），量不到才顯示估計值並標明。
- **併行上限全部可調** — 同時處理數、Office 轉檔同時數、外部服務同時呼叫數、轉檔 CPU 上限、保留記憶體。**填再大也會被實際可用記憶體夾住**。

### 完成通知

作業結束（成功或失敗）時主動通知，訊息只包含**工具名稱、檔名與狀態，不含檔案內容**。

- **站內通知** — 側欄的通知按鈕顯示未讀數與最近完成的作業，認證關閉時一樣可用。
- **Email** — HTML 版通知信，含站台 logo 與工具圖示（圖片隨信內嵌，不必對外連線），並附純文字版。收件地址取自帳號的信箱欄位；LDAP / AD / SSO 帳號自動由來源同步（可指定屬性，例如 `mail` / `mailPrimaryAddress`），本機帳號可自行在個人卡片修改。
- **通訊軟體 / Webhook** — Telegram、Slack、Microsoft Teams、Discord、Zulip、Nextcloud Talk、LINE、通用 Webhook。**這些管道目前標示為開發階段**：載荷格式有單元測試，但尚未接上真實服務端到端驗證過；只有 Email 是實機寄送、收件者確認收過的。
- **每人自選** — 使用者自己決定要開哪些管道；管理員負責填連線設定（SMTP、Bot token 等）。

管理員可在「設定 → 通知設定」與「設定 → 背景作業與併行度」調整；「設定 → 作業管理」可看全站作業。

---

## AD / LDAP 目錄管理

接得上只是起點 —— 真正會出事的是主要 DC 重啟、權限掛在主要群組上卻沒生效、離職的人還留在系統裡、帳號被別人拿去用。

- **多台網域控制站容錯** — 伺服器位址可填多台（逗號或換行分隔），依序嘗試；主要 DC 維護或重啟時自動換下一台，不會變成全公司登不進來。連線與查詢都有逾時，DC 不通時幾秒內回明確訊息，不會讓使用者枯等。掛掉的 DC 修好後會自動回到輪替。
- **AD 主要群組（primaryGroupID）也算數** — AD 的主要群組**不會出現在 `memberOf`**：把某個群組設成主要群組後，權限掛上去成員一個都拿不到，而且在 AD 使用者的「成員隸屬」分頁也看不出原因。現在會一併納入權限解析。OpenLDAP 沒有這個概念，行為不受影響。
- **巢狀群組會繼承** — 權限指派給上層部門群組，子群組的成員也拿得到（群組樹本來就畫在畫面上，卻不會繼承，比明講不支援更容易誤導）。
- **離職 / 停用的帳號看得見** — 使用者清單新增「目錄已無」與「AD 已停用」兩個檢視與徽章。判定**只認完整掃描**：帶名稱過濾的同步只看得到一部分目錄，拿它當基準會把整個組織誤標成離職，所以沒做過完整掃描時一律不下結論。本機與 SSO 帳號永遠不標。
- **密碼到期預警** — 標示幾天後到期（已過期另外標示）。日期讀的是 AD 自己算好的值，套了細緻密碼原則（PSO）或「密碼永久有效」的人都正確 —— 用網域全域設定自己推算會算錯。
- **批次停用，而且有安全閥** — 一鍵停用整個檢視的帳號，按下去之前先試算並告訴你實際會動到幾個人；也可設成同步後自動停用（**預設關閉**）。**一次要動的人數超過目錄帳號總數的 20% 就整批中止、一個都不動** —— 服務帳號密碼過期或搜尋範圍被改都會讓「全公司都不見了」，那時候什麼都不做才是對的。只停用不刪除，帳號與權限指派都保留。絕不動內建管理員。
- **現在有誰登入著** — 使用者清單顯示在線人數（同一人開多個瀏覽器算一位），每個帳號可看目前的登入裝置（瀏覽器 / 作業系統、來源位址、最後活動）並個別或全部強制登出，動作留在稽核記錄。帳號可能外洩時不必改密碼就能先把所有裝置踢掉。
- **權限指派不必等對方先登入** — 目錄瀏覽裡點任何一位使用者就能直接指派角色，新人報到當天就能用；「所屬群組」每一列也能當場設群組權限。原本只能指派給整個 OU。
- **「他到底能用什麼」一眼看完** — 編輯使用者時可展開「有效權限」面板：最終能用哪些工具、每一項是哪一條規則給的（直接角色 / 群組含巢狀 / OU）。稽核回應與交接說明直接照著念。

---

## LLM AI 加值（選用，預設關閉）

接 OpenAI-compatible 後端（本機 Ollama / vLLM / LM Studio / DGX Spark）後，**12 個工具**自動多出聰明選項：

| 工具 | LLM 做什麼 | 模式 |
|---|---|---|
| 逐句翻譯 | 翻譯時保留排版 + 領域專業用詞 | text |
| 擷取文字 | 把 PDF 雙欄切斷的句子重新接回 | text |
| OCR 文字辨識 | 校正 OCR typo（同 word count 才套用，避免幻覺改字） | text |
| 表單自動填寫 | 填完後 LLM 看 PNG 校驗欄位錯位 / 截斷 | **vision** |
| 送件前檢核 | 內容語意檢查 + PNG 視覺驗收（補充 regex / 結構檢查抓不到的問題） | text + **vision** |
| 文件去識別化 | regex 抓不到的客戶代號 / 主管姓名 / 內部編號 | text |
| 文字去識別化 | 同上，純文字輸入版 | text |
| 字數統計 | 額外生成 3-5 句摘要 + TOP 10 關鍵字 | text |
| 註解整理 | 多筆審閱意見自動分「重大 / 一般 / 提問」 | text |
| 文件差異比對 | 行 diff 之外多給「主要修改了哪幾條條款」自然語言摘要 | text |
| 電子發票處理 | 規則對不到的品項，用 LLM 判讀會計科目分類 | text |

**核心工具完全不依賴 LLM**；沒設定就跟以前一樣全部能用。詳見 **[LLM.md](LLM.md)**。

---

## 文件導覽

| 文件 | 內容 |
|---|---|
| **[INSTALL.md](INSTALL.md)** | 三平台詳細安裝、必要工具、安裝位置、系統需求、解除安裝 |
| **[OPS.md](OPS.md)** | 日常運維：`jtdt` 指令、升級、反向代理(nginx/Caddy)、監聽位置、備份還原、排程清理 |
| **[AUTH.md](AUTH.md)** | 認證 / RBAC / 內建帳號(jtdt-admin / jtdt-auditor)/ 2FA / SSO(OIDC+SAML) / Reverse Proxy SSO(Kerberos) / 帳號鎖定 / 緊急復原 |
| **[reverse_proxy_sso.md](reverse_proxy_sso.md)** | Reverse Proxy SSO（Kerberos / SPNEGO）完整部署：AD service account、setspn、ktpass / keytab、Nginx 設定、瀏覽器自動登入、標頭偽造防護 |
| **[API.md](API.md)**（[線上網頁版](https://jasoncheng7115.github.io/jt-doc-tools/api.html)）| REST API:Bearer token、endpoint 一覽、上傳格式、回傳格式、錯誤碼、curl / Python 範例、Job 流程 |
| **[LLM.md](LLM.md)** | LLM AI 加值功能（預設關閉）：12 個工具如何用 LLM、效果範例、部署選項（Ollama / vLLM / DGX Spark） |
| **[SECURITY.md](SECURITY.md)** | 資安政策、OWASP Top 10 (2025) 對照、漏洞回報管道、GitHub native scan 整合 |
| **[CHANGELOG.md](CHANGELOG.md)** | 完整更新記錄 |
| **[TEST_PLAN.md](TEST_PLAN.md)** | 測試清單、發版前檢查 |
| **[OFFLINE.md](OFFLINE.md)** | 封閉網路 / 離線安裝（Docker 映像檔搬運、走公司 PyPI 代理） |
| **[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)** | 第三方套件授權聲明 |

---

## 隱私 / 安全要點

- **⚠ 不建議直接對「公開網際網路」開放** — 同仁多半會用它處理公司**內部 / 機密文件**
  （合約、報價、個資、統編資料等），直接對外開放等於把這些文件與後台一併暴露，有**資料
  外洩風險**；加上本工具會解析上傳的 PDF / Office / 圖片（底層是 MuPDF / LibreOffice /
  Pillow 等記憶體不安全的原生程式，屬高風險攻擊面）。**首選只在內網 / VPN 使用**；若因
  業務必須對外屬「風險自負」，至少要反向代理 + HTTPS + 認證 + 強制 2FA + WAF / 速率限制
  + 持續更新相依。詳見 [OPS.md](OPS.md)。
- **⚠ 非本機存取一律走反向代理 + HTTPS** — 只要不是「本機單人」使用（任何網路 /
  多人 / 內網 / 對外），**一律放在 nginx（或 Caddy）反向代理 + HTTPS 後面,不要把
  `:8765` 直接對網路開放**。應用程式預設只綁 `127.0.0.1:8765`（純 HTTP 無 TLS）,
  直接對外等於明文傳帳密與文件。正確做法見下方與 [OPS.md](OPS.md)。
- **不上雲、資料留在自己手中** — 所有檔案處理發生在你的伺服器上
- **資料目錄獨立** — 不會跟使用者個人檔案混在一起，Windows 不 roam
- **預設不啟用認證**（單機模式） — 全新安裝跟以前一樣大家直接用；要多人或內網部署再啟用
- **稽核記錄 + SIEM 轉送** — 啟用認證後所有敏感操作記下並可即時轉發
- **可選 LLM 校驗** — 預設關閉，自接 Ollama / 本機 LLM 才會啟用，不打雲端

### 反向代理（nginx）資安設定

```nginx
server {
    listen 443 ssl;
    http2 on;
    server_name docs.example.com;

    ssl_certificate     /etc/letsencrypt/live/docs.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/docs.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    server_tokens off;            # 不洩 nginx 版本（ZAP「Server Leaks Version」）
    client_max_body_size 300M;    # 必設：上傳大檔
    proxy_read_timeout 900s;      # 必設：LLM 工具單筆推理可能數分鐘
    proxy_send_timeout 900s;
    proxy_buffering off;

    location / {
        proxy_pass http://127.0.0.1:8765/;          # 後端只聽 127.0.0.1
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;  # 必設：後端據此設 Secure cookie + HSTS
    }
}
```

安全標頭（CSP / HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy）由
**後端 app 自動設定**（HSTS 依 `X-Forwarded-Proto` 判斷 https）→ **nginx 不要再
`add_header` 一次**,否則會出現重複標頭。三個常見地雷(必掛 root 路徑、`client_max_body_size`、
逾時)、七項共通要求、防 `X-Forwarded-For` 偽造，以及 **nginx / Caddy / Apache / HAProxy /
Traefik / IIS(ARR) / F5 BIG-IP** 完整範例，都見 [OPS.md](OPS.md)。

詳見 [SECURITY.md](SECURITY.md)。

---

## 開發 / 進階

```bash
# Clone repo
git clone https://github.com/jasoncheng7115/jt-doc-tools
cd jt-doc-tools

# 用 uv 建環境(不修改系統 Python)
uv sync

# 跑測試
uv run pytest

# 開發模式(自動 reload)
JTDT_DEBUG=true uv run python -m app.main
```

---

## 授權

**GNU Affero General Public License v3.0 或任何後續版本（AGPL-3.0-or-later）** —— 詳見 [LICENSE](LICENSE)。
第三方套件授權見 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

### 這對您代表什麼

- **自己用（公司內部架站給同仁用）**：完全自由，沒有任何額外義務。修改也可以。
- **您改了程式，又把它當成網路服務提供給別人使用**（AGPL 第 13 條，**含公司內部使用者**）：
  必須把您修改後的完整原始碼提供給那些使用者。沒有修改就沒有這個問題 —— 指向本專案的
  GitHub 即可。
- **想閉源散布 / 包進自家商業產品**：AGPL 不允許。

會採用 AGPL 而不是寬鬆授權，是因為核心的 PDF 引擎 **PyMuPDF 本身就是 AGPL**
（Artifex 雙授權），本程式在同一個行程內使用它。

## 免責聲明

本軟體依「現狀」(AS IS)提供，**不附任何明示或暗示之保證**，包含但不限於商業適售性、特定用途之適用性、不侵權之保證。

- 使用者應**自行承擔**使用本軟體之全部風險
- 對於本軟體導致之任何**直接、間接、附帶、衍生性或懲罰性損害**（含資料毀損、商業中斷、收益損失、商譽損害等），作者與貢獻者**概不負責**
- 涉及個人資料、敏感商業文件處理時，使用者應**自行確保符合**所在地之個人資料保護法、公司資安政策、以及相關法規（包含但不限於我國個人資料保護法、營業秘密法）
- 本軟體之 LLM / AI 校驗等功能為**選用且預設關閉**；若啟用後接外部模型供應商，相關資料傳輸風險由使用者自負
- 本軟體之輸出結果（如表單自動填寫、去識別化、OCR、LLM 校對）僅供**輔助參考**，最終正確性仍須由使用者確認；對重要文件請務必對照原檔複核
- 本軟體與 Adobe、Microsoft、OSSII、TheDocumentFoundation 等任何第三方公司**無任何附屬、贊助或背書關係**

繼續使用即視為接受上述條款。

---

## 連結 / 作者

- **介紹網站**：<https://jasoncheng7115.github.io/jt-doc-tools/>
- **原始碼庫**：<https://github.com/jasoncheng7115/jt-doc-tools>
- **回報問題**：<https://github.com/jasoncheng7115/jt-doc-tools/issues>

**Jason Cheng** (Jason Tools)


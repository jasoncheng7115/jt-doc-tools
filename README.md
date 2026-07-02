# Jason Tools 文件工具箱 v1.12.56

> 整合式 PDF / Office 文件處理平台，39 個工具一站式解決：**填單用印**、**浮水印**、**多頁合併 / 拆分 / 旋轉 / 整理**、**轉檔**、**掃描拼合**、**去識別化**、**字數統計**、**註解整理**、**差異比對**、**逐句翻譯**、**清單處理**、**電子發票處理**、**統編查詢**、**頁面編輯器**、**加密 / 解密**等。
>
> 企業功能：**本機 / LDAP / AD 多領域認證**、**SSO 單一登入**(OIDC + SAML，可接 M365 / Google / Keycloak)、**RBAC 角色權限**、**稽核記錄**、**SIEM 轉送**(syslog / CEF / GELF)、**字型管理**、**使用者工作區**、**REST API**。
>
> **不上雲，資料留在自己手中。** Linux / macOS / Windows 三平台都可單機跑或內網架站給多人用。

完整介紹網站：<https://jasoncheng7115.github.io/jt-doc-tools/>

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![CodeQL](https://github.com/jasoncheng7115/jt-doc-tools/actions/workflows/codeql.yml/badge.svg?branch=main)](https://github.com/jasoncheng7115/jt-doc-tools/actions/workflows/codeql.yml)
[![OWASP Top 10 (2025)](https://img.shields.io/badge/OWASP%20Top%2010%20(2025)-A01--A10%20covered-success?logo=owasp)](SECURITY.md)
[![Tests](https://img.shields.io/badge/pytest-470%20passed-brightgreen?logo=pytest)](tests/)
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

**Windows 10 / 11**（以系統管理員身分執行 PowerShell）:
```powershell
$f="$env:TEMP\jtdt-install.ps1"; try { Invoke-WebRequest 'https://cdn.jsdelivr.net/gh/jasoncheng7115/jt-doc-tools@main/install.ps1' -OutFile $f -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop; powershell -NoProfile -ExecutionPolicy Bypass -File $f } catch { Write-Host "[X] 下載安裝腳本失敗：$($_.Exception.Message)" -ForegroundColor Red }; Read-Host '按 Enter 關閉'
```

裝完開瀏覽器到 **<http://127.0.0.1:8765/>** 即可使用。

> 安裝時長約 5-15 分鐘（依網速 — PyTorch 700MB 是大頭）。網速慢的環境建議先 `screen` / `tmux` 開背景再跑，避免斷線。

詳細安裝說明見 **[INSTALL.md](INSTALL.md)**（含必要工具、平台差異、解除安裝）。

---

## 39 個工具速覽

### 填單用印
- **表單自動填寫** — 自動偵測欄位 + 模板填值
- **用印與簽名** — 拖放套用印章 / 簽名
- **浮水印** — 文字 / 圖片浮水印，支援多檔批次

### 檔案編輯
- **頁面編輯器** — 文字框 / 形狀 / 白底 / 螢光筆 / 簽名 / 註解 / 真刪物件
- **頁面整理 / 旋轉 / 頁碼 / 多頁合併 (N-up)**
- **檔案合併 / 頁面分拆**
- **掃描拼合** — 拉入多張掃描，自動抓出有內容的區塊、保留原彩色，依原位置合成到同一張 A4 白底；主打證件正反面，可拖曳微調、淡灰底自動淨白

### 內容處理
- **擷取文字 / 圖片 / 附件** — 含 LLM 段落重排選項
- **字數統計** — 表格 + 圖表 + LLM 摘要
- **註解整理 / 清除 / 平面化**
- **OCR 文字辨識** — 掃描 PDF / 圖片跑 OCR 後變可搜尋、可滑鼠選取複製（同 macOS 預覽程式 Live Text 概念）；雙引擎（**EasyOCR** 預設，中日韓辨識準確度高；**Tesseract** 備援），可選 LLM 校正 typo。**支援外部 GPU 識別伺服器**（DGX Spark / H100 / 4090 等），管理介面下載 `install.sh` 即可一鍵部署，每頁辨識時間從 CPU 上的 8-15 秒降到 GPU 上的 0.3-0.8 秒（**速度 10× 以上**）。
- **送件前檢核** — 批次驗收：頁面尺寸、字型嵌入、欄位完整、敏感資料殘留、隱藏內容
- **清單處理** — 貼文字 / 上傳 .txt / .csv / .xlsx / .docx / .pdf 等檔案，一行一筆做排序 / 去重 / 篩選 / 取頭尾 / 大小寫轉換等，可組合多種操作；結果一鍵複製或下載 .txt / .csv / .xlsx
- **電子發票處理** — 掃台灣電子發票 QR Code 解出發票號碼 / 日期 / 金額 / 統編，自動帶賣方公司名、行業、會計科目（規則 + 可選 LLM 判讀），支援報帳檢查 + 當期發票檢查，匯出 .xlsx / .ods / .csv / .json / .xml / .txt / .md（標題可自訂）
- **統編查詢** — 輸入 8 位統一編號反查，或公司 / 機關 / 學校名稱、地址、行業關鍵字模糊搜尋（高亮命中字）；含類別篩選 + 批次查詢 + CSV 匯出

### 格式轉換 [需 OxOffice/LibreOffice]
- **文書轉 PDF / 圖片** — Word / Excel / PowerPoint / ODF
- **圖片轉 PDF**
- **PDF 轉文書檔（Beta）** — PDF 反轉成 Word (.docx) / OpenDocument (.odt)，雙引擎可選：pdf2docx 與自家 jtdt-reform，還原版面 / 表格 / 圖片

### 資安處理
- **文件去識別化 / 文字去識別化** — 身分證 / 電話 / 銀行帳號 / 統編 / AD DN 等 14+ 種敏感資料
- **PDF 加密 / 解密**
- **中繼資料清除**
- **隱藏內容掃描**
- **文件差異比對 / 文字差異比對**
- **逐句翻譯**
- **壓縮**

> 標 [需 OxOffice/LibreOffice] 的工具會用到 OxOffice / LibreOffice（OxOffice 優先，OSSII 維護的台灣本地化 fork，CJK 支援更好）。其他 27 個工具只處理 PDF / 純文字 / 圖片，不需要 Office 引擎。安裝腳本會自動處理。

---

## 使用者工作區（選用，管理員可開關）

把各工具輸出的 PDF / PNG 暫存在伺服器、跨工具接力使用，不必在工具之間來回下載再上傳。

- **存至工作區** — 各工具輸出的 PDF / PNG 一鍵保留在伺服器，綁帳號隔離，只有自己看得到。
- **從工作區載入** — 任何工具的上傳區一鍵取回（OCR → 蓋章 → 去識別化 …），免重新找檔。
- **我的工作區頁** — 首頁縮圖預覽（PDF 渲染第一頁）、容量條與保留期限、下載 / 重新命名 / 刪除、多選批次刪除、直接拖曳上傳。
- **管理員控管** — 啟用 / 停用整個功能（停用即完全隱藏）、統一每人容量額度、單檔上限、保留時數，可清空使用者佔用。
- **隔離與安全** — 每人檔案僅自己可見；認證關閉時為單機共用工作區；保留時數到期由排程自動清理。

預設啟用，管理員可於「設定 → 工作區設定」隨時關閉。

---

## LLM AI 加值（選用，預設關閉）

接 OpenAI-compatible 後端（本機 Ollama / vLLM / LM Studio / DGX Spark）後，**11 個工具**自動多出聰明選項：

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
| **[LLM.md](LLM.md)** | LLM AI 加值功能（預設關閉）：11 個工具如何用 LLM、效果範例、部署選項（Ollama / vLLM / DGX Spark） |
| **[SECURITY.md](SECURITY.md)** | 資安政策、OWASP Top 10 (2025) 對照、漏洞回報管道、GitHub native scan 整合 |
| **[CHANGELOG.md](CHANGELOG.md)** | 完整更新記錄 |
| **[TEST_PLAN.md](TEST_PLAN.md)** | 測試清單、發版前檢查 |
| **[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)** | 第三方套件授權聲明 |

---

## 隱私 / 安全要點

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
逾時)與 Caddy 範例見 [OPS.md](OPS.md)。

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

Apache License 2.0 — 詳見 [LICENSE](LICENSE)。第三方套件授權見 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

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

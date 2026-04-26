# Jason Tools 文件工具箱 v1.1.47

> 整合式 PDF / Office 文件處理平台。包含 22 個工具：**表單自動填寫**、**用印與簽名**、**浮水印**、**N-up 多頁合併**、**檔案合併 / 分拆 / 轉向 / 頁面整理 / 頁碼**、**文書轉 PDF / 圖片**、**擷取文字 / 圖片 / 附件**、**敏感資料去識別化**、**PDF 加密 / 解密**、**Metadata 清除**、**隱藏內容掃描**、**差異比對**、**頁面編輯器**、**壓縮**、**AES 加密壓縮檔**。
>
> 企業管理功能：可選 **本機 / LDAP / AD 多領域認證**（同名帳號可分屬不同領域 `username@realm`）、**角色與權限矩陣 (RBAC)**、**稽核記錄**、**Log 轉發**（syslog / CEF / GELF）、**字型管理**、**API tokens**。
>
> **不上雲，資料留在自己內網。** 可在 Linux 架站給內網多人使用，所有檔案處理只發生在你的伺服器，不外傳任何雲端。

完整介紹網站：<https://jasoncheng7115.github.io/jt-doc-tools/>

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

---

## 功能總覽

### 填單與用印
- **表單自動填寫**：上傳廠商資料表 / 申請書（PDF、Word、Excel、ODF），自動辨識欄位並填入公司基本資料
- **用印與簽名**：套用印章、簽名、Logo 圖片到 PDF，支援批次
- **浮水印**：透明度、角度、平鋪填滿或指定位置

### 檔案編輯
- **PDF 編輯器**：疊加文字、圖片、形狀、白底遮罩、標註；可編輯或刪除原 PDF 上的文字與圖片；內建字型管理
- **PDF 壓縮**：3 種預設或進階自訂圖片 DPI、字型子集化等
- **多頁合併（N-up）**：把 2/4/6/8/9/16 頁 PDF 合併到一張紙；自訂版面、間距、邊框、頁碼
- **檔案合併**：把多份 PDF 依上傳順序合併為一份
- **分拆 / 轉向 / 頁面整理 / 插入頁碼**：基本 PDF 操作

### 內容擷取
- **擷取文字**：輸出 TXT / Markdown / Word / ODT；可選交給 LLM 重排被 PDF 版面切斷的段落
- **擷取圖片**：把 PDF 中所有嵌入的圖片擷取出來，xref 自動 dedupe，可勾選下載成 ZIP 或單張下載
- **PDF 附件萃取**：列出並取出 PDF 中嵌入的檔案（EmbeddedFiles）

### 格式轉換
- **文書轉 PDF**：Word / Excel / PowerPoint / ODF 批次轉 PDF
- **文書轉圖片**：PDF 或 Office 文件每頁轉成 PNG，5 段 DPI 可選（100 草稿 → 400 高 DPI 印刷）

### 資安處理
- **文件去識別化**：偵測身分證 / 手機 / Email / 統編 / 信用卡 / 銀行帳號 / 公司名稱 / 人名 …，一鍵編修（Redaction，不可還原）或資料遮罩（Masking，保留格式）
- **PDF 密碼保護 / 解除**：AES-256 加密、權限控制
- **AES 加密壓縮檔**：把多份檔案打包成密碼保護的 zip,可寄信附件
- **Metadata 清除**：作者 / 標題 / XMP / 修訂歷史
- **隱藏內容掃描**：JavaScript / 嵌入檔 / 隱藏文字 / 外部連結等風險，一鍵清除
- **差異比對**：兩份 PDF 逐頁並排比對，文字差異標紅

### 多人 / 企業環境
- **多領域認證**：本機帳號 / LDAP / Active Directory；同名帳號可在不同領域並存（如 `jason@local` + `jason@ldap`），以 `username@realm` 區分
- **角色與權限矩陣 (RBAC)**：6 個內建角色（管理員 / 一般使用者 / 文管 / 財務 / 業務 / 法務資安）+ 自訂角色；可指派工具使用權限到使用者 / 群組 / OU
- **稽核記錄**：登入 / 操作 / 設定變更 / 工具呼叫（含上傳檔名）全部記下；async 寫入不影響服務效能；可篩選 / 匯出 CSV
- **Log 轉發**：syslog (RFC 5424 UDP/TCP) / CEF (ArcSight) / GELF (Graylog)，多目的地並行；失敗 retry + 寫本地稽核
- **檔案保留 / 自動清理**：表單填寫 / 用印簽名 / 浮水印歷史 / 暫存檔 / Job 結果 / 稽核 各類獨立保留天數；排程清理
- **API tokens**：對外呼叫 `/api/*` 的 bearer token；歸屬到使用者，視同該使用者權限
- **字型管理**：標準 14 字型 + 內建思源黑體 / 宋體繁中 + 系統字型 + 自訂上傳

---

## 快速安裝（一行指令）

> 三平台都需要**系統管理員權限**。安裝過程會：
>
> 1. 偵測 / 自動安裝 OxOffice 或 LibreOffice（沒裝會自動補）
> 2. 下載獨立的 Python 環境（不影響系統 Python）
> 3. 從 GitHub 取得程式碼
> 4. 註冊為系統服務（systemd / launchd / Windows Service）
> 5. 開機自動啟動

### Linux（Ubuntu / Debian / Fedora 等）

#### 必要工具（沒裝請先補）

桌面版的 Ubuntu / Debian / Fedora 通常都已內建 `curl` / `git`，但**伺服器版或 LXC / Docker 容器**通常是精簡安裝，需要先補：

```bash
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y curl git ca-certificates
```

```bash
# Fedora / RHEL / Rocky
sudo dnf install -y curl git ca-certificates
```

> 不需要預先安裝 Python — 安裝腳本會用 `uv` 下載一份獨立的 Python 3.12，不會影響系統。

#### 一行安裝

```bash
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
```

### macOS

#### 必要工具

macOS 內建 `curl`，<b>`git` 在第一次跑會自動觸發 Xcode Command Line Tools 安裝精靈</b>（會跳一個 GUI 視窗，按「安裝」即可，約 5-10 分鐘）。如果想預先裝：

```bash
xcode-select --install
```

> 不需要預先安裝 Python 或 Homebrew — 安裝腳本會用 `uv` 下載獨立 Python。
> OxOffice / LibreOffice 沒裝的話腳本會自動下載安裝（OxOffice 從 GitHub release，失敗時 fallback Homebrew LibreOffice — 此時需 brew）。

#### 一行安裝

```bash
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
```

### Windows 10 / 11

#### 必要工具

Windows 10 1803+ / 11 內建 `curl`，但 <b>`git` 不在預設清單</b>。建議先用 winget 補：

```powershell
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
```

> 沒裝 git 也行 — 安裝腳本會 fallback 用 zip tarball 下載。但有 git 之後 `jtdt update` 才能用。
> 不需要預先安裝 Python — 由 uv 處理。
> Office 引擎優先 OxOffice MSI（GitHub release），失敗時 fallback `winget install LibreOffice`。

#### 一行安裝

以<b>「以系統管理員身分執行」</b>開啟 PowerShell（右鍵 PowerShell 圖示 → 系統管理員），貼：

```powershell
$u='https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.ps1'; $f="$env:TEMP\jtdt-install.ps1"; [Net.ServicePointManager]::SecurityProtocol='Tls12'; (New-Object Net.WebClient).DownloadFile($u,$f); powershell -NoProfile -ExecutionPolicy Bypass -File $f; Read-Host '安裝結束，按 Enter 關閉'
```

> **為什麼這麼長？** PS 5.1 (Windows 內建版本) 有幾個雷：
> 1. `iex (irm ...)` 模式下，腳本內 `exit / throw` 會把整個 PowerShell 視窗一起關掉
> 2. `irm -OutFile` 會做 UTF-8 → 系統 codepage 的編碼轉換，把腳本內非 ASCII 字元搞壞，導致 parser 報「缺 `}`」一堆假錯誤
>
> 用 `WebClient.DownloadFile` 寫原始 bytes（不經 PS 編碼層），用**子 PowerShell** 執行（子行程退出不殺父視窗），最後父 shell `Read-Host` 等按 Enter。整段貼進「以系統管理員身分執行」的 PowerShell 就行。

---

安裝完成後，開瀏覽器到 **http://127.0.0.1:8765/** 即可使用。

---

## 日常操作（`jtdt` 指令）

安裝完成後會在系統 PATH 加入 `jtdt`：

| 指令 | 說明 |
|------|------|
| `jtdt status`        | 顯示版本、服務狀態、安裝路徑 |
| `jtdt start`         | 啟動服務 |
| `jtdt stop`          | 停止服務 |
| `jtdt restart`       | 重啟服務 |
| `jtdt logs -f`       | 即時看 log |
| `jtdt open`          | 用瀏覽器開啟介面 |
| `sudo jtdt update`   | 升級到最新版（會自動備份資料） |
| `sudo jtdt uninstall` | 解除安裝（資料保留），加 `--purge` 連同資料一起刪 |

> **升級流程**：自動停服務 → 備份 `data/` → `git pull` → `uv sync` → 重啟 → 健康檢查。最近 3 份備份會自動保留。

---

## 安裝位置

| OS | 程式 | 資料 | 服務 |
|---|---|---|---|
| Linux   | `/opt/jt-doc-tools/`            | `/var/lib/jt-doc-tools/data/` | systemd `jt-doc-tools.service` |
| macOS   | `/usr/local/jt-doc-tools/`       | `/Library/Application Support/jt-doc-tools/data/` | LaunchDaemon `com.jasontools.doctools` |
| Windows | `C:\Program Files\jt-doc-tools\` | `C:\ProgramData\jt-doc-tools\Data\` | Windows Service `jt-doc-tools` (NSSM) |

---

## 反向代理（HTTPS）

預設綁 `127.0.0.1:8765`，若要從外部用 HTTPS 存取，加上反向代理：

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name docs.example.com;

    ssl_certificate     /etc/letsencrypt/live/docs.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/docs.example.com/privkey.pem;

    # 必設：上傳大檔需要
    client_max_body_size 100M;

    # 保險：未來 LLM 校驗會跑比較久
    proxy_read_timeout 300s;

    location / {
        proxy_pass http://127.0.0.1:8765/;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Caddy

```caddyfile
docs.example.com {
    reverse_proxy 127.0.0.1:8765 {
        flush_interval -1
    }
    request_body {
        max_size 100MB
    }
}
```

### 反代地雷

1. **`client_max_body_size 100M`**：上傳大檔必設
2. **必須掛根路徑** `/`（不能 `/jtdt/`）— 所有頁面用絕對路徑
3. **`proxy_read_timeout 300s`** 保險（未來 LLM 校驗會用到）
4. WebSocket 暫時沒用，不需特別 headers

---

## 系統需求

| 項目 | 最低 | 建議 |
|------|------|------|
| OS   | Ubuntu 22.04 / macOS 12 / Windows 10 21H2 | 較新版本 |
| RAM  | 2 GB | 4 GB+ |
| 硬碟 | 2 GB（含 OxOffice + Python 環境） | 10 GB+（含使用者資料） |
| Python | 不需預裝（uv 會自動下載獨立 Python 3.12） | — |

---

## 隱私 / 安全

- **不上雲、資料留在自己內網**：所有檔案處理發生在你的伺服器上（單機或區網內 server），不上傳任何雲端服務
- **資料目錄獨立**：在系統 `data/` 區，不會跟使用者個人檔案混在一起，也不會 roam（Windows）
- **預設不啟用認證**（單機模式）：全新安裝跟以前一樣大家直接用；要多人或內網部署再到 `/admin/auth-settings` 啟用本機 / LDAP / AD 認證
- **稽核記錄 + Log 轉發**：啟用認證後所有敏感操作（登入、權限變更、工具呼叫含檔名、設定變更）都會記下，可即時轉發到 SIEM（Splunk / Graylog / ArcSight 等）
- **可選 LLM 校驗**：如果有用 LLM 重排段落，預設關閉，需要在 `/admin/llm-settings` 自己接 Ollama / 本機 LLM 才會啟用

---

## 開發 / 進階

```bash
# Clone repo
git clone https://github.com/jasoncheng7115/jt-doc-tools
cd jt-doc-tools

# 用 uv 建環境（不修改系統 Python）
uv sync

# 跑測試
uv run pytest

# 開發模式（自動 reload）
JTDT_DEBUG=true uv run python -m app.main
```

---

## 授權

Apache License 2.0 — 詳見 [LICENSE](LICENSE)。

第三方套件授權聲明見 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

---

## 連結

- **介紹網站**：<https://jasoncheng7115.github.io/jt-doc-tools/>
- **GitHub Repo**：<https://github.com/jasoncheng7115/jt-doc-tools>
- **回報問題**：<https://github.com/jasoncheng7115/jt-doc-tools/issues>

---

## 作者

**Jason Cheng** (Jason Tools)
jason@jason.tools

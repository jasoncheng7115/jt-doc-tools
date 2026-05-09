# 安裝指南

三個平台的詳細安裝說明 + 必要工具 + 一行安裝指令。安裝過程會：

1. 偵測 / 自動安裝 OxOffice 或 LibreOffice（沒裝會自動補）
2. 下載獨立的 Python 環境（不影響系統 Python）
3. 從 GitHub 取得程式碼
4. 註冊為系統服務（systemd / launchd / Windows Service）
5. 開機自動啟動

> 三平台都需要**系統管理員權限**。

## Linux（Ubuntu / Debian / Fedora 等）

### 必要工具（沒裝請先補）

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

### 一行安裝

```bash
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
```

## macOS

### 必要工具

macOS 內建 `curl`，<b>`git` 在第一次跑會自動觸發 Xcode Command Line Tools 安裝精靈</b>（會跳一個 GUI 視窗，按「安裝」即可，約 5-10 分鐘）。如果想預先裝：

```bash
xcode-select --install
```

> 不需要預先安裝 Python 或 Homebrew — 安裝腳本會用 `uv` 下載獨立 Python。
> OxOffice / LibreOffice 沒裝的話腳本會自動下載安裝（OxOffice 從 GitHub release，失敗時 fallback Homebrew LibreOffice — 此時需 brew）。

### 一行安裝

```bash
curl -fsSL https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.sh | sudo bash
```

## Windows 10 / 11

### 必要工具

Windows 10 1803+ / 11 內建 `curl`，但 <b>`git` 不在預設清單</b>。建議先用 winget 補：

```powershell
winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
```

> 沒裝 git 也行 — 安裝腳本會 fallback 用 zip tarball 下載。但有 git 之後 `jtdt update` 才能用。
> 不需要預先安裝 Python — 由 uv 處理。
> Office 引擎優先 OxOffice MSI（GitHub release），失敗時 fallback `winget install LibreOffice`。

### 一行安裝

以<b>「以系統管理員身分執行」</b>開啟 PowerShell（右鍵 PowerShell 圖示 → 系統管理員），貼：

```powershell
$f="$env:TEMP\jtdt-install.ps1"; try { Invoke-WebRequest 'https://cdn.jsdelivr.net/gh/jasoncheng7115/jt-doc-tools@main/install.ps1' -OutFile $f -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop; powershell -NoProfile -ExecutionPolicy Bypass -File $f } catch { Write-Host "[X] 下載安裝腳本失敗：$($_.Exception.Message)" -ForegroundColor Red; Write-Host "請檢查網路（VPN？防火牆？DNS？）後重試。" -ForegroundColor Yellow }; Read-Host '按 Enter 關閉'
```

> **為什麼用 jsdelivr 不用 raw.githubusercontent.com？** GitHub raw 的 Fastly cache 不認 query string 當 cache key，安裝腳本更新後最久要等 5 分鐘才生效。jsdelivr 的 CDN 對 GitHub repo 更新反應快得多，幾秒就同步。
>
> **連線失敗會 15 秒內 fail-fast**：用 `Invoke-WebRequest -TimeoutSec 15`（不像舊版 `Net.WebClient.DownloadFile()` 會卡 2 分鐘），網路不通馬上紅字提示。
>
> 安裝腳本本身已是純 ASCII，不需要任何 BOM 或編碼處理；用**子 PowerShell** 執行（子行程退出不殺父視窗），最後父 shell `Read-Host` 等按 Enter。

## 驗證安裝

安裝完成後，開瀏覽器到 **<http://127.0.0.1:8765/>** 即可使用。

或用 CLI：

```bash
jtdt status
```

## 安裝位置

| OS | 程式 | 資料 | 服務 |
|---|---|---|---|
| Linux   | `/opt/jt-doc-tools/`            | `/var/lib/jt-doc-tools/data/` | systemd `jt-doc-tools.service` |
| macOS   | `/usr/local/jt-doc-tools/`       | `~/Library/Application Support/jt-doc-tools/data/` | `/Applications/Jason Tools 文件工具箱.app`（LaunchServices 啟動，由登入項目自動啟動） |
| Windows | `C:\Program Files\jt-doc-tools\` | `C:\ProgramData\jt-doc-tools\Data\` | Windows Service `jt-doc-tools` (WinSW) |

## 系統需求

| 項目 | 最低 | 建議 |
|------|------|------|
| OS   | Ubuntu 22.04 / macOS 12 / Windows 10 21H2 | 較新版本 |
| RAM  | 2 GB | 4 GB+ |
| 硬碟 | 2 GB（含 OxOffice + Python 環境） | 10 GB+（含使用者資料） |
| Python | 不需預裝（uv 會自動下載獨立 Python 3.12） | — |

## 解除安裝

```bash
# 保留資料
sudo jtdt uninstall

# 連同資料一起刪
sudo jtdt uninstall --purge
```

Windows 沒有 `sudo`，請以「系統管理員身分執行 PowerShell」後跑 `jtdt uninstall`。

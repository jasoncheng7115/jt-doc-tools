# ==========================================================================
# Jason Tools 文件工具箱 — 一鍵安裝 (Windows 10 / 11)
#
# 用法：以「系統管理員身分執行」開啟 PowerShell，貼上：
#   iex (irm https://raw.githubusercontent.com/jasoncheng7115/jt-doc-tools/main/install.ps1)
# ==========================================================================
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # 加快 Invoke-WebRequest

# Force UTF-8 in console so 中文 doesn't render as ?
# (PS 5.1 預設 console codepage 是 cp950 / cp936，會吃掉非系統 codepage 的字元)
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    $OutputEncoding = [System.Text.UTF8Encoding]::new()
    chcp 65001 > $null 2>&1
} catch { }

$RepoUrl     = 'https://github.com/jasoncheng7115/jt-doc-tools'
$RepoBranch  = 'main'
$ServiceName = 'jt-doc-tools'

function Log  ($m) { Write-Host "==> $m"  -ForegroundColor Cyan }
function Ok   ($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "[!]  $m" -ForegroundColor Yellow }
function Die  ($m) {
    Write-Host "[X]  $m" -ForegroundColor Red
    Write-Host ""
    Write-Host "Install failed. Press Enter to close ..." -ForegroundColor Red
    try { Read-Host | Out-Null } catch { Start-Sleep -Seconds 30 }
    throw $m
}

# --------------------------------------------------------------- 管理員檢查
$ident = [Security.Principal.WindowsIdentity]::GetCurrent()
$prin  = New-Object Security.Principal.WindowsPrincipal($ident)
if (-not $prin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die "需要系統管理員權限。請以「以系統管理員身分執行」開啟 PowerShell 後再跑。"
}

# --------------------------------------------------------------- 平台
$Arch = if ([Environment]::Is64BitOperatingSystem) { 'x86_64' } else { 'x86' }
if ($Arch -eq 'x86') { Die "本程式不支援 32-bit Windows。" }

$ProgFiles  = ${env:ProgramFiles}
$ProgData   = ${env:ProgramData}
$InstallDir = Join-Path $ProgFiles 'jt-doc-tools'
$DataDir    = Join-Path (Join-Path $ProgData 'jt-doc-tools') 'Data'
$LogDir     = Join-Path (Join-Path $ProgData 'jt-doc-tools') 'Logs'
$BinDir     = Join-Path $InstallDir 'bin'
$NssmExe    = Join-Path $BinDir 'nssm.exe'
$UvExe      = Join-Path $BinDir 'uv.exe'
$CliShim    = Join-Path $InstallDir 'jtdt.cmd'

# --------------------------------------------------------------- Office 偵測
function Test-Office {
    # OxOffice / LibreOffice 的常見安裝路徑
    $paths = @(
        "${env:ProgramFiles}\OxOffice\program\soffice.exe",
        "${env:ProgramFiles}\LibreOffice\program\soffice.exe",
        "${env:ProgramFiles(x86)}\LibreOffice\program\soffice.exe"
    )
    foreach ($p in $paths) { if (Test-Path $p) { return $true } }
    if (Get-Command soffice.exe -ErrorAction SilentlyContinue) { return $true }
    return $false
}

function Install-OxOffice {
    Log "嘗試從 GitHub 下載並安裝 OxOffice ..."
    try {
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/OSSII/OxOffice/releases/latest' -Headers @{ 'User-Agent' = 'jt-doc-tools-installer' }
        $asset = $rel.assets | Where-Object { $_.name -match '\.msi$' -and ($_.name -match 'win|Windows|x64') } | Select-Object -First 1
        if (-not $asset) { Warn "找不到 OxOffice 的 Windows MSI 安裝檔"; return $false }
        $tmp = Join-Path $env:TEMP "oxoffice-$(Get-Date -Format yyyyMMddHHmmss).msi"
        Log "下載 $($asset.browser_download_url)"
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp
        Log "安裝 OxOffice (silent) ..."
        $proc = Start-Process msiexec.exe -ArgumentList "/i `"$tmp`" /qn /norestart" -Wait -PassThru
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) { Warn "OxOffice MSI 安裝回傳 exit code $($proc.ExitCode)"; return $false }
        return Test-Office
    } catch {
        Warn "OxOffice 安裝失敗：$_"
        return $false
    }
}

function Install-LibreOffice {
    Log "改用 LibreOffice (透過官方下載 LATEST 鏈結) ..."
    try {
        # LibreOffice 官方下載頁的「latest stable」MSI 鏡像（透過 download.documentfoundation.org/.../LibreOffice_x.y.z_Win_x86-64.msi）
        # 這裡用 winget 比較穩，沒有 winget 才 fallback。
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $proc = Start-Process winget -ArgumentList "install --id TheDocumentFoundation.LibreOffice -e --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
            if ($proc.ExitCode -eq 0) { return Test-Office }
        }
        Warn "未安裝 winget 或 winget 安裝失敗"
        return $false
    } catch {
        Warn "LibreOffice 安裝失敗：$_"
        return $false
    }
}

function Ensure-Office {
    if (Test-Office) { Ok "已偵測到 Office 引擎"; return }
    Log "未偵測到 OxOffice / LibreOffice"
    if (Install-OxOffice) { Ok "OxOffice 安裝完成"; return }
    if (Install-LibreOffice) { Ok "LibreOffice 安裝完成"; return }
    Write-Host ""
    Warn "OxOffice 與 LibreOffice 都自動安裝失敗。請手動安裝後再重跑這支腳本："
    Warn "  • OxOffice：    https://github.com/OSSII/OxOffice/releases"
    Warn "  • LibreOffice： https://www.libreoffice.org/download/"
    Start-Process 'https://github.com/OSSII/OxOffice/releases'
    exit 1
}

# --------------------------------------------------------------- uv
function Install-Uv {
    if (Test-Path $UvExe) { Ok "uv 已存在"; return }
    Log "下載 uv ..."
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    # uv 官方 PowerShell 安裝腳本，限定安裝目錄
    $env:UV_INSTALL_DIR = $BinDir
    $env:UV_NO_MODIFY_PATH = '1'
    # 注意：astral.sh/uv/install.ps1 走 application/octet-stream，PS 5.1 的
    # iwr.Content 會回 byte[]，iex 會炸 "無法將 byte[] 轉換為 String"。
    # 用 irm (Invoke-RestMethod) 才會自動 UTF-8 解碼成 string。
    Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1')
    if (-not (Test-Path $UvExe)) { Die "uv 安裝失敗" }
    Ok "uv 安裝在 $UvExe"
}

# --------------------------------------------------------------- NSSM (Windows Service wrapper)
function Install-Nssm {
    if (Test-Path $NssmExe) { Ok "nssm 已存在"; return }
    Log "下載 NSSM (Windows Service wrapper) ..."
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $tmp = Join-Path $env:TEMP "nssm.zip"
    # NSSM 2.24 是最後正式版。nssm.cc 偶爾 503，準備多個 mirror。
    $urls = @(
        'https://nssm.cc/release/nssm-2.24.zip',
        'https://web.archive.org/web/2024/https://nssm.cc/release/nssm-2.24.zip',
        'https://github.com/jasoncheng7115/jt-doc-tools/releases/download/deps/nssm-2.24.zip'
    )
    $ok = $false
    foreach ($url in $urls) {
        Log "  嘗試 $url"
        for ($i = 0; $i -lt 3; $i++) {
            try {
                (New-Object Net.WebClient).DownloadFile($url, $tmp)
                if ((Get-Item $tmp).Length -gt 100000) { $ok = $true; break }
            } catch {
                Warn "    第 $($i+1) 次失敗：$($_.Exception.Message.Split([Environment]::NewLine)[0])"
                Start-Sleep -Seconds 2
            }
        }
        if ($ok) { Ok "  下載成功"; break }
    }
    if (-not $ok) { Die "NSSM 下載失敗（所有 mirror 都連不上）。請稍後重試，或手動下載 nssm-2.24.zip 放到 $tmp 後再跑一次。" }
    $extractDir = Join-Path $env:TEMP "nssm-extract"
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
    Expand-Archive -Path $tmp -DestinationPath $extractDir -Force
    Copy-Item -Path (Join-Path $extractDir 'nssm-2.24\win64\nssm.exe') -Destination $NssmExe -Force
    Remove-Item $tmp, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Ok "nssm 安裝在 $NssmExe"
}

# --------------------------------------------------------------- 程式碼
function Fetch-Code {
    if (Test-Path (Join-Path $InstallDir '.git')) {
        Log "已存在安裝，更新 git 內容 ..."
        Push-Location $InstallDir
        try {
            git fetch --depth=1 origin $RepoBranch
            git reset --hard "origin/$RepoBranch"
        } finally { Pop-Location }
        return
    }
    if ((Test-Path $InstallDir) -and (Get-ChildItem $InstallDir -Force | Where-Object { $_.Name -ne 'bin' }) ) {
        Die "$InstallDir 已存在但不是 git repo，請先備份/移除再重跑"
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Log "從 $RepoUrl clone 程式碼 ..."
        git clone --depth=1 --branch $RepoBranch $RepoUrl $InstallDir
    } else {
        Log "git 未安裝，改用 tarball 下載 ..."
        $tmp = Join-Path $env:TEMP "jtdt-src.zip"
        Invoke-WebRequest -Uri "$RepoUrl/archive/refs/heads/$RepoBranch.zip" -OutFile $tmp
        $extractDir = Join-Path $env:TEMP "jtdt-extract"
        if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
        Expand-Archive -Path $tmp -DestinationPath $extractDir -Force
        $first = Get-ChildItem $extractDir -Directory | Select-Object -First 1
        Copy-Item "$($first.FullName)\*" $InstallDir -Recurse -Force
        Remove-Item $tmp, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Setup-Python {
    Log "建立獨立 Python 環境並安裝依賴 (uv sync) ..."
    Push-Location $InstallDir
    try {
        & $UvExe sync --frozen 2>$null
        if ($LASTEXITCODE -ne 0) { & $UvExe sync }
        if ($LASTEXITCODE -ne 0) { Die "uv sync 失敗" }
    } finally { Pop-Location }
    if (-not (Test-Path (Join-Path $InstallDir '.venv\Scripts\python.exe'))) {
        Die "Python venv 建立失敗"
    }
    Ok "Python 環境就緒：$InstallDir\.venv"
}

# --------------------------------------------------------------- 資料
function Prepare-Data {
    Log "準備資料目錄 $DataDir ..."
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir  | Out-Null
    $seed = Join-Path $InstallDir 'data'
    if ((Test-Path $seed) -and (-not (Get-ChildItem $DataDir -Force -ErrorAction SilentlyContinue))) {
        Copy-Item "$seed\*" $DataDir -Recurse -Force
    }
}

# --------------------------------------------------------------- 服務
function Install-Service {
    Log "安裝 Windows Service (透過 NSSM) ..."
    # 砍掉舊的（如果有）
    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        & $NssmExe stop $ServiceName confirm | Out-Null
        & $NssmExe remove $ServiceName confirm | Out-Null
    }
    $py = Join-Path $InstallDir '.venv\Scripts\python.exe'
    & $NssmExe install $ServiceName $py "-m" "app.main" | Out-Null
    & $NssmExe set $ServiceName AppDirectory $InstallDir | Out-Null
    & $NssmExe set $ServiceName AppEnvironmentExtra "JTDT_DATA_DIR=$DataDir" "JTDT_HOST=127.0.0.1" "JTDT_PORT=8765" | Out-Null
    & $NssmExe set $ServiceName AppStdout (Join-Path $LogDir 'jt-doc-tools.log') | Out-Null
    & $NssmExe set $ServiceName AppStderr (Join-Path $LogDir 'jt-doc-tools.err') | Out-Null
    & $NssmExe set $ServiceName AppRotateFiles 1 | Out-Null
    & $NssmExe set $ServiceName AppRotateBytes 5242880 | Out-Null
    & $NssmExe set $ServiceName Start SERVICE_AUTO_START | Out-Null
    & $NssmExe set $ServiceName Description "Jason Tools 文件工具箱 — PDF / Office 文件處理平台" | Out-Null
    & $NssmExe start $ServiceName | Out-Null
    Ok "Windows Service '$ServiceName' 已安裝並啟動，開機自動啟動"
}

# --------------------------------------------------------------- jtdt CLI shim
function Install-Cli {
    Log "建立 jtdt 指令 ..."
    $py = Join-Path $InstallDir '.venv\Scripts\python.exe'
    $content = @"
@echo off
"$py" -m app.cli %*
"@
    Set-Content -Path $CliShim -Value $content -Encoding ASCII
    # 加入 PATH（系統等級，需要管理員）
    $sysPath = [Environment]::GetEnvironmentVariable('Path','Machine')
    if ($sysPath -notmatch [regex]::Escape($InstallDir)) {
        [Environment]::SetEnvironmentVariable('Path', "$sysPath;$InstallDir", 'Machine')
        Ok "已加入系統 PATH（重開新的 terminal 才會生效）"
    }
    Ok "jtdt 指令：$CliShim"
}

# --------------------------------------------------------------- 健康檢查
function Health-Check {
    Log "等待服務啟動 ..."
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/healthz' -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) {
                Ok "服務已上線：http://127.0.0.1:8765/"
                return
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    Warn "30 秒內未通過健康檢查，請執行：jtdt logs"
}

# --------------------------------------------------------------- 主流程
Write-Host ""
Log "Jason Tools 文件工具箱 — Windows 系統安裝"
Log "平台：Windows ($Arch)"
Log "程式：$InstallDir"
Log "資料：$DataDir"
Write-Host ""

Ensure-Office
Install-Uv
Install-Nssm
Fetch-Code
Setup-Python
Prepare-Data
Install-Service
Install-Cli
Health-Check

Write-Host ""
Ok "安裝完成！"
Write-Host ""
Write-Host "  介面：    http://127.0.0.1:8765/"
Write-Host "  狀態：    jtdt status"
Write-Host "  Log：     jtdt logs -f"
Write-Host "  升級：    jtdt update    （需以系統管理員身分跑 PowerShell）"
Write-Host "  解除：    jtdt uninstall （加 --purge 連同資料一起刪）"
Write-Host ""

# 自動開瀏覽器到介面（單機模式 user 體驗）
try {
    Start-Process 'http://127.0.0.1:8765/'
    Ok "已自動開啟瀏覽器"
} catch {
    Warn "無法自動開啟瀏覽器，請手動前往 http://127.0.0.1:8765/"
}

# 不要讓 PowerShell 視窗自動關掉（user 看不到上面訊息）
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  按 Enter 鍵關閉此視窗"                             -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
try { Read-Host | Out-Null } catch { Start-Sleep -Seconds 30 }
Write-Host ""

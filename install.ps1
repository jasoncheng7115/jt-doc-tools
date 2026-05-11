# 不能用 'Stop' — 會把 native command 寫 stderr 當成 terminating error，
# 整個 install.ps1 會在 winsw / uv / git 任何寫一行 stderr 時就死。
# 我們在 Cmdlet 失敗時用 try/catch 處理，native command 失敗用 $LASTEXITCODE 判斷。
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

# Allow $env:JTDT_REPO_URL / $env:JTDT_REPO_BRANCH override — used for
# pre-release testing against a local file:// mirror without polluting GitHub.
# Customer installs run with no env vars set, so default to GitHub.
$RepoUrl     = if ($env:JTDT_REPO_URL)    { $env:JTDT_REPO_URL }    else { 'https://github.com/jasoncheng7115/jt-doc-tools' }
$RepoBranch  = if ($env:JTDT_REPO_BRANCH) { $env:JTDT_REPO_BRANCH } else { 'main' }
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

# Admin check
$ident = [Security.Principal.WindowsIdentity]::GetCurrent()
$prin  = New-Object Security.Principal.WindowsPrincipal($ident)
if (-not $prin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die "Administrator privileges required. Please run PowerShell as Administrator and try again."
}

# Network preflight — fail fast if no internet (VPN off, firewall, DNS, etc.).
# Without this, downloads of uv / python / git tarball / OxOffice silently
# stall for a couple of minutes each before timing out.
function Test-Internet {
    $hosts = @('github.com', 'cdn.jsdelivr.net', 'astral.sh')
    foreach ($h in $hosts) {
        try {
            $r = Invoke-WebRequest -Uri "https://$h" -Method Head `
                -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
            if ($r.StatusCode -ge 200) { return $true }
        } catch { }
    }
    return $false
}
Log "Checking network ..."
if (-not (Test-Internet)) {
    Die @"
Cannot reach the internet (github.com / cdn.jsdelivr.net / astral.sh all unreachable).
Please check:
  1) VPN / proxy connectivity
  2) Firewall outbound rules (port 443)
  3) DNS resolution
Then re-run the installer.
"@
}
Ok "Network reachable"

# Platform
$Arch = if ([Environment]::Is64BitOperatingSystem) { 'x86_64' } else { 'x86' }
if ($Arch -eq 'x86') { Die "32-bit Windows is not supported." }

$ProgFiles  = ${env:ProgramFiles}
$ProgData   = ${env:ProgramData}
$InstallDir = Join-Path $ProgFiles 'jt-doc-tools'
$DataDir    = Join-Path (Join-Path $ProgData 'jt-doc-tools') 'Data'
$LogDir     = Join-Path (Join-Path $ProgData 'jt-doc-tools') 'Logs'
$BinDir     = Join-Path $InstallDir 'bin'
$NssmExe    = Join-Path $BinDir 'nssm.exe'   # legacy, kept for migration detection
$WinswExe   = Join-Path $BinDir 'jtdt-svc.exe'   # WinSW renamed (must match XML basename)
$WinswXml   = Join-Path $BinDir 'jtdt-svc.xml'
$UvExe      = Join-Path $BinDir 'uv.exe'
$CliShim    = Join-Path $InstallDir 'jtdt.cmd'

# Office detection
function Test-Office {
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
    Log "Trying OxOffice from GitHub release ..."
    try {
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/OSSII/OxOffice/releases/latest' -Headers @{ 'User-Agent' = 'jt-doc-tools-installer' }
        $asset = $rel.assets | Where-Object { $_.name -match '\.msi$' -and ($_.name -match 'win|Windows|x64') } | Select-Object -First 1
        if (-not $asset) { Warn "No Windows MSI asset found for OxOffice"; return $false }
        $tmp = Join-Path $env:TEMP "oxoffice-$(Get-Date -Format yyyyMMddHHmmss).msi"
        Log "Downloading $($asset.browser_download_url)"
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp
        Log "Installing OxOffice (silent) ..."
        $proc = Start-Process msiexec.exe -ArgumentList "/i `"$tmp`" /qn /norestart" -Wait -PassThru
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) { Warn "OxOffice MSI exit code $($proc.ExitCode)"; return $false }
        return Test-Office
    } catch {
        Warn "OxOffice install failed: $_"
        return $false
    }
}

function Install-LibreOffice {
    Log "Falling back to LibreOffice via winget ..."
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $proc = Start-Process winget -ArgumentList "install --id TheDocumentFoundation.LibreOffice -e --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
            if ($proc.ExitCode -eq 0) { return Test-Office }
        }
        Warn "winget not available or install failed"
        return $false
    } catch {
        Warn "LibreOffice install failed: $_"
        return $false
    }
}

function Ensure-Office {
    if (Test-Office) { Ok "Office engine detected"; return }
    Log "No OxOffice / LibreOffice detected"
    if (Install-OxOffice) { Ok "OxOffice installed"; return }
    if (Install-LibreOffice) { Ok "LibreOffice installed"; return }
    Write-Host ""
    Warn "Neither OxOffice nor LibreOffice could be installed automatically."
    Warn "Please install manually and re-run this script:"
    Warn "  - OxOffice:    https://github.com/OSSII/OxOffice/releases"
    Warn "  - LibreOffice: https://www.libreoffice.org/download/"
    Start-Process 'https://github.com/OSSII/OxOffice/releases'
    exit 1
}

# Tesseract OCR — soft optional. Used by pdf-editor to recover real text from
# PDFs whose Identity-H subset font has a missing/identity ToUnicode CMap.
# Any failure here is non-fatal — system runs fine without it, OCR feature
# just degrades to "ask user to retype" message.
function Find-TesseractExe {
    # 1) PATH
    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Path }
    # 2) Standard install locations (winget / UB-Mannheim installer)
    #    issue #4: winget sometimes installs but doesn't add to PATH so
    #    Get-Command misses it.
    $candidates = @(
        "C:\Program Files\Tesseract-OCR\tesseract.exe",
        "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return ""
}

function Test-Tesseract {
    $exe = Find-TesseractExe
    if (-not $exe) { return $false }
    $langs = & $exe --list-langs 2>&1 | Out-String
    return ($langs -match 'chi_tra')
}

function Ensure-TesseractChiTra {
    # winget UB-Mannheim 的 silent install 預設元件不一定含 chi_tra；
    # 直接把 traineddata 從官方 tessdata GitHub repo 下載到 tessdata 目錄
    # 是最穩的補救（檔案 ~12MB，比 reinstall 整個 Tesseract 快也乾淨）。
    $exe = Find-TesseractExe
    if (-not $exe) { return }
    $langs = & $exe --list-langs 2>&1 | Out-String
    if ($langs -match 'chi_tra') { return }  # already there
    $tessdataDir = Join-Path (Split-Path -Parent $exe) 'tessdata'
    if (-not (Test-Path $tessdataDir)) {
        Warn "tessdata dir not found: $tessdataDir (tesseract install layout 不標準，跳過 chi_tra 下載)"
        return
    }
    # tessdata_fast/chi_tra.traineddata ~12MB, 快版（精度略低速度快），對 OCR
    # 補捉 PDF 字夠用。要更精準可改抓 tessdata_best 但檔案大很多 (~50MB)。
    $url = 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_tra.traineddata'
    $dst = Join-Path $tessdataDir 'chi_tra.traineddata'
    Log "Downloading chi_tra.traineddata (~12MB) for Chinese OCR..."
    try {
        Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
        if (Test-Path $dst) {
            $sz = (Get-Item $dst).Length
            if ($sz -gt 1000000) {
                Ok "chi_tra.traineddata downloaded ($([math]::Round($sz/1MB,1)) MB)"
            } else {
                Warn "chi_tra download incomplete (only $sz bytes), removed"
                Remove-Item $dst -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {
        Warn "chi_tra download failed: $_  (Chinese OCR 不可用，可手動下載 https://github.com/tesseract-ocr/tessdata_fast 放到 $tessdataDir)"
    }
}

function Add-TesseractToPath {
    # 加到 SYSTEM PATH 讓所有 process（包括 jt-doc-tools service）看得到。
    # 即使我們的 app 程式碼會 fallback 抓標準路徑，加進 PATH 仍是好習慣 —
    # CLI 用法、其它工具呼叫 tesseract 都能 work。issue #4 客戶踩雷。
    $exe = Find-TesseractExe
    if (-not $exe) { return }
    $dir = Split-Path -Parent $exe
    $cur = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $parts = $cur -split ';' | Where-Object { $_ -ne '' }
    if ($parts -contains $dir) { return }  # already there
    Log "Adding Tesseract to system PATH: $dir"
    try {
        $new = ($parts + $dir) -join ';'
        [Environment]::SetEnvironmentVariable("Path", $new, "Machine")
        # Also patch current session so the subsequent --list-langs probe works
        $env:Path = "$env:Path;$dir"
        Ok "Tesseract added to system PATH (existing shells need restart)"
    } catch {
        Warn "Could not modify system PATH (need admin?): $_"
    }
}

function Install-Tesseract {
    $exe = Find-TesseractExe
    if ($exe) {
        # 已裝（透過 winget / installer / 之前的 install.ps1），補強檢查 chi_tra
        Add-TesseractToPath
        Ensure-TesseractChiTra
        if (Test-Tesseract) { Ok "tesseract + chi_tra already installed"; return }
    }
    Log "Installing tesseract OCR (soft optional; pdf-editor text recovery)..."
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            # UB-Mannheim 套件 silent install 可能不含 chi_tra，下面 Ensure 會補
            $proc = Start-Process winget -ArgumentList "install --id UB-Mannheim.TesseractOCR -e --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow -ErrorAction SilentlyContinue
            if ($proc.ExitCode -eq 0) {
                Add-TesseractToPath
                Ensure-TesseractChiTra  # 不論結果，都嘗試補 chi_tra
                if (Test-Tesseract) {
                    Ok "tesseract installed via winget"
                    return
                }
            }
        }
        Warn "tesseract auto-install failed - pdf-editor OCR feature will be disabled"
        Warn "  To enable later: download from https://github.com/UB-Mannheim/tesseract/wiki"
    } catch {
        Warn "tesseract install error: $_  (continuing - OCR is optional)"
    }
}

# uv
function Ensure-VCRedist {
    # PyTorch 2.x（EasyOCR 主依賴）需要 Visual C++ Redistributable 2015-2022
    # (14.40+)。沒裝會 c10.dll load failure (WinError 1114)，EasyOCR 完全
    # 載不起來，OCR 會 silent fallback 到 tesseract。
    #
    # 即使 vc_redist installer 回 exit 3010 (suggests reboot)，PyTorch 仍可
    # 在「之後新 spawn 的 process」載入 — 我們不 prompt user 重啟，後續
    # uv sync + service restart 都是新 process，會抓到新 DLL。
    Log "Checking Visual C++ Redistributable (PyTorch dep) ..."
    $key  = "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64"
    $key2 = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X64"
    $current = ""
    foreach ($k in @($key, $key2)) {
        if (Test-Path $k) {
            try { $v = (Get-ItemProperty $k).Version; if ($v) { $current = $v; break } } catch {}
        }
    }
    # 解析版本：v14.40+ 才符合現代 PyTorch；之前的（如 v14.0.23026 = 2015 RTM）不行
    $needsInstall = $true
    if ($current -match '^v?(\d+)\.(\d+)') {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -gt 14 -or ($major -eq 14 -and $minor -ge 40)) {
            $needsInstall = $false
            Ok "Visual C++ Redistributable already current ($current)"
        } else {
            Log "Visual C++ Redistributable is old ($current) — upgrading to latest"
        }
    } else {
        Log "Visual C++ Redistributable not found — installing latest"
    }
    if (-not $needsInstall) { return }

    $vc = Join-Path $env:TEMP "jtdt-vc_redist.x64.exe"
    if (Test-Path $vc) { Remove-Item $vc -Force -ErrorAction SilentlyContinue }
    try {
        Log "Downloading Microsoft Visual C++ Redistributable (~25 MB) ..."
        Invoke-WebRequest -Uri "https://aka.ms/vs/17/release/vc_redist.x64.exe" `
            -OutFile $vc -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
        Log "Installing (silent, no reboot) ..."
        $proc = Start-Process -FilePath $vc -ArgumentList "/install","/quiet","/norestart" `
            -Wait -PassThru -ErrorAction Stop
        if ($proc.ExitCode -eq 0) {
            Ok "Visual C++ Redistributable installed"
        } elseif ($proc.ExitCode -eq 3010) {
            # 3010 = success but reboot recommended.PyTorch 仍可在新 process load
            Ok "Visual C++ Redistributable installed (exit 3010 — 新 process 可正常 load，不需重啟)"
        } else {
            Warn "vc_redist exit $($proc.ExitCode) — EasyOCR 可能載不起，OCR 會 fallback tesseract"
        }
    } catch {
        Warn "vc_redist 下載 / 安裝失敗：$_"
        Warn "  EasyOCR 將無法載入；OCR 會自動 fallback tesseract（CJK 識別率較弱）"
        Warn "  手動補裝：開啟 https://aka.ms/vs/17/release/vc_redist.x64.exe 安裝"
    }
}


function Install-Uv {
    if (Test-Path $UvExe) { Ok "uv already present"; return }
    Log "Downloading uv ..."
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $env:UV_INSTALL_DIR = $BinDir
    $env:UV_NO_MODIFY_PATH = '1'
    # Note: astral.sh/uv/install.ps1 serves application/octet-stream;
    # PS 5.1 iwr.Content returns byte[] which iex cannot consume.
    # irm (Invoke-RestMethod) auto-decodes UTF-8 to string.
    Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1')
    if (-not (Test-Path $UvExe)) { Die "uv install failed" }
    Ok "uv installed at $UvExe"
}

# WinSW (Windows Service Wrapper) — v1.4.44 起取代 NSSM
#
# 為什麼換掉 NSSM？
#   - NSSM 2.24 是 2014 年最後一版，10 年無更新
#   - GitHub issues #1 / #3 反映 nssm.cc 不時 503 / 404
#   - 部分企業 AV 把 NSSM 標 PUA
#
# WinSW 優勢：
#   - GitHub 託管下載（穩定）+ 活躍維護（v2.12 是 2024 年版本）
#   - MIT 授權清楚
#   - Jenkins 等大型專案使用，AV 信任度高
#   - 配置由 XML 檔（更直觀）
#
# 安全保證跟 NSSM 一樣：
#   1. SHA256 寫死在 $WinswBundledSha256，被改過拒絕使用
#   2. 萬一 bundled 不見 / hash 不符 / AV 隔離，退到 GitHub Release 網路下載
#
# WinSW 命名規則：service exe 與 XML 設定檔須同名（去掉副檔名）。
# 所以 winsw.exe 重命名為 jtdt-svc.exe，配 jtdt-svc.xml 一起放在 bin/。
$WinswBundledSha256 = 'b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f'
$WinswReleaseUrl = 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe'

function Install-Winsw {
    if (Test-Path $WinswExe) { Ok "WinSW already present at $WinswExe"; return }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $bundled = Join-Path $InstallDir 'packaging\windows\winsw.exe'
    if (Test-Path $bundled) {
        $actualHash = (Get-FileHash -Path $bundled -Algorithm SHA256).Hash.ToLower()
        if ($actualHash -ne $WinswBundledSha256) {
            Warn "Bundled winsw.exe SHA256 mismatch - expected $WinswBundledSha256 got $actualHash"
            Warn "Possibly tampered repo or AV quarantine; falling back to network download"
        } else {
            Log "Using bundled winsw.exe ($([int]((Get-Item $bundled).Length / 1KB)) KB, SHA256 verified)"
            Copy-Item -Path $bundled -Destination $WinswExe -Force
            Ok "WinSW installed at $WinswExe (bundled, signature verified)"
            return
        }
    }
    Warn "Bundled winsw.exe not found at $bundled - falling back to GitHub Release"
    Log "Downloading WinSW (Windows Service wrapper) from $WinswReleaseUrl ..."
    for ($i = 0; $i -lt 3; $i++) {
        try {
            Invoke-WebRequest -Uri $WinswReleaseUrl -OutFile $WinswExe `
                -UseBasicParsing -TimeoutSec 20 -ErrorAction Stop
            $actualHash = (Get-FileHash -Path $WinswExe -Algorithm SHA256).Hash.ToLower()
            if ($actualHash -ne $WinswBundledSha256) {
                Remove-Item $WinswExe -Force -ErrorAction SilentlyContinue
                throw "Downloaded winsw.exe SHA256 mismatch (expected $WinswBundledSha256, got $actualHash)"
            }
            Ok "WinSW downloaded and SHA256 verified"
            return
        } catch {
            Warn "  Attempt $($i+1) failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
            Start-Sleep -Seconds 3
        }
    }
    $msg = "WinSW download failed (GitHub unreachable + bundled copy missing).`n" +
           "Please install manually:`n" +
           "  1. Download from $WinswReleaseUrl`n" +
           "  2. Save as $WinswExe`n" +
           "  3. Re-run this installer."
    Die $msg
    Ok "nssm installed at $NssmExe (from network)"
}

# Source code
function Install-Git {
    # Try to install Git if missing — git mode is required for `jtdt update`
    # to work later. Falling through to tarball mode leaves customers stuck
    # ("not a git repo, can't git pull") and they have to manually install
    # git + re-run the installer. Better to install upfront.
    # Soft-fail: if winget can't install git, fall through to tarball mode.
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Ok "git already installed"
        return
    }
    Log "git not found; trying to install via winget (required for jtdt update)..."
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Warn "winget not available; jtdt update will require manual git install later"
        Warn "  Manual: https://git-scm.com/download/win  then re-run this installer"
        return
    }
    try {
        $proc = Start-Process winget `
            -ArgumentList "install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements" `
            -Wait -PassThru -NoNewWindow -ErrorAction SilentlyContinue
        if ($proc.ExitCode -eq 0) {
            # Refresh PATH so git is visible in this session (winget added
            # it to system PATH but current shell hasn't reloaded).
            $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                        [System.Environment]::GetEnvironmentVariable('Path', 'User')
            if (Get-Command git -ErrorAction SilentlyContinue) {
                Ok "git installed via winget"
                return
            }
        }
        Warn "git winget install completed but git command still not found"
    } catch {
        Warn "git install via winget failed: $_"
    }
    Warn "Falling through — installer will use tarball mode; jtdt update will not work until git is manually installed"
}


function Fetch-Code {
    if (Test-Path (Join-Path $InstallDir '.git')) {
        Log "Existing install detected, updating via git ..."
        Push-Location $InstallDir
        try {
            git fetch --depth=1 origin $RepoBranch
            if ($LASTEXITCODE -ne 0) { Die "git fetch failed" }
            git reset --hard "origin/$RepoBranch"
            if ($LASTEXITCODE -ne 0) { Die "git reset failed" }
        } finally { Pop-Location }
        return
    }
    # InstallDir may already exist because Install-Uv / Install-Winsw just put
    # bin/ in there. We keep bin/ (uv + winsw + legacy nssm) and wipe everything else, then
    # clone into a temp dir and copy contents in. We can't clone directly
    # into $InstallDir because git refuses non-empty destinations.
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    # IMPORTANT: stop the service first if it's running. Otherwise
    # `.venv\Scripts\python.exe` is held open by the running process and
    # `Remove-Item` silently fails (we use SilentlyContinue), leaving a
    # corrupted `.venv` that uv sync won't recreate. End result is no real
    # venv, no ldap3, jt-doc-tools registers as old version. v1.1.66~v1.1.69 bug.
    $svc = Get-Service jt-doc-tools -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq 'Running') {
        Log "Stopping running service before refreshing files ..."
        Stop-Service jt-doc-tools -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Warn "$InstallDir is not a git repo; cleaning non-bin files (keeping bin/uv.exe, bin/jtdt-svc.exe, and bin/nssm.exe if present) ..."
    Get-ChildItem $InstallDir -Force |
        Where-Object { $_.Name -ne 'bin' } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    # Verify .venv actually went away — if not, the service stop above didn't
    # release file handles in time, and uv sync will see "broken venv" and
    # never regenerate it. Fail loud instead of silently producing a half-baked install.
    if (Test-Path "$InstallDir\.venv") {
        Warn ".venv still present after cleanup; force-removing again ..."
        Start-Sleep -Seconds 2
        Remove-Item "$InstallDir\.venv" -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path "$InstallDir\.venv") {
            Die ".venv could not be removed (file locked). Run: Stop-Service jt-doc-tools -Force; then re-run installer."
        }
    }

    $tmpSrc = Join-Path $env:TEMP "jtdt-src-$(Get-Date -Format yyyyMMddHHmmss)"
    if (Test-Path $tmpSrc) {
        Remove-Item $tmpSrc -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (Get-Command git -ErrorAction SilentlyContinue) {
        Log "Cloning code from $RepoUrl to temporary directory ..."
        git clone --depth=1 --branch $RepoBranch $RepoUrl $tmpSrc
        if ($LASTEXITCODE -ne 0) { Die "git clone failed" }
        Log "Copying source code to $InstallDir ..."
        Get-ChildItem $tmpSrc -Force |
            Copy-Item -Destination $InstallDir -Recurse -Force
        Remove-Item $tmpSrc -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Log "git not installed, falling back to tarball download ..."
        $tmp = Join-Path $env:TEMP "jtdt-src.zip"
        $extractDir = Join-Path $env:TEMP "jtdt-extract"
        if (Test-Path $tmp)        { Remove-Item $tmp        -Force         -ErrorAction SilentlyContinue }
        if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue }
        Invoke-WebRequest -Uri "$RepoUrl/archive/refs/heads/$RepoBranch.zip" -OutFile $tmp
        Expand-Archive -Path $tmp -DestinationPath $extractDir -Force
        $first = Get-ChildItem $extractDir -Directory | Select-Object -First 1
        Copy-Item "$($first.FullName)\*" $InstallDir -Recurse -Force
        Remove-Item $tmp, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path (Join-Path $InstallDir 'pyproject.toml'))) {
        Die "Source fetch failed: pyproject.toml not found in $InstallDir"
    }
    Ok "Source code ready"
}

function Setup-Python {
    Log "Setting up isolated Python environment (uv sync) ..."
    # Force uv to use its own managed Python; avoid Microsoft Store python.exe stub
    # (the Store stub is not a real Python, it pops the Store and uv crashes).
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    # 完全跳脫 PowerShell：把 venv 建立 / uv sync / smoke test 全寫成純
    # cmd 批次檔（github/setup-python.cmd），這裡只負責 cmd /c 呼叫它。
    # PowerShell 的 native-command 處理在 elevated session + *>&1 redirect
    # 環境下有太多怪行為（Args 自動變數、Out-Host 吞輸出、Stop 把 stderr
    # 當 fatal 等等），不如直接交給 cmd 跑。
    # 明確走 script scope 取 InstallDir（function scope 在某些路徑下取不到）
    $myInstallDir = $Script:InstallDir
    if (-not $myInstallDir) { $myInstallDir = $InstallDir }
    Write-Output "[debug] InstallDir=$myInstallDir"
    $setupBat = Join-Path $myInstallDir 'setup-python.cmd'
    Write-Output "[debug] setupBat=$setupBat"
    if (-not (Test-Path $setupBat)) {
        Die "setup-python.cmd not found at $setupBat (run install.sh / install.ps1 again to fetch latest source)"
    }
    cmd /c "`"$setupBat`" `"$myInstallDir`" 2>&1" | ForEach-Object { Write-Output $_ }
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        switch ($rc) {
            2 { Die "uv venv failed" }
            3 { Die "uv sync failed" }
            4 { Die "Critical import smoke test failed - install incomplete (deps not actually installed)" }
            default { Die "Setup-Python failed (exit $rc)" }
        }
    }
    Ok "Python environment ready: $myInstallDir\.venv"
}

# Data
function Prepare-Data {
    Log "Preparing data directory $DataDir ..."
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    New-Item -ItemType Directory -Force -Path $LogDir  | Out-Null
    $seed = Join-Path $InstallDir 'data'
    if ((Test-Path $seed) -and (-not (Get-ChildItem $DataDir -Force -ErrorAction SilentlyContinue))) {
        Copy-Item "$seed\*" $DataDir -Recurse -Force
    }
}

function Write-WinswXml {
    # NB: parameter name MUST NOT be $Host — that's a PowerShell automatic
    # variable (read-only) and reassigning it triggers VariableNotWritable.
    param(
        [string]$BindHost = '127.0.0.1',
        [int]$Port = 8765
    )
    $py = Join-Path $InstallDir '.venv\Scripts\python.exe'
    # XML 安全：所有路徑用變數插值；不含使用者輸入。Description / id 不允許特殊字元
    $xml = @'
<service>
  <id>{0}</id>
  <name>Jason Tools Document Toolbox</name>
  <description>Jason Tools Document Toolbox - PDF / Office processing</description>
  <executable>{1}</executable>
  <arguments>-m app.main</arguments>
  <workingdirectory>{2}</workingdirectory>
  <log mode="roll-by-size">
    <sizeThreshold>5120</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
  <logpath>{3}</logpath>
  <env name="JTDT_DATA_DIR" value="{4}"/>
  <env name="JTDT_HOST" value="{5}"/>
  <env name="JTDT_PORT" value="{6}"/>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="20 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <startmode>Automatic</startmode>
</service>
'@ -f $ServiceName, $py, $InstallDir, $LogDir, $DataDir, $BindHost, $Port
    Set-Content -Path $WinswXml -Value $xml -Encoding UTF8
}

# Service
function Install-Service {
    Log "Installing Windows Service (via WinSW) ..."
    # Detect & remove pre-existing service. Could be:
    #   (a) old NSSM-wrapped install — use nssm.exe to remove
    #   (b) old WinSW install — use winsw uninstall (sc.exe delete works too)
    #   (c) sc.exe-only install (legacy) — sc.exe delete
    # All three respond to `sc.exe stop` for stop, and `sc.exe delete` cleans up
    # the SCM record. NSSM-specific cleanup also drops registry params.
    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Log "  Existing service detected, stopping & removing ..."
        & sc.exe stop $ServiceName 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        if (Test-Path $NssmExe) {
            # Old NSSM install — use proper remove (cleans up registry env vars)
            Log "  Removing old NSSM-wrapped service ..."
            & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null
        } else {
            & sc.exe delete $ServiceName 2>&1 | Out-Null
        }
        Start-Sleep -Seconds 1
    }
    # Old NSSM binary cleanup — keep nssm.exe ONLY if user might still need it
    # for diagnosis; we'll remove it after WinSW is verified working.
    if (Test-Path $NssmExe) {
        Log "  Old nssm.exe present at $NssmExe - will remove after WinSW verified"
    }
    Write-WinswXml -BindHost '127.0.0.1' -Port 8765
    Log "  Generated WinSW XML config: $WinswXml"
    & $WinswExe install 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Die "WinSW install failed (exit $LASTEXITCODE). Check $LogDir for details."
    }
    & $WinswExe start 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc -or $svc.Status -ne 'Running') {
        Die "WinSW start failed - service not in Running state. Check $LogDir\$ServiceName.wrapper.log."
    }
    # Now safe to remove old nssm.exe
    if (Test-Path $NssmExe) {
        try {
            Remove-Item $NssmExe -Force -ErrorAction Stop
            Log "  Cleaned up old nssm.exe"
        } catch {
            Warn "  Failed to remove $NssmExe (in use?): $($_.Exception.Message)"
        }
    }
    Ok "Windows Service '$ServiceName' installed and started via WinSW, autostart enabled"
}

# jtdt CLI shim
function Install-Cli {
    Log "Creating jtdt command ..."
    $py = Join-Path $InstallDir '.venv\Scripts\python.exe'
    $content = @"
@echo off
"$py" -m app.cli %*
"@
    Set-Content -Path $CliShim -Value $content -Encoding ASCII
    # Add to system PATH (machine scope, requires admin)
    $sysPath = [Environment]::GetEnvironmentVariable('Path','Machine')
    if ($sysPath -notmatch [regex]::Escape($InstallDir)) {
        [Environment]::SetEnvironmentVariable('Path', "$sysPath;$InstallDir", 'Machine')
        Ok "Added to system PATH (open a new terminal for it to take effect)"
    }
    Ok "jtdt command: $CliShim"
}

# Health check
function Health-Check {
    Log "Waiting for service to come up ..."
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8765/healthz' -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) {
                Ok "Service online: http://127.0.0.1:8765/"
                return
            }
        } catch {}
        Start-Sleep -Seconds 1
    }
    Warn "Health check did not pass within 30s. Run: jtdt logs"
}

# Main
Write-Host ""
Log "Jason Tools Document Toolbox - Windows installer"
Log "Platform: Windows ($Arch)"
Log "Program:  $InstallDir"
Log "Data:     $DataDir"
Write-Host ""

Ensure-Office
Install-Tesseract
Install-Git
Install-Uv
Fetch-Code
# IMPORTANT: Install-Winsw 必須在 Fetch-Code 之後 — 我們把 winsw.exe bundle 在 repo
# 內 (github/packaging/windows/winsw.exe)，所以 git clone 完才有 binary 可用。
# 不再依賴 nssm.cc 之類不穩定的下載源；fallback 也走 GitHub Release。
Install-Winsw
# v1.7.2: PyTorch (EasyOCR 主依賴) 需要新版 Visual C++ Redistributable，
# 必須在 Setup-Python (uv sync) 之前裝好，不然 uv 雖會把 torch wheel 下載
# 安裝，但執行時 c10.dll 會 fail to load。
Ensure-VCRedist
Setup-Python
Prepare-Data
Install-Service
Install-Cli
Health-Check

Write-Host ""
Ok "Install complete!"
Write-Host ""
Write-Host "  Web UI:    http://127.0.0.1:8765/"
Write-Host "  Status:    jtdt status"
Write-Host "  Logs:      jtdt logs -f"
Write-Host "  Update:    jtdt update     (run PowerShell as Administrator)"
Write-Host "  Uninstall: jtdt uninstall  (--purge to also remove user data)"
Write-Host ""

# Auto-open browser to UI
try {
    Start-Process 'http://127.0.0.1:8765/'
    Ok "Browser opened automatically"
} catch {
    Warn "Could not open browser automatically. Visit: http://127.0.0.1:8765/"
}

# Keep window open
Write-Host ""
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Press Enter to close this window"                  -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
try { Read-Host | Out-Null } catch { Start-Sleep -Seconds 30 }
Write-Host ""

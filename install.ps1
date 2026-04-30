$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

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
$NssmExe    = Join-Path $BinDir 'nssm.exe'
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

# uv
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

# NSSM (Windows Service wrapper)
function Install-Nssm {
    if (Test-Path $NssmExe) { Ok "nssm already present"; return }
    Log "Downloading NSSM (Windows Service wrapper) ..."
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $tmp = Join-Path $env:TEMP "nssm.zip"
    # NSSM 2.24 is the last stable release. nssm.cc returns 503 intermittently, try mirrors.
    $urls = @(
        'https://nssm.cc/release/nssm-2.24.zip',
        'https://web.archive.org/web/2024/https://nssm.cc/release/nssm-2.24.zip',
        'https://github.com/jasoncheng7115/jt-doc-tools/releases/download/deps/nssm-2.24.zip'
    )
    $ok = $false
    foreach ($url in $urls) {
        Log "  Trying $url"
        for ($i = 0; $i -lt 3; $i++) {
            try {
                (New-Object Net.WebClient).DownloadFile($url, $tmp)
                if ((Get-Item $tmp).Length -gt 100000) { $ok = $true; break }
            } catch {
                Warn "    Attempt $($i+1) failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
                Start-Sleep -Seconds 2
            }
        }
        if ($ok) { Ok "  Download succeeded"; break }
    }
    if (-not $ok) { Die "NSSM download failed (all mirrors unreachable). Retry later, or place nssm-2.24.zip at $tmp manually and re-run." }
    $extractDir = Join-Path $env:TEMP "nssm-extract"
    if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
    Expand-Archive -Path $tmp -DestinationPath $extractDir -Force
    Copy-Item -Path (Join-Path $extractDir 'nssm-2.24\win64\nssm.exe') -Destination $NssmExe -Force
    Remove-Item $tmp, $extractDir -Recurse -Force -ErrorAction SilentlyContinue
    Ok "nssm installed at $NssmExe"
}

# Source code
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
    # InstallDir may already exist because Install-Uv / Install-Nssm just put
    # bin/ in there. We keep bin/ (uv + nssm) and wipe everything else, then
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
    Warn "$InstallDir is not a git repo; cleaning non-bin files (keeping bin/uv.exe and bin/nssm.exe) ..."
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
    Push-Location $InstallDir
    # 重要：暫時把 $ErrorActionPreference 從 'Stop' 改成 'Continue'。
    # 因為 uv 對「Python 3.12 已裝」的提示是寫到 stderr，搭配 'Stop' 會被當成
    # terminating error 直接讓 install.ps1 在 setup_python 第一行就死掉、無 log。
    # 同樣地 uv 在拉 packages 時也會大量寫進度訊息到 stderr。
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        # 不要 pipe (`| ForEach`) 也不用 2>&1 redirect — 兩者都可能讓 uv 偵測到
        # non-tty 後行為改變，導致 .venv 沒被建起來。直接呼叫，外層的
        # `*>&1 | Out-File` 會捕捉所有輸出（含 stderr）。
        & $UvExe python install 3.12
        # uv python install 對「已裝過」可能回 exit 1，這不是真失敗 — 不 Die
        # NEVER use --frozen — 會盲信 uv.lock；缺 dep（如 v1.1.66 之前漏 ldap3）
        # 仍「成功」但實際少裝 package。一律完整 reconcile。
        & $UvExe sync --python 3.12
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEAP
            Die "uv sync failed (exit $LASTEXITCODE)"
        }
    } finally {
        Pop-Location
        $ErrorActionPreference = $prevEAP
    }
    $venvPython = Join-Path $InstallDir '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPython)) {
        Die "Python venv creation failed"
    }
    Log "Verifying critical imports ..."
    & $venvPython -c "import fastapi, fitz, ldap3, PIL, pdfplumber, docx, odf, pyzipper, httpx"
    if ($LASTEXITCODE -ne 0) {
        Die "Critical import smoke test failed - install incomplete"
    }
    Ok "Python environment ready: $InstallDir\.venv"
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

# Service
function Install-Service {
    Log "Installing Windows Service (via NSSM) ..."
    # Remove old service if exists
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
    & $NssmExe set $ServiceName Description "Jason Tools Document Toolbox - PDF / Office processing" | Out-Null
    & $NssmExe start $ServiceName | Out-Null
    Ok "Windows Service '$ServiceName' installed and started, autostart enabled"
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
Install-Uv
Install-Nssm
Fetch-Code
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

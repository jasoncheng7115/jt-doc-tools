# =====================================================================
#  install_core.ps1  --  jt-doc-tools Windows GUI installer core logic
# ---------------------------------------------------------------------
#  This is a STANDALONE, NON-INTERACTIVE installer core invoked by the
#  NSIS GUI installer (installer.nsi). It is intentionally SEPARATE from
#  the command-line install.ps1 (curl|iex one-liner) so that the two
#  install paths evolve independently and changes here can never destabilise
#  the existing one-liner flow.
#
#  Differences vs install.ps1:
#    * Parameter driven (NSIS passes InstallDir / component switches)
#    * No interactive prompts (no Read-Host, no "Press Enter") -- NSIS runs
#      it headless and captures stdout into the wizard details pane.
#    * Optional components (OCR / Office / Service / Firewall) are gated by
#      switches so the user's component-page choices are honoured.
#    * Non-zero exit code on fatal error so NSIS can detect failure.
#
#  Exit codes:
#    0  success
#    10 admin privileges missing
#    11 no internet
#    12 unsupported arch (32-bit)
#    20 uv install failed
#    21 source fetch failed
#    22 python env setup failed
#    23 winsw install failed
#    24 service start failed
# =====================================================================

param(
    [string]$InstallDir    = (Join-Path ${env:ProgramFiles} 'jt-doc-tools'),
    [string]$DataDir       = (Join-Path (Join-Path ${env:ProgramData} 'jt-doc-tools') 'Data'),
    [string]$BindHost      = '127.0.0.1',
    [int]   $Port          = 8765,
    [switch]$InstallOcr,        # PyTorch/EasyOCR VC++ redist + tesseract chi_tra
    [switch]$InstallOffice,     # OxOffice / LibreOffice document conversion
    [switch]$InstallService,    # register WinSW Windows service (autostart)
    [switch]$InstallFirewall,   # allow LAN access (binds 0.0.0.0 + firewall rule)
    [string]$RepoUrl       = 'https://github.com/jasoncheng7115/jt-doc-tools',
    [string]$RepoBranch    = 'main'
)

# Continue on native-command stderr; we judge native failures by $LASTEXITCODE
# and cmdlet failures via try/catch -- same rationale as install.ps1.
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

# Allow env override for pre-release testing against a local file:// mirror.
if ($env:JTDT_REPO_URL)    { $RepoUrl    = $env:JTDT_REPO_URL }
if ($env:JTDT_REPO_BRANCH) { $RepoBranch = $env:JTDT_REPO_BRANCH }

$ServiceName = 'jt-doc-tools'
$ProgData    = ${env:ProgramData}
$LogDir      = Join-Path (Join-Path $ProgData 'jt-doc-tools') 'Logs'
$BinDir      = Join-Path $InstallDir 'bin'
$NssmExe     = Join-Path $BinDir 'nssm.exe'        # legacy migration detection
$WinswExe    = Join-Path $BinDir 'jtdt-svc.exe'    # must match XML basename
$WinswXml    = Join-Path $BinDir 'jtdt-svc.xml'
$UvExe       = Join-Path $BinDir 'uv.exe'
$CliShim     = Join-Path $InstallDir 'jtdt.cmd'

# --- logging (stdout for NSIS details pane + a log file) -------------
$null = New-Item -ItemType Directory -Force -Path $LogDir -ErrorAction SilentlyContinue
$InstallLog = Join-Path $LogDir 'installer.log'
function _w($pfx, $m, $col) {
    $line = "$pfx $m"
    Write-Host $line -ForegroundColor $col
    try { Add-Content -Path $InstallLog -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
}
function Log  ($m) { _w '==>'  $m 'Cyan'   }
function Ok   ($m) { _w '[OK]' $m 'Green'  }
function Warn ($m) { _w '[!] ' $m 'Yellow' }
function Die  ($m, $code) {
    _w '[X] ' $m 'Red'
    exit $code
}

# --- preflight ------------------------------------------------------
$ident = [Security.Principal.WindowsIdentity]::GetCurrent()
$prin  = New-Object Security.Principal.WindowsPrincipal($ident)
if (-not $prin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die 'Administrator privileges required.' 10
}

if (-not [Environment]::Is64BitOperatingSystem) { Die '32-bit Windows is not supported.' 12 }
# Report the real machine arch. NOTE: this script may be launched by the 32-bit
# NSIS installer, so prefer PROCESSOR_ARCHITEW6432 (the 64-bit arch seen from a
# WOW64 process) and fall back to PROCESSOR_ARCHITECTURE. Covers both AMD64 (x64)
# and ARM64. uv selects the matching managed Python automatically.
$Arch = $env:PROCESSOR_ARCHITEW6432
if (-not $Arch) { $Arch = $env:PROCESSOR_ARCHITECTURE }
if (-not $Arch) { $Arch = 'x86_64' }
$IsArm64 = ($Arch -match 'ARM64')

# Real 64-bit "Program Files" -- $env:ProgramFiles resolves to the x86 path when
# this script is spawned by the 32-bit NSIS installer, so use ProgramW6432 which
# always points to the native 64-bit Program Files (x64 and ARM64 alike).
$PF64 = $env:ProgramW6432
if (-not $PF64) { $PF64 = $env:ProgramFiles }
$PFx86 = ${env:ProgramFiles(x86)}

function Test-Internet {
    foreach ($h in @('github.com', 'cdn.jsdelivr.net', 'astral.sh')) {
        try {
            $r = Invoke-WebRequest -Uri "https://$h" -Method Head -UseBasicParsing -TimeoutSec 8 -ErrorAction Stop
            if ($r.StatusCode -ge 200) { return $true }
        } catch {}
    }
    return $false
}
Log 'Checking network ...'
if (-not (Test-Internet)) {
    Die 'Cannot reach the internet (github.com / cdn.jsdelivr.net / astral.sh). Check VPN / firewall / DNS and retry.' 11
}
Ok 'Network reachable'

Log "Jason Tools Document Toolbox - GUI installer core"
Log "Platform:  Windows ($Arch)"
Log "Program:   $InstallDir"
Log "Data:      $DataDir"
Log "Bind:      $BindHost`:$Port"
Log ("Components: OCR={0} Office={1} Service={2} Firewall={3}" -f `
        [bool]$InstallOcr, [bool]$InstallOffice, [bool]$InstallService, [bool]$InstallFirewall)

# =====================================================================
#  Office (optional component)
# =====================================================================
function Test-Office {
    # Use $PF64 (real 64-bit Program Files) -- $env:ProgramFiles would be the x86
    # path here because NSIS spawns this script as a WOW64 child.
    $paths = @(
        "$PF64\OxOffice\program\soffice.exe",
        "$PF64\LibreOffice\program\soffice.exe",
        "$PFx86\OxOffice\program\soffice.exe",
        "$PFx86\LibreOffice\program\soffice.exe"
    )
    foreach ($p in $paths) { if ($p -and (Test-Path $p)) { return $true } }
    if (Get-Command soffice.exe -ErrorAction SilentlyContinue) { return $true }
    return $false
}
function Install-OxOffice {
    Log 'Trying OxOffice from GitHub release ...'
    try {
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/OSSII/OxOffice/releases/latest' -Headers @{ 'User-Agent' = 'jt-doc-tools-installer' }
        $asset = $rel.assets | Where-Object { $_.name -match '\.msi$' -and ($_.name -match 'win|Windows|x64') } | Select-Object -First 1
        if (-not $asset) { Warn 'No Windows MSI asset found for OxOffice'; return $false }
        $tmp = Join-Path $env:TEMP "oxoffice-install.msi"
        Log "Downloading $($asset.browser_download_url)"
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp -UseBasicParsing
        Log 'Installing OxOffice (silent) ...'
        $proc = Start-Process msiexec.exe -ArgumentList "/i `"$tmp`" /qn /norestart" -Wait -PassThru
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        if ($proc.ExitCode -ne 0) { Warn "OxOffice MSI exit code $($proc.ExitCode)"; return $false }
        return Test-Office
    } catch { Warn "OxOffice install failed: $_"; return $false }
}
function Install-LibreOffice {
    Log 'Falling back to LibreOffice via winget ...'
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $proc = Start-Process winget -ArgumentList 'install --id TheDocumentFoundation.LibreOffice -e --silent --accept-package-agreements --accept-source-agreements' -Wait -PassThru -NoNewWindow
            if ($proc.ExitCode -eq 0) { return Test-Office }
        }
        return $false
    } catch { Warn "LibreOffice install failed: $_"; return $false }
}
function Ensure-Office {
    if (Test-Office) { Ok 'Office engine detected'; return }
    Log 'No OxOffice / LibreOffice detected'
    if (Install-OxOffice)    { Ok 'OxOffice installed';    return }
    if (Install-LibreOffice) { Ok 'LibreOffice installed'; return }
    # Non-fatal: document conversion tools degrade, the rest works.
    Warn 'Office engine could not be installed automatically.'
    Warn '  Install later from https://github.com/OSSII/OxOffice/releases and re-run.'
}

# =====================================================================
#  Tesseract OCR + chi_tra (optional, part of OCR component)
# =====================================================================
function Find-TesseractExe {
    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Path }
    foreach ($c in @(
        "C:\Program Files\Tesseract-OCR\tesseract.exe",
        "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe")) {
        if (Test-Path $c) { return $c }
    }
    return ''
}
function Test-Tesseract {
    $exe = Find-TesseractExe
    if (-not $exe) { return $false }
    $langs = & $exe --list-langs 2>&1 | Out-String
    return ($langs -match 'chi_tra')
}
function Add-TesseractToPath {
    $exe = Find-TesseractExe
    if (-not $exe) { return }
    $dir = Split-Path -Parent $exe
    $cur = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $parts = $cur -split ';' | Where-Object { $_ -ne '' }
    if ($parts -contains $dir) { return }
    try {
        [Environment]::SetEnvironmentVariable('Path', (($parts + $dir) -join ';'), 'Machine')
        $env:Path = "$env:Path;$dir"
        Ok "Tesseract added to system PATH ($dir)"
    } catch { Warn "Could not modify system PATH: $_" }
}
function Ensure-TesseractChiTra {
    $exe = Find-TesseractExe
    if (-not $exe) { return }
    $langs = & $exe --list-langs 2>&1 | Out-String
    if ($langs -match 'chi_tra') { return }
    $tessdataDir = Join-Path (Split-Path -Parent $exe) 'tessdata'
    if (-not (Test-Path $tessdataDir)) { Warn "tessdata dir not found, skip chi_tra"; return }
    $url = 'https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_tra.traineddata'
    $dst = Join-Path $tessdataDir 'chi_tra.traineddata'
    Log 'Downloading chi_tra.traineddata (~12MB) for Chinese OCR ...'
    try {
        Invoke-WebRequest -Uri $url -OutFile $dst -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
        if ((Test-Path $dst) -and (Get-Item $dst).Length -gt 1000000) {
            Ok "chi_tra.traineddata downloaded ($([math]::Round((Get-Item $dst).Length/1MB,1)) MB)"
        } else {
            Remove-Item $dst -Force -ErrorAction SilentlyContinue
            Warn 'chi_tra download incomplete, removed'
        }
    } catch { Warn "chi_tra download failed: $_" }
}
function Install-Tesseract {
    $exe = Find-TesseractExe
    if ($exe) {
        Add-TesseractToPath; Ensure-TesseractChiTra
        if (Test-Tesseract) { Ok 'tesseract + chi_tra already installed'; return }
    }
    Log 'Installing tesseract OCR (optional) ...'
    try {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $proc = Start-Process winget -ArgumentList 'install --id UB-Mannheim.TesseractOCR -e --silent --accept-package-agreements --accept-source-agreements' -Wait -PassThru -NoNewWindow -ErrorAction SilentlyContinue
            if ($proc.ExitCode -eq 0) {
                Add-TesseractToPath; Ensure-TesseractChiTra
                if (Test-Tesseract) { Ok 'tesseract installed via winget'; return }
            }
        }
        Warn 'tesseract auto-install failed - OCR text recovery limited (EasyOCR still works)'
    } catch { Warn "tesseract install error: $_ (continuing)" }
}

# =====================================================================
#  Visual C++ Redistributable (PyTorch dep, part of OCR component)
# =====================================================================
function Ensure-VCRedist {
    Log 'Checking Visual C++ Redistributable (PyTorch dep) ...'
    $current = ''
    foreach ($k in @('HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64',
                     'HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X64')) {
        if (Test-Path $k) {
            try { $v = (Get-ItemProperty $k).Version; if ($v) { $current = $v; break } } catch {}
        }
    }
    $needsInstall = $true
    if ($current -match '^v?(\d+)\.(\d+)') {
        $major = [int]$Matches[1]; $minor = [int]$Matches[2]
        if ($major -gt 14 -or ($major -eq 14 -and $minor -ge 40)) {
            $needsInstall = $false; Ok "Visual C++ Redistributable already current ($current)"
        }
    }
    if (-not $needsInstall) { return }
    $vc = Join-Path $env:TEMP 'jtdt-vc_redist.x64.exe'
    if (Test-Path $vc) { Remove-Item $vc -Force -ErrorAction SilentlyContinue }
    try {
        Log 'Downloading Microsoft Visual C++ Redistributable (~25 MB) ...'
        Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vc -UseBasicParsing -TimeoutSec 60 -ErrorAction Stop
        $proc = Start-Process -FilePath $vc -ArgumentList '/install','/quiet','/norestart' -Wait -PassThru -ErrorAction Stop
        if ($proc.ExitCode -eq 0)       { Ok 'Visual C++ Redistributable installed' }
        elseif ($proc.ExitCode -eq 3010){ Ok 'Visual C++ Redistributable installed (reboot suggested; new process loads fine)' }
        else { Warn "vc_redist exit $($proc.ExitCode) - EasyOCR may fall back to tesseract" }
    } catch { Warn "vc_redist download/install failed: $_ (OCR falls back to tesseract)" }
}

# =====================================================================
#  git / uv / source / winsw / python  (always required)
# =====================================================================
function Install-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) { Ok 'git already installed'; return }
    Log 'git not found; trying winget (required for jtdt update) ...'
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Warn 'winget not available; jtdt update needs git installed manually later'; return
    }
    try {
        $proc = Start-Process winget -ArgumentList 'install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements' -Wait -PassThru -NoNewWindow -ErrorAction SilentlyContinue
        if ($proc.ExitCode -eq 0) {
            $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
            if (Get-Command git -ErrorAction SilentlyContinue) { Ok 'git installed via winget'; return }
        }
        Warn 'git winget install finished but git still not found'
    } catch { Warn "git install via winget failed: $_" }
}

function Install-Uv {
    if (Test-Path $UvExe) { Ok 'uv already present'; return }
    Log 'Downloading uv ...'
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $env:UV_INSTALL_DIR = $BinDir
    $env:UV_NO_MODIFY_PATH = '1'
    Invoke-Expression (Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1')
    if (-not (Test-Path $UvExe)) { Die 'uv install failed' 20 }
    Ok "uv installed at $UvExe"
}

$WinswBundledSha256 = 'b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f'
$WinswReleaseUrl    = 'https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW.NET461.exe'
function Install-Winsw {
    if (Test-Path $WinswExe) { Ok "WinSW already present"; return }
    New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
    $bundled = Join-Path $InstallDir 'packaging\windows\winsw.exe'
    if (Test-Path $bundled) {
        $h = (Get-FileHash -Path $bundled -Algorithm SHA256).Hash.ToLower()
        if ($h -eq $WinswBundledSha256) {
            Copy-Item -Path $bundled -Destination $WinswExe -Force
            Ok 'WinSW installed (bundled, SHA256 verified)'; return
        }
        Warn "Bundled winsw.exe SHA256 mismatch; falling back to network download"
    }
    Log "Downloading WinSW from $WinswReleaseUrl ..."
    for ($i = 0; $i -lt 3; $i++) {
        try {
            Invoke-WebRequest -Uri $WinswReleaseUrl -OutFile $WinswExe -UseBasicParsing -TimeoutSec 20 -ErrorAction Stop
            $h = (Get-FileHash -Path $WinswExe -Algorithm SHA256).Hash.ToLower()
            if ($h -ne $WinswBundledSha256) { Remove-Item $WinswExe -Force -ErrorAction SilentlyContinue; throw 'SHA256 mismatch' }
            Ok 'WinSW downloaded and SHA256 verified'; return
        } catch { Warn "  attempt $($i+1) failed: $($_.Exception.Message)"; Start-Sleep -Seconds 3 }
    }
    Die 'WinSW install failed (network + bundled both unavailable).' 23
}

function Fetch-Code {
    if (Test-Path (Join-Path $InstallDir '.git')) {
        Log 'Existing git install detected, updating ...'
        Push-Location $InstallDir
        try {
            git fetch --depth=1 origin $RepoBranch
            if ($LASTEXITCODE -ne 0) { Die 'git fetch failed' 21 }
            git reset --hard "origin/$RepoBranch"
            if ($LASTEXITCODE -ne 0) { Die 'git reset failed' 21 }
        } finally { Pop-Location }
        return
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq 'Running') {
        Log 'Stopping running service before refreshing files ...'
        Stop-Service $ServiceName -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
    }
    Warn "$InstallDir not a git repo; cleaning non-bin files (keeping bin/) ..."
    Get-ChildItem $InstallDir -Force | Where-Object { $_.Name -ne 'bin' } |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path "$InstallDir\.venv") {
        Start-Sleep -Seconds 2
        Remove-Item "$InstallDir\.venv" -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path "$InstallDir\.venv") { Die '.venv locked; stop service and retry.' 21 }
    }
    $tmpSrc = Join-Path $env:TEMP 'jtdt-gui-src'
    if (Test-Path $tmpSrc) { Remove-Item $tmpSrc -Recurse -Force -ErrorAction SilentlyContinue }
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Log "Cloning $RepoUrl ($RepoBranch) ..."
        git clone --depth=1 --branch $RepoBranch $RepoUrl $tmpSrc
        if ($LASTEXITCODE -ne 0) { Die 'git clone failed' 21 }
        Get-ChildItem $tmpSrc -Force | Copy-Item -Destination $InstallDir -Recurse -Force
        Remove-Item $tmpSrc -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Log 'git unavailable, tarball fallback ...'
        $tmp = Join-Path $env:TEMP 'jtdt-src.zip'
        $ext = Join-Path $env:TEMP 'jtdt-extract'
        foreach ($p in @($tmp,$ext)) { if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue } }
        Invoke-WebRequest -Uri "$RepoUrl/archive/refs/heads/$RepoBranch.zip" -OutFile $tmp -UseBasicParsing
        Expand-Archive -Path $tmp -DestinationPath $ext -Force
        $first = Get-ChildItem $ext -Directory | Select-Object -First 1
        Copy-Item "$($first.FullName)\*" $InstallDir -Recurse -Force
        Remove-Item $tmp, $ext -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path (Join-Path $InstallDir 'pyproject.toml'))) { Die 'Source fetch failed: pyproject.toml missing' 21 }
    Ok 'Source code ready'
}

function Setup-Python {
    Log 'Setting up isolated Python environment (uv sync) ...'
    $env:UV_PYTHON_PREFERENCE = 'only-managed'
    $setupBat = Join-Path $InstallDir 'setup-python.cmd'
    if (-not (Test-Path $setupBat)) { Die "setup-python.cmd not found at $setupBat" 22 }
    # 防 LF-only 行尾：無 git 的機器走 tarball 下載原始碼時保留 repo 的 LF 行尾，
    # cmd.exe 執行 LF-only 批次檔會逐 token 噴「不是內部或外部命令」錯誤。執行前
    # 一律強制正規化成 CRLF，無論來源是 git clone 或 tarball 都保證可跑。
    try {
        $rawCmd = [System.IO.File]::ReadAllText($setupBat)
        $crlfCmd = ($rawCmd -replace "`r`n", "`n") -replace "`n", "`r`n"
        if ($crlfCmd -ne $rawCmd) {
            [System.IO.File]::WriteAllText($setupBat, $crlfCmd, (New-Object System.Text.UTF8Encoding($false)))
        }
    } catch {
        Warn "Could not normalize setup-python.cmd line endings: $_"
    }
    cmd /c "`"$setupBat`" `"$InstallDir`" 2>&1" | ForEach-Object { Write-Output $_ }
    switch ($LASTEXITCODE) {
        0 { Ok "Python environment ready: $InstallDir\.venv" }
        2 { Die 'uv venv failed' 22 }
        3 { Die 'uv sync failed' 22 }
        4 { Die 'Critical import smoke test failed - deps not installed' 22 }
        default { Die "Setup-Python failed (exit $LASTEXITCODE)" 22 }
    }
}

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
    param([string]$BindAddr = '127.0.0.1', [int]$SvcPort = 8765)
    $py = Join-Path $InstallDir '.venv\Scripts\python.exe'
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
'@ -f $ServiceName, $py, $InstallDir, $LogDir, $DataDir, $BindAddr, $SvcPort
    Set-Content -Path $WinswXml -Value $xml -Encoding UTF8
}

function Install-Service {
    param([string]$BindAddr = '127.0.0.1', [int]$SvcPort = 8765)
    Log 'Installing Windows Service (via WinSW) ...'
    $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($existing) {
        Log '  Existing service detected, stopping & removing ...'
        & sc.exe stop $ServiceName 2>&1 | Out-Null
        Start-Sleep -Seconds 2
        if (Test-Path $NssmExe) { & $NssmExe remove $ServiceName confirm 2>&1 | Out-Null }
        else { & sc.exe delete $ServiceName 2>&1 | Out-Null }
        Start-Sleep -Seconds 1
    }
    Write-WinswXml -BindAddr $BindAddr -SvcPort $SvcPort
    & $WinswExe install 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Die "WinSW install failed (exit $LASTEXITCODE)." 23 }
    & $WinswExe start 2>&1 | Out-Null
    Start-Sleep -Seconds 2
    $svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $svc -or $svc.Status -ne 'Running') { Die 'WinSW start failed - service not Running.' 24 }
    if (Test-Path $NssmExe) { Remove-Item $NssmExe -Force -ErrorAction SilentlyContinue }
    Ok "Windows Service '$ServiceName' installed and started (autostart)"
}

function Install-Cli {
    Log 'Creating jtdt command ...'
    $py = Join-Path $InstallDir '.venv\Scripts\python.exe'
    Set-Content -Path $CliShim -Value "@echo off`r`n`"$py`" -m app.cli %*" -Encoding ASCII
    $sysPath = [Environment]::GetEnvironmentVariable('Path','Machine')
    if ($sysPath -notmatch [regex]::Escape($InstallDir)) {
        [Environment]::SetEnvironmentVariable('Path', "$sysPath;$InstallDir", 'Machine')
        Ok 'Added to system PATH (new terminal required)'
    }
    Ok "jtdt command: $CliShim"
}

function Install-Firewall {
    param([int]$SvcPort = 8765)
    Log "Adding firewall rule for LAN access (TCP $SvcPort) ..."
    & netsh advfirewall firewall delete rule name="jt-doc-tools" 2>&1 | Out-Null
    & netsh advfirewall firewall add rule name="jt-doc-tools" dir=in action=allow protocol=TCP localport=$SvcPort 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "Firewall inbound rule added (TCP $SvcPort)" }
    else { Warn "Could not add firewall rule (exit $LASTEXITCODE)" }
}

function Health-Check {
    param([int]$SvcPort = 8765)
    Log 'Waiting for service to come up ...'
    for ($i = 0; $i -lt 30; $i++) {
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$SvcPort/healthz" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { Ok "Service online: http://127.0.0.1:$SvcPort/"; return }
        } catch {}
        Start-Sleep -Seconds 1
    }
    Warn 'Health check did not pass within 30s. Run: jtdt logs'
}

# =====================================================================
#  Orchestration
# =====================================================================
# LAN access => bind 0.0.0.0 so other machines on the subnet can connect.
# Otherwise localhost-only (matches install.ps1 default).
$EffectiveBind = if ($InstallFirewall) { '0.0.0.0' } else { '127.0.0.1' }

if ($InstallOffice) { Ensure-Office } else { Log 'Office component skipped (user choice)' }
if ($InstallOcr) {
    if ($IsArm64) {
        Warn 'ARM64 detected: EasyOCR (PyTorch) wheels may be unavailable on Windows ARM64;'
        Warn '  OCR will fall back to tesseract (lighter, CJK accuracy lower). tesseract still installs.'
    }
    Install-Tesseract
} else { Log 'OCR component skipped (user choice)' }
Install-Git
Install-Uv
Fetch-Code
Install-Winsw
if ($InstallOcr)    { Ensure-VCRedist }
Setup-Python
Prepare-Data
if ($InstallService) {
    Install-Service -BindAddr $EffectiveBind -SvcPort $Port
    Install-Cli
    if ($InstallFirewall) { Install-Firewall -SvcPort $Port }
    Health-Check -SvcPort $Port
} else {
    Install-Cli
    Log 'Service component skipped; start manually with: jtdt start'
}

Ok 'Install complete!'
exit 0

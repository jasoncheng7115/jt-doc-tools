@echo off
REM Pure cmd batch script - set up Python venv via uv.
REM Used by install.ps1 to avoid PowerShell native-command quirks under
REM elevated Start-Process + redirect (Args is a reserved var, Out-Host
REM swallows output, EAP=Stop turns stderr into fatal, etc).
REM
REM Usage:  setup-python.cmd ^<install_dir^>
REM Exit codes:
REM   0 = success
REM   2 = uv venv failed
REM   3 = uv sync failed
REM   4 = critical import smoke test failed (deps not really installed)

setlocal enabledelayedexpansion
set "INSTALL_DIR=%~1"
if "%INSTALL_DIR%"=="" (
    echo [ERR] Usage: setup-python.cmd ^<install_dir^>
    exit /b 1
)
set "UV_EXE=%INSTALL_DIR%\bin\uv.exe"
set "VENV_PY=%INSTALL_DIR%\.venv\Scripts\python.exe"

if not exist "%UV_EXE%" (
    echo [ERR] uv not found: %UV_EXE%
    exit /b 1
)

REM 企業 TLS 檢查設備（MITM proxy）會把 HTTPS 憑證換成自家 CA；uv 預設用內建
REM 根憑證不認那個 CA，下載 Python / 套件會失敗。改用作業系統信任庫（企業 CA
REM 通常已裝在那裡）。只設這支 uv 認得的那一個：新版是 UV_SYSTEM_CERTS、舊版是
REM UV_NATIVE_TLS。兩個都設的話，新版 uv 每次都印一行棄用警告。
REM 問不到就退回舊的（舊版一定認得）。呼叫端 / 使用者已設定時不覆寫。
if not defined UV_NATIVE_TLS if not defined UV_SYSTEM_CERTS (
    "%UV_EXE%" sync --help 2>nul | findstr /c:"--system-certs" >nul && (set UV_SYSTEM_CERTS=true) || (set UV_NATIVE_TLS=true)
)
if "%JTDT_TLS_INSECURE%"=="1" (
    if not defined UV_INSECURE_HOST set UV_INSECURE_HOST=pypi.org files.pythonhosted.org github.com objects.githubusercontent.com astral.sh
)

REM uv may exit 1 saying "already installed" when Python 3.12 is present.
REM That's not an error, ignore.
echo ==^> Installing managed Python 3.12 via uv ...
REM Keep the managed Python inside the install dir, not the installing
REM user's %APPDATA%\uv\python: the service runs as SYSTEM, and when that
REM user's profile is removed the venv loses its base interpreter.
REM The venv is rebuilt with --clear below, so a re-run moves old installs too.
set "UV_PYTHON_INSTALL_DIR=%INSTALL_DIR%\python"
set UV_PYTHON_PREFERENCE=only-managed
REM Windows on ARM64: several dependencies (opencv-python-headless, torch) publish
REM no win_arm64 wheels, so `uv sync` fails on a native ARM64 Python (issue #60).
REM Use the x64 Python instead - Windows 11 on ARM runs x64 programs through its
REM built-in emulation, and every dependency has win_amd64 wheels.
REM PROCESSOR_ARCHITEW6432 is set when this runs from a 32-bit process (NSIS).
set "PY_REQ=3.12"
set "HOST_ARCH=%PROCESSOR_ARCHITEW6432%"
if not defined HOST_ARCH set "HOST_ARCH=%PROCESSOR_ARCHITECTURE%"
if /i "%HOST_ARCH%"=="ARM64" (
    set "PY_REQ=cpython-3.12-windows-x86_64-none"
    echo ==^> Windows ARM64: using the x64 Python 3.12 ^(runs through Windows x64 emulation^)
)
"%UV_EXE%" python install !PY_REQ!
echo [debug] uv python install exit=!ERRORLEVEL!

echo ==^> Creating venv via uv venv ...
pushd "%INSTALL_DIR%"
REM --clear: a leftover .venv (interrupted install, older layout) makes
REM `uv venv` fail outright with "A virtual environment already exists",
REM so every later attempt dies the same way and the machine can never
REM heal itself. Measured 2026-09-05 on a Win11 box whose install was
REM interrupted mid-sync. Recreating is cheap; uv re-links from its cache.
"%UV_EXE%" venv --clear --python !PY_REQ! .venv
set VENV_RC=!ERRORLEVEL!
echo [debug] uv venv exit=!VENV_RC!
REM NOTE: `( popd ^& exit /b 2 )` does NOT work -- inside parentheses the
REM caret is taken literally, cmd prints "the syntax of the command is
REM incorrect" and CARRIES ON. The failure guard was therefore dead: a
REM broken venv fell through to `uv sync`, which then crashed with
REM 0xC0000409. Use separate statements.
if not !VENV_RC! equ 0 (
  popd
  exit /b 2
)

echo ==^> Installing dependencies via uv sync ...
REM Don't pass --python here. With --python uv may pick the BASE managed Python
REM and install packages into Roaming\uv\python\Lib\site-packages instead of
REM our .venv\Lib\site-packages, leaving the venv empty.
REM --reinstall forces re-install of all deps even if uv thinks they're already
REM satisfied via cache (which can be wrong if previous install polluted base
REM Python with editable install).
REM uv sync is the slowest step (it downloads ~1 GB) and its output used to
REM reach only the installer window, never installer.log -- a failure left
REM nothing to diagnose. Keep a copy in the log directory, then show it.
set "SYNC_LOG=%ProgramData%\jt-doc-tools\Logs\setup-python-sync.log"
if not exist "%ProgramData%\jt-doc-tools\Logs" mkdir "%ProgramData%\jt-doc-tools\Logs" 2>nul
REM Time only: %DATE% carries the weekday in the user's language (e.g. Chinese),
REM and cmd writes the file in the OEM code page -- on a machine whose
REM non-Unicode locale is English that weekday became "??" in the log.
echo [%TIME%] uv sync start > "%SYNC_LOG%" 2>nul
"%UV_EXE%" sync --reinstall >> "%SYNC_LOG%" 2>&1
set SYNC_RC=!ERRORLEVEL!
echo [%TIME%] uv sync exit=!SYNC_RC! >> "%SYNC_LOG%" 2>nul
type "%SYNC_LOG%" 2>nul
echo [debug] uv sync exit=!SYNC_RC!
if not !SYNC_RC! equ 0 (
  popd
  exit /b 3
)
popd

echo ==^> Verifying critical imports ...
"%VENV_PY%" -c "import fastapi, fitz, ldap3, PIL, pillow_heif, pdfplumber, docx, odf, openpyxl, pyzipper, httpx, psutil, pyotp, qrcode, pdf2docx, rapidfuzz, fontTools, numpy, cv2, lxml, pymupdf4llm, markdown_it, pygments, jwt, onelogin.saml2.auth, xmlsec, truststore, dns.resolver, defusedxml.ElementTree, pypinyin, smb.SMBConnection; print('OK')"
set IMP_RC=!ERRORLEVEL!
echo [debug] import smoke test exit=!IMP_RC!
if not !IMP_RC! equ 0 exit /b 4

REM EasyOCR 是 v1.7.2 主 OCR 引擎；deps 重（PyTorch ~700MB）— 失敗 warn 不 die
"%VENV_PY%" -c "import easyocr; print('easyocr OK')" 2>nul
if !ERRORLEVEL! equ 0 (
    echo [OK] EasyOCR available
) else (
    echo [WARN] EasyOCR not installed - OCR will fall back to tesseract ^(lower CJK accuracy^)
    echo [WARN]   Manual install: "%UV_EXE%" sync   or  "%VENV_PY%" -m pip install easyocr
)

echo [OK] Python environment ready: %INSTALL_DIR%\.venv
exit /b 0

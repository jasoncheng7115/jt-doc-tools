@echo off
REM Pure cmd batch script — set up Python venv via uv. We use this instead
REM of doing it inline in install.ps1 because PowerShell's native-command
REM error handling (especially under elevated `Start-Process -Verb RunAs`
REM with `*>&1 | Out-File` redirect) is unreliable: it swallows our debug
REM messages, treats uv's stderr writes as fatal, and reuses `$Args`-style
REM variables in confusing ways.
REM
REM Usage:  setup-python.cmd <install_dir>
REM Output: stdout/stderr in normal cmd format. Exit code:
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

REM uv 對「已裝過」回 exit 1，是正常訊息不算錯
echo ==^> Installing managed Python 3.12 via uv ...
set UV_PYTHON_PREFERENCE=only-managed
"%UV_EXE%" python install 3.12
echo [debug] uv python install exit=!ERRORLEVEL!

echo ==^> Creating venv via uv venv ...
pushd "%INSTALL_DIR%"
"%UV_EXE%" venv --python 3.12 .venv
set VENV_RC=!ERRORLEVEL!
echo [debug] uv venv exit=!VENV_RC!
if not !VENV_RC! equ 0 ( popd & exit /b 2 )

echo ==^> Installing dependencies via uv sync ...
"%UV_EXE%" sync --python 3.12
set SYNC_RC=!ERRORLEVEL!
echo [debug] uv sync exit=!SYNC_RC!
if not !SYNC_RC! equ 0 ( popd & exit /b 3 )
popd

echo ==^> Verifying critical imports ...
"%VENV_PY%" -c "import fastapi, fitz, ldap3, PIL, pdfplumber, docx, odf, pyzipper, httpx; print('OK')"
set IMP_RC=!ERRORLEVEL!
echo [debug] import smoke test exit=!IMP_RC!
if not !IMP_RC! equ 0 exit /b 4

echo [OK] Python environment ready: %INSTALL_DIR%\.venv
exit /b 0

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

REM uv may exit 1 saying "already installed" when Python 3.12 is present.
REM That's not an error, ignore.
echo ==^> Installing managed Python 3.12 via uv ...
set UV_PYTHON_PREFERENCE=only-managed
"%UV_EXE%" python install 3.12
echo [debug] uv python install exit=!ERRORLEVEL!

echo ==^> Creating venv via uv venv ...
pushd "%INSTALL_DIR%"
"%UV_EXE%" venv --python 3.12 .venv
set VENV_RC=!ERRORLEVEL!
echo [debug] uv venv exit=!VENV_RC!
if not !VENV_RC! equ 0 ( popd ^& exit /b 2 )

echo ==^> Installing dependencies via uv sync ...
REM Don't pass --python here. With --python uv may pick the BASE managed Python
REM and install packages into Roaming\uv\python\Lib\site-packages instead of
REM our .venv\Lib\site-packages, leaving the venv empty.
REM --reinstall forces re-install of all deps even if uv thinks they're already
REM satisfied via cache (which can be wrong if previous install polluted base
REM Python with editable install).
"%UV_EXE%" sync --reinstall
set SYNC_RC=!ERRORLEVEL!
echo [debug] uv sync exit=!SYNC_RC!
if not !SYNC_RC! equ 0 ( popd ^& exit /b 3 )
popd

echo ==^> Verifying critical imports ...
"%VENV_PY%" -c "import fastapi, fitz, ldap3, PIL, pdfplumber, docx, odf, pyzipper, httpx; print('OK')"
set IMP_RC=!ERRORLEVEL!
echo [debug] import smoke test exit=!IMP_RC!
if not !IMP_RC! equ 0 exit /b 4

echo [OK] Python environment ready: %INSTALL_DIR%\.venv
exit /b 0

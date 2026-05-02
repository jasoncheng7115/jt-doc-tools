"""``jtdt`` command-line interface.

Provides a small set of operational verbs that wrap whatever service
manager the platform uses (systemd / launchd / Windows Service via
NSSM). The actual install / uninstall is done by ``install.sh`` /
``install.ps1`` — this module is the runtime control surface that ships
with the installed app.

Verbs:
    jtdt start          — start the service
    jtdt stop           — stop the service
    jtdt restart
    jtdt status         — print service status + URL
    jtdt logs [-f]      — tail service logs
    jtdt open           — open the web UI in the default browser
    jtdt update         — git pull + uv sync + restart
    jtdt version        — print installed version
    jtdt run            — foreground run (for systemd ExecStart, debugging)
    jtdt uninstall      — remove service + program (keeps data; --purge to wipe)
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Optional

REPO_URL = "https://github.com/jasoncheng7115/jt-doc-tools"
SERVICE_NAME = "jt-doc-tools"
PLIST_LABEL = "com.jasontools.doctools"


# ---------------------------------------------------------------- env helpers

def _is_windows() -> bool:
    return platform.system() == "Windows"


def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _install_root() -> Path:
    """Resolve the on-disk install root. The CLI lives at
    ``<root>/app/cli.py``."""
    return Path(__file__).resolve().parent.parent


def _real_user() -> str:
    """Find the real (non-root) user that owns this install. Used on macOS
    to locate the LaunchAgent paths even when called via ``sudo``."""
    return os.environ.get("SUDO_USER") or os.environ.get("USER") or ""


def _real_home() -> Path:
    """Real user's home (not /var/root when invoked via sudo)."""
    user = _real_user()
    if user and user != "root":
        try:
            import pwd
            return Path(pwd.getpwnam(user).pw_dir)
        except Exception:
            pass
    return Path(os.path.expanduser("~"))


def _data_dir() -> Path:
    """Where user data lives. Honours ``JTDT_DATA_DIR`` override."""
    env = os.environ.get("JTDT_DATA_DIR")
    if env:
        return Path(env)
    if _is_windows():
        return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "jt-doc-tools" / "Data"
    if _is_macos():
        return _real_home() / "Library" / "Application Support" / "jt-doc-tools" / "data"
    return Path("/var/lib/jt-doc-tools/data")


MACOS_APP_PATH = "/Applications/Jason Tools 文件工具箱.app"


def _macos_app_running_pid() -> Optional[int]:
    """Return the pid of whatever's listening on our port, or None.

    We *don't* pgrep by command line: ``.venv/bin/python`` is a symlink to
    brew's interpreter, and ps shows the resolved Cellar path — pgrep -f
    won't match the symlink form. lsof on the listening port is the most
    robust way to find "the running service".
    """
    port = os.environ.get("JTDT_PORT", "8765")
    try:
        out = subprocess.check_output(
            ["lsof", "-tiTCP:" + port, "-sTCP:LISTEN"],
            text=True,
        )
        for line in out.splitlines():
            line = line.strip()
            if line.isdigit():
                return int(line)
    except Exception:
        pass
    return None


def _version_tuple(v: str) -> tuple:
    """Parse "1.2.3" → (1,2,3) for safe ordered comparison. Unknown / bad
    inputs sort lowest so update isn't blocked by parse errors."""
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split(".")[:4])
    except Exception:
        return (0,)


def _read_version() -> str:
    # Read directly from main.py text — `from .main import VERSION` would be
    # cached in sys.modules after first call, so a long-running process (e.g.
    # `jtdt update`) would still see the pre-upgrade value after git pull.
    try:
        import re as _re
        txt = (_install_root() / "app" / "main.py").read_text(encoding="utf-8")
        m = _re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', txt, _re.M)
        return m.group(1) if m else "?"
    except Exception:
        return "?"


def _server_url() -> str:
    host = os.environ.get("JTDT_HOST", "127.0.0.1")
    port = os.environ.get("JTDT_PORT", "8765")
    return f"http://{host}:{port}/"


# ------------------------------------------------------------ service control

def _run(cmd: list[str], **kw) -> int:
    return subprocess.call(cmd, **kw)


def _run_capture(cmd: list[str]) -> tuple[int, str]:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        return 0, out
    except subprocess.CalledProcessError as e:
        return e.returncode, e.output or ""
    except FileNotFoundError:
        return 127, ""


def svc_start() -> int:
    if _is_linux():
        return _run(["systemctl", "start", SERVICE_NAME])
    if _is_macos():
        # Launch the .app via LaunchServices (so soffice subprocess gets Aqua).
        if not Path(MACOS_APP_PATH).exists():
            print(f"App not installed at {MACOS_APP_PATH}", file=sys.stderr)
            return 1
        # When invoked under sudo, `open` runs as root and LaunchServices
        # refuses to launch GUI apps into the user's Aqua session
        # (errAEEventNotHandled / -600). Re-spawn as the real user.
        user = _real_user()
        cmd = ["open", "-a", MACOS_APP_PATH]
        if os.geteuid() == 0 and user and user != "root":
            cmd = ["sudo", "-u", user] + cmd
        return _run(cmd)
    if _is_windows():
        return _run(["sc.exe", "start", SERVICE_NAME])
    return 1


def svc_stop() -> int:
    if _is_linux():
        return _run(["systemctl", "stop", SERVICE_NAME])
    if _is_macos():
        pid = _macos_app_running_pid()
        if pid is None:
            print("(service not running)")
            return 0
        try:
            os.kill(pid, 15)  # SIGTERM
        except Exception as e:
            print(f"kill {pid} failed: {e}", file=sys.stderr)
            return 1
        # Wait for the port to actually free up — otherwise an immediate
        # svc_start() races with the dying process and the new .app launcher
        # sees the still-alive healthz, skipping its `exec python`.
        import time as _t
        for _ in range(20):  # up to 4s
            _t.sleep(0.2)
            if _macos_app_running_pid() is None:
                return 0
        try:
            os.kill(pid, 9)  # SIGKILL fallback
        except Exception:
            pass
        return 0
    if _is_windows():
        return _run(["sc.exe", "stop", SERVICE_NAME])
    return 1


def svc_restart() -> int:
    if _is_linux():
        return _run(["systemctl", "restart", SERVICE_NAME])
    svc_stop()
    return svc_start()


def svc_status() -> int:
    print(f"jt-doc-tools v{_read_version()}")
    print(f"  install : {_install_root()}")
    print(f"  data    : {_data_dir()}")
    print(f"  url     : {_server_url()}")
    print()
    if _is_linux():
        rc, out = _run_capture(["systemctl", "is-active", SERVICE_NAME])
        print(f"  service : {out.strip() or 'unknown'}")
        return rc
    if _is_macos():
        pid = _macos_app_running_pid()
        if pid:
            print(f"  service : running (pid {pid})")
            return 0
        print("  service : not running (open '{}' to start)".format(MACOS_APP_PATH))
        return 1
    if _is_windows():
        rc, out = _run_capture(["sc.exe", "query", SERVICE_NAME])
        for line in out.splitlines():
            if "STATE" in line:
                print(f"  service : {line.strip()}")
        return rc
    return 1


def svc_logs(follow: bool) -> int:
    if _is_linux():
        cmd = ["journalctl", "-u", SERVICE_NAME, "--no-pager", "-n", "200"]
        if follow:
            cmd.append("-f")
        return _run(cmd)
    if _is_macos():
        log = _real_home() / "Library" / "Logs" / "jt-doc-tools.log"
        if not log.exists():
            print(f"log not found: {log}", file=sys.stderr)
            return 1
        cmd = ["tail", "-n", "200"]
        if follow:
            cmd.append("-F")
        cmd.append(str(log))
        return _run(cmd)
    if _is_windows():
        log = _data_dir() / "logs" / "jt-doc-tools.log"
        if not log.exists():
            print(f"log not found: {log}", file=sys.stderr)
            return 1
        if follow:
            print("(use Get-Content -Wait in PowerShell to follow)")
        return _run(["powershell", "-NoProfile", "-Command",
                     f"Get-Content -Path '{log}' -Tail 200" + (" -Wait" if follow else "")])
    return 1


def svc_open() -> int:
    webbrowser.open(_server_url())
    return 0


def svc_run() -> int:
    """Foreground run — used by service managers as ExecStart."""
    from .main import run  # type: ignore
    run()
    return 0


def svc_version() -> int:
    print(_read_version())
    return 0


# ------------------------------------------------------------ update flow

def _is_admin() -> bool:
    if _is_windows():
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def _install_owner(root: Path) -> Optional[tuple[int, int]]:
    """Return (uid, gid) that owns the install dir on Linux/macOS, None on Windows.

    Used to restore ownership after `sudo jtdt update` writes new files as
    root. Also tells us when to set `safe.directory` for git so it doesn't
    refuse to operate on a differently-owned repo (git 2.35.2+ behaviour)."""
    if _is_windows():
        return None
    try:
        st = root.stat()
        return (st.st_uid, st.st_gid)
    except Exception:
        return None


def _git_env_for(root: Path) -> dict[str, str]:
    """Return an env dict with `safe.directory=<root>` set so git won't
    error out with `fatal: detected dubious ownership in repository`. This
    happens when ``sudo jtdt update`` runs git as root against a repo
    chowned to a service user (`jtdt` on Linux)."""
    env = os.environ.copy()
    if not _is_windows():
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = str(root)
    return env


def _restore_ownership(root: Path, owner: Optional[tuple[int, int]]) -> None:
    """Recursively chown the install dir back to the original owner. Called
    after git pull / uv sync on Linux when those ran as root but the install
    dir is owned by the service user (so the service can keep reading)."""
    if not owner or _is_windows():
        return
    uid, gid = owner
    if uid == 0:
        return  # Was root-owned to begin with, no need to restore
    try:
        subprocess.call(["chown", "-R", f"{uid}:{gid}", str(root)])
    except Exception as exc:
        print(f"Warning: failed to restore owner of {root}: {exc}", file=sys.stderr)


def svc_update() -> int:
    """Pull latest release and re-sync deps. Backups data dir first."""
    if not _is_admin():
        print("Upgrade requires administrator privileges.", file=sys.stderr)
        if _is_windows():
            print("Please run PowerShell as Administrator, then re-run 'jtdt update'.", file=sys.stderr)
        else:
            print("Run with sudo:  sudo jtdt update", file=sys.stderr)
        return 1

    root = _install_root()
    owner = _install_owner(root)
    if not (root / ".git").exists():
        print(f"Install dir {root} is not a git repo; cannot git pull", file=sys.stderr)
        print(f"Re-run the install script to upgrade", file=sys.stderr)
        return 1

    # Capture current version
    cur = _read_version()
    print(f"Current version: v{cur}")

    # 1. Stop service
    print("Stopping service ...")
    svc_stop()

    # 2. Backup data
    import datetime
    data = _data_dir()
    if data.exists():
        backup = data.parent / f"{data.name}.backup-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        print(f"Backed up data: {data} -> {backup}")
        shutil.copytree(data, backup, dirs_exist_ok=False)
        # Keep only last 3 backups
        siblings = sorted(
            data.parent.glob(f"{data.name}.backup-*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in siblings[3:]:
            print(f"  Removed old backup: {stale}")
            shutil.rmtree(stale, ignore_errors=True)

    # 3. git pull (with safe.directory so it works on differently-owned repos)
    print("Pulling latest from GitHub ...")
    git_env = _git_env_for(root)
    rc = subprocess.call(
        ["git", "-C", str(root), "fetch", "--tags", "origin"], env=git_env)
    if rc != 0:
        print("git fetch failed, restoring: starting previous service", file=sys.stderr)
        _restore_ownership(root, owner)
        svc_start()
        return rc
    # 用 fetch + reset --hard 而非 pull --ff-only：後者在 remote 被 force-push
    # (歷史重寫) 時會 abort「Not possible to fast-forward」。reset --hard 強制
    # 對齊 origin/main 是 fresh-checkout 的標準作法 — 我們不在 install 內做開發
    # commit，所以無「本地未提交變更」需要保留。
    rc = subprocess.call(
        ["git", "-C", str(root), "reset", "--hard", "origin/main"], env=git_env)
    if rc != 0:
        print("git reset --hard origin/main failed, restoring", file=sys.stderr)
        _restore_ownership(root, owner)
        svc_start()
        return rc

    # 3b. 降版保護：若 origin/main 的 VERSION 比 cur 還舊，幾乎一定是
    # origin 設錯（例如指向過期的本地 file:// 鏡像）。直接降版會掉功能、
    # DB migration 不可逆、客戶資料風險高 — 直接 abort 並還原。
    new_ver = _read_version()
    if _version_tuple(new_ver) < _version_tuple(cur):
        print(
            f"WARNING: downgrade detected: origin/main is v{new_ver}, older than current v{cur}.\n"
            f"  Almost certainly a git remote misconfig (e.g. stale local file:// mirror).\n"
            f"  Check with:  git -C {root} remote -v\n"
            f"  Official repo should be:  https://github.com/jasoncheng7115/jt-doc-tools.git\n"
            f"  Aborted upgrade and restored previous state.",
            file=sys.stderr,
        )
        # Restore previous code by undoing the reset
        subprocess.call(
            ["git", "-C", str(root), "reset", "--hard", f"v{cur}"],
            env=git_env,
        )  # may fail if no tag; that's fine — server stays stopped, user fixes manually
        _restore_ownership(root, owner)
        svc_start()
        return 1

    # 4. uv sync — never use --frozen, lockfile may be stale (see v1.1.68 fix).
    # Always reconcile against pyproject.toml so missing deps (eg. ldap3 in
    # uv.lock < 1.1.68) get installed.
    # On Windows the uv binary is `uv.exe`, on Linux/macOS just `uv`.
    uv_local = root / "bin" / ("uv.exe" if _is_windows() else "uv")
    if uv_local.exists():
        uv = str(uv_local)
    elif shutil.which("uv"):
        uv = shutil.which("uv")
    else:
        print(f"uv binary not found (looked at {uv_local} and PATH); cannot sync deps",
              file=sys.stderr)
        _restore_ownership(root, owner)
        svc_start()
        return 1
    print("Syncing Python deps (uv sync) ...")
    rc = subprocess.call([uv, "sync"], cwd=str(root))
    if rc != 0:
        print("uv sync failed, restoring previous state", file=sys.stderr)
        _restore_ownership(root, owner)
        svc_start()
        return rc

    # 4b. Smoke-test critical imports — catches the "uv said OK but actually
    # didn't install some dep" class of bug that hit the v1.1.66 customer.
    venv_py = root / ".venv" / "bin" / "python"
    if not venv_py.exists() and _is_windows():
        venv_py = root / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        print("Verifying critical deps (fastapi / fitz / ldap3 / PIL / pdfplumber / docx / odf / pyzipper) ...")
        rc = subprocess.call([str(venv_py), "-c",
            "import fastapi, fitz, ldap3, PIL, pdfplumber, docx, odf, pyzipper, httpx"])
        if rc != 0:
            print("Dep import failed — upgrade may be incomplete, restoring", file=sys.stderr)
            _restore_ownership(root, owner)
            svc_start()
            return rc

    # 5. Restore ownership so the service user can read the new files
    _restore_ownership(root, owner)

    # 5b. Ensure system-level deps for new features (auto best-effort).
    _ensure_system_deps_for_update()

    # 5. Restart
    print("Starting new version ...")
    rc = svc_start()
    if rc != 0:
        print("Service failed to start; check 'jtdt logs'", file=sys.stderr)
        return rc

    # 6. Health check
    import time
    import urllib.request
    print("Health check ...")
    url = _server_url() + "healthz"
    for _ in range(15):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    new = _read_version()
                    print(f"Upgrade done: v{cur} -> v{new}")
                    _print_system_deps_summary()
                    return 0
        except Exception:
            time.sleep(1)
    print("Health check timed out; check 'jtdt logs'", file=sys.stderr)
    _print_system_deps_summary()
    return 1


def _print_system_deps_summary() -> None:
    """Print system dependency status table after upgrade.

    Each entry: (display name, detect-fn -> bool, impact description, install
    command dict). All English to ensure compatibility with Windows console
    that may not render CJK reliably.
    """
    deps = [
        (
            "tesseract OCR",
            lambda: bool(shutil.which("tesseract")) and _tesseract_has_lang("chi_tra"),
            "pdf-editor automatic OCR text recognition (without it, manual retype required)",
            {
                "linux": "sudo apt install tesseract-ocr tesseract-ocr-chi-tra tesseract-ocr-eng",
                "macos": "brew install tesseract tesseract-lang",
                "windows": "Download https://github.com/UB-Mannheim/tesseract/wiki",
            },
        ),
        (
            "Office engine (OxOffice / LibreOffice)",
            _office_present,
            "office-to-pdf / pdf-to-office tools",
            {
                "linux": "sudo apt install libreoffice fonts-noto-cjk",
                "macos": "brew install --cask libreoffice",
                "windows": "winget install TheDocumentFoundation.LibreOffice",
            },
        ),
    ]
    missing = [d for d in deps if not d[1]()]
    if not missing:
        return
    print()
    print("Missing system dependencies:")
    plat = "linux" if _is_linux() else ("macos" if _is_macos() else "windows")
    for name, _, impact, cmds in missing:
        print(f"  -{name}")
        print(f"    Impact: {impact}")
        print(f"    Install:  {cmds.get(plat, 'see official docs')}")
    print()


def _tesseract_has_lang(lang: str) -> bool:
    try:
        out = subprocess.run(
            ["tesseract", "--list-langs"],
            capture_output=True, text=True, timeout=5,
        )
        return lang in (out.stdout or "")
    except Exception:
        return False


def _office_present() -> bool:
    """偵測 OxOffice / LibreOffice 任一存在。"""
    candidates = []
    if _is_linux():
        candidates = [
            "/opt/oxoffice/program/soffice",
            "/usr/bin/libreoffice", "/usr/bin/soffice",
        ]
    elif _is_macos():
        candidates = [
            "/Applications/OxOffice.app/Contents/MacOS/soffice",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        ]
    elif _is_windows():
        prog = os.environ.get("ProgramFiles", r"C:\Program Files")
        prog86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates = [
            rf"{prog}\OxOffice\program\soffice.exe",
            rf"{prog}\LibreOffice\program\soffice.exe",
            rf"{prog86}\LibreOffice\program\soffice.exe",
        ]
    if any(Path(c).exists() for c in candidates):
        return True
    return bool(shutil.which("soffice") or shutil.which("libreoffice"))


def _ensure_system_deps_for_update() -> None:
    """在 jtdt update 流程中自動補裝新版需要的系統套件。

    任何錯誤都只 warn 不 raise — 升級流程不能因為某個 optional system 套件
    裝不起來就 abort。每個套件的安裝結果獨立判斷。

    新加任何系統相依套件時，請在這裡加一段 best-effort 安裝邏輯（並同步更新
    install.sh / install.ps1 對應段落）。
    """
    # tesseract OCR — pdf-editor 文字辨識 fallback (自 v1.2.2 起)
    _ensure_tesseract()


def _ensure_tesseract() -> None:
    if shutil.which("tesseract"):
        # Already installed — verify chi_tra trained data also present
        try:
            out = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True, text=True, timeout=5,
            )
            if "chi_tra" in (out.stdout or ""):
                return
        except Exception:
            return
    print("Installing tesseract OCR (pdf-editor text recovery fallback) ...")
    rc = -1
    try:
        if _is_linux():
            if shutil.which("apt-get"):
                env = os.environ.copy()
                env["DEBIAN_FRONTEND"] = "noninteractive"
                rc = subprocess.call(
                    ["apt-get", "install", "-y",
                     "tesseract-ocr", "tesseract-ocr-chi-tra", "tesseract-ocr-eng"],
                    env=env,
                )
            elif shutil.which("dnf"):
                rc = subprocess.call(
                    ["dnf", "install", "-y",
                     "tesseract", "tesseract-langpack-chi_tra", "tesseract-langpack-eng"],
                )
        elif _is_macos():
            brew = shutil.which("brew")
            if brew:
                rc = subprocess.call([brew, "install", "tesseract", "tesseract-lang"])
        elif _is_windows():
            winget = shutil.which("winget")
            if winget:
                rc = subprocess.call([
                    winget, "install", "--id", "UB-Mannheim.TesseractOCR",
                    "-e", "--silent",
                    "--accept-package-agreements",
                    "--accept-source-agreements",
                ])
    except Exception as e:
        print(f"  WARNING: tesseract install errored: {e}  (pdf-editor OCR disabled, rest still works)",
              file=sys.stderr)
        return
    if rc == 0 and shutil.which("tesseract"):
        print("  OK: tesseract installed")
    else:
        print("  WARNING: tesseract auto-install failed  (pdf-editor OCR disabled, rest still works)",
              file=sys.stderr)
        if _is_windows():
            print("    Download manually: https://github.com/UB-Mannheim/tesseract/wiki",
                  file=sys.stderr)


def svc_uninstall(purge: bool) -> int:
    if not _is_admin():
        print("Uninstall requires administrator privileges.", file=sys.stderr)
        return 1
    print("Stopping and removing service ...")
    svc_stop()
    if _is_linux():
        _run(["systemctl", "disable", SERVICE_NAME])
        Path(f"/etc/systemd/system/{SERVICE_NAME}.service").unlink(missing_ok=True)
        _run(["systemctl", "daemon-reload"])
    elif _is_macos():
        # Stop the service (if running)
        pid = _macos_app_running_pid()
        if pid:
            try:
                os.kill(pid, 15)
                import time as _t; _t.sleep(1)
            except Exception:
                pass
        # Remove .app
        app = Path(MACOS_APP_PATH)
        if app.exists():
            shutil.rmtree(app, ignore_errors=True)
        # Remove from Login Items
        user = _real_user()
        if user and user != "root":
            try:
                subprocess.call(
                    ["sudo", "-u", user, "osascript", "-e",
                     'tell application "System Events" to delete login item "Jason Tools 文件工具箱"'],
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass
        # Old LaunchDaemon/Agent cleanup (if upgrading from old install)
        for legacy in (
            Path("/Library/LaunchDaemons/com.jasontools.doctools.plist"),
            _real_home() / "Library" / "LaunchAgents" / "com.jasontools.doctools.plist",
        ):
            if legacy.exists():
                try:
                    subprocess.call(["launchctl", "bootout", "system" if "Library/Launch" in str(legacy) and "Daemons" in str(legacy) else f"gui/{os.getuid()}", str(legacy)],
                                    stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                legacy.unlink(missing_ok=True)
    elif _is_windows():
        _run(["sc.exe", "delete", SERVICE_NAME])

    root = _install_root()

    # Remove the jtdt CLI shim (created outside the install dir on Linux/macOS)
    for shim in (Path("/usr/local/bin/jtdt"), Path("/usr/bin/jtdt")):
        if shim.exists() or shim.is_symlink():
            print(f"Removed CLI shim: {shim}")
            try:
                shim.unlink()
            except Exception:
                pass

    print(f"Removed program files: {root}")
    if _is_windows():
        # We're running from a Python interpreter inside `root` (launched via
        # `jtdt.cmd` shim that also lives in `root`). If we rmtree it now,
        # cmd.exe will print "找不到批次檔。" because it tries to read the
        # next line from the now-deleted .cmd. Defer the deletion to a
        # detached helper that fires AFTER we exit.
        helper = Path(os.environ.get("TEMP") or os.environ.get("TMP") or r"C:\Windows\Temp") \
                 / f"jtdt-cleanup-{os.getpid()}.cmd"
        helper.write_text(
            "@echo off\r\n"
            "timeout /t 2 /nobreak >nul\r\n"
            f'rd /s /q "{root}"\r\n'
            'del /q "%~f0"\r\n',
            encoding="ascii",
        )
        # DETACHED_PROCESS = 0x00000008, CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            ["cmd.exe", "/c", "start", "", "/B", str(helper)],
            creationflags=0x00000008 | 0x00000200,
            close_fds=True,
        )
    else:
        shutil.rmtree(root, ignore_errors=True)

    # Clean up macOS log files
    if _is_macos():
        log_dir = _real_home() / "Library" / "Logs"
        for log in (log_dir / "jt-doc-tools.log", log_dir / "jt-doc-tools.err"):
            try:
                log.unlink(missing_ok=True)
            except Exception:
                pass

    data = _data_dir()
    if purge:
        if data.exists():
            print(f"Removed data dir: {data}")
            shutil.rmtree(data, ignore_errors=True)
        # Also wipe the rotation backups (jtdt update creates these alongside).
        for bk in sorted(data.parent.glob(f"{data.name}.backup-*")):
            print(f"Removed backup: {bk}")
            shutil.rmtree(bk, ignore_errors=True)
        # If the parent dir is now empty (Linux: /var/lib/jt-doc-tools/),
        # remove it too — leaving an empty dir owned by the (about-to-be-
        # removed) jtdt user just looks abandoned.
        try:
            if data.parent.exists() and not any(data.parent.iterdir()):
                data.parent.rmdir()
                print(f"Removed empty parent dir: {data.parent}")
        except Exception:
            pass
        # Linux only: remove the dedicated `jtdt` system user we created.
        # Skipped if any file on the system is still owned by it (paranoia
        # against leaving orphans).
        if _is_linux():
            try:
                import pwd as _pwd
                _pwd.getpwnam("jtdt")  # raises if user doesn't exist
                # check ownership: scan a few likely places quickly
                rc, _ = _run_capture(["find", "/var", "/etc", "/opt", "-xdev",
                                      "-uid", str(_pwd.getpwnam("jtdt").pw_uid),
                                      "-print", "-quit"])
                # `find ... -print -quit` exits 0 with empty output if nothing found
                _, leftover = _run_capture(["find", "/var", "/etc", "/opt", "-xdev",
                                            "-uid", str(_pwd.getpwnam("jtdt").pw_uid),
                                            "-print", "-quit"])
                if leftover.strip():
                    print(f"Keeping jtdt service user (other files still owned: {leftover.strip()})")
                else:
                    _run(["userdel", "jtdt"])
                    print("Removed jtdt service user")
            except KeyError:
                pass  # user doesn't exist, nothing to do
            except Exception as e:
                print(f"Failed to remove jtdt user: {e}", file=sys.stderr)
    elif data.exists():
        print(f"Data preserved: {data}  (use --purge to also remove)")
    return 0


# --------------------------------------------------------------------- bind 變更

def svc_bind(addr: str) -> int:
    """改變服務監聽的位址 / port，無痛跨平台切換 127.0.0.1 ↔ 0.0.0.0 等。

    addr 接受三種格式：
      - "0.0.0.0"        只改 host，port 保留
      - ":9999"          只改 port，host 保留
      - "0.0.0.0:9999"   兩個一起改
    """
    if not _is_admin():
        print("Bind change requires administrator privileges: sudo jtdt bind ...", file=sys.stderr)
        return 1

    new_host: Optional[str] = None
    new_port: Optional[str] = None
    if ":" in addr:
        h, _, p = addr.rpartition(":")
        if h: new_host = h
        if p: new_port = p
    else:
        new_host = addr

    if new_host is None and new_port is None:
        print("Usage:  sudo jtdt bind <addr>[:port]    e.g.  sudo jtdt bind 0.0.0.0", file=sys.stderr)
        return 2

    changed = []

    if _is_linux():
        unit = Path("/etc/systemd/system/jt-doc-tools.service")
        if not unit.exists():
            print(f"systemd unit not found: {unit}", file=sys.stderr)
            return 1
        txt = unit.read_text()
        import re as _re
        if new_host is not None:
            txt2 = _re.sub(r"^Environment=JTDT_HOST=.*$",
                           f"Environment=JTDT_HOST={new_host}", txt, flags=_re.M)
            if txt2 != txt: changed.append(f"JTDT_HOST → {new_host}")
            txt = txt2
        if new_port is not None:
            txt2 = _re.sub(r"^Environment=JTDT_PORT=.*$",
                           f"Environment=JTDT_PORT={new_port}", txt, flags=_re.M)
            if txt2 != txt: changed.append(f"JTDT_PORT → {new_port}")
            txt = txt2
        if not changed:
            print("No change (value may already be set)"); return 0
        unit.write_text(txt)
        for c in changed: print(f"  {c}")
        print("Reloading systemd + restarting service ...")
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "restart", "jt-doc-tools"])
        return 0

    if _is_macos():
        launcher = Path(MACOS_APP_PATH) / "Contents" / "MacOS" / "launcher"
        if not launcher.exists():
            print(f"launcher not found: {launcher}", file=sys.stderr)
            return 1
        txt = launcher.read_text()
        import re as _re
        if new_host is not None:
            txt2 = _re.sub(r"JTDT_HOST=\S+", f"JTDT_HOST={new_host}", txt)
            if txt2 != txt: changed.append(f"JTDT_HOST → {new_host}")
            txt = txt2
        if new_port is not None:
            # launcher 內 URL 變數 + JTDT_PORT 都要改
            txt2 = _re.sub(r"JTDT_PORT=\S+", f"JTDT_PORT={new_port}", txt)
            txt2 = _re.sub(r'URL="http://127\.0\.0\.1:\d+/"',
                           f'URL="http://127.0.0.1:{new_port}/"', txt2)
            if txt2 != txt: changed.append(f"JTDT_PORT → {new_port}")
            txt = txt2
        if not changed:
            print("No change (value may already be set)"); return 0
        launcher.write_text(txt)
        for c in changed: print(f"  {c}")
        print("Restarting service ...")
        svc_stop()
        svc_start()
        return 0

    if _is_windows():
        print("Use NSSM on Windows:", file=sys.stderr)
        if new_host is not None:
            print(f"  nssm set jt-doc-tools AppEnvironmentExtra JTDT_HOST={new_host}")
        if new_port is not None:
            print(f"  nssm set jt-doc-tools AppEnvironmentExtra JTDT_PORT={new_port}")
        print("  nssm restart jt-doc-tools")
        return 1
    return 1


# --------------------------------------------------------------------- reset password (recovery)

def svc_reset_password(username: str, new_password: Optional[str] = None) -> int:
    """Emergency password reset, runs offline against the auth DB.

    For when the admin lost their password and can't log in. Requires sudo
    (we touch the data dir + need to be the user that owns it). Will:
      1. Verify the username exists in auth.sqlite + is local-source
      2. Prompt for a new password (twice) unless given via --password
      3. Validate against the password policy
      4. Hash with scrypt and update users.password_hash
      5. Revoke ALL active sessions for that user (forces re-login everywhere)
      6. Audit-log the reset (logged as actor=cli)

    The service does NOT need to be stopped — SQLite WAL handles the write
    safely while a service might be reading.
    """
    if not _is_admin():
        print("Reset-password requires administrator privileges：sudo jtdt reset-password <username>",
              file=sys.stderr)
        return 1

    install_root = _install_root()
    venv_python = install_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print(f"venv python not found: {venv_python}", file=sys.stderr)
        return 1

    # Run via the venv python so we get auth_db / passwords / etc.
    helper = f"""
import sys, getpass
from pathlib import Path
import os
# Make sure JTDT_DATA_DIR matches what the service uses.
os.environ.setdefault('JTDT_DATA_DIR', {repr(str(_data_dir()))})
sys.path.insert(0, {repr(str(install_root))})

from app.core import auth_db, passwords, sessions, audit_db, db
auth_db.init()
audit_db.init()

username = sys.argv[1]
preset = sys.argv[2] if len(sys.argv) > 2 else None

conn = auth_db.conn()
row = conn.execute(
    "SELECT id, source FROM users WHERE username=? AND source='local'",
    (username,)
).fetchone()
if not row:
    print(f"User {{username!r}} not found or not a local account (LDAP/AD users: change password in the directory server)",
          file=sys.stderr)
    sys.exit(2)

if preset:
    pw1 = preset
else:
    pw1 = getpass.getpass(f"New password for {{username}}: ")
    pw2 = getpass.getpass("Confirm new password: ")
    if pw1 != pw2:
        print("Passwords do not match", file=sys.stderr)
        sys.exit(3)

ok, err = passwords.validate_password(pw1)
if not ok:
    print(err, file=sys.stderr)
    sys.exit(4)

new_hash = passwords.hash_password(pw1)
with db.tx(conn):
    conn.execute("UPDATE users SET password_hash=?, enabled=1 WHERE id=?",
                 (new_hash, row['id']))
    # Wipe all sessions so old cookies stop working
    conn.execute("DELETE FROM sessions WHERE user_id=?", (row['id'],))
    # Reset any lockout for this user
    conn.execute("DELETE FROM lockouts WHERE key LIKE ?", (f"user:{{username.lower()}}",))

audit_db.log_event(
    "user_pwd_reset", username="(cli)", target=username,
    details={{"via": "jtdt reset-password"}}
)
print(f"OK: password reset for user {{username}} (user_id={{row['id']}})")
print(f"   All existing sessions invalidated; failure-counter reset.")
"""
    cmd = [str(venv_python), "-c", helper, username]
    if new_password:
        cmd.append(new_password)
    return subprocess.call(cmd)


# --------------------------------------------------------------------- auth recovery (offline)

def _run_auth_helper(snippet: str) -> int:
    """Run a Python snippet inside the install venv with the data-dir env
    set up. Used for offline auth-recovery commands (disable-auth, show-auth)
    so they don't require the web service to be running."""
    install_root = _install_root()
    venv_python = install_root / ".venv" / "bin" / "python"
    if not venv_python.exists() and _is_windows():
        venv_python = install_root / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        print(f"venv python not found: {venv_python}", file=sys.stderr)
        return 1
    header = (
        "import os, sys\n"
        f"os.environ.setdefault('JTDT_DATA_DIR', {repr(str(_data_dir()))})\n"
        f"sys.path.insert(0, {repr(str(install_root))})\n"
    )
    return subprocess.call([str(venv_python), "-c", header + snippet])


def svc_auth_show() -> int:
    """Print the current auth backend + brief settings (no secrets)."""
    return _run_auth_helper(
        "from app.core import auth_settings\n"
        "s = auth_settings.get()\n"
        "backend = s.get('backend', 'off')\n"
        "labels = {'off': 'disabled', 'local': 'local', 'ldap': 'LDAP', 'ad': 'Active Directory'}\n"
        "print(f'Auth backend: {backend} ({labels.get(backend, backend)})')\n"
        "if backend in ('ldap', 'ad'):\n"
        "    d = s.get('directory', {}) or {}\n"
        "    print(f'  Server URI:  {d.get(\"uri\", \"(unset)\")}')\n"
        "    print(f'  Search Base: {d.get(\"user_search_base\", \"(unset)\")}')\n"
        "    print(f'  Bind DN:     {d.get(\"bind_dn\", \"(unset)\")}')\n"
        "    print(f'  TLS:         {d.get(\"use_tls\", False)}')\n"
    )


def svc_auth_disable() -> int:
    """Switch auth backend to 'off'. Sessions wiped, user/perm rows kept
    so re-enabling later doesn't lose setup. Use this when LDAP/AD config
    locks you out and you can't login to fix it via the web UI."""
    if not _is_admin():
        print("Auth setting change requires admin privileges: sudo jtdt auth disable",
              file=sys.stderr)
        return 1
    print("Switching auth backend to 'off' (all sessions will be invalidated) ...")
    return _run_auth_helper(
        "from app.core import auth_settings\n"
        "before = auth_settings.get_backend()\n"
        "if before == 'off':\n"
        "    print('Already 'off'; no change needed.'); raise SystemExit(0)\n"
        "auth_settings.disable_auth(actor='cli', ip='localhost')\n"
        "print(f'OK: switched from {before} to off. Restart the service: jtdt restart')\n"
    )


def svc_auth_set_local() -> int:
    """Switch auth backend to 'local' (keeps existing local users). If no
    local admin exists yet, you still need ``jtdt reset-password jtdt-admin``
    to seed/recover the seed admin."""
    if not _is_admin():
        print("Auth setting change requires admin privileges: sudo jtdt auth set-local",
              file=sys.stderr)
        return 1
    return _run_auth_helper(
        "from app.core import auth_settings, auth_db\n"
        "auth_db.init()\n"
        "before = auth_settings.get_backend()\n"
        "if before == 'local':\n"
        "    print('Already on 'local' backend.'); raise SystemExit(0)\n"
        "s = auth_settings.get()\n"
        "s['backend'] = 'local'\n"
        "auth_settings.save(s)\n"
        "# Wipe sessions so old LDAP/AD cookies don't carry over\n"
        "from app.core import db\n"
        "conn = auth_db.conn()\n"
        "with db.tx(conn):\n"
        "    conn.execute('DELETE FROM sessions')\n"
        "print(f'OK: switched from {before} to 'local'. Restart: jtdt restart')\n"
        "print('  To reset admin password, run: sudo jtdt reset-password jtdt-admin')\n"
    )


# --------------------------------------------------------------------- argparse

def _print_friendly_help() -> None:
    """Pretty grouped command list — beats argparse's cramped one-liner usage
    that overflows on terminal widths < 100 cols.

    English-only on purpose: some server terminals (raw TTY, ssh into minimal
    container, Windows console without UTF-8 codepage) can't render CJK and
    show garbled text. CLI help should be readable everywhere.
    """
    ver = _read_version()
    print(f"jtdt — Jason Tools document toolbox v{ver}")
    print()
    print("Usage: jtdt <command> [options]")
    print()
    print("Service control:")
    print("  start                   Start the service")
    print("  stop                    Stop the service")
    print("  restart                 Restart the service")
    print("  status                  Show status and settings")
    print("  logs [-f]               Show service logs (-f to follow)")
    print("  open                    Open the web UI in the default browser")
    print()
    print("Upgrade and maintenance:")
    print("  update                  Pull latest from GitHub and restart")
    print("  version                 Print version")
    print("  bind <host:port>        Change listen address / port (auto-restart)")
    print("  uninstall [--purge]     Uninstall (--purge to also wipe data)")
    print()
    print("Emergency recovery (auth locked out, forgotten password):")
    print("  auth show               Show current auth backend")
    print("  auth disable            Switch auth backend to off (unlock login)")
    print("  auth set-local          Switch auth backend to local (built-in users)")
    print("  reset-password <user>   Reset a local user's password")
    print()
    print("Per-command help: jtdt <command> --help")


def main(argv: list[str] | None = None) -> int:
    # Show friendly grouped help when no args given (or just `-h` / `--help`).
    # Default argparse output is one cramped line that overflows on narrow
    # terminals — bad first impression for a CLI customers see daily.
    raw = argv if argv is not None else sys.argv[1:]
    if not raw or raw[0] in ("-h", "--help", "help"):
        _print_friendly_help()
        return 0

    p = argparse.ArgumentParser(
        prog="jtdt",
        description="Jason Tools document toolbox — run 'jtdt' (no args) for grouped command list",
        usage="jtdt <command> [options]   (run 'jtdt' for the full command list)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("start", help="啟動服務")
    sub.add_parser("stop", help="停止服務")
    sub.add_parser("restart", help="重啟服務")
    sub.add_parser("status", help="顯示狀態與設定")
    p_logs = sub.add_parser("logs", help="顯示服務 log")
    p_logs.add_argument("-f", "--follow", action="store_true")
    sub.add_parser("open", help="用瀏覽器開啟介面")
    sub.add_parser("update", help="從 GitHub 拉新版並重啟")
    sub.add_parser("version", help="顯示版本")
    sub.add_parser("run", help="前景啟動（給 service manager 用）")
    p_bind = sub.add_parser("bind", help="變更服務監聽位址 / port（會自動重啟）")
    p_bind.add_argument("addr", help="<host>、:port、或 <host>:<port>。例：0.0.0.0、:9999、0.0.0.0:9999")
    p_uninst = sub.add_parser("uninstall", help="解除安裝（資料預設保留）")
    p_uninst.add_argument("--purge", action="store_true", help="連同資料一起刪除")
    p_rpw = sub.add_parser("reset-password",
                            help="緊急重設帳號密碼（管理員忘記密碼時用）")
    p_rpw.add_argument("username", help="要重設的本機帳號")
    p_rpw.add_argument("--password", help="直接給新密碼（避免互動 prompt；不建議在共享機器用）")

    p_auth = sub.add_parser("auth", help="認證設定（緊急復原用）")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd", required=True)
    auth_sub.add_parser("show", help="顯示目前認證 backend")
    auth_sub.add_parser("disable", help="把認證 backend 切回 off（解除登入封鎖）")
    auth_sub.add_parser("set-local", help="把認證 backend 切到 local（本機帳號）")

    args = p.parse_args(argv)
    table = {
        "start": svc_start,
        "stop": svc_stop,
        "restart": svc_restart,
        "status": svc_status,
        "open": svc_open,
        "update": svc_update,
        "version": svc_version,
        "run": svc_run,
    }
    if args.cmd == "logs":
        return svc_logs(args.follow)
    if args.cmd == "uninstall":
        return svc_uninstall(args.purge)
    if args.cmd == "bind":
        return svc_bind(args.addr)
    if args.cmd == "reset-password":
        return svc_reset_password(args.username, args.password)
    if args.cmd == "auth":
        if args.auth_cmd == "show":
            return svc_auth_show()
        if args.auth_cmd == "disable":
            return svc_auth_disable()
        if args.auth_cmd == "set-local":
            return svc_auth_set_local()
        return 1
    return table[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())

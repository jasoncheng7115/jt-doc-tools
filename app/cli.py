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


def _service_bind() -> tuple[Optional[str], Optional[str]]:
    """Read the host / port the **installed service** actually uses.

    `jtdt bind` writes these into the service manager's own configuration
    (systemd `Environment=`, the macOS launcher script, the WinSW XML) —
    **not** into anybody's shell. So `sudo jtdt update` does not inherit
    them, and reading only `os.environ` gives the defaults 127.0.0.1:8765.

    That is a real customer-visible bug: on an install bound to another
    port (or to one specific LAN address rather than 0.0.0.0), the update
    health check probes a port nothing is listening on and reports
    "Health check timed out" while the service is perfectly healthy.
    """
    import re as _re
    txt = ""
    try:
        if _is_linux():
            unit = Path("/etc/systemd/system") / f"{SERVICE_NAME}.service"
            if unit.exists():
                txt = unit.read_text(encoding="utf-8", errors="replace")
        elif _is_macos():
            launcher = Path(MACOS_APP_PATH) / "Contents" / "MacOS" / "launcher"
            if launcher.exists():
                txt = launcher.read_text(encoding="utf-8", errors="replace")
        elif _is_windows():
            xml = _winsw_xml_path()
            if xml.exists():
                txt = xml.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    if not txt:
        return None, None

    def _pick(name: str) -> Optional[str]:
        # systemd `Environment=JTDT_HOST=x` / launcher `JTDT_HOST=x`
        m = _re.search(rf"{name}=([^\s\"']+)", txt)
        if m:
            return m.group(1)
        # WinSW `<env name="JTDT_HOST" value="x"/>`
        m = _re.search(rf'name="{name}"\s+value="([^"]+)"', txt)
        return m.group(1) if m else None

    return _pick("JTDT_HOST"), _pick("JTDT_PORT")


#: `0.0.0.0` / `::` 是**綁定**位址（「所有介面」），不是連得上的位址 ——
#: 印出來給人點、或拿去探測都要換成 loopback。
_WILDCARD_BINDS = ("0.0.0.0", "::", "*", "")     # noqa: S104 - 只是比對用


def _reachable_host(host: str) -> str:
    return "127.0.0.1" if host in _WILDCARD_BINDS else host


def _bind_now() -> tuple[str, str]:
    """目前生效的 host / port。

    明確匯出的環境變數優先（前景執行 `jtdt run` 就是靠它驅動的），
    其次問已安裝的服務，最後才是預設值。
    """
    svc_host, svc_port = _service_bind()
    host = os.environ.get("JTDT_HOST") or svc_host or "127.0.0.1"
    port = os.environ.get("JTDT_PORT") or svc_port or "8765"
    return host, port


def _server_url() -> str:
    """這個安裝連得上的網址（`jtdt status` / `jtdt open` 都用這個）。"""
    host, port = _bind_now()
    return f"http://{_reachable_host(host)}:{port}/"


def _health_urls() -> list[str]:
    """Every address worth probing for `healthz`, most specific first.

    `0.0.0.0` / `::` mean "every interface" — those are **bind** addresses,
    not addresses you can connect to on every platform, so translate them
    to loopback. Loopback on the configured port is always worth a try as
    well: a service bound to one LAN address still tells us it is up if we
    can reach it, and this way a wrong host guess cannot fail the check on
    its own.
    """
    host, port = _bind_now()
    urls = [f"http://{_reachable_host(host)}:{port}/healthz"]
    loopback = f"http://127.0.0.1:{port}/healthz"
    if loopback not in urls:
        urls.append(loopback)
    return urls


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


def _win_service_state() -> str:
    """Windows 服務目前的狀態字（RUNNING / STOPPED / STOP_PENDING …）。

    查不到就回空字串 —— 呼叫端要能在「問不到狀態」時仍做出保守決定。
    """
    rc, out = _run_capture(["sc.exe", "query", SERVICE_NAME])
    if rc != 0:
        return ""
    for word in ("STOP_PENDING", "START_PENDING", "RUNNING", "STOPPED",
                 "PAUSED"):
        if word in out.upper():
            return word
    return ""


def _win_wait_state(target: str, timeout_s: float = 45.0) -> bool:
    """等服務進入指定狀態；逾時回 False。

    **`sc.exe stop` 是非同步的** —— 它送出停止要求就回來了，服務還停在
    `STOP_PENDING`。緊接著下 `sc.exe start`，SCM 會回 **1056
    （ERROR_SERVICE_ALREADY_RUNNING）**，而我們原本把 1056 當成「已經在跑」
    直接回成功 —— 於是 `jtdt restart` 的結果是：**start 沒生效、服務停完就停在
    STOPPED、回傳碼 0、畫面上沒有任何訊息**（2026-08-24 在 Windows 測試機重現）。
    更新流程也走同一組函式，客戶更新完會發現服務沒起來卻查不到原因。
    """
    import time as _t
    deadline = _t.time() + timeout_s
    while _t.time() < deadline:
        if _win_service_state() == target:
            return True
        _t.sleep(1.0)
    return _win_service_state() == target


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
        # 服務還在停止中就下 start，SCM 會回 1056 而**不會**啟動它。
        # 先等它真的停下來（見 `_win_wait_state`）。
        if _win_service_state() == "STOP_PENDING":
            _win_wait_state("STOPPED")
        rc, out = _run_capture(["sc.exe", "start", SERVICE_NAME])
        if rc == 0:
            return 0
        # 1056 = ERROR_SERVICE_ALREADY_RUNNING。**只有在服務真的 RUNNING 時
        # 才算成功** —— 停止中的服務也會回 1056，早期版本把它一律當成功，
        # 結果是「restart 之後服務沒起來，回傳碼卻是 0」。
        if "1056" in out or "1056" in str(rc):
            if _win_service_state() == "RUNNING":
                return 0
            # 停止中 → 等停完再試一次
            _win_wait_state("STOPPED")
            rc, out = _run_capture(["sc.exe", "start", SERVICE_NAME])
            if rc == 0:
                return 0
        # Verify by querying — maybe sc.exe error was transient
        if _win_service_state() == "RUNNING":
            return 0
        sys.stderr.write(out)
        return rc or 1
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
        rc = _run(["sc.exe", "stop", SERVICE_NAME])
        # `sc.exe stop` 是**非同步**的：它送出要求就回來，服務仍在
        # STOP_PENDING。不等的話，緊接著的 start 會被 SCM 以 1056 擋掉
        # （見 `_win_wait_state` 的說明）。macOS 那條路徑早就有同樣的等待。
        if not _win_wait_state("STOPPED"):
            print("服務在 45 秒內沒有停止（仍在停止中）；"
                  "接下來的啟動可能會失敗", file=sys.stderr)
        return rc
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


def _find_git() -> Optional[str]:
    r"""git 執行檔的完整路徑；找不到回 None。

    **不可以只靠 `shutil.which("git")`。** Windows 上剛裝完 Git 之後，
    PATH 只更新在**登錄檔**裡 —— 已經開著的 PowerShell / Windows Terminal
    保留舊的環境區塊，**開新分頁也一樣是舊的**（分頁繼承自同一個終端機
    行程）。使用者照著「重新開啟 PowerShell」做卻還是找不到 git，只好
    重開機 —— 客戶 2026-08-23 回報的就是這一步。

    改成直接找檔案，就完全不必管 PATH 有沒有更新：
      1. PATH（最快，多數情況命中）
      2. 登錄檔 `HKLM\SOFTWARE\GitForWindows\InstallPath`（官方安裝檔會寫）
      3. 標準安裝位置

    與 `sys_deps.configure_pytesseract()` 是同一套做法（Tesseract 也踩過
    「裝了但不在 PATH」）。
    """
    found = shutil.which("git")
    if found:
        return found
    if not _is_windows():
        return None
    cands: list[Path] = []
    try:
        import winreg  # noqa: PLC0415 — Windows 專用
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r"SOFTWARE\GitForWindows") as k:
                    base = winreg.QueryValueEx(k, "InstallPath")[0]
                    cands.append(Path(base) / "cmd" / "git.exe")
            except OSError:
                pass
    except Exception:  # noqa: BLE001 — 沒有 winreg / 讀不到就往下試固定路徑
        pass
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432",
                    "LOCALAPPDATA"):
        base = os.environ.get(env_var)
        if not base:
            continue
        cands.append(Path(base) / "Git" / "cmd" / "git.exe")
        cands.append(Path(base) / "Programs" / "Git" / "cmd" / "git.exe")
    for c in cands:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
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


def _sync_windows_display_version(version: str) -> None:
    """Windows: fix the version shown in Settings -> Apps.

    The NSIS installer is a thin bootstrapper. It bakes its own build-time
    version into the Add/Remove Programs entry, but the code it installs comes
    from GitHub main -- so the two drift apart the moment a newer version is
    pushed. Measured on 2026-09-05: an installer named 1.12.82 installed
    v1.15.6 while the registry still said 1.12.82, which misleads both users
    and support.

    Fixing it here (rather than only in install_core.ps1) means every existing
    installation is corrected by its next `jtdt update`, without rebuilding
    the installer. Never raises: a wrong entry in Add/Remove Programs must not
    be able to fail an upgrade.
    """
    if not _is_windows() or not version:
        return
    try:
        import winreg  # noqa: PLC0415  (Windows-only import)

        key_path = (r"SOFTWARE\Microsoft\Windows\CurrentVersion"
                    r"\Uninstall\jt-doc-tools")
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
            try:
                current, _ = winreg.QueryValueEx(k, "DisplayVersion")
            except OSError:
                current = None
            if current != version:
                winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, version)
                print(f"  Add/Remove Programs version: {current} -> {version}")
    except Exception:      # noqa: BLE001 - never break an upgrade over this
        pass


#: 升級前備份**不必**複製的東西。判準是「弄丟了能自己長回來」——
#: 而在真實部署上這佔了絕大部分：正式機的資料目錄 2.0 GB，其中
#: **1.4 GB 是統編資料庫**（政府公開資料，排程會自己重新下載）。每次升級
#: 整份複製、還留三份，等於 4.2 GB 的純浪費；那台磁碟只剩 11 GB，再幾次
#: 更新就滿了 —— 而滿掉的時機正好是「服務已經停掉、備份寫到一半」。
_BACKUP_SKIP = {
    "vat_db.sqlite", "vat_db.sqlite-wal", "vat_db.sqlite-shm",  # 可重新下載
    "temp",          # 暫存檔，本來就有 2 小時保留期
    "jobs",          # 作業產出，本來就有 24 小時保留期
    "db_backups",    # 資料庫備份的備份
}


def _dir_size(path: Path, skip: set[str]) -> int:
    total = 0
    try:
        for entry in path.iterdir():
            if entry.name in skip:
                continue
            try:
                if entry.is_dir():
                    total += _dir_size(entry, set())
                else:
                    total += entry.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _backup_space_needed() -> tuple[int, Optional[int]]:
    """(大約要多少 bytes, 現在還有多少)。取不到就回 (0, None)。"""
    data = _data_dir()
    if not data.exists():
        return 0, None
    need = int(_dir_size(data, _BACKUP_SKIP) * 1.2)     # 兩成餘裕
    try:
        avail = shutil.disk_usage(data.parent).free
    except OSError:
        avail = None
    return need, avail


def _backup_data_dir() -> None:
    """升級前把資料目錄複製一份（保留最近三份）。"""
    import datetime
    data = _data_dir()
    if not data.exists():
        return
    backup = data.parent / f"{data.name}.backup-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    skipped = sorted(n for n in _BACKUP_SKIP if (data / n).exists())
    print(f"Backed up data: {data} -> {backup}")
    try:
        shutil.copytree(data, backup, dirs_exist_ok=False,
                        ignore=lambda _d, names: [n for n in names
                                                  if n in _BACKUP_SKIP])
    except OSError as exc:
        # 備份失敗不可以無聲 —— 使用者要知道這次升級沒有回頭路
        print(f"WARNING: backup failed ({exc}); continuing without one.",
              file=sys.stderr)
        shutil.rmtree(backup, ignore_errors=True)
        return
    if skipped:
        print("  Skipped (rebuildable / temporary): " + ", ".join(skipped))
    siblings = sorted(
        data.parent.glob(f"{data.name}.backup-*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for stale in siblings[3:]:
        print(f"  Removed old backup: {stale}")
        shutil.rmtree(stale, ignore_errors=True)


def _rollback_code(root, git_exe, git_env, pre_sha: str,
                   uv: Optional[str] = None, uv_env: Optional[dict] = None) -> str:
    """升級中途失敗 → 把**程式碼**放回升級前那個 commit，並盡量把相依環境也帶回去。

    **為什麼要有這支**（外部稽核 F03）：原本 `uv sync` 失敗時印的是
    `uv sync failed, restoring previous state`，實際只做了「把檔案擁有者改回
    去」＋「啟動服務」—— 工作樹還停在 `origin/main` 的**新程式**，配著**沒有
    同步完的相依**，然後服務就這樣起來了。**訊息承諾了一個不存在的保證**
    （跟串流逾時那次「設定說明寫單次呼叫上限、其實只管每個 chunk」同一個病）。

    回傳一句**描述實際結果**的話，給呼叫端印出來 —— 不可以再用一句籠統的
    「已回復」蓋掉三種不同的結局：

    * 程式碼與相依都回去了
    * 程式碼回去了，但相依沒辦法重新同步（離線 / 磁碟滿）→ **要講出來**
    * 連程式碼都回不去（不該發生，`pre_sha` 是幾秒前從這個 repo 取的）
    """
    if not pre_sha:
        return ("無法回復：升級前沒有取到 commit 編號。"
                "程式碼目前是新版，相依環境可能不完整。")
    try:
        rc = subprocess.call([git_exe, "-C", str(root), "reset", "--hard", pre_sha],
                             env=git_env)
    except OSError as exc:
        # git 不見了 / 不能執行 —— 這裡是「升級已經失敗」的收尾路徑，
        # **絕對不可以再丟例外**，否則使用者看到的是堆疊而不是下一步。
        return f"回復失敗：無法執行 git（{exc}）。程式碼目前是新版。"
    if rc != 0:
        return (f"回復失敗：`git reset --hard {pre_sha[:12]}` 回傳 {rc}。"
                "程式碼目前是新版，相依環境可能不完整。")
    if uv is None:
        return f"程式碼已回到升級前的版本（{pre_sha[:12]}）。"
    # 樹回到舊 commit 之後，`uv.lock` 也是舊的 → 再同步一次就會把相依帶回舊版。
    print("Re-syncing deps back to the previous lockfile ...")
    try:
        rc2 = subprocess.call([uv, "sync"], cwd=str(root),
                              env=uv_env or os.environ.copy())
    except OSError:
        rc2 = 1        # uv 檔案不見 / 不能執行 → 當成同步失敗（不可以往外丟）
    if rc2 != 0:
        return (f"程式碼已回到升級前的版本（{pre_sha[:12]}），"
                "但**相依環境沒有同步回去**（可能離線或磁碟不足）。"
                "網路恢復後請再跑一次 `jtdt update`。")
    return f"已完整回復到升級前的版本（{pre_sha[:12]}，含相依環境）。"


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
        # Tarball-installed (no git at install time, e.g. the website one-liner
        # on a box without git) → adopt the dir into a git repo in place so
        # updates work from now on. .venv / bin / data are preserved (data dir
        # lives elsewhere; untracked files survive `git reset --hard`).
        git_exe = _find_git()
        if not git_exe:
            print(f"Install dir {root} is not a git repo and git is not installed.",
                  file=sys.stderr)
            print("Install git first, then re-run the upgrade:", file=sys.stderr)
            if _is_windows():
                # **不要只講 winget。** Windows Server 2019 / 2022 沒有內建
                # winget（App Installer 不在 Server 版；Server 2025 才有，
                # Server Core 一律沒有）—— 而那正是最可能缺 git 的機器。
                # 只給 winget 等於叫人用一個他也沒有的工具，是循環死結。
                print("  下載安裝 Git for Windows："
                      "https://git-scm.com/download/win", file=sys.stderr)
                print("  （若這台有 winget，也可以："
                      "winget install --id Git.Git -e）", file=sys.stderr)
                # 裝在標準位置的話 `_find_git()` 直接找得到，不必重開終端機
                # 也不必重開機；只有裝到非標準路徑才需要讓 PATH 生效。
                print("  裝好後直接再執行一次 jtdt update 即可。"
                      "若仍找不到（裝在非標準路徑），"
                      "請重新開機讓 PATH 生效後再試。", file=sys.stderr)
            else:
                print("  sudo apt install -y git    # Debian/Ubuntu (or dnf/yum/zypper/pacman)",
                      file=sys.stderr)
                print("  sudo jtdt update", file=sys.stderr)
            return 1
        print(f"Install dir {root} is not a git repo (tarball install); adopting into git ...")
        adopt_env = _git_env_for(root)
        subprocess.call([git_exe, "-C", str(root), "init", "-q"], env=adopt_env)
        subprocess.call([git_exe, "-C", str(root), "remote", "remove", "origin"],
                        env=adopt_env, stderr=subprocess.DEVNULL)
        rc = subprocess.call(
            [git_exe, "-C", str(root), "remote", "add", "origin",
             "https://github.com/jasoncheng7115/jt-doc-tools"], env=adopt_env)
        subprocess.call([git_exe, "config", "--global", "--add", "safe.directory", str(root)],
                        env=adopt_env, stderr=subprocess.DEVNULL)
        if rc != 0 or not (root / ".git").exists():
            print("Failed to convert install dir into a git repo; re-run the install script.",
                  file=sys.stderr)
            return 1
        # falls through to the normal git fetch + reset --hard origin/main flow below

    # Capture current version
    cur = _read_version()
    print(f"Current version: v{cur}")

    # Snapshot auth_settings.json BEFORE we touch anything. If anything in
    # the upgrade flow (migration / seed / dep install / restart) somehow
    # changes it, we restore the snapshot — auth backend / LDAP server URI
    # / TLS settings must NEVER change just because of an upgrade.
    # (User concern raised at v1.5.0: 「升級完為何 ldap 登入認證不見了」)
    auth_settings_path = _data_dir() / "auth_settings.json"
    pre_auth_snapshot: Optional[bytes] = None
    if auth_settings_path.is_file():
        try:
            pre_auth_snapshot = auth_settings_path.read_bytes()
        except OSError:
            pass

    # 0b. Check there is room for the backup **before stopping the service**.
    #     Running out of disk half way through leaves the service down and a
    #     half-written backup taking up the little space that was left.
    need, avail = _backup_space_needed()
    if need and avail is not None and avail < need:
        print(f"Not enough free disk for the pre-upgrade backup: "
              f"need about {need // (1024**2)} MB, {avail // (1024**2)} MB free.",
              file=sys.stderr)
        print("Free some space (older data.backup-* directories are safe to "
              "delete) and run the upgrade again. The service was NOT stopped.",
              file=sys.stderr)
        return 1

    # 1. Stop service
    print("Stopping service ...")
    svc_stop()

    # 2. Backup data
    _backup_data_dir()

    # 3. git pull (with safe.directory so it works on differently-owned repos)
    print("Pulling latest from GitHub ...")
    git_env = _git_env_for(root)
    # 走完整路徑，不靠 PATH —— 見 `_find_git()` 的說明（剛裝完 git 的
    # Windows 上，已開著的終端機看不到新的 PATH）。
    git_exe = _find_git()
    if not git_exe:
        print("找不到 git。請安裝 Git for Windows："
              "https://git-scm.com/download/win" if _is_windows()
              else "git not found; please install git first", file=sys.stderr)
        _restore_ownership(root, owner)
        svc_start()
        return 1
    rc = subprocess.call(
        [git_exe, "-C", str(root), "fetch", "--tags", "origin"], env=git_env)
    if rc != 0:
        print("git fetch failed, restoring: starting previous service", file=sys.stderr)
        _restore_ownership(root, owner)
        svc_start()
        return rc
    # 用 fetch + reset --hard 而非 pull --ff-only：後者在 remote 被 force-push
    # (歷史重寫) 時會 abort「Not possible to fast-forward」。reset --hard 強制
    # 對齊 origin/main 是 fresh-checkout 的標準作法 — 我們不在 install 內做開發
    # commit，所以無「本地未提交變更」需要保留。
    # 先把目前 HEAD 的 SHA 記下來 — 萬一發現 origin/main 是降版，要靠 SHA 回復
    # （tag 名 `v{cur}` 不一定存在，例如本地手動 reset 過、release 沒 tag 過 …）
    pre_sha_proc = subprocess.run(
        [git_exe, "-C", str(root), "rev-parse", "HEAD"],
        env=git_env, capture_output=True, text=True)
    pre_sha = pre_sha_proc.stdout.strip() if pre_sha_proc.returncode == 0 else ""
    rc = subprocess.call(
        [git_exe, "-C", str(root), "reset", "--hard", "origin/main"], env=git_env)
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
            f"  Aborted upgrade.",
            file=sys.stderr,
        )
        # 這裡還沒動過相依（uv sync 在後面），所以只要把程式碼放回去。
        # 走同一支 `_rollback_code` —— 原本這一段自己寫了一份 reset 邏輯，
        # 而另外兩條失敗路徑**根本沒有回復**（外部稽核 F03）。
        msg = _rollback_code(root, git_exe, git_env, pre_sha)
        if "無法回復" in msg or "回復失敗" in msg:
            # SHA 回不去（不該發生）→ 最後試標籤。VERSION 有 bump 但沒打 tag
            # 的安裝上 `v{cur}` 可能不存在，所以這只是保險不是主路徑。
            subprocess.call(
                [git_exe, "-C", str(root), "reset", "--hard", f"v{cur}"],
                env=git_env)
        print("  " + msg, file=sys.stderr)
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
    # 企業 TLS 檢查設備（MITM proxy）會換掉 HTTPS 憑證；uv 預設用內建 webpki 根
    # 憑證不認那個 CA → uv sync 失敗。預設讓 uv 改用 OS 信任庫（企業 CA 通常在
    # 那裡），新版 UV_SYSTEM_CERTS / 舊版 UV_NATIVE_TLS 都設，uv 忽略不認得的那個。
    # 使用者可用環境變數覆寫（含 JTDT_TLS_INSECURE=1 最後手段停用驗證）。
    uv_env = os.environ.copy()
    uv_env.setdefault("UV_NATIVE_TLS", "true")
    uv_env.setdefault("UV_SYSTEM_CERTS", "true")
    if os.environ.get("JTDT_TLS_INSECURE") == "1":
        uv_env.setdefault("UV_INSECURE_HOST",
                          "pypi.org files.pythonhosted.org github.com "
                          "objects.githubusercontent.com astral.sh")
    rc = subprocess.call([uv, "sync"], cwd=str(root), env=uv_env)
    if rc != 0:
        print("uv sync failed.", file=sys.stderr)
        print("  " + _rollback_code(root, git_exe, git_env, pre_sha, uv, uv_env),
              file=sys.stderr)
        _restore_ownership(root, owner)
        svc_start()
        return rc

    # 4b. Smoke-test critical imports — catches the "uv said OK but actually
    # didn't install some dep" class of bug that hit the v1.1.66 customer.
    venv_py = root / ".venv" / "bin" / "python"
    if not venv_py.exists() and _is_windows():
        venv_py = root / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        print("Verifying critical deps (fastapi / fitz / ldap3 / PIL / pdfplumber / docx / odf / pyzipper / pdf2docx / rapidfuzz / defusedxml) ...")
        rc = subprocess.call([str(venv_py), "-c",
            "import fastapi, fitz, ldap3, PIL, pillow_heif, pdfplumber, docx, odf, openpyxl, pyzipper, httpx, psutil, pyotp, qrcode, pdf2docx, rapidfuzz, fontTools, numpy, lxml, pymupdf4llm, markdown_it, jwt, onelogin.saml2.auth, xmlsec, truststore, dns.resolver, defusedxml.ElementTree"])
        if rc != 0:
            print("Dep import failed — the upgrade is incomplete.", file=sys.stderr)
            print("  " + _rollback_code(root, git_exe, git_env, pre_sha, uv, uv_env),
                  file=sys.stderr)
            _restore_ownership(root, owner)
            svc_start()
            return rc
        # easyocr 軟性檢查 — 沒裝會 fallback tesseract，warn 不 die
        eo_rc = subprocess.call([str(venv_py), "-c", "import easyocr"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if eo_rc == 0:
            print("  OK: EasyOCR available (主 OCR 引擎)")
        else:
            print("  WARNING: EasyOCR 未裝（OCR 會自動 fallback tesseract，CJK 識別率較弱）",
                  file=sys.stderr)
            print("    手動補裝：sudo jtdt update  或  <venv>/bin/pip install easyocr",
                  file=sys.stderr)

    # 4a. PDF.js vendor 完整性檢查 — 隨 git 來，這裡只驗有沒漏掉檔
    pdfjs_dir = root / "static" / "vendor" / "pdfjs"
    pdfjs_required = [
        pdfjs_dir / "build" / "pdf.mjs",
        pdfjs_dir / "build" / "pdf.worker.mjs",
        pdfjs_dir / "web" / "viewer.html",
        pdfjs_dir / "web" / "viewer.mjs",
    ]
    pdfjs_missing = [str(p.relative_to(root)) for p in pdfjs_required if not p.exists()]
    if pdfjs_missing:
        print("  WARNING: PDF.js vendor 不完整（pdf-ocr 內嵌 viewer 會載不到，下載仍可）：",
              file=sys.stderr)
        for m in pdfjs_missing:
            print(f"    缺：{m}", file=sys.stderr)
    else:
        print("  OK: PDF.js vendor 完整（pdf-ocr 內嵌 viewer）")

    # 5. Restore ownership so the service user can read the new files
    _restore_ownership(root, owner)

    # 5a. Defensive heal — chown data dir back to its owner. Catches the
    # "previous CLI command (sudo jtdt auth disable / reset-password) ran
    # as root and left data/ files root-owned mode 600" trap. Without this
    # the service can't read auth_settings.json after upgrade, making it
    # look like LDAP/AD config disappeared (v1.4.2 客戶慘案).
    _chown_data_files_back()

    # 5b. Ensure system-level deps for new features (auto best-effort).
    _ensure_system_deps_for_update()

    # 5b1. Backfill UTF-8 locale env into systemd unit on Linux.
    # 舊版安裝（< v1.4.71）的 jt-doc-tools.service 沒有 LANG/LC_ALL — 客戶 host
    # 若用 LANG=C 跑（很多 minimal Debian / RHEL container 預設），上傳中文檔名
    # 字型 / 處理中文檔名 PDF 會踩 ascii encoding。新安裝 install.sh 已加入；
    # 升級時也順手補一次。Idempotent — 已有的不重加。
    if _is_linux():
        _ensure_systemd_utf8_locale()

    # 5c. Self-bootstrap: when upgrading, the NEW cli.py is on disk but
    # THIS process still has OLD cli.py in memory. So any helper that's
    # newer than what's running (esp. _migrate_nssm_to_winsw on Windows,
    # but also new _ensure_* probes added in this release) wouldn't run.
    # Spawn a child interpreter so it imports the fresh cli.py and re-runs
    # the system-deps check. Idempotent.
    if venv_py.exists():
        print("Re-running system deps check with new code ...")
        try:
            subprocess.call(
                [str(venv_py), "-c",
                 "from app.cli import _ensure_system_deps_for_update; "
                 "_ensure_system_deps_for_update()"],
            )
        except Exception as e:
            print(f"  warning: post-upgrade re-check failed: {e}",
                  file=sys.stderr)

    # 5d. Verify auth_settings.json untouched. Compare bytes with the
    # snapshot taken before svc_stop. The new code is on disk + deps
    # synced + system deps installed — if anything in that chain (incl.
    # the seed_default_auditor_user we added in v1.5.0) silently rewrote
    # auth_settings.json, restore the snapshot and warn loudly. This
    # also catches accidental future regressions.
    if pre_auth_snapshot is not None and auth_settings_path.is_file():
        try:
            now_bytes = auth_settings_path.read_bytes()
        except OSError:
            now_bytes = b""
        if now_bytes and now_bytes != pre_auth_snapshot:
            print("WARNING: auth_settings.json changed during upgrade — restoring snapshot",
                  file=sys.stderr)
            try:
                auth_settings_path.write_bytes(pre_auth_snapshot)
                _chown_data_files_back()
            except OSError as exc:
                print(f"  failed to restore: {exc}", file=sys.stderr)

    # 5. Restart
    print("Starting new version ...")
    rc = svc_start()
    if rc != 0:
        print("Service failed to start; check 'jtdt logs'", file=sys.stderr)
        return rc

    # 6. Health check
    import time
    urls = _health_urls()
    print(f"Health check ({urls[0]}) ...")
    for _ in range(15):
        for url in urls:
            try:
                with _safe_fetch.urlopen_direct(url, timeout=2) as r:
                    if r.status == 200:
                        new = _read_version()
                        _sync_windows_display_version(new)
                        print(f"Upgrade done: v{cur} -> v{new}")
                        _print_system_deps_summary()
                        return 0
            except Exception:
                pass
        time.sleep(1)
    _report_health_failure(urls)
    _print_system_deps_summary()
    return 1


def _report_health_failure(urls: list[str]) -> None:
    """Say what actually went wrong instead of only "timed out".

    "Timed out" tells the operator nothing: a crashed service, a service
    listening somewhere else and a blocked probe all print the same line,
    and the customer is left guessing. Print what we probed, whether the
    service manager thinks it is running, and the tail of the log.
    """
    print("", file=sys.stderr)
    print("Health check failed. Probed:", file=sys.stderr)
    for u in urls:
        print(f"  {u}", file=sys.stderr)
    state = ""
    if _is_linux():
        _, state = _run_capture(["systemctl", "is-active", SERVICE_NAME])
    elif _is_windows():
        state = _win_service_state()
    state = (state or "").strip()
    if state:
        print(f"Service state: {state}", file=sys.stderr)
    if state.startswith("active") or state == "RUNNING":
        print("The service is running, so this may be a wrong address or a "
              "proxy in the way rather than a broken upgrade — open the URL "
              "above in a browser to confirm.", file=sys.stderr)
    print("Last log lines:", file=sys.stderr)
    _print_log_tail(20)
    print("Full log: jtdt logs", file=sys.stderr)


def _print_log_tail(n: int) -> None:
    try:
        if _is_linux():
            _run(["journalctl", "-u", SERVICE_NAME, "--no-pager", "-n", str(n)])
            return
        log = (_real_home() / "Library" / "Logs" / "jt-doc-tools.log"
               if _is_macos() else _data_dir() / "logs" / "jt-doc-tools.log")
        if not log.exists():
            print(f"  (no log at {log})", file=sys.stderr)
            return
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-n:]:
            print(f"  {line}", file=sys.stderr)
    except Exception as e:          # noqa: BLE001 - 診斷用，不可以自己炸掉
        print(f"  (could not read the log: {e})", file=sys.stderr)


def _ensure_systemd_utf8_locale() -> None:
    """Ensure /etc/systemd/system/jt-doc-tools.service has LANG=C.UTF-8 etc.

    舊安裝（< v1.4.71）的 unit 沒有這幾行；客戶 host 預設 LANG=C 時
    Python 會把 filesystem encoding 當 ascii，上傳/處理中文檔名爆 Unicode
    錯。這裡 idempotent 補插（已存在就不動）。"""
    unit = Path("/etc/systemd/system/jt-doc-tools.service")
    if not unit.exists():
        return
    try:
        txt = unit.read_text(encoding="utf-8")
    except Exception:
        return
    needed = [
        ("LANG", "C.UTF-8"),
        ("LC_ALL", "C.UTF-8"),
        ("PYTHONIOENCODING", "utf-8"),
        ("PYTHONUTF8", "1"),
    ]
    import re as _re
    additions = []
    for key, val in needed:
        if not _re.search(rf"^Environment={_re.escape(key)}=", txt, _re.M):
            additions.append(f"Environment={key}={val}")
    if not additions:
        return  # all present
    # Insert right before the ExecStart line so it's grouped with other Environment= lines.
    new_lines = "\n".join(additions) + "\n"
    if "ExecStart=" in txt:
        txt = txt.replace("ExecStart=", new_lines + "ExecStart=", 1)
    else:
        txt = txt.rstrip() + "\n" + new_lines
    try:
        unit.write_text(txt, encoding="utf-8")
        subprocess.call(["systemctl", "daemon-reload"])
        print("  Backfilled UTF-8 locale env into systemd unit (LANG=C.UTF-8 etc).")
    except Exception as e:
        print(f"  warning: could not patch systemd unit for UTF-8 locale: {e}",
              file=sys.stderr)


def _print_system_deps_summary() -> None:
    """Print system dependency status table after upgrade.

    Each entry: (display name, detect-fn -> bool, impact description, install
    command dict). All English to ensure compatibility with Windows console
    that may not render CJK reliably.
    """
    deps = [
        (
            "tesseract OCR",
            lambda: bool(_resolve_tesseract_binary()) and _tesseract_has_lang("chi_tra"),
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
        (
            "zbar (libzbar)",
            _zbar_present,
            "einvoice-scan QR code parsing (Windows wheel bundles DLL — N/A there)",
            {
                "linux": "sudo apt install libzbar0",
                "macos": "brew install zbar",
                "windows": "(bundled in pyzbar wheel)",
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


def _resolve_tesseract_binary() -> str:
    """Find tesseract.exe even when not on PATH (very common on Windows
    when client installed UB-Mannheim build but didn't tick the PATH option,
    or winget install skipped PATH update — GitHub issue #4).

    Mirrors logic in app/core/sys_deps.py:_find_tesseract_binary so cli.py
    can be used standalone without importing the FastAPI app stack."""
    p = shutil.which("tesseract")
    if p:
        return p
    if _is_windows():
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
        ]
    elif _is_macos():
        candidates = [
            "/opt/homebrew/bin/tesseract",
            "/usr/local/bin/tesseract",
            "/opt/local/bin/tesseract",
        ]
    else:
        candidates = ["/usr/bin/tesseract", "/usr/local/bin/tesseract"]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return ""


def _tesseract_has_lang(lang: str) -> bool:
    binary = _resolve_tesseract_binary()
    if not binary:
        return False
    try:
        out = subprocess.run(
            [binary, "--list-langs"],
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


_MACOS_LIBZBAR_PATHS = (
    "/opt/homebrew/lib/libzbar.dylib",
    "/opt/homebrew/lib/libzbar.0.dylib",
    "/usr/local/lib/libzbar.dylib",
    "/usr/local/lib/libzbar.0.dylib",
)


def _zbar_present() -> bool:
    """zbar shared lib 是否可用。Windows pyzbar wheel 內建 DLL → 永遠 True。
    macOS 直接看 brew 安裝路徑（root context import 不到也算數）。
    Linux 透過 import pyzbar.pyzbar 偵測 (ctypes load 失敗會 raise)。"""
    if _is_windows():
        return True
    if _is_macos():
        # 不能用 import — sudo / root context 下 ctypes 找不到 brew lib 即使已裝
        return any(os.path.exists(p) for p in _MACOS_LIBZBAR_PATHS)
    try:
        import importlib
        importlib.import_module("pyzbar.pyzbar")
        return True
    except Exception:
        return False


def _ensure_system_deps_for_update() -> None:
    """在 jtdt update 流程中自動補裝新版需要的系統套件。

    任何錯誤都只 warn 不 raise — 升級流程不能因為某個 optional system 套件
    裝不起來就 abort。每個套件的安裝結果獨立判斷。

    新加任何系統相依套件時，請在這裡加一段 best-effort 安裝邏輯（並同步更新
    install.sh / install.ps1 對應段落）。
    """
    # tesseract OCR — pdf-editor 文字辨識 fallback (自 v1.2.2 起)
    _ensure_tesseract()
    # OxOffice / LibreOffice X11 runtime libs — Linux only (自 v1.3.15 起)
    _ensure_oxoffice_x11_libs()
    # zbar — pyzbar (einvoice-scan QR code 解析) 的 native 依賴 (自 v1.7.78 起)
    _ensure_office_impress()
    _ensure_zbar()
    # Java JRE — OxOffice/LibreOffice 部分匯入需要 (自 v1.4.40 起，客戶 v1.4.39 踩到)
    _ensure_java_runtime()
    # NSSM → WinSW 移轉 — Windows only (自 v1.4.44 起)
    if _is_windows():
        try:
            _migrate_nssm_to_winsw()
        except Exception as e:
            print(f"  warning: NSSM→WinSW migration errored: {e}", file=sys.stderr)
        # v1.7.2: vc_redist for PyTorch (EasyOCR dep)
        try:
            _ensure_vc_redist_windows()
        except Exception as e:
            print(f"  warning: vc_redist install errored: {e}", file=sys.stderr)


def _ensure_vc_redist_windows() -> None:
    """Windows only — 確保 Microsoft Visual C++ Redistributable 14.40+ 已安裝。
    PyTorch (EasyOCR 主依賴) 需要它，沒有會 c10.dll load failure。
    符合條件就跳過；舊版或缺則無提示安裝（不需 reboot — 新 process 即可載 DLL）。"""
    if not _is_windows():
        return
    import winreg
    needs_install = True
    for hive_path in (
        r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64",
        r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\X64",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, hive_path) as k:
                ver, _ = winreg.QueryValueEx(k, "Version")
                # ver looks like "v14.40.33810.00" or "v14.0.23026.00"
                import re as _re
                m = _re.match(r"^v?(\d+)\.(\d+)", str(ver))
                if m:
                    major = int(m.group(1))
                    minor = int(m.group(2))
                    if major > 14 or (major == 14 and minor >= 40):
                        print(f"  OK: VC++ Redistributable already current ({ver})")
                        return
                    print(f"  VC++ Redistributable too old ({ver}) — upgrading")
                    break
        except FileNotFoundError:
            continue
        except Exception:
            continue
    print("  Installing Microsoft Visual C++ Redistributable (PyTorch dep, ~25MB)...")
    import urllib.request
    import tempfile
    import subprocess as _sp
    tmp = Path(tempfile.gettempdir()) / "jtdt-vc_redist.x64.exe"
    try:
        _safe_fetch.urlretrieve(
            "https://aka.ms/vs/17/release/vc_redist.x64.exe", str(tmp),
        )
        rc = _sp.call([str(tmp), "/install", "/quiet", "/norestart"])
        if rc in (0, 3010):
            print(f"  OK: VC++ Redistributable installed (exit {rc} — 新 process 可正常載入，不需重啟)")
        else:
            print(f"  WARNING: vc_redist exit {rc} — EasyOCR 可能載不起，OCR 會 fallback tesseract",
                  file=sys.stderr)
    except Exception as e:
        print(f"  WARNING: vc_redist 下載 / 安裝失敗：{e}", file=sys.stderr)
        print("    手動補裝：開 https://aka.ms/vs/17/release/vc_redist.x64.exe", file=sys.stderr)


def _ensure_tesseract_chi_tra(binary: str) -> bool:
    """Backward-compat wrapper — 真正工作交給 _ensure_tesseract_core_langs。"""
    return _ensure_tesseract_core_langs(binary)


def _ensure_tesseract_core_langs(binary: str) -> bool:
    """確保 chi_tra + eng 兩個語言都有 fast + best 雙變體。

    新行為（v1.7.2+）：對每個核心語言（chi_tra / eng）下載 fast + best
    兩個變體，並把 best 設為 active 主檔。預設品質 = best。
    用 tessdata_manager.install_lang() 統一邏輯，避免重複實作。

    向後相容：若既有 tessdata 已有 single `<code>.traineddata` 但沒
    `.fast` / `.best` 變體，install_lang 內的 _download_variant 會偵測
    既有同 size 變體（不存在）→ 重新下載兩個。完成後 active 切到 best。

    Returns True if BOTH chi_tra + eng 都有可用 active variant 了。
    """
    try:
        # 用 sys.path-based import — cli.py 是 jt-doc-tools 的一部分，可以
        # 直接 import app modules
        from app.core import tessdata_manager as tm
    except Exception as e:
        print(f"  WARNING: tessdata_manager import failed: {e} — fall back to legacy fast-only download", file=sys.stderr)
        return _legacy_download_chi_tra_fast_only(binary)

    overall_ok = True
    for code in ("chi_tra", "eng"):
        try:
            print(f"  Ensuring {code} (fast + best variants)...")
            result = tm.install_lang(code, download_both=True)
            if result.ok:
                size_mb = result.bytes_written / 1024 / 1024
                if size_mb > 0.1:
                    print(f"    OK: {code} variants installed ({size_mb:.1f} MB total)")
                else:
                    print(f"    OK: {code} already present")
                if result.error:
                    print(f"    note: {result.error}")
            else:
                print(f"    WARNING: {code} install failed: {result.error}", file=sys.stderr)
                # eng 在 chi_tra 之外通常已是 tesseract 內建 — 失敗不要 abort
                if code == "chi_tra":
                    overall_ok = False
        except Exception as e:
            print(f"    WARNING: {code} install errored: {e}", file=sys.stderr)
            if code == "chi_tra":
                overall_ok = False
    return overall_ok


def _legacy_download_chi_tra_fast_only(binary: str) -> bool:
    """Pre-v1.7.2 行為：只下 chi_tra fast 一個檔。當 tessdata_manager 不可
    import 時的 last-ditch fallback（極少觸發 — cli.py 跟 app 同一 process）。"""
    try:
        out = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=5,
        )
        if "chi_tra" in (out.stdout or ""):
            return True
    except Exception:
        return False
    tessdata = Path(binary).parent / "tessdata"
    if not tessdata.exists():
        print(f"  WARNING: tessdata dir not found: {tessdata}", file=sys.stderr)
        return False
    url = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_tra.traineddata"
    dst = tessdata / "chi_tra.traineddata"
    print(f"  Downloading chi_tra.traineddata (~12MB) → {dst} ...")
    try:
        import urllib.request
        _safe_fetch.urlretrieve(url, str(dst))
        if dst.exists() and dst.stat().st_size > 1_000_000:
            print(f"  OK: chi_tra.traineddata installed ({dst.stat().st_size / 1024 / 1024:.1f} MB)")
            return True
        dst.unlink(missing_ok=True)
        print("  WARNING: chi_tra download incomplete, removed", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  WARNING: chi_tra download failed: {e}", file=sys.stderr)
        return False


def _ensure_tesseract() -> None:
    binary = _resolve_tesseract_binary()
    if binary:
        # Already installed (PATH or standard install dir) — verify chi_tra
        if _ensure_tesseract_chi_tra(binary):
            return
        # If chi_tra still missing after attempted download, fall through to
        # try platform package manager (apt may have it as separate package)
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
            # Apple Silicon 優先用 ARM brew，避免裝出 x86_64 binary
            arch = platform.machine()
            if arch == "arm64" and os.path.exists("/opt/homebrew/bin/brew"):
                brew = "/opt/homebrew/bin/brew"
            elif os.path.exists("/usr/local/bin/brew"):
                brew = "/usr/local/bin/brew"
            else:
                brew = shutil.which("brew")
            if brew:
                # brew 拒絕 root 執行 — root context 下要 drop 到 console user
                cmd = [brew, "install", "tesseract", "tesseract-lang"]
                if os.geteuid() == 0:
                    real_user = _real_user()
                    if real_user and real_user != "root":
                        cmd = ["sudo", "-u", real_user] + cmd
                    else:
                        print("  WARNING: tesseract 未安裝；Homebrew 在 root 下無法執行。",
                              file=sys.stderr)
                        print("           請以一般帳號跑：brew install tesseract tesseract-lang",
                              file=sys.stderr)
                        return
                rc = subprocess.call(cmd)
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
    if rc == 0 and _resolve_tesseract_binary():
        # Re-verify chi_tra after winget install — UB-Mannheim silent install
        # often skips it, fall back to GitHub download
        binary2 = _resolve_tesseract_binary()
        if binary2 and _ensure_tesseract_chi_tra(binary2):
            print("  OK: tesseract + chi_tra installed")
        else:
            print("  WARNING: tesseract installed but chi_tra missing  (Chinese OCR 不可用，可手動下載)",
                  file=sys.stderr)
    else:
        print("  WARNING: tesseract auto-install failed  (pdf-editor OCR disabled, rest still works)",
              file=sys.stderr)
        if _is_windows():
            print("    Download manually: https://github.com/UB-Mannheim/tesseract/wiki",
                  file=sys.stderr)


def _ensure_oxoffice_x11_libs() -> None:
    """Install X11 client libs that OxOffice / LibreOffice oosplash dlopens
    at startup, even in --headless mode. Debian/Ubuntu minimal/server lacks
    these by default, causing 'libXinerama.so.1: cannot open shared object
    file' on office-to-pdf etc. Linux-only; macOS/Windows skip silently."""
    if not _is_linux():
        return
    from .core.sys_deps import _OXOFFICE_X11_LIBS, _probe_oxoffice_x11_libs
    probe = _probe_oxoffice_x11_libs()
    if probe.get("ok"):
        return
    missing = probe.get("missing_pkgs") or [pkg for _, pkg in _OXOFFICE_X11_LIBS]
    print(f"Installing OxOffice X11 runtime libs ({len(missing)} missing) ...")
    rc = -1
    try:
        if shutil.which("apt-get"):
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
            rc = subprocess.call(
                ["apt-get", "install", "-y"] + list(missing),
                env=env,
            )
        elif shutil.which("dnf"):
            dnf_pkgs = [
                "libXinerama", "libXrandr", "libXcursor", "libXi", "libXtst",
                "libSM", "libXext", "libXrender",
                "libX11-xcb", "libXcomposite", "libXdamage", "libXfixes",
                "libxkbcommon",
                "dbus-libs", "cups-libs",
                "fontconfig", "freetype", "cairo",
                "pango", "gdk-pixbuf2", "nss",
            ]
            rc = subprocess.call(["dnf", "install", "-y"] + dnf_pkgs)
        else:
            print("  WARNING: no apt-get or dnf found; install X11 libs manually",
                  file=sys.stderr)
            return
    except Exception as e:
        print(f"  WARNING: X11 libs install errored: {e}  "
              "(office-to-pdf may fail until installed)", file=sys.stderr)
        return
    if rc == 0:
        print("  OK: X11 runtime libs installed")
    else:
        print("  WARNING: X11 libs install failed  "
              "(office-to-pdf may fail until installed manually)",
              file=sys.stderr)


def _ensure_office_impress() -> None:
    """確保 office 套件含 Impress 模組（「PDF 轉簡報」的必要條件）。

    為什麼特別處理：缺這個模組時 soffice 只回一句 "source file could not be
    loaded"，**連正常的 .odp 都載不進來**，訊息完全看不出是缺元件；而部分發行版的
    `libreoffice` meta 套件並不含 Impress，既有客戶升級後就會突然不能用。
    """
    try:
        from .core.sys_deps import _probe_office
    except Exception:  # noqa: BLE001
        return
    info = _probe_office()
    if not info.get("installed") or info.get("impress"):
        return
    flavor = info.get("flavor") or "LibreOffice"
    if flavor == "OxOffice":
        # OxOffice 的模組來自 OSSII 釋出的套件包，不在系統套件庫裡 → 無法自動補裝
        print("  WARNING: OxOffice 缺 Impress 模組，PDF 轉簡報不可用。",
              file=sys.stderr)
        print("    重跑安裝程式即可補齊："
              "https://github.com/OSSII/OxOffice/releases", file=sys.stderr)
        return
    if not _is_linux():
        print("  WARNING: LibreOffice 缺 Impress 模組，PDF 轉簡報不可用。"
              "請重新安裝 LibreOffice 並勾選 Impress。", file=sys.stderr)
        return
    print("Installing Impress module (needed by PDF to slides) ...")
    rc = -1
    try:
        if shutil.which("apt-get"):
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
            rc = subprocess.call(["apt-get", "install", "-y",
                                  "libreoffice-impress"], env=env)
        elif shutil.which("dnf"):
            rc = subprocess.call(["dnf", "install", "-y", "libreoffice-impress"])
        else:
            print("  WARNING: no apt-get or dnf; install libreoffice-impress "
                  "manually", file=sys.stderr)
            return
    except Exception as e:  # noqa: BLE001
        print(f"  WARNING: Impress module install errored: {e}", file=sys.stderr)
        return
    print("  OK: Impress module installed" if rc == 0
          else "  WARNING: Impress module install failed "
               "(PDF to slides unavailable until installed)",
          file=sys.stderr if rc != 0 else sys.stdout)


def _ensure_zbar() -> None:
    """zbar shared lib — pyzbar (einvoice-scan QR code 解析) 的 native 依賴。
    Windows pyzbar wheel 內建 DLL 不需安裝；Linux/macOS 需額外裝。
    缺則 einvoice-scan QR 掃描功能會在啟動時 503，其餘工具不受影響。"""
    if _is_windows():
        return
    # macOS 用檔案檢查（不能用 import — sudo / root 下 ctypes 找不到 brew 路徑會誤判
    # 然後跑 brew install 又會被 Homebrew 拒絕 root → 卡死）
    if _is_macos():
        existing = [p for p in _MACOS_LIBZBAR_PATHS if os.path.exists(p)]
        if existing:
            # 已裝；補 /usr/local/lib 內 symlink 讓 service 啟動時 ctypes 找得到
            _ensure_macos_zbar_symlink(existing[0])
            return
        # 沒裝 — 嘗試 brew install（注意：brew 在 root 下會拒絕）
        if os.geteuid() == 0:
            print("  WARNING: zbar 未安裝；Homebrew 在 root 下無法執行。",
                  file=sys.stderr)
            print("           請以一般帳號跑：brew install zbar", file=sys.stderr)
            return
        if shutil.which("brew"):
            print("Installing zbar via Homebrew for einvoice-scan QR scanning ...")
            rc = subprocess.call(["brew", "install", "zbar"])
            if rc == 0:
                # 補 symlink 給未來 service 啟動用
                for p in _MACOS_LIBZBAR_PATHS:
                    if os.path.exists(p):
                        _ensure_macos_zbar_symlink(p)
                        break
                print("  OK: zbar installed")
            else:
                print("  WARNING: zbar install failed — einvoice-scan QR scanning disabled",
                      file=sys.stderr)
        else:
            print("  WARNING: Homebrew not found — install zbar manually: brew install zbar",
                  file=sys.stderr)
        return
    # Linux：原本邏輯（import 偵測 + apt/dnf 安裝）
    try:
        import importlib
        importlib.import_module("pyzbar.pyzbar")
        return  # import 成功 = zbar 可用
    except Exception:
        pass
    if _is_linux():
        if shutil.which("apt-get"):
            print("Installing zbar (libzbar0) for einvoice-scan QR scanning ...")
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
            rc = subprocess.call(["apt-get", "install", "-y", "libzbar0"], env=env)
            if rc == 0:
                print("  OK: zbar installed")
            else:
                print("  WARNING: zbar install failed — einvoice-scan QR scanning disabled",
                      file=sys.stderr)
        elif shutil.which("dnf"):
            print("Installing zbar for einvoice-scan QR scanning ...")
            rc = subprocess.call(["dnf", "install", "-y", "zbar"])
            if rc == 0:
                print("  OK: zbar installed")
            else:
                print("  WARNING: zbar install failed — einvoice-scan QR scanning disabled",
                      file=sys.stderr)
        else:
            print("  WARNING: no apt-get/dnf — install zbar (libzbar0) manually",
                  file=sys.stderr)


def _ensure_macos_zbar_symlink(src: str) -> None:
    """確保 /usr/local/lib/libzbar.dylib symlink 存在，讓 service 啟動時 ctypes 找得到。
    Apple Silicon brew 裝在 /opt/homebrew，預設 dyld search path 沒含；symlink 一份到
    /usr/local/lib（在預設搜尋內）就解決。已存在或建立失敗都不出提示 — 不影響主流程。"""
    target_dir = "/usr/local/lib"
    target = os.path.join(target_dir, "libzbar.dylib")
    if os.path.exists(target) or os.path.islink(target):
        return
    try:
        if not os.path.isdir(target_dir):
            os.makedirs(target_dir, exist_ok=True)
        os.symlink(src, target)
        # 順便建一個 .0.dylib 給有些 ctypes 版本找
        target0 = os.path.join(target_dir, "libzbar.0.dylib")
        if not os.path.exists(target0) and not os.path.islink(target0):
            os.symlink(src, target0)
        print(f"  OK: linked {src} → {target}")
    except (OSError, PermissionError):
        # /usr/local/lib 需要 root；非 root 跑 jtdt update 時略過 (qr_decoder 的
        # ctypes pre-load shim 會兜底)
        pass


def _ensure_java_runtime() -> None:
    """OxOffice / LibreOffice 在處理含 macro 的舊 .doc / .xls 或部分 ODF 公式時
    呼叫 javaldx 確認 JRE 路徑。沒裝 JRE 會 abort 並印
    'javaldx: Could not find a Java Runtime Environment!'.
    Linux only — macOS/Windows 通常已內建或會跟著 OxOffice 安裝套件帶上。"""
    if not _is_linux():
        return
    if shutil.which("java"):
        return
    print("Installing Java runtime (default-jre-headless) for OxOffice ...")
    rc = -1
    try:
        if shutil.which("apt-get"):
            env = os.environ.copy()
            env["DEBIAN_FRONTEND"] = "noninteractive"
            rc = subprocess.call(
                ["apt-get", "install", "-y", "default-jre-headless"],
                env=env,
            )
        elif shutil.which("dnf"):
            rc = subprocess.call(
                ["dnf", "install", "-y", "java-21-openjdk-headless"])
            if rc != 0:
                rc = subprocess.call(
                    ["dnf", "install", "-y", "java-17-openjdk-headless"])
        else:
            print("  WARNING: no apt-get or dnf found; install Java JRE manually",
                  file=sys.stderr)
            return
    except Exception as e:
        print(f"  WARNING: Java JRE install errored: {e}  "
              "(office-to-pdf 部分 docx/xls 可能失敗)", file=sys.stderr)
        return
    if rc == 0:
        print("  OK: Java JRE installed")
    else:
        print("  WARNING: Java JRE install failed  "
              "(office-to-pdf 部分 docx/xls 可能失敗，請手動 sudo apt install default-jre-headless)",
              file=sys.stderr)


# ===========================================================================
# Windows Service wrapper management — WinSW from v1.4.44, NSSM before that
# ===========================================================================
#
# Why WinSW now: NSSM 2.24 (2014) is unmaintained, nssm.cc serves intermittent
# 503/404 (GitHub issues #1, #3), and various AVs flag it as PUA. WinSW (v2)
# is MIT-licensed, GitHub-hosted, actively maintained, and used by Jenkins
# etc. — better trust, better availability.
#
# Migration rule: customers upgrading from NSSM hit `_migrate_nssm_to_winsw`
# during `jtdt update`; we capture their env vars (JTDT_HOST/PORT/DATA_DIR)
# from the NSSM registry, remove the old service, and re-register via WinSW
# preserving the same service name "jt-doc-tools" so external integrations
# (sc.exe, monitoring, etc.) keep working.

_WINSW_BUNDLED_SHA256 = (
    "b5066b7bbdfba1293e5d15cda3caaea88fbeab35bd5b38c41c913d492aadfc4f"
)
_WINSW_RELEASE_URL = (
    "https://github.com/winsw/winsw/releases/download/"
    "v2.12.0/WinSW.NET461.exe"
)


def _winsw_exe_path() -> Path:
    """`bin/jtdt-svc.exe` — WinSW renamed; basename must match the .xml."""
    return _install_root() / "bin" / "jtdt-svc.exe"


def _winsw_xml_path() -> Path:
    return _install_root() / "bin" / "jtdt-svc.xml"


def _nssm_exe_path() -> Path:
    """Legacy NSSM wrapper path — used only for migration detection / cleanup."""
    return _install_root() / "bin" / "nssm.exe"


def _log_dir_windows() -> Path:
    return Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "jt-doc-tools" / "Logs"


def _detect_service_wrapper() -> str:
    """Return 'winsw', 'nssm', 'sc', or 'none' based on what's on disk
    and registered as the SCM binary path. Windows-only."""
    if not _is_windows():
        return "none"
    # If the service isn't registered, no wrapper.
    rc, _ = _run_capture(["sc.exe", "query", SERVICE_NAME])
    if rc != 0:
        return "none"
    # Read SCM binary path
    rc, qc = _run_capture(["sc.exe", "qc", SERVICE_NAME])
    if rc != 0:
        return "unknown"
    qc_lower = qc.lower()
    if "jtdt-svc.exe" in qc_lower or "winsw" in qc_lower:
        return "winsw"
    if "nssm.exe" in qc_lower:
        return "nssm"
    return "sc"


def _read_nssm_env_vars() -> dict[str, str]:
    """Read NSSM AppEnvironmentExtra from the registry. Returns dict of
    var_name → value. Empty dict if nothing found / not Windows."""
    if not _is_windows():
        return {}
    try:
        import winreg  # type: ignore
    except ImportError:
        return {}
    out: dict[str, str] = {}
    # NSSM stores env at HKLM\SYSTEM\CurrentControlSet\Services\<svc>\Parameters\AppEnvironmentExtra
    key_path = rf"SYSTEM\CurrentControlSet\Services\{SERVICE_NAME}\Parameters"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
            try:
                # AppEnvironmentExtra is REG_MULTI_SZ ("KEY=VAL" lines)
                val, _ = winreg.QueryValueEx(k, "AppEnvironmentExtra")
                if isinstance(val, list):
                    for line in val:
                        if "=" in line:
                            kk, _, vv = line.partition("=")
                            out[kk.strip()] = vv.strip()
            except FileNotFoundError:
                pass
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  warning: cannot read NSSM env from registry: {e}",
              file=sys.stderr)
    return out


def _write_winsw_xml(host: str = "127.0.0.1", port: int = 8765,
                     data_dir: Optional[Path] = None) -> None:
    """Generate the WinSW service config. Caller is responsible for service
    install/start. Path / values are XML-escaped via str.translate."""
    if not _is_windows():
        return
    root = _install_root()
    log_dir = _log_dir_windows()
    py = root / ".venv" / "Scripts" / "python.exe"
    data = data_dir if data_dir is not None else _data_dir()

    def _xe(s: str) -> str:
        # Minimal XML escape — paths shouldn't contain &/</>/quotes but be safe
        return (s.replace("&", "&amp;")
                 .replace("<", "&lt;")
                 .replace(">", "&gt;")
                 .replace('"', "&quot;"))

    xml = f"""<service>
  <id>{_xe(SERVICE_NAME)}</id>
  <name>Jason Tools Document Toolbox</name>
  <description>Jason Tools Document Toolbox - PDF / Office processing</description>
  <executable>{_xe(str(py))}</executable>
  <arguments>-m app.main</arguments>
  <workingdirectory>{_xe(str(root))}</workingdirectory>
  <log mode="roll-by-size">
    <sizeThreshold>5120</sizeThreshold>
    <keepFiles>5</keepFiles>
  </log>
  <logpath>{_xe(str(log_dir))}</logpath>
  <env name="JTDT_DATA_DIR" value="{_xe(str(data))}"/>
  <env name="JTDT_HOST" value="{_xe(host)}"/>
  <env name="JTDT_PORT" value="{port}"/>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="20 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <startmode>Automatic</startmode>
</service>
"""
    log_dir.mkdir(parents=True, exist_ok=True)
    _winsw_xml_path().parent.mkdir(parents=True, exist_ok=True)
    _winsw_xml_path().write_text(xml, encoding="utf-8")


def _ensure_winsw_binary() -> bool:
    """Make sure bin/jtdt-svc.exe exists and SHA256 matches.

    Tries (in order): existing bin/jtdt-svc.exe (verify hash), bundled
    packaging/windows/winsw.exe (copy + verify), GitHub release download.
    Returns True on success, False if all paths failed."""
    if not _is_windows():
        return False
    target = _winsw_exe_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    def _verify(p: Path) -> bool:
        try:
            import hashlib
            h = hashlib.sha256(p.read_bytes()).hexdigest()
            return h.lower() == _WINSW_BUNDLED_SHA256
        except Exception:
            return False

    if target.exists() and _verify(target):
        return True

    bundled = _install_root() / "packaging" / "windows" / "winsw.exe"
    if bundled.exists() and _verify(bundled):
        try:
            shutil.copy2(bundled, target)
            return True
        except Exception as e:
            print(f"  warning: copy bundled winsw.exe failed: {e}",
                  file=sys.stderr)

    # Last resort: download from GitHub Release.
    print(f"  Downloading WinSW from {_WINSW_RELEASE_URL} ...")
    try:
        import urllib.request
        with _safe_fetch.urlopen(_WINSW_RELEASE_URL, timeout=30) as r:
            data = r.read()
        target.write_bytes(data)
        if _verify(target):
            return True
        print("  ERROR: downloaded winsw.exe SHA256 mismatch", file=sys.stderr)
        target.unlink(missing_ok=True)
    except Exception as e:
        print(f"  ERROR: WinSW download failed: {e}", file=sys.stderr)
    return False


def _migrate_nssm_to_winsw() -> bool:
    """If the service is currently NSSM-wrapped, switch it to WinSW while
    preserving env vars. Idempotent — safe to call when already on WinSW.
    Returns True if migration ran successfully (or wasn't needed).

    Steps:
      1. Detect wrapper (nssm vs winsw vs none)
      2. If nssm: capture env vars from registry
      3. Stop service
      4. nssm.exe remove (cleans registry)
      5. Ensure jtdt-svc.exe binary exists + SHA-verified
      6. Write XML with captured env vars
      7. jtdt-svc.exe install + start
      8. Verify Running, then remove legacy nssm.exe
    """
    if not _is_windows():
        return True
    wrapper = _detect_service_wrapper()
    if wrapper == "winsw":
        # Already migrated; refresh XML + restart so new fields take effect.
        return True
    if wrapper == "none":
        # No service registered at all — fresh install, install.ps1 owns this.
        return True
    if wrapper not in ("nssm", "sc", "unknown"):
        print(f"  warning: unknown service wrapper '{wrapper}', "
              "skipping migration", file=sys.stderr)
        return False

    print(f"Migrating Windows Service wrapper: {wrapper} → WinSW ...")

    # 1+2: capture env vars (best-effort — fall back to defaults if unreadable)
    captured = _read_nssm_env_vars()
    host = captured.get("JTDT_HOST") or "127.0.0.1"
    port_str = captured.get("JTDT_PORT") or "8765"
    try:
        port = int(port_str)
    except ValueError:
        port = 8765
    data_dir_str = captured.get("JTDT_DATA_DIR")
    data_dir = Path(data_dir_str) if data_dir_str else None
    print(f"  Preserving env: JTDT_HOST={host} JTDT_PORT={port} "
          f"JTDT_DATA_DIR={data_dir or '(default)'}")

    # 3: stop service
    print("  Stopping current service ...")
    subprocess.call(["sc.exe", "stop", SERVICE_NAME],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    import time as _t
    _t.sleep(2)

    # 4: remove via nssm if available, else via sc.exe
    nssm = _nssm_exe_path()
    if wrapper == "nssm" and nssm.exists():
        print("  Removing NSSM-wrapped service ...")
        rc = subprocess.call(
            [str(nssm), "remove", SERVICE_NAME, "confirm"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if rc != 0:
            # Fallback to sc.exe — NSSM remove can fail mid-AV-scan
            subprocess.call(["sc.exe", "delete", SERVICE_NAME],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    else:
        subprocess.call(["sc.exe", "delete", SERVICE_NAME],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _t.sleep(1)

    # 5: ensure WinSW binary
    if not _ensure_winsw_binary():
        print("  ERROR: cannot place verified jtdt-svc.exe; aborting migration. "
              "Run install.ps1 again or restore manually.", file=sys.stderr)
        return False

    # 6: write XML
    _write_winsw_xml(host=host, port=port, data_dir=data_dir)
    print(f"  Wrote {_winsw_xml_path()}")

    # 7: install + start
    winsw = _winsw_exe_path()
    rc = subprocess.call([str(winsw), "install"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc != 0:
        print(f"  ERROR: WinSW install failed (rc={rc})", file=sys.stderr)
        return False
    rc = subprocess.call([str(winsw), "start"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc != 0:
        print(f"  WARNING: WinSW start returned rc={rc}; "
              "service may need manual start", file=sys.stderr)
    _t.sleep(2)

    # 8: verify + clean up legacy nssm.exe
    rc, out = _run_capture(["sc.exe", "query", SERVICE_NAME])
    if rc != 0 or "RUNNING" not in out.upper():
        print(f"  WARNING: service not in RUNNING state after migration. "
              f"Check {_log_dir_windows()}\\{SERVICE_NAME}.wrapper.log",
              file=sys.stderr)
        # Don't remove nssm.exe — keep for fallback diagnosis
        return False
    if nssm.exists():
        try:
            nssm.unlink()
            print("  Removed legacy nssm.exe")
        except Exception:
            # File may still be locked by SCM right after service uninstall.
            # Queue for removal on next reboot via MoveFileEx; harmless to
            # leave behind otherwise. Wraps Win32 API via ctypes.
            try:
                import ctypes
                MOVEFILE_DELAY_UNTIL_REBOOT = 4
                ok = ctypes.windll.kernel32.MoveFileExW(
                    str(nssm), None, MOVEFILE_DELAY_UNTIL_REBOOT)
                if ok:
                    print("  nssm.exe still in use; queued for removal on next reboot")
                else:
                    print("  nssm.exe still present (in use); will be cleaned later",
                          file=sys.stderr)
            except Exception:
                pass
    print(f"  Migration complete. Service '{SERVICE_NAME}' running via WinSW.")
    return True


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
        # WinSW knows how to clean its own SCM entry + log files; fall back
        # to sc.exe delete if the binary's missing (manual install / partial state).
        winsw = _winsw_exe_path()
        if winsw.exists():
            _run([str(winsw), "stop"])
            _run([str(winsw), "uninstall"])
        else:
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
        # v1.4.44+: re-write the WinSW XML and restart. Fall back to manual
        # instructions if the XML/binary aren't where we expect.
        # NB: cli.py never imports `re` at module scope; import locally so
        # the Windows branch doesn't NameError (issue #16, v1.7.8).
        import re
        xml_path = _winsw_xml_path()
        winsw = _winsw_exe_path()
        if xml_path.exists() and winsw.exists():
            try:
                # Read existing host/port from the XML so we only patch what changed
                txt = xml_path.read_text(encoding="utf-8")
                cur_host = "127.0.0.1"
                cur_port = 8765
                m = re.search(r'name="JTDT_HOST"\s+value="([^"]+)"', txt)
                if m:
                    cur_host = m.group(1)
                m = re.search(r'name="JTDT_PORT"\s+value="([^"]+)"', txt)
                if m:
                    try:
                        cur_port = int(m.group(1))
                    except ValueError:
                        pass
                final_host = new_host if new_host is not None else cur_host
                final_port = new_port if new_port is not None else cur_port
                _write_winsw_xml(host=final_host, port=final_port)
                print(f"  Updated {xml_path}: JTDT_HOST={final_host} JTDT_PORT={final_port}")
                print("Restarting service ...")
                subprocess.call(["sc.exe", "stop", SERVICE_NAME],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                import time as _t
                _t.sleep(2)
                subprocess.call(["sc.exe", "start", SERVICE_NAME],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return 0
            except Exception as e:
                print(f"  WinSW XML update failed: {e}; manual instructions follow:",
                      file=sys.stderr)
        print("Edit WinSW XML on Windows (run as Administrator):", file=sys.stderr)
        print(f"  notepad {xml_path}")
        if new_host is not None:
            print(f'    change <env name="JTDT_HOST" value="..."/> to "{new_host}"')
        if new_port is not None:
            print(f'    change <env name="JTDT_PORT" value="..."/> to "{new_port}"')
        print(f"  sc.exe stop {SERVICE_NAME}; sc.exe start {SERVICE_NAME}")
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
    rc = subprocess.call(cmd)
    # ⚠ 以 root 寫資料目錄之後**一定要把擁有者改回去**。這支是「被鎖在門外」
    # 時的救命指令，而它跑起來是 root：只要它建出任何一個新檔案
    # （audit.sqlite、WAL、備份目錄…），服務帳號接下來就會拿到
    # `attempt to write a readonly database` —— 等於救援完之後反而登不進去。
    _chown_data_files_back()
    return rc


# --------------------------------------------------------------------- auth recovery (offline)

def _data_dir_owner() -> Optional[tuple[int, int]]:
    """Return (uid, gid) of the data dir, or None on Windows / dir missing.
    Used to chown CLI-written files back to the service user — without this
    `sudo jtdt auth disable` writes auth_settings.json as root:root mode 600
    and the service (running as `jtdt`) can never read it again, ending up
    showing default placeholders instead of saved settings (v1.4.2 bug)."""
    if _is_windows():
        return None
    try:
        d = _data_dir()
        if not d.exists():
            return None
        st = d.stat()
        return (st.st_uid, st.st_gid)
    except Exception:
        return None


def _chown_data_files_back() -> None:
    """Recursively chown the entire data dir to the dir's own owner. Called
    after any CLI helper that writes to data/ as root, to undo the
    "root-owned 600 file the service can't read" trap. Idempotent."""
    owner = _data_dir_owner()
    if not owner:
        return
    uid, gid = owner
    if uid == 0:
        return  # data dir is root-owned anyway, no fix needed
    try:
        subprocess.call(["chown", "-R", f"{uid}:{gid}", str(_data_dir())])
    except Exception as exc:
        print(f"Warning: failed to chown data dir back: {exc}", file=sys.stderr)


def _run_auth_helper(snippet: str) -> int:
    """Run a Python snippet inside the install venv with the data-dir env
    set up. Used for offline auth-recovery commands (disable-auth, show-auth)
    so they don't require the web service to be running.

    IMPORTANT: When invoked via sudo, this process runs as root. Any file the
    snippet writes (auth_settings.json, auth.sqlite, etc.) will end up
    root-owned mode 600 — the service user (jtdt) then can't read it and
    saved settings disappear from the web UI (v1.4.2 慘案). We chown the
    data dir back to its original owner after every run as defence.
    """
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
    rc = subprocess.call([str(venv_python), "-c", header + snippet])
    # Always chown back — even if snippet failed mid-write, we don't want
    # half-written files to be unreadable by the service.
    _chown_data_files_back()
    return rc


def svc_auth_show() -> int:
    """Print the current auth backend + brief settings (no secrets)."""
    return _run_auth_helper(
        "from app.core import auth_settings\n"
        "s = auth_settings.get()\n"
        "backend = s.get('backend', 'off')\n"
        "labels = {'off': 'disabled', 'local': 'local', 'ldap': 'LDAP', 'ad': 'Active Directory'}\n"
        "print(f'Auth backend: {backend} ({labels.get(backend, backend)})')\n"
        "if backend in ('ldap', 'ad'):\n"
        "    d = s.get('ldap', {}) or {}\n"
        "    print(f'  Server URI:  {d.get(\"server_url\", \"(unset)\")}')\n"
        "    print(f'  Search Base: {d.get(\"user_search_base\", \"(unset)\")}')\n"
        "    print(f'  Bind DN:     {d.get(\"service_dn\", \"(unset)\")}')\n"
        "    print(f'  TLS:         {d.get(\"use_tls\", False)}')\n"
        "    print(f'  Verify cert: {d.get(\"verify_cert\", False)}')\n"
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
        "    print('Already off; no change needed.'); raise SystemExit(0)\n"
        "auth_settings.disable_auth(actor='cli', ip='localhost')\n"
        "print(f'OK: switched from {before} to off. Restart the service: jtdt restart')\n"
    )


def svc_db_check(thorough: bool = False) -> int:
    """Integrity-check every managed SQLite database.

    Offline by design: when auth.sqlite is damaged the web UI is unreachable,
    so the CLI is the only way in.
    """
    return _run_auth_helper(
        "from app.core import db_health\n"
        f"th = {bool(thorough)!r}\n"
        "rows = db_health.check_all(thorough=th,\n"
        "                           max_bytes=None if th else db_health._STARTUP_MAX_BYTES)\n"
        "bad = 0\n"
        "for r in rows:\n"
        "    if not r['exists']:\n"
        "        print('  %-20s -  not created yet' % r['file']); continue\n"
        "    mark = 'SKIPPED' if r.get('skipped') else ('OK' if r['ok'] else 'DAMAGED')\n"
        "    size = r['size_bytes'] / 1024.0 / 1024.0\n"
        "    print('  %-20s %-8s %7.1f MB  backups=%d  %s'\n"
        "          % (r['file'], mark, size, r['backups'],\n"
        "             '' if r['ok'] else r['detail'][:60]))\n"
        "    if not r['ok']:\n"
        "        bad += 1\n"
        "        print('      impact : %s' % r['impact_en'])\n"
        "        print('      recover: jtdt db-restore %s' % r['file'])\n"
        "raise SystemExit(1 if bad else 0)\n"
    )


def svc_db_backup() -> int:
    """Create a hot backup (VACUUM INTO) of the critical databases."""
    return _run_auth_helper(
        "from app.core import db_health\n"
        "rep = db_health.backup_all()\n"
        "for name in rep['created']:\n"
        "    print('  created %s' % name)\n"
        "for sk in rep['skipped']:\n"
        "    print('  SKIPPED %s - %s' % (sk['file'], sk['reason'][:60]))\n"
        "if rep['removed']:\n"
        "    print('  rotated out %d old backup(s)' % rep['removed'])\n"
        "if not rep['created'] and not rep['skipped']:\n"
        "    print('  nothing to back up')\n"
        "raise SystemExit(1 if rep['skipped'] else 0)\n"
    )


def svc_db_restore(db_name: str, backup: str = "") -> int:
    """Restore a database from its newest (or a named) backup."""
    return _run_auth_helper(
        "from app.core import db_health\n"
        f"name = {db_name!r}\n"
        f"bk = {backup!r} or None\n"
        "cands = db_health.list_backups(name)\n"
        "if not bk and not cands:\n"
        "    print('no backup found for %s' % name); raise SystemExit(1)\n"
        "res = db_health.restore(name, bk)\n"
        "if not res['ok']:\n"
        "    print('FAILED: %s' % res['error']); raise SystemExit(1)\n"
        "print('restored from %s' % res['restored_from'])\n"
        "if res.get('previous_saved_as'):\n"
        "    print('previous file kept at %s' % res['previous_saved_as'])\n"
        "print('NOTE: restart the service for it to take effect '\n"
        "      '(jtdt restart)')\n"
    )


def svc_db_backups(db_name: str = "") -> int:
    """List available backups."""
    return _run_auth_helper(
        "from app.core import db_health\n"
        f"only = {db_name!r}\n"
        "names = [only] if only else [m['file'] for m in db_health.MANAGED]\n"
        "found = 0\n"
        "for n in names:\n"
        "    for b in db_health.list_backups(n):\n"
        "        print('  %-40s %8.1f MB' % (b.name, b.stat().st_size/1048576.0))\n"
        "        found += 1\n"
        "if not found:\n"
        "    print('  no backups yet (run: jtdt db-backup)')\n"
    )


def svc_audit_user_create(username: str, password: Optional[str] = None,
                           display_name: Optional[str] = None) -> int:
    """Create a new local user with the `auditor` role + force TOTP 2FA.

    Auditor 角色用途：唯讀存取稽核紀錄與檔案歷史，不能用工具、不能改設定。
    符合郵件歸檔（mail archive）風格的職責分離 — 確保「看記錄的人」與「管系統的人」
    分開，admin 也看得到記錄但無法停用稽核員的 2FA 強制。

    Steps:
    1. Validate username format + uniqueness in users table
    2. Prompt password twice (or take --password)
    3. Create user with source=local, password_hash=scrypt(pw), totp_required=1
    4. Assign 'auditor' role to that user (subject_roles)
    5. Print next-steps (login + 2FA setup flow)
    """
    if not _is_admin():
        print("Audit user creation requires admin privileges: sudo jtdt audit-user create <name>",
              file=sys.stderr)
        return 1

    install_root = _install_root()
    venv_python = install_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print(f"venv python not found: {venv_python}", file=sys.stderr)
        return 1

    helper = f"""
import sys, getpass, time
import os
os.environ.setdefault('JTDT_DATA_DIR', {repr(str(_data_dir()))})
sys.path.insert(0, {repr(str(install_root))})

from app.core import auth_db, passwords, audit_db, db, roles as _roles
auth_db.init()
audit_db.init()
_roles.seed_builtin_roles()  # ensure 'auditor' role exists

username = sys.argv[1].strip()
preset_pw = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
display_name = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else username

import re
from app.core import safe_fetch as _safe_fetch
if not re.fullmatch(r'[A-Za-z][A-Za-z0-9._-]{{1,30}}', username):
    print('Invalid username — letters/digits/._- only, 2-31 chars, must start with a letter',
          file=sys.stderr)
    sys.exit(2)

conn = auth_db.conn()
exists = conn.execute(
    "SELECT 1 FROM users WHERE username=? AND source='local'", (username,)
).fetchone()
if exists:
    print(f'User {{username!r}} already exists (local). Use reset-password to update.',
          file=sys.stderr)
    sys.exit(3)

if preset_pw:
    pw1 = preset_pw
else:
    pw1 = getpass.getpass(f'Password for new audit user {{username}}: ')
    pw2 = getpass.getpass('Confirm password: ')
    if pw1 != pw2:
        print('Passwords do not match', file=sys.stderr)
        sys.exit(4)

ok, err = passwords.validate_password(pw1)
if not ok:
    print(err, file=sys.stderr); sys.exit(5)

# Make sure 'auditor' role exists (seed_builtin_roles ran already)
role = conn.execute("SELECT 1 FROM roles WHERE id='auditor'").fetchone()
if not role:
    print('auditor role not seeded — please run jtdt restart first then retry',
          file=sys.stderr); sys.exit(6)

now = time.time()
new_hash = passwords.hash_password(pw1)
with db.tx(conn):
    cur = conn.execute(
        "INSERT INTO users(username, display_name, password_hash, source, "
        "enabled, is_admin_seed, created_at, last_login_at, "
        "totp_secret, totp_enabled, totp_required) "
        "VALUES (?,?,?,'local',1,0,?,0,NULL,0,1)",
        (username, display_name, new_hash, now),
    )
    uid = cur.lastrowid
    conn.execute(
        "INSERT INTO subject_roles(subject_type, subject_key, role_id) "
        "VALUES ('user', ?, 'auditor')",
        (str(uid),),
    )

audit_db.log_event(
    'user_create', username='cli', target=username,
    details={{'source': 'local', 'role': 'auditor', 'totp_required': True}},
)
print(f'OK: created auditor user {{username!r}} (id={{uid}})')
print('  Next: user logs in at /login → automatically forced to /2fa-verify')
print('  → scan QR code with TOTP app → enter 6-digit code → done.')
print('  Auditor can ONLY view: /admin/audit /admin/history/* /admin/uploads /admin/system-status')
print('  Cannot use tools, cannot change settings, cannot disable own 2FA.')
"""
    cmd = [str(venv_python), "-c", helper, username, password or "", display_name or ""]
    rc = subprocess.call(cmd)
    _chown_data_files_back()
    return rc


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
        "    print('Already on local backend.'); raise SystemExit(0)\n"
        "s = auth_settings.get()\n"
        "s['backend'] = 'local'\n"
        "auth_settings.save(s)\n"
        "# Wipe sessions so old LDAP/AD cookies don't carry over\n"
        "from app.core import db\n"
        "conn = auth_db.conn()\n"
        "with db.tx(conn):\n"
        "    conn.execute('DELETE FROM sessions')\n"
        "print(f'OK: switched from {before} to local. Restart: jtdt restart')\n"
        "print('  To reset admin password, run: sudo jtdt reset-password jtdt-admin')\n"
    )


def svc_ocr_lang_list() -> int:
    """印 OCR 訓練檔狀態表 — 給 admin 在 console 看安裝情況。"""
    from app.core import tessdata_manager as tm
    catalog = tm.catalog_with_status()
    default_q = tm.get_default_quality()
    td = tm.get_tessdata_dir()
    print(f"tessdata dir: {td or '(not found)'}")
    print(f"default quality: {default_q}")
    print()
    print(f"{'code':<10}{'name':<14}{'fast':<10}{'best':<10}{'active':<10}")
    print("-" * 56)
    for c in catalog:
        fast = f"{c['fast_size_mb']:.0f}MB" if c['fast_installed'] else "-"
        best = f"{c['best_size_mb']:.0f}MB" if c['best_installed'] else "-"
        active = c['active_variant'] if c['installed'] else "-"
        # 將中文名 padding 對齊（中文字算 2 個 cell width）
        name = c['name']
        cjk_pad = 14 - sum(2 if ord(ch) > 127 else 1 for ch in name)
        print(f"{c['code']:<10}{name}{' ' * max(0, cjk_pad)}{fast:<10}{best:<10}{active:<10}")
    return 0


def svc_ocr_lang_install(code: str, only: Optional[str] = None) -> int:
    """安裝 OCR 訓練檔，預設下 fast + best 兩變體。only='fast'/'best' 限單變體。"""
    from app.core import tessdata_manager as tm
    if only and only not in ("fast", "best"):
        print(f"ERROR: --only must be 'fast' or 'best' (got {only!r})", file=sys.stderr)
        return 2
    if not tm.is_valid_lang_code(code):
        print(f"ERROR: invalid lang code {code!r}; supported: "
              f"{', '.join(i['code'] for i in tm.LANG_CATALOG)}", file=sys.stderr)
        return 2
    if not tm.can_write_tessdata():
        h = tm.platform_install_hint(code)
        print(f"ERROR: {h['message']}", file=sys.stderr)
        for m in h.get("methods", []):
            print(f"  {m['name']}: {m['command']}", file=sys.stderr)
        return 1
    print(f"Installing {code} ({'both fast+best' if not only else only})...")
    result = tm.install_lang(code, variant=only, download_both=(only is None))
    if result.ok:
        size_mb = result.bytes_written / 1024 / 1024
        print(f"OK: {code} installed ({size_mb:.1f}MB) → {result.path}")
        if result.error:
            print(f"  note: {result.error}")
        return 0
    print(f"FAIL: {result.error}", file=sys.stderr)
    return 1


def svc_ocr_lang_remove(code: str) -> int:
    from app.core import tessdata_manager as tm
    if not tm.is_valid_lang_code(code):
        print(f"ERROR: invalid lang code {code!r}", file=sys.stderr)
        return 2
    result = tm.uninstall_lang(code)
    if result.ok:
        print(f"OK: {code} removed")
        return 0
    print(f"FAIL: {result.error}", file=sys.stderr)
    return 1


def svc_ocr_lang_switch(code: str, quality: str) -> int:
    """切換單一語言的 active 變體（fast / best）。"""
    from app.core import tessdata_manager as tm
    if quality not in ("fast", "best"):
        print(f"ERROR: quality must be 'fast' or 'best' (got {quality!r})", file=sys.stderr)
        return 2
    if not tm.is_valid_lang_code(code):
        print(f"ERROR: invalid lang code {code!r}", file=sys.stderr)
        return 2
    result = tm.switch_active_quality(code, quality)
    if result.ok:
        print(f"OK: {code} active → {quality}")
        return 0
    print(f"FAIL: {result.error}", file=sys.stderr)
    return 1


def svc_ocr_lang_quality(quality: str) -> int:
    """設 OCR 全域預設 quality（fast / best）。同時把所有兩變體都齊的 lang 改 active。"""
    from app.core import tessdata_manager as tm
    if quality not in ("fast", "best"):
        print(f"ERROR: quality must be 'fast' or 'best' (got {quality!r})", file=sys.stderr)
        return 2
    if tm.set_default_quality(quality):
        print(f"OK: default quality → {quality}")
        print("    （所有兩變體都已下載的 lang 已自動切到 {})".format(quality))
        return 0
    print("FAIL: could not save setting", file=sys.stderr)
    return 1


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

    # 企業 TLS 攔截環境：CLI 的下載（tessdata 等）也走 OS 原生信任庫。
    try:
        from .core.net_ssl import install_os_trust
        install_os_trust()
    except Exception:
        pass

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

    p_audit = sub.add_parser(
        "audit-user",
        help="管理稽核員（auditor）帳號 — 唯讀稽核紀錄 + 強制 2FA",
    )
    audit_sub = p_audit.add_subparsers(dest="audit_cmd", required=True)
    p_au_create = audit_sub.add_parser(
        "create", help="建立新本機稽核員帳號（強制 2FA）",
    )
    p_au_create.add_argument("username", help="新稽核員帳號（英數開頭，2-31 字）")
    p_au_create.add_argument("--password",
                              help="預設密碼（避免互動 prompt；不建議在共享機器用）")
    p_au_create.add_argument("--display-name", default=None,
                              help="顯示名稱（沒給就用 username）")

    # OCR 訓練檔管理（fleet/headless 場景的 admin/ocr-langs CLI 替代）
    p_dbc = sub.add_parser("db-check",
                           help="check database integrity (works offline)")
    p_dbc.add_argument("--thorough", action="store_true",
                       help="full integrity_check instead of quick_check")
    sub.add_parser("db-backup", help="hot-backup the critical databases")
    p_dbl = sub.add_parser("db-backups", help="list available backups")
    p_dbl.add_argument("db", nargs="?", default="",
                       help="e.g. auth.sqlite (default: all)")
    p_dbr = sub.add_parser("db-restore",
                           help="restore a database from backup (offline)")
    p_dbr.add_argument("db", help="e.g. auth.sqlite")
    p_dbr.add_argument("--backup", default="",
                       help="specific backup file (default: newest)")

    p_ocr = sub.add_parser("ocr-lang", help="OCR 訓練檔管理（同 admin/ocr-langs 的 CLI 版）")
    ocr_sub = p_ocr.add_subparsers(dest="ocr_cmd", required=True)
    ocr_sub.add_parser("list", help="列所有支援語言 + 安裝狀態")
    p_ol_inst = ocr_sub.add_parser("install", help="安裝語言（預設下 fast + best 兩變體）")
    p_ol_inst.add_argument("code", help="語言碼（如 chi_sim、jpn）")
    p_ol_inst.add_argument("--only", choices=["fast", "best"], default=None,
                            help="只下單一變體（預設 fast + best 都下）")
    p_ol_rm = ocr_sub.add_parser("remove", help="移除語言（fast + best 兩變體都刪）")
    p_ol_rm.add_argument("code", help="語言碼")
    p_ol_sw = ocr_sub.add_parser("switch", help="切換單一語言的 active 變體")
    p_ol_sw.add_argument("code", help="語言碼")
    p_ol_sw.add_argument("quality", choices=["fast", "best"], help="切到 fast 或 best")
    p_ol_q = ocr_sub.add_parser("quality", help="設 OCR 全域預設品質（fast / best）")
    p_ol_q.add_argument("quality", choices=["fast", "best"], help="預設品質")

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
    if args.cmd == "db-check":
        return svc_db_check(args.thorough)
    if args.cmd == "db-backup":
        return svc_db_backup()
    if args.cmd == "db-backups":
        return svc_db_backups(args.db)
    if args.cmd == "db-restore":
        return svc_db_restore(args.db, args.backup)
    if args.cmd == "audit-user":
        if args.audit_cmd == "create":
            return svc_audit_user_create(
                args.username, args.password, args.display_name,
            )
        return 1
    if args.cmd == "ocr-lang":
        # 這幾支會以 root 寫 `ocr_settings.json`（就在資料目錄裡）。收尾一定
        # 要把擁有者改回去 —— 這些指令有好幾個 return 點，逐個補一定會漏，
        # 所以在派送這一層統一做。
        try:
            if args.ocr_cmd == "list":
                return svc_ocr_lang_list()
            if args.ocr_cmd == "install":
                return svc_ocr_lang_install(args.code, only=args.only)
            if args.ocr_cmd == "remove":
                return svc_ocr_lang_remove(args.code)
            if args.ocr_cmd == "switch":
                return svc_ocr_lang_switch(args.code, args.quality)
            if args.ocr_cmd == "quality":
                return svc_ocr_lang_quality(args.quality)
            return 1
        finally:
            _chown_data_files_back()
    return table[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())

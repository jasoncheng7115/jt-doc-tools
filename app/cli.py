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


def svc_update() -> int:
    """Pull latest release and re-sync deps. Backups data dir first."""
    if not _is_admin():
        print("升級需要系統管理員權限。", file=sys.stderr)
        if _is_windows():
            print("請以「以系統管理員身分執行 PowerShell」後再跑 jtdt update", file=sys.stderr)
        else:
            print("請改用：sudo jtdt update", file=sys.stderr)
        return 1

    root = _install_root()
    if not (root / ".git").exists():
        print(f"安裝目錄 {root} 不是 git repo，無法 git pull", file=sys.stderr)
        print(f"請重新跑安裝腳本以升級", file=sys.stderr)
        return 1

    # Capture current version
    cur = _read_version()
    print(f"目前版本：v{cur}")

    # 1. Stop service
    print("停止服務 ...")
    svc_stop()

    # 2. Backup data
    import datetime
    data = _data_dir()
    if data.exists():
        backup = data.parent / f"{data.name}.backup-{datetime.datetime.now():%Y%m%d-%H%M%S}"
        print(f"備份資料：{data} → {backup}")
        shutil.copytree(data, backup, dirs_exist_ok=False)
        # Keep only last 3 backups
        siblings = sorted(
            data.parent.glob(f"{data.name}.backup-*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in siblings[3:]:
            print(f"  清掉舊備份：{stale}")
            shutil.rmtree(stale, ignore_errors=True)

    # 3. git pull
    print("從 GitHub 拉新版 ...")
    rc = _run(["git", "-C", str(root), "fetch", "--tags", "origin"])
    if rc != 0:
        print("git fetch 失敗，回滾：啟動原服務", file=sys.stderr)
        svc_start()
        return rc
    rc = _run(["git", "-C", str(root), "pull", "--ff-only", "origin", "main"])
    if rc != 0:
        print("git pull 失敗（可能本地有未提交變更），回滾", file=sys.stderr)
        svc_start()
        return rc

    # 4. uv sync
    uv = shutil.which("uv") or str(root / "bin" / "uv")
    if not Path(uv).exists() and not shutil.which("uv"):
        print("找不到 uv 指令，無法同步依賴", file=sys.stderr)
        svc_start()
        return 1
    print("同步 Python 依賴（uv sync）...")
    rc = subprocess.call([uv, "sync"], cwd=str(root))
    if rc != 0:
        print("uv sync 失敗，回滾", file=sys.stderr)
        svc_start()
        return rc

    # 5. Restart
    print("啟動新版服務 ...")
    rc = svc_start()
    if rc != 0:
        print("服務啟動失敗，請查 jtdt logs", file=sys.stderr)
        return rc

    # 6. Health check
    import time
    import urllib.request
    print("健康檢查 ...")
    url = _server_url() + "healthz"
    for _ in range(15):
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    new = _read_version()
                    print(f"升級完成：v{cur} → v{new}")
                    return 0
        except Exception:
            time.sleep(1)
    print("健康檢查超時，請檢查 jtdt logs", file=sys.stderr)
    return 1


def svc_uninstall(purge: bool) -> int:
    if not _is_admin():
        print("解除安裝需要系統管理員權限。", file=sys.stderr)
        return 1
    print("停止並移除服務 ...")
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
            print(f"移除指令：{shim}")
            try:
                shim.unlink()
            except Exception:
                pass

    print(f"移除程式：{root}")
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
            print(f"清除資料：{data}")
            shutil.rmtree(data, ignore_errors=True)
        # Also wipe the rotation backups (jtdt update creates these alongside).
        for bk in sorted(data.parent.glob(f"{data.name}.backup-*")):
            print(f"清除備份：{bk}")
            shutil.rmtree(bk, ignore_errors=True)
        # If the parent dir is now empty (Linux: /var/lib/jt-doc-tools/),
        # remove it too — leaving an empty dir owned by the (about-to-be-
        # removed) jtdt user just looks abandoned.
        try:
            if data.parent.exists() and not any(data.parent.iterdir()):
                data.parent.rmdir()
                print(f"清除空目錄：{data.parent}")
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
                    print(f"保留 jtdt 系統使用者（仍有檔案：{leftover.strip()}）")
                else:
                    _run(["userdel", "jtdt"])
                    print("移除 jtdt 系統使用者")
            except KeyError:
                pass  # user doesn't exist, nothing to do
            except Exception as e:
                print(f"移除 jtdt 使用者失敗：{e}", file=sys.stderr)
    elif data.exists():
        print(f"資料保留：{data}（要一起清除請加 --purge）")
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
        print("變更設定需要系統管理員權限：sudo jtdt bind ...", file=sys.stderr)
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
        print("用法：sudo jtdt bind <addr>[:port]  例如  sudo jtdt bind 0.0.0.0", file=sys.stderr)
        return 2

    changed = []

    if _is_linux():
        unit = Path("/etc/systemd/system/jt-doc-tools.service")
        if not unit.exists():
            print(f"找不到 systemd unit：{unit}", file=sys.stderr)
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
            print("沒變更（可能已經是這個值）"); return 0
        unit.write_text(txt)
        for c in changed: print(f"  {c}")
        print("重新載入 systemd + 重啟服務 ...")
        _run(["systemctl", "daemon-reload"])
        _run(["systemctl", "restart", "jt-doc-tools"])
        return 0

    if _is_macos():
        launcher = Path(MACOS_APP_PATH) / "Contents" / "MacOS" / "launcher"
        if not launcher.exists():
            print(f"找不到 launcher：{launcher}", file=sys.stderr)
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
            print("沒變更（可能已經是這個值）"); return 0
        launcher.write_text(txt)
        for c in changed: print(f"  {c}")
        print("重啟服務 ...")
        svc_stop()
        svc_start()
        return 0

    if _is_windows():
        print("Windows 請改用 NSSM：", file=sys.stderr)
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
        print("重設密碼需要系統管理員權限：sudo jtdt reset-password <username>",
              file=sys.stderr)
        return 1

    install_root = _install_root()
    venv_python = install_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print(f"找不到 venv python: {venv_python}", file=sys.stderr)
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
    print(f"使用者 {{username!r}} 不存在或不是 local 帳號（LDAP/AD 使用者請改密碼於目錄端）",
          file=sys.stderr)
    sys.exit(2)

if preset:
    pw1 = preset
else:
    pw1 = getpass.getpass(f"輸入 {{username}} 的新密碼：")
    pw2 = getpass.getpass("再輸入一次確認：")
    if pw1 != pw2:
        print("兩次輸入不一致", file=sys.stderr)
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
print(f"✓ 已重設密碼：{{username}}（user_id={{row['id']}}）")
print(f"   所有現有 session 已失效，鎖定計數已歸零。")
"""
    cmd = [str(venv_python), "-c", helper, username]
    if new_password:
        cmd.append(new_password)
    return subprocess.call(cmd)


# --------------------------------------------------------------------- argparse

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jtdt", description="Jason Tools 文件工具箱 control")
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
    return table[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())

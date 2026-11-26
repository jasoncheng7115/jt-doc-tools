"""作業系統升級換掉系統 Python 之後（例如 Ubuntu 22.04 → 24.04，3.10 → 3.12）。

舊的 Linux 安裝把 venv 建在系統 Python 上（正式機就是：`.venv/bin/python -> /usr/bin/python3`，
pyvenv.cfg 寫 3.10.12）。系統 Python 換了之後套件全部看不到，服務每次啟動都失敗，記錄裡看不出原因。

* 服務一啟動就檢查，對不上就寫一行講清楚（`venv_check`，在任何第三方套件 import 之前）。
* `jtdt update` 在 Linux 上把 Python 放在安裝目錄裡；**環境好好的就不動**，壞了才用安裝目錄裡的
  Python 3.12 重建。實測 uv 的行為（2026-10-08，uv 0.11.27）：
  - `UV_PYTHON_PREFERENCE=only-managed` 會把**正常的**系統 Python 環境也重建（每台都重新下載所有相依）；
  - 環境壞掉時不設 `UV_PYTHON_INSTALL_DIR`，`uv sync` 拿 **root 家目錄**裡的 Python 重建 —— 服務帳號讀不到。
* `install.sh` 全新安裝就用安裝目錄裡的 Python。
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tools.repo_paths import public_root

from app import cli, venv_check

ROOT = Path(__file__).resolve().parent.parent
CUR = "%d.%d" % sys.version_info[:2]
OTHER = "3.10" if CUR != "3.10" else "3.11"


#: 假環境的 `home`：**不可以用跑測試的那個 Python 的目錄** —— CI 上它在 `/home/runner/…`，
#: 剛好被「Python 在某人家目錄裡」那條判成壞的（2026-10-08 只有 CI 紅）。`home` 只拿來比路徑，不會去執行。
_NEUTRAL_HOME = "/usr/local/lib/jtdt-test-python/bin"


def _fake_venv(where: Path, built: str, home: str = _NEUTRAL_HOME) -> Path:
    """一個 venv：bin/python 指到這個測試正在跑的 Python，pyvenv.cfg 寫 `built` 那一版。"""
    venv = where / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(
        f"home = {home}\nimplementation = CPython\n"
        f"version_info = {built}.12\ninclude-system-site-packages = false\n", encoding="utf-8")
    (venv / "bin" / "python").symlink_to(Path(sys.executable).resolve())
    return venv


pytestmark = pytest.mark.skipif(os.name == "nt", reason="venv 的形狀照 Linux / macOS")


# ------------------------------------------------------------------ 服務啟動時的檢查

def test_mismatch_is_detected(tmp_path):
    venv = _fake_venv(tmp_path, OTHER)
    assert venv_check.mismatch(str(venv), "/usr", sys.version_info) == (OTHER, CUR)
    assert venv_check.mismatch(str(_fake_venv(tmp_path / "ok", CUR)), "/usr", sys.version_info) is None
    assert venv_check.mismatch("/usr", "/usr") is None, "不在 venv 裡不檢查"


def test_the_service_stops_with_a_clear_message(tmp_path):
    """真的用那個 venv 的 python 跑一次：結束碼 78、訊息講出兩個版本與 `sudo jtdt update`。

    這條**真的執行**假環境的 python，`home` 要是那個 Python 真正的目錄（找得到標準函式庫）；
    這裡只比版本、不看「家目錄」那條，所以 CI 上在 `/home/runner` 也沒關係。"""
    real = str(Path(sys.executable).resolve().parent)
    venv = _fake_venv(tmp_path, OTHER, home=real)
    code = f"import sys; sys.path.insert(0, {str(ROOT)!r}); import app.venv_check as v; v.exit_if_mismatched()"
    out = subprocess.run([str(venv / "bin" / "python"), "-c", code], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == venv_check.EXIT_CODE, (out.returncode, out.stderr)
    assert f"Python {OTHER}" in out.stderr and f"now {CUR}" in out.stderr and "sudo jtdt update" in out.stderr
    ok = _fake_venv(tmp_path / "ok", CUR, home=real)
    out = subprocess.run([str(ok / "bin" / "python"), "-c", code], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr


def test_main_checks_before_any_third_party_import():
    """檢查要在 `import fastapi` 之前 —— 放在後面的話，對不上時根本走不到那一行。"""
    tree = ast.parse((ROOT / "app/main.py").read_text(encoding="utf-8"))
    first_third_party = guard_call = None
    for n, node in enumerate(tree.body):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and \
                getattr(node.value.func, "id", "") == "_venv_guard":
            guard_call = n
        if isinstance(node, (ast.Import, ast.ImportFrom)) and first_third_party is None:
            mod = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            level = getattr(node, "level", 0)
            if not level and mod and mod.split(".")[0] not in sys.stdlib_module_names \
                    and mod != "__future__":
                first_third_party = n
    assert guard_call is not None, "app.main 沒有呼叫 venv 檢查"
    assert first_third_party is not None and guard_call < first_third_party
    src = (ROOT / "app/venv_check.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mod = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
            assert mod == "__future__" or mod.split(".")[0] in sys.stdlib_module_names, \
                f"venv_check 只能用標準函式庫：{mod}"


# ------------------------------------------------------------------ jtdt update

def test_cli_sees_the_broken_environment(tmp_path):
    _fake_venv(tmp_path, OTHER)
    assert cli._venv_python_mismatch(tmp_path) == (OTHER, CUR)
    ok = tmp_path / "ok"
    _fake_venv(ok, CUR)
    assert cli._venv_python_mismatch(ok) is None
    (ok / ".venv" / "bin" / "python").unlink()
    assert cli._venv_python_mismatch(ok)[1] == "missing"
    assert cli._venv_python_mismatch(tmp_path / "nothing") is None, "還沒建環境不算壞"


def test_update_keeps_a_healthy_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_is_linux", lambda: True)
    _fake_venv(tmp_path, CUR)
    env, args = cli._python_for_sync(tmp_path, {"PATH": "/usr/bin"})
    assert env["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "python"), "Python 要放在安裝目錄裡"
    assert env["UV_PYTHON_PREFERENCE"] == "managed", "only-managed 會把正常的環境也重建"
    assert args == []


def test_update_rebuilds_a_broken_environment_with_the_private_python(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_is_linux", lambda: True)
    _fake_venv(tmp_path, OTHER)
    env, args = cli._python_for_sync(tmp_path, {})
    assert env["UV_PYTHON_INSTALL_DIR"] == str(tmp_path / "python")
    assert env["UV_PYTHON_PREFERENCE"] == "only-managed"
    assert args == ["--python", cli.MANAGED_PYTHON]
    out = capsys.readouterr().out
    assert OTHER in out and "downloaded again" in out, "要講出為什麼會比平常久"


def test_other_platforms_are_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_is_linux", lambda: False)
    _fake_venv(tmp_path, OTHER)
    env, args = cli._python_for_sync(tmp_path, {"X": "1"})
    assert env == {"X": "1"} and args == []


def test_update_actually_uses_it():
    src = (ROOT / "app/cli.py").read_text(encoding="utf-8")
    body = src[src.index("def svc_update"):]
    assert re.search(r"uv_env, py_args = _python_for_sync\(root, uv_env\)", body)
    assert re.search(r'subprocess\.call\(\[uv, "sync", (?:"--no-dev", )?\*py_args\]', body)


# ------------------------------------------------------------------ install.sh

def _install_sh() -> str:
    from tools.repo_paths import public_root
    return (public_root(ROOT) / "install.sh").read_text(encoding="utf-8")


def _venv_ok_rc(install_dir: Path) -> int:
    src = _install_sh()
    fn = src[src.index("venv_ok() {"):]
    fn = fn[:fn.index("\n}\n") + 3]
    script = f'INSTALL_DIR={str(install_dir)!r}\n{fn}\nvenv_ok'
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=30).returncode


def test_install_sh_detects_the_broken_environment(tmp_path):
    good, bad = tmp_path / "good", tmp_path / "bad"
    _fake_venv(good, CUR)
    _fake_venv(bad, OTHER)
    assert _venv_ok_rc(good) == 0
    assert _venv_ok_rc(bad) != 0
    assert _venv_ok_rc(tmp_path / "none") != 0, "全新安裝（沒有 venv）"


def test_install_sh_uses_the_private_python_only_when_needed():
    src = _install_sh()
    fn = src[src.index("setup_python() {"):]
    fn = fn[:fn.index('"$INSTALL_DIR/bin/uv" sync')]
    block = fn[fn.index('if [ "$PLATFORM" = "linux" ]; then'):]
    assert 'export UV_PYTHON_INSTALL_DIR="$INSTALL_DIR/python"' in block
    ok_branch, bad_branch = block.split("else", 1)
    assert "UV_PYTHON_PREFERENCE=managed" in ok_branch and "only-managed" not in ok_branch
    assert "only-managed" in bad_branch and 'UV_EXTRA_ARGS="--python 3.12"' in bad_branch


def test_a_python_in_someones_home_counts_as_broken(tmp_path, monkeypatch):
    """舊版 `jtdt update` 用 root 跑 `uv sync`，可能拿 `/root/.local/share/uv/python` 重建：
    root 跑得動（所以版本檢查是好的），服務帳號讀不到 —— 也要重建。"""
    monkeypatch.setattr(cli, "_is_linux", lambda: True)
    _fake_venv(tmp_path, CUR, home="/root/.local/share/uv/python/cpython-3.12/bin")
    assert cli._venv_python_mismatch(tmp_path) == (CUR, "home-dir")
    assert _venv_ok_rc(tmp_path) != 0, "install.sh 也要認得"
    other = tmp_path / "u"
    _fake_venv(other, CUR, home="/home/someone/.local/share/uv/python/cpython-3.12/bin")
    assert cli._venv_python_mismatch(other) == (CUR, "home-dir")


# ---------------- Windows ----------------
# venv 的底層 Python 原本在**安裝者**的 %APPDATA%\uv\python（服務用 SYSTEM 跑照樣讀得到，
# 但那個帳號的設定檔一被刪掉，服務就起不來）。`jtdt update` 在 Windows 修不了這件事：
# jtdt.cmd 跑的就是 venv 裡的 python，環境正被它自己佔著。所以修在 setup-python.cmd ——
# 全新安裝與重跑安裝程式都會用到它，而它本來就 `uv venv --clear` 重建環境。

def _setup_cmd() -> str:
    from tools.repo_paths import public_root
    return (public_root(ROOT) / "setup-python.cmd").read_text(encoding="utf-8")


def test_setup_python_cmd_puts_python_in_the_install_dir():
    lines = [l.strip() for l in _setup_cmd().splitlines()
             if l.strip() and not l.strip().upper().startswith("REM")]
    sets = [i for i, l in enumerate(lines)
            if re.match(r'set\s+"?UV_PYTHON_INSTALL_DIR=%INSTALL_DIR%\\python"?$', l, re.I)]
    installs = [i for i, l in enumerate(lines) if re.search(r'"\s+python\s+install\b', l)]
    venvs = [i for i, l in enumerate(lines) if re.search(r'"\s+venv\b', l)]
    assert installs and venvs, "找不到 uv python install / uv venv —— 腳本改寫了，這條要跟著改"
    assert sets, "要把 UV_PYTHON_INSTALL_DIR 設到 %INSTALL_DIR%\\python"
    assert sets[0] < installs[0], "UV_PYTHON_INSTALL_DIR 要在 uv python install 之前設"
    assert sets[0] < venvs[0], "建 venv 之前就要設，不然 venv 指到安裝者自己的 Python"
    assert "--clear" in lines[venvs[0]], "重跑安裝程式要重建環境，舊安裝才會搬過去"


def test_the_private_python_dir_is_not_tracked_by_git():
    """`git status` 才不會多一整個未追蹤的目錄（同步腳本會重寫 .gitignore，規則要寫在它裡面）。"""
    sync = ROOT / "sync-to-github.sh"
    if not sync.is_file():
        pytest.skip("公開 clone 沒有同步腳本")
    assert "\n/python/\n" in sync.read_text(encoding="utf-8")


# ------------------------------------------------------------------ 太舊的 Python（拿不到安全性修正）

def _venv_reporting(where: Path, ver: str) -> Path:
    """一個 venv：建的版本＝現在的版本＝`ver`（bin/python 是只會回報版本的小腳本）。"""
    venv = where / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text(
        f"home = {_NEUTRAL_HOME}\nimplementation = CPython\nversion_info = {ver}.12\n"
        "include-system-site-packages = false\n", encoding="utf-8")
    py = venv / "bin" / "python"
    py.write_text(f"#!/bin/sh\necho {ver}\n", encoding="utf-8")
    py.chmod(0o755)
    return venv


def test_update_rebuilds_an_environment_on_a_too_old_python(tmp_path, monkeypatch, capsys):
    """Pillow 13 起要 3.11+、12.x 沒有修補版（2026-10-20）：建在系統 Python 3.10 上的環境
    （例如 Ubuntu 22.04 的舊安裝）更新時改用安裝目錄裡的 3.12 重建一次。專案本身仍支援 3.10。"""
    monkeypatch.setattr(cli, "_is_linux", lambda: True)
    _venv_reporting(tmp_path, "3.10")
    assert cli._venv_python_mismatch(tmp_path) == ("3.10", "3.10 (too old)")
    env, args = cli._python_for_sync(tmp_path, {})
    assert env["UV_PYTHON_PREFERENCE"] == "only-managed" and args == ["--python", cli.MANAGED_PYTHON]
    out = capsys.readouterr().out
    assert "3.10" in out and "security" in out and "downloaded again" in out
    assert out.isascii(), "CLI 訊息一律英文 ASCII"
    for ok in ("3.11", "3.12", "3.13"):
        d = tmp_path / ok
        _venv_reporting(d, ok)
        assert cli._venv_python_mismatch(d) is None, ok


def test_install_sh_rebuilds_an_environment_on_a_too_old_python(tmp_path):
    for ver, want in (("3.10", 1), ("3.9", 1), ("3.11", 0), ("3.12", 0)):
        d = tmp_path / ver
        _venv_reporting(d, ver)
        assert (_venv_ok_rc(d) != 0) == bool(want), ver


def test_the_environment_needs_python_3_11_but_a_3_10_host_can_still_install():
    """使用者 2026-10-22：不再用 Python 3.10 建環境（Pillow 12.3 累積 28 則沒有修補版的公告，critical 5 則）。
    主機只有 3.10 照樣能裝（2026-10-21：不要停止支援 3.10）：Linux 安裝與更新用安裝目錄裡的 Python 3.12。"""
    import re
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.11"
    pins = [d for d in data["project"]["dependencies"] if re.match(r"(?i)pillow\s*[<>=;]", d)]
    assert pins == ["Pillow>=13.0.0,<14"], pins
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.findall(r"(?mi)^pillow\s*[<>=;].*$", req) == ["Pillow>=13.0.0,<14"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    assert lock["requires-python"] == ">=3.11"
    assert [p["version"] for p in lock["package"] if p["name"] == "pillow"] == ["13.0.0"]
    sh = (public_root(ROOT) / "install.sh").read_text(encoding="utf-8")
    assert 'UV_PYTHON_INSTALL_DIR="$INSTALL_DIR/python"' in sh and 'UV_EXTRA_ARGS="--python 3.12"' in sh
    assert cli.MANAGED_PYTHON == "3.12" and cli.MIN_SECURE_PYTHON == (3, 11)

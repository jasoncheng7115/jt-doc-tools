"""Windows ARM64 用 x64 的 Python 建環境（issue #60）。

## 由來

Surface Pro（Windows ARM64）全新安裝停在 `uv sync`：`opencv-python-headless` 沒有 `win_arm64`
的 wheel（PyTorch 也沒有），原生 ARM64 的 Python 裝不起來。Windows 11 on ARM 內建 x64 模擬，
x64 的 Python 與所有相依都有 `win_amd64` wheel —— 安裝與升級都用 x64 的 Python 3.12 建環境。

GUI 安裝程式與一行安裝指令都呼叫 repo 裡的 `setup-python.cmd`（安裝當下從 GitHub 抓），
所以只要改這一支，不用重新簽安裝程式。`jtdt update` 在 Windows 沿用既有的環境，
環境本身是 x64 的，之後的升級照樣裝得起來。

實測（2026-11-19，Windows 11 x64 測試機強制走 ARM64 分支）：x64 Python 3.12、`uv sync` 成功、
主要模組（含 cv2）與 EasyOCR 都載得起來；不強制時照舊。手邊沒有 ARM64 實機。
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.repo_paths import public_root

ROOT = Path(__file__).resolve().parents[1]
CMD = public_root(ROOT) / "setup-python.cmd"


def _text() -> str:
    return CMD.read_bytes().decode("utf-8")


def test_arm64_uses_the_x64_python_for_install_and_venv():
    t = _text()
    assert re.search(r'set "HOST_ARCH=%PROCESSOR_ARCHITEW6432%"', t), "32 位元的 NSIS 呼叫時要看 W6432"
    assert re.search(r'if not defined HOST_ARCH set "HOST_ARCH=%PROCESSOR_ARCHITECTURE%"', t)
    m = re.search(r'if /i "%HOST_ARCH%"=="ARM64" \((.*?)\r?\n\)', t, re.S)
    assert m and 'set "PY_REQ=cpython-3.12-windows-x86_64-none"' in m.group(1)
    assert '"%UV_EXE%" python install !PY_REQ!' in t
    assert '"%UV_EXE%" venv --clear --python !PY_REQ! .venv' in t
    assert 'set "PY_REQ=3.12"' in t, "其他機器照舊用 3.12（uv 依本機架構挑）"
    # 不可以殘留寫死版本的舊寫法（只改了一處的話 venv 會用回原生 ARM64 的 Python）
    assert not re.search(r'python install 3\.12\s*$', t, re.M)
    assert "venv --clear --python 3.12 " not in t


def test_uv_sync_does_not_pass_python():
    """`uv sync` 不帶 --python：它用 .venv 的 Python（x64）決定要裝哪個平台的 wheel。"""
    line = next(ln for ln in _text().splitlines() if '" sync ' in ln)
    assert "--python" not in line


def test_still_crlf():
    b = CMD.read_bytes()
    assert b.count(b"\r\n") == b.count(b"\n"), "cmd 批次檔要 CRLF（無 git 的安裝跑 LF 會壞）"

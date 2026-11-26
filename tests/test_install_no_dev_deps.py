"""客戶機不裝開發用的套件群組（v1.17.56，使用者 2026-11-25「做」）。

`pyproject.toml` 的 `[dependency-groups] dev`（pytest、bandit…）只有跑測試才要；`uv sync` 預設會一起裝，
上課機（照客戶的方式安裝）實際看到 pytest 在服務的 Python 環境裡。安裝與 `jtdt update` 一律 `uv sync --no-dev`；
Docker 原本就是。檢查每一個會真的同步的 `uv sync` 呼叫都帶 `--no-dev`（`uv sync --help` 是查參數，不算）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _pub(rel: str) -> Path:
    """公開樹裡的檔案（開發樹與 clone 下來的樹結構不同，一律經 `public_root()`）。"""
    from tools.repo_paths import public_root
    return public_root() / rel


def _sync_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        code = line.split("#", 1)[0] if not line.lstrip().upper().startswith("REM") else ""
        # 只認真的呼叫（`.../bin/uv" sync`、`"%UV_EXE%" sync`、行首或 `&&` 之後的 `uv sync`），日誌訊息裡的字不算
        if re.search(r'(bin/uv"|uv\.exe"|UV_EXE%"|^\s*uv|&&\s*uv|;\s*uv)\s+sync\b', code) and "--help" not in code:
            out.append(line.strip())
    return out


FILES = ["install.sh", "setup-python.cmd", "Dockerfile"]


def test_installers_sync_without_dev_group():
    for rel in FILES:
        lines = _sync_lines(_pub(rel).read_text(encoding="utf-8"))
        assert lines, f"{rel} 找不到 uv sync —— 換了寫法要更新這支測試"
        bad = [l for l in lines if "--no-dev" not in l]
        assert not bad, f"{rel} 的 uv sync 沒帶 --no-dev：{bad}"


def test_jtdt_update_syncs_without_dev_group():
    src = (ROOT / "app" / "cli.py").read_text(encoding="utf-8")
    calls = re.findall(r'\[uv,\s*"sync"[^\]]*\]', src)
    real = [c for c in calls if '"--help"' not in c]
    assert len(real) >= 2, "更新與失敗還原兩處"
    assert all('"--no-dev"' in c for c in real), real


def test_dev_group_still_declared_for_developers():
    """開發機照樣 `uv sync` 會裝 pytest（README 的開發說明不動）。"""
    pp = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[dependency-groups]" in pp and '"pytest' in pp

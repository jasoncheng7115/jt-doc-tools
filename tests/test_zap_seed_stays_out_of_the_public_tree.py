"""ZAP 掃描的種子腳本不可以進公開樹。

`tools/run_zap_scan.py` 掃描時會寫一支種子腳本（建拋棄式管理員、發 session），裡面有那個拋棄式
管理員的隨機密碼。原本寫在 `tools/` 底下，而 `tools/` 會同步進公開樹 —— 2026-11-14 掃描跑到一半時
同步，這支就被推上去了（拋棄式實例早就刪了，密碼沒有用處，但不該出現在公開 repo）。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_seed_script_is_written_into_the_throwaway_data_dir():
    src = (ROOT / "tools" / "run_zap_scan.py").read_text(encoding="utf-8")
    assert 'seed = tmp / "_zap_seed.py"' in src, "種子腳本要寫在拋棄式實例的暫存目錄"
    assert 'ROOT / "tools" / "_zap_seed' not in src, "種子腳本不可以寫進 tools/（會同步進公開樹）"


def test_no_seed_script_is_left_in_synced_folders():
    from tools.repo_paths import public_root
    places = {ROOT / "tools", public_root(ROOT) / "tools"}    # 開發樹另有公開樹那一層；clone 下來是同一個
    left = [str(p) for d in places for p in d.glob("_zap_seed*")]
    assert not left, f"掃描的種子腳本留在要同步的資料夾裡：{left}"

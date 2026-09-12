"""PNG 匯出：不整份堆記憶體、暫存要有人清、要有併行上限（F09，v1.15.28）。

三個問題（外部稽核）：
1. 每頁的 PNG bytes 全放在一個 list，再做一份 BytesIO 的 zip，再寫一份到磁碟
   —— 一份大文件同時持有三份資料。
2. `tempfile.mkdtemp(prefix="job_png_")` 建在**系統暫存目錄**，而我們的清理
   迴圈只掃 `settings.temp_dir`、而且**只刪檔案跳過目錄** → 那些資料夾永遠
   不會被清。`finally` 裡只有 `pass`。
3. 這條路用 `to_thread` 直接跑，**不經過作業佇列的准入判斷**。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app import main as appmain

SRC = pathlib.Path(appmain.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _func(name: str):
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"找不到 {name}()")


def _png_export_src() -> str:
    """PNG 匯出端點的原始碼（含內層的 `_work`）。"""
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and "png" in node.name.lower():
            return ast.get_source_segment(SRC, node) or ""
    raise AssertionError("找不到 PNG 匯出端點")


def test_pages_are_not_all_held_in_memory():
    """不可以先把每頁的 bytes 收成一個 list 再打包。"""
    src = _png_export_src()
    assert "pngs.append" not in src, (
        "又把每頁的 PNG bytes 堆進 list 了（大文件會同時持有三份）")
    assert "io.BytesIO()" not in src, (
        "又用 BytesIO 在記憶體裡組整包 zip 了")


def test_the_temp_dir_lives_where_the_sweeper_can_see_it():
    src = _png_export_src()
    assert "dir=str(settings.temp_dir)" in src, (
        "暫存目錄又建在系統暫存區了 —— 清理迴圈只掃 settings.temp_dir")


def test_the_intermediate_files_are_actually_removed():
    """`finally` 不可以只有 `pass`。"""
    src = _png_export_src()
    assert "shutil.rmtree(tmp" in src, "中間檔沒有被清掉"


def test_the_served_file_is_deleted_after_streaming():
    src = _png_export_src()
    assert "BackgroundTask(_unlink_quietly" in src, (
        "產出檔沒有掛串流結束後的清理")


def test_the_served_file_is_a_flat_file_in_temp_dir():
    """清理迴圈**只刪檔案、跳過目錄** —— 產出不可以藏在子資料夾裡。"""
    fn = _func("_served_tmp_path")
    src = ast.get_source_segment(SRC, fn) or ""
    assert "settings.temp_dir /" in src, "產出不是平鋪在 temp_dir 底下"
    assert "mkdtemp" not in src


def test_the_sweeper_only_deletes_files_so_the_flat_layout_matters():
    """把上面那條的前提釘住：掃描迴圈真的跳過目錄。

    這條的用意是「如果哪天掃描改成會刪目錄，上面的限制就可以放寬」——
    不寫下來的話，下一個人看不出為什麼要平鋪。
    """
    src = ast.get_source_segment(SRC, _func("_sweep_temp_files_loop")) or ""
    assert "is_file()" in src


def test_the_expensive_export_is_capped():
    src = _png_export_src()
    assert "_png_export_sem" in src, (
        "幾十頁的算圖沒有任何併行上限（這條路不經過作業准入）")


def test_unlink_helper_never_raises(tmp_path):
    appmain._unlink_quietly(tmp_path / "nope.png")      # 不存在也不可以炸
    f = tmp_path / "x.png"
    f.write_bytes(b"1")
    appmain._unlink_quietly(f)
    assert not f.exists()

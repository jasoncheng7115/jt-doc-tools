"""取消 / 清理之後不可以留著執行函式（外部稽核 F05，v1.15.28）。

`_fns` 存的是「還沒跑的那個 callable」——一個 closure，捕捉了上傳路徑、參數，
有時還有比較大的資料物件。原本的清除寫在 `_run()` 的收尾裡，而**取消掉的
排隊工作再也不會進 `_run()`** → 那個 closure 永遠不會被釋放。

稽核報告的重現方式：暫停派送、連續提交並取消 400 件、再 `_trim_memory()`
—— `_pending=0`、工作列裁到 300，但 `_fns` 仍有 400 筆。

同一個洞還有兩處（**報告沒提到**）：`_trim_memory()` 與 `cleanup_expired()`
丟掉 `_jobs` 那一列時，也沒有丟 `_fns` / `_subprocs`。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from app.core.job_manager import JobManager


def _mgr():
    m = JobManager(workers=1)
    m.set_paused(True)               # 不要真的開始跑
    return m


def _submit(m, n):
    ids = []
    for i in range(n):
        big = "x" * 10_000           # 讓 closure 捕捉一點東西，像真的作業
        job = m.submit("pdf-merge", lambda job, _b=big: None)
        ids.append(job.id)
    return ids


def test_cancelling_a_queued_job_releases_the_callable():
    m = _mgr()
    ids = _submit(m, 50)
    assert len(m._fns) == 50
    for jid in ids:
        assert m.cancel(jid) is True
    assert len(m._pending) == 0
    assert m._fns == {}, f"取消後還留著 {len(m._fns)} 個執行函式"


def test_the_job_row_survives_so_the_ui_can_show_it():
    """只放掉 callable —— 使用者要看得到那一列寫著「已取消」。"""
    m = _mgr()
    jid = _submit(m, 1)[0]
    m.cancel(jid)
    job = m._jobs.get(jid)
    assert job is not None and job.status == "cancelled"
    assert jid not in m._fns


def test_trimming_memory_also_drops_the_callables():
    """`_trim_memory` 丟掉工作列時不可以只丟一半。"""
    m = _mgr()
    ids = _submit(m, 400)
    for jid in ids:
        m.cancel(jid)
    m._trim_memory()
    assert m._fns == {}
    # 被裁掉的列不可以在 _subprocs 裡留下孤兒
    assert all(jid in m._jobs for jid in m._subprocs)


def test_expiry_cleanup_also_drops_the_callables(monkeypatch):
    m = _mgr()
    ids = _submit(m, 5)
    for jid in ids:
        m.cancel(jid)
    for jid in ids:                  # 假裝它們已經過期
        m._jobs[jid].updated_at = 0.0
    removed = m.cleanup_expired()
    assert removed == 5
    assert m._jobs == {} and m._fns == {}


def test_every_place_that_drops_a_job_row_goes_through_forget():
    """判準走 AST：不可以有人繞過 `_forget` 自己 pop。

    這條擋的是「日後有人加第四條清理路徑，又漏掉附帶狀態」——
    F05 之所以存在，就是因為清除邏輯只寫在其中一條路上。

    **`_subprocs` 不在檢查範圍內**：`unregister_subprocess()` 在子行程結束時
    清它，那時工作還在跑，不是「這件工作結束了」那條路。
    """
    src = pathlib.Path(
        __file__).resolve().parents[1] / "app" / "core" / "job_manager.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name == "_forget":
            continue
        for sub in ast.walk(node):
            # self._jobs.pop(...) / del self._jobs[...] / self._fns.pop(...)
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) \
                    and sub.func.attr == "pop" \
                    and isinstance(sub.func.value, ast.Attribute) \
                    and sub.func.value.attr in ("_jobs", "_fns"):
                offenders.append(f"{node.name}:{sub.lineno} pop {sub.func.value.attr}")
            if isinstance(sub, ast.Delete):
                for tgt in sub.targets:
                    if isinstance(tgt, ast.Subscript) and \
                            isinstance(tgt.value, ast.Attribute) and \
                            tgt.value.attr in ("_jobs", "_fns"):
                        offenders.append(f"{node.name}:{sub.lineno} del {tgt.value.attr}")
    assert not offenders, ("這些地方自己清狀態、沒走 _forget（F05 的成因）："
                           + ", ".join(offenders))

"""升級失敗時要真的回復，而且訊息要說出實際結果（外部稽核 F03，v1.15.28）。

原本 `uv sync` 失敗時印的是 `uv sync failed, restoring previous state`，
實際只做了「把檔案擁有者改回去」＋「啟動服務」——
**工作樹還停在新版程式、配著沒同步完的相依，然後服務就這樣起來了。**
`pre_sha` 的 reset 只存在於「偵測到降版」那一條分支。

> 這是**訊息承諾了一個不存在的保證**，跟串流逾時那次（設定說明寫「單次 HTTP
> 呼叫上限」、其實只管每個 chunk）是同一個病。所以這裡不只驗「有沒有回復」，
> 也驗**訊息會不會誠實區分三種結局**。
"""
from __future__ import annotations

import ast
import pathlib
import subprocess

import pytest

from app import cli

SRC = pathlib.Path(cli.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def _func(name: str) -> ast.FunctionDef:
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"找不到 {name}()")


# ---------------------------------------------------------------- 行為層

def _repo(tmp_path):
    """做一個真的 git repo：兩個 commit，HEAD 在「新版」。"""
    root = tmp_path / "repo"
    root.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e",
           "PATH": "/usr/bin:/bin"}
    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], env=env,
                              capture_output=True, text=True, check=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    (root / "VERSION").write_text("old\n", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "old")
    old_sha = git("rev-parse", "HEAD").stdout.strip()
    (root / "VERSION").write_text("new\n", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "new")
    return root, old_sha, env


def test_rollback_actually_moves_the_tree_back(tmp_path):
    root, old_sha, env = _repo(tmp_path)
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "new"

    msg = cli._rollback_code(root, "git", env, old_sha)

    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "old", (
        "程式碼沒有真的回到升級前的 commit")
    assert old_sha[:12] in msg


def test_rollback_says_so_when_the_deps_could_not_be_resynced(tmp_path):
    """離線 / 磁碟滿時相依同步會失敗 —— 那時**不可以說已完整回復**。"""
    root, old_sha, env = _repo(tmp_path)
    msg = cli._rollback_code(root, "git", env, old_sha,
                             uv="/nonexistent/uv-that-fails", uv_env=dict(env))
    assert "相依環境沒有同步回去" in msg, msg
    assert "完整回復" not in msg, "相依沒回去卻說完整回復了"
    assert (root / "VERSION").read_text(encoding="utf-8").strip() == "old"


def test_rollback_is_honest_when_there_is_no_commit_to_go_back_to(tmp_path):
    root, _old, env = _repo(tmp_path)
    msg = cli._rollback_code(root, "git", env, "")
    assert "無法回復" in msg
    assert "相依環境可能不完整" in msg, "要講出服務現在跑的是什麼狀態"


def test_a_successful_resync_is_reported_as_complete(tmp_path):
    """相依同步成功才可以說「完整回復」。"""
    root, old_sha, env = _repo(tmp_path)
    ok_uv = tmp_path / "uv"
    ok_uv.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    ok_uv.chmod(0o755)
    msg = cli._rollback_code(root, "git", env, old_sha, uv=str(ok_uv),
                             uv_env=dict(env))
    assert "已完整回復" in msg


# ---------------------------------------------------------------- 結構層

@pytest.mark.parametrize("label", ["uv sync", "dep import"])
def test_every_failure_path_after_the_pull_rolls_back(label):
    """`svc_update()` 裡「拉完新程式之後」的失敗分支都要呼叫 `_rollback_code`。

    這條擋的是 F03 本身：原本三條失敗路徑只有一條真的回復。
    """
    body = _func("svc_update").body
    calls = [n for n in ast.walk(ast.Module(body=body, type_ignores=[]))
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "_rollback_code"]
    assert len(calls) >= 3, (
        f"只有 {len(calls)} 條失敗路徑會回復 —— 降版偵測、uv sync 失敗、"
        "相依 import 失敗三條都要")


def test_no_failure_path_claims_to_restore_without_doing_it():
    """不可以再出現「restoring previous state」這種只說不做的訊息。"""
    src = ast.get_source_segment(SRC, _func("svc_update")) or ""
    for lie in ("restoring previous state", "restored previous state"):
        assert lie not in src, (
            f"訊息又寫了「{lie}」—— 要嘛真的回復，要嘛說出實際狀態")


def test_the_rollback_resyncs_dependencies_not_just_the_code():
    """光 reset 程式碼不夠：新版的相依已經裝進 venv 了，要跟著回去。"""
    src = ast.get_source_segment(SRC, _func("_rollback_code")) or ""
    tree = ast.parse(src)
    subs = [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(getattr(n.func, "attr", ""), "lower", lambda: "")() == "call"]
    assert len(subs) >= 2, "回復流程要同時做 git reset 與 uv sync"

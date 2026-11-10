"""文件翻譯依模型的上下文長度夾小一批的字數（2026-11-09 在推論機實測 4096 之後）。

上下文不夠時 Ollama 不報錯、只把輸出切斷：那一批解析失敗、對切重跑，結果對但慢。
"""
from __future__ import annotations

import ast
from pathlib import Path

import importlib

R = importlib.import_module("app.tools.doc_translate.router")


class _Fake:
    def __init__(self, probe=None, exc=None):
        self.probe, self.exc, self.asked = probe, exc, []

    def context_probe(self, model):
        self.asked.append(model)
        if self.exc:
            raise self.exc
        return self.probe


def test_small_context_caps_the_batch():
    c = _Fake({"ollama": True, "effective": 4096})
    assert R._context_char_cap(c, "gemma4:26b", 3000) == int(4096 * R.CONTEXT_CHAR_RATIO)
    assert c.asked == ["gemma4:26b"]
    # 實測 2,240 字已經貼邊、3,029 字失敗：夾出來的值要在安全那一側
    assert R._context_char_cap(c, "m", 3000) <= 2240


def test_large_context_or_small_setting_is_unchanged():
    assert R._context_char_cap(_Fake({"ollama": True, "effective": 131072}), "m", 1200) == 1200
    assert R._context_char_cap(_Fake({"ollama": True, "effective": 4096}), "m", 1200) == 1200


def test_unknown_context_keeps_the_setting():
    for probe in ({"ollama": False}, {"ollama": True, "effective": None}, {}, None,
                  {"ollama": True, "effective": True}, {"ollama": True, "effective": "4096"}):
        assert R._context_char_cap(_Fake(probe), "m", 3000) == 3000, probe
    assert R._context_char_cap(_Fake(exc=RuntimeError("down")), "m", 3000) == 3000


def test_tiny_context_never_goes_below_the_minimum():
    assert R._context_char_cap(_Fake({"ollama": True, "effective": 100}), "m", 3000) == 200


def test_the_job_actually_uses_it():
    tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))
    job = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_job")
    calls = [n.func.id for n in ast.walk(job) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "_context_char_cap" in calls


def test_context_is_read_after_the_model_is_warmed_up():
    """沒載入的模型讀不到實際上下文（`/api/ps` 沒有它）→ 一定要在 `_warmup_llm` 之後才讀。"""
    tree = ast.parse(Path(R.__file__).read_text(encoding="utf-8"))
    job = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_run_job")
    line = {n.func.id: n.lineno for n in ast.walk(job) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert line["_warmup_llm"] < line["_context_char_cap"] < line["_make_batches"]

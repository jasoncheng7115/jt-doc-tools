"""字典在兩支翻譯工具上真的有作用（**驗產出，不驗中間狀態**）。

只驗核心模組不算驗收：接線錯了（沒把 matcher 傳下去、旗標沒讀到）核心
測試照樣全綠，而使用者拿到的譯文裡專有名詞還是錯的。
"""
from __future__ import annotations

import importlib
import io
import re
import zipfile

import pytest

from app.core import office_text_map as M, translation_glossary as G

DT = importlib.import_module("app.tools.doc_translate.router")
TRD = importlib.import_module("app.tools.translate_doc.router")


def _t(source, target, **kw):
    kw.setdefault("src_lang", "en")
    kw.setdefault("tgt_lang", "zh-TW")
    return G.Term(source=source, target=target, **kw)


class _Fake:
    """乖模型：原樣保留佔位符（好的模型會這樣）。"""

    def __init__(self, mangle: bool = False):
        self.mangle = mangle

    def _one(self, t: str) -> str:
        return ("【" + (t.replace("[[T", "<<").replace("]]", ">>") if self.mangle else t)
                + "】")

    def text_query(self, prompt, **kw):
        if "⟦1⟧" in prompt:
            segs = [m.groups() for line in prompt.splitlines()
                    if (m := re.match(r"^⟦(\d+)⟧(.*)$", line))]
            return "\n".join(f"⟦{n}⟧{self._one(s)}" for n, s in segs)
        return self._one(prompt.rsplit("<source>", 1)[-1].split("</source>")[0].strip())


@pytest.fixture
def env(tmp_path, monkeypatch):
    d = tmp_path / "data"
    (d / "temp").mkdir(parents=True)     # `settings.temp_dir` 不會自己建
    monkeypatch.setattr("app.config.settings.data_dir", d)
    monkeypatch.setattr(G, "_CACHE", None)
    for R in (DT, TRD):
        monkeypatch.setattr(R.llm_settings, "is_enabled", lambda: True)
        monkeypatch.setattr(R.llm_settings, "get_model_for", lambda _t: "fake")
        monkeypatch.setattr(R.llm_settings, "get", lambda: {
            "translate_concurrency": 1,
            "doctr_batch_segments": 40, "doctr_batch_chars": 1200})
        monkeypatch.setattr(R, "_warmup_llm", lambda *a, **k: None)
    monkeypatch.setattr(DT, "_make_preview", lambda *a, **k: (0, 0))
    G.save([_t("Acer", "宏碁"), _t("Foxconn", "富士康"),
            _t("jt-doc-tools", "", mode="keep")])
    return d


class _Job:
    progress = 0.0
    message = ""
    cancelled = False
    result_path = None
    result_filename = ""

    def __init__(self):
        self.meta = {}


def _xlsx(*texts: str) -> bytes:
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    si = "".join(f"<si><t>{t}</t></si>" for t in texts)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">{si}</sst>')
    return buf.getvalue()


def _run_doc(env, monkeypatch, *, mangle=False, use_glossary=True,
             texts=("The Acer server runs Foxconn firmware.",)) -> tuple[list[str], dict]:
    monkeypatch.setattr(DT.llm_settings, "make_client", lambda *a, **k: _Fake(mangle))
    uid = "a" * 32
    DT._src_path(uid).write_bytes(_xlsx(*texts))
    job = _Job()
    DT._run_job(job, uid, {"filename": "a.xlsx", "ext": ".xlsx",
                           "work_ext": ".xlsx"},
                "en", "zh-TW", "", use_glossary=use_glossary)
    units, _ = M.extract_units(DT._out_path(uid, ".xlsx").read_bytes(), ".xlsx")
    return [u.text for u in units], job.meta


def test_document_translation_uses_the_dictionary(env, monkeypatch):
    out, meta = _run_doc(env, monkeypatch)
    assert "宏碁" in out[0] and "富士康" in out[0]
    assert "Acer" not in out[0]
    assert meta["glossary_terms"] == 3


def test_keep_mode_survives_the_document_path(env, monkeypatch):
    out, _ = _run_doc(env, monkeypatch,
                      texts=("Install jt-doc-tools on the Acer box.",))
    assert "jt-doc-tools" in out[0] and "宏碁" in out[0]


def test_the_checkbox_really_turns_it_off(env, monkeypatch):
    out, meta = _run_doc(env, monkeypatch, use_glossary=False)
    assert "Acer" in out[0] and "宏碁" not in out[0]
    assert meta["glossary_terms"] == 0


@pytest.mark.parametrize("texts", [
    ("The Acer server runs Foxconn firmware.",),                    # 單段
    ("The Acer server.", "Another Foxconn box.", "A third Acer."),  # 走批次
])
def test_a_model_that_mangles_the_markers_never_leaks_one(env, monkeypatch,
                                                          texts):
    """**產出裡絕對不可以殘留 `[[T1]]`** —— 那比翻錯還明顯。"""
    out, meta = _run_doc(env, monkeypatch, mangle=True, texts=texts)
    for t in out:
        assert not G.has_placeholder(t), t
    # 而且要**誠實回報**退回了幾次：回報 0 次但字典其實沒生效，
    # 比不回報還糟 —— 使用者會以為它有用。
    assert meta["glossary_fallbacks"] >= 1


def test_sentence_translation_uses_the_dictionary(env, monkeypatch):
    monkeypatch.setattr(TRD.llm_settings, "make_client", lambda *a, **k: _Fake())
    res = TRD._translate_sentences(["The Acer server runs Foxconn firmware."],
                                   "en", "zh-TW")
    assert "宏碁" in res[0]["translated"]
    assert "富士康" in res[0]["translated"]


def test_sentence_translation_respects_the_flag(env, monkeypatch):
    monkeypatch.setattr(TRD.llm_settings, "make_client", lambda *a, **k: _Fake())
    res = TRD._translate_sentences(["The Acer server."], "en", "zh-TW",
                                   use_glossary=False)
    assert "Acer" in res[0]["translated"]


def test_sentence_translation_falls_back_and_reports(env, monkeypatch):
    monkeypatch.setattr(TRD.llm_settings, "make_client", lambda *a, **k: _Fake(mangle=True))
    res = TRD._translate_sentences(["The Acer server."], "en", "zh-TW")
    assert not G.has_placeholder(res[0]["translated"])
    assert res[0]["glossary"]["fallback"] is True


def test_both_tools_offer_the_toggle_only_when_it_would_do_something():
    """沒設定就給一個永遠沒作用的勾選，只會讓人以為自己設錯了。"""
    from pathlib import Path
    for tpl, ident in (("app/tools/doc_translate/templates/doc_translate.html",
                        "dtGlossRow"),
                       ("app/tools/translate_doc/templates/translate_doc.html",
                        "trdGlossRow")):
        html = Path(tpl).read_text(encoding="utf-8")
        assert f'id="{ident}" hidden' in html, f"{tpl}：勾選預設要藏起來"
        assert "glossary_pairs" in html, f"{tpl}：沒有依語言對決定顯示"
        assert "use_glossary" in html, f"{tpl}：送出時沒有帶旗標"

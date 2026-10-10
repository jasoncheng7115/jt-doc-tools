"""資料保護與合規頁（`COMPLIANCE.md` → `docs/compliance.html`）。

* 內容只有 `COMPLIANCE.md` 一份；`docs/compliance.html` 由 `build-compliance-page.py` 產生，
  不可以手改（改了 md 沒重跑產生器的話，網頁會停在舊內容）。
* 主頁要有連結（稽核那一節的說明框），每一頁的頁尾也要有。
* 英文與日文版都要產生、沒有漏翻。
* **只寫做得到、對過程式的事**：頁面上寫的保留天數直接跟程式的預設值比對。

2026-10-10 改版：資料在哪裡（流向圖＋存在哪裡、留多久＋會連到外部的情況）、工具做到的 /
組織要做的兩排卡片、法規對照、兩份 ISO 的對照表（第三欄是「你的組織要做的」）。

同一天第二輪：
個資法（含施行細則第 12 條各款）與 GDPR 的逐條對照、卡片的「對應」條號與「如何驗證」測試、
建議的驗收案例。搜尋的行為在 `test_compliance_search_browser.py`。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(ROOT))
from tools.repo_paths import public_root  # noqa: E402

PUB = public_root(ROOT)
DOCS = PUB / "docs"


def _gen():
    spec = importlib.util.spec_from_file_location("bcp", PUB / "build-compliance-page.py")
    if spec is None or not (PUB / "build-compliance-page.py").exists():
        pytest.skip("公開樹沒有產生器")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _md() -> str:
    return (PUB / "COMPLIANCE.md").read_text(encoding="utf-8")


def _page(name: str = "compliance") -> str:
    return (DOCS / f"{name}.html").read_text(encoding="utf-8")


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_the_page_is_generated_from_the_markdown():
    assert _page() == _gen().build(), \
        "docs/compliance.html 跟 COMPLIANCE.md 對不上：改了 md 要重跑 build-compliance-page.py"


def test_every_heading_and_table_of_the_markdown_is_on_the_page():
    md, page = _md(), _page()
    heads = re.findall(r"^#{2,3} (.+)$", md, re.M)
    assert len(heads) >= 20, heads
    plain = re.sub(r"<[^>]+>", "", page)
    for h in heads:
        assert re.sub(r"[`*]", "", h) in plain, h
    # 分隔列（`|---|---|`）一個表格一列
    assert page.count('<table class="cp-table') == len(re.findall(r"^\|(?:\s*-+\s*\|)+\s*$", md, re.M))


def test_the_main_page_and_every_footer_link_to_it():
    idx = _page("index")
    assert 'class="cp-cta"' in idx and 'href="compliance.html"' in idx
    for name in ("index", "api", "troubleshooting", "compliance"):
        t = _page(name)
        foot = t[t.index('<footer class="footer">'):]
        assert 'href="compliance.html"' in foot, f"{name}.html 的頁尾沒有連到合規頁"
    assert "COMPLIANCE.md" in (PUB / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_translated_pages_exist_and_link_to_their_own_language(lang):
    t = _page(f"compliance-{lang}")
    assert f'href="index-{lang}.html' in t, "導覽要連到同語言的首頁"
    body = _text(t[t.index("<main"):t.index("</main>")])
    if lang == "en":
        assert not re.search(r"[㐀-鿿]", body), "英文頁還有中文"


# ---- 版面：頁首、頁內導覽、流向圖、卡片、畫面 ----

def test_the_hero_says_it_is_not_a_certificate_or_legal_advice():
    page = _page()
    hero = page[page.index('<header class="cp-hero">'):page.index("</header>", page.index('<header class="cp-hero">'))]
    assert 'class="cp-title"' in hero and 'class="cp-disclaimer"' in hero
    assert "不是法律意見" in hero and "不宣稱" in hero


def test_the_page_nav_reaches_every_section():
    gen, page = _gen(), _page()
    nav = page[page.index('<nav class="cp-pagenav"'):page.index("</nav>", page.index('<nav class="cp-pagenav"'))]
    targets = re.findall(r'href="#([\w-]+)"', nav)
    sections = re.findall(r'<section class="cp-sec[^"]*" id="([\w-]+)"', page)
    assert targets == sections and len(sections) == len(gen.SECTIONS), (targets, sections)


def test_the_data_flow_diagram_is_there_and_is_text_not_a_picture():
    """圖裡的字要是 HTML 文字：英文、日文版才翻得到（圖片裡的字翻不到）。"""
    page = _page()
    assert page.count('class="cp-flow"') == 1
    en = _page("compliance-en")
    flow = en[en.index('class="cp-flow"'):en.index("</table>", en.index('class="cp-flow"'))]
    assert "Cloud AI services" in flow and not re.search(r"[㐀-鿿]", _text(flow))


def test_every_card_has_an_icon_and_bullets():
    gen, md, page = _gen(), _md(), _page()
    titles = []
    for sec in gen.CARD_SECTIONS:
        part = md[md.index(f"## {sec}"):]
        part = part[:part.index("\n## ", 3)]
        titles += re.findall(r"^### (.+)$", part, re.M)
    assert len(titles) >= 8, titles
    cards = re.findall(r'<div class="cp-card">(.*?)</ul>', page, re.S)
    assert len(cards) == len(titles)
    for t, c in zip(titles, cards):
        assert t in c and '<svg class="cp-ic"' in c and c.count("<li>") >= 3, t


def test_the_screenshots_exist_in_every_language_and_reserve_their_space():
    """沒寫寬高的話，從頁內導覽跳到下面某一節時，上方延遲載入的截圖載進來會把整頁往下推，
    停在錯的那一節。"""
    page = _page()
    imgs = re.findall(r"<img [^>]*>", page[page.index("<main"):])
    assert len(imgs) >= 4, imgs
    assert all(re.search(r'width="\d+" height="\d+"', i) for i in imgs), imgs
    for lang in ("", "en/", "ja/"):
        for f in re.findall(r'<img src="screenshots/([^"]+)"', page):
            assert (DOCS / "screenshots" / f"{lang}{f}").is_file(), f"screenshots/{lang}{f} 不見了"


# ---- 寫的跟程式一致 ----

def test_the_retention_periods_on_the_page_are_the_defaults_in_the_code():
    """頁面寫「2 小時」「365 天」—— 那是程式的預設值。改了預設值、頁面沒跟著改的話，
    讀的人照著頁面寫制度文件，跟實際行為對不上。"""
    from app.core import retention, workspace
    d = retention._DEFAULTS
    md = _md()
    data = md[md.index("| 資料 | 存在哪裡、留多久 |"):md.index("資料目錄預設在")]
    expect = {
        "上傳的檔案與處理中的產出": f"{d['temp_hours']} 小時",
        "背景作業的結果": f"{d['jobs_hours']} 小時",
        "我的工作區": f"{workspace._DEFAULTS['retention_hours']} 小時",
        "表單填寫、用印簽名、浮水印的歷史檔案": f"{d['fill_history_days']} 天",
        "公文撰擬案件": f"{d['official_doc_cases_days']} 天",
        "會議錄音轉逐字稿上傳的原始錄音": f"{d['speech_audio_days']} 天",
    }
    assert d["fill_history_days"] == d["stamp_history_days"] == d["watermark_history_days"]
    rows = {r.split("|")[1].strip(): r for r in data.splitlines() if r.startswith("| ")}
    for name, want in expect.items():
        assert want in rows[name], f"「{name}」頁面寫的跟程式預設值 {want} 不同：{rows[name]}"
    assert f"作業紀錄 {d['job_records_days']} 天" in rows["背景作業的結果"]
    assert f"稽核記錄 {d['audit_days']} 天" in rows["帳號、權限與稽核記錄"]


# ---- 條文與控制項對照 ----
#
# 判準：①每一個編號都是那一份標準裡真的有的條文或控制項（打錯一碼，稽核員一查就知道）；
# ②每一個編號都有英文名稱（第一欄下面那一行）；③每一列「本工具提供的」「你的組織要做的」都有寫；
# ④每一列都有錨點，而且不重複。

#: ISO/IEC 27001:2022 附錄 A：5.1–5.37、6.1–6.8、7.1–7.14、8.1–8.34
A27 = ({f"A.5.{i}" for i in range(1, 38)} | {f"A.6.{i}" for i in range(1, 9)}
       | {f"A.7.{i}" for i in range(1, 15)} | {f"A.8.{i}" for i in range(1, 35)} | {"A.7"})
#: ISO/IEC 42001:2023 附錄 A（38 項）
A42 = {"A.2.2", "A.2.3", "A.2.4", "A.3.2", "A.3.3", "A.4.2", "A.4.3", "A.4.4", "A.4.5", "A.4.6",
       "A.5.2", "A.5.3", "A.5.4", "A.5.5", "A.6.1.2", "A.6.1.3", "A.6.2.2", "A.6.2.3", "A.6.2.4",
       "A.6.2.5", "A.6.2.6", "A.6.2.7", "A.6.2.8", "A.7.2", "A.7.3", "A.7.4", "A.7.5", "A.7.6",
       "A.8.2", "A.8.3", "A.8.4", "A.8.5", "A.9.2", "A.9.3", "A.9.4", "A.10.2", "A.10.3", "A.10.4"}
#: 個資法：第 1 至 56 條（另有第 1 條之 1）；施行細則第 12 條第 2 項的 11 款寫成 `k1`～`k11`
PDPA = {str(i) for i in range(1, 57)} | {f"k{i}" for i in range(1, 12)}
#: GDPR：第 1 至 99 條
GDPR = {str(i) for i in range(1, 100)}
#: 兩份標準本文的條文（兩份都是 Annex SL 架構；6.1.4 只有 42001 有）
_CL = ({f"4.{i}" for i in range(1, 5)} | {f"5.{i}" for i in range(1, 4)}
       | {"6.1", "6.1.1", "6.1.2", "6.1.3", "6.2", "6.3"} | {f"7.{i}" for i in range(1, 6)}
       | {"7.5.1", "7.5.2", "7.5.3", "9.1", "9.2", "9.2.1", "9.2.2", "9.3", "9.3.1", "9.3.2", "9.3.3",
          "10", "10.1", "10.2"})
C27 = _CL | {"8.1", "8.2", "8.3"}
C42 = _CL | {"6.1.4", "8.1", "8.2", "8.3", "8.4"}
VALID = {"27": A27 | C27, "42": A42 | C42, "pdpa": PDPA, "gdpr": GDPR}
STDS = ("pdpa", "gdpr", "27", "42")


def _valid(std: str, code: str) -> bool:
    """`44-49` 這種範圍：兩端都要是真的條號，而且由小到大。"""
    if "-" in code and std in ("pdpa", "gdpr"):
        a, b = code.split("-")
        kp = "k" if a.startswith("k") else ""
        return a in VALID[std] and kp + b in VALID[std] and int(a.lstrip("k")) < int(b)
    return code in VALID[std]


def _art_codes(text: str) -> list[str]:
    """「第 7 至 9 條」「施行細則第 12 條第 1、3、7、11 款」→ 編號（款寫成 `k1`）。"""
    out = []
    for nums, unit in re.findall(r"第 ([\d、至 ]+?) (條|款)", text):
        codes = [nums.replace(" 至 ", "-")] if " 至 " in nums else nums.split("、")
        out += [("k" + c.replace("-", "-k") if unit == "款" else c) for c in codes]
    return out
_CODE = re.compile(r"(?:A\.)?\d+(?:\.\d+)*")


def _mapping():
    """回 (rows, org)：rows＝[(標準, [編號], 本工具提供的, 你的組織要做的)]；
    org＝[(標準, [編號])]（「由組織的制度落實的」那幾條）。"""
    gen, md = _gen(), _md()
    rows, org, std, in_org = [], [], None, False
    for ln in md.split("\n"):
        if ln.startswith("## "):
            std, in_org = gen._std_of(ln[3:]), False
        elif ln.startswith("### "):
            in_org = "制度落實" in ln
        elif std and ln.startswith("|") and not re.match(r"^\|(?:\s*-+\s*\|)+", ln):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            got = gen.codes_of(std, cells[0])
            if got:
                rows.append((std, got[0], cells[1], cells[2]))
        elif std and in_org and ln.startswith("- **"):
            head = ln[4:ln.index("**", 4)]
            if std in ("pdpa", "gdpr"):
                codes = _art_codes(head)
            else:
                codes = re.findall(r"(?:A\.)?\d+(?:\.\d+)*", head.split(" ")[0] + " " + head)
            org.append((std, [c for c in dict.fromkeys(codes)]))
    return rows, org


def test_the_mapping_is_there_for_every_law_and_standard():
    rows, org = _mapping()
    least = {"27": 15, "42": 15, "pdpa": 12, "gdpr": 10}
    for std in STDS:
        assert sum(1 for s, *_ in rows if s == std) >= least[std], f"{std} 的對照表幾乎是空的"
        assert any(s == std for s, _ in org), f"{std} 沒有列「由組織的制度落實的」"


def test_every_number_is_a_real_clause_or_control_of_that_standard():
    rows, org = _mapping()
    bad = [f"{std} {c}" for std, codes, *_ in rows for c in codes if not _valid(std, c)]
    bad += [f"{std} {c}（組織落實）" for std, codes in org for c in codes if not _valid(std, c)]
    assert not bad, f"這些編號在法規或標準裡不存在：{bad}"


def test_all_eleven_items_of_the_enforcement_rules_are_accounted_for():
    """施行細則第 12 條第 2 項有 11 款：表格列的加上「由組織的制度落實的」那一條，剛好 11 款、不重複
    （頁面寫「跟本工具有關的是下面 7 款；其餘 4 款由組織的制度落實」，數字要對得上）。"""
    rows, org = _mapping()
    table = [c for s, codes, *_ in rows if s == "pdpa" for c in codes if c.startswith("k")]
    rest = [c for s, codes in org if s == "pdpa" for c in codes if c.startswith("k")]
    assert sorted(table + rest, key=lambda c: int(c[1:])) == [f"k{i}" for i in range(1, 12)], (table, rest)
    md = _md()
    assert f"跟本工具有關的是下面 {len(table)} 款；其餘 {len(rest)} 款" in md


def test_every_row_says_both_what_the_tool_does_and_what_you_do():
    rows, _ = _mapping()
    bad = [f"{std}001 {codes}" for std, codes, tool, you in rows if len(tool) < 6 or len(you) < 4]
    assert not bad, bad


def test_every_number_has_its_english_name():
    """GDPR 與兩份 ISO 有原文的英文名稱，每一列都要附上；個資法沒有官方的條文標題，不附。"""
    gen = _gen()
    rows, _ = _mapping()
    named = [(std, codes) for std, codes, *_ in rows if std != "pdpa"]
    missing = [f"{std} {c}" for std, codes in named for c in codes if c not in gen.EN_NAMES[std]]
    assert not missing, f"EN_NAMES 少了這幾個：{missing}"
    assert "pdpa" not in gen.EN_NAMES
    page = _page()
    assert page.count('class="cp-en"') == len(named)


def test_every_row_has_a_unique_anchor():
    page = _page()
    ids = re.findall(r'<tr id="([^"]+)"', page)
    rows, _ = _mapping()
    assert len(ids) == len(rows) and len(set(ids)) == len(ids), ids


def test_the_english_page_uses_the_official_control_names():
    page = _text(_page("compliance-en"))
    for name in ("A.8.15 Logging", "A.5.15 Access control", "A.6.2.8 AI system recording of event logs",
                 "9.2 Internal audit"):
        assert name in page, name


def test_the_english_name_line_is_hidden_only_on_the_english_page():
    """英文頁第一欄的譯名本來就是英文名稱，下面那一行再寫一次就重複了。"""
    css = (DOCS / "style.css").read_text(encoding="utf-8")
    assert re.search(r'html\[lang="en"\]\s+\.cp-en\s*\{\s*display:\s*none', css)
    assert '<html lang="ja"' in _page("compliance-ja") and 'class="cp-en"' in _page("compliance-ja")


# ---- 標準一律寫出版本（2026-10-10 使用者：「iso 有版本，例如 iso 27001:2022，你要標上」）----

_EDITION_FILES = ("COMPLIANCE.md", "README.md", "README_en.md", "README_ja.md", "AUTH.md")


def _edition_sources():
    out = [PUB / f for f in _EDITION_FILES if (PUB / f).exists()]
    out += sorted((PUB / "docs").glob("*.html"))
    return out


def test_the_edition_scan_reaches_the_pages():
    names = {p.name for p in _edition_sources()}
    assert {"COMPLIANCE.md", "README.md", "compliance.html", "compliance-en.html",
            "compliance-ja.html", "index.html"} <= names, names


@pytest.mark.parametrize("path", _edition_sources(), ids=lambda p: p.name)
def test_every_mention_of_the_standards_names_the_edition(path):
    """ISO/IEC 27001 與 42001 都有好幾版（27001:2013 / 27001:2022），條文與附錄 A 的編號在不同版本完全不同 ——
    不寫版本的話，讀的人拿去對另一版的控制項清單會對不起來。更新記錄是歷史，不在範圍內。"""
    text = path.read_text(encoding="utf-8")
    bad = [m.group(0) for m in re.finditer(r"\b(27001|42001)\b(?!:20\d\d)", text)]
    assert not bad, f"{path.name} 有 {len(bad)} 處沒寫版本：{bad[:5]}"


# ---- 卡片的「對應」與「如何驗證」（2026-10-10 第二輪）----
#
# 判準：①每一顆條號都連得到頁面上真的有的那一列（三種語言都是）；②「工具本身做到的」每一張卡片
# 都有對應與驗證；③寫在頁面上的每一支測試都真的存在、裡面真的有測試 —— 拿不存在的檔名當證據，
# 比沒有寫更糟。

def _card_sections():
    gen, md = _gen(), _md()
    part = md[md.index(f"## {gen.CARD_SECTIONS[0]}"):]
    part = part[:part.index("\n## ", 3)]
    return gen, re.split(r"^### ", part, flags=re.M)[1:]


def test_every_tool_card_has_references_and_tests():
    gen, cards = _card_sections()
    assert len(cards) >= 6
    for c in cards:
        title = c.split("\n", 1)[0]
        assert "\n**對應**：" in c, f"「{title}」沒有對應的條號"
        assert "\n**驗證**：" in c, f"「{title}」沒有驗證用的測試"
        line = re.search(r"^\*\*驗證\*\*：(.+)$", c, re.M).group(1)
        assert len(gen.verify_items(line)) >= 3, title
    page = _page()
    assert page.count('<div class="cp-refs">') == len(cards)
    assert page.count('<details class="cp-verify">') == len(cards)


def _referenced_tests() -> set[str]:
    return set(re.findall(r"`(tests/test_\w+\.py)`", _md()))


def test_every_test_named_on_the_page_exists_and_has_tests():
    named = _referenced_tests()
    assert len(named) >= 25, named
    for rel in sorted(named):
        f = ROOT / rel
        assert f.is_file(), f"頁面拿 {rel} 當證據，可是沒有這支測試"
        assert re.search(r"^\s*def test_", f.read_text(encoding="utf-8"), re.M), f"{rel} 裡沒有測試"


def test_the_tests_link_to_the_public_repo():
    page = _page()
    for rel in _referenced_tests():
        assert f'href="https://github.com/jasoncheng7115/jt-doc-tools/blob/main/{rel}"' in page, rel


@pytest.mark.parametrize("name", ["compliance", "compliance-en", "compliance-ja"])
def test_every_reference_chip_lands_on_a_row(name):
    page = _page(name)
    ids = set(re.findall(r'<tr id="([^"]+)"', page))
    chips = re.findall(r'<a class="cp-chip" href="#([^"]+)">', page)
    assert len(chips) >= 30, len(chips)
    bad = [c for c in chips if c not in ids]
    assert not bad, f"這些條號連不到對照表：{bad}"


def test_the_reference_chips_cover_every_law_and_standard():
    page = _page()
    chips = re.findall(r'<a class="cp-chip" href="#([^"]+)">', page)
    for prefix in ("a27-", "a42-", "pdpa-", "pdpa-k", "gdpr-"):
        assert any(c.startswith(prefix) for c in chips), prefix


def test_the_acceptance_table_names_a_test_on_every_row():
    md = _md()
    part = md[md.index("## 建議的驗收案例"):]
    rows = [r for r in part.split("\n") if r.startswith("| ") and not r.startswith("| 驗收案例")]
    assert len(rows) >= 15, rows
    for r in rows:
        cells = [c.strip() for c in r.strip().strip("|").split("|")]
        assert re.search(r"`tests/test_\w+\.py`", cells[2]), r


# ---- 搜尋框（行為在 test_compliance_search_browser.py）----

@pytest.mark.parametrize("name", ["compliance", "compliance-en", "compliance-ja"])
def test_every_language_has_the_search_box_and_its_script(name):
    page = _page(name)
    nav = page[page.index('<nav class="cp-pagenav"'):page.index("</nav>", page.index('<nav class="cp-pagenav"'))]
    assert 'id="cpSearch"' in nav and 'type="search"' in nav, "搜尋框要在頁內導覽裡（捲到哪裡都按得到）"
    assert '<script src="compliance-search.js" defer></script>' in page
    tpl = re.search(r'<span class="cp-search-tpl" id="cpSearchTpl" hidden>([^<]*)</span>', page).group(1)
    assert "{n}" in tpl, "計數的文字要留著 {n}（翻譯時漏掉的話，畫面上看不到幾項）"
    assert 'id="cpSearchEmpty"' in page
    assert (DOCS / "compliance-search.js").is_file()

#!/usr/bin/env python3
"""從 `COMPLIANCE.md` 產生介紹站的獨立頁 `docs/compliance.html`。

內容只寫在 `COMPLIANCE.md` 一份；這支把它排進介紹站的版面（導覽列與頁尾取自
`docs/troubleshooting.html`，同一套樣式）。改了 `COMPLIANCE.md` 或那一頁的導覽列 / 頁尾，
重跑這支，再跑 `build-i18n-page.py` 產英文與日文版。

    python3 build-compliance-page.py

## 版面（2026-10-10 改版）

原本是一份照章節排下去的文件（每一節一張兩欄表、四張大截圖，整頁 13,900px），
改成讀的人實際會問的順序：

* 頁首：一句話講重點，下面一個框講清楚「認證驗的是組織，本頁不是法律意見」。
* 頁內導覽（固定在導覽列下方）。
* 資料在哪裡：資料流向圖 ＋「存在哪裡、留多久」表 ＋「會連到外部的情況」表。
* 工具本身做到的 / 你的組織使用後要做的：各一排圖示卡片，條列精簡。
* 法規對照：個資法、GDPR、兩份 ISO。
* 個資法、GDPR、兩份 ISO 的逐條對照表：第三欄是「你的組織要做的」（原本是「本文件章節」，
  對讀的人沒用處）；GDPR 與 ISO 的名稱下面附原文的英文名稱。
* 建議的驗收案例：情境、合格判定、對應的自動化測試。

同一天第二輪：

* 「工具本身做到的」每張卡片下面一排**對應**（條號，點下去跳到對照表那一列）
  與**如何驗證**（自動化測試，連到 GitHub 上那一支）—— 寫的東西都要有證據，而且讀的人點得到。
* 頁內導覽右邊一個搜尋框：只留下符合的列、條列與卡片，沒有符合的區塊整塊收起來
  （行為在 `docs/compliance-search.js`）。

md 的寫法慣例（產生器照這些排版）：

* `# 眉標：標題` → 頁首；接著的段落是導言，`> ` 那一段是頁首的說明框。
* `## ` 每一節一個區塊，底色交替；緊接在標題後的第一段是區塊的導言。
* `CARD_SECTIONS` 裡的那兩節：`### ` 每一個是一張卡片，底下的條列放進卡片。
  卡片裡 `**對應**：` 那一行是條號（`；` 分組，每組開頭是法規或標準的名稱），
  `**驗證**：` 那一行是測試（`；` 分隔，每一項是 `` `tests/…py` `` 加一句說明）。
* 對照表那四節（`_std_of` 認得的標題）：表格第一欄的編號加上錨點，GDPR 與 ISO 另附英文名稱；
  導言之後的段落是小字的附註。個資法與 GDPR 的編號寫成「第 N 條」「第 N、M 條」「第 N 至 M 條」，
  施行細則的款寫成「第 N 款」。
* `<!-- 資料流向圖 … -->` → `FLOW`。
"""
from __future__ import annotations

import html
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "COMPLIANCE.md"
LAYOUT = HERE / "docs" / "troubleshooting.html"
DST = HERE / "docs" / "compliance.html"
REPO = "https://github.com/jasoncheng7115/jt-doc-tools/blob/main/"

DESC = ("Jason Tools 文件工具箱的資料保護與合規：文件留在自己的伺服器、工具本身做到的控制、"
        "組織使用後要做的事，以及個資法、GDPR、ISO/IEC 27001:2022、ISO/IEC 42001:2023 的對照。")
TITLE = "資料保護與合規 | Jason Tools 文件工具箱"


#: 圖示（Lucide 的線條圖示，同介紹站其他區塊的畫法）
_IC = {
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "log": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'
           '<path d="M16 13H8"/><path d="M16 17H8"/><path d="M10 9H8"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "box": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>'
           '<path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
    "db": '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>'
          '<path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/>'
             '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "check": '<path d="m9 11 3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/>',
    "code": '<path d="m16 18 6-6-6-6"/><path d="m8 6-6 6 6 6"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "sparkle": '<path d="M12 3l1.9 5.8L20 11l-6.1 2.2L12 19l-1.9-5.8L4 11l6.1-2.2z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "flask": '<path d="M9 3h6"/><path d="M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/>'
             '<path d="M7.5 15h9"/>',
}

#: 兩節卡片：卡片標題 → 圖示。新增卡片時要補一個，不然產生器會停下來（`KeyError`）。
CARD_SECTIONS = ("工具本身做到的", "你的組織使用後要做的")
CARD_ICONS = {
    "存取控制": "lock", "稽核記錄": "log", "保留期限與備份": "clock", "AI 產出可核對": "sparkle",
    "文件處理的隔離": "box", "透明與安全開發": "code",
    "部署與連線": "globe", "帳號與權限": "users", "保留與備份": "db", "AI 使用與人工覆核": "eye",
}

#: 各節的錨點與頁內導覽的短名稱（照 `## ` 標題對；改標題要一起改）
SECTIONS = {
    "資料在哪裡": ("data", "資料在哪裡"),
    "工具本身做到的": ("controls", "工具做到的"),
    "你的組織使用後要做的": ("checklist", "組織要做的"),
    "對照常見的法規與標準": ("standards", "法規對照"),
    "個人資料保護法條文對照": ("pdpa", "個資法對照"),
    "GDPR 條文對照": ("gdpr", "GDPR 對照"),
    "ISO/IEC 27001:2022 條文與附錄 A 控制項對照": ("iso27001", "27001:2022 對照"),
    "ISO/IEC 42001:2023 條文與附錄 A 控制項對照": ("iso42001", "42001:2023 對照"),
    "建議的驗收案例": ("acceptance", "驗收案例"),
}

#: 對照表第一欄開頭的編號（`A.5.16 身分管理`、`6.1.3、8.3 AI 風險處理`、`A.5.19、A.5.21 …`）
_CODE = re.compile(r"^((?:A\.)?\d+(?:\.\d+)+)")
_CODES = re.compile(r"^((?:A\.)?\d+(?:\.\d+)+(?:、(?:A\.)?\d+(?:\.\d+)+)*)\s+(.+)$")
#: 個資法與 GDPR 的條號（`第 3 條 …`、`第 15、16、19、20 條 …`、`第 44 至 49 條 …`），
#: 以及個資法施行細則第 12 條的款（`第 2 款 …`）
_ARTS = re.compile(r"^第 (\d+(?:(?:、| 至 )\d+)*) (條|款)\s+(.+)$")
#: 卡片「對應」那一行每一組的開頭 → 哪一份（**長的寫前面**：「個資法施行細則」要先於「個資法」）
REF_STDS = (("ISO/IEC 27001:2022", "27"), ("ISO/IEC 42001:2023", "42"),
            ("個資法施行細則", "pdpa"), ("個資法", "pdpa"), ("GDPR", "gdpr"))

#: 標準的英文名稱（對照表第一欄附在中文譯名下面；英文頁那一行藏起來，見 style.css）。
#: 名稱照兩份標準的原文；多個編號一列的照順序用「; 」接起來。測試會擋「表格有、這裡沒有」。
EN_NAMES = {
    "27": {
        "7.5": "Documented information", "9.1": "Monitoring, measurement, analysis and evaluation",
        "9.2": "Internal audit",
        "A.5.3": "Segregation of duties", "A.5.13": "Labelling of information",
        "A.5.14": "Information transfer", "A.5.15": "Access control", "A.5.16": "Identity management",
        "A.5.17": "Authentication information", "A.5.18": "Access rights",
        "A.5.19": "Information security in supplier relationships",
        "A.5.21": "Managing information security in the ICT supply chain",
        "A.5.23": "Information security for use of cloud services", "A.5.33": "Protection of records",
        "A.5.34": "Privacy and protection of PII", "A.5.37": "Documented operating procedures",
        "A.8.2": "Privileged access rights", "A.8.3": "Information access restriction",
        "A.8.5": "Secure authentication", "A.8.6": "Capacity management",
        "A.8.7": "Protection against malware", "A.8.8": "Management of technical vulnerabilities",
        "A.8.9": "Configuration management", "A.8.10": "Information deletion",
        "A.8.11": "Data masking", "A.8.12": "Data leakage prevention", "A.8.13": "Information backup",
        "A.8.15": "Logging", "A.8.16": "Monitoring activities", "A.8.20": "Networks security",
        "A.8.22": "Segregation of networks", "A.8.24": "Use of cryptography",
        "A.8.25": "Secure development life cycle", "A.8.26": "Application security requirements",
        "A.8.27": "Secure system architecture and engineering principles", "A.8.28": "Secure coding",
        "A.8.29": "Security testing in development and acceptance", "A.8.32": "Change management",
    },
    "42": {
        "4.3": "Determining the scope of the AI management system",
        "6.1.3": "AI risk treatment", "8.3": "AI risk treatment",
        "6.1.4": "AI system impact assessment", "8.4": "AI system impact assessment",
        "7.5": "Documented information", "9.1": "Monitoring, measurement, analysis and evaluation",
        "A.3.2": "AI roles and responsibilities", "A.4.2": "Resource documentation",
        "A.4.3": "Data resources", "A.4.4": "Tooling resources", "A.4.5": "System and computing resources",
        "A.6.2.4": "AI system verification and validation", "A.6.2.5": "AI system deployment",
        "A.6.2.6": "AI system operation and monitoring", "A.6.2.7": "AI system technical documentation",
        "A.6.2.8": "AI system recording of event logs", "A.7.5": "Data provenance",
        "A.8.2": "System documentation and information for users",
        "A.9.2": "Processes for responsible use of AI systems", "A.9.4": "Intended use of the AI system",
        "A.10.3": "Suppliers",
    },
    #: GDPR 的條文標題（Regulation (EU) 2016/679 官方英文本）；第 44 至 49 條用第五章的章名
    "gdpr": {
        "5": "Principles relating to processing of personal data",
        "9": "Processing of special categories of personal data",
        "15": "Right of access by the data subject", "17": "Right to erasure ('right to be forgotten')",
        "22": "Automated individual decision-making, including profiling",
        "25": "Data protection by design and by default", "28": "Processor",
        "30": "Records of processing activities", "32": "Security of processing",
        "33": "Notification of a personal data breach to the supervisory authority",
        "34": "Communication of a personal data breach to the data subject",
        "35": "Data protection impact assessment",
        "44-49": "Transfers of personal data to third countries or international organisations",
    },
}

#: 「工具本身做到的」卡片下面那一排畫面（介紹站既有的截圖；英文、日文版由
#: build-i18n-page.py 換成該語言的截圖）
FIGS = (
    ("users-multi-realm.png", "使用者管理：本機、LDAP、AD 與單一登入的帳號並存"),
    ("premissions.png", "權限矩陣：依角色、群組或組織單位指派工具"),
    ("deident-1.png", "文件去識別化：偵測到的個資逐項列出，確認後再編修"),
    ("official-doc.png", "公文撰擬：草稿旁列出檢查結果，找不到依據的標出來"),
)

_ARROW2 = ('<svg class="cp-arrow" viewBox="0 0 44 16" aria-hidden="true">'
           '<path d="M5 8h34M33 3l6 5-6 5M11 3 5 8l6 5" fill="none" stroke="currentColor" '
           'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>')

#: 資料流向圖。字是 HTML（不是圖片裡的字），英文、日文版才翻得到，手機上也排得下。
FLOW = (
    '<div class="cp-flow">'
    '<div class="cp-zone">'
    '<div class="cp-zone-title">你的內網（自己架設的伺服器）</div>'
    '<div class="cp-flow-row">'
    '<div class="cp-node"><b>使用者的瀏覽器</b><span>網頁只連本站</span></div>'
    f'{_ARROW2}'
    '<div class="cp-node cp-node-main"><b>Jason Tools 文件工具箱</b>'
    '<span>轉檔、OCR、去識別化在這裡執行</span><span>檔案、作業與稽核記錄都在資料目錄</span></div>'
    f'{_ARROW2}'
    '<div class="cp-node-col">'
    '<div class="cp-node cp-node-opt"><b>LLM 伺服器（選用）</b><span>例如 Ollama；AI 加值預設關閉</span></div>'
    '<div class="cp-node cp-node-opt"><b>GPU OCR、語音服務（選用）</b><span>自己架設的伺服器</span></div>'
    '<div class="cp-node cp-node-opt"><b>公司目錄與單一登入</b><span>LDAP、AD、OIDC、SAML</span></div>'
    '</div></div></div>'
    '<div class="cp-zone cp-zone-out">'
    '<div class="cp-zone-title">外部</div>'
    '<div class="cp-node cp-out cp-out-no"><b>雲端 AI 服務</b><span>ChatGPT、Claude、Gemini…</span>'
    '<em>不需要，也不會自己連過去</em></div>'
    '<div class="cp-node cp-out cp-out-opt"><b>通知管道</b><span>Email、Slack、Teams…</span>'
    '<em>你開啟才送，不含檔案內容</em></div>'
    '<div class="cp-node cp-out cp-out-ok"><b>GitHub、政府公開資料</b><span>程式、模型與資料</span>'
    '<em>只下載，不上傳你的資料</em></div>'
    '</div></div>'
)


def _std_of(title: str):
    """標題講的是哪一份（`27` / `42` / `pdpa` / `gdpr`），都不是回 None。"""
    if "27001" in title:
        return "27"
    if "42001" in title:
        return "42"
    if title.startswith("個人資料保護法"):
        return "pdpa"
    if title.startswith("GDPR"):
        return "gdpr"
    return None


def anchor(std: str, code: str) -> str:
    """對照表那一列的 id：附錄 A 控制項是 `a27-A.5.16`、條文是 `c42-6.1.3`；
    個資法第 3 條是 `pdpa-3`、施行細則第 2 款是 `pdpa-k2`、GDPR 第 44 至 49 條是 `gdpr-44-49`。"""
    if std in ("pdpa", "gdpr"):
        return f"{std}-{code}"
    return f"{'a' if code.startswith('A.') else 'c'}{std}-{code}"


def codes_of(std: str, cell: str):
    """對照表第一欄 → (編號清單, 編號那一段原文, 名稱)；不是編號開頭的回 None。

    個資法與 GDPR：「第 15、16 條」→ `["15", "16"]`、「第 44 至 49 條」→ `["44-49"]`、
    施行細則「第 2 款」→ `["k2"]`。"""
    if std in ("pdpa", "gdpr"):
        m = _ARTS.match(cell)
        if not m:
            return None
        nums, unit, name = m.groups()
        codes = [nums.replace(" 至 ", "-")] if " 至 " in nums else nums.split("、")
        if unit == "款":
            codes = [f"k{c}" for c in codes]
        return codes, f"第 {nums} {unit}", name
    m = _CODES.match(cell)
    if not m:
        return None
    return m.group(1).split("、"), m.group(1), m.group(2)


#: 卡片「對應」那一行裡的單一編號
_REF_ISO = re.compile(r"(?:A\.)?\d+(?:\.\d+)+")
_REF_ART = re.compile(r"第 \d+(?:(?:、| 至 )\d+)* (?:條|款)")


def _icon(name: str, size: int = 20) -> str:
    return (f'<svg class="cp-ic" viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
            f'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{_IC[name]}</svg>')


def _png_size(path: Path) -> tuple[int, int]:
    """PNG 的寬高（讀檔頭，不需要 Pillow）。

    圖片要寫明寬高，瀏覽器才會先留好位置：沒寫的話，從頁內導覽跳到下面某一節時，
    上方延遲載入的截圖載進來會把整頁往下推，停在錯的那一節。"""
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def _inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    # 文件裡提到的另外幾份說明、驗證用的測試，連到 GitHub 上那一份
    return re.sub(r"<code>((?:TEST_PLAN_SECURITY|OPS|INSTALL|CHANGELOG|LLM|THIRD-PARTY-NOTICES)\.md"
                  r"|tests/test_\w+\.py)</code>",
                  lambda m: f'<a href="{REPO}{m.group(1)}" target="_blank" rel="noopener">'
                            f"<code>{m.group(1)}</code></a>", s)


def _code_cell(std: str, cell: str) -> tuple[str, str]:
    """對照表第一欄：粗體編號 ＋ 中文譯名，下面一行原文的英文名稱（個資法沒有官方的條文標題，
    不附）。回 (html, 錨點 id)。"""
    got = codes_of(std, cell)
    if not got:
        return _inline(cell), ""
    codes, label, name = got
    out = f'<p class="cp-cn"><b>{html.escape(label)}</b> {_inline(name)}</p>'
    if std in EN_NAMES:
        out += f'<p class="cp-en">{html.escape("; ".join(EN_NAMES[std][c] for c in codes))}</p>'
    return out, anchor(std, codes[0])


def row_anchors(page: dict) -> dict:
    """整頁對照表的 (標準, 編號) → 那一列的錨點。一列有好幾個編號的，每一個都指到那一列。"""
    out = {}
    for sec in page["sections"]:
        std = _std_of(sec["title"])
        if not std:
            continue
        for kind, val in sec["blocks"]:
            if kind != "table":
                continue
            for r in val[2:]:
                got = codes_of(std, r[0])
                if got:
                    for c in got[0]:
                        out[(std, c)] = anchor(std, got[0][0])
    return out


def ref_groups(line: str):
    """卡片「對應」那一行 → [(標準名稱, 標準代號, [(顯示的編號, [編號])])]。"""
    out = []
    for grp in line.split("；"):
        grp = grp.strip()
        name, std = next((n, k) for n, k in REF_STDS if grp.startswith(n))
        rest = grp[len(name):]
        chips = []
        if std in ("27", "42"):
            chips = [(c, [c]) for c in _REF_ISO.findall(rest)]
        else:
            for t in _REF_ART.findall(rest):
                got = codes_of(std, t + " x")
                chips.append((t, got[0]))
        out.append((name, std, chips))
    return out


def _refs(line: str, anchors: dict) -> str:
    """卡片「對應」那一行：每一個編號一顆，點下去跳到對照表那一列。
    對不到的編號直接停下來（`KeyError`）—— 寫錯一碼就是一個連不過去的連結。"""
    out = ['<div class="cp-refs"><span class="cp-refs-k">對應</span>']
    for name, std, chips in ref_groups(line):
        out.append(f'<span class="cp-ref-g"><span class="cp-ref-std">{html.escape(name)}</span>'
                   '<span class="cp-ref-chips">')
        out += [f'<a class="cp-chip" href="#{anchors[(std, codes[0])]}">{html.escape(label)}</a>'
                for label, codes in chips]
        out.append("</span></span>")
    out.append("</div>")
    return "".join(out)


def verify_items(line: str) -> list[tuple[str, str]]:
    """卡片「驗證」那一行 → [(測試檔路徑, 說明)]。"""
    out = []
    for it in line.split("；"):
        m = re.match(r"^`(tests/test_\w+\.py)`\s*(.*)$", it.strip())
        out.append((m.group(1), m.group(2)))
    return out


def _verify(line: str) -> str:
    """卡片「如何驗證」：收起來的清單，每一項連到 GitHub 上那一支測試。"""
    items = verify_items(line)
    lis = "".join(f"<li>{_inline(f'`{path}`')} {_inline(desc)}</li>" for path, desc in items)
    return (f'<details class="cp-verify"><summary>{_icon("flask", 15)}<span>如何驗證</span>'
            f'<span class="cp-verify-n">{len(items)}</span></summary>'
            f'<ul class="cp-verify-list">{lis}</ul></details>')


def _table(rows: list[list[str]], std, sid: str = "") -> str:
    """表格。每一格的內容包在 `<p>` 裡、欄位名稱另放一個 `<p class="cp-td-k">`：

    * 手機上表格改成一列一張卡（寬表格在 390px 一定超出畫面），那時候要看得到欄位名稱；
    * 英日版產生器以 `<p>` 為單位整句翻 —— 直接把行內標籤放在 `<td>` 裡又夾一個欄位名稱的話，
      句子會被切成碎片逐段翻。"""
    head = rows[0]
    t = ['<div class="cp-table-wrap"><table class="cp-table'
         + (" cp-maptab" if std else "") + (f" cp-tab-{sid}" if sid else "") + f' cp-cols{len(head)}"><thead><tr>']
    t += [f"<th>{_inline(h)}</th>" for h in head]
    t.append("</tr></thead><tbody>")
    for r in rows[2:]:
        rid = ""
        tds = []
        for j, c in enumerate(r):
            if j == 0 and std:
                cell, rid = _code_cell(std, c)
            elif j == 0:
                cell = f'<p class="cp-td-v">{_inline(c)}</p>'
            else:
                cell = f'<p class="cp-td-k">{_inline(head[j])}</p><p class="cp-td-v">{_inline(c)}</p>'
            tds.append(f"<td>{cell}</td>")
        t.append((f'<tr id="{rid}">' if rid else "<tr>") + "".join(tds) + "</tr>")
    t.append("</tbody></table></div>")
    return "".join(t)


def _gallery() -> str:
    out = ['<div class="cp-gallery">']
    for img, cap in FIGS:
        w, h = _png_size(HERE / "docs" / "screenshots" / img)
        out.append(f'<figure class="cp-shot"><a href="screenshots/{img}" target="_blank" rel="noopener">'
                   f'<img src="screenshots/{img}" alt="{html.escape(cap)}" width="{w}" height="{h}" '
                   f'loading="lazy"></a><figcaption>{_inline(cap)}</figcaption></figure>')
    out.append("</div>")
    return "".join(out)


def _parse(md: str) -> dict:
    """md → 頁首（眉標、標題、導言、說明框）＋ 各節（標題、導言、內容區塊）。"""
    lines = md.split("\n")
    page = {"eyebrow": "", "title": "", "lead": [], "note": "", "sections": []}
    sec = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("# "):
            eyebrow, _, title = ln[2:].partition("：")
            page["eyebrow"], page["title"] = eyebrow.strip(), title.strip()
        elif ln.startswith("## "):
            sec = {"title": ln[3:].strip(), "blocks": []}
            page["sections"].append(sec)
        elif ln.startswith("### "):
            sec["blocks"].append(("h3", ln[4:].strip()))
        elif ln.startswith("> "):
            page["note"] = ln[2:].strip()
        elif ln.startswith("<!-- 資料流向圖"):
            sec["blocks"].append(("flow", ""))
        elif ln.startswith("**對應**："):
            sec["blocks"].append(("refs", ln[len("**對應**："):].strip()))
        elif ln.startswith("**驗證**："):
            sec["blocks"].append(("verify", ln[len("**驗證**："):].strip()))
        elif ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            sec["blocks"].append(("table", rows))
            continue
        elif ln.startswith("- "):
            items = []
            while i < len(lines) and lines[i].startswith("- "):
                items.append(lines[i][2:].strip())
                i += 1
            sec["blocks"].append(("list", items))
            continue
        elif ln.strip() and ln.strip() != "---":
            para = [ln]
            while (i + 1 < len(lines) and lines[i + 1].strip()
                   and not re.match(r"^(#|\||- |>|<!--|---)", lines[i + 1])):
                i += 1
                para.append(lines[i])
            text = "".join(para)
            if sec is None:
                page["lead"].append(text)
            else:
                sec["blocks"].append(("p", text))
        i += 1
    return page


def _section(n: int, sec: dict, anchors: dict) -> str:
    """一節。非卡片的 `### ` 連同底下的內容包成一個 `cp-subsec` —— 搜尋時整塊沒有符合的，
    小標題要跟著收起來（只藏表格的話，畫面上會剩一排沒有內容的小標題）。"""
    sid, _short = SECTIONS[sec["title"]]
    std = _std_of(sec["title"])
    cards = sec["title"] in CARD_SECTIONS
    blocks = list(sec["blocks"])
    out = [f'<section class="cp-sec{" cp-sec-alt" if n % 2 else ""}" id="{sid}"><div class="container">',
           f'<h2 class="cp-sec-title">{_inline(sec["title"])}</h2>']
    if blocks and blocks[0][0] == "p":
        out.append(f'<p class="cp-sec-lead">{_inline(blocks.pop(0)[1])}</p>')

    k, card_open, grid_open, sub, sub_open, h3 = 0, False, False, 0, False, ""
    for kind, val in blocks:
        if kind == "h3" and cards:
            if card_open:
                out.append("</div>")
            if not grid_open:
                out.append('<div class="cp-cards">')
                grid_open = True
            k += 1
            out.append(f'<div class="cp-card"><div class="cp-card-head">'
                       f'<span class="cp-card-ic cp-ic-{(k - 1) % 6 + 1}">{_icon(CARD_ICONS[val], 22)}</span>'
                       f'<h3>{_inline(val)}</h3></div>')
            card_open = True
        elif kind == "h3":
            sub += 1
            if sub_open:
                out.append("</div>")
            out.append('<div class="cp-subsec">')
            sub_open, h3 = True, val
            out.append(f'<h3 class="cp-sub" id="{sid}-{sub}">{_inline(val)}</h3>')
        elif kind == "list":
            cls = "cp-bullets cp-org" if not cards and "制度落實" in h3 else "cp-bullets"
            out.append(f'<ul class="{cls}">' + "".join(f"<li>{_inline(x)}</li>" for x in val) + "</ul>")
        elif kind == "table":
            out.append(_table(val, std, sid))
        elif kind == "flow":
            out.append(FLOW)
        elif kind == "refs":
            out.append(_refs(val, anchors))
        elif kind == "verify":
            out.append(_verify(val))
        elif kind == "p":
            if out[-1].startswith('<h3 class="cp-sub"'):
                out.append(f'<p class="cp-sub-lead">{_inline(val)}</p>')
            elif std:
                out.append(f'<p class="cp-note">{_inline(val)}</p>')
            else:
                out.append(f'<p class="cp-p">{_inline(val)}</p>')
    if card_open:
        out.append("</div>")
    if grid_open:
        out.append("</div>")
    if sub_open:
        out.append("</div>")
    if sec["title"] == CARD_SECTIONS[0]:
        out.append(_gallery())
    if sid == "standards":
        out.append('<div class="cp-jump">'
                   + "".join(f'<a href="#{SECTIONS[t][0]}">{_icon("shield", 15)}{_inline(SECTIONS[t][1])}</a>'
                             for t in SECTIONS if _std_of(t)) + "</div>")
    out.append("</div></section>")
    return "\n".join(out)


#: 搜尋框。給 JS 用的文字放在頁面上（英文、日文版才翻得到）：`{n}` 換成符合的項數。
SEARCH = (
    '<div class="cp-search" role="search">'
    + _icon("search", 16).replace('class="cp-ic"', 'class="cp-ic cp-search-ic"')
    + '<input type="search" id="cpSearch" class="cp-search-in" autocomplete="off" spellcheck="false" '
      'placeholder="搜尋本頁" aria-label="搜尋本頁" aria-describedby="cpSearchN">'
    '<span class="cp-search-n" id="cpSearchN" aria-live="polite"></span>'
    '<span class="cp-search-tpl" id="cpSearchTpl" hidden>{n} 項</span>'
    '</div>'
)
SEARCH_EMPTY = ('<div class="container cp-search-empty" id="cpSearchEmpty" hidden>'
                '<p>找不到符合的項目。換個關鍵字，或用控制項編號（例如 A.8.15）、條號（例如 第 12 條）搜尋。</p>'
                '</div>')


def _body(md: str) -> str:
    page = _parse(md)
    anchors = row_anchors(page)
    hero = ['<header class="cp-hero"><div class="container">',
            f'<span class="cp-eyebrow">{_inline(page["eyebrow"])}</span>',
            f'<h1 class="cp-title">{_inline(page["title"])}</h1>']
    hero += [f'<p class="cp-lead">{_inline(p)}</p>' for p in page["lead"]]
    hero.append(f'<p class="cp-disclaimer">{_inline(page["note"])}</p>')
    hero.append("</div></header>")
    nav = ['<nav class="cp-pagenav" aria-label="本頁"><div class="container cp-pagenav-in">'
           '<span class="cp-pagenav-k">本頁</span><div class="cp-pagenav-links">']
    nav += [f'<a href="#{sid}">{_inline(short)}</a>' for sid, short in
            (SECTIONS[s["title"]] for s in page["sections"])]
    nav.append("</div>" + SEARCH + "</div></nav>")
    secs = [_section(n, s, anchors) for n, s in enumerate(page["sections"])]
    return "\n".join(hero + nav + [SEARCH_EMPTY] + secs)


def build() -> str:
    lay = LAYOUT.read_text(encoding="utf-8")
    head = lay[:lay.index('<main class="container ts-main">')]
    foot = lay[lay.index('<footer class="footer">'):]
    head = re.sub(r'<meta name="description" content="[^"]*">',
                  f'<meta name="description" content="{html.escape(DESC)}">', head, count=1)
    head = re.sub(r"<title>[^<]*</title>", f"<title>{html.escape(TITLE)}</title>", head, count=1)
    head = head.replace('value="troubleshooting.html"', 'value="compliance.html"') \
               .replace('value="troubleshooting-en.html"', 'value="compliance-en.html"') \
               .replace('value="troubleshooting-ja.html"', 'value="compliance-ja.html"')
    foot = foot.replace("</body>", '<script src="compliance-search.js" defer></script>\n</body>', 1)
    return (head + '<main class="cp-main">\n'
            + _body(SRC.read_text(encoding="utf-8")) + "\n</main>\n\n" + foot)


if __name__ == "__main__":
    DST.write_text(build(), encoding="utf-8")
    print(f"{DST.name}: 產生完成")

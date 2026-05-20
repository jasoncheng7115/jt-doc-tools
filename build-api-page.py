#!/usr/bin/env python3
"""build-api-page.py — 把 github/API.md 轉成 github/docs/api.html。

純 stdlib markdown→HTML 轉換器（只支援 API.md 用到的語法：ATX 標題、
fenced code、表格、引用、清單、水平線、行內 bold / code / 連結）。標題
anchor 採 GitHub 相容 slug，讓 API.md 內既有的目錄連結可正常跳轉。

執行：python3 github/build-api-page.py
"""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "API.md"
OUT = ROOT / "docs" / "api.html"


def _esc(s: str) -> str:
    return html.escape(s)


def _hl_json(code: str) -> str:
    """JSON 語法上色：key / 字串 / 數字 / 布林 / null。"""
    token = re.compile(
        r'(?P<key>"(?:[^"\\]|\\.)*")(?=\s*:)'
        r'|(?P<str>"(?:[^"\\]|\\.)*")'
        r'|(?P<num>-?\b\d+(?:\.\d+)?\b)'
        r'|(?P<bool>\b(?:true|false|null)\b)'
        r'|(?P<cmt>//[^\n]*|/\*.*?\*/)'
    )
    out = []
    pos = 0
    for m in token.finditer(code):
        out.append(_esc(code[pos:m.start()]))
        kind = m.lastgroup
        out.append(f'<span class="hl-{kind}">{_esc(m.group())}</span>')
        pos = m.end()
    out.append(_esc(code[pos:]))
    return "".join(out)


def _hl_shell(code: str) -> str:
    """shell / curl / http / yaml / 通用：註解 / 字串 / URL / 方法 / 旗標 / 數字。"""
    token = re.compile(
        r'(?P<cmt>(?<![\w:/])#[^\n]*)'
        r'|(?P<str>"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')'
        r'|(?P<url>https?://[^\s"\'<>)]+)'
        r'|(?P<method>\b(?:GET|POST|PUT|DELETE|PATCH|HEAD)\b)'
        r'|(?P<flag>(?<=\s)--?[A-Za-z][\w-]*)'
        r'|(?P<num>\b\d+(?:\.\d+)?\b)'
    )
    out = []
    pos = 0
    for m in token.finditer(code):
        out.append(_esc(code[pos:m.start()]))
        kind = m.lastgroup
        out.append(f'<span class="hl-{kind}">{_esc(m.group())}</span>')
        pos = m.end()
    out.append(_esc(code[pos:]))
    return "".join(out)


def highlight_code(code: str, lang: str) -> str:
    """依語言上色；未知語言走 shell 通用上色。回傳已 HTML 跳脫的字串。"""
    lang = (lang or "").lower()
    if lang == "json":
        return _hl_json(code)
    return _hl_shell(code)


def slugify(text: str) -> str:
    """GitHub 風格 anchor slug：小寫、去標點、空白轉連字號、保留 CJK。"""
    s = text.strip().lower()
    # 移除行內 markdown 標記
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\*\*([^*]*)\*\*", r"\1", s)
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    # 只保留：英數、CJK、空白、連字號
    out = []
    for ch in s:
        if ch.isalnum() or ch == " " or ch == "-":
            out.append(ch)
        elif "一" <= ch <= "鿿" or "぀" <= ch <= "ヿ":
            out.append(ch)
        # 其餘標點直接丟棄
    s = "".join(out)
    s = s.strip().replace(" ", "-")
    # 注意：不可收斂連續連字號 — GitHub slug 對「CI / GitHub」會產生 "ci--github"
    # （移除 / 後左右兩個空白各轉一個連字號），收斂會讓目錄 anchor 對不上。
    return s


def render_inline(text: str) -> str:
    """行內：先抽出 code span 保護，再處理 bold / link，最後跳脫。"""
    tokens: list[str] = []

    def stash(htmlfrag: str) -> str:
        tokens.append(htmlfrag)
        return f"\x00{len(tokens) - 1}\x00"

    # inline code
    def code_repl(m: re.Match) -> str:
        return stash(f"<code>{html.escape(m.group(1))}</code>")

    text = re.sub(r"`([^`]+)`", code_repl, text)

    # links [text](url)
    def link_repl(m: re.Match) -> str:
        label = m.group(1)
        url = m.group(2)
        # 純頁內 anchor（目錄）保留；外部連結開新分頁
        if url.startswith("#"):
            return stash(f'<a href="{html.escape(url)}">{html.escape(label)}</a>')
        return stash(
            f'<a href="{html.escape(url)}" target="_blank" '
            f'rel="noopener">{html.escape(label)}</a>'
        )

    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_repl, text)

    # bold
    def bold_repl(m: re.Match) -> str:
        return stash(f"<strong>{html.escape(m.group(1))}</strong>")

    text = re.sub(r"\*\*([^*]+)\*\*", bold_repl, text)

    # escape remaining
    text = html.escape(text)

    # restore tokens
    def restore(m: re.Match) -> str:
        return tokens[int(m.group(1))]

    text = re.sub(r"\x00(\d+)\x00", restore, text)
    return text


def md_to_html(md: str) -> tuple[str, list[tuple[int, str, str]]]:
    """回傳 (body_html, headings)。headings = [(level, text, slug), ...]
    （只收 h2/h3 給左側目錄；level 為原始 markdown # 數）。"""
    lines = md.split("\n")
    out: list[str] = []
    headings: list[tuple[int, str, str]] = []
    i = 0
    n = len(lines)
    list_stack: list[str] = []  # 'ul' / 'ol'

    def close_lists(level: int = 0) -> None:
        while len(list_stack) > level:
            out.append(f"</{list_stack.pop()}>")

    while i < n:
        line = lines[i]

        # fenced code
        m = re.match(r"^```(\w*)\s*$", line)
        if m:
            close_lists()
            lang = m.group(1)
            buf = []
            i += 1
            while i < n and not re.match(r"^```\s*$", lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            code = highlight_code("\n".join(buf), lang)
            cls = f' class="lang-{lang}"' if lang else ""
            out.append(f"<pre><code{cls}>{code}</code></pre>")
            continue

        # horizontal rule
        if re.match(r"^---+\s*$", line):
            close_lists()
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            close_lists()
            level = len(m.group(1))
            text = m.group(2).strip()
            # 跳過 markdown 內建「目錄」整段（標題 + 後續清單），改用左側 sidebar
            if level == 2 and text.replace(" ", "") == "目錄":
                i += 1
                while i < n and not re.match(r"^#{1,3}\s+", lines[i]):
                    if re.match(r"^---+\s*$", lines[i]):
                        i += 1
                        break
                    i += 1
                continue
            slug = slugify(text)
            tag = f"h{min(level + 1, 6)}"  # # → h2
            out.append(f'<{tag} id="{slug}">{render_inline(text)}</{tag}>')
            if level in (2, 3):
                headings.append((level, text, slug))
            i += 1
            continue

        # blockquote (may span multiple lines)
        if re.match(r"^>\s?", line):
            close_lists()
            buf = []
            while i < n and re.match(r"^>\s?", lines[i]):
                buf.append(re.sub(r"^>\s?", "", lines[i]))
                i += 1
            inner = render_inline(" ".join(b for b in buf if b.strip()))
            out.append(f"<blockquote><p>{inner}</p></blockquote>")
            continue

        # table: header row + separator row
        if line.lstrip().startswith("|") and i + 1 < n and re.match(
            r"^\s*\|?[\s:|-]+\|?\s*$", lines[i + 1]
        ) and "-" in lines[i + 1]:
            def split_row(row: str) -> list[str]:
                row = row.strip()
                if row.startswith("|"):
                    row = row[1:]
                if row.endswith("|"):
                    row = row[:-1]
                return [c.strip() for c in row.split("|")]

            close_lists()
            headers = split_row(line)
            i += 2  # skip header + separator
            body_rows = []
            while i < n and lines[i].lstrip().startswith("|"):
                body_rows.append(split_row(lines[i]))
                i += 1
            thead = "".join(f"<th>{render_inline(h)}</th>" for h in headers)
            tbody = ""
            for r in body_rows:
                cells = "".join(f"<td>{render_inline(c)}</td>" for c in r)
                tbody += f"<tr>{cells}</tr>"
            out.append(
                f'<div class="table-wrap"><table><thead><tr>{thead}</tr>'
                f"</thead><tbody>{tbody}</tbody></table></div>"
            )
            continue

        # list item (unordered / ordered), supports one nesting level
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", line)
        if m:
            indent = len(m.group(1))
            ordered = bool(re.match(r"^\d+\.$", m.group(2)))
            kind = "ol" if ordered else "ul"
            depth = 1 if indent >= 2 else 0
            target_level = depth + 1
            # adjust stack
            while len(list_stack) > target_level:
                out.append(f"</{list_stack.pop()}>")
            while len(list_stack) < target_level:
                out.append(f"<{kind}>")
                list_stack.append(kind)
            out.append(f"<li>{render_inline(m.group(3))}</li>")
            i += 1
            continue

        # blank line
        if not line.strip():
            close_lists()
            i += 1
            continue

        # paragraph (gather until blank / block start)
        close_lists()
        buf = [line]
        i += 1
        while i < n and lines[i].strip() and not re.match(
            r"^(#{1,6}\s|```|>|\s*([-*]|\d+\.)\s|---+\s*$|\|)", lines[i]
        ):
            buf.append(lines[i])
            i += 1
        out.append(f"<p>{render_inline(' '.join(buf))}</p>")

    close_lists()
    return out, headings


PAGE = """\
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Jason Tools 文件工具箱 API 使用手冊：所有工具的 REST API 端點、認證方式、請求／回應格式與整合範例（cURL / Python / Node.js / CI）。">
<title>API 使用手冊 — Jason Tools 文件工具箱</title>
<link rel="icon" type="image/png" sizes="32x32" href="favicon-32.png">
<link rel="icon" type="image/png" sizes="192x192" href="favicon-192.png">
<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">
<link rel="stylesheet" href="style.css">
</head>
<body>

<header class="topbar">
  <div class="container topbar-inner">
    <a class="brand" href="index.html#top">
      <div class="brand-mark">
        <svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>
        </svg>
      </div>
      <div class="brand-text">
        <div class="brand-name">Jason Tools 文件工具箱</div>
        <div class="brand-sub">jt-doc-tools</div>
      </div>
    </a>
    <nav class="topnav" id="topnav">
      <a href="index.html#features" class="nav-link">功能</a>
      <a href="index.html#install" class="nav-link">安裝</a>
      <a href="api.html" class="nav-link">API</a>
      <a class="btn btn-outline nav-github" href="https://github.com/jasoncheng7115/jt-doc-tools" target="_blank" rel="noopener">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.6-1.4-1.4-1.8-1.4-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.9 1.2 1.9 1.2 1.1 1.9 2.9 1.4 3.6 1 .1-.8.4-1.4.8-1.7-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2 0-.4-.5-1.6.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.6.2 2.8.1 3.2.8.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .3"/></svg>
        GitHub
      </a>
    </nav>
    <button class="nav-toggle" id="navToggle" type="button" aria-label="展開選單" aria-controls="topnav" aria-expanded="false">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>
      </svg>
    </button>
  </div>
</header>
<script>
(function () {
  var toggle = document.getElementById('navToggle');
  var nav = document.getElementById('topnav');
  if (!toggle || !nav) return;
  toggle.addEventListener('click', function () {
    var open = nav.classList.toggle('open');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  nav.addEventListener('click', function (e) {
    if (e.target.closest('a') && nav.classList.contains('open')) {
      nav.classList.remove('open');
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
})();
</script>

<main class="doc-page">
  <div class="doc-shell">
    <aside class="doc-nav" id="docNav">
      <div class="doc-nav-h">API 手冊</div>
      <div class="doc-search">
        <input type="search" id="docSearch" placeholder="搜尋工具 / 端點…"
               autocomplete="off" aria-label="搜尋 API 端點">
        <button type="button" id="docSearchClear" class="doc-search-clear"
                aria-label="清除" hidden>&times;</button>
      </div>
      <div class="doc-search-empty" id="docSearchEmpty" hidden>找不到符合的項目</div>
      <nav class="doc-nav-list">
__NAV__
      </nav>
    </aside>
    <article class="doc-content">
__SECTIONS__
      <nav class="doc-prevnext" id="docPrevNext">
        <a class="pn pn-prev" href="#" hidden><span>上一頁</span><b></b></a>
        <a class="pn pn-next" href="#" hidden><span>下一頁</span><b></b></a>
      </nav>
    </article>
    <aside class="doc-otp is-empty" id="docOtp">
      <div class="otp-h">本節細項</div>
      <nav class="otp-list" id="otpList"></nav>
    </aside>
  </div>
</main>
<script>
(function () {
  // 左欄：章節 + 其下各端點（可收折）。每「章」一頁切換。
  // 右欄：只放「目前章節內更細的小標題」(h5)，沒有就隱藏。
  var sections = Array.prototype.slice.call(
    document.querySelectorAll('.doc-section'));
  var navLinks = Array.prototype.slice.call(
    document.querySelectorAll('.doc-nav-list a'));
  var otpHost = document.getElementById('docOtp');
  var otpList = document.getElementById('otpList');
  var pnPrev = document.querySelector('.pn-prev');
  var pnNext = document.querySelector('.pn-next');
  if (!sections.length) return;
  var order = sections.map(function (s) { return s.dataset.sec; });
  var spyHandler = null;

  // 章節收折：點整列章名（或前方三角）即可收折，章名本身不連到任何頁面
  function toggleGroup(head) {
    var grp = head.closest('.nav-group');
    if (!grp) return;
    var c = grp.classList.toggle('collapsed');
    head.setAttribute('aria-expanded', c ? 'false' : 'true');
  }
  document.querySelectorAll('.nav-group-head').forEach(function (head) {
    head.addEventListener('click', function (e) {
      e.preventDefault();
      toggleGroup(head);
    });
    head.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggleGroup(head);
      }
    });
  });

  function buildOtp(sec) {
    otpList.innerHTML = '';
    var subs = Array.prototype.slice.call(sec.querySelectorAll('h5[id]'));
    if (!subs.length) { otpHost.classList.add('is-empty'); return []; }
    otpHost.classList.remove('is-empty');
    subs.forEach(function (h) {
      var a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent;
      a.addEventListener('click', function (e) {
        e.preventDefault();
        h.scrollIntoView({ behavior: 'smooth', block: 'start' });
        history.replaceState(null, '', '#' + h.id);
      });
      otpList.appendChild(a);
    });
    return subs;
  }

  function spyTargets(subs) {
    // 每頁只有單一端點，僅右欄「本節細項」(h5) 隨捲動高亮
    if (spyHandler) window.removeEventListener('scroll', spyHandler);
    if (!subs.length) return;
    var otpLinks = Array.prototype.slice.call(otpList.querySelectorAll('a'));
    spyHandler = function () {
      var top = window.scrollY + 130;
      var cur = subs[0].id;
      subs.forEach(function (h) { if (h.offsetTop <= top) cur = h.id; });
      otpLinks.forEach(function (a) {
        a.classList.toggle('active', a.getAttribute('href') === '#' + cur);
      });
    };
    window.addEventListener('scroll', spyHandler, { passive: true });
    spyHandler();
  }

  function setPrevNext(idx) {
    if (idx > 0) {
      pnPrev.hidden = false;
      pnPrev.setAttribute('href', '#' + sections[idx - 1].dataset.sec);
      pnPrev.querySelector('b').textContent = sections[idx - 1].dataset.title;
    } else { pnPrev.hidden = true; }
    if (idx < sections.length - 1) {
      pnNext.hidden = false;
      pnNext.setAttribute('href', '#' + sections[idx + 1].dataset.sec);
      pnNext.querySelector('b').textContent = sections[idx + 1].dataset.title;
    } else { pnNext.hidden = true; }
  }

  function activate(id, opts) {
    opts = opts || {};
    var el = id ? document.getElementById(id) : null;
    var sec = el ? el.closest('.doc-section') : null;
    if (!sec) {
      for (var i = 0; i < sections.length; i++) {
        if (sections[i].dataset.sec === id) { sec = sections[i]; break; }
      }
    }
    if (!sec) sec = sections[0];
    sections.forEach(function (s) { s.hidden = (s !== sec); });
    var slug = sec.dataset.sec;
    navLinks.forEach(function (a) { a.classList.remove('active', 'nav-current'); });
    var activeLink = navLinks.filter(function (a) {
      return a.getAttribute('href') === '#' + slug;
    })[0];
    if (activeLink) {
      activeLink.classList.add('active');
      var grp = activeLink.closest('.nav-group');
      if (grp) {
        grp.classList.remove('collapsed');
        // 端點頁 → 父章節給細微標示（粗體），提供章節脈絡
        var chLink = grp.querySelector('.nav-ch');
        if (chLink && chLink !== activeLink) chLink.classList.add('nav-current');
      }
    }
    var subs = buildOtp(sec);
    spyTargets(subs);
    setPrevNext(order.indexOf(slug));
    if (!opts.keepScroll) window.scrollTo({ top: 0 });
  }

  function curHash() {
    try { return decodeURIComponent(location.hash.slice(1)); }
    catch (e) { return location.hash.slice(1); }
  }
  function onClick(e) {
    var a = e.currentTarget;
    var id = decodeURIComponent(a.getAttribute('href').slice(1));
    if (!id) return;
    e.preventDefault();
    if (curHash() === id) activate(id);
    else location.hash = id;
  }
  navLinks.forEach(function (a) { a.addEventListener('click', onClick); });
  pnPrev.addEventListener('click', onClick);
  pnNext.addEventListener('click', onClick);
  window.addEventListener('hashchange', function () { activate(curHash()); });
  activate(curHash(), { keepScroll: !!location.hash });

  // 搜尋 / 過濾左欄章節與端點
  var searchEl = document.getElementById('docSearch');
  var clearEl = document.getElementById('docSearchClear');
  var emptyEl = document.getElementById('docSearchEmpty');
  var groups = Array.prototype.slice.call(document.querySelectorAll('.nav-group'));

  function runFilter(q) {
    q = (q || '').trim().toLowerCase();
    clearEl.hidden = !q;
    var anyShown = false;
    groups.forEach(function (g) {
      var chLink = g.querySelector('.nav-ch');
      var chText = chLink ? chLink.textContent.toLowerCase() : '';
      var eps = Array.prototype.slice.call(g.querySelectorAll('.nav-ep'));
      if (!q) {
        g.hidden = false;
        eps.forEach(function (a) { a.hidden = false; });
        return;
      }
      var chHit = chText.indexOf(q) >= 0;
      var epHits = 0;
      eps.forEach(function (a) {
        var hit = chHit || a.textContent.toLowerCase().indexOf(q) >= 0;
        a.hidden = !hit;
        if (hit) epHits++;
      });
      // 章名命中 → 整章顯示；否則只在有端點命中時顯示
      var show = chHit || epHits > 0;
      g.hidden = !show;
      if (show) {
        anyShown = true;
        g.classList.remove('collapsed');  // 搜尋時自動展開
      }
    });
    emptyEl.hidden = !q || anyShown;
  }

  if (searchEl) {
    searchEl.addEventListener('input', function () { runFilter(searchEl.value); });
    searchEl.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { searchEl.value = ''; runFilter(''); }
    });
  }
  if (clearEl) {
    clearEl.addEventListener('click', function () {
      searchEl.value = ''; runFilter(''); searchEl.focus();
    });
  }
})();
</script>

<footer class="footer">
  <div class="container footer-inner">
    <div class="footer-col">
      <div class="footer-brand">Jason Tools 文件工具箱</div>
      <p class="footer-tag">PDF / Office 整合式文件處理平台：不上雲，資料留在自己手中。</p>
    </div>
    <div class="footer-col">
      <div class="footer-h">連結</div>
      <a href="index.html">介紹網站</a>
      <a href="https://github.com/jasoncheng7115/jt-doc-tools" target="_blank" rel="noopener">原始碼庫</a>
      <a href="https://github.com/jasoncheng7115/jt-doc-tools/blob/main/github/CHANGELOG.md" target="_blank" rel="noopener">Changelog</a>
      <a href="https://github.com/jasoncheng7115/jt-doc-tools/issues" target="_blank" rel="noopener">回報問題</a>
    </div>
    <div class="footer-col">
      <div class="footer-h">作者</div>
      <p>
        <strong>Jason Cheng</strong><br>
        <a href="https://[網址]" target="_blank" rel="noopener">Jason Tools</a>
      </p>
    </div>
    <div class="footer-col">
      <div class="footer-h">授權</div>
      <p>Apache License 2.0</p>
    </div>
  </div>
  <div class="footer-bottom">
    <div class="container">
      © 2026 Jason Tools · Built with HTML & CSS, hosted on GitHub Pages
    </div>
  </div>
</footer>

</body>
</html>
"""


def _clean_label(text: str) -> str:
    label = re.sub(r"`([^`]*)`", r"\1", text)
    label = re.sub(r"\*\*([^*]*)\*\*", r"\1", label)
    return html.escape(label)


_INTRO_SLUG = "intro"
_INTRO_TITLE = "簡介"


def _title_from_h3(block: str) -> str:
    m = re.match(r'<h3 id="[^"]+">(.*)</h3>', block)
    if not m:
        return ""
    # 去掉行內標籤給左欄 / data-title 用
    return re.sub(r"<[^>]+>", "", m.group(1))


def _heading_title(block: str, tag: str) -> str:
    m = re.match(rf'<{tag} id="[^"]+">(.*)</{tag}>', block)
    return re.sub(r"<[^>]+>", "", m.group(1)) if m else ""


class _Chapter:
    def __init__(self, slug: str, title: str):
        self.slug = slug
        self.title = title
        self.lead: list[str] = []  # h3 + 章節前言（第一個 h4 之前）
        self.eps: list[tuple[str, str, list[str]]] = []  # (slug, title, blocks)


def build_sections(blocks: list[str]) -> tuple[str, str]:
    """每個「節」(端點 h4) = 獨立一頁；章 (h3) 概覽自成一頁。
    左欄為階層式：章 + 其下端點，可收折。回傳 (nav_html, sections_html)。"""
    intro: list[str] = []
    chapters: list[_Chapter] = []
    cur_ch: _Chapter | None = None
    cur_ep: list[str] | None = None
    for b in blocks:
        m3 = re.match(r'<h3 id="([^"]+)"', b)
        m4 = re.match(r'<h4 id="([^"]+)"', b)
        if m3:
            cur_ch = _Chapter(m3.group(1), _heading_title(b, "h3"))
            cur_ch.lead.append(b)
            chapters.append(cur_ch)
            cur_ep = None
        elif m4 and cur_ch is not None:
            cur_ep = [b]
            cur_ch.eps.append((m4.group(1), _heading_title(b, "h4"), cur_ep))
        elif cur_ch is None:
            intro.append(b)
        elif cur_ep is None:
            cur_ch.lead.append(b)
        else:
            cur_ep.append(b)

    # pages：(slug, title, html_blocks)；nav：階層式
    pages: list[tuple[str, str, list[str]]] = []
    nav_rows: list[str] = []

    if intro:
        pages.append((_INTRO_SLUG, _INTRO_TITLE, intro))
        nav_rows.append('        <div class="nav-group nav-group-flat">')
        nav_rows.append(
            f'          <a class="nav-ch" href="#{_INTRO_SLUG}">'
            f"{html.escape(_INTRO_TITLE)}</a>"
        )
        nav_rows.append("        </div>")

    for ch in chapters:
        if ch.eps:
            # 章名沒有頁面，只是收折用（純 span，非連結）。章節前言併入第一個
            # 端點頁；每個端點 = 獨立一頁。
            lead_extra = ch.lead[1:]  # 去掉 h3（導覽路徑已顯示章名）
            nav_rows.append('        <div class="nav-group">')
            nav_rows.append(
                '          <div class="nav-group-head" role="button" '
                'tabindex="0" aria-expanded="true">'
            )
            nav_rows.append(
                '            <button class="toc-toggle" type="button" '
                'tabindex="-1" aria-hidden="true"></button>'
            )
            nav_rows.append(
                f'            <span class="nav-ch nav-ch-toggle">'
                f"{html.escape(ch.title)}</span>"
            )
            nav_rows.append("          </div>")
            nav_rows.append('          <div class="nav-children">')
            for i, (ep_slug, ep_title, ep_blocks) in enumerate(ch.eps):
                # 端點頁：章節導覽路徑 + （首個端點併入章節前言）+ 端點內容
                crumb = (
                    f'<div class="doc-crumb">{html.escape(ch.title)}</div>'
                )
                extra = lead_extra if i == 0 else []
                pages.append((ep_slug, ep_title, [crumb] + extra + ep_blocks))
                nav_rows.append(
                    f'            <a class="nav-ep" href="#{ep_slug}">'
                    f"{html.escape(ep_title)}</a>"
                )
            nav_rows.append("          </div>")
            nav_rows.append("        </div>")
        else:
            pages.append((ch.slug, ch.title, ch.lead))
            nav_rows.append('        <div class="nav-group nav-group-flat">')
            nav_rows.append(
                f'          <a class="nav-ch" href="#{ch.slug}">'
                f"{html.escape(ch.title)}</a>"
            )
            nav_rows.append("        </div>")

    sec_rows = []
    for idx, (slug, title, blks) in enumerate(pages):
        hidden = "" if idx == 0 else " hidden"
        sec_rows.append(
            f'      <section class="doc-section" data-sec="{slug}" '
            f'data-title="{html.escape(title)}"{hidden}>'
        )
        sec_rows.append("\n".join(blks))
        sec_rows.append("      </section>")
    return "\n".join(nav_rows), "\n".join(sec_rows)


def main() -> None:
    md = SRC.read_text(encoding="utf-8")
    blocks, _headings = md_to_html(md)
    nav_html, sections_html = build_sections(blocks)
    page = (
        PAGE.replace("__NAV__", nav_html)
        .replace("__SECTIONS__", sections_html)
    )
    OUT.write_text(page, encoding="utf-8")
    n_sec = sections_html.count('<section class="doc-section"')
    print(f"wrote {OUT} ({len(page)} bytes), {n_sec} chapters")


if __name__ == "__main__":
    main()

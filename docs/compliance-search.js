/* 合規頁的頁內搜尋（compliance.html、compliance-en.html、compliance-ja.html 共用）。
 *
 * 搜尋的單位是「項目」：表格的一列、條列的一項、一段說明、一張卡片、一張截圖。
 * 符合的留下來，其他藏起來；一個項目也不剩的表格、小節、區塊整塊收起來。
 *
 * 比對前兩邊做同樣的正規化：全形半形（NFKC）、大小寫、「台」「臺」，並且不看空白
 * （「第12條」找得到「第 12 條」）。多個關鍵字（空白隔開）先當成一整句找；整頁都沒有的話，
 * 改成每一個字都要出現。
 *
 * 卡片：標題符合就整張留著；不然只留符合的條列、「對應」或「如何驗證」（符合的話自動展開）。
 * 底色用 CSS Custom Highlight API 標，不改動頁面內容（不支援的瀏覽器只是沒有底色）。
 *
 * 清掉搜尋（Esc 或清空）時回到完整的頁面，並停在剛才最上面那一個結果的位置；
 * 搜尋中按「對應」的條號或頁內導覽，先清掉搜尋再跳過去（不然目標可能正被藏著）。
 * 網址帶 `?q=` 會直接搜尋。
 */
(function () {
  'use strict';
  var input = document.getElementById('cpSearch');
  var main = document.querySelector('.cp-main');
  if (!input || !main) return;
  var countEl = document.getElementById('cpSearchN');
  var tplEl = document.getElementById('cpSearchTpl');
  var tpl = tplEl ? tplEl.textContent : '{n}';
  var empty = document.getElementById('cpSearchEmpty');
  var nav = document.querySelector('.cp-pagenav');
  var HL = (window.CSS && CSS.highlights && window.Highlight) ? 'cp-hit' : null;

  function norm(s) {
    return (s || '').normalize('NFKC').toLowerCase().replace(/台/g, '臺');
  }
  function flat(s) { return norm(s).replace(/\s+/g, ''); }
  function all(sel, root) { return Array.prototype.slice.call((root || main).querySelectorAll(sel)); }

  // ---- 項目 ----
  var units = [];        // {el, text, card?}
  var cards = [];        // {el, head, parts: [{el, text}]}
  all('.cp-card').forEach(function (card) {
    var head = card.querySelector('.cp-card-head');
    var parts = all('.cp-bullets > li, .cp-refs, .cp-verify', card).map(function (el) {
      return { el: el, text: flat(el.textContent) };
    });
    cards.push({ el: card, headText: flat(head ? head.textContent : ''), parts: parts });
  });
  all('.cp-table tbody tr, .cp-bullets > li, .cp-sec-lead, .cp-p, .cp-note, .cp-sub-lead, .cp-flow, .cp-shot')
    .forEach(function (el) {
      if (el.closest('.cp-card')) return;
      units.push({ el: el, text: flat(el.textContent) });
    });
  //: 由內往外：裡面一個項目都不剩就收起來的容器
  var BOXES = '.cp-table-wrap, ul.cp-bullets, .cp-cards, .cp-gallery, .cp-subsec, section.cp-sec';

  var hidden = [];       // 這一輪藏起來的元素（清掉時一次還原）
  var opened = [];       // 這一輪自動展開的「如何驗證」
  var active = false;

  function hide(el) { if (!el.hidden) { el.hidden = true; hidden.push(el); } }
  function restore() {
    hidden.forEach(function (el) { el.hidden = false; });
    hidden = [];
    opened.forEach(function (d) { d.open = false; });
    opened = [];
  }

  function matcher(q) {
    var phrase = flat(q);
    var terms = norm(q).split(/\s+/).filter(Boolean);
    var hasPhrase = function (t) { return t.indexOf(phrase) >= 0; };
    if (terms.length < 2) return { test: hasPhrase, needles: [phrase] };
    var any = units.some(function (u) { return hasPhrase(u.text); }) ||
      cards.some(function (c) { return hasPhrase(c.headText) || c.parts.some(function (p) { return hasPhrase(p.text); }); });
    if (any) return { test: hasPhrase, needles: [phrase] };
    var flatTerms = terms.map(flat);
    return {
      test: function (t) { return flatTerms.every(function (w) { return t.indexOf(w) >= 0; }); },
      needles: flatTerms
    };
  }

  // ---- 標示 ----
  function ranges(el, needles, out) {
    // 把元素裡的文字攤平成正規化後的一串，記下每一個字來自哪個文字節點的哪個位置
    var map = [], s = '';
    var walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walk.nextNode())) {
      if (node.parentNode.closest && node.parentNode.closest('svg, [hidden]')) continue;
      var v = node.nodeValue;
      for (var i = 0; i < v.length; i++) {
        var ch = v[i];
        var n = ch.length ? norm(ch) : '';
        if (!n || /\s/.test(n)) continue;
        for (var k = 0; k < n.length; k++) { map.push([node, i]); s += n[k]; }
      }
    }
    needles.forEach(function (w) {
      if (!w) return;
      var at = s.indexOf(w);
      while (at >= 0) {
        var a = map[at], b = map[at + w.length - 1];
        var r = document.createRange();
        r.setStart(a[0], a[1]);
        r.setEnd(b[0], b[1] + 1);
        out.push(r);
        at = s.indexOf(w, at + w.length);
      }
    });
  }

  function paint(els, needles) {
    if (!HL) return;
    var rs = [];
    els.forEach(function (el) { ranges(el, needles, rs); });
    CSS.highlights.set(HL, new Highlight(...rs));
  }
  function unpaint() { if (HL) CSS.highlights.delete(HL); }

  // ---- 搜尋 ----
  function run(q) {
    restore();
    q = (q || '').trim();
    if (!q) {
      active = false;
      document.body.classList.remove('cp-searching');
      unpaint();
      countEl.textContent = '';
      countEl.classList.remove('cp-search-zero');
      if (empty) empty.hidden = true;
      return;
    }
    active = true;
    document.body.classList.add('cp-searching');
    var m = matcher(q), hits = [], n = 0;

    units.forEach(function (u) {
      if (m.test(u.text)) { hits.push(u.el); n++; } else hide(u.el);
    });
    cards.forEach(function (c) {
      if (m.test(c.headText)) { hits.push(c.el); n++; return; }
      var any = false;
      c.parts.forEach(function (p) {
        if (m.test(p.text)) {
          any = true;
          hits.push(p.el);
          if (p.el.tagName === 'DETAILS' && !p.el.open) { p.el.open = true; opened.push(p.el); }
        } else hide(p.el);
      });
      if (any) n++; else hide(c.el);
    });
    all('.cp-jump').forEach(hide);
    // 由內往外收：容器裡一個看得到的項目都沒有就收起來
    all(BOXES).reverse().forEach(function (box) {
      if (box.hidden) return;
      var keep = all('.cp-table tbody tr, li, .cp-card, .cp-sec-lead, .cp-p, .cp-note, .cp-sub-lead, .cp-flow, .cp-shot', box)
        .some(function (el) { return !el.hidden && !el.closest('[hidden]'); });
      if (!keep) hide(box);
    });

    countEl.textContent = tpl.replace('{n}', n);
    countEl.classList.toggle('cp-search-zero', n === 0);
    if (empty) empty.hidden = n > 0;
    paint(hits, m.needles);
  }

  // 介紹站整頁是平滑捲動；搜尋造成的捲動要立即到位（不然版面一變，動畫停在半路）
  function jump(y) { window.scrollTo({ top: Math.max(0, y), left: 0, behavior: 'instant' }); }
  // 頁內導覽黏在頂端時的下緣（導覽列＋頁內導覽）；頁首還在畫面上時它的實際位置比這個低
  function stuckBottom() {
    if (!nav) return 0;
    return (parseFloat(getComputedStyle(nav).top) || 0) + nav.offsetHeight;
  }

  // 清掉之前記下最上面那一個結果，清掉之後停在它的位置
  function firstVisible() {
    var top = nav ? nav.getBoundingClientRect().bottom : 0;
    var cand = all('.cp-table tbody tr, .cp-card, .cp-bullets > li, .cp-p, .cp-note, .cp-sec-lead, .cp-sub-lead');
    for (var i = 0; i < cand.length; i++) {
      var el = cand[i];
      if (el.hidden || el.closest('[hidden]')) continue;
      if (el.getBoundingClientRect().bottom > top + 4) return el;
    }
    return null;
  }
  function clear(keepPlace) {
    var at = keepPlace && active ? firstVisible() : null;
    input.value = '';
    run('');
    if (at) jump(at.getBoundingClientRect().top + window.pageYOffset - stuckBottom() - 12);
  }

  var timer = null, last = '';
  input.addEventListener('input', function () {
    clearTimeout(timer);
    timer = setTimeout(function () {
      var q = input.value;
      if (!q.trim()) { clear(true); last = ''; return; }
      if (q === last) return;
      last = q;
      run(q);
      jump(0);
    }, 80);
  });
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { e.preventDefault(); clear(true); last = ''; }
  });
  // 「/」直接跳到搜尋框（正在輸入別的東西時不搶）
  document.addEventListener('keydown', function (e) {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    var t = e.target;
    if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
    e.preventDefault();
    input.focus();
  });
  // 搜尋中按頁內連結：先清掉搜尋，目標才看得到
  document.addEventListener('click', function (e) {
    if (!active) return;
    var a = e.target.closest && e.target.closest('a[href^="#"]');
    if (a) { clear(false); last = ''; }
  }, true);

  var q0 = null;
  try { q0 = new URLSearchParams(location.search).get('q'); } catch (err) { q0 = null; }
  if (q0) { input.value = q0; last = q0; run(q0); }
})();

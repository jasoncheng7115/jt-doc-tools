// 機關名稱欄位：從「公文電子交換系統地址簿」挑全銜，挑了一起記住機關代碼（電子公文要用）。
//
// 樣板在輸入框加 `data-org-pick="single"`（一格一個機關）或 `"multi"`（正本、副本：一格好幾個，
// 「、」「，」分隔），頁面上另放一個設定元素（伺服器端填好地址簿的狀態）：
//
//   <div id="orgPickerCfg" hidden data-endpoint="/tools/official-doc/orgs" data-installed="1"
//        data-stale="0" data-days="2" data-updated="2026/10/07" data-admin="1"
//        data-settings="/admin/official-doc"></div>
//
// 規則（2026-10-09 使用者：「要有一個可以從地址簿選的按鈕」「隨打即找，這樣才能帶入機關代碼」
// 「原本的看起來像 tooltip，不像可以選的」「地址簿還沒資料或太久沒更新要彈出提醒」）：
//
// * 清單是**本站樣式**（跟 jt-select 同一組外觀），不是原生 datalist —— 原生的在 Mac 上是一塊黑色提示框，
//   也放不下機關代碼。打字停 200 ms、兩個字以上才查；輸入框裡右邊的 ▼ 也打得開。
// * **代碼以「名稱 → 代碼」記**：選了清單的那一筆才記；之後改了字，名稱就對不上，代碼自然不算數
//   （不留過期代碼）。沒從清單選、自己打完整名稱的，伺服器說「名稱完全相同而且只有一筆」（`exact`）才記。
// * 一格好幾個機關時，只查、只換**游標所在的那一段**。
// * 地址簿沒下載、或超過門檻天數沒更新：第一次打字或按 ▼ 時跳一次提醒（同一頁只跳一次），
//   一般使用者「請通知管理員」，管理員直接給設定頁的連結。
(function () {
  'use strict';

  var SEP = /[、，,；;]/;
  var DEBOUNCE_MS = 200;
  var MIN_CHARS = 2;
  var LIMIT = 12;

  var cfg = null;
  var warned = false;
  var known = Object.create(null);   // 名稱 → 機關代碼（這一頁挑過的、或地址簿裡名稱完全相同的）
  var fields = [];                   // 掛上去的每一格
  var openField = null;

  function readCfg() {
    var el = document.getElementById('orgPickerCfg');
    if (!el) return null;
    var d = el.dataset;
    return {
      endpoint: d.endpoint || '',
      installed: d.installed === '1',
      stale: d.stale === '1',
      days: d.days || '',
      updated: d.updated || '',
      admin: d.admin === '1',
      settings: d.settings || '',
    };
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // 地址簿沒有或太舊：第一次用的時候講一次
  function remind() {
    if (warned || !cfg) return;
    if (cfg.installed && !cfg.stale) return;
    warned = true;
    var msg = cfg.installed
      ? tr('機關地址簿已經 {0} 天沒更新（最後更新 {1}），機關改名或改制之後可能查不到。')
          .replace('{0}', cfg.days).replace('{1}', cfg.updated)
      : tr('機關地址簿還沒下載，挑不到機關，也帶不出機關代碼（電子公文要用）。');
    var html = esc(msg) + ' ';
    if (cfg.admin && cfg.settings) {
      html += '<a href="' + esc(cfg.settings) + '">' + esc(tr('前往公文撰擬設定')) + '</a>';
    } else {
      html += esc(tr('請通知管理員到「公文撰擬設定」下載或更新。'));
    }
    if (window.showAlert) window.showAlert(html, { title: tr('機關地址簿'), kind: 'warn', html: true });
  }

  // ---------------------------------------------------------------- 一格裡的「段」

  function isMulti(input) { return input.dataset.orgPick === 'multi'; }

  function segmentAt(input) {
    var v = input.value || '';
    if (!isMulti(input)) return { start: 0, end: v.length, text: v.trim() };
    var pos = input.selectionStart == null ? v.length : input.selectionStart;
    var start = 0, end = v.length, i;
    for (i = pos - 1; i >= 0; i--) { if (SEP.test(v.charAt(i))) { start = i + 1; break; } }
    for (i = pos; i < v.length; i++) { if (SEP.test(v.charAt(i))) { end = i; break; } }
    return { start: start, end: end, text: v.slice(start, end).trim() };
  }

  function namesIn(input) {
    var v = input.value || '';
    var parts = isMulti(input) ? v.split(SEP) : [v];
    return parts.map(function (s) { return s.trim(); }).filter(Boolean);
  }

  // ---------------------------------------------------------------- 代碼那一行

  function paintCode(f) {
    var names = namesIn(f.input);
    var have = names.filter(function (n) { return known[n]; });
    var line = f.codeLine;
    if (!have.length) { line.hidden = true; line.textContent = ''; line.title = ''; return; }
    var text = tr('機關代碼 {0}').replace('{0}', have.map(function (n) { return known[n]; }).join('、'));
    var miss = names.length - have.length;
    if (miss > 0) text += tr('（{0} 個沒有代碼）').replace('{0}', String(miss));
    line.textContent = text;
    line.title = have.map(function (n) { return n + '：' + known[n]; }).join('\n');
    line.hidden = false;
  }
  function paintAll() { fields.forEach(paintCode); }

  // ---------------------------------------------------------------- 清單

  function closeAll() { if (openField) openField.close(); }

  function attach(input) {
    if (!input || input.dataset.orgPickMounted) return;
    input.dataset.orgPickMounted = '1';
    var host = input.parentNode;
    host.classList.add('op-host');
    input.classList.add('op-input');
    input.setAttribute('autocomplete', 'off');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');

    var hist = host.querySelector('.fh-btn[data-fh-for="' + input.id + '"]');

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'op-btn';
    btn.tabIndex = -1;
    btn.title = tr('從機關地址簿挑');
    btn.setAttribute('aria-label', tr('從機關地址簿挑'));
    var caret = document.createElement('span');
    caret.className = 'op-caret';
    btn.appendChild(caret);
    host.appendChild(btn);

    var panel = document.createElement('div');
    panel.className = 'jt-select-panel op-panel';
    panel.setAttribute('role', 'listbox');
    panel.id = (input.id || 'op') + 'OrgList';
    panel.hidden = true;
    host.appendChild(panel);
    input.setAttribute('aria-controls', panel.id);

    var codeLine = document.createElement('p');
    codeLine.className = 'op-codeline';
    codeLine.hidden = true;
    codeLine.setAttribute('data-i18n', 'skip');   // 代碼是資料
    input.insertAdjacentElement('afterend', codeLine);

    var timer = null, seq = 0, items = [], active = -1, lastSeg = null, suppress = false;

    // 位置：疊在輸入框裡的右緣、「之前填過的」左邊。走 CSSOM（CSP 擋行內 style 屬性，不擋 CSSOM）。
    function place() {
      if (!input.getClientRects().length) return;
      var top = input.offsetTop + (input.offsetHeight - btn.offsetHeight) / 2;
      var right = host.clientWidth - (input.offsetLeft + input.offsetWidth) + 3 + (hist ? 28 : 0);
      btn.style.top = Math.round(top) + 'px';
      btn.style.right = Math.max(0, Math.round(right)) + 'px';
      if (!panel.hidden) placePanel();
    }
    function placePanel() {
      panel.style.top = Math.round(input.offsetTop + input.offsetHeight + 4) + 'px';
      panel.style.left = Math.round(input.offsetLeft) + 'px';
      panel.style.minWidth = Math.round(input.offsetWidth) + 'px';
    }
    if (window.ResizeObserver) {
      var ro = new ResizeObserver(place);
      ro.observe(input);
      ro.observe(host);
    }
    window.addEventListener('resize', place);
    place();

    function show() {
      if (openField && openField !== api) openField.close();
      openField = api;
      panel.hidden = false;
      placePanel();
      btn.classList.add('open');
      input.setAttribute('aria-expanded', 'true');
    }
    function close() {
      panel.hidden = true;
      btn.classList.remove('open');
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      active = -1;
      if (openField === api) openField = null;
    }

    function note(text) {
      panel.replaceChildren();
      items = [];
      active = -1;
      var p = document.createElement('div');
      p.className = 'op-empty';
      p.textContent = text;
      panel.appendChild(p);
      show();
    }

    // 符合處標亮：位置是伺服器算的（跟比對同一套正規化），以 code point 計，一律 Array.from 切
    function paintMarks(node, text, marks) {
      var chars = Array.from(text || ''), at = 0;
      (marks || []).forEach(function (m) {
        var s = m[0], e = m[1];
        if (s < at || e > chars.length || s >= e) return;
        if (s > at) node.appendChild(document.createTextNode(chars.slice(at, s).join('')));
        var mk = document.createElement('mark');
        mk.className = 'op-hl';
        mk.textContent = chars.slice(s, e).join('');
        node.appendChild(mk);
        at = e;
      });
      if (at < chars.length) node.appendChild(document.createTextNode(chars.slice(at).join('')));
    }

    // 清單只列前幾筆時，最下面一列講出「12 / 165 筆」並給「全部顯示」——
    // 不講的話，排在後面的機關看起來像是地址簿裡沒有（查「數位」時臺中市政府數位發展局
    // 排在第 37 筆，清單卻只列 12 筆）。
    function footer(shown, total, max, all) {
      if (!(total > shown) && !all) return;
      var f = document.createElement('div');
      f.className = 'op-more';
      var say = document.createElement('span');
      say.textContent = tr('{0} / {1} 筆').replace('{0}', shown).replace('{1}', total);
      f.appendChild(say);
      if (total > shown && shown < max) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'op-all';
        b.textContent = tr('全部顯示');
        b.addEventListener('click', function () { lookup(true, true); });
        f.appendChild(b);
      } else if (total > shown) {
        var n = document.createElement('span');
        n.className = 'op-narrow';
        n.textContent = tr('只列前 {0} 筆；多打幾個字可以縮小範圍').replace('{0}', shown);
        f.appendChild(n);
      }
      panel.appendChild(f);
    }

    function render(rows) {
      panel.replaceChildren();
      items = [];
      active = -1;
      rows.forEach(function (r, i) {
        var opt = document.createElement('div');
        opt.className = 'jt-select-option op-opt';
        opt.setAttribute('role', 'option');
        opt.id = panel.id + '-' + i;
        opt.dataset.i18n = 'skip';               // 機關名稱是資料
        var nm = document.createElement('span');
        nm.className = 'op-name';
        paintMarks(nm, r.name, r.marks);
        nm.title = r.name;
        var cd = document.createElement('span');
        cd.className = 'op-code';
        cd.textContent = r.id;
        opt.appendChild(nm);
        opt.appendChild(cd);
        opt.addEventListener('click', function () { pick(r); });
        panel.appendChild(opt);
        items.push({ el: opt, row: r });
      });
      show();
    }

    function setActive(i) {
      if (!items.length) return;
      active = (i + items.length) % items.length;
      items.forEach(function (it, k) { it.el.classList.toggle('op-active', k === active); });
      var cur = items[active].el;
      input.setAttribute('aria-activedescendant', cur.id);
      cur.scrollIntoView({ block: 'nearest' });
    }

    async function lookup(force, all) {
      var seg = segmentAt(input);
      lastSeg = seg;
      if (!cfg || !cfg.installed || !cfg.endpoint) return;
      if (seg.text.length < MIN_CHARS) {
        if (force) note(tr('輸入機關名稱兩個字以上')); else close();
        return;
      }
      var my = ++seq;
      try {
        var lim = all ? (cfg.max || 2000) : LIMIT;
        var r = await fetch(cfg.endpoint + '?limit=' + lim + '&q=' + encodeURIComponent(seg.text));
        if (!r.ok || my !== seq) return;
        var d = await r.json();
        if (my !== seq) return;
        if (d.exact) { known[seg.text] = d.exact; paintAll(); }
        var rows = (d.orgs || []).filter(function (o) { return o && o.name; });
        // 已經是完整名稱、清單只剩它自己：不用再跳出來
        if (!force && rows.length === 1 && rows[0].name === seg.text) { close(); return; }
        if (d.max) cfg.max = d.max;
        if (rows.length) {
          render(rows);
          footer(rows.length, d.total || rows.length, cfg.max || 2000, !!all);
        } else note(tr('地址簿裡找不到「{0}」').replace('{0}', seg.text));
      } catch (e) { /* 只是建議：查不到就不顯示 */ }
    }

    function pick(r) {
      var v = input.value || '';
      var seg = lastSeg || segmentAt(input);
      var before = v.slice(0, seg.start), after = v.slice(seg.end);
      // 一格好幾個機關時，分隔符號後面原本的空白留著不動
      var lead = isMulti(input) ? (v.slice(seg.start, seg.end).match(/^\s*/) || [''])[0] : '';
      input.value = before + lead + r.name + after;
      var caretAt = (before + lead + r.name).length;
      try { input.setSelectionRange(caretAt, caretAt); } catch (e) { /* 有些型別不支援 */ }
      if (r.id) known[r.name] = r.id;
      close();
      suppress = true;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
      suppress = false;
      paintAll();
      input.focus();
    }

    input.addEventListener('input', function () {
      paintAll();
      if (suppress) return;
      if ((input.value || '').trim()) remind();
      clearTimeout(timer);
      var seg = segmentAt(input);
      if (seg.text.length < MIN_CHARS) { close(); return; }
      timer = setTimeout(function () { lookup(false); }, DEBOUNCE_MS);
    });
    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (panel.hidden || !items.length) lookup(true); else setActive(active + 1);
      } else if (e.key === 'ArrowUp') {
        if (!panel.hidden && items.length) { e.preventDefault(); setActive(active - 1); }
      } else if (e.key === 'Enter') {
        if (!panel.hidden && active >= 0 && items[active]) { e.preventDefault(); pick(items[active].row); }
      } else if (e.key === 'Escape') {
        if (!panel.hidden) { e.preventDefault(); close(); }
      } else if (e.key === 'Tab') {
        close();
      }
    });
    input.addEventListener('blur', function () {
      // 點清單裡的一列時焦點不會離開（mousedown 擋掉），這裡只處理真的離開這一格
      setTimeout(function () { if (document.activeElement !== input) close(); }, 0);
    });
    // 按清單、按 ▼ 不要讓輸入框失去焦點
    panel.addEventListener('mousedown', function (e) { e.preventDefault(); });
    btn.addEventListener('mousedown', function (e) { e.preventDefault(); });
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (!panel.hidden) { close(); return; }
      if (!cfg || !cfg.installed) { warned = false; remind(); return; }
      remind();
      input.focus();
      lookup(true);
    });

    var api = { close: close, input: input, codeLine: codeLine, lookup: lookup };
    var f = { input: input, codeLine: codeLine, api: api };
    fields.push(f);
    input._orgPicker = api;
    return api;
  }

  document.addEventListener('click', function (e) {
    if (!openField) return;
    var host = openField.input.parentNode;
    if (!host.contains(e.target)) openField.close();
  });

  function attachAll(root) {
    if (!cfg) cfg = readCfg();
    (root || document).querySelectorAll('input[data-org-pick]').forEach(attach);
    paintAll();
  }

  // 送出用：掛上去的各格裡**現在還在**的名稱 → 代碼（改掉的、刪掉的名稱不送）
  function collect() {
    var out = {};
    fields.forEach(function (f) {
      namesIn(f.input).forEach(function (n) { if (known[n]) out[n] = known[n]; });
    });
    return out;
  }

  // 打開舊案件：把當初存的對照放回來
  function restore(map) {
    if (map && typeof map === 'object') {
      Object.keys(map).forEach(function (k) {
        if (typeof map[k] === 'string' && map[k]) known[k] = map[k];
      });
    }
    paintAll();
  }

  window.OrgPicker = { attach: attach, attachAll: attachAll, collect: collect, restore: restore,
                       config: function () { return cfg; } };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { attachAll(); });
  } else {
    attachAll();
  }
})();

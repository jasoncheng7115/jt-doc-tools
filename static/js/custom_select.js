// Custom select widget — replaces native <select class="jt-select"> with a
// styled dropdown that matches jt-doc-tools aesthetic. Supports <optgroup>,
// click outside to close, keyboard ESC, syncs back to original <select> so
// existing form / change-event JS keeps working.
(function () {
  class JtSelect {
    constructor(selectEl) {
      this.select = selectEl;
      if (selectEl.dataset.jtSelectMounted) return;
      selectEl.dataset.jtSelectMounted = '1';
      // 讓頁面程式拿得到這個實例（用程式改 `.value` 之後要 `refresh()`）
      selectEl._jtSelect = this;

      // Build wrapper so native + custom DOM live together
      const wrap = document.createElement('div');
      wrap.className = 'jt-select-wrap';
      // Inherit style "width"/"max-width" from original select to keep layout
      const w = selectEl.style.width || selectEl.style.maxWidth || '';
      if (w) wrap.style.maxWidth = w;
      // **`data-i18n` 要傳給外層** —— 可見的那份清單是這裡另外建的節點，
      //  不在原本的 `<select>` 裡面，`closest('[data-i18n="skip"]')` 找不到。
      //  標在 select 上的「不要翻」於是只擋到隱藏的原生 option，
      //  畫面上那一份照樣被判成漏翻（2026-09-16 個資限用章的用途範本）。
      if (selectEl.dataset.i18n) wrap.dataset.i18n = selectEl.dataset.i18n;
      selectEl.parentNode.insertBefore(wrap, selectEl);

      // Hidden native (keeps form/.value semantics)
      selectEl.classList.add('jt-select-native');
      selectEl.tabIndex = -1;
      wrap.appendChild(selectEl);

      // Visible trigger
      this.trigger = document.createElement('button');
      this.trigger.type = 'button';
      this.trigger.className = 'jt-select-trigger';
      this.trigger.setAttribute('aria-haspopup', 'listbox');
      this.trigger.setAttribute('aria-expanded', 'false');
      wrap.appendChild(this.trigger);

      // Dropdown panel
      this.panel = document.createElement('div');
      this.panel.className = 'jt-select-panel';
      this.panel.hidden = true;
      this.panel.setAttribute('role', 'listbox');
      wrap.appendChild(this.panel);

      this.wrap = wrap;
      // 選項帶色票時面板放寬、說明可以折行（版面主題那種「名稱 ＋ 一句說明」）
      if (selectEl.querySelector('option[data-swatches]')) wrap.classList.add('has-swatches');
      this._buildPanel();
      this._sync();
      this._bind();
    }

    _buildPanel() {
      this.panel.innerHTML = '';
      const items = [];
      // **隨打即找**（選用）：`<select data-search>` 才有。選項很多（例如挑使用者）時用。
      // 比對選項文字與 data-sub，不分大小寫；只是隱藏不符的項目，不改 <select> 本身。
      this.searchInput = null;
      if (this.select.hasAttribute('data-search')) {
        const box = document.createElement('div');
        box.className = 'jt-select-search';
        const inp = document.createElement('input');
        inp.type = 'search';
        inp.className = 'field';
        inp.placeholder = this.select.getAttribute('data-search') || '';
        inp.setAttribute('aria-label', inp.placeholder || 'search');
        inp.autocomplete = 'off';
        inp.addEventListener('click', e => e.stopPropagation());
        inp.addEventListener('input', () => this._filter(inp.value));
        inp.addEventListener('keydown', e => {
          if (e.key !== 'Escape') e.stopPropagation();   // 不要讓清單的鍵盤處理再跳一格
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            const first = (this._items || []).find(el => !el.hidden && !el.classList.contains('disabled'));
            if (first) { first.tabIndex = 0; first.focus(); }
          } else if (e.key === 'Enter') {
            e.preventDefault();
            const vis = (this._items || []).filter(el => !el.hidden && !el.classList.contains('disabled'));
            if (vis.length === 1) { this._pick(vis[0].dataset.value); this.trigger.focus(); }
          }
        });
        box.appendChild(inp);
        this.panel.appendChild(box);
        this.searchInput = inp;
        const none = document.createElement('div');
        none.className = 'jt-select-empty';
        none.hidden = true;
        none.textContent = this.select.getAttribute('data-search-empty') || '—';
        this._emptyEl = none;
      }
      Array.from(this.select.children).forEach(child => {
        if (child.tagName === 'OPTGROUP') {
          const lbl = document.createElement('div');
          lbl.className = 'jt-select-group';
          lbl.textContent = child.label || '';
          this.panel.appendChild(lbl);
          Array.from(child.children).forEach(opt => items.push(this._addOption(opt)));
        } else if (child.tagName === 'OPTION') {
          items.push(this._addOption(child));
        }
      });
      this._items = items;
      if (this._emptyEl) this.panel.appendChild(this._emptyEl);
    }

    _filter(q) {
      const needle = (q || '').trim().toLowerCase();
      let shown = 0;
      (this._items || []).forEach(el => {
        const hit = !needle || el.textContent.toLowerCase().includes(needle);
        el.hidden = !hit;
        if (hit) shown++;
      });
      if (this._emptyEl) this._emptyEl.hidden = shown > 0;
    }

    _addOption(opt) {
      const item = document.createElement('div');
      item.className = 'jt-select-option';
      item.dataset.value = opt.value;
      item.setAttribute('role', 'option');
      // 主標籤(可套 data-preview-style 預覽字型) + 可選次標籤(data-sub)
      const main = document.createElement('div');
      main.className = 'jt-select-option-main';
      const sw = this._swatches(opt);
      if (sw) {
        main.appendChild(sw);
        const txt = document.createElement('span');
        txt.textContent = opt.textContent;
        main.appendChild(txt);
      } else {
        main.textContent = opt.textContent;
      }
      const previewStyle = opt.getAttribute('data-preview-style');
      if (previewStyle) main.style.cssText = previewStyle;
      item.appendChild(main);
      const sub = opt.getAttribute('data-sub');
      if (sub) {
        const subEl = document.createElement('div');
        subEl.className = 'jt-select-option-sub';
        subEl.textContent = sub;
        item.appendChild(subEl);
      }
      if (opt.disabled) item.classList.add('disabled');
      item.addEventListener('click', () => {
        if (opt.disabled) return;
        this._pick(opt.value);
      });
      this.panel.appendChild(item);
      return item;
    }

    // `data-swatches="#1e3a8a,#3b82f6,#eff6ff"` → 一排小色塊。
    // **顏色走 CSSOM、而且只收 #rgb / #rrggbb**：CSP 擋行內 style 屬性，
    // 而這個值來自頁面的 HTML，不收任意字串（`url(...)` 之類）。
    _swatches(opt) {
      const raw = opt ? (opt.getAttribute('data-swatches') || '') : '';
      const cols = raw.split(',').map(c => c.trim())
        .filter(c => /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(c));
      if (!cols.length) return null;
      const box = document.createElement('span');
      box.className = 'jt-select-swatches';
      box.setAttribute('aria-hidden', 'true');
      cols.forEach(c => {
        const d = document.createElement('span');
        d.className = 'jt-select-swatch';
        d.style.backgroundColor = c;
        box.appendChild(d);
      });
      return box;
    }

    _pick(value) {
      if (this.select.value !== value) {
        this.select.value = value;
        this.select.dispatchEvent(new Event('change', { bubbles: true }));
      }
      this._sync();
      this.close();
    }

    _sync() {
      const v = this.select.value;
      const opt = this.select.querySelector(`option[value="${CSS.escape(v)}"]`);
      const label = opt ? opt.textContent : v;
      const previewStyle = opt ? (opt.getAttribute('data-preview-style') || '') : '';
      // **不可以用 innerHTML 拼 `style="..."` 屬性**：CSP 的 style-src 已經
      // 移除 'unsafe-inline'，那個屬性會被瀏覽器丟掉（字串留在 HTML 裡，但
      // `el.style.length === 0`），收合狀態的字型預覽因此永遠不生效 ——
      // 而面板展開後那份走 CSSOM（`main.style.cssText`）反而是好的，於是
      // 「打開看得到預覽、收起來就沒了」。改用 DOM API + CSSOM 兩者一致。
      this.trigger.textContent = '';
      const sw = this._swatches(opt);
      if (sw) this.trigger.appendChild(sw);
      const lab = document.createElement('span');
      lab.className = 'jt-select-label';
      lab.textContent = label;
      if (previewStyle) lab.style.cssText = previewStyle;
      const arrow = document.createElement('span');
      arrow.className = 'jt-select-arrow';
      arrow.setAttribute('aria-hidden', 'true');
      this.trigger.appendChild(lab);
      this.trigger.appendChild(arrow);
      (this._items || []).forEach(el => {
        el.classList.toggle('selected', el.dataset.value === v);
      });
    }


    _bind() {
      this.trigger.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggle();
      });
      document.addEventListener('click', (e) => {
        if (!this.wrap.contains(e.target)) this.close();
      });
      this.trigger.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
          e.preventDefault();
          this.open();
          if (!this.searchInput) this._focusSelectedItem();
        }
      });
      this.panel.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          this.close();
          this.trigger.focus();
          return;
        }
        const items = (this._items || []).filter(el => !el.classList.contains('disabled') && !el.hidden);
        const cur = document.activeElement;
        const idx = items.indexOf(cur);
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          const next = items[Math.min(items.length - 1, Math.max(0, idx + 1))];
          if (next) { next.tabIndex = 0; next.focus(); }
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          const prev = items[Math.max(0, idx - 1)];
          if (prev) { prev.tabIndex = 0; prev.focus(); }
        } else if (e.key === 'Enter' || e.key === ' ') {
          if (cur && cur.classList.contains('jt-select-option')) {
            e.preventDefault();
            this._pick(cur.dataset.value);
            this.trigger.focus();
          }
        }
      });
      // 頁面自己送 change（例如還原上次的選擇）時也要跟著更新顯示
      this.select.addEventListener('change', () => this._sync());
      // External programmatic .value change should re-sync
      const obs = new MutationObserver(() => this._sync());
      obs.observe(this.select, { attributes: true, attributeFilter: ['value'] });
    }

    _focusSelectedItem() {
      const sel = (this._items || []).find(el => el.classList.contains('selected')) ||
                  (this._items || [])[0];
      if (sel) { sel.tabIndex = 0; sel.focus(); }
    }

    toggle() { if (this.panel.hidden) this.open(); else this.close(); }
    open() {
      // Close other open instances
      document.querySelectorAll('.jt-select-trigger.open').forEach(t => {
        if (t !== this.trigger) t.click();
      });
      this.panel.hidden = false;
      this.trigger.classList.add('open');
      this.trigger.setAttribute('aria-expanded', 'true');
      // 計算空間，若下方不夠就翻向上(panel 顯示在 trigger 上方)
      this._adjustPanelDirection();
      // Scroll selected into view
      const sel = this.panel.querySelector('.jt-select-option.selected');
      if (sel) sel.scrollIntoView({ block: 'nearest' });
      if (this.searchInput) {
        this.searchInput.value = '';
        this._filter('');
        this.searchInput.focus();
      }
    }
    _adjustPanelDirection() {
      this.wrap.classList.remove('up');
      const rect = this.trigger.getBoundingClientRect();
      const panelHeight = this.panel.offsetHeight || 240;  // 沒 render 完先估
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const margin = 8;  // 留邊距
      if (spaceBelow < panelHeight + margin && spaceAbove > spaceBelow) {
        this.wrap.classList.add('up');
      }
    }
    close() {
      this.panel.hidden = true;
      this.trigger.classList.remove('open');
      this.trigger.setAttribute('aria-expanded', 'false');
    }

    refresh() { this._buildPanel(); this._sync(); }
  }

  function enhance(root) {
    (root || document).querySelectorAll('select.jt-select').forEach(s => new JtSelect(s));
  }
  window.JtSelect = JtSelect;
  window.enhanceJtSelects = enhance;
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => enhance());
  } else {
    enhance();
  }
})();

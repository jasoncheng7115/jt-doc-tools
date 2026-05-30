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

      // Build wrapper so native + custom DOM live together
      const wrap = document.createElement('div');
      wrap.className = 'jt-select-wrap';
      // Inherit style "width"/"max-width" from original select to keep layout
      const w = selectEl.style.width || selectEl.style.maxWidth || '';
      if (w) wrap.style.maxWidth = w;
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
      this._buildPanel();
      this._sync();
      this._bind();
    }

    _buildPanel() {
      this.panel.innerHTML = '';
      const items = [];
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
    }

    _addOption(opt) {
      const item = document.createElement('div');
      item.className = 'jt-select-option';
      item.dataset.value = opt.value;
      item.setAttribute('role', 'option');
      // 主標籤(可套 data-preview-style 預覽字型) + 可選次標籤(data-sub)
      const main = document.createElement('div');
      main.className = 'jt-select-option-main';
      main.textContent = opt.textContent;
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
      this.trigger.innerHTML =
        `<span class="jt-select-label"${previewStyle ? ` style="${this._escape(previewStyle)}"` : ''}>${this._escape(label)}</span>` +
        `<span class="jt-select-arrow" aria-hidden="true"></span>`;
      (this._items || []).forEach(el => {
        el.classList.toggle('selected', el.dataset.value === v);
      });
    }

    _escape(s) {
      return String(s).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
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
          this._focusSelectedItem();
        }
      });
      this.panel.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          this.close();
          this.trigger.focus();
          return;
        }
        const items = (this._items || []).filter(el => !el.classList.contains('disabled'));
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

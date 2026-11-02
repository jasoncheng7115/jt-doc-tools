// Custom modal helpers replacing window.alert / confirm / prompt.
// All return a Promise resolving to:
//   showAlert(msg)               → undefined when dismissed
//   showConfirm(msg, opts?)      → true if OK, false otherwise
//   showPrompt(msg, opts?)       → string or null
//
// opts (object) supports: title, okText, cancelText, kind ("info"|"warn"|"danger"),
// defaultValue (prompt only), placeholder (prompt only), html (boolean).
(function () {
  function ensureHost() {
    let host = document.getElementById('modal-host');
    if (!host) {
      host = document.createElement('div');
      host.id = 'modal-host';
      document.body.appendChild(host);
    }
    return host;
  }

  // 對話框圖示（inline SVG）：不依賴字型 / emoji，跨平台一致；CSP 下也免外部資源。
  const _SVG = 'xmlns="http://www.w3.org/2000/svg" width="17" height="17" viewBox="0 0 24 24"'
    + ' fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"'
    + ' stroke-linejoin="round"';
  function _kindIcon(kind) {
    if (kind === 'danger')
      return `<svg ${_SVG}><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>`;
    if (kind === 'warn')
      return `<svg ${_SVG}><circle cx="12" cy="12" r="9"/><path d="M12 8v5"/><path d="M12 16h.01"/></svg>`;
    return `<svg ${_SVG}><circle cx="12" cy="12" r="9"/><path d="M12 16v-5"/><path d="M12 8h.01"/></svg>`;
  }
  function _closeIcon() {
    return `<svg ${_SVG} width="15" height="15"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`;
  }


  // 對話框按鈕的圖示。
  //
  // 站上其他按鈕都是「圖示 + 文字」（模板走 `icon()` macro），只有對話框的
  // 確定 / 取消是純文字，看起來像少了什麼（使用者回報「關閉鈕缺少 icon」）。
  //
  // 這裡是 JS 動態產生，用不到 macro，所以**四個屬性要跟 macro 完全一致**
  // （width / height / class="ic" / stroke-width）—— 少了尺寸，SVG 會用
  // viewBox 的自然大小把按鈕撐到 50px 以上。
  const _ICON_PATHS = {
    check: ['M5 12l5 5L20 7'],
    close: ['M6 6l12 12', 'M6 18L18 6'],
  };

  function _iconSvg(name) {
    const paths = _ICON_PATHS[name];
    if (!paths) return null;
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'ic');
    svg.setAttribute('width', '16');
    svg.setAttribute('height', '16');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '1.6');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    paths.forEach((d) => {
      const el = document.createElementNS(NS, 'path');
      el.setAttribute('d', d);
      svg.appendChild(el);
    });
    return svg;
  }

  function _setBtnLabel(btn, iconName, text) {
    btn.textContent = '';
    const svg = iconName === false ? null : _iconSvg(iconName);
    if (svg) btn.appendChild(svg);
    btn.appendChild(document.createTextNode(text));
  }

  function _show({ title, body, kind, okText, cancelText, showCancel, prompt, defaultValue, placeholder, html, okIcon, cancelIcon, inputType }) {
    return new Promise((resolve) => {
      const host = ensureHost();
      const overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      const card = document.createElement('div');
      card.className = 'modal-card modal-' + (kind || 'info');
      // v1.5.4: 改用 createElement / textContent（不用 innerHTML 字串拼接）
      // 為了過 CodeQL `js/xss-through-dom`。html=true 是 caller 明示允許 raw HTML
      // 才走獨立分支（仍是 innerHTML，但 caller 自己負責安全性）。
      // 標題列：左側依 kind 自動配圖示（info / warn / danger），右側關閉鈕也帶圖示
      // —— 全站對話框一致。SVG 是本檔常數（非使用者輸入），標題文字仍走 textContent。
      if (title) {
        const titleEl = document.createElement('div');
        titleEl.className = 'modal-title';
        const ic = document.createElement('span');
        ic.className = 'modal-title-icon';
        ic.innerHTML = _kindIcon(kind);
        titleEl.appendChild(ic);
        const tx = document.createElement('span');
        tx.className = 'modal-title-text';
        tx.textContent = title;
        titleEl.appendChild(tx);
        const xb = document.createElement('button');
        xb.type = 'button';
        xb.className = 'modal-x';
        xb.setAttribute('aria-label', tr('關閉'));
        xb.title = tr('關閉');
        xb.innerHTML = _closeIcon();
        titleEl.appendChild(xb);
        card.appendChild(titleEl);
      }
      const bodyEl = document.createElement('div');
      bodyEl.className = 'modal-body';
      if (html) {
        // 即使呼叫端宣告 trusted，仍以 DOMPurify 清洗（剝除 script / 事件處理器 /
        // javascript: 等可執行內容）—— 防止意外夾帶的內容（如例外文字含
        // <img onerror>）被當 HTML 執行。DOMPurify 於 base.html 全站載入,且是
        // CodeQL 認得的 sanitizer（js/xss-through-dom、js/html-constructed-from-input
        // 都認 barrier）。萬一未載入 → 退成純文字（安全）。
        if (window.DOMPurify) {
          // 允許 target：說明裡的連結（例如「PDF 密碼解除」）要開新分頁，不然一點就離開目前的工具、
          // 剛設定好的東西全沒了。新分頁一律帶 rel=noopener（呼叫端寫），瀏覽器預設也是 noopener。
          bodyEl.innerHTML = window.DOMPurify.sanitize(String(body), { ADD_ATTR: ['target'] });
        } else {
          bodyEl.textContent = String(body);
        }
      } else {
        // Honour newlines as <br>: split + appendChild text + br nodes.
        const lines = String(body || '').split('\n');
        lines.forEach((ln, i) => {
          if (i > 0) bodyEl.appendChild(document.createElement('br'));
          bodyEl.appendChild(document.createTextNode(ln));
        });
      }
      card.appendChild(bodyEl);
      if (prompt) {
        const inputEl = document.createElement('input');
        inputEl.type = inputType === 'password' ? 'password' : 'text';
        if (inputType === 'password') inputEl.autocomplete = 'off';
        inputEl.className = 'modal-input';
        inputEl.value = defaultValue || '';
        inputEl.placeholder = placeholder || '';
        card.appendChild(inputEl);
      }
      const actionsEl = document.createElement('div');
      actionsEl.className = 'modal-actions';
      if (showCancel) {
        const cb = document.createElement('button');
        cb.type = 'button';
        cb.className = 'btn modal-cancel';
        _setBtnLabel(cb, cancelIcon === undefined ? 'close' : cancelIcon,
                     cancelText || tr('取消'));
        actionsEl.appendChild(cb);
      }
      const ob = document.createElement('button');
      ob.type = 'button';
      ob.className = 'btn btn-primary modal-ok';
      _setBtnLabel(ob, okIcon === undefined ? 'check' : okIcon,
                   okText || tr('確定'));
      actionsEl.appendChild(ob);
      card.appendChild(actionsEl);
      overlay.appendChild(card);
      host.appendChild(overlay);

      const input = card.querySelector('.modal-input');
      const okBtn = card.querySelector('.modal-ok');
      const cancelBtn = card.querySelector('.modal-cancel');

      function close(result) {
        overlay.classList.add('closing');
        setTimeout(() => overlay.remove(), 150);
        document.removeEventListener('keydown', onKey);
        resolve(result);
      }
      function onKey(e) {
        if (e.key === 'Escape') {
          e.preventDefault();
          close(prompt ? null : (showCancel ? false : undefined));
        } else if (e.key === 'Enter' && !e.shiftKey) {
          // Don't intercept Enter inside multi-line; we don't have textarea here anyway.
          if (document.activeElement && document.activeElement.tagName === 'TEXTAREA') return;
          e.preventDefault();
          okBtn.click();
        }
      }
      okBtn.addEventListener('click', () => {
        if (prompt) close(input ? input.value : '');
        else close(showCancel ? true : undefined);
      });
      if (cancelBtn) cancelBtn.addEventListener('click', () => close(prompt ? null : false));
      // 標題列的關閉鈕 = 等同取消 / Esc
      const xBtn = card.querySelector('.modal-x');
      if (xBtn) xBtn.addEventListener('click',
        () => close(prompt ? null : (showCancel ? false : undefined)));
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay) close(prompt ? null : (showCancel ? false : undefined));
      });
      document.addEventListener('keydown', onKey);
      // Animate in
      requestAnimationFrame(() => overlay.classList.add('show'));
      // Focus the right element
      setTimeout(() => (input || okBtn).focus(), 30);
      if (input) input.select();
    });
  }

  function escapeHTML(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  function escapeAttr(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  window.showAlert = function (msg, opts) {
    opts = opts || {};
    return _show({
      title: opts.title, body: msg, kind: opts.kind, html: opts.html,
      okText: opts.okText || tr('確定'), okIcon: opts.okIcon, showCancel: false,
    });
  };
  window.showConfirm = function (msg, opts) {
    opts = opts || {};
    return _show({
      title: opts.title, body: msg, kind: opts.kind || 'warn', html: opts.html,
      okText: opts.okText || tr('確定'), cancelText: opts.cancelText || tr('取消'),
      okIcon: opts.okIcon, cancelIcon: opts.cancelIcon,
      showCancel: true,
    });
  };
  window.showPrompt = function (msg, opts) {
    opts = opts || {};
    return _show({
      title: opts.title, body: msg, kind: opts.kind, html: opts.html,
      okText: opts.okText || tr('確定'), cancelText: opts.cancelText || tr('取消'),
      showCancel: true, prompt: true,
      defaultValue: opts.defaultValue || '', placeholder: opts.placeholder || '',
      inputType: opts.inputType,   // 'password'：輸入時不顯示
    });
  };

  // Non-invasive shims so legacy code calling alert/confirm/prompt still works.
  // Note: confirm/prompt are async now — return Promises. Existing callers that
  // do `if (confirm(...))` will not work; we audit & rewrite those.
  window.alert = (msg) => window.showAlert(String(msg));
  // Keep window.confirm and window.prompt as the real native ones for any old
  // synchronous code, since making them async would silently break flow.
})();

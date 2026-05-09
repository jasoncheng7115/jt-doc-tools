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

  function _show({ title, body, kind, okText, cancelText, showCancel, prompt, defaultValue, placeholder, html }) {
    return new Promise((resolve) => {
      const host = ensureHost();
      const overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      const card = document.createElement('div');
      card.className = 'modal-card modal-' + (kind || 'info');
      // v1.5.4: 改用 createElement / textContent（不用 innerHTML 字串拼接）
      // 為了過 CodeQL `js/xss-through-dom`。html=true 是 caller 明示允許 raw HTML
      // 才走獨立分支（仍是 innerHTML，但 caller 自己負責安全性）。
      if (title) {
        const titleEl = document.createElement('div');
        titleEl.className = 'modal-title';
        titleEl.textContent = title;
        card.appendChild(titleEl);
      }
      const bodyEl = document.createElement('div');
      bodyEl.className = 'modal-body';
      if (html) {
        // Caller opted into raw HTML; trust them. The `html=true` flag is
        // only set by trusted internal callers (e.g. showAlert with our own
        // server-controlled HTML for status messages) — never with user input.
        // lgtm[js/xss-through-dom]
        bodyEl.innerHTML = body;
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
        inputEl.type = 'text';
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
        cb.textContent = cancelText || '取消';
        actionsEl.appendChild(cb);
      }
      const ob = document.createElement('button');
      ob.type = 'button';
      ob.className = 'btn btn-primary modal-ok';
      ob.textContent = okText || '確定';
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
      okText: opts.okText || '確定', showCancel: false,
    });
  };
  window.showConfirm = function (msg, opts) {
    opts = opts || {};
    return _show({
      title: opts.title, body: msg, kind: opts.kind || 'warn', html: opts.html,
      okText: opts.okText || '確定', cancelText: opts.cancelText || '取消',
      showCancel: true,
    });
  };
  window.showPrompt = function (msg, opts) {
    opts = opts || {};
    return _show({
      title: opts.title, body: msg, kind: opts.kind, html: opts.html,
      okText: opts.okText || '確定', cancelText: opts.cancelText || '取消',
      showCancel: true, prompt: true,
      defaultValue: opts.defaultValue || '', placeholder: opts.placeholder || '',
    });
  };

  // Non-invasive shims so legacy code calling alert/confirm/prompt still works.
  // Note: confirm/prompt are async now — return Promises. Existing callers that
  // do `if (confirm(...))` will not work; we audit & rewrite those.
  window.alert = (msg) => window.showAlert(String(msg));
  // Keep window.confirm and window.prompt as the real native ones for any old
  // synchronous code, since making them async would silently break flow.
})();

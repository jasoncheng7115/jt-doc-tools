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
      const titleEl = title
        ? `<div class="modal-title">${escapeHTML(title)}</div>`
        : '';
      const bodyHtml = html ? body : `<div class="modal-body">${escapeHTML(body || '').replace(/\n/g, '<br>')}</div>`;
      const inputHtml = prompt
        ? `<input type="text" class="modal-input" value="${escapeAttr(defaultValue || '')}" placeholder="${escapeAttr(placeholder || '')}" />`
        : '';
      card.innerHTML = `
        ${titleEl}
        ${bodyHtml}
        ${inputHtml}
        <div class="modal-actions">
          ${showCancel ? `<button type="button" class="btn modal-cancel">${escapeHTML(cancelText || '取消')}</button>` : ''}
          <button type="button" class="btn btn-primary modal-ok">${escapeHTML(okText || '確定')}</button>
        </div>
      `;
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

// Shared toast helper. Usage: showToast('訊息', 'ok' | 'err' | '')
window.showToast = function(msg, kind) {
  let host = document.getElementById('toast-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toast-host';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = msg;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 250);
  }, 1800);
};

// Make every .panel with a plain <h2> as its first child collapsible on
// title-click. Skips <details class="panel"> (already native-collapsible).
// Persists state in localStorage keyed by location.pathname + h2 text.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.panel').forEach(panel => {
    if (panel.tagName === 'DETAILS') return;
    const h2 = panel.firstElementChild;
    if (!h2 || h2.tagName !== 'H2') return;
    const key = 'panel-collapsed:' + location.pathname + ':' + (h2.textContent || '').trim();
    if (localStorage.getItem(key) === '1') panel.classList.add('collapsed');
    h2.addEventListener('click', (e) => {
      // don't collapse when clicking something inside the h2 (links, buttons, inputs)
      if (e.target !== h2 && e.target.closest('a, button, input, select, textarea')) return;
      panel.classList.toggle('collapsed');
      localStorage.setItem(key, panel.classList.contains('collapsed') ? '1' : '0');
    });
  });
});

// flashSaved(buttonEl): briefly turn a button green with "✓ 已儲存" label.
window.flashSaved = function(btn, originalHTML) {
  const restore = originalHTML || btn.dataset.origHTML || btn.innerHTML;
  btn.dataset.origHTML = restore;
  btn.innerHTML = '✓ 已儲存';
  btn.classList.add('saved-flash');
  btn.disabled = true;
  setTimeout(() => {
    btn.innerHTML = restore;
    btn.classList.remove('saved-flash');
    btn.disabled = false;
  }, 1600);
};

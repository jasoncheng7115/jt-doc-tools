// Workspace picker + save helpers — shared across all tools.
//
// Loaded globally (only when the workspace feature is enabled). Provides:
//   window.openWorkspacePicker({accept, onPick})  — modal to choose a saved file
//   window.workspaceFileAsFile(fileId, meta)      — fetch a saved file as a File
//   window.saveToWorkspace(blobOrSpec, name, tool)— POST output into workspace
//   window.workspaceAcceptExts(acceptAttr)        — derive [pdf,png] subset
(function () {
  function fmtBytes(n) {
    if (!n) return '0 B';
    const u = ['B', 'KB', 'MB', 'GB']; let i = 0; let v = n;
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return (i === 0 ? v : v.toFixed(1)) + ' ' + u[i];
  }
  function esc(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }

  // Map an <input accept> attribute to the subset of {pdf, png} the workspace
  // can supply to this upload. Empty/wildcard → both. Returns [] if neither
  // (caller should then hide the "load from workspace" button).
  function workspaceAcceptExts(acceptAttr) {
    const a = (acceptAttr || '').toLowerCase();
    if (!a.trim() || a.includes('*/*')) return ['pdf', 'png'];
    const out = [];
    if (a.includes('pdf')) out.push('pdf');
    if (a.includes('png') || a.includes('image/')) out.push('png');
    return out;
  }

  async function workspaceFileAsFile(fileId, meta) {
    const r = await fetch('/workspace/file/' + fileId);
    if (!r.ok) throw new Error('讀取工作區檔案失敗');
    const blob = await r.blob();
    const name = (meta && meta.name) || ('workspace' + ((meta && meta.ext) || ''));
    const type = (meta && meta.mime) || blob.type || 'application/octet-stream';
    return new File([blob], name, { type });
  }

  // Save a tool's output into the workspace.
  //   spec: {jobId} | {blob} | {url}
  async function saveToWorkspace(spec, name, tool) {
    const fd = new FormData();
    if (name) fd.append('name', name);
    if (tool) fd.append('source_tool', tool);
    if (spec && spec.jobId) {
      fd.append('job_id', spec.jobId);
    } else if (spec && spec.blob) {
      fd.append('file', spec.blob, name || 'file');
    } else if (spec && spec.url) {
      const rr = await fetch(spec.url);
      if (!rr.ok) throw new Error('讀取輸出檔失敗');
      const blob = await rr.blob();
      fd.append('file', blob, name || 'file');
    } else {
      throw new Error('沒有可儲存的內容');
    }
    const r = await fetch('/workspace/save', { method: 'POST', body: fd });
    if (!r.ok) {
      throw new Error(await window.friendlyServerError(r, '存至工作區失敗'));
    }
    return await r.json();  // { ok, file, duplicate }
  }

  function buildModal() {
    let m = document.getElementById('ws-picker-modal');
    if (m) return m;
    m = document.createElement('div');
    m.id = 'ws-picker-modal';
    m.className = 'ws-picker-backdrop';
    m.hidden = true;
    m.innerHTML =
      '<div class="ws-picker-dialog" role="dialog" aria-modal="true">' +
      '  <div class="ws-picker-head"><b>從工作區載入</b>' +
      '    <button type="button" class="ws-picker-close" aria-label="關閉">' +
      '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6 6l12 12"/><path d="M6 18L18 6"/></svg></button></div>' +
      '  <div class="ws-picker-body"><div class="ws-picker-status muted">載入中…</div>' +
      '    <div class="ws-picker-grid"></div></div>' +
      '</div>';
    document.body.appendChild(m);
    const style = document.createElement('style');
    style.textContent =
      '.ws-picker-backdrop{position:fixed;inset:0;background:rgba(15,23,42,.5);z-index:9999;display:flex;align-items:center;justify-content:center;padding:20px;}' +
      '.ws-picker-dialog{background:#fff;border-radius:12px;width:min(720px,96vw);max-height:84vh;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.3);}' +
      '.ws-picker-head{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #e5e7eb;font-size:15px;}' +
      '.ws-picker-close{border:none;background:none;font-size:24px;line-height:1;cursor:pointer;color:#64748b;}' +
      '.ws-picker-body{padding:16px 18px;overflow:auto;}' +
      '.ws-picker-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;}' +
      '.ws-pick-card{border:1px solid #e2e8f0;border-radius:9px;overflow:hidden;cursor:pointer;background:#fff;transition:all .12s;display:flex;flex-direction:column;}' +
      '.ws-pick-card:hover{border-color:#2563eb;box-shadow:0 2px 10px rgba(37,99,235,.18);}' +
      '.ws-pick-thumb{height:110px;background:#f1f5f9;display:flex;align-items:center;justify-content:center;overflow:hidden;}' +
      '.ws-pick-thumb img{width:100%;height:100%;object-fit:cover;object-position:center;display:block;}' +
      '.ws-pick-badge{font-size:11px;font-weight:700;color:#fff;padding:3px 8px;border-radius:6px;background:#dc2626;}' +
      '.ws-pick-info{padding:8px 10px;font-size:12px;}' +
      '.ws-pick-name{font-weight:600;color:#1e293b;word-break:break-all;line-height:1.3;}' +
      '.ws-pick-meta{color:#94a3b8;font-size:11px;margin-top:2px;}';
    document.head.appendChild(style);
    m.querySelector('.ws-picker-close').addEventListener('click', () => { m.hidden = true; });
    m.addEventListener('click', (e) => { if (e.target === m) m.hidden = true; });
    return m;
  }

  async function openWorkspacePicker(opts) {
    opts = opts || {};
    const exts = opts.accept && opts.accept.length ? opts.accept : ['pdf', 'png'];
    const m = buildModal();
    const grid = m.querySelector('.ws-picker-grid');
    const status = m.querySelector('.ws-picker-status');
    grid.innerHTML = '';
    status.textContent = '載入中…';
    status.hidden = false;
    m.hidden = false;
    let files = [];
    try {
      const r = await fetch('/workspace/api/list?accept=' + encodeURIComponent(exts.join(',')));
      if (!r.ok) throw new Error(await window.friendlyServerError(r, '載入工作區失敗'));
      files = (await r.json()).files || [];
    } catch (e) { status.textContent = e.message || '載入工作區失敗'; return; }
    if (!files.length) { status.textContent = '工作區內沒有符合的檔案（' + exts.join(' / ').toUpperCase() + '）。'; return; }
    status.hidden = true;
    grid.innerHTML = files.map(f => {
      const ext = (f.ext || '').replace('.', '');
      const thumb = '<img src="/workspace/thumb/' + f.file_id + '" alt="" loading="lazy" ' +
        'onerror="this.onerror=null;this.replaceWith(Object.assign(document.createElement(\'span\'),{className:\'ws-pick-badge\',textContent:\'' + ext.toUpperCase() + '\'}))">';
      return '<div class="ws-pick-card" data-id="' + f.file_id + '">' +
        '<div class="ws-pick-thumb">' + thumb + '</div>' +
        '<div class="ws-pick-info"><div class="ws-pick-name">' + esc(f.name) + '</div>' +
        '<div class="ws-pick-meta">' + fmtBytes(f.size) + '</div></div></div>';
    }).join('');
    grid.querySelectorAll('.ws-pick-card').forEach(card => {
      card.addEventListener('click', async () => {
        const id = card.dataset.id;
        const meta = files.find(x => x.file_id === id);
        m.hidden = true;
        try { await opts.onPick(id, meta); }
        catch (e) { alert(e.message || '載入失敗'); }
      });
    });
  }

  // Wire a 「存至工作區」 button for direct-download tools. `specFn` runs on
  // click and returns {url|blob, name, tool}. `saveToWorkspace` can fetch a
  // blob: URL just as well as a server URL, so the same anchor.href works for
  // both client-blob and server-file downloads.
  function attachWorkspaceSave(btn, specFn) {
    if (!btn || !window.saveToWorkspace) { if (btn) btn.hidden = true; return; }
    btn.hidden = false;
    btn.disabled = false;
    const orig = btn.dataset.wsOrig || (btn.dataset.wsOrig = btn.innerHTML);
    btn.innerHTML = orig;
    btn.onclick = async () => {
      btn.disabled = true;
      try {
        const s = await specFn();
        const res = await window.saveToWorkspace(s, s.name, s.tool);
        const dup = res && res.duplicate;
        btn.innerHTML = '已存至工作區';
        if (window.showToast) window.showToast(
          dup ? '已存至工作區（工作區已有同名檔，已另存一份）' : '已存至工作區', 'ok');
      } catch (e) {
        btn.disabled = false;
        (window.showAlert || window.alert)(e.message || '存至工作區失敗');
      }
    };
  }

  // Wire a 「從工作區載入」 button for tools with a CUSTOM upload UI (not the
  // shared file_upload component). On pick it sets the given <input type=file>
  // and dispatches a 'change' event, so the tool's existing handler runs.
  function attachWorkspaceLoadButton(btn, inputEl, opts) {
    opts = opts || {};
    if (!btn || !inputEl || !window.openWorkspacePicker) { if (btn) btn.hidden = true; return; }
    const exts = (opts.accept && opts.accept.length)
      ? opts.accept : workspaceAcceptExts(inputEl.getAttribute('accept') || '');
    if (!exts.length) { btn.hidden = true; return; }
    btn.addEventListener('click', (e) => {
      e.preventDefault(); e.stopPropagation();
      openWorkspacePicker({ accept: exts, onPick: async (id, meta) => {
        const file = await workspaceFileAsFile(id, meta);
        try { const dt = new DataTransfer(); dt.items.add(file); inputEl.files = dt.files; } catch (_e) {}
        inputEl.dispatchEvent(new Event('change', { bubbles: true }));
      }});
    });
  }

  window.openWorkspacePicker = openWorkspacePicker;
  window.attachWorkspaceSave = attachWorkspaceSave;
  window.attachWorkspaceLoadButton = attachWorkspaceLoadButton;
  window.workspaceFileAsFile = workspaceFileAsFile;
  window.saveToWorkspace = saveToWorkspace;
  window.workspaceAcceptExts = workspaceAcceptExts;
})();

// FileUpload: simple drop-zone + change handler.
// Usage: const fu = new FileUpload(document.getElementById('upload'), onFile);
//
// Also installs a *document-level* drag guard on first use, so files dropped
// OUTSIDE any drop-zone don't fall through to the browser's default
// "open-the-file" behavior (which confusingly navigates away from the tool).
// The page-specific drop-zone handlers call preventDefault + stopPropagation,
// so they still work — this only catches what would otherwise escape.
(function () {
  if (!window.__jtdtDragGuardInstalled) {
    window.__jtdtDragGuardInstalled = true;
    ['dragover', 'drop'].forEach(ev => {
      window.addEventListener(ev, (e) => {
        // Only guard when the drop involves actual files (not internal
        // drag-drop of DOM elements, sortable libs, etc.)
        const dt = e.dataTransfer;
        if (!dt) return;
        const hasFile = Array.from(dt.types || []).includes('Files');
        if (!hasFile) return;
        // If a FileUpload's drop-zone is handling this, it will have
        // called preventDefault + stopPropagation, and we never get here.
        e.preventDefault();
      });
    });
  }
  class FileUpload {
    constructor(root, onFile) {
      this.root = root;
      this.input = root.querySelector('input[type=file]');
      this.dropZone = root.querySelector('.drop-zone');
      this.nameEl = root.querySelector('.drop-zone-filename');
      this.onFile = onFile || (() => {});
      this.multiple = !!this.input.multiple;
      this._bind();
    }
    _bind() {
      this.input.addEventListener('change', () => {
        const files = Array.from(this.input.files || []);
        if (files.length) this._pick(files);
      });
      ['dragenter', 'dragover'].forEach(ev => {
        this.dropZone.addEventListener(ev, (e) => {
          e.preventDefault(); e.stopPropagation();
          this.dropZone.classList.add('dragover');
        });
      });
      ['dragleave', 'drop'].forEach(ev => {
        this.dropZone.addEventListener(ev, (e) => {
          e.preventDefault(); e.stopPropagation();
          this.dropZone.classList.remove('dragover');
        });
      });
      this.dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        if (!dt || !dt.files || !dt.files.length) return;
        let files = Array.from(dt.files);
        if (!this.multiple && files.length > 1) {
          // Single-file upload area but the user dropped many — keep
          // only the first and tell them via the filename label.
          files = [files[0]];
          // Build a 1-item DataTransfer so input.files reflects the trim.
          try {
            const dt2 = new DataTransfer();
            dt2.items.add(files[0]);
            this.input.files = dt2.files;
          } catch (_e) {
            this.input.files = dt.files;  // fallback
          }
          this._dropMultiNotice = `（拖入了 ${dt.files.length} 份，只取第一份）`;
        } else {
          this.input.files = dt.files;
          this._dropMultiNotice = '';
        }
        this._pick(files);
      });
    }
    _pick(files) {
      const arr = Array.isArray(files) ? files : [files];
      if (this.nameEl) {
        if (arr.length > 1) {
          this.nameEl.textContent = `${arr.length} 個檔案：${arr.map(f => f.name).join('、')}`;
        } else {
          this.nameEl.textContent = arr[0].name + (this._dropMultiNotice || '');
        }
      }
      // Back-compat: single-arg callbacks receive the first file.
      // If the handler returns a Promise (most do — they're async functions),
      // auto-show a spinner overlay over the drop-zone until it settles.
      // Saves every tool from having to plumb its own "busy" state.
      const ret = this.onFile(this.multiple ? arr : arr[0], arr);
      if (ret && typeof ret.then === 'function') {
        this.setBusy(true);
        ret.finally(() => this.setBusy(false));
      }
    }
    setBusy(busy) {
      if (!this.dropZone) return;
      this.dropZone.classList.toggle('uploading', !!busy);
      if (!busy) this._setProgress(null);
    }
    _ensureProgress() {
      if (this._progEls) return this._progEls;
      const wrap = document.createElement('div');
      wrap.className = 'fu-progress';
      wrap.innerHTML =
        '<div class="fu-progress-label">準備中…</div>' +
        '<div class="fu-progress-bar"><div class="fu-progress-fill"></div></div>' +
        '<div class="fu-progress-pct">0%</div>';
      this.dropZone.appendChild(wrap);
      this._progEls = {
        wrap: wrap,
        label: wrap.querySelector('.fu-progress-label'),
        fill:  wrap.querySelector('.fu-progress-fill'),
        pct:   wrap.querySelector('.fu-progress-pct'),
      };
      return this._progEls;
    }
    _setProgress(state) {
      // state: null = hide; {pct, label, indeterminate}
      if (!state) {
        if (this._progEls) this._progEls.wrap.hidden = true;
        return;
      }
      const els = this._ensureProgress();
      els.wrap.hidden = false;
      els.label.textContent = state.label || '';
      if (state.indeterminate) {
        els.fill.classList.add('indeterminate');
        els.fill.style.width = '100%';
        els.pct.textContent = '—';
      } else {
        els.fill.classList.remove('indeterminate');
        const pct = Math.max(0, Math.min(100, state.pct || 0));
        els.fill.style.width = pct + '%';
        els.pct.textContent = pct + '%';
      }
    }
    // Convenience wrapper: POST a FormData to `url`, automatically render
    // upload progress inside the drop-zone, then switch to indeterminate
    // ("處理中…") once upload hits 100% (server still rendering / saving).
    // Returns the same Response-like object as window.uploadWithProgress.
    upload(url, formData, opts) {
      opts = opts || {};
      const fmt = (n) => {
        if (n < 1024) return n + ' B';
        if (n < 1048576) return (n/1024).toFixed(1) + ' KB';
        if (n < 1073741824) return (n/1048576).toFixed(2) + ' MB';
        return (n/1073741824).toFixed(2) + ' GB';
      };
      const self = this;
      self.setBusy(true);
      return window.uploadWithProgress(url, formData, function (loaded, total, pct) {
        if (pct < 100) {
          self._setProgress({pct: pct, label: '上傳中… ' + fmt(loaded) + ' / ' + fmt(total)});
        } else {
          self._setProgress({indeterminate: true, label: opts.processingLabel || '處理中…（' + fmt(total) + '）'});
        }
      }, opts).finally(function () {
        // Hide progress shortly after; tool's own UI will take over.
        setTimeout(function () { self.setBusy(false); }, 250);
      });
    }
    // bfcache: when user navigates back/forward, the page's previous
    // .uploading state can persist. Reset on pageshow so they're not
    // staring at a fake "uploading" overlay.
    static _installPageShowReset() {
      if (window.__jtdtFuPageShowInstalled) return;
      window.__jtdtFuPageShowInstalled = true;
      window.addEventListener('pageshow', () => {
        document.querySelectorAll('.file-upload .drop-zone.uploading').forEach(z =>
          z.classList.remove('uploading'));
      });
    }
    reset() {
      this.input.value = '';
      if (this.nameEl) this.nameEl.textContent = '';
      this.setBusy(false);
    }
  }
  window.FileUpload = FileUpload;
  FileUpload._installPageShowReset();

  // Drop-in fetch replacement that emits actual upload-byte progress.
  // fetch() can't surface upload progress (no streaming spec yet in 2026
  // for upload bodies), so we fall back to XHR for the multipart POST.
  // Returns a Response-like object so handlers using `.ok / .json() / .text()`
  // keep working. Usage:
  //
  //   const r = await uploadWithProgress(url, formData, (loaded, total, pct) => {
  //     statusEl.textContent = `上傳中… ${pct}%`;
  //   });
  //   if (!r.ok) ...
  //
  // After upload completes the server still has work to do — caller should
  // switch the UI to an indeterminate "處理中…" spinner once we hit 100%.
  window.uploadWithProgress = function (url, formData, onProgress, opts) {
    opts = opts || {};
    return new Promise(function (resolve, reject) {
      const xhr = new XMLHttpRequest();
      if (xhr.upload && onProgress) {
        xhr.upload.addEventListener('progress', function (e) {
          if (e.lengthComputable) {
            const pct = Math.min(100, Math.round((e.loaded / e.total) * 100));
            try { onProgress(e.loaded, e.total, pct); } catch (_e) {}
          }
        });
      }
      xhr.addEventListener('load', function () {
        const text = xhr.responseText || '';
        const ct = xhr.getResponseHeader('content-type') || '';
        const wrap = {
          ok: xhr.status >= 200 && xhr.status < 300,
          status: xhr.status, statusText: xhr.statusText,
          headers: { get: function (k) { return xhr.getResponseHeader(k); } },
          text: function () { return Promise.resolve(text); },
          json: function () {
            try { return Promise.resolve(JSON.parse(text)); }
            catch (e) { return Promise.reject(new Error('invalid JSON in response: ' + e.message)); }
          },
          blob: function () { return Promise.resolve(new Blob([text], { type: ct })); },
        };
        resolve(wrap);
      });
      xhr.addEventListener('error', function () { reject(new Error('network error')); });
      xhr.addEventListener('abort', function () { reject(new Error('aborted')); });
      xhr.open(opts.method || 'POST', url);
      if (opts.headers) {
        Object.keys(opts.headers).forEach(function (k) { xhr.setRequestHeader(k, opts.headers[k]); });
      }
      xhr.send(formData);
    });
  };
})();

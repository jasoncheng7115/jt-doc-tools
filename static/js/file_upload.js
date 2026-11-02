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
    // opts.onWorkspacePick(fileId, meta)：選填。工具自己能在伺服器端直接取用工作區
    // 裡的檔案時給這個 —— 「從工作區載入」就不再把檔案下載到瀏覽器、再整份上傳回去
    // （錄音檔動輒上百 MB，同一份檔案在網路上來回兩趟）。歸屬一樣由伺服器判斷。
    constructor(root, onFile, opts) {
      this.root = root;
      this.input = root.querySelector('input[type=file]');
      this.dropZone = root.querySelector('.drop-zone');
      this.nameEl = root.querySelector('.drop-zone-filename');
      this.onFile = onFile || (() => {});
      this.onWorkspacePick = (opts && opts.onWorkspacePick) || null;
      this.multiple = !!this.input.multiple;
      this._bind();
      this._wireWorkspaceLoad();
      this._wireJobHandoff();
    }
    // 工具之間的交接：把上一個工具的產出直接帶進這個工具，不用先下載再上傳。
    //
    // 兩種來源，接口一致（之後要串多個工具的工作流程也走這裡）：
    //
    //   ?from_ws=<file_id>   從**我的工作區**取（優先）。檔案是持久的 ——
    //                        重啟、隔天再回來都還在，也看得到、刪得掉。
    //   ?from_job=<job_id>   從作業結果取。工作區被管理員停用時的退路；
    //                        作業會過期、重啟後也不在，所以不當主要路徑。
    //
    // 安全性一律靠伺服器端：`/workspace/file/{id}` 與 `/api/jobs/{id}/download`
    // 本來就驗歸屬，拿別人的 id 一樣取不到。前端不做任何判斷。
    _wireJobHandoff() {
      // 一頁可能有多個上傳框（例如騎縫章有文件與印章圖兩個）。
      // 只有第一個吃這個參數，否則印章框會被塞進一份 PDF。
      if (window.__jtHandoffClaimed) return;
      const qs = new URLSearchParams(window.location.search);
      const ok = (v) => !!v && /^[A-Za-z0-9_-]{6,64}$/.test(v);
      // **各自驗各自的**。第一版寫成「`from_ws` 存在就用它，格式不合就整個
      // return」，後果有兩個：①**連 `from_job` 退路都不試**（工作區被停用時
      // 走的正是那條）②**網址參數沒被清掉**，重新整理照樣走同一條死路，
      // 而且畫面上沒有任何訊息。
      const wsId = ok(qs.get('from_ws')) ? qs.get('from_ws') : '';
      const jobId = ok(qs.get('from_job')) ? qs.get('from_job') : '';
      if (!wsId && !jobId) return;
      window.__jtHandoffClaimed = true;
      // **兩個參數都要先讀出來再清網址** —— 清完才讀的話讀到的是空的
      // （檔名會變成預設值，使用者拿到一份叫 document.pdf 的檔）。
      const name = qs.get('from_name') || 'document.pdf';
      // 網址用完就清掉：重新整理不該再抓一次，而且留著也只是雜訊
      try {
        const u = new URL(window.location.href);
        ['from_ws', 'from_job', 'from_name'].forEach(k => u.searchParams.delete(k));
        window.history.replaceState({}, '', u.toString());
      } catch (_e) { /* 清不掉不影響功能 */ }
      const url = wsId
        ? '/workspace/file/' + encodeURIComponent(wsId)
        : '/api/jobs/' + encodeURIComponent(jobId) + '/download';
      this._setProgress({indeterminate: true, label: tr('接收上一個工具的檔案…')});
      fetch(url)
        .then(r => { if (!r.ok) throw new Error(tr('取不到檔案')); return r.blob(); })
        .then(b => {
          this._setProgress(null);
          const f = new File([b], name, {type: b.type || 'application/pdf'});
          // **交接來的檔案也要過 `accept`**。工作區收得下 docx / xlsx / pptx，
          // 但很多工具只吃 PDF —— 沒有這道檢查的話，一份 .docx 會被包成
          // `document.pdf` 塞進純 PDF 的工具，伺服器竟然回 200（PyMuPDF 開得
          // 起來），使用者拿到的是一份莫名其妙的產出而不是清楚的錯誤。
          // 工作區挑選器本來就有依 accept 過濾，交接這條路漏了。
          if (!this._acceptsFile(f)) {
            if (window.showAlert) {
              window.showAlert(tr('這個工具不接受「{0}」這種檔案格式。').replace('{0}', f.name));
            }
            return;
          }
          this.loadFiles([f]);
        })
        .catch(() => {
          this._setProgress(null);
          // 檔案可能已被刪除或作業已過期 —— 講清楚，不要留一個空畫面
          if (window.showAlert) {
            window.showAlert(tr('上一個工具的檔案取不到了（可能已刪除或過期），請重新上傳。'));
          }
        });
    }
    // Wire the optional 「從工作區載入」 button (rendered by the shared
    // file_upload.html component when the workspace feature is enabled).
    // Derives which saved file types (pdf/png) this upload can accept; hides
    // the button when neither applies (e.g. a .docx-only upload).
    _wireWorkspaceLoad() {
      const btn = this.root.querySelector('.ws-load-btn');
      if (!btn) return;
      if (!window.openWorkspacePicker || !window.workspaceAcceptExts) { btn.hidden = true; return; }
      const exts = window.workspaceAcceptExts(
        this.input.getAttribute('accept') || btn.dataset.accept || '',
        btn.dataset.wsExts || '');
      if (!exts.length) { btn.hidden = true; return; }
      btn.addEventListener('click', (e) => {
        e.preventDefault(); e.stopPropagation();
        window.openWorkspacePicker({
          accept: exts,
          // 收多個檔的上傳框可以一次選多個；伺服器端直接取用的工具（onWorkspacePick）維持單選
          multiple: this.multiple && !this.onWorkspacePick,
          onPickMany: async (picks) => {
            this.loadFiles(await window.workspaceFilesAsFiles(picks));
          },
          onPick: async (id, meta) => {
            if (this.onWorkspacePick) {
              if (this.nameEl) this.nameEl.textContent = (meta && meta.name) || '';
              this.setBusy(true);
              try { await this.onWorkspacePick(id, meta); }
              finally { this.setBusy(false); }
              return;
            }
            const file = await window.workspaceFileAsFile(id, meta);
            this.loadFiles([file]);
          },
        });
      });
    }
    // 這個上傳框收不收這種檔案（依 `accept` 屬性判斷）。
    // 沒有設 `accept` 就是什麼都收。
    _acceptsFile(file) {
      const acc = (this.input.getAttribute('accept') || '').trim();
      if (!acc) return true;
      const name = (file.name || '').toLowerCase();
      const type = (file.type || '').toLowerCase();
      return acc.split(',').map(s => s.trim().toLowerCase()).filter(Boolean)
        .some(rule => {
          if (rule.startsWith('.')) return name.endsWith(rule);
          if (rule.endsWith('/*')) return type.startsWith(rule.slice(0, -1));
          return type === rule;
        });
    }
    // Inject File object(s) programmatically (used by 從工作區載入) — mirrors
    // a real drop/selection so the tool's onFile pipeline runs unchanged.
    loadFiles(files) {
      const arr = Array.from(files || []);
      if (!arr.length) return;
      const picked = this.multiple ? arr : [arr[0]];
      try {
        const dt = new DataTransfer();
        picked.forEach(f => dt.items.add(f));
        this.input.files = dt.files;
      } catch (_e) { /* DataTransfer unsupported — onFile still fires below */ }
      this._dropMultiNotice = '';
      this._pick(picked);
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
          this._dropMultiNotice = tr('（拖入了 {0} 份，只取第一份）').replace('{0}', dt.files.length);
        } else {
          this.input.files = dt.files;
          this._dropMultiNotice = '';
        }
        this._pick(files);
      });
    }
    // 有開啟密碼的 PDF：先問密碼（或指去「PDF 密碼解除」），解開後換成解開的那份再往下走。
    // 使用者 2026-11-02：拉進有密碼的 PDF，用印頁什麼反應都沒有 —— 每一支吃 PDF 的工具都是
    // 同一個形狀，所以收在共用元件這裡做一次。取消或解不開的那份就不往下送。
    async _pick(files) {
      let arr = Array.isArray(files) ? files : [files];
      if (window.unlockPdfFiles) {
        const before = arr;
        arr = await window.unlockPdfFiles(arr, this.toolId());
        if (!arr.length) { this.reset(); return; }
        if (arr.some((f, i) => f !== before[i]) || arr.length !== before.length) {
          try {
            const dt = new DataTransfer();
            arr.forEach(f => dt.items.add(f));
            this.input.files = dt.files;     // 之後讀 input.files 的工具拿到的是解開的那份
          } catch (_e) { /* 不支援 DataTransfer：onFile 仍拿到解開的檔 */ }
        }
      }
      this._pickResolved(arr);
    }
    toolId() {
      const m = /^\/tools\/([a-z0-9-]+)/.exec(window.location.pathname || '');
      return m ? m[1] : '';
    }
    _pickResolved(arr) {
      if (this.nameEl) {
        if (arr.length > 1) {
          this.nameEl.textContent = tr('{0} 個檔案：{1}').replace('{0}', arr.length).replace('{1}', arr.map(f => f.name).join('、'));
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
        '<div class="fu-progress-label">' + tr('準備中…') + '</div>' +
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
          self._setProgress({pct: pct, label: tr('上傳中… ') + fmt(loaded) + ' / ' + fmt(total)});
        } else {
          self._setProgress({indeterminate: true, label: opts.processingLabel || tr('處理中…（{0}）').replace('{0}', fmt(total))});
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

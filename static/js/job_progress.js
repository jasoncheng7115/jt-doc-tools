// JobProgress: polls /api/jobs/{id} and shows status bar + download link(s).
(function () {
  class JobProgress {
    constructor(root, { downloadUrl, downloadPngUrl, onReset, onDone, onError, onCancel } = {}) {
      this.root = root;
      this.bar = root.querySelector('.job-bar-inner');
      this.status = root.querySelector('.job-status');
      this.dlBtn = root.querySelector('.job-download');
      this.dlPngBtn = root.querySelector('.job-download-png');
      this.saveWsBtn = root.querySelector('.job-save-ws');
      this.resetBtn = root.querySelector('.job-reset');
      this.downloadUrl = downloadUrl || ((jid) => `/api/jobs/${jid}/download`);
      this.downloadPngUrl = downloadPngUrl || ((jid) => `/api/jobs/${jid}/download-png`);
      this.onReset = onReset || (() => {});
      this.onDone = onDone || (() => {});
      this.onError = onError || (() => {});
      this.onCancel = onCancel || (() => {});
      this._timer = null;
      this.jobId = null;
      this.resetBtn.addEventListener('click', () => { this.hide(); this.onReset(); });
    }
    show() { this.root.hidden = false; }
    stopPolling() { this._stop(); }
    // 主動停止：呼叫 cancel API + 停輪詢（UI 端立即回饋）
    async cancel() {
      const jid = this.jobId;
      this._stop();
      if (jid) {
        try { await fetch(`/api/jobs/${jid}/cancel`, { method: 'POST' }); } catch (_) {}
      }
    }
    hide() {
      this.root.hidden = true;
      this.bar.style.width = '0%';
      this.status.textContent = '準備中…';
      this.dlBtn.hidden = true;
      if (this.dlPngBtn) this.dlPngBtn.hidden = true;
      if (this.saveWsBtn) this.saveWsBtn.hidden = true;
      this._stop();
    }
    _stop() { if (this._timer) { clearInterval(this._timer); this._timer = null; } }
    // Show + wire 「存至工作區」 for a finished job whose result is a PDF/PNG.
    _wireSaveWs(j) {
      const btn = this.saveWsBtn;
      if (!btn || !window.saveToWorkspace) return;
      const fname = j.result_filename || '';
      if (!/\.(pdf|png|docx|odt)$/i.test(fname)) { btn.hidden = true; return; }  // workspace = PDF/PNG/DOCX/ODT
      btn.hidden = false;
      btn.disabled = false;
      const orig = btn.dataset.origHtml || (btn.dataset.origHtml = btn.innerHTML);
      btn.innerHTML = orig;
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const res = await window.saveToWorkspace({ jobId: this.jobId }, fname, j.tool_id || '');
          btn.innerHTML = '已存至工作區';
          if (window.showToast) window.showToast(
            (res && res.duplicate) ? '已存至工作區（工作區已有同名檔，已另存一份）' : '已存至工作區', 'ok');
        } catch (e) {
          btn.disabled = false;
          if (window.showAlert) window.showAlert(e.message || '存至工作區失敗');
          else alert(e.message || '存至工作區失敗');
        }
      };
    }
    track(jobId) {
      this.jobId = jobId;
      // 重設前次 run 的殘留狀態（進度條 / 下載按鈕 / 錯誤色），避免顯示 stale UI
      this.bar.style.width = '0%';
      this.bar.style.background = '';
      this.dlBtn.hidden = true;
      if (this.dlPngBtn) this.dlPngBtn.hidden = true;
      if (this.saveWsBtn) this.saveWsBtn.hidden = true;
      this.show();
      this.status.textContent = '處理中…';
      this._stop();
      const tick = async () => {
        try {
          const r = await fetch(`/api/jobs/${jobId}`);
          if (!r.ok) throw new Error('job not found');
          const j = await r.json();
          const pct = Math.max(5, Math.round((j.progress || 0) * 100));
          this.bar.style.width = pct + '%';
          if (j.status === 'running') this.status.textContent = j.message || '處理中…';
          else if (j.status === 'pending') this.status.textContent = '排隊中…';
          else if (j.status === 'done') {
            this.bar.style.width = '100%';
            this.status.textContent = j.message || '完成';
            this.dlBtn.hidden = false;
            this.dlBtn.href = this.downloadUrl(jobId);
            if (this.dlPngBtn) {
              this.dlPngBtn.hidden = false;
              this.dlPngBtn.href = this.downloadPngUrl(jobId);
            }
            this._wireSaveWs(j);
            this._stop();
            try { this.onDone(j); } catch (_) {}
          } else if (j.status === 'error') {
            this.status.textContent = '失敗：' + (j.error || '未知錯誤');
            this.bar.style.background = '#dc2626';
            this._stop();
            try { this.onError(j); } catch (_) {}
          } else if (j.status === 'cancelled') {
            this.status.textContent = j.message || '已停止';
            this._stop();
            try { this.onCancel(j); } catch (_) {}
          }
        } catch (e) {
          this.status.textContent = '查詢狀態失敗';
          this._stop();
          try { this.onError({ error: e.message }); } catch (_) {}
        }
      };
      this._timer = setInterval(tick, 800);
      tick();
    }
  }
  window.JobProgress = JobProgress;
})();

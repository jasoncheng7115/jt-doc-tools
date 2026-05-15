// JobProgress: polls /api/jobs/{id} and shows status bar + download link(s).
(function () {
  class JobProgress {
    constructor(root, { downloadUrl, downloadPngUrl, onReset, onDone, onError } = {}) {
      this.root = root;
      this.bar = root.querySelector('.job-bar-inner');
      this.status = root.querySelector('.job-status');
      this.dlBtn = root.querySelector('.job-download');
      this.dlPngBtn = root.querySelector('.job-download-png');
      this.resetBtn = root.querySelector('.job-reset');
      this.downloadUrl = downloadUrl || ((jid) => `/api/jobs/${jid}/download`);
      this.downloadPngUrl = downloadPngUrl || ((jid) => `/api/jobs/${jid}/download-png`);
      this.onReset = onReset || (() => {});
      this.onDone = onDone || (() => {});
      this.onError = onError || (() => {});
      this._timer = null;
      this.resetBtn.addEventListener('click', () => { this.hide(); this.onReset(); });
    }
    show() { this.root.hidden = false; }
    hide() {
      this.root.hidden = true;
      this.bar.style.width = '0%';
      this.status.textContent = '準備中…';
      this.dlBtn.hidden = true;
      if (this.dlPngBtn) this.dlPngBtn.hidden = true;
      this._stop();
    }
    _stop() { if (this._timer) { clearInterval(this._timer); this._timer = null; } }
    track(jobId) {
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
            this._stop();
            try { this.onDone(j); } catch (_) {}
          } else if (j.status === 'error') {
            this.status.textContent = '失敗：' + (j.error || '未知錯誤');
            this.bar.style.background = '#dc2626';
            this._stop();
            try { this.onError(j); } catch (_) {}
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

// JobProgress: polls /api/jobs/{id} and shows status bar + download link(s).
(function () {
  class JobProgress {
    constructor(root, { downloadUrl, downloadPngUrl, onReset, onDone, onError,
                        onCancel, showDownload = true } = {}) {
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
      // 有些工具的結果頁自己有一整排下載按鈕（PDF / Markdown / ZIP …），
      // 這時再冒一顆「下載 .json」只會讓人以為那是別的東西。
      this.showDownload = showDownload;
      this._timer = null;
      this._elapsedTimer = null;
      this._startedAt = 0;
      this.elapsed = root.querySelector('.job-elapsed');
      // 「可以關掉這頁」提示 —— 有 job_id 才有意義，所以由這個元件控制
      this.bgNote = root.querySelector('.job-bg-note');
      this.jobId = null;
      this.resetBtn.addEventListener('click', () => { this.hide(); this.onReset(); });
    }
    show() { this.root.hidden = false; }
    stopPolling() { this._stop(); }
    // 主動停止：呼叫 cancel API + 停輪詢 + **把畫面切成「已停止」**
    //
    // ⚠ 原本只做前兩件，於是按下去之後：進度條停在原處、狀態文字還寫著
    // 「擷取重點 4/5」、「可以關掉這一頁」還掛著 —— 跟「卡住了」長得一模一樣
    // （2026-09-19 使用者回報「時間是沒走了，但是畫面看不出是停止的樣子」）。
    //
    // 停輪詢**正是**看不出來的原因：輪詢一停，就再也不會讀到伺服器回的
    // `status: 'cancelled'`，而畫成「已停止」的程式碼就在那條路上。
    // 所以要在這裡自己畫一次。
    async cancel() {
      const jid = this.jobId;
      this._stop();
      this.markStopped();
      if (jid) {
        try { await fetch(`/api/jobs/${jid}/cancel`, { method: 'POST' }); } catch (_) {}
      }
    }
    // 畫成「已停止」。**已過時間留著不清掉** —— 那是「跑了多久才停」，
    // 是有用的資訊；清掉反而看起來像什麼都沒發生。
    markStopped(msg, j) {
      this.root.classList.add('jp-stopped');
      this.status.textContent = msg || tr('已停止');
      if (this.bgNote) this.bgNote.hidden = true;
      if (this.dlBtn) this.dlBtn.hidden = true;
      if (this.dlPngBtn) this.dlPngBtn.hidden = true;
      if (this.saveWsBtn) this.saveWsBtn.hidden = true;
      this.onCancel(j);
    }
    hide() {
      this.root.hidden = true;
      // **下一次作業要從乾淨的狀態開始** —— 不清掉的話再送一份檔案，
      // 進度條一開始就是灰的而且寫著「已停止」。
      this.root.classList.remove('jp-stopped');
      this.bar.style.width = '0%';
      this.status.textContent = tr('準備中…');
      if (this.elapsed) { this.elapsed.hidden = true; this.elapsed.textContent = ''; }
      this.dlBtn.hidden = true;
      if (this.dlPngBtn) this.dlPngBtn.hidden = true;
      if (this.saveWsBtn) this.saveWsBtn.hidden = true;
      if (this.bgNote) this.bgNote.hidden = true;
      this._stop();
    }
    _stop() {
      if (this._timer) { clearInterval(this._timer); this._timer = null; }
      if (this._elapsedTimer) { clearInterval(this._elapsedTimer); this._elapsedTimer = null; }
    }

    // 結果檔的副檔名（小寫，不含點）。拿不到就回空字串。
    _resultExt(j) {
      const n = (j && j.result_filename) || '';
      const i = n.lastIndexOf('.');
      return i < 0 ? '' : n.slice(i + 1).toLowerCase();
    }
    // PDF 或「一包 PDF」的 ZIP —— 只有這兩種算得出 PNG
    _resultIsPdfish(j) {
      const e = this._resultExt(j);
      return e === '' || e === 'pdf' || e === 'zip';
    }
    // 下載鈕的文字要跟實際產出一致。模板寫死「下載 PDF」，但用這個元件的
    // 工具有一半產出的不是 PDF（.docx / .odp / .xlsx / .zip …），
    // 寫著 PDF 會讓人以為下載到的是 PDF。
    _labelDownload(j) {
      const ext = this._resultExt(j);
      if (!ext) return;                        // 拿不到就維持模板原本的字
      const label = ext === 'pdf' ? tr('下載 PDF')
                  : ext === 'zip' ? tr('下載 ZIP')
                  : tr('下載 .{0}').replace('{0}', ext);
      const node = [...this.dlBtn.childNodes]
        .reverse().find((n) => n.nodeType === 3 && n.textContent.trim());
      if (node) node.textContent = label;
      else this.dlBtn.append(label);
    }

    // 已過時間（分:秒）。轉檔動輒數分鐘，沒有這個使用者會以為當掉。
    _fmtElapsed(ms) {
      const s = Math.max(0, Math.floor(ms / 1000));
      return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
    }
    _startElapsed() {
      if (!this.elapsed) return;
      this._startedAt = Date.now();
      this.elapsed.hidden = false;
      const paint = () => {
        this.elapsed.textContent = tr('已過 {0}')
          .replace('{0}', this._fmtElapsed(Date.now() - this._startedAt));
      };
      paint();
      this._elapsedTimer = setInterval(paint, 1000);
    }
    // `tpl` 是**整句**的樣板（`'耗時 {0}'`），不是前綴 —— 把片語接起來的話
    // 英文與日文的語序不同，接出來不成句（CLAUDE.md 記過這條）。
    _finishElapsed(tpl) {
      if (!this.elapsed) return;
      if (this._elapsedTimer) { clearInterval(this._elapsedTimer); this._elapsedTimer = null; }
      if (this._startedAt) {
        this.elapsed.textContent = (tpl || tr('耗時 {0}'))
          .replace('{0}', this._fmtElapsed(Date.now() - this._startedAt));
      }
    }
    // Show + wire 「存至工作區」 for a finished job whose result is a PDF/PNG.
    _wireSaveWs(j) {
      const btn = this.saveWsBtn;
      if (!btn || !window.saveToWorkspace) return;
      const fname = j.result_filename || '';
      // **副檔名清單由伺服器端給**（`data-ws-exts`），不要在這裡寫死。
      // 原本這行寫死 pdf|png|docx|odt，但伺服器端 v1.14.6 就多收了
      // .xlsx / .ods / .pptx / .odp —— 兩邊沒一起改，結果是伺服器收得下的
      // 檔案畫面上那顆鈕卻不出現，而且沒有任何錯誤訊息。
      const exts = (this.root.dataset.wsExts || '').split(/\s+/).filter(Boolean);
      const ext = ((fname.match(/\.([^.]+)$/) || [])[1] || '').toLowerCase();
      if (!exts.length || !exts.includes(ext)) { btn.hidden = true; return; }
      btn.hidden = false;
      btn.disabled = false;
      const orig = btn.dataset.origHtml || (btn.dataset.origHtml = btn.innerHTML);
      btn.innerHTML = orig;
      btn.onclick = async () => {
        btn.disabled = true;
        try {
          const res = await window.saveToWorkspace({ jobId: this.jobId }, fname, j.tool_id || '');
          btn.innerHTML = tr('已存至工作區');
          if (window.showToast) window.showToast(
            (res && res.duplicate) ? tr('已存至工作區（工作區已有同名檔，已另存一份）') : tr('已存至工作區'), 'ok');
        } catch (e) {
          btn.disabled = false;
          if (window.showAlert) window.showAlert(e.message || tr('存至工作區失敗'));
          else alert(e.message || tr('存至工作區失敗'));
        }
      };
    }
    // 從「我的作業」按「開啟」回來（網址帶 `?job=<作業編號>`）：接著追蹤那件作業，
    // 跑完照樣呼叫 `onDone` 把結果畫面畫出來（結果都在作業的 `meta` 裡）。
    // 編號格式不對就當作沒有 —— 網址是使用者改得到的。回作業編號或 null。
    //
    // `reveal`：進度列與結果放在「上傳之後才打開」的面板裡時，傳那個面板 —— 接回時打開它，
    // 面板裡跟這次結果無關的東西（預覽、選項、送出按鈕，都要先有上傳的檔案才有意義）先收起來，
    // `keep` 是要留著的結果區。使用者接著選了新檔案或按「重新開始」，收起來的東西放回去。
    resumeFromUrl({ reveal = null, keep = [] } = {}) {
      let jid = null;
      try { jid = new URLSearchParams(location.search).get('job'); } catch (_) { jid = null; }
      if (!jid || !/^[a-f0-9]{32}$/.test(jid)) return null;
      if (reveal) {
        const tucked = [];
        reveal.hidden = false;
        Array.from(reveal.children).forEach((ch) => {
          if (ch.contains(this.root) || keep.some((k) => k && (ch === k || ch.contains(k)))) return;
          if (!ch.hidden) { ch.hidden = true; tucked.push(ch); }
        });
        const restore = () => { tucked.splice(0).forEach((ch) => { ch.hidden = false; }); };
        document.addEventListener('change', (e) => {
          if (e.target && e.target.type === 'file') restore();
        }, true);
        this.resetBtn.addEventListener('click', restore);
      }
      this.track(jid);
      try { this.root.scrollIntoView({ block: 'nearest' }); } catch (_) {}
      return jid;
    }
    track(jobId) {
      // 作業編號一律**當成不可信的值**：它從網址 / 伺服器回應進來，直接拼進
      // 路徑的話可以塞進 `../` 或 `//` 改寫成別的端點（CodeQL 的
      // client-side request forgery 指的就是這一條）。編碼一次就沒有這個問題。
      jobId = encodeURIComponent(String(jobId == null ? '' : jobId));
      this.jobId = jobId;
      // 重設前次 run 的殘留狀態（進度條 / 下載按鈕 / 錯誤色），避免顯示 stale UI
      this.bar.style.width = '0%';
      this.bar.style.background = '';
      this.dlBtn.hidden = true;
      if (this.dlPngBtn) this.dlPngBtn.hidden = true;
      if (this.saveWsBtn) this.saveWsBtn.hidden = true;
      this.show();
      this.status.textContent = tr('處理中…');
      // 作業已經送到伺服器了 → 這時候講「可以關掉這頁」才是對的時機
      if (this.bgNote) this.bgNote.hidden = false;
      this._stop();
      this._startElapsed();
      const tick = async () => {
        try {
          const r = await fetch(`/api/jobs/${jobId}`);
          if (!r.ok) throw new Error('job not found');
          const j = await r.json();
          const pct = Math.max(5, Math.round((j.progress || 0) * 100));
          this.bar.style.width = pct + '%';
          if (j.status === 'running') // 作業訊息是**伺服器端**產生的中文（各引擎的階段說明）。整句當鍵去查，
          // 查不到就原樣顯示中文 —— 不會壞，只是那一句還沒翻。
          this.status.textContent = tr(j.message || tr('處理中…'));
          else if (j.status === 'pending') this.status.textContent = '排隊中…';
          else if (j.status === 'done') {
            this.bar.style.width = '100%';
            this.status.textContent = tr(j.message || tr('完成'));
            this._finishElapsed(tr('耗時 {0}'));
            this.dlBtn.hidden = false;
            this.dlBtn.href = this.downloadUrl(jobId);
            this._labelDownload(j);
            // 「下載 PNG」是把**結果 PDF** 算成圖 —— 結果不是 PDF 的時候
            // 那顆鈕按下去必然失敗（端點找不到 PDF）。以前是無條件顯示，
            // 於是產出 .docx / .doc / .xlsx 的工具都掛著一顆壞掉的鈕。
            if (this.dlPngBtn) {
              const canPng = this._resultIsPdfish(j);
              this.dlPngBtn.hidden = !canPng;
              if (canPng) this.dlPngBtn.href = this.downloadPngUrl(jobId);
            }
            if (!this.showDownload) {
              this.dlBtn.hidden = true;
              if (this.dlPngBtn) this.dlPngBtn.hidden = true;
            }
            this._wireSaveWs(j);
            // 作業已經結束 —— 沒有「要不要繼續等」的問題了，收起提示
            if (this.bgNote) this.bgNote.hidden = true;
            this._stop();
            try { this.onDone(j); } catch (_) {}
          } else if (j.status === 'error') {
            // **失敗原因也要翻** —— 它跟 `j.message` 一樣是伺服器在背景
            // 執行緒裡產生的中文（那時候不知道使用者的語言）。少包這一層
            // 的話，英 / 日介面下「工作失敗了」那一句永遠是中文，而那正是
            // 最需要讀懂的一句。查不到就原樣回傳，最壞跟現在一樣。
            this.status.textContent = tr('失敗：')
              + (j.error ? tr(j.error) : tr('未知錯誤'));
            this._finishElapsed(tr('已過 {0}'));
            this.bar.style.background = '#dc2626';
            // 作業已經結束 —— 沒有「要不要繼續等」的問題了，收起提示
            if (this.bgNote) this.bgNote.hidden = true;
            this._stop();
            try { this.onError(j); } catch (_) {}
          } else if (j.status === 'cancelled') {
            // 走同一支 —— 這條路是「別的地方取消的」（另一個分頁、管理頁），
            // 畫面要跟自己按停止長得一樣。
            this._finishElapsed(tr('已過 {0}'));
            this._stop();
            try { this.markStopped(j.message ? tr(j.message) : null, j); } catch (_) {}
          } else if (j.status === 'interrupted') {
            // 服務在這個工作執行時重新啟動（多半是 `jtdt update` 升級）。
            // 少了這個分支，狀態會落到所有 if 之外 → 進度條**永遠轉下去**，
            // 使用者只看到一個卡住的頁面，不知道該重送。
            this.status.textContent =
              tr('服務已重新啟動，這個作業被中斷，請重新送出');
            this._finishElapsed(tr('已過 {0}'));
            this.bar.style.background = '#f59e0b';
            // 作業已經結束 —— 沒有「要不要繼續等」的問題了，收起提示
            if (this.bgNote) this.bgNote.hidden = true;
            this._stop();
            try { this.onError(j); } catch (_) {}
          }
        } catch (e) {
          this.status.textContent = tr('查詢狀態失敗');
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

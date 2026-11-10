// 加密紙本備份：還原的畫面邏輯（網站頁面與離線還原包共用同一份）。
// 讀主文件與分片（PDF、圖片、.jtpb、手打的備援碼）→ 進度 → 還原 → 顯示 / 下載。全部在瀏覽器裡，不上傳。
// 元素 id 固定（pbLog、pbProg*、pbCodes、pbRestoreGo、pbResult*）；文字由呼叫端給（介面語言）。
import * as C from './pb_codec.mjs';
import { Collector } from './pb_core.mjs';
import { scanAll } from './pb_scan.mjs';
import { isTestRecord, TestChecker } from './pb_testpage.mjs';

// opts：{ M, loadPdfjs: async () => pdfjs 模組, jsQR, onAudit(body), onReset() }
export function initRestore(opts) {
  const M = opts.M;
  const $ = id => document.getElementById(id);
  const fmt = (s, ...a) => a.reduce((t, v, i) => t.split('{' + i + '}').join(String(v)), s);
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
  const errText = e => (e && M['err_' + e.code]) ? M['err_' + e.code] : fmt(M.err_generic, (e && (e.code || e.message)) || String(e));
  let col = new Collector();
  let tester = new TestChecker();             // 列印測試頁：另外比對，不混進正式備份
  function renderTest() {
    const box = $('pbTestResult');
    if (!box) return;
    box.hidden = !tester.seen();
    if (!tester.seen()) return;
    const lines = tester.summary().map(s => {
      let t = fmt(M.testLine, M['density_' + s.density], s.ok, s.total);
      if (s.missing.length) t += fmt(M.testMissing, s.missing.join(', '));
      if (s.wrong.length) t += fmt(M.testWrong, s.wrong.join(', '));
      return t;
    });
    const dp = tester.demoProgress();
    lines.push(tester.demoResult === 'ok' ? M.testDemoOk : tester.demoResult === 'fail' ? M.testDemoFail
               : fmt(M.testDemoWait, dp.blocks, dp.shares));
    box.className = 'pb-test ' + (tester.allOk() ? 'pb-ok' : 'pb-warn');
    box.replaceChildren(el('b', null, tester.allOk() ? M.testAllOk : M.testNotAll), ...lines.map(t => el('div', null, t)));
    // 示範備份收齊了就真的解一次（非同步），解完再畫一次
    if (dp.ready && !tester.demoResult) tester.verifyDemo().then(() => renderTest());
  }
  let resultEnv = null, resultUrl = null;
  // 來源：每個讀進來的檔案、手動輸入的分片、相機各算一個，保留它解出的每一筆記錄（含被拒的），
  // 移除某個來源時用剩下的來源從頭重算 —— 傳錯檔案可以拿掉，原本因衝突被擋的也會自動恢復。
  let sources = [], nextId = 1;
  function sourceFor(key, name) {
    let s = key && sources.find(x => x.key === key);
    if (!s) { s = { id: nextId++, key, name, all: [], fatal: '', none: false }; sources.push(s); }
    return s;
  }
  function feed(s, bytes) {
    s.all.push(bytes);
    return apply(s, bytes);
  }
  function apply(s, bytes) {
    if (isTestRecord(bytes)) {
      const st = tester.add(bytes);
      if (st === 'wrong') s.issues.push(M.issueBadQr); else s.test++;
      return st === 'ok' ? 1 : 0;
    }
    try {
      const r = col.add(bytes);
      if (r.status === 'other_set') { s.issues.push(fmt(M.issueOtherSet, C.backupIdText(r.rec.backupId))); return 0; }
      if (r.rec.type === C.TYPE_MAIN) s.blocks.add(r.rec.idx + 1); else s.shares.add(r.rec.shareNo);
      return r.status === 'new' ? 1 : 0;
    } catch (e) {
      s.issues.push(e && e.code === 'CONFLICT' ? fmt(M.issueConflict, e.detail) : M.issueBadQr);
      return 0;
    }
  }
  function resetStats(s) { s.issues = []; s.blocks = new Set(); s.shares = new Set(); s.test = 0; }
  function rebuild() {
    col = new Collector(); tester = new TestChecker();
    for (const s of sources) { resetStats(s); for (const b of s.all) apply(s, b); }
    renderSources(); renderTest(); renderProgress();
  }
  function ranges(nums) {
    const a = [...nums].sort((x, y) => x - y), out = [];
    for (let i = 0; i < a.length; i++) {
      let j = i; while (j + 1 < a.length && a[j + 1] === a[j] + 1) j++;
      out.push(j > i ? a[i] + '–' + a[j] : String(a[i])); i = j;
    }
    return out.join(', ');
  }
  function renderSources() {
    const ul = $('pbLog');
    ul.replaceChildren(...sources.map(s => {
      const li = el('li', 'pb-src');
      const body = el('div', 'pb-src-body');
      const nm = el('div', 'pb-src-name', s.name);
      const parts = [];
      if (s.blocks.size) parts.push(fmt(M.srcBlocks, ranges(s.blocks)));
      if (s.shares.size) parts.push(fmt(M.srcShares, ranges(s.shares)));
      if (s.test) parts.push(fmt(M.srcTest, s.test));
      const line = el('div', 'pb-src-what', s.fatal ? s.fatal : s.none ? M.issueNone : (parts.join(' · ') || M.srcNothing));
      if (s.fatal || s.none || !parts.length) line.classList.add('bad');
      body.append(nm, line);
      for (const t of [...new Set(s.issues)]) body.append(el('div', 'pb-src-what bad', t));
      const rm = el('button', 'btn btn-small');
      rm.type = 'button';
      const ic = opts.icon && opts.icon('trash'); if (ic) rm.append(ic);
      rm.append(el('span', null, M.removeSrc));
      rm.addEventListener('click', () => { sources = sources.filter(x => x !== s); rebuild(); });
      li.append(body, rm);
      return li;
    }));
  }
  function addText(s, text) {
    let b;
    try { b = C.qrTextToBytes(text); } catch (_e) { s.issues.push(M.issueBadQr); return 0; }
    return feed(s, b);
  }
  function renderProgress() {
    const p = col.progress();
    $('pbProgId').textContent = p.backupId ? fmt(M.idIs, C.backupIdText(p.backupId)) : '';
    $('pbProgMain').textContent = p.blocksHave + ' / ' + (p.blocksTotal ?? '?');
    $('pbBarMain').style.width = p.blocksTotal ? (100 * p.blocksHave / p.blocksTotal) + '%' : '0';
    $('pbProgMiss').textContent = p.blocksMissing.length ? fmt(M.blocksMissing, p.blocksMissing.slice(0, 30).join(', ') + (p.blocksMissing.length > 30 ? '…' : '')) : '';
    $('pbProgShare').textContent = p.sharesHave + ' / ' + (p.t ?? '?');
    $('pbBarShare').style.width = p.t ? Math.min(100, 100 * p.sharesHave / p.t) + '%' : '0';
    $('pbProgShareNos').textContent = p.shareNos.length ? fmt(M.sharesHave, p.shareNos.join(', ')) : '';
    $('pbRestoreGo').disabled = !col.ready();
    $('pbRestoreMsg').textContent = col.ready() ? M.ready : '';
  }
  renderProgress();

  // src：來源物件，或字串（'camera' 這類固定來源的代號）
  async function scanCanvas(cv, src) {
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    const img = ctx.getImageData(0, 0, cv.width, cv.height);
    const found = scanAll(img.data, cv.width, cv.height, opts.jsQR, { max: 16 });
    if (typeof src === 'string' && !found.length) return 0;      // 相機這一格沒讀到東西：不建空的來源
    const s = typeof src === 'string' ? sourceFor(src, src === 'camera' ? M.srcCamera : src) : src;
    if (!s.issues) resetStats(s);
    let added = 0;
    for (const f of found) added += addText(s, f.text);
    if (added || typeof src === 'string') { renderSources(); renderTest(); }
    return found.length;
  }
  function canvasFrom(source, w, h, maxSide) {
    const s = Math.min(1, maxSide / Math.max(w, h));
    const cv = document.createElement('canvas');
    cv.width = Math.round(w * s); cv.height = Math.round(h * s);
    const ctx = cv.getContext('2d', { willReadFrequently: true });
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.drawImage(source, 0, 0, cv.width, cv.height);
    return cv;
  }
  let pdfjs = null;
  async function readPdf(file, src) {
    if (!pdfjs) pdfjs = await opts.loadPdfjs();
    const doc = await pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()), isEvalSupported: false, enableXfa: false }).promise;
    let total = 0;
    try {
      const pages = Math.min(doc.numPages, 80);
      for (let i = 1; i <= pages; i++) {
        const page = await doc.getPage(i);
        const vp = page.getViewport({ scale: 200 / 72 });
        const cv = document.createElement('canvas'); cv.width = Math.ceil(vp.width); cv.height = Math.ceil(vp.height);
        const ctx = cv.getContext('2d', { willReadFrequently: true });
        ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
        await page.render({ canvasContext: ctx, viewport: vp }).promise;
        total += await scanCanvas(cv, src);
        renderProgress();
      }
    } finally { doc.destroy(); }
    return total;
  }
  async function readFile(file) {
    const name = file.name || '';
    const s = sourceFor(null, name); resetStats(s);
    renderSources();
    $('pbRestoreMsg').textContent = fmt(M.readFile, name);
    let n = 0;
    try {
      if (/\.(jtpb|txt)$/i.test(name)) {
        const recs = C.parseMainFileText(await file.text());
        for (const r of recs) feed(s, r);
        n = recs.length;
      } else if (/\.pdf$/i.test(name) || file.type === 'application/pdf') {
        n = await readPdf(file, s);
      } else {
        const bmp = await createImageBitmap(file);
        try { n = await scanCanvas(canvasFrom(bmp, bmp.width, bmp.height, 3200), s); } finally { bmp.close && bmp.close(); }
      }
      s.none = !n;
    } catch (e) {
      s.fatal = fmt(M.err_generic, (e && (e.code || e.message)) || e);
    }
    renderSources(); renderTest(); renderProgress();
  }
  // 手動輸入備援碼：16 組 × 6 字，逐組即時檢查
  const codeInputs = [];
  for (let g = 0; g < C.CODE_GROUPS; g++) {
    const wrapEl = el('label');
    wrapEl.append(el('span', 'pb-gno', String(g + 1).padStart(2, '0')));
    const inp = el('input', 'field'); inp.maxLength = 8; inp.autocomplete = 'off'; inp.spellcheck = false;
    inp.setAttribute('aria-label', String(g + 1));
    inp.addEventListener('input', () => {
      const v = inp.value;
      inp.classList.remove('good', 'bad');
      const norm = C.normalizeGroup(v);
      if (norm && norm.length === 6) inp.classList.add(C.checkGroup(v, g) ? 'good' : 'bad');
      else if (!norm) inp.classList.add('bad');
      if (norm && norm.length === 6 && C.checkGroup(v, g) && codeInputs[g + 1]) codeInputs[g + 1].focus();
    });
    inp.addEventListener('paste', e => {
      const text = (e.clipboardData || window.clipboardData).getData('text');
      const groups = C.splitCodeText(text);
      if (groups) { e.preventDefault(); groups.forEach((s, i) => { codeInputs[i].value = s; codeInputs[i].dispatchEvent(new Event('input')); }); }
    });
    wrapEl.append(inp);
    codeInputs.push(inp);
    $('pbCodes').append(wrapEl);
  }
  $('pbCodeAdd').addEventListener('click', () => {
    const vals = codeInputs.map(i => i.value);
    if (vals.some(v => !v.trim())) { $('pbCodeMsg').textContent = M.codeIncomplete; return; }
    const bad = vals.findIndex((v, g) => !C.checkGroup(v, g));
    if (bad >= 0) { $('pbCodeMsg').textContent = fmt(M.codeGroupBad, bad + 1); codeInputs[bad].focus(); return; }
    let rec;
    try { rec = C.codeToShareRecord(vals); } catch (_e) { $('pbCodeMsg').textContent = fmt(M.badQr, M.kindText); return; }
    const src = sourceFor(null, fmt(M.srcCode, rec[14])); resetStats(src);
    if (feed(src, rec)) {
      $('pbCodeMsg').textContent = fmt(M.codeAdded, rec[14]);
      codeInputs.forEach(i => { i.value = ''; i.classList.remove('good', 'bad'); });
    }
    renderSources(); renderProgress();
  });
  $('pbCodeClear').addEventListener('click', () => { codeInputs.forEach(i => { i.value = ''; i.classList.remove('good', 'bad'); }); $('pbCodeMsg').textContent = ''; });

  // 還原
  function clearResult() {
    if (resultUrl) URL.revokeObjectURL(resultUrl); resultUrl = null;
    if (resultEnv) resultEnv.content.fill(0); resultEnv = null;
    $('pbResultText').value = ''; $('pbResultTextBox').hidden = true; $('pbResult').hidden = true;
  }
  $('pbRestoreGo').addEventListener('click', async () => {
    clearResult();
    $('pbRestoreMsg').textContent = M.restoring;
    const p = col.progress();
    try {
      resultEnv = await col.restore();
      const when = resultEnv.created ? new Date(resultEnv.created * 1000).toISOString().slice(0, 10) : '';
      const what = resultEnv.kind === C.KIND_TEXT ? M.kindText : fmt(M.kindFile, resultEnv.name);
      $('pbResultMeta').textContent = fmt(M.restored, what, when);
      $('pbResult').hidden = false;
      $('pbResultShow').hidden = resultEnv.kind !== C.KIND_TEXT;
      $('pbResultCopy').hidden = true;
      $('pbRestoreMsg').textContent = '';
      opts.onAudit({ action: 'restore', backup_id: C.hex(p.backupId), t: p.t, n: p.n, ok: true });
    } catch (e) {
      $('pbRestoreMsg').textContent = e && e.code === 'DECRYPT_FAILED' ? M.decryptFailed : errText(e);
      if (p.backupId && p.t) opts.onAudit({ action: 'restore', backup_id: C.hex(p.backupId), t: p.t, n: p.n, ok: false });
    }
  });
  $('pbResultShow').addEventListener('click', () => {
    if (!resultEnv) return;
    $('pbResultText').value = new TextDecoder().decode(resultEnv.content);
    $('pbResultTextBox').hidden = false; $('pbResultCopy').hidden = false;
  });
  $('pbResultCopy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('pbResultText').value); $('pbRestoreMsg').textContent = M.copied; } catch (_e) { $('pbResultText').select(); }
  });
  $('pbResultSave').addEventListener('click', () => {
    if (!resultEnv) return;
    const name = resultEnv.kind === C.KIND_TEXT ? 'restored.txt' : (resultEnv.name || 'restored.bin').replace(/[\\/:*?"<>|\x00-\x1f]/g, '_');
    const blob = new Blob([resultEnv.content], { type: resultEnv.kind === C.KIND_TEXT ? 'text/plain;charset=utf-8' : 'application/octet-stream' });
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    resultUrl = URL.createObjectURL(blob);
    const a = el('a'); a.href = resultUrl; a.download = name; document.body.append(a); a.click(); a.remove();
  });
  $('pbResultClear').addEventListener('click', clearResult);
  $('pbRestoreReset').addEventListener('click', () => {
    sources = []; col = new Collector(); tester = new TestChecker(); renderTest(); renderSources(); clearResult(); if (opts.onReset) opts.onReset(); renderProgress();
  });

  window.addEventListener('pagehide', () => { sources = []; col.reset(); clearResult(); });
  return {
    readFiles: async files => { for (const f of files) await readFile(f); },
    scanCanvas, canvasFrom, renderProgress,
    ready: () => col.ready(),
  };
}

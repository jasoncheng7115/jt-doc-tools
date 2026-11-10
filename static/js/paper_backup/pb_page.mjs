// 加密紙本備份頁面：建立與還原全部在瀏覽器裡（主機看不到內容、金鑰與分片）。
// 畫面上的文字都由樣板的 #pbText18n 給（介面語言），這裡不寫死任何一句。
import * as C from './pb_codec.mjs';
import { createBackup } from './pb_core.mjs';
import { buildMainPdf, buildSharePdf, buildTestPdf } from './pb_print.mjs';
import { initRestore } from './pb_restore.mjs';
import { makeZip } from './pb_zip.mjs';

const $ = id => document.getElementById(id);
const I = JSON.parse($('pbText18n').textContent);
const L = I.L, M = I.M;
const fmt = (s, ...a) => a.reduce((t, v, i) => t.split('{' + i + '}').join(String(v)), s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const ICONS = $('pbIcons').content;

// ---- 安全連線檢查（WebCrypto 只在 HTTPS / localhost）
const secure = window.isSecureContext && window.crypto && crypto.subtle && typeof CompressionStream === 'function';
if (!secure) { $('pbInsecure').hidden = false; $('pbTool').hidden = true; }

// ---- 分頁
document.querySelectorAll('.pb-tab').forEach(b => b.addEventListener('click', () => {
  document.querySelectorAll('.pb-tab').forEach(x => x.classList.toggle('active', x === b));
  $('pbCreate').hidden = b.dataset.tab !== 'create';
  $('pbRestore').hidden = b.dataset.tab !== 'restore';
  if (b.dataset.tab !== 'restore') stopCam();
}));

function jtSel(sel) {
  sel.classList.add('jt-select');
  if (window.JtSelect && !sel._jtSelect) new window.JtSelect(sel);
  else if (sel._jtSelect && sel._jtSelect.refresh) sel._jtSelect.refresh();
}

// ================= 建立 =================
function fillTn() {
  const n = $('pbN'), t = $('pbT');
  if (!n.options.length) { for (let i = 2; i <= C.MAX_N; i++) { const o = el('option', null, fmt(M.shareCount, i)); o.value = String(i); n.append(o); } n.value = '3'; }
  const nv = parseInt(n.value, 10), keep = parseInt(t.value || '2', 10);
  t.replaceChildren(...Array.from({ length: nv - 1 }, (_, k) => { const o = el('option', null, fmt(M.shareCount, k + 2)); o.value = String(k + 2); return o; }));
  t.value = String(Math.min(Math.max(2, keep), nv));
  jtSel(n); jtSel(t);
  renderHow();
}
// 頁首「主文件＋分片＝還原」的說明，數字跟著設定走
function renderHow() {
  const nv = $('pbN').value, tv = $('pbT').value;
  $('pbHowShares').textContent = fmt(M.howShares, nv);
  $('pbHowSharesDesc').textContent = fmt(M.howSharesDesc, nv);
  $('pbHowRestore').textContent = fmt(M.howRestore, tv, nv);
}
$('pbT').addEventListener('change', renderHow);
fillTn();
$('pbN').addEventListener('change', fillTn);
jtSel($('pbPaper'));


// 輸入：同一個地方打字 / 貼文字 / 拖檔（不用先切換）。有檔案時就備份檔案，文字框先收起來。
let pickedFile = null;
const dropBar = $('pbDrop'), dropTextEl = $('pbDropText'), dropOrig = dropTextEl.innerHTML;
function sizeText(n) { return n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB'; }
function setFile(f) {
  if (!f) return;
  pickedFile = f;
  dropBar.classList.add('has-file');
  dropTextEl.replaceChildren(el('b', null, f.name), el('span', 'pb-drop-hint', sizeText(f.size)));
  $('pbFileClear').hidden = false; $('pbText').hidden = true;
}
function clearFile() {
  pickedFile = null; $('pbFileInput').value = '';
  dropBar.classList.remove('has-file'); dropTextEl.innerHTML = dropOrig;
  $('pbFileClear').hidden = true; $('pbText').hidden = false;
}
$('pbFileInput').addEventListener('change', e => setFile(e.target.files[0]));
$('pbFileClear').addEventListener('click', e => { e.preventDefault(); e.stopPropagation(); clearFile(); });
dropBar.addEventListener('click', e => { if (pickedFile) e.preventDefault(); });   // 已選檔時點條子不重開選檔
for (const [node, cls] of [[dropBar, 'is-dragover'], [$('pbText'), 'drop-hover']]) {
  ['dragenter', 'dragover'].forEach(ev => node.addEventListener(ev, e => {
    if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { e.preventDefault(); node.classList.add(cls); }
  }));
  ['dragleave', 'dragend'].forEach(ev => node.addEventListener(ev, () => node.classList.remove(cls)));
  node.addEventListener('drop', e => {
    node.classList.remove(cls);
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) { e.preventDefault(); setFile(f); }
  });
}
// 貼上：剪貼簿裡是檔案就當檔案，是文字就照常貼進文字框
$('pbText').addEventListener('paste', e => {
  const f = e.clipboardData && e.clipboardData.files && e.clipboardData.files[0];
  if (f) { e.preventDefault(); setFile(f); }
});

// 文字畫成 300 dpi 灰階影像（中文用本機字型；放進 PDF）
const SANS = '"Noto Sans TC","Noto Sans CJK TC","Microsoft JhengHei","PingFang TC","Heiti TC","Hiragino Sans",sans-serif';
const MONO = 'ui-monospace,"SFMono-Regular",Menlo,Consolas,"DejaVu Sans Mono","Noto Sans Mono",monospace';
const DPI = 300, PXMM = DPI / 25.4;
const measureCtx = document.createElement('canvas').getContext('2d');
function wrap(ctx, text, maxW) {
  const tokens = String(text).match(/[\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF\u3000-\u303F]|[^\s\u2E80-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF\u3000-\u303F]+\s*|\s+/g) || [''];
  const lines = []; let cur = '';
  for (const tk of tokens) {
    if (cur && ctx.measureText(cur + tk).width > maxW) { lines.push(cur.trimEnd()); cur = tk.trimStart(); }
    else cur += tk;
  }
  lines.push(cur.trimEnd());
  return lines;
}
function textImage(spec) {
  const wPx = Math.max(1, Math.round(spec.width * PXMM));
  const rows = [];
  for (const ln of spec.lines) {
    const px = ln.size * DPI / 72;
    const font = (ln.bold ? '700 ' : '400 ') + px.toFixed(1) + 'px ' + (ln.mono ? MONO : SANS);
    measureCtx.font = font;
    for (const t of wrap(measureCtx, ln.t, wPx)) rows.push({ t, font, h: px * 1.35 + (spec.gap || 0) * PXMM });
  }
  const hPx = Math.max(1, Math.ceil(rows.reduce((s, r) => s + r.h, 0)));
  const cv = document.createElement('canvas'); cv.width = wPx; cv.height = hPx;
  const ctx = cv.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, wPx, hPx);
  ctx.fillStyle = spec.color || '#000'; ctx.textBaseline = 'top';
  let y = 0;
  for (const r of rows) {
    ctx.font = r.font;
    const x = spec.align === 'center' ? (wPx - ctx.measureText(r.t).width) / 2 : 0;
    ctx.fillText(r.t, x, y + r.h * 0.12);
    y += r.h;
  }
  let src = cv;
  if (spec.rotate === 90) {                      // 直排（由下往上讀），給左邊界的浮水印
    const r = document.createElement('canvas'); r.width = hPx; r.height = wPx;
    const rc = r.getContext('2d'); rc.translate(0, wPx); rc.rotate(-Math.PI / 2); rc.drawImage(cv, 0, 0);
    src = r;
  }
  const W = src.width, H = src.height;
  const rgba = src.getContext('2d').getImageData(0, 0, W, H).data;
  const gray = new Uint8Array(W * H);
  for (let i = 0; i < gray.length; i++) gray[i] = rgba[i * 4];
  return { gray, wPx: W, hPx: H, wMm: W / PXMM, hMm: H / PXMM };
}

let madeUrls = [];
function clearMade() {
  madeUrls.forEach(u => URL.revokeObjectURL(u)); madeUrls = [];
  $('pbMadeFiles').replaceChildren(); $('pbMade').hidden = true; $('pbMadeId').textContent = '';
}
// 預覽：用 PDF.js 把產生的 PDF 畫成縮圖（點了用全站的放大檢視看）；只在瀏覽器裡，不上傳
let pdfjsMod = null;
async function pdfjsLib() {
  if (!pdfjsMod) {
    pdfjsMod = await import('/static/vendor/pdfjs/build/pdf.mjs');
    pdfjsMod.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/build/pdf.worker.mjs';
  }
  return pdfjsMod;
}
const PREVIEW_MAX = 8;
async function addPreview(li, blob, label) {
  const box = el('div', 'pb-thumbs'); box.setAttribute('data-lightbox', '');
  li.append(box);
  try {
    const P = await pdfjsLib();
    const doc = await P.getDocument({ data: new Uint8Array(await blob.arrayBuffer()), isEvalSupported: false }).promise;
    try {
      const n = Math.min(doc.numPages, PREVIEW_MAX);
      for (let i = 1; i <= n; i++) {
        const page = await doc.getPage(i);
        const vp0 = page.getViewport({ scale: 1 });
        const vp = page.getViewport({ scale: 900 / vp0.width });
        const cv = document.createElement('canvas'); cv.width = Math.ceil(vp.width); cv.height = Math.ceil(vp.height);
        const ctx = cv.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
        await page.render({ canvasContext: ctx, viewport: vp }).promise;
        const img = el('img'); img.src = cv.toDataURL('image/png');
        img.alt = fmt(M.previewPage, label, i, doc.numPages); img.dataset.caption = img.alt;
        box.append(img);
      }
      if (doc.numPages > PREVIEW_MAX) box.append(el('span', 'pb-more', fmt(M.previewMore, doc.numPages - PREVIEW_MAX)));
    } finally { doc.destroy(); }
  } catch (_e) { box.remove(); }
}
function fileRow(name, label, blob) {
  const url = URL.createObjectURL(blob); madeUrls.push(url);
  const li = el('li');
  li.append(el('span', 'pb-fname', label));
  const a = el('a', 'btn btn-small');
  a.href = url; a.download = name;
  a._pbBlob = blob;                     // 站台 CSP 的 connect-src 不含 blob:，要讀回內容（測試）用這個
  const svg = ICONS.querySelector('[data-pb-ic=download] svg'); if (svg) a.append(svg.cloneNode(true));
  a.append(el('span', null, M.download));
  li.append(a);
  return li;
}
function errText(e) {
  if (e && e.code === 'TOO_MANY_PAGES') return fmt(M.err_TOO_MANY_PAGES, e.detail);
  if (e && M['err_' + e.code]) return M['err_' + e.code];
  return fmt(M.err_generic, (e && (e.code || e.message)) || String(e));
}
function post(body) { fetch(location.pathname.replace(/\/?$/, '/') + 'audit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).catch(() => {}); }

$('pbMake').addEventListener('click', async () => {
  const kindFile = !!pickedFile;
  let content, name = '', mime = '';
  if (kindFile) {
    content = new Uint8Array(await pickedFile.arrayBuffer()); name = pickedFile.name.slice(0, 200); mime = (pickedFile.type || '').slice(0, 100);
  } else {
    const t = $('pbText').value;
    if (!t) { $('pbMakeMsg').textContent = M.needContent; return; }
    content = new TextEncoder().encode(t); mime = 'text/plain;charset=utf-8';
  }
  const mode = 'paper';                     // 這支工具就是為紙本設計：主文件一律印成 QR 紙本
  const t = parseInt($('pbT').value, 10), n = parseInt($('pbN').value, 10);
  const density = document.querySelector('input[name=pbDensity]:checked').value, paper = $('pbPaper').value, label = $('pbLabel').value.trim();
  const btn = $('pbMake');
  btn.disabled = true; $('pbMakeMsg').textContent = M.making;
  clearMade();
  try {
    const b = await createBackup({ kind: kindFile ? C.KIND_FILE : C.KIND_TEXT, name, mime, content, t, n, density, mode });
    content.fill(0);
    const idText = C.backupIdText(b.backupId);
    const base = 'JTPB-' + idText;
    const now = new Date();
    const cx = I.ctx || {};
    const watermark = fmt(L.watermark, cx.user || L.anonymous, cx.ip || '-', cx.host || location.host, window.jtTime ? window.jtTime.dt(now) : now.toISOString());
    const dateText = window.jtTime ? window.jtTime.date(now) : now.toISOString().slice(0, 10);
    const opts = { paper, label, created: now, dateText, watermark, L, text: textImage };
    const list = $('pbMadeFiles');
    const previews = [], zipFiles = [];
    {
      const blob = new Blob([await buildMainPdf(b, opts)], { type: 'application/pdf' });
      const li = fileRow(base + '-main.pdf', M.mainPdf, blob); list.append(li); previews.push([li, blob, M.mainPdf]);
      zipFiles.push([base + '-main.pdf', blob]);
    }
    for (let i = 0; i < b.n; i++) {
      const lab = fmt(M.sharePdf, i + 1, b.n);
      const blob = new Blob([await buildSharePdf(b, i, opts)], { type: 'application/pdf' });
      const li = fileRow(base + '-share-' + (i + 1) + '-of-' + b.n + '.pdf', lab, blob); list.append(li); previews.push([li, blob, lab]);
      zipFiles.push([base + '-share-' + (i + 1) + '-of-' + b.n + '.pdf', blob]);
    }
    // 全部下載：瀏覽器裡打包成一個 ZIP（不經過主機）
    const zipBlob = new Blob([makeZip(await Promise.all(zipFiles.map(async ([name, bl]) => ({ name, data: new Uint8Array(await bl.arrayBuffer()) }))))],
                            { type: 'application/zip' });
    const zipUrl = URL.createObjectURL(zipBlob); madeUrls.push(zipUrl);
    $('pbZip').href = zipUrl; $('pbZip').download = base + '.zip'; $('pbZip')._pbBlob = zipBlob;
    $('pbMadeId').textContent = idText;
    $('pbMade').hidden = false;
    $('pbMakeMsg').textContent = M.made;
    post({ action: 'create', backup_id: C.hex(b.backupId), t, n, mode, density, blocks: b.mainRecords.length });
    for (const [li, blob, lab] of previews) await addPreview(li, blob, lab);
    b.shareRecords.forEach(r => r.fill(0));
  } catch (e) {
    $('pbMakeMsg').textContent = errText(e);
  } finally { btn.disabled = false; }
});
// 列印測試頁：不含秘密，用跟正式備份同一套版面與 QR 編碼
let testUrl = null;
$('pbTestMake').addEventListener('click', async () => {
  const btn = $('pbTestMake'); btn.disabled = true; $('pbTestMsg').textContent = M.testMaking;
  try {
    const now = new Date(), cx = I.ctx || {};
    const opts = { paper: $('pbPaper').value, created: now, L, text: textImage,
                   dateText: window.jtTime ? window.jtTime.date(now) : now.toISOString().slice(0, 10),
                   watermark: fmt(L.watermark, cx.user || L.anonymous, cx.ip || '-', cx.host || location.host, window.jtTime ? window.jtTime.dt(now) : now.toISOString()) };
    const blob = new Blob([await buildTestPdf(opts)], { type: 'application/pdf' });
    if (testUrl) URL.revokeObjectURL(testUrl);
    $('pbTestFiles').replaceChildren();
    const li = fileRow('JTPB-print-test-' + opts.paper + '.pdf', M.testPdf, blob);
    testUrl = li.querySelector('a').href;
    $('pbTestFiles').append(li);
    $('pbTestMsg').textContent = '';
    await addPreview(li, blob, M.testPdf);
  } catch (e) { $('pbTestMsg').textContent = errText(e); }
  finally { btn.disabled = false; }
});

$('pbClear').addEventListener('click', () => {
  clearMade(); $('pbText').value = ''; $('pbLabel').value = ''; clearFile(); $('pbMakeMsg').textContent = '';
});

// ================= 還原（共用 pb_restore.mjs；網站多一個相機） =================
const R = initRestore({
  M, jsQR: window.jsQR, onAudit: post, onReset: () => stopCam(),
  icon: name => { const svg = ICONS.querySelector('[data-pb-ic=' + name + '] svg'); return svg ? svg.cloneNode(true) : null; },
  loadPdfjs: async () => {
    const p = await import('/static/vendor/pdfjs/build/pdf.mjs');
    p.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/build/pdf.worker.mjs';
    return p;
  },
});
if (window.FileUpload && $('pbScanUp')) {
  const up = new window.FileUpload($('pbScanUp'), async files => {
    await R.readFiles(Array.isArray(files) ? files : [files]);
    up.reset();
  });
}

// 相機（HTTPS / localhost 才有）
let stream = null, camTimer = null;
function stopCam() {
  if (camTimer) { clearInterval(camTimer); camTimer = null; }
  if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; }
  $('pbVideo').srcObject = null; $('pbCamBox').hidden = true; $('pbCamOn').hidden = false; $('pbCamOff').hidden = true;
}
$('pbCamOn').addEventListener('click', async () => {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { $('pbCamMsg').textContent = M.camNoSupport; return; }
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment', width: { ideal: 1920 } }, audio: false });
  } catch (e) { $('pbCamMsg').textContent = fmt(M.camDenied, (e && e.name) || e); return; }
  const v = $('pbVideo'); v.srcObject = stream; await v.play();
  $('pbCamBox').hidden = false; $('pbCamOn').hidden = true; $('pbCamOff').hidden = false; $('pbCamMsg').textContent = M.camScanning;
  let busy = false;
  camTimer = setInterval(async () => {
    if (busy || !v.videoWidth) return;
    busy = true;
    try { const n = await R.scanCanvas(R.canvasFrom(v, v.videoWidth, v.videoHeight, 1600), 'camera'); if (n) R.renderProgress(); }
    finally { busy = false; }
    if (R.ready()) stopCam();
  }, 350);
});
$('pbCamOff').addEventListener('click', stopCam);
window.addEventListener('pagehide', stopCam);

// 離線還原程式的檔名與 SHA-256（給使用者記下、日後核對）
fetch('/paper-backup/offline.json').then(r => r.ok ? r.json() : null).then(j => {
  if (j) $('pbOfflineInfo').textContent = fmt(M.offlineSha, j.filename, j.sha256);
}).catch(() => {});

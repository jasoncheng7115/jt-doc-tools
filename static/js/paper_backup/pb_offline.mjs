// 加密紙本備份：離線還原包的介面（由離線 HTML 的啟動程式以 Blob 網址載入）。
// 還原邏輯跟網站共用 pb_restore.mjs；這裡只有：選檔 / 拖放、PDF.js 從內嵌的網址載入、自我測試。
import * as C from './pb_codec.mjs';
import { Collector } from './pb_core.mjs';
import { initRestore } from './pb_restore.mjs';

const $ = id => document.getElementById(id);
const M = JSON.parse($('pbText18n').textContent).M;
const urls = window.__PB_OFFLINE || {};

const R = initRestore({
  M, jsQR: window.jsQR, onAudit: () => {}, onReset: () => {},
  loadPdfjs: async () => {
    try {
      const p = await import(urls.pdf);
      p.GlobalWorkerOptions.workerSrc = urls.pdfworker;
      return p;
    } catch (_e) { throw Object.assign(new Error(M.offPdfFail), { code: 'PDF_UNAVAILABLE' }); }
  },
});

const drop = $('pbDrop'), input = $('pbFiles');
input.addEventListener('change', async () => { const fs = [...input.files]; input.value = ''; await R.readFiles(fs); });
['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', async e => { const fs = [...(e.dataTransfer && e.dataTransfer.files || [])]; if (fs.length) await R.readFiles(fs); });
// 檔案拖到頁面其他地方不要讓瀏覽器直接打開它
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => e.preventDefault());

// 自我測試：用內嵌的公開測試資料（不是任何人的秘密）跑一次完整還原
$('pbSelfTest').addEventListener('click', async () => {
  const v = JSON.parse($('pbVector').textContent);
  try {
    const col = new Collector();
    for (const s of [...v.main, ...v.shares]) col.add(C.qrTextToBytes(s));
    const env = await col.restore();
    const want = Uint8Array.from(atob(v.content_b64), ch => ch.charCodeAt(0));
    const same = env.content.length === want.length && env.content.every((b, i) => b === want[i]);
    $('pbSelfMsg').textContent = same ? M.selfTestOk : M.selfTestFail.replace('{0}', 'mismatch');
  } catch (e) {
    $('pbSelfMsg').textContent = M.selfTestFail.replace('{0}', (e && (e.code || e.message)) || e);
  }
});

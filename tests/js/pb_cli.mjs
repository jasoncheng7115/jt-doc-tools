// 測試用指令：讓 Python 測試用瀏覽器同一份模組建立 / 還原，做跨實作交叉驗證。
// node --experimental-loader tests/js/pb_loader.mjs tests/js/pb_cli.mjs make '<json>'
// node --experimental-loader tests/js/pb_loader.mjs tests/js/pb_cli.mjs restore <檔案>
import { readFileSync } from 'node:fs';
import { webcrypto } from 'node:crypto';
if (!globalThis.crypto || !globalThis.crypto.subtle) globalThis.crypto = webcrypto;
const C = await import('../../static/js/paper_backup/pb_codec.mjs');
const { createBackup, Collector } = await import('../../static/js/paper_backup/pb_core.mjs');
const { buildMainPdf, buildSharePdf, buildTestPdf } = await import('../../static/js/paper_backup/pb_print.mjs');
const TP = await import('../../static/js/paper_backup/pb_testpage.mjs');
const { scanAll } = await import('../../static/js/paper_backup/pb_scan.mjs');
import { writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const jsQR = createRequire(import.meta.url)('../../static/vendor/jsqr/jsQR.js');

// 測試用替身：文字畫成白底、左上一條黑線的影像（只驗版面與 QR，不驗字形）
const DPI = 100;
function stubText(spec) {
  const lines = spec.lines.length;
  const hMm = spec.lines.reduce((s, l) => s + l.size * 0.3528 * 1.35, 0) + (spec.gap || 0) * lines;
  const wMm = spec.width;
  let wPx = Math.max(1, Math.round(wMm * DPI / 25.4)), hPx = Math.max(1, Math.round(hMm * DPI / 25.4));
  const gray = new Uint8Array(wPx * hPx).fill(255);
  for (let x = 0; x < Math.min(wPx, 20); x++) gray[x] = 0;
  if (spec.rotate === 90) return { gray, wPx: hPx, hPx: wPx, wMm: hMm, hMm: wMm };
  return { gray, wPx, hPx, wMm, hMm };
}
const L = {
  mainTitle: 'Main', fieldId: 'ID {0}', fieldThreshold: '{0} of {1}', fieldBlocks: '{0} blocks {1} pages', fieldFormat: 'JTPB v1 {0}',
  density_low: 'low', density_medium: 'medium', density_high: 'high', fieldCreated: 'Created {0}', pageOf: 'Page {0}/{1}',
  mainHelp: ['help'], mainWarn: ['warn'], mainHeader: 'Main {0} {1}/{2}', blockLabel: 'Block {0}/{1}',
  shareTitle: 'Share {0}/{1}', codeTitle: 'Code', shareHelp: ['help'], shareWarn: ['warn'],
  signCustodian: 'Custodian', signDate: 'Date', signName: 'Signature',
  testBanner: 'TEST', testHelp: ['help {0}'], testQrLabel: '{0} {1} {2}/{3}', testRuler: '50 mm', testFields: ['a', 'b', 'c', 'd', 'e', 'f'], testDemoHelp: ['demo'], testDemoMain: 'dm', testDemoShare: 'ds {0}/{1}', testDemoCode: 'code',
};
const b64 = b => Buffer.from(b).toString('base64');
const [cmd, arg] = process.argv.slice(2);
if (cmd === 'make') {
  const p = JSON.parse(arg);
  const b = await createBackup({ ...p, content: Buffer.from(p.content_b64, 'base64') });
  console.log(JSON.stringify({
    backupId: C.hex(b.backupId), backupIdText: C.backupIdText(b.backupId), compressed: b.compressed,
    main: b.mainRecords.map(r => C.base45Encode(r)), shares: b.shareRecords.map(r => C.base45Encode(r)),
    codes: b.shareRecords.map(r => C.shareRecordToCode(r)),
  }));
} else if (cmd === 'pdf') {
  // 建一組並把主文件、每一份分片的 PDF 寫進目錄
  const p = JSON.parse(arg);
  const b = await createBackup({ ...p, content: Buffer.from(p.content_b64, 'base64') });
  const opts = { paper: p.paper || 'a4', label: p.label || '', created: new Date(1762646400000), watermark: p.watermark || '', L, text: stubText };
  writeFileSync(p.out + '/main.pdf', await buildMainPdf(b, opts));
  for (let i = 0; i < b.n; i++) writeFileSync(p.out + '/share-' + (i + 1) + '.pdf', await buildSharePdf(b, i, opts));
  console.log(JSON.stringify({ main: b.mainRecords.map(r => C.base45Encode(r)), shares: b.shareRecords.map(r => C.base45Encode(r)),
                               codes: b.shareRecords.map(r => C.shareRecordToCode(r)) }));
} else if (cmd === 'testpdf') {
  const p = JSON.parse(arg);
  writeFileSync(p.out + '/test.pdf', await buildTestPdf({ paper: p.paper || 'a4', created: new Date(1762646400000), watermark: 'wm', L, text: stubText }));
  console.log(JSON.stringify({ expected: TP.testRecords().map(r => C.base45Encode(r.rec)), demo: [...TP.DEMO.main, ...TP.DEMO.shares], demoContent: TP.DEMO.content }));
} else if (cmd === 'scan') {
  // 參數：JSON [{file, w, h}]，檔案是原始 RGBA
  const items = JSON.parse(readFileSync(arg, 'utf-8'));
  const out = items.map(it => {
    const rgba = new Uint8ClampedArray(readFileSync(it.file));
    return scanAll(rgba, it.w, it.h, jsQR, { max: 16 }).map(f => f.text);
  });
  console.log(JSON.stringify(out));
} else if (cmd === 'restore') {
  const p = JSON.parse(readFileSync(arg, 'utf-8'));
  const col = new Collector();
  try {
    for (const s of [...p.main, ...p.shares]) col.add(C.qrTextToBytes(s));
    for (const g of p.codes || []) col.add(C.codeToShareRecord(g));
    const e = await col.restore();
    console.log(JSON.stringify({ ok: true, kind: e.kind, name: e.name, mime: e.mime, created: e.created, content_b64: b64(e.content) }));
  } catch (e) { console.log(JSON.stringify({ ok: false, code: e.code || String(e) })); }
}

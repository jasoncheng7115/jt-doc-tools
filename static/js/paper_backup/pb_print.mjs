// 加密紙本備份：主文件與分片的 PDF 版面（ADR-001 第 7 節）。
// 文字由呼叫端給的 text() 畫成灰階影像（瀏覽器用 canvas，測試用替身），版面文字由呼叫端給（介面語言）。
import qrcode from '../../vendor/qrcode-generator-2.0.4/qrcode.mjs';
import * as C from './pb_codec.mjs';
import { DENSITY, QR_PER_PAGE } from './pb_core.mjs';
import { PdfDoc, drawQr } from './pb_pdf.mjs';
import { testRecords, PER_DENSITY, DENSITIES, DEMO } from './pb_testpage.mjs';

const MARGIN = 15, COLS = 3, ROWS = 4, QUIET = 4;

export function qrMatrix(text, version) {
  // version 省略＝自動挑最小能放下的（分片用）；主文件固定版本，放不下就是程式錯
  const tryV = v => { const q = qrcode(v, 'Q'); q.addData(text, 'Alphanumeric'); q.make(); return q; };
  let q = null;
  if (version) q = tryV(version);
  else for (let v = 1; v <= 40 && !q; v++) { try { q = tryV(v); } catch (_e) { q = null; } }
  const n = q.getModuleCount();
  const m = []; for (let r = 0; r < n; r++) { const row = []; for (let c = 0; c < n; c++) row.push(q.isDark(r, c)); m.push(row); }
  return m;
}

function fmt(s, ...a) { return a.reduce((t, v, i) => t.replace('{' + i + '}', String(v)), s); }
function put(doc, page, text, spec, x, y) {
  const im = text(spec);
  const id = doc.addImage(im.gray, im.wPx, im.hPx);
  page.image(id, x, y, im.wMm, im.hMm);
  return im.hMm;
}
// 浮水印：產生資訊（帳號、IP、站台、時間）印在頁尾與左邊界 —— 只在邊界，不碰任何 QR 與它的靜區
function watermark(doc, page, text, wm) {
  if (!wm) return;
  const W = doc.w - 2 * MARGIN;
  const foot = text({ width: W, lines: [{ t: wm, size: 7.5 }], color: '#8a8f98' });
  page.image(doc.addImage(foot.gray, foot.wPx, foot.hPx), MARGIN, doc.h - MARGIN + 4, foot.wMm, Math.min(foot.hMm, MARGIN - 6));
  const side = text({ width: doc.h - 2 * MARGIN, lines: [{ t: wm, size: 7.5 }], color: '#8a8f98', rotate: 90 });
  page.image(doc.addImage(side.gray, side.wPx, side.hPx), 4, MARGIN, Math.min(side.wMm, MARGIN - 8), side.hMm);
}
function cornerMarks(page, x, y, w, h) {
  const a = 6, t = 0.6;
  for (const [cx, cy, dx, dy] of [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]]) {
    page.rect(dx > 0 ? cx : cx - a, dy > 0 ? cy : cy - t, a, t);
    page.rect(dx > 0 ? cx : cx - t, dy > 0 ? cy : cy - a, t, a);
  }
  page.fill();
}

// 主文件：封面 1 頁＋每頁 12 個區塊
export async function buildMainPdf(backup, { paper = 'a4', label = '', created = new Date(), dateText = '', watermark: wm = '', L, text }) {
  const dens = DENSITY[backup.density];
  const doc = new PdfDoc(paper, { title: 'JTPB ' + C.backupIdText(backup.backupId) + ' main' });
  const idText = C.backupIdText(backup.backupId);
  const total = backup.mainRecords.length;
  const qrPages = Math.ceil(total / QR_PER_PAGE);
  const pages = qrPages + 1;
  const W = doc.w - 2 * MARGIN;
  dateText = dateText || created.toISOString().slice(0, 10);

  // 封面
  const cover = doc.addPage();
  watermark(doc, cover, text, wm);
  let y = MARGIN;
  y += put(doc, cover, text, { width: W, lines: [{ t: L.mainTitle, size: 20, bold: true }] }, MARGIN, y) + 3;
  if (label) y += put(doc, cover, text, { width: W, lines: [{ t: label, size: 14, bold: true }] }, MARGIN, y) + 3;
  y += put(doc, cover, text, { width: W, lines: [
    { t: fmt(L.fieldId, idText), size: 12 },
    { t: fmt(L.fieldThreshold, backup.t, backup.n), size: 12 },
    { t: fmt(L.fieldBlocks, total, pages), size: 12 },
    { t: fmt(L.fieldFormat, L['density_' + backup.density]), size: 12 },
    { t: fmt(L.fieldCreated, dateText), size: 12 },
    { t: fmt(L.pageOf, 1, pages), size: 10 },
  ] }, MARGIN, y) + 6;
  y += put(doc, cover, text, { width: W, lines: L.mainHelp.map(t => ({ t, size: 11 })) }, MARGIN, y) + 4;
  put(doc, cover, text, { width: W, lines: L.mainWarn.map(t => ({ t, size: 11, bold: true })) }, MARGIN, y);

  // 區塊頁
  const cellW = W / COLS;
  for (let p = 0; p < qrPages; p++) {
    const page = doc.addPage();
    watermark(doc, page, text, wm);
    const hh = put(doc, page, text, { width: W, lines: [{ t: fmt(L.mainHeader, idText, p + 2, pages), size: 10 }] }, MARGIN, MARGIN);
    const top = MARGIN + hh + 4, gridH = doc.h - MARGIN - top;
    const cellH = gridH / ROWS;
    cornerMarks(page, MARGIN - 4, top - 2, W + 8, gridH + 2);
    for (let k = 0; k < QR_PER_PAGE; k++) {
      const i = p * QR_PER_PAGE + k;
      if (i >= total) break;
      const m = qrMatrix(C.base45Encode(backup.mainRecords[i]), dens.version);
      const size = (m.length + 2 * QUIET) * dens.module;
      const cx = MARGIN + (k % COLS) * cellW, cy = top + Math.floor(k / COLS) * cellH;
      const qx = cx + (cellW - size) / 2, qy = cy + 1;
      drawQr(page, m, qx + QUIET * dens.module, qy + QUIET * dens.module, dens.module);
      const lab = text({ width: cellW, align: 'center', lines: [{ t: fmt(L.blockLabel, i + 1, total), size: 9 }] });
      const id = doc.addImage(lab.gray, lab.wPx, lab.hPx);
      page.image(id, cx, qy + size, lab.wMm, lab.hMm);
    }
  }
  return doc.build();
}

// 分片：每份一個 PDF、一頁
export async function buildSharePdf(backup, index, { paper = 'a4', label = '', created = new Date(), dateText = '', watermark: wm = '', L, text }) {
  const rec = backup.shareRecords[index];
  const no = index + 1;
  const doc = new PdfDoc(paper, { title: 'JTPB ' + C.backupIdText(backup.backupId) + ' share ' + no });
  const page = doc.addPage();
  watermark(doc, page, text, wm);
  const W = doc.w - 2 * MARGIN;
  const idText = C.backupIdText(backup.backupId);
  let y = MARGIN;
  y += put(doc, page, text, { width: W, lines: [{ t: fmt(L.shareTitle, no, backup.n), size: 20, bold: true }] }, MARGIN, y) + 2;
  if (label) y += put(doc, page, text, { width: W, lines: [{ t: label, size: 14, bold: true }] }, MARGIN, y) + 2;
  y += put(doc, page, text, { width: W, lines: [
    { t: fmt(L.fieldId, idText), size: 12 },
    { t: fmt(L.fieldThreshold, backup.t, backup.n), size: 12 },
    { t: fmt(L.fieldCreated, dateText || created.toISOString().slice(0, 10)), size: 12 },
  ] }, MARGIN, y) + 4;

  const m = qrMatrix(C.base45Encode(rec));
  const module = 1.0, size = (m.length + 2 * QUIET) * module;
  drawQr(page, m, MARGIN + QUIET * module, y + QUIET * module, module);
  // QR 右邊：文字備援碼（16 組，4 組一行，前面標組號）
  const groups = C.shareRecordToCode(rec);
  const codeLines = [{ t: L.codeTitle, size: 11, bold: true }];
  for (let r = 0; r < 4; r++) {
    codeLines.push({ t: groups.slice(r * 4, r * 4 + 4).map((g, i) => String(r * 4 + i + 1).padStart(2, '0') + ' ' + g).join('   '), size: 13, mono: true });
  }
  const cx = MARGIN + size + 6;
  put(doc, page, text, { width: W - size - 6, lines: codeLines, gap: 2.2 }, cx, y + 2);
  y += size + 6;
  y += put(doc, page, text, { width: W, lines: L.shareHelp.map(t => ({ t, size: 11 })) }, MARGIN, y) + 4;
  y += put(doc, page, text, { width: W, lines: L.shareWarn.map(t => ({ t, size: 11, bold: true })) }, MARGIN, y) + 6;

  // 保管人簽收欄
  const rowH = 11;
  const cols = [L.signCustodian, L.signDate, L.signName];
  const colW = W / cols.length;
  for (let i = 0; i < cols.length; i++) {
    page.box(MARGIN + i * colW, y, colW, rowH * 2);
    put(doc, page, text, { width: colW - 4, lines: [{ t: cols[i], size: 9 }] }, MARGIN + i * colW + 2, y + 1.5);
  }
  page.fill();
  return doc.build();
}

// 列印測試頁：大字「測試資料／不含機密」、三種密度各 3 個 QR（跟正式備份同一套編碼與尺寸）、
// 50 mm 尺規、紀錄欄。A4 與 Letter 都放在一頁。
export const RULER_MM = 50;
export async function buildTestPdf({ paper = 'a4', created = new Date(), dateText = '', watermark: wm = '', L, text }) {
  const doc = new PdfDoc(paper, { title: 'JTPB print test page' });
  const page = doc.addPage();
  watermark(doc, page, text, wm);
  const W = doc.w - 2 * MARGIN;
  let y = MARGIN;
  y += put(doc, page, text, { width: W, lines: [{ t: L.testBanner, size: 18, bold: true }] }, MARGIN, y) + 1;
  y += put(doc, page, text, { width: W, lines: L.testHelp.map(t => ({ t: fmt(t, dateText || created.toISOString().slice(0, 10)), size: 9 })) }, MARGIN, y) + 2;
  const cellW = W / PER_DENSITY;
  const recs = testRecords();
  for (const d of DENSITIES) {
    const dens = DENSITY[d];
    let rowH = 0;
    for (const t of recs.filter(r => r.density === d)) {
      const m = qrMatrix(C.base45Encode(t.rec), dens.version);
      const size = (m.length + 2 * QUIET) * dens.module;
      const cx = MARGIN + (t.n - 1) * cellW;
      const qx = cx + (cellW - size) / 2;
      drawQr(page, m, qx + QUIET * dens.module, y + QUIET * dens.module, dens.module);
      const lab = text({ width: cellW, align: 'center', lines: [{ t: fmt(L.testQrLabel, L['density_' + d], dens.module, t.n, PER_DENSITY), size: 8 }] });
      page.image(doc.addImage(lab.gray, lab.wPx, lab.hPx), cx, y + size, lab.wMm, lab.hMm);
      rowH = Math.max(rowH, size + lab.hMm);
    }
    y += rowH + 2;
  }
  // 50 mm 尺規：主線＋每 10 mm 長刻度、每 5 mm 短刻度
  y += 2;
  const rx = MARGIN, ry = y + 4;
  page.rect(rx, ry, RULER_MM, 0.3);
  for (let mm = 0; mm <= RULER_MM; mm += 5) {
    const h = mm % 10 === 0 ? 4 : 2;
    page.rect(rx + mm - 0.15, ry - h, 0.3, h + 0.3);
  }
  page.fill();
  put(doc, page, text, { width: W - RULER_MM - 6, lines: [{ t: L.testRuler, size: 9 }] }, rx + RULER_MM + 6, y);
  y += 10;
  // 紀錄欄：兩列三欄
  const cols = 3, rowHt = 9, colW = W / cols;
  L.testFields.forEach((f, i) => {
    const x = MARGIN + (i % cols) * colW, yy = y + Math.floor(i / cols) * rowHt;
    page.box(x, yy, colW, rowHt);
    put(doc, page, text, { width: colW - 3, lines: [{ t: f, size: 7.5 }] }, x + 1.5, yy + 1);
  });
  page.fill();
  // 第 2 頁：還原驗證（公開示範備份：主文件 1 塊＋分片 2 份，門檻 2）—— 掃描時真的重組金鑰、解密、比對內容
  const p2 = doc.addPage();
  watermark(doc, p2, text, wm);
  let y2 = MARGIN;
  y2 += put(doc, p2, text, { width: W, lines: [{ t: L.testBanner, size: 18, bold: true }] }, MARGIN, y2) + 1;
  y2 += put(doc, p2, text, { width: W, lines: L.testDemoHelp.map(t => ({ t, size: 9 })) }, MARGIN, y2) + 3;
  const dm = DENSITY.medium;
  const demoQrs = [
    { m: qrMatrix(DEMO.main[0], dm.version), mod: dm.module, label: L.testDemoMain },
    ...DEMO.shares.map((t, i) => ({ m: qrMatrix(t), mod: 1.0, label: fmt(L.testDemoShare, i + 1, DEMO.shares.length) })),
  ];
  let rowH2 = 0;
  demoQrs.forEach((q, i) => {
    const size = (q.m.length + 2 * QUIET) * q.mod;
    const cx = MARGIN + i * cellW, qx = cx + (cellW - size) / 2;
    drawQr(p2, q.m, qx + QUIET * q.mod, y2 + QUIET * q.mod, q.mod);
    const lab = text({ width: cellW, align: 'center', lines: [{ t: q.label, size: 8 }] });
    p2.image(doc.addImage(lab.gray, lab.wPx, lab.hPx), cx, y2 + size, lab.wMm, lab.hMm);
    rowH2 = Math.max(rowH2, size + lab.hMm);
  });
  y2 += rowH2 + 6;
  const groups = C.shareRecordToCode(C.qrTextToBytes(DEMO.shares[1]));
  const codeLines = [{ t: L.testDemoCode, size: 10, bold: true }];
  for (let r = 0; r < 4; r++) {
    codeLines.push({ t: groups.slice(r * 4, r * 4 + 4).map((g, k) => String(r * 4 + k + 1).padStart(2, '0') + ' ' + g).join('   '), size: 12, mono: true });
  }
  put(doc, p2, text, { width: W, lines: codeLines, gap: 2 }, MARGIN, y2);
  return doc.build();
}

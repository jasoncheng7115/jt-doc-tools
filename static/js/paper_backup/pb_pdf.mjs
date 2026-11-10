// 最小的 PDF 產生器：頁面、填色矩形（QR 模組）、8 位元灰階影像（文字先由 canvas 畫好）。
// 不用 jsPDF —— 只需要這三樣，少一個要追安全公告的第三方元件（ADR-001 第 8 節）。
// 座標用 mm、原點在左上角；輸出不含 JavaScript、附件、連結、表單。

const PT = 72 / 25.4;
const n3 = v => { const s = (Math.round(v * 1000) / 1000).toFixed(3); return s.replace(/\.?0+$/, '') || '0'; };
const te = new TextEncoder();

async function zlib(bytes) {
  const reader = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate')).getReader();
  const parts = []; let len = 0;
  for (;;) { const { done, value } = await reader.read(); if (done) break; parts.push(value); len += value.length; }
  const out = new Uint8Array(len); let o = 0; for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

export const PAPER = { a4: [210, 297], letter: [215.9, 279.4] };

export class PdfDoc {
  constructor(paper = 'a4', info = {}) {
    const p = PAPER[paper];
    if (!p) throw new Error('paper');
    this.w = p[0]; this.h = p[1]; this.pages = []; this.images = []; this.info = info;
  }
  addPage() { const pg = new PdfPage(this); this.pages.push(pg); return pg; }
  // gray：Uint8Array（寬 × 高，0 = 黑、255 = 白）
  addImage(gray, wPx, hPx) {
    if (gray.length !== wPx * hPx) throw new Error('image size');
    this.images.push({ gray, wPx, hPx }); return this.images.length;   // 名稱 /Im<n>
  }
  async build() {
    const objs = [];                        // [編號] = Uint8Array（不含 "n 0 obj"）
    const add = body => { objs.push(body); return objs.length; };
    const str = s => te.encode(s);
    const stream = async (dict, raw) => {
      const z = await zlib(raw);
      return { dict: dict + ' /Filter /FlateDecode /Length ' + z.length, data: z };
    };
    const catalogId = add(null), pagesId = add(null);
    const imgIds = [];
    for (const im of this.images) {
      const s = await stream('/Type /XObject /Subtype /Image /Width ' + im.wPx + ' /Height ' + im.hPx + ' /ColorSpace /DeviceGray /BitsPerComponent 8', im.gray);
      imgIds.push(add(s));
    }
    const pageIds = [];
    for (const pg of this.pages) {
      const content = await stream('', str(pg.ops.join('\n')));
      const cid = add(content);
      const xo = [...pg.usedImages].map(i => '/Im' + i + ' ' + imgIds[i - 1] + ' 0 R').join(' ');
      pageIds.push(add(str('<< /Type /Page /Parent ' + pagesId + ' 0 R /MediaBox [0 0 ' + n3(this.w * PT) + ' ' + n3(this.h * PT) + '] '
        + '/Resources << ' + (xo ? '/XObject << ' + xo + ' >> ' : '') + '>> /Contents ' + cid + ' 0 R >>')));
    }
    objs[pagesId - 1] = str('<< /Type /Pages /Kids [' + pageIds.map(i => i + ' 0 R').join(' ') + '] /Count ' + pageIds.length + ' >>');
    objs[catalogId - 1] = str('<< /Type /Catalog /Pages ' + pagesId + ' 0 R >>');
    // 只放不含秘密的資訊（標題＝備份 ID 與文件類型；不放檔名或內容說明）
    const esc = s => String(s).replace(/[^\x20-\x7e]/g, '?').replace(/([\\()])/g, '\\$1');
    const infoId = add(str('<< /Producer (JTDT paper-backup) /Title (' + esc(this.info.title || '') + ') >>'));

    // 檔頭第二行放 4 個高位元組，讓傳輸工具把它當二進位檔
    const chunks = [str('%PDF-1.7\n'), new Uint8Array([0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a])];
    let off = chunks.reduce((n, c) => n + c.length, 0);
    const xref = [];
    objs.forEach((o, i) => {
      xref.push(off);
      let parts;
      if (o instanceof Uint8Array) parts = [str((i + 1) + ' 0 obj\n'), o, str('\nendobj\n')];
      else parts = [str((i + 1) + ' 0 obj\n<< ' + o.dict + ' >>\nstream\n'), o.data, str('\nendstream\nendobj\n')];
      for (const p of parts) { chunks.push(p); off += p.length; }
    });
    let x = 'xref\n0 ' + (objs.length + 1) + '\n0000000000 65535 f \n';
    for (const o of xref) x += String(o).padStart(10, '0') + ' 00000 n \n';
    x += 'trailer\n<< /Size ' + (objs.length + 1) + ' /Root ' + catalogId + ' 0 R /Info ' + infoId + ' 0 R >>\nstartxref\n' + off + '\n%%EOF\n';
    chunks.push(str(x));
    const total = chunks.reduce((n, c) => n + c.length, 0);
    const out = new Uint8Array(total); let o = 0; for (const c of chunks) { out.set(c, o); o += c.length; }
    return out;
  }
}

class PdfPage {
  constructor(doc) { this.doc = doc; this.ops = []; this.usedImages = new Set(); this.rects = []; }
  // 填黑色矩形；同一頁的矩形最後一次 fill（不會在相鄰模組之間留下細縫）
  rect(x, y, w, h) { this.ops.push(n3(x * PT) + ' ' + n3((this.doc.h - y - h) * PT) + ' ' + n3(w * PT) + ' ' + n3(h * PT) + ' re'); this._dirty = true; }
  fill() { if (this._dirty) { this.ops.push('0 g f'); this._dirty = false; } }
  // 細線框（簽收欄、定位記號）：用細矩形畫，粗細 t mm
  box(x, y, w, h, t = 0.25) { this.rect(x, y, w, t); this.rect(x, y + h - t, w, t); this.rect(x, y, t, h); this.rect(x + w - t, y, t, h); }
  image(id, x, y, w, h) {
    this.fill();
    this.usedImages.add(id);
    this.ops.push('q ' + n3(w * PT) + ' 0 0 ' + n3(h * PT) + ' ' + n3(x * PT) + ' ' + n3((this.doc.h - y - h) * PT) + ' cm /Im' + id + ' Do Q');
  }
}

// QR 模組畫成矩形：同一列連續的深色模組合併成一個
export function drawQr(page, matrix, x, y, module) {
  const n = matrix.length;
  for (let r = 0; r < n; r++) {
    let c = 0;
    while (c < n) {
      if (!matrix[r][c]) { c++; continue; }
      let e = c; while (e < n && matrix[r][e]) e++;
      page.rect(x + c * module, y + r * module, (e - c) * module, module);
      c = e;
    }
  }
  page.fill();
}

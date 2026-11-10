// 加密紙本備份：建立與還原（瀏覽器本機；主機看不到明文、金鑰與分片）。
// 加密 AES-256-GCM（WebCrypto），金鑰門檻分片用內建的 Privy shamir-secret-sharing v0.0.3（審計版本，不自己寫有限體運算）。
import { split, combine } from '../../vendor/shamir-secret-sharing-0.0.3/index.js';
import * as C from './pb_codec.mjs';

export const DENSITY = {
  // QR 版本、模組 mm、每塊資料 bytes（Base45 ＋ 糾錯 Q 實測容量扣掉 20 bytes 記錄開銷）
  low:    { version: 10, module: 0.8,  data: 126 },
  medium: { version: 15, module: 0.65, data: 264 },
  high:   { version: 20, module: 0.5,  data: 448 },
};
export const MAX_MAIN_PAGES = 50, QR_PER_PAGE = 12;

function rand(n) { return crypto.getRandomValues(new Uint8Array(n)); }

async function streamThrough(bytes, stream, limit) {
  const reader = new Blob([bytes]).stream().pipeThrough(stream).getReader();
  const parts = []; let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.length;
    if (limit && total > limit) { try { await reader.cancel(); } catch (_e) { /* 已經在中止 */ } throw new C.FormatError('TOO_LARGE'); }
    parts.push(value);
  }
  return C.concat(...parts);
}
// zlib（RFC 1950，CompressionStream 'deflate'）：各瀏覽器與 node 18 都支援，另有 Adler-32 檢查
async function deflate(b) { return streamThrough(b, new CompressionStream('deflate')); }
export async function inflate(b, limit) {
  try { return await streamThrough(b, new DecompressionStream('deflate'), limit); }
  catch (e) { if (e instanceof C.FormatError) throw e; throw new C.FormatError('BAD_COMPRESSION'); }
}

// 一份資料 → 主文件區塊記錄＋N 份分片記錄
export async function createBackup({ kind, name = '', mime = '', content, t, n, density = 'medium', mode = 'paper', created }) {
  if (!(content instanceof Uint8Array)) throw new C.FormatError('BAD_INPUT');
  const maxContent = mode === 'file' ? C.MAX_CONTENT_FILE : C.MAX_CONTENT_PAPER;
  if (content.length > maxContent) throw new C.FormatError('TOO_LARGE');
  if (!(Number.isInteger(t) && Number.isInteger(n) && t >= 2 && t <= n && n <= C.MAX_N)) throw new C.FormatError('BAD_TN');
  const dens = DENSITY[density];
  if (!dens) throw new C.FormatError('BAD_DENSITY');

  const env = await C.packEnvelope({ kind, created: created ?? Math.floor(Date.now() / 1000), name, mime, content });
  const packed = await deflate(env);
  const useDeflate = packed.length < env.length;
  const plain = useDeflate ? packed : env;

  const key = rand(32), nonce = rand(12), backupId = rand(8);
  try {
    const header = C.packHeader({ flags: useDeflate ? C.FLAG_DEFLATE : 0, backupId, t, n, nonce, ctLen: plain.length + 16 });
    const k = await crypto.subtle.importKey('raw', key, { name: 'AES-GCM' }, false, ['encrypt']);
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce, additionalData: header, tagLength: 128 }, k, plain));
    const main = C.concat(header, ct);
    const blocks = Math.ceil(main.length / dens.data);
    if (blocks > C.MAX_BLOCKS) throw new C.FormatError('TOO_LARGE');
    const pages = Math.ceil(blocks / QR_PER_PAGE);
    if (mode === 'paper' && pages > MAX_MAIN_PAGES) throw new C.FormatError('TOO_MANY_PAGES', String(pages));
    const mainRecords = [];
    for (let i = 0; i < blocks; i++) mainRecords.push(C.packMainRecord(backupId, i, blocks, main.subarray(i * dens.data, (i + 1) * dens.data)));
    const shares = await split(key, n, t);
    const shareRecords = shares.map((s, i) => C.packShareRecord(backupId, t, n, i + 1, s));
    shares.forEach(s => s.fill(0));
    return { backupId, t, n, density, mode, mainRecords, shareRecords, mainBytes: main.length, compressed: useDeflate };
  } finally {
    key.fill(0);
  }
}

// 還原端：逐筆收集（QR、電子檔一行、手打的分片），隨時可以問進度；收齊才解
export class Collector {
  constructor() { this.reset(); }
  reset() { this.backupId = null; this.total = null; this.blocks = new Map(); this.shares = new Map(); this.t = null; this.n = null; }
  // rec：記錄位元組。回 { status, rec }：new / dup / other_set；矛盾的資料丟 FormatError
  add(recBytes) {
    const r = C.parseRecord(recBytes);
    if (this.backupId && C.hex(r.backupId) !== C.hex(this.backupId)) return { status: 'other_set', rec: r };
    if (r.type === C.TYPE_MAIN) {
      if (this.total !== null && r.total !== this.total) throw new C.FormatError('CONFLICT', 'total');
      const had = this.blocks.get(r.idx);
      if (had) { if (C.hex(had) !== C.hex(r.data)) throw new C.FormatError('CONFLICT', 'block ' + (r.idx + 1)); return { status: 'dup', rec: r }; }
      this.backupId = this.backupId || r.backupId; this.total = r.total; this.blocks.set(r.idx, r.data);
      return { status: 'new', rec: r };
    }
    if (this.t !== null && (r.t !== this.t || r.n !== this.n)) throw new C.FormatError('CONFLICT', 't/n');
    const x = r.share[C.SHARE_LEN - 1];
    const had = this.shares.get(x);
    if (had) { if (C.hex(had.share) !== C.hex(r.share)) throw new C.FormatError('CONFLICT', 'share'); return { status: 'dup', rec: r }; }
    for (const s of this.shares.values()) if (s.shareNo === r.shareNo) throw new C.FormatError('CONFLICT', 'share_no ' + r.shareNo);
    this.backupId = this.backupId || r.backupId; this.t = r.t; this.n = r.n; this.shares.set(x, { shareNo: r.shareNo, share: r.share });
    return { status: 'new', rec: r };
  }
  progress() {
    const missing = [];
    if (this.total !== null) for (let i = 0; i < this.total; i++) if (!this.blocks.has(i)) missing.push(i + 1);
    return {
      backupId: this.backupId, blocksHave: this.blocks.size, blocksTotal: this.total, blocksMissing: missing,
      sharesHave: this.shares.size, t: this.t, n: this.n, shareNos: [...this.shares.values()].map(s => s.shareNo).sort((a, b) => a - b),
    };
  }
  ready() { return this.total !== null && this.blocks.size === this.total && this.t !== null && this.shares.size >= this.t; }
  async restore() {
    if (!this.ready()) throw new C.FormatError('NOT_READY');
    const parts = []; for (let i = 0; i < this.total; i++) parts.push(this.blocks.get(i));
    const main = C.concat(...parts);
    const h = C.parseHeader(main);
    if (C.hex(h.backupId) !== C.hex(this.backupId)) throw new C.FormatError('CONFLICT', 'header id');
    if (h.t !== this.t || h.n !== this.n) throw new C.FormatError('CONFLICT', 'header t/n');
    const ct = main.subarray(C.HEADER_LEN);
    if (ct.length !== h.ctLen) throw new C.FormatError('BAD_HEADER', 'ct_len');
    // 只用前 T 份（依分片編號）；不自動換組合嘗試
    const use = [...this.shares.values()].sort((a, b) => a.shareNo - b.shareNo).slice(0, this.t).map(s => s.share);
    const key = await combine(use);
    let plain;
    try {
      const k = await crypto.subtle.importKey('raw', key, { name: 'AES-GCM' }, false, ['decrypt']);
      plain = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: h.nonce, additionalData: h.aad, tagLength: 128 }, k, ct));
    } catch (_e) {
      throw new C.FormatError('DECRYPT_FAILED');
    } finally { key.fill(0); }
    const envLimit = C.MAX_CONTENT_FILE + 512;
    const env = h.flags & C.FLAG_DEFLATE ? await inflate(plain, envLimit) : plain;
    return C.parseEnvelope(env);
  }
}

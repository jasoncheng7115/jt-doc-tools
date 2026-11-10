// 加密紙本備份：格式 JTPB v1 的編碼與解碼（不碰 DOM、不碰金鑰運算）。
// 規格見內部 ADR-001；這裡的每一個常數改了都會讓舊紙本讀不回來 —— 只能新增版本，不可改語意。

export const MAGIC = new Uint8Array([0x4a, 0x54, 0x50, 0x42]);   // "JTPB"
export const REC_MAGIC = new Uint8Array([0x50, 0x42]);           // "PB"
export const MAJOR = 1, MINOR = 0, SUITE = 1;
export const HEADER_LEN = 34;
export const TYPE_MAIN = 1, TYPE_SHARE = 2;
export const KIND_TEXT = 1, KIND_FILE = 2;
export const FLAG_DEFLATE = 1;                                  // 明文封包經 zlib（RFC 1950）壓縮
export const SHARE_LEN = 33;                                     // 32 bytes y ‖ 1 byte x
export const SHARE_REC_LEN = 52;
export const MAX_N = 16, MAX_BLOCKS = 2000, MAX_BLOCK_DATA = 2048;
export const MAX_CT_PAPER = 160 * 1024;                          // 128 KiB 內容＋封包＋壓縮失敗的餘裕
export const MAX_CT_FILE = 21 * 1024 * 1024;                     // 電子模式 20 MiB＋餘裕
export const MAX_CONTENT_PAPER = 128 * 1024, MAX_CONTENT_FILE = 20 * 1024 * 1024;

export class FormatError extends Error {
  constructor(code, detail) { super(code + (detail ? ': ' + detail : '')); this.code = code; this.detail = detail || ''; }
}

// ---- CRC32（IEEE，同 zlib）
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
  return t;
})();
export function crc32(bytes) {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

// ---- 位元組工具
export function concat(...parts) {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let o = 0; for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}
function u16(v) { return new Uint8Array([(v >>> 8) & 0xff, v & 0xff]); }
function u32(v) { return new Uint8Array([(v >>> 24) & 0xff, (v >>> 16) & 0xff, (v >>> 8) & 0xff, v & 0xff]); }
function rd16(b, o) { return (b[o] << 8) | b[o + 1]; }
function rd32(b, o) { return ((b[o] << 24) >>> 0) + (b[o + 1] << 16) + (b[o + 2] << 8) + b[o + 3]; }
function eq(a, b) { if (a.length !== b.length) return false; for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false; return true; }
function withCrc(body) { return concat(body, u32(crc32(body))); }
function checkCrc(rec) {
  if (rec.length < 8) throw new FormatError('TOO_SHORT');
  const body = rec.subarray(0, rec.length - 4);
  if (crc32(body) !== rd32(rec, rec.length - 4)) throw new FormatError('BAD_CRC');
  return body;
}
export function hex(b) { return [...b].map(x => x.toString(16).padStart(2, '0')).join(''); }

// ---- Base45（RFC 9285）
const B45 = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:';
const B45_IDX = (() => { const m = new Map(); [...B45].forEach((c, i) => m.set(c, i)); return m; })();
export function base45Encode(bytes) {
  let s = '';
  for (let i = 0; i + 1 < bytes.length; i += 2) {
    let n = bytes[i] * 256 + bytes[i + 1];
    const c = n % 45; n = (n - c) / 45; const d = n % 45; const e = (n - d) / 45;
    s += B45[c] + B45[d] + B45[e];
  }
  if (bytes.length % 2) { const n = bytes[bytes.length - 1]; s += B45[n % 45] + B45[Math.floor(n / 45)]; }
  return s;
}
export function base45Decode(str) {
  if (str.length % 3 === 1) throw new FormatError('BAD_BASE45', 'length');
  const out = new Uint8Array(Math.floor(str.length / 3) * 2 + (str.length % 3 === 2 ? 1 : 0));
  let o = 0;
  for (let i = 0; i < str.length; i += 3) {
    const vals = [];
    for (let k = 0; k < 3 && i + k < str.length; k++) {
      const v = B45_IDX.get(str[i + k]);
      if (v === undefined) throw new FormatError('BAD_BASE45', 'char');
      vals.push(v);
    }
    if (vals.length === 3) {
      const n = vals[0] + vals[1] * 45 + vals[2] * 2025;
      if (n > 0xffff) throw new FormatError('BAD_BASE45', 'range');
      out[o++] = n >> 8; out[o++] = n & 0xff;
    } else {
      const n = vals[0] + vals[1] * 45;
      if (n > 0xff) throw new FormatError('BAD_BASE45', 'range');
      out[o++] = n;
    }
  }
  return out;
}
// QR 掃出來的字串 → 記錄位元組。Base45 的最後一個字元數學上不會是空白（三字一組的第三字 ≤ 32、
// 結尾兩字的第二字 ≤ 5），記錄又一律以 "PB" 開頭（第一字是 Q），所以掃描程式去頭尾空白不影響。
export function qrTextToBytes(text) { return base45Decode(String(text)); }

// ---- 標頭
export function packHeader({ flags, backupId, t, n, nonce, ctLen }) {
  if (backupId.length !== 8 || nonce.length !== 12) throw new FormatError('BAD_HEADER', 'sizes');
  return concat(MAGIC, new Uint8Array([MAJOR, MINOR, SUITE, flags & 0xff]), backupId, new Uint8Array([t, n]), nonce, u32(ctLen));
}
export function parseHeader(b, maxCt = MAX_CT_FILE) {
  if (b.length < HEADER_LEN) throw new FormatError('BAD_HEADER', 'short');
  if (!eq(b.subarray(0, 4), MAGIC)) throw new FormatError('BAD_HEADER', 'magic');
  const major = b[4], minor = b[5], suite = b[6], flags = b[7];
  if (major !== MAJOR) throw new FormatError('UNSUPPORTED_VERSION', major + '.' + minor);
  if (suite !== SUITE) throw new FormatError('UNSUPPORTED_SUITE', String(suite));
  if (flags & ~FLAG_DEFLATE) throw new FormatError('BAD_HEADER', 'flags');
  const t = b[16], n = b[17];
  if (!(t >= 2 && t <= n && n <= MAX_N)) throw new FormatError('BAD_HEADER', 't/n');
  const ctLen = rd32(b, 30);
  if (ctLen <= 16 || ctLen > maxCt) throw new FormatError('BAD_HEADER', 'ct_len');
  return { major, minor, suite, flags, backupId: b.slice(8, 16), t, n, nonce: b.slice(18, 30), ctLen, aad: b.slice(0, HEADER_LEN) };
}

// ---- 明文封包
const enc = new TextEncoder(), dec = new TextDecoder('utf-8', { fatal: true });
export async function sha256(bytes) { return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)); }
export async function packEnvelope({ kind, created, name, mime, content }) {
  const nm = enc.encode(name || ''), mm = enc.encode(mime || '');
  if (nm.length > 255) throw new FormatError('NAME_TOO_LONG');
  if (mm.length > 100) throw new FormatError('MIME_TOO_LONG');
  return concat(new Uint8Array([kind]), u32(created >>> 0), u16(nm.length), nm, new Uint8Array([mm.length]), mm,
                u32(content.length), content, await sha256(content));
}
export async function parseEnvelope(b, maxContent = MAX_CONTENT_FILE) {
  let o = 0;
  const need = n => { if (o + n > b.length) throw new FormatError('BAD_ENVELOPE', 'short'); };
  need(1 + 4 + 2); const kind = b[o]; o += 1; const created = rd32(b, o); o += 4;
  if (kind !== KIND_TEXT && kind !== KIND_FILE) throw new FormatError('BAD_ENVELOPE', 'kind');
  const nl = rd16(b, o); o += 2; if (nl > 255) throw new FormatError('BAD_ENVELOPE', 'name'); need(nl); const name = dec.decode(b.subarray(o, o + nl)); o += nl;
  need(1); const ml = b[o]; o += 1; if (ml > 100) throw new FormatError('BAD_ENVELOPE', 'mime'); need(ml); const mime = dec.decode(b.subarray(o, o + ml)); o += ml;
  need(4); const cl = rd32(b, o); o += 4; if (cl > maxContent) throw new FormatError('TOO_LARGE'); need(cl + 32);
  const content = b.slice(o, o + cl); o += cl;
  const digest = b.subarray(o, o + 32); o += 32;
  if (o !== b.length) throw new FormatError('BAD_ENVELOPE', 'trailing');
  if (!eq(digest, await sha256(content))) throw new FormatError('BAD_ENVELOPE', 'digest');
  return { kind, created, name, mime, content };
}

// ---- 主文件區塊記錄
export function packMainRecord(backupId, idx, total, data) {
  if (total < 1 || total > MAX_BLOCKS || idx >= total || data.length > MAX_BLOCK_DATA) throw new FormatError('BAD_BLOCK');
  return withCrc(concat(REC_MAGIC, new Uint8Array([1, TYPE_MAIN]), backupId, u16(idx), u16(total), data));
}
export function packShareRecord(backupId, t, n, shareNo, share) {
  if (share.length !== SHARE_LEN || !(t >= 2 && t <= n && n <= MAX_N) || shareNo < 1 || shareNo > n) throw new FormatError('BAD_SHARE');
  return withCrc(concat(REC_MAGIC, new Uint8Array([1, TYPE_SHARE]), backupId, new Uint8Array([t, n, shareNo]), share));
}
// 任何一筆（QR 或電子檔一行）解回結構；不合法一律丟 FormatError，不猜
export function parseRecord(rec) {
  if (rec.length > MAX_BLOCK_DATA + 64) throw new FormatError('TOO_LONG');
  const body = checkCrc(rec);
  if (body.length < 12 || !eq(body.subarray(0, 2), REC_MAGIC)) throw new FormatError('NOT_JTPB');
  if (body[2] !== 1) throw new FormatError('UNSUPPORTED_VERSION', String(body[2]));
  const type = body[3], backupId = body.slice(4, 12);
  if (type === TYPE_MAIN) {
    if (body.length < 16) throw new FormatError('BAD_BLOCK');
    const idx = rd16(body, 12), total = rd16(body, 14);
    if (total < 1 || total > MAX_BLOCKS || idx >= total) throw new FormatError('BAD_BLOCK');
    return { type, backupId, idx, total, data: body.slice(16) };
  }
  if (type === TYPE_SHARE) {
    if (rec.length !== SHARE_REC_LEN) throw new FormatError('BAD_SHARE', 'length');
    const t = body[12], n = body[13], shareNo = body[14];
    if (!(t >= 2 && t <= n && n <= MAX_N) || shareNo < 1 || shareNo > n) throw new FormatError('BAD_SHARE');
    const share = body.slice(15, 15 + SHARE_LEN);
    if (share[SHARE_LEN - 1] === 0) throw new FormatError('BAD_SHARE', 'x');
    return { type, backupId, t, n, shareNo, share };
  }
  throw new FormatError('UNKNOWN_TYPE', String(type));
}

// ---- 文字備援碼（分片）：Crockford Base32，16 組 × (5 字＋1 檢查字)
export const B32 = '0123456789ABCDEFGHJKMNPQRSTVWXYZ';
const B32_IDX = (() => { const m = new Map(); [...B32].forEach((c, i) => m.set(c, i)); m.set('O', 0); m.set('I', 1); m.set('L', 1); return m; })();
const W = [1, 3, 5, 7, 9];
export const CODE_GROUPS = 16;
function groupCheck(vals, g) { let s = 7 * g; for (let i = 0; i < 5; i++) s += W[i] * vals[i]; return s % 32; }
export function shareRecordToCode(rec) {
  if (rec.length !== SHARE_REC_LEN) throw new FormatError('BAD_SHARE');
  const body = rec.subarray(3);                            // 去掉 magic 與 ver：49 bytes = 392 bits → 補到 400
  let bits = '';
  for (const b of body) bits += b.toString(2).padStart(8, '0');
  bits = bits.padEnd(400, '0');
  const groups = [];
  for (let g = 0; g < CODE_GROUPS; g++) {
    const vals = [];
    for (let i = 0; i < 5; i++) vals.push(parseInt(bits.substr((g * 5 + i) * 5, 5), 2));
    groups.push(vals.map(v => B32[v]).join('') + B32[groupCheck(vals, g)]);
  }
  return groups;                                           // 16 個 6 字元字串
}
// 正規化一組輸入：大寫、去空白與 -、別名 O→0 I/L→1；回 { vals, ok }，不合法字元回 null
export function normalizeGroup(s) {
  const t = String(s).toUpperCase().replace(/[\s-]/g, '');
  const vals = [];
  for (const c of t) { const v = B32_IDX.get(c); if (v === undefined) return null; vals.push(v); }
  return vals;
}
export function checkGroup(s, g) {
  const v = normalizeGroup(s);
  if (!v || v.length !== 6) return false;
  return groupCheck(v.slice(0, 5), g) === v[5];
}
export function codeToShareRecord(groups) {
  if (!Array.isArray(groups) || groups.length !== CODE_GROUPS) throw new FormatError('BAD_CODE', 'groups');
  let bits = '';
  groups.forEach((s, g) => {
    const v = normalizeGroup(s);
    if (!v || v.length !== 6) throw new FormatError('BAD_CODE', String(g + 1));
    if (groupCheck(v.slice(0, 5), g) !== v[5]) throw new FormatError('BAD_CODE_CHECK', String(g + 1));
    for (let i = 0; i < 5; i++) bits += v[i].toString(2).padStart(5, '0');
  });
  if (/1/.test(bits.slice(392))) throw new FormatError('BAD_CODE', 'padding');
  const body = new Uint8Array(49);
  for (let i = 0; i < 49; i++) body[i] = parseInt(bits.substr(i * 8, 8), 2);
  return concat(REC_MAGIC, new Uint8Array([1]), body);
}
// 把一整段貼上的文字切成 16 組（容忍空白、換行、-）
export function splitCodeText(text) {
  const t = String(text).toUpperCase().replace(/[^0-9A-Z]/g, '');
  if (t.length !== CODE_GROUPS * 6) return null;
  const out = []; for (let g = 0; g < CODE_GROUPS; g++) out.push(t.slice(g * 6, g * 6 + 6));
  return out;
}

// 備份 ID 給人看的樣子：Crockford Base32，13 字分成 4-4-5
export function backupIdText(id) {
  let bits = ''; for (const b of id) bits += b.toString(2).padStart(8, '0');
  bits = bits.padEnd(65, '0');
  let s = ''; for (let i = 0; i < 13; i++) s += B32[parseInt(bits.substr(i * 5, 5), 2)];
  return s.slice(0, 4) + '-' + s.slice(4, 8) + '-' + s.slice(8);
}

// ---- 電子模式（.jtpb）
export function mainFileText(backupId, records) {
  return 'JTPB1 main ' + backupIdText(backupId) + '\n' + records.map(r => base45Encode(r)).join('\n') + '\n';
}
export function parseMainFileText(text) {
  const lines = String(text).split(/\r?\n/);
  if (!/^JTPB1 main /.test(lines[0] || '')) throw new FormatError('NOT_JTPB');
  if (lines.length > MAX_BLOCKS + 3) throw new FormatError('TOO_MANY_BLOCKS');
  return lines.slice(1).filter(l => l.length).map(l => qrTextToBytes(l));
}

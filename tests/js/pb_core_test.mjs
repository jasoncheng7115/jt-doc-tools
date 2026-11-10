// 加密紙本備份核心模組的行為測試（由 tests/test_paper_backup_core.py 用 node 執行，印出 JSON）。
import * as C from '../../static/js/paper_backup/pb_codec.mjs';
import { createBackup, Collector, inflate, DENSITY } from '../../static/js/paper_backup/pb_core.mjs';
import * as TP from '../../static/js/paper_backup/pb_testpage.mjs';
import { webcrypto } from 'node:crypto';
if (!globalThis.crypto || !globalThis.crypto.subtle) globalThis.crypto = webcrypto;   // node 18 的 ESM 沒有全域 crypto（瀏覽器有）

const results = [];
async function t(name, fn) {
  try { await fn(); results.push({ name, ok: true }); }
  catch (e) { results.push({ name, ok: false, err: String(e && e.stack || e) }); }
}
function assert(c, m) { if (!c) throw new Error(m || 'assert'); }
async function throwsCode(fn, code) {
  try { await fn(); } catch (e) { if (e && e.code === code) return; throw new Error('expected ' + code + ' got ' + (e && (e.code || e.message))); }
  throw new Error('expected ' + code + ', nothing thrown');
}
const enc = new TextEncoder(), dec = new TextDecoder();
const bytes = s => enc.encode(s);
function recrc(rec) { const b = rec.slice(0, rec.length - 4); const c = C.crc32(b); return C.concat(b, new Uint8Array([c >>> 24, (c >>> 16) & 255, (c >>> 8) & 255, c & 255])); }

await t('base45 RFC 9285 vectors', () => {
  for (const [a, b] of [['AB', 'BB8'], ['Hello!!', '%69 VD92EX0'], ['base-45', 'UJCLQE7W581'], ['ietf!', 'QED8WEX0']]) {
    assert(C.base45Encode(bytes(a)) === b, a); assert(dec.decode(C.base45Decode(b)) === a, b);
  }
});
await t('base45 rejects bad input', async () => {
  await throwsCode(() => C.base45Decode('GGW'), 'BAD_BASE45');
  await throwsCode(() => C.base45Decode('ab'), 'BAD_BASE45');
  await throwsCode(() => C.base45Decode('AAAA'), 'BAD_BASE45');
});
await t('base45 never ends in a space; records never start with one', () => {
  for (let v = 0; v < 65536; v++) { const s = C.base45Encode(new Uint8Array([v >> 8, v & 255])); assert(!s.endsWith(' '), 'pair ' + v); }
  for (let v = 0; v < 256; v++) { const s = C.base45Encode(new Uint8Array([7, 7, v])); assert(!s.endsWith(' '), 'tail ' + v); }
  assert(C.base45Encode(new Uint8Array([0x50, 0x42, 1]))[0] === 'Q');
});
await t('crc32 check value', () => assert(C.crc32(bytes('123456789')) === 0xcbf43926));

const SECRET = 'db password: correct horse battery staple 中文測試 🔐';
for (const density of Object.keys(DENSITY)) {
  await t('round trip text ' + density, async () => {
    const b = await createBackup({ kind: C.KIND_TEXT, name: '測試', content: bytes(SECRET), t: 2, n: 3, density });
    assert(b.shareRecords.length === 3);
    for (const pick of [[0, 1], [1, 2], [0, 2], [2, 0, 1]]) {
      const col = new Collector();
      for (const r of [...b.mainRecords].reverse()) col.add(r);
      pick.forEach(i => col.add(b.shareRecords[i]));
      assert(col.ready());
      const env = await col.restore();
      assert(dec.decode(env.content) === SECRET && env.name === '測試' && env.kind === C.KIND_TEXT, 'content');
    }
    for (const r of b.mainRecords) assert(r.length <= DENSITY[density].data + 20, 'block too big for QR');
  });
}
await t('one share is not enough', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 3, n: 5 });
  const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); col.add(b.shareRecords[4]); col.add(b.shareRecords[1]);
  assert(!col.ready()); assert(col.progress().sharesHave === 2 && col.progress().t === 3);
  await throwsCode(() => col.restore(), 'NOT_READY');
  col.add(b.shareRecords[0]); assert(col.ready());
  assert(dec.decode((await col.restore()).content) === SECRET);
});
await t('progress lists missing blocks', async () => {
  const b = await createBackup({ kind: C.KIND_FILE, name: 'a.bin', content: crypto.getRandomValues(new Uint8Array(2000)), t: 2, n: 2, density: 'low' });
  const col = new Collector(); b.mainRecords.forEach((r, i) => { if (i !== 3 && i !== 7) col.add(r); });
  const p = col.progress(); assert(p.blocksTotal === b.mainRecords.length && p.blocksMissing.join(',') === '4,8', JSON.stringify(p));
});
await t('128 KiB random fits paper at medium, too many pages at low', async () => {
  const big = new Uint8Array(128 * 1024); for (let i = 0; i < big.length; i += 65536) crypto.getRandomValues(big.subarray(i, i + 65536));
  const b = await createBackup({ kind: C.KIND_FILE, name: 'x', content: big, t: 2, n: 3, density: 'medium' });
  assert(Math.ceil(b.mainRecords.length / 12) <= 50);
  await throwsCode(() => createBackup({ kind: C.KIND_FILE, content: big, t: 2, n: 3, density: 'low' }), 'TOO_MANY_PAGES');
  await throwsCode(() => createBackup({ kind: C.KIND_FILE, content: new Uint8Array(128 * 1024 + 1), t: 2, n: 3 }), 'TOO_LARGE');
});
await t('bad t/n rejected', async () => {
  for (const [tt, nn] of [[1, 3], [4, 3], [2, 17], [0, 0], [2.5, 3]]) await throwsCode(() => createBackup({ kind: 1, content: bytes('x'), t: tt, n: nn }), 'BAD_TN');
});
await t('tampered block with fixed crc fails authentication', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 2, n: 3 });
  const last = b.mainRecords.length - 1;
  const bad = b.mainRecords[last].slice(); bad[bad.length - 6] ^= 1;
  const col = new Collector(); b.mainRecords.forEach((r, i) => col.add(i === last ? recrc(bad) : r)); col.add(b.shareRecords[0]); col.add(b.shareRecords[1]);
  await throwsCode(() => col.restore(), 'DECRYPT_FAILED');
});
await t('tampered share with fixed crc fails authentication', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 2, n: 3 });
  const bad = b.shareRecords[0].slice(); bad[20] ^= 0x40;
  const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); col.add(recrc(bad)); col.add(b.shareRecords[1]);
  await throwsCode(() => col.restore(), 'DECRYPT_FAILED');
});
await t('header t changed is caught', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 2, n: 3 });
  const r0 = b.mainRecords[0].slice(); r0[16 + 16] = 3;   // 記錄開頭 16 bytes 之後是標頭；T 在標頭第 16 byte
  const col = new Collector(); b.mainRecords.forEach((r, i) => col.add(i === 0 ? recrc(r0) : r)); col.add(b.shareRecords[0]); col.add(b.shareRecords[1]);
  await throwsCode(() => col.restore(), 'CONFLICT');
});
await t('crc catches corruption', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes('x'), t: 2, n: 2 });
  const bad = b.mainRecords[0].slice(); bad[18] ^= 4;
  await throwsCode(() => C.parseRecord(bad), 'BAD_CRC');
});
await t('collector: other set, duplicates, conflicts', async () => {
  const a = await createBackup({ kind: 1, content: bytes('a'), t: 2, n: 3 });
  const b = await createBackup({ kind: 1, content: bytes('b'), t: 2, n: 3 });
  const col = new Collector(); col.add(a.mainRecords[0]);
  assert(col.add(b.mainRecords[0]).status === 'other_set');
  assert(col.add(a.mainRecords[0]).status === 'dup');
  col.add(a.shareRecords[0]); assert(col.add(a.shareRecords[0]).status === 'dup');
  const forged = C.packShareRecord(a.backupId, 2, 3, 1, b.shareRecords[1].slice(15, 48));
  await throwsCode(() => col.add(forged), 'CONFLICT');
  const otherTn = C.packShareRecord(a.backupId, 3, 3, 2, a.shareRecords[1].slice(15, 48));
  await throwsCode(() => col.add(otherTn), 'CONFLICT');
});
await t('backup code round trip, aliases and typo detection', async () => {
  const b = await createBackup({ kind: 1, content: bytes('a'), t: 2, n: 3 });
  const rec = b.shareRecords[2];
  const groups = C.shareRecordToCode(rec);
  assert(groups.length === 16 && groups.every(g => g.length === 6));
  assert(C.hex(C.codeToShareRecord(groups)) === C.hex(rec));
  const loose = groups.map(g => g.toLowerCase().replace(/0/g, 'o').replace(/1/g, 'l').split('').join(' '));
  assert(C.hex(C.codeToShareRecord(loose)) === C.hex(rec), 'aliases');
  const pasted = C.splitCodeText(groups.join(' - ') + '\n');
  assert(C.hex(C.codeToShareRecord(pasted)) === C.hex(rec), 'paste');
  // 任何一個字元打錯（換成任何其他字元）都要被那一組的檢查字抓到
  for (let g = 0; g < 16; g++) for (let i = 0; i < 6; i++) for (const ch of C.B32) {
    if (ch === groups[g][i]) continue;
    const s = groups[g].slice(0, i) + ch + groups[g].slice(i + 1);
    assert(!C.checkGroup(s, g), 'typo not caught g' + g + ' i' + i + ' ' + ch);
  }
  const sw = groups.slice(); [sw[3], sw[4]] = [sw[4], sw[3]];
  await throwsCode(() => C.codeToShareRecord(sw), 'BAD_CODE_CHECK');
  await throwsCode(() => C.codeToShareRecord(groups.slice(0, 15)), 'BAD_CODE');
  const u = groups.slice(); u[0] = 'U' + u[0].slice(1);
  await throwsCode(() => C.codeToShareRecord(u), 'BAD_CODE');
});
await t('restore from typed codes', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 2, n: 3 });
  const col = new Collector(); b.mainRecords.forEach(r => col.add(r));
  col.add(C.codeToShareRecord(C.shareRecordToCode(b.shareRecords[0])));
  col.add(C.codeToShareRecord(C.shareRecordToCode(b.shareRecords[2])));
  assert(dec.decode((await col.restore()).content) === SECRET);
});
await t('electronic main file round trip', async () => {
  const b = await createBackup({ kind: C.KIND_FILE, name: 'f.bin', content: crypto.getRandomValues(new Uint8Array(5000)), t: 2, n: 2, mode: 'file' });
  const text = C.mainFileText(b.backupId, b.mainRecords);
  assert(text.startsWith('JTPB1 main ' + C.backupIdText(b.backupId)));
  const recs = C.parseMainFileText(text);
  const col = new Collector(); recs.forEach(r => col.add(r)); b.shareRecords.forEach(r => col.add(r));
  const env = await col.restore(); assert(env.name === 'f.bin' && env.content.length === 5000);
});
await t('qr text of every record decodes', async () => {
  const b = await createBackup({ kind: 1, content: bytes(SECRET), t: 2, n: 3, density: 'high' });
  for (const r of [...b.mainRecords, ...b.shareRecords]) assert(C.hex(C.qrTextToBytes(C.base45Encode(r))) === C.hex(r));
});
await t('header with unknown flag bit is rejected', async () => {
  const h = C.packHeader({ flags: 2, backupId: new Uint8Array(8), t: 2, n: 3, nonce: new Uint8Array(12), ctLen: 40 });
  await throwsCode(() => C.parseHeader(h), 'BAD_HEADER');
  const ok = C.packHeader({ flags: 1, backupId: new Uint8Array(8), t: 2, n: 3, nonce: new Uint8Array(12), ctLen: 40 });
  assert(C.parseHeader(ok).t === 2);
  const v2 = ok.slice(); v2[4] = 2; await throwsCode(() => C.parseHeader(v2), 'UNSUPPORTED_VERSION');
});
await t('envelope digest mismatch is rejected', async () => {
  const e = await C.packEnvelope({ kind: 1, created: 1, name: 'n', mime: '', content: bytes('abc') });
  const bad = e.slice(); bad[bad.length - 35] ^= 1;     // 改內容最後一個字元
  await throwsCode(() => C.parseEnvelope(bad), 'BAD_ENVELOPE');
  assert(dec.decode((await C.parseEnvelope(e)).content) === 'abc');
});
await t('decompression is bounded', async () => {
  const zeros = new Uint8Array(4 * 1024 * 1024);
  const reader = new Blob([zeros]).stream().pipeThrough(new CompressionStream('deflate')).getReader();
  const parts = []; for (;;) { const { done, value } = await reader.read(); if (done) break; parts.push(value); }
  const bomb = C.concat(...parts);
  assert(bomb.length < 10000);
  await throwsCode(() => inflate(bomb, 1024 * 1024), 'TOO_LARGE');
  assert((await inflate(bomb, 8 * 1024 * 1024)).length === zeros.length);
});
await t('compression flag follows size', async () => {
  const a = await createBackup({ kind: 1, content: bytes('a'.repeat(5000)), t: 2, n: 2 });
  assert(a.compressed);
  const r = await createBackup({ kind: 2, content: crypto.getRandomValues(new Uint8Array(3000)), t: 2, n: 2 });
  assert(!r.compressed);
});

// ---- 驗收清單補強（05-acceptance：C03、C06、C08、C10）
function combos(arr, k) { const out = []; const rec = (st, cur) => { if (cur.length === k) { out.push(cur.slice()); return; } for (let i = st; i < arr.length; i++) { cur.push(arr[i]); rec(i + 1, cur); cur.pop(); } }; rec(0, []); return out; }
await t('C10: 3 of 5, every combination restores and every pair does not', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 3, n: 5 });
  const idx = [0, 1, 2, 3, 4];
  for (const pick of combos(idx, 3)) {
    const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); pick.forEach(i => col.add(b.shareRecords[i]));
    assert(dec.decode((await col.restore()).content) === SECRET, 'combo ' + pick);
  }
  for (const pick of combos(idx, 2)) {
    const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); pick.forEach(i => col.add(b.shareRecords[i]));
    assert(!col.ready(), 'pair ' + pick);
  }
  for (const pick of combos(idx, 4).concat(combos(idx, 5))) {
    const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); pick.forEach(i => col.add(b.shareRecords[i]));
    assert(dec.decode((await col.restore()).content) === SECRET, 'more ' + pick);
  }
});
await t('C10: larger setting sampled (5 of 16)', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 5, n: 16 });
  for (let k = 0; k < 12; k++) {
    const pick = [...Array(16).keys()].sort(() => crypto.getRandomValues(new Uint8Array(1))[0] - 128).slice(0, 5);
    const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); pick.forEach(i => col.add(b.shareRecords[i]));
    assert(dec.decode((await col.restore()).content) === SECRET, 'sample ' + pick);
  }
});
async function tamperHeaderByte(off, xor) {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 2, n: 2 });
  const r0 = b.mainRecords[0].slice(); r0[16 + off] ^= xor;          // 記錄前 16 bytes 之後是標頭
  const col = new Collector(); b.mainRecords.forEach((r, i) => col.add(i === 0 ? recrc(r0) : r)); b.shareRecords.forEach(r => col.add(r));
  return col;
}
await t('C03: tampered nonce / minor version / tag / ciphertext never yield plaintext', async () => {
  await throwsCode(async () => (await tamperHeaderByte(18, 1)).restore(), 'DECRYPT_FAILED');   // nonce
  await throwsCode(async () => (await tamperHeaderByte(5, 1)).restore(), 'DECRYPT_FAILED');    // minor（AAD 綁住）
  await throwsCode(async () => (await tamperHeaderByte(8, 1)).restore(), 'CONFLICT');          // 標頭的備份 ID
  await throwsCode(async () => (await tamperHeaderByte(30, 0x80)).restore(), 'BAD_HEADER');    // ct_len 造假
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 2, n: 2 });
  const last = b.mainRecords.length - 1;
  for (const back of [5, 6, 20]) {                                    // tag 最後一個位元組、tag 中段、密文
    const bad = b.mainRecords[last].slice(); bad[bad.length - back] ^= 0x10;
    const col = new Collector(); b.mainRecords.forEach((r, i) => col.add(i === last ? recrc(bad) : r)); b.shareRecords.forEach(r => col.add(r));
    await throwsCode(() => col.restore(), 'DECRYPT_FAILED');
  }
});
await t('C06: every backup gets a fresh id, nonce and key', async () => {
  const seen = new Set();
  for (let i = 0; i < 20; i++) {
    const b = await createBackup({ kind: C.KIND_TEXT, content: bytes('same content'), t: 2, n: 2 });
    const main = C.concat(...b.mainRecords.map(r => C.parseRecord(r).data));
    const h = C.parseHeader(main);
    for (const v of [C.hex(h.backupId), C.hex(h.nonce), C.hex(C.parseRecord(b.shareRecords[0]).share)]) { assert(!seen.has(v), 'reused ' + v); seen.add(v); }
  }
});
await t('C08: malformed records are rejected with bounded work', async () => {
  const id = new Uint8Array(8);
  await throwsCode(() => C.packMainRecord(id, 0, 2001, new Uint8Array(4)), 'BAD_BLOCK');
  await throwsCode(() => C.packMainRecord(id, 5, 5, new Uint8Array(4)), 'BAD_BLOCK');
  await throwsCode(() => C.packMainRecord(id, 0, 1, new Uint8Array(4096)), 'BAD_BLOCK');
  // 手工做一筆 total = 65535 的記錄（CRC 正確）→ 解析時就拒絕，不會去配置 65535 格
  const body = C.concat(new Uint8Array([0x50, 0x42, 1, 1]), id, new Uint8Array([0, 0, 0xff, 0xff]), new Uint8Array(10));
  await throwsCode(() => C.parseRecord(recrc(C.concat(body, new Uint8Array(4)))), 'BAD_BLOCK');
  // 分片長度不對、x = 0、T > N、未知類型、未知版本、過長
  const share = C.packShareRecord(id, 2, 3, 1, new Uint8Array(33).fill(7));
  await throwsCode(() => C.parseRecord(recrc(C.concat(share.slice(0, 40), new Uint8Array(4)))), 'BAD_SHARE');
  const x0 = share.slice(); x0[47] = 0; await throwsCode(() => C.parseRecord(recrc(x0)), 'BAD_SHARE');
  const tn = share.slice(); tn[12] = 4; await throwsCode(() => C.parseRecord(recrc(tn)), 'BAD_SHARE');
  const ty = share.slice(); ty[3] = 9; await throwsCode(() => C.parseRecord(recrc(ty)), 'UNKNOWN_TYPE');
  const vv = share.slice(); vv[2] = 2; await throwsCode(() => C.parseRecord(recrc(vv)), 'UNSUPPORTED_VERSION');
  await throwsCode(() => C.parseRecord(new Uint8Array(5000)), 'TOO_LONG');
  await throwsCode(() => C.parseMainFileText('JTPB1 main X\n' + 'AA\n'.repeat(2010)), 'TOO_MANY_BLOCKS');
});
await t('C09: more than T shares — only the first T are used, no combination guessing', async () => {
  const b = await createBackup({ kind: C.KIND_TEXT, content: bytes(SECRET), t: 2, n: 4 });
  // 第 4 份動手腳（重算 CRC，像抄錯）；前 2 份是好的 → 用前 T 份（依編號）照樣解得開
  const bad = b.shareRecords[3].slice(); bad[20] ^= 1;
  const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); [0, 1].forEach(i => col.add(b.shareRecords[i])); col.add(recrc(bad));
  assert(dec.decode((await col.restore()).content) === SECRET);
  // 壞的排在前面 → 直接失敗（不自動換組合去「猜」哪份壞）
  const col2 = new Collector(); b.mainRecords.forEach(r => col2.add(r)); col2.add(recrc((() => { const x = b.shareRecords[0].slice(); x[20] ^= 1; return x; })())); [1, 2].forEach(i => col2.add(b.shareRecords[i]));
  await throwsCode(() => col2.restore(), 'DECRYPT_FAILED');
});
await t('F01/F02: multi-line Unicode text and a file full of zero bytes round-trip exactly', async () => {
  const text = '第一行\r\n第二行\t含 Tab\n\u0000 null 字元 · 日本語 · 한국어 · 🔑\n  前後空白  ';
  const zeros = new Uint8Array(3000);
  for (const [kind, content] of [[C.KIND_TEXT, bytes(text)], [C.KIND_FILE, zeros]]) {
    const b = await createBackup({ kind, name: 'z', content, t: 2, n: 2 });
    const col = new Collector(); b.mainRecords.forEach(r => col.add(r)); b.shareRecords.forEach(r => col.add(r));
    const env = await col.restore();
    assert(C.hex(env.content) === C.hex(content), 'kind ' + kind);
  }
});

await t('C05: if the random generator fails, creation fails (no fallback)', async () => {
  const real = crypto.getRandomValues.bind(crypto);
  crypto.getRandomValues = () => { throw new Error('rng down'); };
  try {
    let threw = false;
    try { await createBackup({ kind: C.KIND_TEXT, content: bytes('x'), t: 2, n: 2 }); } catch (_e) { threw = true; }
    assert(threw, 'must not fall back to another randomness source');
  } finally { crypto.getRandomValues = real; }
});

await t('P03: print test page records and checker', async () => {
  const recs = TP.testRecords();
  assert(recs.length === 9 && new Set(recs.map(r => C.base45Encode(r.rec))).size === 9);
  for (const r of recs) assert(r.rec.length === DENSITY[r.density].data + 20, 'fills ' + r.density);   // 填滿該密度的容量
  assert(recs.every(r => TP.isTestRecord(r.rec)));
  const real = await createBackup({ kind: 1, content: bytes('x'), t: 2, n: 2 });
  assert(!TP.isTestRecord(real.mainRecords[0]) && !TP.isTestRecord(real.shareRecords[0]));
  const ch = new TP.TestChecker();
  recs.filter(r => r.idx !== 4 && r.idx !== 8).forEach(r => assert(ch.add(r.rec) === 'ok'));
  assert(ch.add(recs[0].rec) === 'dup');
  const s = ch.summary();
  assert(s[0].ok === 3 && s[1].ok === 2 && s[1].missing.join() === '2' && s[2].missing.join() === '3' && !ch.allOk());
  // 內容不符（CRC 對但資料不同）→ 標成內容不符，不算讀到
  const fake = C.packMainRecord(TP.TEST_ID, 4, 9, new Uint8Array(DENSITY.medium.data));
  assert(ch.add(fake) === 'wrong' && ch.summary()[1].wrong.join() === '2');
  assert(ch.add(recs[4].rec) === 'ok' && ch.add(recs[8].rec) === 'ok');
  assert(!ch.allOk(), '還原驗證還沒做，不能算全部通過');
  // 還原驗證：示範主文件＋一份分片 → 還不能解；兩份 → 真的解出預期的文字
  ch.add(C.qrTextToBytes(TP.DEMO.main[0])); ch.add(C.qrTextToBytes(TP.DEMO.shares[0]));
  assert(!ch.demoProgress().ready && (await ch.verifyDemo()) === null);
  ch.add(C.qrTextToBytes(TP.DEMO.shares[1]));
  assert((await ch.verifyDemo()) === 'ok' && ch.allOk());
  // 示範主文件被動過（CRC 重算）→ 還原驗證失敗，不能算通過
  const ch2 = new TP.TestChecker();
  const badMain = C.qrTextToBytes(TP.DEMO.main[0]).slice(); badMain[100] ^= 1;
  ch2.add(recrc(badMain)); TP.DEMO.shares.forEach(s => ch2.add(C.qrTextToBytes(s)));
  assert((await ch2.verifyDemo()) === 'fail');
  // 示範備份不會被當成正式備份
  assert(TP.isTestRecord(C.qrTextToBytes(TP.DEMO.shares[0])));
});

console.log(JSON.stringify(results));

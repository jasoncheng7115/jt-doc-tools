// 加密紙本備份：列印測試頁的固定資料與掃描比對（規格「列印與保存指引」第 7 節）。
// 測試頁用跟正式備份完全一樣的記錄格式與 QR 編碼，只是備份 ID 固定為 "JTPBTEST"、內容是公開的固定位元組 ——
// 不含任何秘密；掃回來時逐筆比對內容，不只看「有沒有讀到 QR」。
import * as C from './pb_codec.mjs';
import { DENSITY, Collector } from './pb_core.mjs';

export const TEST_ID = new TextEncoder().encode('JTPBTEST');     // 8 bytes
export const DEMO_ID = new TextEncoder().encode('JTPBDEMO');

// 還原驗證用的公開示範備份（固定、不是秘密）：主文件 1 塊（中密度）＋分片 2 份、門檻 2。
// 掃描測試頁時真的跑一次「重組金鑰 → 解密 → 比對內容」，驗證整條還原流程。格式上線後不可重產覆蓋。
export const DEMO = {
  content: "加密紙本備份還原驗證：看得到這段文字，代表列印、掃描、重組金鑰、解密整條流程都正常。",
  main: ["Q6AW50%H9Q6AHS8 Y9000100%H9Q6AV50V50%H9Q6AHS8 Y9JB0+2FUAAO3PIJTS42B16000+40R4OH0DYHBRJ9Y2W-46PTF3TC7ZQA%U$0O77O8V9:5KKD5J/K010W4J7%J.OSFLCT+BV/VLYRN1H+79PWNOWK I4%QUZU9VIG9+5OSG3GABS4YD6XCPG 8C:A5*7P7T2XG7WSSVP-0UW4G4$D6%8+M4V8AM13QVIT K-8JHXG0N2:OEPE7+HFY+CFN6C5IFU3GEN3$8K+7EPRDD4E.O8.AX7033KQMJ5V492TU*C 9W5AT6+5K$VULC- 4E-SUP2C:VOB9S6P2LT-:6H$1LULXRP2KG2$6L%9U.F31N:J3BNFBU49F0PES$*SA.6-C84JQ9*HV7SI4W5-RVG7"],
  shares: ["Q6AX50%H9Q6AHS8 Y9JB0HA02E70BQTF5%63$F4$GU327WDHL64S0FW0V$05WQC5/VS-SM1BC:TMJ4", "Q6AX50%H9Q6AHS8 Y9JB0MF0GVQ4BP3KK2.NQKP:R8X46B.C.CNL.A /II365DRC:6%TVLRN0.A$Q5"],
};
export const PER_DENSITY = 3;
export const DENSITIES = ['low', 'medium', 'high'];
const TOTAL = PER_DENSITY * DENSITIES.length;

// 每一個測試 QR：{ density, n（第幾個，1 起）, idx, rec }。資料填滿該密度一塊的容量，這樣才測得到最密的情況。
export function testRecords() {
  const out = [];
  DENSITIES.forEach((d, di) => {
    for (let k = 0; k < PER_DENSITY; k++) {
      const idx = di * PER_DENSITY + k;
      const data = new Uint8Array(DENSITY[d].data);
      for (let i = 0; i < data.length; i++) data[i] = (idx * 31 + i * 7 + 13) & 0xff;
      out.push({ density: d, n: k + 1, idx, rec: C.packMainRecord(TEST_ID, idx, TOTAL, data) });
    }
  });
  return out;
}

function idIs(recBytes, id) {
  if (recBytes.length < 12) return false;
  for (let i = 0; i < 8; i++) if (recBytes[4 + i] !== id[i]) return false;
  return true;
}
export function isTestRecord(recBytes) { return idIs(recBytes, TEST_ID) || idIs(recBytes, DEMO_ID); }

// 掃描比對：每讀到一筆就 add()；summary() 告訴你每種密度讀到幾個、缺哪幾個、有沒有讀錯的
export class TestChecker {
  constructor() {
    this.expected = new Map(testRecords().map(t => [t.idx, t]));
    this.ok = new Set(); this.wrong = new Set();
    this.demo = new Collector(); this.demoResult = null;   // null＝還沒收齊；'ok' / 'fail'
  }
  add(recBytes) {
    if (idIs(recBytes, DEMO_ID)) {
      try { const st = this.demo.add(recBytes).status; if (st === 'new') this.demoResult = null; return st === 'new' ? 'ok' : 'dup'; }
      catch (_e) { return 'wrong'; }
    }
    let r;
    try { r = C.parseRecord(recBytes); } catch (_e) { return 'wrong'; }
    const want = this.expected.get(r.idx);
    if (!want || r.total !== TOTAL) return 'wrong';
    if (C.hex(want.rec) !== C.hex(recBytes)) { this.wrong.add(r.idx); return 'wrong'; }
    if (this.ok.has(r.idx)) return 'dup';
    this.ok.add(r.idx); this.wrong.delete(r.idx);
    return 'ok';
  }
  seen() { return this.ok.size + this.wrong.size > 0 || this.demo.progress().backupId !== null; }
  // 還原驗證：示範備份的主文件與兩份分片都收到時，真的解一次並比對內容
  demoProgress() { const p = this.demo.progress(); return { blocks: p.blocksHave, shares: p.sharesHave, ready: this.demo.ready() }; }
  async verifyDemo() {
    if (!this.demo.ready() || this.demoResult) return this.demoResult;
    try {
      const env = await this.demo.restore();
      this.demoResult = new TextDecoder().decode(env.content) === DEMO.content ? 'ok' : 'fail';
    } catch (_e) { this.demoResult = 'fail'; }
    return this.demoResult;
  }
  summary() {
    return DENSITIES.map((d, di) => {
      const idxs = [...Array(PER_DENSITY).keys()].map(k => di * PER_DENSITY + k);
      return {
        density: d, total: PER_DENSITY,
        ok: idxs.filter(i => this.ok.has(i)).length,
        missing: idxs.filter(i => !this.ok.has(i) && !this.wrong.has(i)).map(i => i - di * PER_DENSITY + 1),
        wrong: idxs.filter(i => this.wrong.has(i)).map(i => i - di * PER_DENSITY + 1),
      };
    });
  }
  allOk() { return this.ok.size === TOTAL && this.demoResult === 'ok'; }
}

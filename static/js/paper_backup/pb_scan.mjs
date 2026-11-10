// 加密紙本備份：從一張影像（掃描頁、照片、PDF 畫出來的頁面）找出所有 QR。
// jsQR 一次只認一個，而且同一張圖有很多個 QR 時會把不同 QR 的定位圖案配在一起、一個都認不出來。
// 做法：①整張試一次（只有一個 QR 的照片）；②沿著整條空白的行列把圖切開（XY-cut，QR 之間有白邊），
// 每一塊單獨試；③還有墨跡的地方再用約一個 QR 大小的視窗細步滑過（歪斜的照片）。
// 認到的就在原圖塗白，後面不會重複認，全白的地方直接略過。
// jsQR 由呼叫端傳入（瀏覽器是內建的 /static/vendor/jsqr/jsQR.js，離線包內嵌同一份）。

function blank(rgba, w, x0, y0, x1, y1) {
  for (let y = y0; y <= y1; y++) {
    let o = (y * w + x0) * 4;
    for (let x = x0; x <= x1; x++, o += 4) { rgba[o] = rgba[o + 1] = rgba[o + 2] = 255; }
  }
}
function crop(rgba, w, x0, y0, cw, ch) {
  const out = new Uint8ClampedArray(cw * ch * 4);
  for (let y = 0; y < ch; y++) out.set(rgba.subarray(((y0 + y) * w + x0) * 4, ((y0 + y) * w + x0 + cw) * 4), y * cw * 4);
  return out;
}
// 視窗裡深色像素太少（空白、已經塗白）就不用試
function hasInk(rgba, w, x0, y0, cw, ch) {
  let dark = 0, n = 0;
  for (let y = y0; y < y0 + ch; y += 4) {
    let o = (y * w + x0) * 4;
    for (let x = 0; x < cw; x += 4, o += 16) { n++; if (rgba[o] < 128) dark++; }
  }
  return n && dark / n > 0.08;
}

// XY-cut：在 [x0,x1)×[y0,y1) 找整條幾乎沒有墨跡的欄 / 列，切成幾塊、遞迴；回傳葉子區塊
function inkRuns(rgba, w, x0, y0, x1, y1, vertical) {
  // vertical：逐欄算（找直的空白帶）；否則逐列
  const len = vertical ? x1 - x0 : y1 - y0;
  const span = vertical ? y1 - y0 : x1 - x0;
  const thr = Math.max(1, Math.floor(span * 0.004));
  const ink = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    let d = 0;
    if (vertical) { const x = x0 + i; for (let y = y0; y < y1; y += 1) if (rgba[(y * w + x) * 4] < 128 && ++d > thr) break; }
    else { const y = y0 + i; let o = (y * w + x0) * 4; for (let x = x0; x < x1; x++, o += 4) if (rgba[o] < 128 && ++d > thr) break; }
    ink[i] = d > thr ? 1 : 0;
  }
  const runs = []; let start = -1;
  for (let i = 0; i <= len; i++) {
    if (i < len && ink[i]) { if (start < 0) start = i; }
    else if (start >= 0) { runs.push([start, i]); start = -1; }
  }
  return runs;
}
function xyCut(rgba, w, x0, y0, x1, y1, minGap, depth, out) {
  if (depth > 6 || x1 - x0 < 16 || y1 - y0 < 16) { out.push([x0, y0, x1, y1]); return; }
  for (const vertical of [true, false]) {
    const runs = inkRuns(rgba, w, x0, y0, x1, y1, vertical);
    if (!runs.length) return;                                       // 全白
    // 相鄰兩段之間的空白不夠寬就合併（字與字之間的小空隙不算）
    const merged = [runs[0].slice()];
    for (const r of runs.slice(1)) { const last = merged[merged.length - 1]; if (r[0] - last[1] < minGap) last[1] = r[1]; else merged.push(r.slice()); }
    if (merged.length > 1) {
      for (const [a, b] of merged) {
        if (vertical) xyCut(rgba, w, x0 + a, y0, x0 + b, y1, minGap, depth + 1, out);
        else xyCut(rgba, w, x0, y0 + a, x1, y0 + b, minGap, depth + 1, out);
      }
      return;
    }
    // 只剩一段：把外圍空白修掉再試另一個方向
    if (vertical) { x1 = x0 + merged[0][1]; x0 = x0 + merged[0][0]; } else { y1 = y0 + merged[0][1]; y0 = y0 + merged[0][0]; }
  }
  out.push([x0, y0, x1, y1]);
}

// rgba：Uint8ClampedArray（寬 × 高 × 4），會被改寫（塗白）—— 呼叫端要保留原圖就先複製
export function scanAll(rgba, w, h, jsQR, { max = 16, scales = [0.32, 0.45, 0.65] } = {}) {
  const found = [];
  const take = (r, ox, oy) => {
    if (!r.data) return;                                            // jsQR 偶爾回空字串（認錯），不算
    const L = r.location;
    const pts = [L.topLeftCorner, L.topRightCorner, L.bottomRightCorner, L.bottomLeftCorner].map(p => ({ x: p.x + ox, y: p.y + oy }));
    found.push({ text: r.data, bytes: r.binaryData, corners: pts });
    const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
    const side = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
    const pad = Math.max(4, side * 0.1);
    blank(rgba, w, Math.max(0, Math.floor(Math.min(...xs) - pad)), Math.max(0, Math.floor(Math.min(...ys) - pad)),
          Math.min(w - 1, Math.ceil(Math.max(...xs) + pad)), Math.min(h - 1, Math.ceil(Math.max(...ys) + pad)));
  };
  const opts = { inversionAttempts: 'dontInvert' };
  const whole = jsQR(rgba, w, h, opts);
  if (whole && whole.location) take(whole, 0, 0);
  // XY-cut：空白帶至少約影像短邊的 0.6%（印出來的 QR 之間有 4 模組以上的白邊）
  const leaves = [];
  xyCut(rgba, w, 0, 0, w, h, Math.max(3, Math.round(Math.min(w, h) * 0.006)), 0, leaves);
  for (const [x0, y0, x1, y1] of leaves) {
    if (found.length >= max) break;
    const cw = x1 - x0, ch = y1 - y0;
    if (cw < 21 || ch < 21 || cw > 3 * ch || ch > 3 * cw) continue;  // 太小或太扁（文字行）不是 QR
    const pad = Math.round(Math.max(cw, ch) * 0.08);
    const ax = Math.max(0, x0 - pad), ay = Math.max(0, y0 - pad);
    const aw = Math.min(w, x1 + pad) - ax, ah = Math.min(h, y1 + pad) - ay;
    if (!hasInk(rgba, w, ax, ay, aw, ah)) continue;
    const r = jsQR(crop(rgba, w, ax, ay, aw, ah), aw, ah, opts);
    if (r && r.location) take(r, ax, ay);
  }
  for (const s of scales) {
    if (found.length >= max) break;
    const win = Math.min(w, h, Math.round(s * Math.min(w, h)));
    const step = Math.max(8, Math.round(win / 6));
    for (let y0 = 0; y0 + win <= h + step - 1 && found.length < max; y0 += step) {
      const y = Math.min(y0, h - win);
      for (let x0 = 0; x0 + win <= w + step - 1 && found.length < max; x0 += step) {
        const x = Math.min(x0, w - win);
        if (!hasInk(rgba, w, x, y, win, win)) continue;
        const r = jsQR(crop(rgba, w, x, y, win, win), win, win, opts);
        if (r && r.location) take(r, x, y);
      }
    }
  }
  return found;
}

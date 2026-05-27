// friendlyServerError — 把 fetch Response 轉成 user-friendly 中文錯誤訊息
//   - JSON {detail / error / message}: 直接取
//   - FastAPI validation array: 取第一筆 msg
//   - 純文字短回應: 直接用
//   - HTML / stacktrace: 隱藏細節寫進 console,訊息列只顯示狀態碼
//
// 用法:
//   const r = await fetch(...);
//   if (!r.ok) {
//     alert(await friendlyServerError(r, '上傳失敗'));
//     return;
//   }
//
// 全域可呼叫: window.friendlyServerError
(function () {
  async function friendlyServerError(r, fallback) {
    const code = r ? r.status : 0;
    const codeMap = {
      400: '請求格式錯誤', 401: '未登入或登入逾期', 403: '權限不足',
      404: '找不到資源', 408: '請求逾時', 410: '檔案已過期',
      413: '檔案太大', 415: '不支援的檔案格式', 422: '參數驗證失敗',
      429: '請求過於頻繁',
      500: '伺服器內部錯誤', 502: '後端服務無回應', 503: '服務暫時不可用',
      504: '後端逾時',
    };
    const base = codeMap[code] || (fallback || '操作失敗');
    let detail = '';
    try {
      const ct = (r.headers && r.headers.get && r.headers.get('content-type')) || '';
      if (ct.indexOf('application/json') >= 0) {
        const j = await r.json();
        const d = (j && (j.detail || j.error || j.message));
        if (typeof d === 'string' && d.length && d.length < 300) detail = d;
        else if (Array.isArray(d) && d[0] && d[0].msg) detail = String(d[0].msg).slice(0, 300);
      } else {
        const t = await r.text();
        if (t && t.indexOf('<') !== 0 && t.length < 300 && !/\n/.test(t)) {
          // 純文字短回應 — 嘗試解 JSON (有些 server 不設 ct)
          let parsed = null;
          try {
            parsed = JSON.parse(t);
          } catch (_) {}
          if (parsed && typeof parsed === 'object') {
            const d = parsed.detail || parsed.error || parsed.message;
            if (typeof d === 'string') detail = d;
          } else {
            detail = t;
          }
        } else if (t) {
          try { console.error('[friendly_error] server body:', t.slice(0, 1500)); } catch (_) {}
        }
      }
    } catch (_) {}
    const codeTag = code ? `（${code}）` : '';
    return detail ? `${base}${codeTag}：${detail}` : `${base}${codeTag}`;
  }
  window.friendlyServerError = friendlyServerError;
})();

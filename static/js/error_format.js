// Friendly error parser — extract `.detail` from FastAPI JSON error
// responses; fall back to raw text for non-JSON servers.
//
// Usage:
//   if (!r.ok) return alert('失敗：' + await jtdtError(r));
//
// Returns string. Best-effort — never throws.
window.jtdtError = async function(response) {
  try {
    const raw = await response.text();
    if (!raw) return `HTTP ${response.status} ${response.statusText || ''}`.trim();
    // Try to parse JSON first ({"detail": "..."} or {"message": "..."})
    try {
      const j = JSON.parse(raw);
      if (j && typeof j === 'object') {
        if (j.detail) return String(j.detail);
        if (j.message) return String(j.message);
        if (j.error)   return String(j.error);
      }
    } catch (_) { /* not JSON */ }
    // Plain text — but skip noisy HTML response bodies (long error pages
    // server-side that don't help users).
    if (raw.startsWith('<') && raw.length > 500) {
      return `伺服器錯誤 (HTTP ${response.status})`;
    }
    return raw;
  } catch (e) {
    return `HTTP ${response.status} (無法讀取錯誤訊息：${e.message})`;
  }
};

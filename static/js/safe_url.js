// safeImgSrc — validate URL is safe for assignment to <img src>.
// v1.5.4: Added as defence-in-depth (and to stop CodeQL js/xss-through-dom
// flagging .src = url assignments where url comes from server-fetched JSON).
// Allows: relative URLs starting with "/", "blob:", "data:image/", and
//         http(s):// URLs. Rejects javascript:, vbscript:, data:text/html.
(function () {
  function safeImgSrc(url) {
    if (typeof url !== 'string' || !url) return '';
    const lower = url.trim().toLowerCase();
    // Allow same-origin relative paths
    if (url.startsWith('/') || url.startsWith('./') || url.startsWith('../')) return url;
    // Allow blob: and data:image/ (common for previews)
    if (lower.startsWith('blob:')) return url;
    if (lower.startsWith('data:image/')) return url;
    // Allow http(s)://
    if (lower.startsWith('http://') || lower.startsWith('https://')) return url;
    // Anything else (javascript:, vbscript:, file://, data:text/...) is rejected.
    console.warn('safeImgSrc: rejected suspicious URL', url);
    return '';
  }
  window.safeImgSrc = safeImgSrc;
})();

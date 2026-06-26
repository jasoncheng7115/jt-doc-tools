// CSRF double-submit：把 jtdt_csrf cookie 的 token 自動帶進
//  ① 同源不安全 fetch 的 X-CSRF-Token 標頭
//  ② 原生 POST 表單的隱藏 csrf_token 欄位
// 後端（app/core/csrf.py）比對 cookie 與提交值。須早於其他 script 載入。
(function () {
  function token() {
    var m = document.cookie.match(/(?:^|;\s*)jtdt_csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  // ① 包裝 fetch：同源 POST/PUT/PATCH/DELETE 自動補 X-CSRF-Token
  var _fetch = window.fetch;
  if (_fetch) {
    window.fetch = function (input, init) {
      init = init || {};
      var method = (init.method
        || (input && typeof input === 'object' && input.method) || 'GET').toUpperCase();
      var url = (typeof input === 'string') ? input
        : (input && input.url) || '';
      var sameOrigin = url === '' || url.charAt(0) === '/'
        || url.indexOf(window.location.origin) === 0;
      if (sameOrigin && ['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(method) >= 0) {
        var headers = new Headers(
          init.headers || (input && typeof input === 'object' && input.headers) || {});
        if (!headers.has('X-CSRF-Token')) headers.set('X-CSRF-Token', token());
        init = Object.assign({}, init, { headers: headers });
        return _fetch.call(this, input, init);
      }
      return _fetch.call(this, input, init);
    };
  }

  // ② 原生 POST 表單：補隱藏 csrf_token 欄位
  function ensureField(form) {
    if (!form || form.tagName !== 'FORM') return;
    if ((form.method || 'get').toLowerCase() !== 'post') return;
    if (form.querySelector('input[name="csrf_token"]')) return;
    var i = document.createElement('input');
    i.type = 'hidden'; i.name = 'csrf_token'; i.value = token();
    form.appendChild(i);
  }
  function injectAll() {
    document.querySelectorAll('form').forEach(ensureField);
  }
  if (document.readyState !== 'loading') injectAll();
  else document.addEventListener('DOMContentLoaded', injectAll);
  // 動態建立 / 送出前再保險一次（值取最新 cookie）
  document.addEventListener('submit', function (e) {
    var f = e.target;
    if (f && f.tagName === 'FORM' && (f.method || 'get').toLowerCase() === 'post') {
      var fld = f.querySelector('input[name="csrf_token"]');
      if (!fld) ensureField(f);
      else if (!fld.value) fld.value = token();
    }
  }, true);
})();

// CSRF double-submit：把 jtdt_csrf cookie 的 token 自動帶進
//  ① 同源不安全 fetch 的 X-CSRF-Token 標頭
//  ② 原生 POST 表單的隱藏 csrf_token 欄位
// 後端（app/core/csrf.py）比對 cookie 與提交值。須早於其他 script 載入。
(function () {
  function token() {
    // cookie 為 HttpOnly（JS 讀不到）→ 從 <meta name="csrf-token"> 讀（server
    // render，與 cookie 同值）。退回 cookie（萬一 meta 缺、cookie 非 httponly）。
    var el = document.querySelector('meta[name="csrf-token"]');
    if (el && el.content) return el.content;
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
      // 同源 = 相對 URL（'api/x'、'/x'、''、'./x'…，非絕對也非協定相對）
      //        或 絕對且開頭是本站 origin。涵蓋相對路徑（之前漏掉 'api/x' → 403）。
      var isAbsolute = /^([a-z][a-z0-9+.-]*:)?\/\//i.test(url);
      var sameOrigin = !isAbsolute || url.indexOf(window.location.origin) === 0;
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

  // ②b 包裝 XMLHttpRequest：file_upload.js 用 XHR 做上傳進度條（fetch 無上傳
  //     進度 spec）→ 也要補 X-CSRF-Token，否則所有檔案上傳被 CSRF 擋成 403。
  var XO = window.XMLHttpRequest;
  if (XO && XO.prototype) {
    var _open = XO.prototype.open;
    var _send = XO.prototype.send;
    XO.prototype.open = function (method, url) {
      this.__csrfM = (method || 'GET').toUpperCase();
      this.__csrfU = url || '';
      return _open.apply(this, arguments);
    };
    XO.prototype.send = function () {
      var m = this.__csrfM, u = this.__csrfU;
      var isAbs = /^([a-z][a-z0-9+.-]*:)?\/\//i.test(u);
      var sameOrigin = !isAbs || u.indexOf(window.location.origin) === 0;
      if (sameOrigin && ['POST', 'PUT', 'PATCH', 'DELETE'].indexOf(m) >= 0) {
        try { this.setRequestHeader('X-CSRF-Token', token()); } catch (e) {}
      }
      return _send.apply(this, arguments);
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

// 取代被 CSP nonce 擋掉的通用 inline 事件處理器（宣告式 data-*）。
// 載於 base.html，所有頁共用。頁面專屬的處理器（submission-check 等）在各自
// 的 nonce'd <script> 內用委派處理。
(function () {
  // ① data-stop-prop：點擊阻止冒泡（+ data-prevent-default 則一併 preventDefault）。
  //    必須「直接綁在元素上」（target 階段）—— document 委派在冒泡末端才跑，
  //    來不及阻止父層 handler。動態插入的元素可再呼叫 window.bindStopProp(root)。
  function bindStopProp(root) {
    (root || document).querySelectorAll('[data-stop-prop]').forEach(function (el) {
      if (el._stopPropBound) return;
      el._stopPropBound = 1;
      el.addEventListener('click', function (e) {
        e.stopPropagation();
        if (el.hasAttribute('data-prevent-default')) e.preventDefault();
      });
    });
  }
  window.bindStopProp = bindStopProp;

  // ② data-submit-on-change：change 時送出所屬表單（change 會冒泡 → 委派可行）。
  document.addEventListener('change', function (e) {
    var el = e.target.closest ? e.target.closest('[data-submit-on-change]') : null;
    if (el && el.form) el.form.submit();
  });

  // ③ lazy 圖片載入完成移除 .loading（取代 inline onload；load 不冒泡 → 用 capture）。
  document.addEventListener('load', function (e) {
    var t = e.target;
    if (t && t.tagName === 'IMG' && t.classList && t.classList.contains('loading')) {
      t.classList.remove('loading');
    }
  }, true);

  function init() { bindStopProp(document); }
  if (document.readyState !== 'loading') init();
  else document.addEventListener('DOMContentLoaded', init);
})();

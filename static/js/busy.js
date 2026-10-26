/*
 * The seconds count on a busy button (theme/busy.py). Counted here rather
 * than pushed from the server so a long job costs no socket traffic; it
 * starts from when the browser first sees the spinner, so clock skew between
 * server and reader can't show a wrong number.
 */
(function () {
  if (window.__wizBusy) return;
  window.__wizBusy = true;
  setInterval(function () {
    var now = Date.now();
    document.querySelectorAll('.wiz-busy__elapsed').forEach(function (el) {
      var t0 = Number(el.dataset.t0);
      if (!t0) { el.dataset.t0 = String(now); return; }
      var s = Math.floor((now - t0) / 1000);
      el.textContent = s >= 3 ? s + 's' : '';
    });
  }, 1000);
})();

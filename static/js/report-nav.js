(function () {
  if (window.__wizReportsInstalled) return;
  window.__wizReportsInstalled = true;

  var PREFIX = 'wiz-report-scroll:';
  var RESTORE_FRAME_BUDGET = 300;

  function onReports() {
    return window.location.pathname.indexOf('/admin/reports') !== -1;
  }

  function reportKey() {
    var m = /[?&]report=([^&]*)/.exec(window.location.search);
    return PREFIX + (m ? decodeURIComponent(m[1]) : 'dashboard');
  }

  var SCROLL_KEYS = {
    PageUp: 1, PageDown: 1, Home: 1, End: 1, ' ': 1,
    ArrowUp: 1, ArrowDown: 1, ArrowLeft: 1, ArrowRight: 1,
  };
  var INTENT_WINDOW_MS = 500;
  var lastIntent = 0;
  var restoring = false;

  function markIntent() {
    lastIntent = Date.now();
  }

  function track() {
    // Only operator-intended scrolls count: focusing a date input jumps the page to the top on its own.
    window.addEventListener('wheel', markIntent, { passive: true });
    window.addEventListener('touchmove', markIntent, { passive: true });
    window.addEventListener('keydown', function (e) {
      if (SCROLL_KEYS[e.key]) markIntent();
    }, { passive: true });
    // Scrollbar drags target the root element rather than any content.
    window.addEventListener('mousedown', function (e) {
      if (e.target === document.documentElement || e.target === document.body) markIntent();
    }, { passive: true });

    window.addEventListener(
      'scroll',
      function () {
        // Written as it happens: the filter handler applies synchronously, so fetching scrollY then would race it.
        if (restoring) return;
        if (Date.now() - lastIntent > INTENT_WINDOW_MS) return;
        try {
          window.sessionStorage.setItem(reportKey(), String(Math.round(window.scrollY)));
        } catch (e) {
          /* private mode / quota — losing the position is not worth an error */
        }
      },
      { passive: true }
    );
  }

  function restore() {
    var raw;
    try {
      raw = window.sessionStorage.getItem(reportKey());
    } catch (e) {
      return;
    }
    if (!raw) return;
    var y = parseInt(raw, 10);
    if (!y || y <= 0) return;

    // Retry until the streamed body is tall enough. `restoring` keeps the collapse (and our scrollTo) from overwriting the stored value.
    var frames = 0;
    restoring = true;
    (function step() {
      if (document.body && document.body.scrollHeight >= y + window.innerHeight * 0.5) {
        window.scrollTo(0, y);
        restoring = false;
        return;
      }
      if (++frames > RESTORE_FRAME_BUDGET) {
        restoring = false;
        return;
      }
      window.requestAnimationFrame(step);
    })();
  }

  // Called by the server right after it replaceStates the new filter URL.
  window.wizReportKeepPlace = restore;

  if (onReports()) {
    track();
    restore();
  }
})();

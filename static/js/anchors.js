/*
 * Scroll to the address's #fragment once the thing it names exists
 * (theme/anchors.py). A page here builds over its socket after load, and a lazy
 * tab or a board's rows later still, so the browser's own jump to the fragment
 * finds nothing. This watches the page for up to 20s, scrolls the target into
 * view, and flashes it so the eye lands on it. Runs again on hashchange.
 *
 *   #your-matches  an element with that id
 *   #match-123     the first visible row or card for match 123
 *   #shift-45      the shift block for shift 45
 */
(function () {
  if (window.__wizAnchors) return;
  window.__wizAnchors = true;

  function visible(el) {
    return el && el.getClientRects().length > 0;
  }

  function first(list) {
    for (var i = 0; i < list.length; i++) if (visible(list[i])) return list[i];
    return null;
  }

  function find(name) {
    var byId = document.getElementById(name);
    if (visible(byId)) return byId;
    var m = /^match-(\d+)$/.exec(name);
    if (m) {
      var hit = first(document.querySelectorAll('[data-wiz-row="' + m[1] + '"]'));
      return hit ? (hit.closest('tr') || hit) : null;
    }
    var s = /^shift-(\d+)$/.exec(name);
    if (s) return first(document.querySelectorAll('[data-wiz-shift="' + s[1] + '"]'));
    return null;
  }

  var observer = null;
  var timer = null;

  function stop() {
    if (observer) observer.disconnect();
    if (timer) clearTimeout(timer);
    observer = timer = null;
  }

  function land(el) {
    stop();
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    el.classList.remove('wiz-anchor-hit');
    void el.offsetWidth;
    el.classList.add('wiz-anchor-hit');
    setTimeout(function () { el.classList.remove('wiz-anchor-hit'); }, 2400);
  }

  function seek() {
    stop();
    var name;
    try {
      name = decodeURIComponent(location.hash.slice(1));
    } catch (_) {
      return;
    }
    if (!name) return;
    var el = find(name);
    if (el) return land(el);
    observer = new MutationObserver(function () {
      var found = find(name);
      if (found) setTimeout(function () { land(found); }, 150);
    });
    observer.observe(document.body, { childList: true, subtree: true });
    timer = setTimeout(stop, 20000);
  }

  window.addEventListener('hashchange', seek);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', seek);
  else seek();
})();

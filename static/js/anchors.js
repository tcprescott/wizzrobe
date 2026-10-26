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
 *
 * Anything carrying ``data-wiz-copy-link`` copies a link to this page at that
 * fragment when clicked (an empty value means the nearest enclosing section's id).
 * It's a document listener rather than a server round trip because Safari only
 * lets a page write the clipboard inside the click itself.
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

  function toast(message, color) {
    if (window.Quasar && Quasar.Notify) {
      Quasar.Notify.create({ message: message, color: color, icon: 'link', position: 'top', timeout: 2500 });
    }
  }

  function fallbackCopy(text) {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    var ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    area.remove();
    return ok;
  }

  function copyLink(fragment) {
    var url = location.origin + location.pathname + location.search + (fragment ? '#' + fragment : '');
    var done = function () { toast('Link copied.', 'positive'); };
    var failed = function () {
      if (fallbackCopy(url)) done();
      else toast("Couldn't copy the link. It's in the address bar now.", 'warning');
    };
    if (fragment) history.replaceState(history.state, '', url);
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, failed);
    else failed();
  }

  window.wizCopyLink = copyLink;

  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('[data-wiz-copy-link]');
    if (!el) return;
    var fragment = el.getAttribute('data-wiz-copy-link');
    // NiceGUI gives every element an id like c123, which no link should name.
    for (var owner = el.parentElement; !fragment && owner; owner = owner.parentElement) {
      if (owner.id && !/^c\d+$/.test(owner.id)) fragment = owner.id;
    }
    copyLink(fragment);
  });

  window.addEventListener('hashchange', seek);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', seek);
  else seek();
})();

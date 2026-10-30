"""Client-side connection watch — an action that cannot reach the server says so.

While the socket is down the server can say nothing: ``ui.notify``, a
``refreshable.refresh()`` and a disabled-state update all travel over the very
socket that is gone. So the honest-offline state is built in the browser and
judged by what the operator sees with the network off:

* one **card** pinned top-centre under the header, where toasts open (not a
  toast itself — a toast fired while the phone was in a pocket has expired by
  the time anyone looks). Top rather than bottom so it never covers a phone
  bottom sheet's Save button, the control people reach for while offline;
* a ``wiz-offline`` class on ``<body>``, the single hook every other offline rule
  reads;
* a capture-phase click guard that refuses any control marked
  ``wiz-requires-socket`` and explains the refusal through ``Quasar.Notify``,
  which is client-side and therefore still works. While the card shows,
  ``wiz-connection-card-shown`` and ``--wiz-connection-card-h`` push the toast
  stack below it so the two never overlap.

Detection reads two independent signals because neither alone is sufficient: the
window ``offline``/``online`` events fire the moment the link drops, while the
socket's ``disconnect``/``connect`` cover a live link to an unreachable server,
where ``navigator.onLine`` stays ``true``.

The framework's own ``#popup`` ("Connection lost / Trying to reconnect") is
hidden in ``static/css/styles.css``: it shows on the same socket ``disconnect``
this card already reads, only later (``ping_interval + ping_timeout`` plus a 2 s
``transition-delay``), so it would only ever repeat the card.
"""

import json

from nicegui import ui

__all__ = [
    'BLOCKED_ACTION_TEXT',
    'CARD_SHOWN_BODY_CLASS',
    'OFFLINE_BODY_CLASS',
    'OFFLINE_BODY_TEXT',
    'OFFLINE_TITLE',
    'ONLINE_TITLE',
    'RECONNECTING_TEXT',
    'REQUIRES_SOCKET_CLASS',
    'connection_watch_script',
    'install_connection_watch',
]

# Copy says what it means for the operator, not what happened to the socket.
OFFLINE_TITLE = 'Connection lost'
OFFLINE_BODY_TEXT = "Anything you change now won't be saved."
RECONNECTING_TEXT = 'Trying to reconnect…'
ONLINE_TITLE = 'Back online'
BLOCKED_ACTION_TEXT = "No connection, so that wasn't sent. Try again once you're back online."

OFFLINE_BODY_CLASS = 'wiz-offline'
CARD_SHOWN_BODY_CLASS = 'wiz-connection-card-shown'
#: Put on any control whose effect needs a round trip, so the guard can refuse it.
REQUIRES_SOCKET_CLASS = 'wiz-requires-socket'

_CARD_ID = 'wiz-connection-card'
_RECONNECTED_MS = 2000
_SOCKET_POLL_MS = 100
_SOCKET_POLL_TRIES = 100


def connection_watch_script() -> str:
    """The ``<script>`` installed by :func:`install_connection_watch`.

    Exposed so its copy and its four event bindings can be asserted without a
    browser.
    """
    return _SCRIPT


def install_connection_watch() -> None:
    """Give the current page a visible, persistent offline state.

    Call at page-build time. ``BaseLayout.render_chrome`` calls it for every
    themed page, which is deliberate: tab panels are built lazily inside an event
    handler, and ``ui.add_body_html`` from there inserts markup with
    ``insertAdjacentHTML``, which never executes a ``<script>``. A tab that
    installed its own watch would silently get nothing.
    """
    ui.add_body_html(_SCRIPT)


_SCRIPT = '''<script>
(function () {
  if (window.__wizConnectionWatch) return;
  window.__wizConnectionWatch = true;

  var OFFLINE_TITLE = %(offline_title)s;
  var OFFLINE_BODY = %(offline_body)s;
  var RECONNECTING = %(reconnecting)s;
  var ONLINE_TITLE = %(online_title)s;
  var BLOCKED_TEXT = %(blocked)s;
  var OFFLINE_CLASS = %(offline_class)s;
  var SHOWN_CLASS = %(shown_class)s;
  var REQUIRES_SOCKET = %(requires_socket)s;
  var clearTimer = null;
  var parts = null;

  function part(tag, cls, parent) {
    var el = document.createElement(tag);
    el.className = cls;
    parent.appendChild(el);
    return el;
  }

  function card() {
    if (parts) return parts;
    var el = document.createElement('div');
    el.id = %(card_id)s;
    el.className = 'wiz-connection-card';
    el.setAttribute('role', 'status');
    el.setAttribute('aria-live', 'polite');
    el.hidden = true;
    var icon = part('i', 'wiz-connection-card__icon q-icon notranslate material-icons', el);
    icon.setAttribute('aria-hidden', 'true');
    var text = part('div', 'wiz-connection-card__text', el);
    var title = part('div', 'wiz-connection-card__title', text);
    var body = part('div', 'wiz-connection-card__body', text);
    var status = part('div', 'wiz-connection-card__status', text);
    part('span', 'wiz-connection-card__spinner', status).setAttribute('aria-hidden', 'true');
    var statusText = part('span', '', status);
    document.body.appendChild(el);
    parts = { el: el, icon: icon, title: title, body: body, status: status, statusText: statusText };
    return parts;
  }

  // Toasts open top-centre too; the stack reads this height to sit below the card.
  function measure() {
    if (!parts || parts.el.hidden) return;
    document.documentElement.style.setProperty('--wiz-connection-card-h', parts.el.offsetHeight + 'px');
  }

  function hide() {
    if (parts) parts.el.hidden = true;
    document.body.classList.remove(SHOWN_CLASS);
  }

  function show(ok) {
    var c = card();
    c.icon.textContent = ok ? 'wifi' : 'wifi_off';
    c.title.textContent = ok ? ONLINE_TITLE : OFFLINE_TITLE;
    c.body.textContent = OFFLINE_BODY;
    c.statusText.textContent = RECONNECTING;
    c.body.hidden = !!ok;
    c.status.hidden = !!ok;
    c.el.classList.toggle('wiz-connection-card--ok', !!ok);
    c.el.hidden = false;
    document.body.classList.add(SHOWN_CLASS);
    measure();
    if (clearTimer) { clearTimeout(clearTimer); clearTimer = null; }
    if (ok) clearTimer = setTimeout(hide, %(reconnected_ms)d);
  }

  function down() {
    document.body.classList.add(OFFLINE_CLASS);
    show(false);
  }

  function up() {
    // Only announce a recovery we actually reported, so the socket's first
    // connect on a normal page load does not flash "Back online".
    if (!document.body.classList.contains(OFFLINE_CLASS)) return;
    document.body.classList.remove(OFFLINE_CLASS);
    show(true);
  }

  window.addEventListener('offline', down);
  window.addEventListener('online', up);
  window.addEventListener('resize', measure);

  // window.socket is assigned in nicegui.js's mounted(), which can run after
  // this snippet is parsed — poll for it briefly instead of assuming it exists,
  // and give up quietly rather than throw.
  var tries = 0;
  (function bindSocket() {
    if (window.socket && window.socket.on) {
      window.socket.on('disconnect', down);
      window.socket.on('connect', up);
      return;
    }
    if (tries++ < %(poll_tries)d) setTimeout(bindSocket, %(poll_ms)d);
  })();

  // Refuse in the capture phase, before Vue's own click listener runs, and
  // explain the refusal with Quasar.Notify — ui.notify would need the socket
  // that is down.
  document.addEventListener('click', function (e) {
    if (!document.body.classList.contains(OFFLINE_CLASS)) return;
    var hit = e.target && e.target.closest ? e.target.closest('.' + REQUIRES_SOCKET) : null;
    if (!hit) return;
    e.preventDefault();
    e.stopPropagation();
    e.stopImmediatePropagation();
    if (window.Quasar && Quasar.Notify) {
      Quasar.Notify.create({ message: BLOCKED_TEXT, color: 'warning', position: 'top', timeout: 4000 });
    }
  }, true);
})();
</script>''' % {
    'offline_title': json.dumps(OFFLINE_TITLE),
    'offline_body': json.dumps(OFFLINE_BODY_TEXT),
    'reconnecting': json.dumps(RECONNECTING_TEXT),
    'online_title': json.dumps(ONLINE_TITLE),
    'blocked': json.dumps(BLOCKED_ACTION_TEXT),
    'offline_class': json.dumps(OFFLINE_BODY_CLASS),
    'shown_class': json.dumps(CARD_SHOWN_BODY_CLASS),
    'requires_socket': json.dumps(REQUIRES_SOCKET_CLASS),
    'card_id': json.dumps(_CARD_ID),
    'reconnected_ms': _RECONNECTED_MS,
    'poll_ms': _SOCKET_POLL_MS,
    'poll_tries': _SOCKET_POLL_TRIES,
}

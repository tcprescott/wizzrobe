// Minimal service worker for Wizzrobe.
//
// Two jobs, deliberately nothing more:
//
// 1. Satisfy Android/Chrome's PWA installability requirement: the "Install
//    app" / "Add to Home screen" prompt is only offered when a service worker
//    with a fetch handler is registered. It caches NOTHING — every request
//    passes straight through to the network, so the app is always fresh and
//    there is no stale-asset class of bug to reason about.
//
// 2. Display Web Push messages on browsers without Declarative Web Push
//    (Chrome/Android). The server always sends the declarative JSON shape
//    ({web_push: 8030, notification: {...}}); Safari 18.4+/iOS 18.4+ renders
//    it natively without ever waking this worker, while the handlers below
//    render the same payload everywhere else.
//
// 3. Re-register the device when its push service retires the subscription
//    ('pushsubscriptionchange'), so a rotation no longer costs the user their
//    notifications until they notice and re-enable by hand.

importScripts('/static/js/web-push-common.js');

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
  // Network-only pass-through: no caching, ever.
  event.respondWith(fetch(event.request));
});

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { notification: { body: event.data ? event.data.text() : '' } };
  }
  const n = data.notification || {};
  event.waitUntil(self.registration.showNotification(n.title || 'Wizzrobe', {
    body: n.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    lang: n.lang,
    dir: n.dir,
    silent: !!n.silent,
    data: { navigate: n.navigate || '/' },
  }));
});

// A push service can retire a subscription on its own (key rotation, a long
// idle period, a browser update). Re-subscribe and hand the server the new
// endpoint, authenticating with the retired subscription's auth secret — see
// application/services/web_push_service.py rotate_subscription.
//
// The event's own oldSubscription/newSubscription are used when present but
// never relied on: browsers have shipped this event with both null, which is
// why web-push-common.js keeps the device's record in IndexedDB.
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(rotateSubscription(event).catch(() => {}));
});

function subscriptionKeys(subscription) {
  try {
    return (subscription && subscription.toJSON().keys) || {};
  } catch (e) {
    return {};
  }
}

async function rotateSubscription(event) {
  const stored = await self.wizWebPushStore.load();
  const old = event.oldSubscription;
  const oldEndpoint = (stored && stored.endpoint) || (old && old.endpoint);
  const oldAuth = (stored && stored.auth) || subscriptionKeys(old).auth;
  // Without both we cannot say which subscription this is, nor prove we own
  // it. Bail rather than register a device the server can't attribute.
  if (!oldEndpoint || !oldAuth) return;

  let fresh = event.newSubscription;
  if (!fresh) {
    if (!stored || !stored.applicationServerKey) return;
    fresh = await self.registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: self.wizWebPushKey(stored.applicationServerKey),
    });
  }
  const json = fresh.toJSON();
  const keys = json.keys || {};
  const response = await fetch('/api/web-push/rotate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      old_endpoint: oldEndpoint,
      old_auth: oldAuth,
      endpoint: json.endpoint,
      p256dh: keys.p256dh,
      auth: keys.auth,
      user_agent: navigator.userAgent,
    }),
  });
  // Keep the old record on failure: it is the only credential a retry has.
  if (!response.ok) return;
  await self.wizWebPushStore.save({
    endpoint: json.endpoint,
    auth: keys.auth,
    applicationServerKey: stored ? stored.applicationServerKey : null,
  });
}

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL((event.notification.data || {}).navigate || '/', self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const win of windows) {
      if (win.url === target && 'focus' in win) return win.focus();
    }
    return self.clients.openWindow(target);
  })());
});

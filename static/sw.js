// The fetch handler is required for PWA installability. It caches NOTHING: every request goes to the network.

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

// The event's oldSubscription/newSubscription are used when present but never relied on: browsers ship it with both null.
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
  // Without both we can't say which subscription this is, nor prove we own it.
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

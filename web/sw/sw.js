/* global self, caches, indexedDB, fetch, URL */
// Built into dist/sw.js by the `serviceWorker` plugin in vite.config.ts, which
// replaces the two placeholders below. Caches only the app's own build files
// (code, styles, fonts, icons); API data always comes from the network, so one
// person's data never sits in a cache another could open.

const BUILD = '__WIZ_BUILD__'
const PRECACHE = __WIZ_PRECACHE__
const API_BASE = '__WIZ_API_BASE__'
const CACHE = `wiz-app-${BUILD}`
const SHELL = '/index.html'

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll([SHELL, ...PRECACHE]))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys()
      await Promise.all(names.filter((n) => n.startsWith('wiz-app-') && n !== CACHE).map((n) => caches.delete(n)))
      await self.clients.claim()
    })(),
  )
})

// Paths the backend answers, never the app: they must always reach the network.
const SERVER_PATHS = ['/api/', '/oauth', '/.well-known/', '/internal/', '/static/', '/mcp']

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  if (SERVER_PATHS.some((p) => url.pathname.startsWith(p))) return

  if (request.mode === 'navigate') {
    // Network first so a deploy shows at once; the cached shell covers a dead connection.
    event.respondWith(fetch(request).catch(() => caches.match(SHELL).then((hit) => hit || Response.error())))
    return
  }
  event.respondWith(caches.match(request).then((hit) => hit || fetch(request)))
})

self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { notification: { body: event.data ? event.data.text() : '' } }
  }
  const n = data.notification || {}
  event.waitUntil(
    self.registration.showNotification(n.title || 'Wizzrobe', {
      body: n.body || '',
      icon: '/icons/icon-192.png',
      badge: '/icons/icon-192.png',
      lang: n.lang,
      dir: n.dir,
      silent: !!n.silent,
      data: { navigate: n.navigate || '/' },
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  // Links are built on the server's base URL; open them on this origin so the
  // tap lands in this app wherever it is hosted.
  const raw = new URL((event.notification.data || {}).navigate || '/', self.location.origin)
  const target = new URL(raw.pathname + raw.search + raw.hash, self.location.origin).href
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      for (const win of windows) {
        if (win.url === target && 'focus' in win) return win.focus()
      }
      for (const win of windows) {
        if ('navigate' in win && 'focus' in win) {
          await win.focus()
          return win.navigate(target)
        }
      }
      return self.clients.openWindow(target)
    })(),
  )
})

// Browsers can reissue a subscription with no page open, and usually send both
// old and new as null, so the page saved what a rotation needs when it subscribed.
const STORE_DB = 'wiz-web-push'
const STORE = 'state'
const STORE_KEY = 'subscription'

function idb(mode, action) {
  return new Promise((resolve, reject) => {
    const open = indexedDB.open(STORE_DB, 1)
    open.onupgradeneeded = () => {
      if (!open.result.objectStoreNames.contains(STORE)) open.result.createObjectStore(STORE)
    }
    open.onerror = () => reject(open.error)
    open.onsuccess = () => {
      const db = open.result
      const req = action(db.transaction(STORE, mode).objectStore(STORE))
      req.onsuccess = () => {
        db.close()
        resolve(req.result)
      }
      req.onerror = () => {
        db.close()
        reject(req.error)
      }
    }
  })
}

function keyBytes(base64url) {
  const raw = atob((base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/'))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(rotate(event).catch(() => {}))
})

async function rotate(event) {
  const stored = await idb('readonly', (s) => s.get(STORE_KEY)).catch(() => null)
  const old = event.oldSubscription
  const oldEndpoint = (stored && stored.endpoint) || (old && old.endpoint)
  const oldAuth = (stored && stored.auth) || (old && (old.toJSON().keys || {}).auth)
  if (!oldEndpoint || !oldAuth) return

  let fresh = event.newSubscription
  if (!fresh) {
    if (!stored || !stored.applicationServerKey) return
    fresh = await self.registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: keyBytes(stored.applicationServerKey),
    })
  }
  const json = fresh.toJSON()
  const keys = json.keys || {}
  const response = await fetch(`${API_BASE}/api/web-push/rotate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      old_endpoint: oldEndpoint,
      old_auth: oldAuth,
      endpoint: json.endpoint,
      p256dh: keys.p256dh,
      auth: keys.auth,
      user_agent: self.navigator.userAgent,
    }),
  })
  // Keep the old record on failure: it is the only credential a retry has.
  if (!response.ok) return
  await idb('readwrite', (s) =>
    s.put({ endpoint: json.endpoint, auth: keys.auth, applicationServerKey: stored ? stored.applicationServerKey : null }, STORE_KEY),
  ).catch(() => {})
}

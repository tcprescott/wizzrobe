/**
 * This device's web-push subscription. Needs a registered service worker;
 * where there is none (or iOS outside a Home Screen install) it says so
 * rather than hanging.
 */

function isIOS(): boolean {
  return /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
}

function standalone(): boolean {
  return matchMedia('(display-mode: standalone)').matches || (navigator as { standalone?: boolean }).standalone === true
}

async function manager(): Promise<PushManager | null> {
  if (isIOS() && !standalone()) return null
  if (!('serviceWorker' in navigator)) return null
  const reg = await Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => setTimeout(() => resolve(null), 3000)),
  ])
  return reg?.pushManager ?? null
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const pad = '='.repeat((4 - (base64url.length % 4)) % 4)
  const raw = atob((base64url + pad).replace(/-/g, '+').replace(/_/g, '/'))
  const out = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i)
  return out
}

function b64(buffer: ArrayBuffer | null): string {
  if (!buffer) return ''
  return btoa(String.fromCharCode(...new Uint8Array(buffer)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

export interface DeviceSubscription {
  endpoint: string
  p256dh: string
  auth: string
  user_agent: string
}

/** Why this device can't take notifications, or null when it can. */
export async function pushUnavailable(): Promise<string | null> {
  if (isIOS() && !standalone()) return 'On iPhone and iPad, add Wizzrobe to your Home Screen first, then turn this on from there.'
  if (!('Notification' in window)) return "This browser can't show notifications."
  if (!(await manager())) return "This copy of the app can't take notifications yet. Use the website or the installed app."
  return null
}

export async function currentEndpoint(): Promise<string | null> {
  const pm = await manager()
  const sub = pm ? await pm.getSubscription() : null
  return sub?.endpoint ?? null
}

export async function subscribeDevice(publicKey: string): Promise<DeviceSubscription> {
  const pm = await manager()
  if (!pm) throw new Error("This copy of the app can't take notifications yet.")
  if ((await Notification.requestPermission()) !== 'granted') {
    throw new Error('Notifications are blocked for this site. Allow them in your browser settings, then try again.')
  }
  const sub = (await pm.getSubscription()) ?? (await pm.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(publicKey) }))
  await rememberForRotation({ endpoint: sub.endpoint, auth: b64(sub.getKey('auth')), applicationServerKey: publicKey })
  return { endpoint: sub.endpoint, p256dh: b64(sub.getKey('p256dh')), auth: b64(sub.getKey('auth')), user_agent: navigator.userAgent }
}

interface RotationRecord {
  endpoint: string
  auth: string
  applicationServerKey: string
}

/**
 * What the service worker needs if the browser reissues this subscription
 * with no page open (see sw/sw.js). Same database and key as the worker reads.
 */
function rememberForRotation(record: RotationRecord): Promise<void> {
  return new Promise((resolve) => {
    try {
      const open = indexedDB.open('wiz-web-push', 1)
      open.onupgradeneeded = () => {
        if (!open.result.objectStoreNames.contains('state')) open.result.createObjectStore('state')
      }
      open.onerror = () => resolve()
      open.onsuccess = () => {
        const db = open.result
        const tx = db.transaction('state', 'readwrite')
        tx.objectStore('state').put(record, 'subscription')
        tx.oncomplete = tx.onerror = () => {
          db.close()
          resolve()
        }
      }
    } catch {
      resolve()
    }
  })
}

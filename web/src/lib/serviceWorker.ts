import { isNative } from './platform'

/**
 * Registers dist/sw.js (built from sw/sw.js) on the website. The store apps
 * skip it: their screens are bundled, and push comes through the native plugin.
 */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || isNative() || !('serviceWorker' in navigator)) return
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      // No offline copy and no web push on this visit; everything else works.
    })
  })
}

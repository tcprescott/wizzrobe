/**
 * Wires the store apps' native events into the router: links that open the
 * app (universal links / app links), the sign-in return on the app scheme,
 * notification taps and Android's back button. Does nothing on the website.
 */
import { App, type URLOpenListenerEvent } from '@capacitor/app'
import { PushNotifications } from '@capacitor/push-notifications'
import { api } from '../api/client'
import { beforeSignOut } from '../auth/session'
import { isNative } from '../lib/platform'
import { isCheckoutReturn } from './checkout'
import { createChannels, rememberDeviceId, rememberedDeviceId } from './push'
import { closeSignInPage } from './signIn'

type Navigate = (to: string) => void

/** Where a link or notification target lands inside the app, or null if it isn't ours. */
export function inAppPath(raw: string, appId: string): string | null {
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    return raw.startsWith('/') && !raw.startsWith('//') ? raw : null
  }
  if (url.protocol === `${appId.toLowerCase()}:`) {
    // org.example.app:/auth/callback?code=… parses with the path in pathname.
    const path = url.pathname.startsWith('/') ? url.pathname : `/${url.pathname}`
    return path === '/auth/callback' ? `${path}${url.search}` : null
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return null
  return `${url.pathname}${url.search}${url.hash}` || '/'
}

export function startNativeBridge(navigate: Navigate): void {
  if (!isNative()) return
  const appId = import.meta.env.VITE_APP_ID

  void App.addListener('appUrlOpen', (event: URLOpenListenerEvent) => {
    const path = inAppPath(event.url, appId)
    if (!path) return
    if (path.startsWith('/auth/callback') || isCheckoutReturn(path)) void closeSignInPage()
    navigate(path)
  })

  void PushNotifications.addListener('pushNotificationActionPerformed', (action) => {
    const target = (action.notification.data as { navigate?: string } | undefined)?.navigate
    const path = target ? inAppPath(target, appId) : null
    if (path) navigate(path)
  })

  void App.addListener('backButton', ({ canGoBack }) => {
    if (canGoBack) window.history.back()
    else void App.exitApp()
  })

  void createChannels().catch(() => undefined)

  beforeSignOut(async () => {
    const id = rememberedDeviceId()
    if (id === null) return
    rememberDeviceId(null)
    await api.DELETE('/api/me/channels/native-devices/{device_id}', { params: { path: { device_id: id } } })
    await PushNotifications.unregister().catch(() => undefined)
  })
}

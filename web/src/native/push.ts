/**
 * Native push in the store apps: ask the phone for permission, get its token
 * (APNs on iPhone, FCM on Android) and register it with the server.
 */
import { Device } from '@capacitor/device'
import { PushNotifications, type Token } from '@capacitor/push-notifications'
import { readStored, writeStored } from '../lib/storage'
import { nativePlatform, type NativePlatform } from '../lib/platform'

const DEVICE_KEY = 'wiz.nativePushDevice'

export interface NativeRegistration {
  platform: NativePlatform
  token: string
  device_name: string | null
}

/** Android sorts notifications into channels; the server picks one per message. */
export async function createChannels(): Promise<void> {
  if (nativePlatform() !== 'android') return
  await PushNotifications.createChannel({
    id: 'messages',
    name: 'Messages',
    description: 'Matches, shifts and anything that needs you',
    importance: 4,
    visibility: 1,
  })
  await PushNotifications.createChannel({
    id: 'quiet',
    name: 'Quiet updates',
    description: 'Messages your settings deliver without a sound',
    importance: 2,
    visibility: 1,
  })
}

/** Why this phone can't take notifications, or null when it can (or might, once asked). */
export async function nativePushUnavailable(): Promise<string | null> {
  const status = await PushNotifications.checkPermissions()
  if (status.receive === 'denied') {
    return 'Notifications are off for Wizzrobe. Turn them on in your phone’s Settings, then try again.'
  }
  return null
}

function waitForToken(): Promise<string> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      reject(new Error("Your phone didn't hand out a notification token. Try again in a moment."))
    }, 15_000)
    const done = (fn: () => void) => {
      window.clearTimeout(timer)
      void ok.then((h) => h.remove())
      void bad.then((h) => h.remove())
      fn()
    }
    const ok = PushNotifications.addListener('registration', (token: Token) => done(() => resolve(token.value)))
    const bad = PushNotifications.addListener('registrationError', (err) =>
      done(() => reject(new Error(err.error || "Your phone couldn't sign up for notifications."))),
    )
  })
}

/** Asks for permission (the system prompt) and returns what to send to the server. */
export async function requestNativePush(): Promise<NativeRegistration> {
  const platform = nativePlatform()
  if (!platform) throw new Error('This only works in the Wizzrobe app.')
  let status = await PushNotifications.checkPermissions()
  if (status.receive === 'prompt' || status.receive === 'prompt-with-rationale') {
    status = await PushNotifications.requestPermissions()
  }
  if (status.receive !== 'granted') {
    throw new Error('Notifications are off for Wizzrobe. Turn them on in your phone’s Settings, then try again.')
  }
  const token = waitForToken()
  await PushNotifications.register()
  const info = await Device.getInfo().catch(() => null)
  return { platform, token: await token, device_name: info?.model ?? null }
}

/** The server's id for this phone's registration, kept so sign-out can remove it. */
export function rememberedDeviceId(): number | null {
  const raw = readStored(DEVICE_KEY)
  const id = raw ? Number(raw) : NaN
  return Number.isInteger(id) ? id : null
}

export function rememberDeviceId(id: number | null): void {
  writeStored(DEVICE_KEY, id === null ? null : String(id))
}

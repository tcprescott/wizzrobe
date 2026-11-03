import { Browser } from '@capacitor/browser'
import { isNative } from '../lib/platform'

/**
 * The store's hosted checkout. In a browser the tab goes there and the store
 * sends it back; in the store apps it opens in the system's in-app browser and
 * the return link comes back as a universal link, which closes it.
 */
export async function openCheckout(url: string): Promise<void> {
  if (isNative()) await Browser.open({ url, presentationStyle: 'popover' })
  else window.location.assign(url)
}

export function isCheckoutReturn(path: string): boolean {
  return /\/event\/badges\?(.*&)?order=\d+/.test(path)
}

import { useEffect } from 'react'
import { Browser } from '@capacitor/browser'
import { openSignInPage } from '../native/signIn'
import { isNative } from './platform'

/**
 * Opens an outside service's sign-in page whose address the server hands out.
 *
 * The tab is opened before the request so a browser doesn't treat it as a
 * pop-up; it's pointed at the address once the server answers.
 */
export async function openOutsideSignIn(address: () => Promise<string>): Promise<void> {
  if (isNative()) {
    await openSignInPage(await address())
    return
  }
  const tab = window.open('', '_blank')
  try {
    const url = await address()
    if (tab) {
      tab.opener = null
      tab.location.href = url
    } else {
      window.location.assign(url)
    }
  } catch (err) {
    tab?.close()
    throw err
  }
}

/** Calls `reload` when the person comes back from a sign-in tab or the in-app browser. */
export function useReloadOnReturn(reload: () => void) {
  useEffect(() => {
    const back = () => {
      if (document.visibilityState === 'visible') reload()
    }
    document.addEventListener('visibilitychange', back)
    window.addEventListener('focus', back)
    const finished = isNative() ? Browser.addListener('browserFinished', reload) : null
    return () => {
      document.removeEventListener('visibilitychange', back)
      window.removeEventListener('focus', back)
      void finished?.then((h) => h.remove())
    }
  }, [reload])
}

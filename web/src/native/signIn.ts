import { Browser } from '@capacitor/browser'

/** Opens /oauth/authorize in the system's in-app browser (SFSafariViewController / Custom Tabs). */
export async function openSignInPage(url: string): Promise<void> {
  await Browser.open({ url, presentationStyle: 'popover' })
}

export async function closeSignInPage(): Promise<void> {
  try {
    await Browser.close()
  } catch {
    // Android closes Custom Tabs itself when the app takes the redirect.
  }
}

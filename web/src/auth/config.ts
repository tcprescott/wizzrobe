import { isNative } from '../lib/platform'

/**
 * Where the API and the authorization server live. Empty VITE_API_BASE means
 * same origin: the Vite dev proxy in development, and the deployed app when
 * it's served beside the API.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

/** The app's client id at our own OAuth server (a public client: PKCE, no secret). */
export const CLIENT_ID = import.meta.env.VITE_OAUTH_CLIENT_ID || 'wizzrobe-app'

/**
 * Where the browser goes to sign in. The sign-in pages are the backend's own
 * (Discord login, then straight back), so in dev this is the backend's origin
 * rather than the Vite proxy. Must match the server's BASE_URL: the issuer it
 * reports back is checked against it.
 */
export const AUTH_ORIGIN = (import.meta.env.VITE_AUTH_ORIGIN ?? '').replace(/\/$/, '')

export function issuer(): string {
  return `${AUTH_ORIGIN || API_BASE || window.location.origin}/oauth`
}

/**
 * The store apps run on capacitor://localhost or https://localhost, which the
 * browser doing the sign-in can't return to, so they come back on their own
 * app scheme (RFC 8252) and the native layer hands the URL over.
 */
export function redirectUri(): string {
  if (isNative()) return `${import.meta.env.VITE_APP_ID}:/auth/callback`
  return `${window.location.origin}/auth/callback`
}

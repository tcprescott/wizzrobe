/**
 * Where the API and the authorization server live. Empty VITE_API_BASE means
 * same origin: the Vite dev proxy in development, and the deployed app when
 * it's served beside the API.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

/** The app's client id at our own OAuth server (a public client: PKCE, no secret). */
export const CLIENT_ID = import.meta.env.VITE_OAUTH_CLIENT_ID || 'wizzrobe-app'

export function issuer(): string {
  return `${API_BASE || window.location.origin}/oauth`
}

export function redirectUri(): string {
  return `${window.location.origin}/auth/callback`
}

/**
 * The signed-in session: the token pair from /oauth/token, refreshed before it
 * runs out.
 *
 * The server rotates the refresh token on every use and treats a spent one as
 * stolen, revoking the whole grant. Two tabs refreshing at once would trip
 * that, so a refresh runs under a cross-tab Web Lock and re-reads storage
 * inside it: the second tab finds the first tab's fresh pair and uses it.
 *
 * Kept in localStorage for the web build; the store apps swap this module's
 * storage for the platform keychain.
 */
import { readStored, writeStored } from '../lib/storage'
import { API_BASE, CLIENT_ID, issuer, redirectUri } from './config'
import { challengeFor, createVerifier, randomToken } from './pkce'

const SESSION_KEY = 'wiz.session'
const PENDING_KEY = 'wiz.signin'
const REFRESH_EARLY_MS = 60_000

export interface Tokens {
  access: string
  refresh: string
  expiresAt: number
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  expires_in: number
}

interface PendingSignIn {
  verifier: string
  state: string
  returnTo: string
}

export class SignInError extends Error {}

type Listener = (signedIn: boolean) => void
const listeners = new Set<Listener>()

export function onSessionChange(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function notify(): void {
  const signedIn = loadTokens() !== null
  listeners.forEach((listener) => listener(signedIn))
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (event) => {
    if (event.key === SESSION_KEY) notify()
  })
}

export function loadTokens(): Tokens | null {
  const raw = readStored(SESSION_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Tokens
    return parsed.access && parsed.refresh ? parsed : null
  } catch {
    return null
  }
}

function saveTokens(tokens: Tokens | null): void {
  writeStored(SESSION_KEY, tokens ? JSON.stringify(tokens) : null)
  notify()
}

function fromResponse(body: TokenResponse): Tokens {
  return { access: body.access_token, refresh: body.refresh_token, expiresAt: Date.now() + body.expires_in * 1000 }
}

async function postToken(form: Record<string, string>): Promise<Response> {
  return fetch(`${API_BASE}/oauth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ ...form, client_id: CLIENT_ID }),
  })
}

export async function beginSignIn(returnTo: string): Promise<void> {
  const pending: PendingSignIn = { verifier: createVerifier(), state: randomToken(16), returnTo }
  writeStored(PENDING_KEY, JSON.stringify(pending), 'session')
  const url = new URL(`${issuer()}/authorize`)
  url.search = new URLSearchParams({
    response_type: 'code',
    client_id: CLIENT_ID,
    redirect_uri: redirectUri(),
    code_challenge: await challengeFor(pending.verifier),
    code_challenge_method: 'S256',
    state: pending.state,
  }).toString()
  window.location.assign(url.toString())
}

/** Finish the redirect back from /oauth/authorize. Returns where to go next. */
export async function completeSignIn(params: URLSearchParams): Promise<string> {
  const raw = readStored(PENDING_KEY, 'session')
  writeStored(PENDING_KEY, null, 'session')
  const pending = raw ? (JSON.parse(raw) as PendingSignIn) : null
  if (!pending || params.get('state') !== pending.state) {
    throw new SignInError("This sign-in didn't start here, or it expired. Try again.")
  }
  const iss = params.get('iss')
  if (iss !== null && iss !== issuer()) {
    throw new SignInError('This sign-in came back from the wrong server. Try again.')
  }
  const error = params.get('error')
  if (error) {
    throw new SignInError(
      error === 'access_denied' ? 'Sign-in was cancelled.' : params.get('error_description') || 'Sign-in failed.',
    )
  }
  const code = params.get('code')
  if (!code) throw new SignInError('Sign-in came back without a code. Try again.')

  const response = await postToken({
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri(),
    code_verifier: pending.verifier,
  })
  if (!response.ok) throw new SignInError("Sign-in couldn't be finished. Try again.")
  saveTokens(fromResponse((await response.json()) as TokenResponse))
  return pending.returnTo || '/'
}

async function withRefreshLock<T>(work: () => Promise<T>): Promise<T> {
  const locks = typeof navigator !== 'undefined' ? navigator.locks : undefined
  return locks ? locks.request('wiz.session.refresh', work) : work()
}

let inflight: Promise<Tokens | null> | null = null

/** Rotate the pair. `stale` is the access token the caller saw rejected or expiring. */
export function refreshTokens(stale: string): Promise<Tokens | null> {
  inflight ??= withRefreshLock(async () => {
    const current = loadTokens()
    if (!current) return null
    if (current.access !== stale && current.expiresAt - Date.now() > REFRESH_EARLY_MS) return current
    const response = await postToken({ grant_type: 'refresh_token', refresh_token: current.refresh })
    if (response.status === 400 || response.status === 401) {
      saveTokens(null)
      return null
    }
    if (!response.ok) throw new Error(`Refreshing the session failed (${response.status})`)
    const next = fromResponse((await response.json()) as TokenResponse)
    saveTokens(next)
    return next
  }).finally(() => {
    inflight = null
  })
  return inflight
}

/** A usable access token, refreshing first when it's about to run out. */
export async function accessToken(): Promise<string | null> {
  const tokens = loadTokens()
  if (!tokens) return null
  if (tokens.expiresAt - Date.now() > REFRESH_EARLY_MS) return tokens.access
  return (await refreshTokens(tokens.access))?.access ?? null
}

export async function signOut(): Promise<void> {
  const tokens = loadTokens()
  saveTokens(null)
  if (!tokens) return
  try {
    await fetch(`${API_BASE}/oauth/revoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ token: tokens.refresh, token_type_hint: 'refresh_token', client_id: CLIENT_ID }),
    })
  } catch {
    // Signed out locally either way; the grant expires on its own.
  }
}

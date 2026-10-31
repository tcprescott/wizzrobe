/**
 * The typed REST client. Types come from src/api/schema.d.ts, generated from
 * the backend's OpenAPI schema (`npm run gen:api`); CI fails when they drift.
 *
 * Every call carries the session's bearer token and the community it acts in
 * (X-Wizzrobe-Community): the app's one sign-in reaches every community the
 * person belongs to, and the server checks membership and roles per call.
 * A 401 refreshes the session once and retries.
 */
import createClient from 'openapi-fetch'
import { API_BASE } from '../auth/config'
import { accessToken, refreshTokens } from '../auth/session'
import type { components, paths } from './schema'

export const COMMUNITY_HEADER = 'X-Wizzrobe-Community'

let community: string | null = null

/** The shell sets this from the URL (/t/<slug>/…) before any screen calls the API. */
export function setCommunity(slug: string | null): void {
  community = slug
}

function withAuth(request: Request, token: string | null): Request {
  const next = new Request(request)
  if (token) next.headers.set('Authorization', `Bearer ${token}`)
  if (community && !next.headers.has(COMMUNITY_HEADER)) next.headers.set(COMMUNITY_HEADER, community)
  return next
}

export async function authedFetch(request: Request): Promise<Response> {
  const retry = request.clone()
  const token = await accessToken()
  const response = await fetch(withAuth(request, token))
  if (response.status !== 401 || !token) return response
  const fresh = await refreshTokens(token)
  return fresh ? fetch(withAuth(retry, fresh.access)) : response
}

export const api = createClient<paths>({ baseUrl: API_BASE, fetch: authedFetch })

export type Schemas = components['schemas']

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

/** The message a person should see for a failed call: the server's `detail` when it has one. */
export function errorMessage(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return detail
  if (status === 0) return "Couldn't reach Wizzrobe. Check your connection and try again."
  return 'Something went wrong. Try again.'
}

/** Unwrap an openapi-fetch result, throwing ApiError with a readable message. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.data !== undefined) return result.data
  throw new ApiError(errorMessage(result.error, result.response.status), result.response.status)
}

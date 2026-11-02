import { useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { API_BASE, AUTH_ORIGIN } from '../auth/config'

export type Block = Schemas['BlockOut']
export type Span = Schemas['SpanOut']
export type ArticleData = Schemas['Article']
export type ArticleSummary = Schemas['ArticleSummary']
export type Tournament = Schemas['HandbookTournamentOut']
export type { Schemas }

/**
 * Where the reading pages hang: `/t/<slug>` or `/t/<slug>/e/<event>`, read from
 * the URL rather than the community provider, so the same screens work for
 * someone who isn't signed in.
 */
export function useReadingBase(): { base: string; community: string; event?: string } {
  const { community = '', event } = useParams()
  return { base: event ? `/t/${community}/e/${event}` : `/t/${community}`, community, event }
}

function eventQuery(event: string | undefined): { event?: string } {
  return event ? { event } : {}
}

export function useHelpIndex(q: string) {
  const { community, event } = useReadingBase()
  return useQuery(`help@${community}/${event ?? ''}?${q}`, () =>
    api.GET('/api/help', { params: { query: { ...eventQuery(event), q: q || undefined } } }).then(unwrap),
  )
}

export function useHelpArticle(slug: string) {
  const { community, event } = useReadingBase()
  return useQuery(`help/${slug}@${community}/${event ?? ''}`, () =>
    api.GET('/api/help/{slug}', { params: { path: { slug }, query: eventQuery(event) } }).then(unwrap),
  )
}

export function useEventInfo() {
  const { community, event } = useReadingBase()
  return useQuery(`event-info@${community}/${event ?? ''}`, () =>
    api.GET('/api/event-info', { params: { query: eventQuery(event) } }).then(unwrap),
  )
}

export function useEventInfoArticle(slug: string) {
  const { community, event } = useReadingBase()
  return useQuery(`event-info/${slug}@${community}/${event ?? ''}`, () =>
    api.GET('/api/event-info/{slug}', { params: { path: { slug }, query: eventQuery(event) } }).then(unwrap),
  )
}

export const useChangelog = () => useQuery('changelog', () => api.GET('/api/changelog').then(unwrap))
export const useAbout = () => useQuery('about', () => api.GET('/api/about').then(unwrap))
export const usePrivacy = () => useQuery('about/privacy', () => api.GET('/api/about/privacy').then(unwrap))
export const useNotices = () => useQuery('about/notices', () => api.GET('/api/about/notices').then(unwrap))
export const useFacts = () => useQuery('facts', () => api.GET('/api/facts').then(unwrap))

export function loadNotice(key: string) {
  return api.GET('/api/about/notice', { params: { query: { key } } }).then(unwrap)
}

/** A file the server hosts (help screenshots under /static/help/), as a full URL. */
export function serverUrl(path: string): string {
  return `${API_BASE}${path}`
}

/** A page of the website, for links into parts of it the app doesn't have yet. */
export function websiteUrl(path: string, community: string): string {
  return `${AUTH_ORIGIN || API_BASE || window.location.origin}/t/${community}${path}`
}

/** The site paths an article may link to that are screens in the app, by their path under the base. */
const APP_PATHS = /^\/(help|event-info|changelog|about|facts)(\/|$|#)/

/** Where an article's site path goes: a screen in the app, or the website. */
export function resolveSitePath(href: string, base: string, community: string): { to: string } | { href: string } {
  if (APP_PATHS.test(href)) return { to: `${base}${href}` }
  return { href: websiteUrl(href, community) }
}

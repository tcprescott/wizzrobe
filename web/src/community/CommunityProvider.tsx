import { createContext, useContext, useEffect, useMemo, type ReactNode } from 'react'
import { useParams } from 'react-router-dom'
import { api, setCommunity, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { writeStored } from '../lib/storage'

export type MyCommunity = Schemas['MyCommunity']
export type MyEvent = Schemas['MyCommunityEvent']

export const LAST_COMMUNITY_KEY = 'wiz.community'

export function loadMyCommunities(): Promise<MyCommunity[]> {
  return api.GET('/api/me/communities').then(unwrap)
}

interface CommunityState {
  communities: MyCommunity[]
  community: MyCommunity
  event: MyEvent | undefined
  /** `/t/<slug>` or `/t/<slug>/e/<event>`: tab paths hang off this. */
  base: string
  hasWork: boolean
}

const CommunityContext = createContext<CommunityState | null>(null)

export function useCommunity(): CommunityState {
  const value = useContext(CommunityContext)
  if (!value) throw new Error('useCommunity needs a CommunityProvider')
  return value
}

interface ProviderProps {
  /** Rendered while loading, or when the slug isn't one of yours. */
  fallback: (state: { loading: boolean; error?: Error; reload: () => void }) => ReactNode
  children: ReactNode
}

/** Binds the community (and event) the URL names, for the API client and every screen. */
export function CommunityProvider({ fallback, children }: ProviderProps) {
  const { community: slug, event: eventSlug } = useParams()
  const { data, error, loading, reload } = useQuery('me/communities', loadMyCommunities)

  const community = data?.find((c) => c.slug === slug)
  setCommunity(community ? community.slug : null)

  useEffect(() => {
    if (community) writeStored(LAST_COMMUNITY_KEY, community.slug)
  }, [community])

  const value = useMemo<CommunityState | null>(() => {
    if (!data || !community) return null
    const event = eventSlug
      ? community.events.find((e) => e.slug === eventSlug)
      : community.events.find((e) => e.is_default)
    const base = eventSlug ? `/t/${community.slug}/e/${eventSlug}` : `/t/${community.slug}`
    return { communities: data, community, event, base, hasWork: community.roles.length > 0 }
  }, [data, community, eventSlug])

  if (!value) return <>{fallback({ loading: loading && !data, error, reload })}</>
  return <CommunityContext.Provider value={value}>{children}</CommunityContext.Provider>
}

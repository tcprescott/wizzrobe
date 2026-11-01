import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { useCommunity } from '../community/CommunityProvider'

export type Entry = Schemas['ScheduleEntryResponse']
export type Need = Schemas['NeedResponse']
export type Bookable = Schemas['BookableResponse']
export type Home = Schemas['HomeResponse']

/** `?event=` when the URL names an event; otherwise the server uses the community's default. */
function useEventQuery(): { event?: string } {
  const { base, event } = useCommunity()
  return base.includes('/e/') && event ? { event: event.slug } : {}
}

/** Reload when the person comes back to the tab: what's waiting on them changes while they're away. */
function useReloadOnReturn(reload: () => void): void {
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') reload()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [reload])
}

export function useHome() {
  const { community } = useCommunity()
  const query = useEventQuery()
  const result = useQuery(`me/home@${community.slug}/${query.event ?? ''}`, () =>
    api.GET('/api/me/home', { params: { query } }).then(unwrap),
  )
  useReloadOnReturn(result.reload)
  return result
}

export function useSchedule(past: boolean) {
  const { community } = useCommunity()
  const query = useEventQuery()
  const result = useQuery(`me/schedule@${community.slug}/${query.event ?? ''}/${past}`, () =>
    api.GET('/api/me/schedule', { params: { query: { ...query, past } } }).then(unwrap),
  )
  useReloadOnReturn(result.reload)
  return result
}

export function useEventParam(): { event?: string } {
  return useEventQuery()
}

/** The current minute, ticking, for countdowns. */
export function useNow(everyMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), everyMs)
    return () => window.clearInterval(id)
  }, [everyMs])
  return now
}

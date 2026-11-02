import { useEffect, useState } from 'react'
import { ApiError, errorMessage, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { useCommunity } from '../community/CommunityProvider'

export type Profile = Schemas['ProfileResponse']
export type Section = Schemas['ProfileSectionResponse']
export type Channels = Schemas['ChannelsResponse']

/** `?event=` when the URL names an event; otherwise the server uses the community's default. */
export function useEventQuery(): { event?: string } {
  const { base, event } = useCommunity()
  return base.includes('/e/') && event ? { event: event.slug } : {}
}

/** One section's read, keyed on the community so switching community reloads it. */
export function useSection<T>(name: string, load: () => Promise<T>) {
  const { community } = useCommunity()
  const { event } = useEventQuery()
  return useQuery(`me/${name}@${community.slug}/${event ?? ''}`, load)
}

/** For writes that answer 204: throw the server's message on failure, otherwise nothing. */
export function done(result: { error?: unknown; response: Response }): void {
  if (!result.response.ok) throw new ApiError(errorMessage(result.error, result.response.status), result.response.status)
}

export function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** Whether the layout is the phone one (below 768px), tracking resizes. */
export function usePhone(): boolean {
  const query = '(max-width: 767.98px)'
  const [phone, setPhone] = useState(() => typeof matchMedia === 'function' && matchMedia(query).matches)
  useEffect(() => {
    if (typeof matchMedia !== 'function') return
    const media = matchMedia(query)
    const on = () => setPhone(media.matches)
    media.addEventListener('change', on)
    return () => media.removeEventListener('change', on)
  }, [])
  return phone
}

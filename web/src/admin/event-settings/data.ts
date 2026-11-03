import { useEventQuery } from '../../me/data'
import { useAdminQuery } from '../kit'

/** `{ params: { query } }` naming the event the URL is in, for every event-settings call. */
export function useEventParams() {
  const query = useEventQuery()
  return { params: { query } }
}

/** One event setting's read, reloaded when the community or event changes. */
export function useEventSetting<T>(name: string, load: () => Promise<T>) {
  return useAdminQuery(`event-settings/${name}`, load)
}

/** `2027-10-21` as "Thu, Oct 21", read as a calendar day (no timezone shift). */
export function dayLabel(iso: string): string {
  const [y = 1970, m = 1, d = 1] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

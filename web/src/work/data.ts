import { useEffect } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { useCommunity } from '../community/CommunityProvider'
import { useEventQuery } from '../me/data'

export type WorkAreas = Schemas['WorkAreasResponse']
export type Desk = Schemas['DeskResponse']
export type DeskRow = Schemas['DeskRowResponse']
export type CheckInEvent = Schemas['CheckInEventResponse']
export type VolunteerSchedule = Schemas['VolunteerScheduleResponse']
export type GridShift = Schemas['GridShiftResponse']
export type GridRole = Schemas['GridRoleResponse']
export type Member = Schemas['DeskMemberResponse']

/** Live screens re-read this often until the live stream exists, and after every action. */
export const REFRESH_MS = 10_000

/** One Work read, keyed on the community and event so switching either reloads it. */
export function useWorkQuery<T>(name: string | null, load: () => Promise<T>) {
  const { community } = useCommunity()
  const { event } = useEventQuery()
  return useQuery(name === null ? null : `work/${name}@${community.slug}/${event ?? ''}`, load)
}

export function useWorkAreas() {
  const query = useEventQuery()
  return useWorkQuery('areas', () => api.GET('/api/work', { params: { query } }).then(unwrap))
}

/** Re-run `reload` every `ms` while the tab is visible, and as soon as it comes back. */
export function usePolling(reload: () => void, ms: number = REFRESH_MS): void {
  useEffect(() => {
    let id: number | undefined
    const start = () => {
      window.clearInterval(id)
      id = window.setInterval(reload, ms)
    }
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        reload()
        start()
      } else {
        window.clearInterval(id)
      }
    }
    start()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      window.clearInterval(id)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [reload, ms])
}

/** What WorkLayout hands each area screen through its Outlet. */
export interface WorkOutlet {
  areas: WorkAreas
  reloadAreas: () => void
}

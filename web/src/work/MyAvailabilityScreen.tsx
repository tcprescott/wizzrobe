import { useOutletContext } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { AvailabilityEditor, DECLARE_MODE, type Status } from '../availability/AvailabilityEditor'
import { PageHeader } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { effectiveZone } from '../lib/time'
import { useWorkQuery, type WorkOutlet } from './data'

/** A volunteer's own availability: the times they can work, which coordinators see when they fill shifts. */
export function MyAvailabilityScreen() {
  const { event } = useCommunity()
  const { areas, reloadAreas } = useOutletContext<WorkOutlet>()
  const { data, reload } = useWorkQuery('availability', () => api.GET('/api/volunteers/me/availability').then(unwrap))
  return (
    <>
      <PageHeader title="My availability" lede="When you can volunteer." />
      <AvailabilityEditor
        mode={DECLARE_MODE}
        zone={effectiveZone(areas.zone)}
        startsOn={event?.starts_on}
        endsOn={event?.ends_on}
        data={data?.map((w) => ({ ...w, status: w.status as Status }))}
        reload={() => {
          reload()
          reloadAreas()
        }}
        put={(windows) => api.PUT('/api/volunteers/me/availability', { body: { windows } }).then(unwrap)}
      />
    </>
  )
}

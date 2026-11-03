import { api, unwrap, type Schemas } from '../../api/client'
import { AvailabilityEditor, BLOCK_MODE, type Status } from '../../availability/AvailabilityEditor'
import { useCommunity } from '../../community/CommunityProvider'
import { effectiveZone } from '../../lib/time'
import { useSection } from '../data'
import { useProfile } from '../MeLayout'

type Window = Schemas['PlayerAvailabilityResponse']

/** When you can't play: blocked times opponents and staff see, drawn as one bar per event day. */
export function Availability() {
  const { profile } = useProfile()
  const { event } = useCommunity()
  const zone = effectiveZone(profile.zone_pinned ? profile.community_zone : profile.timezone)
  const { data, reload } = useSection('availability', () => api.GET('/api/users/me/availability').then(unwrap))
  return (
    <AvailabilityEditor
      mode={BLOCK_MODE}
      zone={zone}
      startsOn={event?.starts_on}
      endsOn={event?.ends_on}
      data={data?.map((w: Window) => ({ ...w, status: w.status as Status }))}
      reload={reload}
      put={(windows) => api.PUT('/api/users/me/availability', { body: { windows } }).then(unwrap)}
    />
  )
}

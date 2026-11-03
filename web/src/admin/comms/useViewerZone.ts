import { api, unwrap } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { useCommunity } from '../../community/CommunityProvider'
import { effectiveZone } from '../../lib/time'

/** The zone this viewer reads times in here: the community's pin, else their own, else the device's. */
export function useViewerZone(): string {
  const { community } = useCommunity()
  const { data } = useQuery(`me/profile@${community.slug}`, () => api.GET('/api/me/profile').then(unwrap))
  return effectiveZone(data?.zone_pinned ? data.community_zone : data?.timezone)
}

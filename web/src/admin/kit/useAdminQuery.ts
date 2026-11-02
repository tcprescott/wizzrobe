import { useQuery } from '../../api/useQuery'
import { useCommunity } from '../../community/CommunityProvider'

/** A read keyed on the community and event, so switching either reloads it. */
export function useAdminQuery<T>(name: string | null, load: () => Promise<T>) {
  const { community, event } = useCommunity()
  return useQuery(name === null ? null : `admin/${name}@${community.slug}/${event?.slug ?? ''}`, load)
}

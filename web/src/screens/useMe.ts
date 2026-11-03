import { api, unwrap } from '../api/client'
import { useQuery } from '../api/useQuery'
import { useCommunity } from '../community/CommunityProvider'

export function useMe() {
  const { community } = useCommunity()
  const query = useQuery(`users/me@${community.slug}`, () => api.GET('/api/users/me').then(unwrap))
  return { me: query.data, ...query }
}

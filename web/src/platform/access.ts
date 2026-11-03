import { api, unwrap } from '../api/client'
import { useQuery } from '../api/useQuery'

/** Whether the signed-in person is a super admin (so may open /platform), and their account id. */
export function usePlatformAccess() {
  return useQuery('me/platform', () => api.GET('/api/me/platform').then(unwrap))
}

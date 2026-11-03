import { useCommunity } from '../../community/CommunityProvider'
import { useQuery, type QueryState } from '../../api/useQuery'

/** A report's data, keyed by community and whatever the report's filters are. */
export function useReport<T>(
  parts: (string | number | boolean | null | undefined)[] | null,
  load: () => Promise<T>,
): QueryState<T> {
  const { community } = useCommunity()
  return useQuery(parts && `reports/${parts.map((p) => String(p ?? '')).join('/')}@${community.slug}`, load)
}

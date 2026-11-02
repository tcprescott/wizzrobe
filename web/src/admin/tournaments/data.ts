import { api, unwrap, type Schemas } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { useCommunity } from '../../community/CommunityProvider'

export type Board = Schemas['BoardResponse']
export type BoardRow = Schemas['BoardRowResponse']
export type Tournament = Schemas['TournamentResponse']
export type Player = Schemas['PlayerResponse']
export type Person = Schemas['PersonResponse']
export type EntrantList = Schemas['EntrantListResponse']
export type Entrant = Schemas['EntrantResponse']
export type RoundStage = Schemas['RoundScheduleStageResponse']
export type StartggSettings = Schemas['StartggSettingsResponse']

/** A read keyed on the community and event, so switching either reloads it. */
export function useAdminQuery<T>(name: string | null, load: () => Promise<T>) {
  const { community, event } = useCommunity()
  return useQuery(name === null ? null : `admin/${name}@${community.slug}/${event?.slug ?? ''}`, load)
}

export function useBoard() {
  return useAdminQuery('tournaments/board', () => api.GET('/api/admin/tournaments/board').then(unwrap))
}

export const SIGNUP_LABEL: Record<BoardRow['signup_state'], string> = {
  upcoming: 'Not open yet',
  open: 'Signups open',
  closed: 'Signups closed',
}

export const SIGNUP_TONE = { upcoming: 'pending', open: 'live', closed: 'neutral' } as const

export function matchLength(row: Pick<BoardRow, 'average_match_duration' | 'max_match_duration'>): string | null {
  const { average_match_duration: avg, max_match_duration: max } = row
  if (avg && max) return `${avg} min · max ${max}`
  if (avg) return `${avg} min`
  if (max) return `max ${max} min`
  return null
}

import { api, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { useCommunity } from '../community/CommunityProvider'
import { useEventParam } from '../player/data'

export type Row = Schemas['RunSheetRowResponse']
export type Stage = Schemas['StageCardResponse']
export type StreamMatch = Schemas['StreamMatchResponse']
export type TournamentCard = Schemas['TournamentCardResponse']
export type Bracket = Schemas['api__schemas__bracket_overview__TournamentBracketResponse']
export type Side = Schemas['SideResponse']

export function useHub() {
  const { community } = useCommunity()
  const query = useEventParam()
  return useQuery(`event/hub@${community.slug}/${query.event ?? ''}`, () =>
    api.GET('/api/event/hub', { params: { query } }).then(unwrap),
  )
}

export function useRunSheet(start: Date | null, end: Date | null) {
  const { community } = useCommunity()
  const query = useEventParam()
  const key = start && end ? `event/schedule@${community.slug}/${query.event ?? ''}/${start.toISOString()}` : null
  return useQuery(key, () =>
    api
      .GET('/api/event/schedule', {
        params: { query: { ...query, start: start!.toISOString(), end: end!.toISOString() } },
      })
      .then(unwrap),
  )
}

export function useWatch() {
  const { community } = useCommunity()
  const query = useEventParam()
  return useQuery(`event/watch@${community.slug}/${query.event ?? ''}`, () =>
    api.GET('/api/event/watch', { params: { query } }).then(unwrap),
  )
}

export function useBracket(tournamentId: number) {
  const { community } = useCommunity()
  const query = useEventParam()
  return useQuery(`event/bracket@${community.slug}/${query.event ?? ''}/${tournamentId}`, () =>
    api
      .GET('/api/tournaments/{tournament_id}/bracket', { params: { path: { tournament_id: tournamentId }, query } })
      .then(unwrap),
  )
}

/** `Sam Ruiz vs Alex Chen`, or the first two and a count for a race. */
export function sidesLabel(sides: Side[]): string {
  if (sides.length === 0) return 'TBD'
  if (sides.length <= 2) return sides.map((s) => s.name).join(' vs ')
  return `${sides[0]!.name} vs ${sides[1]!.name} +${sides.length - 2} more`
}

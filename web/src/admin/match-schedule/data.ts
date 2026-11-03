import { useEffect } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { useQuery } from '../../api/useQuery'
import { useCommunity } from '../../community/CommunityProvider'
import { deviceZone } from '../../lib/time'

export type Board = Schemas['BoardResponse']
export type BoardRow = Schemas['BoardRow']
export type Pane = Schemas['MatchPaneResponse']
export type CrewRole = Schemas['CrewRole']
export type Need = Schemas['Need']
export type RescheduleNeed = Schemas['RescheduleNeed']
export type Comment = Schemas['MatchCommentResponse']
export type CommentKind = Schemas['MatchCommentCreate']['kind']
export type MatchForm = Schemas['MatchFormResponse']
export type MatchFormSave = Schemas['MatchFormSave']
export type TimeCheck = Schemas['TimeCheck']
export type Unbooked = Schemas['UnbookedItem']
export interface BookingRef {
  source: string
  key: number
}

export interface BoardFilters {
  day: string | null
  tournament: number | null
  stage: number | null
  q: string
}

function useEventQuery(): { event?: string } {
  const { base, event } = useCommunity()
  return base.includes('/e/') && event ? { event: event.slug } : {}
}

function useReloadOnReturn(reload: () => void): void {
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') reload()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [reload])
}

/** `proctor` reads the same board from Proctor Station's routes: proctor groups, no crew. */
export type BoardSource = 'admin' | 'proctor'

export function useBoard(filters: BoardFilters, source: BoardSource = 'admin') {
  const { community } = useCommunity()
  const event = useEventQuery()
  const query = {
    ...event,
    tz: deviceZone(),
    ...(filters.day ? { day: filters.day } : {}),
    ...(filters.tournament ? { tournament_id: filters.tournament } : {}),
    ...(filters.stage ? { stage_id: filters.stage } : {}),
    ...(filters.q.trim() ? { q: filters.q.trim() } : {}),
  }
  const result = useQuery(`${source}/schedule@${community.slug}/${JSON.stringify(query)}`, () =>
    (source === 'proctor'
      ? api.GET('/api/work/proctor', { params: { query } })
      : api.GET('/api/admin/match-schedule', { params: { query } })
    ).then(unwrap),
  )
  useReloadOnReturn(result.reload)
  return result
}

export function usePane(matchId: number | null, source: BoardSource = 'admin') {
  const { community } = useCommunity()
  const event = useEventQuery()
  return useQuery(matchId === null ? null : `${source}/schedule/match@${community.slug}/${matchId}`, () => {
    const params = { path: { match_id: matchId as number }, query: { ...event, tz: deviceZone() } }
    return (
      source === 'proctor'
        ? api.GET('/api/work/proctor/matches/{match_id}', { params })
        : api.GET('/api/admin/match-schedule/matches/{match_id}', { params })
    ).then(unwrap)
  })
}

export function useComments(matchId: number) {
  const { community } = useCommunity()
  const event = useEventQuery()
  return useQuery(`admin/schedule/comments@${community.slug}/${matchId}`, () =>
    api
      .GET('/api/matches/{match_id}/comments', { params: { path: { match_id: matchId }, query: event } })
      .then(unwrap),
  )
}

export function useMatchForm(matchId: number | null) {
  const { community } = useCommunity()
  const event = useEventQuery()
  return useQuery(`admin/schedule/form@${community.slug}/${matchId ?? 'new'}`, () =>
    api
      .GET('/api/admin/match-schedule/form', { params: { query: { ...event, ...(matchId ? { match_id: matchId } : {}) } } })
      .then(unwrap),
  )
}

export function useUnbooked(enabled: boolean) {
  const { community } = useCommunity()
  const event = useEventQuery()
  const result = useQuery(enabled ? `admin/schedule/unbooked@${community.slug}/${event.event ?? ''}` : null, () =>
    api.GET('/api/admin/match-schedule/unbooked', { params: { query: event } }).then(unwrap),
  )
  useReloadOnReturn(result.reload)
  return result
}

/** Calls that take the event along, so a match made from /e/<slug> lands in that event. */
export function useScheduling() {
  const event = useEventQuery()
  return {
    create: (body: MatchFormSave) =>
      api.POST('/api/admin/match-schedule/matches', { params: { query: event }, body }).then(unwrap),
    update: (id: number, body: MatchFormSave) =>
      api
        .PUT('/api/admin/match-schedule/matches/{match_id}', { params: { path: { match_id: id }, query: event }, body })
        .then(unwrap),
    checkTime: (body: Schemas['TimeCheckRequest']) =>
      api.POST('/api/admin/match-schedule/check-time', { params: { query: event }, body }).then(unwrap),
    suggest: (body: Schemas['SuggestRequest']) =>
      api.POST('/api/admin/match-schedule/suggest-time', { params: { query: event }, body }).then(unwrap),
    book: (ref: BookingRef, scheduledAt: string, allowOutsideRound: boolean) =>
      api
        .POST('/api/admin/match-schedule/unbooked/{source}/{key}/book', {
          params: { path: ref, query: event },
          body: { scheduled_at: scheduledAt, allow_outside_round: allowOutsideRound },
        })
        .then(unwrap),
    link: (ref: BookingRef, matchId: number, ahead: boolean) =>
      api
        .POST('/api/admin/match-schedule/unbooked/{source}/{key}/link', {
          params: { path: ref, query: { ...event, ahead } },
          body: { match_id: matchId },
        })
        .then((r) => {
          if (!r.response.ok) unwrap(r)
        }),
  }
}

const CREW_TYPE: Record<string, string> = {
  commentators: 'commentator',
  trackers: 'tracker',
  broadcasters: 'broadcaster',
}

/** Every write the board and pane make, each a plain call the caller awaits. */
export const actions = {
  seat: (id: number) => api.POST('/api/matches/{match_id}/seat', { params: { path: { match_id: id } } }).then(unwrap),
  start: (id: number) => api.POST('/api/matches/{match_id}/start', { params: { path: { match_id: id } } }).then(unwrap),
  finish: (id: number) => api.POST('/api/matches/{match_id}/finish', { params: { path: { match_id: id } } }).then(unwrap),
  confirm: (id: number) => api.POST('/api/matches/{match_id}/confirm', { params: { path: { match_id: id } } }).then(unwrap),
  roll: (id: number) => api.POST('/api/matches/{match_id}/seed', { params: { path: { match_id: id } } }).then(unwrap),
  winner: (id: number, playerId: number) =>
    api
      .POST('/api/matches/{match_id}/result', { params: { path: { match_id: id } }, body: { winner_id: playerId } })
      .then(unwrap),
  stage: (id: number, stageId: number | null) =>
    api.POST('/api/matches/{match_id}/stage', { params: { path: { match_id: id } }, body: { stage_id: stageId } }).then(unwrap),
  stations: (id: number, assignments: Record<string, string | null>) =>
    api.POST('/api/matches/{match_id}/stations', { params: { path: { match_id: id } }, body: { assignments } }).then(unwrap),
  suggestStations: (id: number) =>
    api.POST('/api/matches/{match_id}/stations/suggest', { params: { path: { match_id: id } } }).then(unwrap),
  clearSeed: (id: number) =>
    api
      .PATCH('/api/matches/{match_id}', {
        params: { path: { match_id: id } },
        body: { clear_seed: true, clear_seated: false, clear_started: false, clear_finished: false, clear_confirmed: false },
      })
      .then(unwrap),
  rewind: (id: number, step: string) =>
    api.POST('/api/matches/{match_id}/rewind', { params: { path: { match_id: id } }, body: { to_step: step } }).then(unwrap),
  callOff: (id: number, reason: string) =>
    api.POST('/api/matches/{match_id}/cancel', { params: { path: { match_id: id } }, body: { reason } }).then((r) => {
      if (!r.response.ok) unwrap(r)
    }),
  remove: (id: number) =>
    api.DELETE('/api/matches/{match_id}', { params: { path: { match_id: id } } }).then((r) => {
      if (!r.response.ok) unwrap(r)
    }),
  crew: (role: string, crewId: number, approved: boolean) =>
    api
      .POST('/api/crew/{crew_type}/{crew_id}/approval', {
        params: { path: { crew_type: CREW_TYPE[role] ?? role, crew_id: crewId } },
        body: { approved },
      })
      .then(unwrap),
  comment: (id: number, body: string, kind: CommentKind) =>
    api
      .POST('/api/matches/{match_id}/comments', { params: { path: { match_id: id } }, body: { body, kind } })
      .then(unwrap),
  deleteComment: (commentId: number) =>
    api.DELETE('/api/match-comments/{comment_id}', { params: { path: { comment_id: commentId } } }).then((r) => {
      if (!r.response.ok) unwrap(r)
    }),
  approveRequest: (requestId: number, scheduledAt: string | null = null) =>
    api
      .POST('/api/reschedule-requests/{request_id}/approve', {
        params: { path: { request_id: requestId } },
        body: { scheduled_at: scheduledAt, note: null },
      })
      .then(unwrap),
  declineRequest: (requestId: number, note: string) =>
    api
      .POST('/api/reschedule-requests/{request_id}/decline', { params: { path: { request_id: requestId } }, body: { note } })
      .then(unwrap),
}

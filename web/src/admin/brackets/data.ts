import type { Schemas } from '../../api/client'
import type { Tone } from '../../components'

export type BracketsBoard = Schemas['BracketsBoardResponse']
export type StageRow = Schemas['BracketStageRowResponse']
export type StageDetail = Schemas['BracketStageDetailResponse']
export type SeedEntry = Schemas['BracketSeedEntryResponse']
export type RosterEntrant = Schemas['BracketRosterEntrantResponse']
export type StageMatch = Schemas['BracketStageMatchResponse']

export const STATE_TONE: Record<string, Tone> = {
  draft: 'neutral',
  active: 'live',
  complete: 'ok',
  cancelled: 'cancelled',
}

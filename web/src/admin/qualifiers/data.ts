import type { Schemas } from '../../api/client'
import type { Tone } from '../../components'

export type QualifierBoard = Schemas['QualifierBoardResponse']
export type QualifierRow = Schemas['QualifierRowResponse']
export type QualifierDetail = Schemas['QualifierDetailResponse']
export type QueueCard = Schemas['QualifierQueueCardResponse']
export type PoolLine = Schemas['QualifierPoolLineResponse']

export const STATE_TONE: Record<string, Tone> = {
  open: 'live',
  upcoming: 'pending',
  closed: 'neutral',
  off: 'cancelled',
}

/** `4325` → `1:12:05`. */
export function hms(seconds: number | null | undefined): string {
  if (seconds == null) return '–'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}:${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}

export function rowSub(r: QualifierRow): string {
  const review = r.to_review ? `${r.to_review} to review` : r.runs ? 'All reviewed' : 'No runs yet'
  return `${review} · ${r.runs} ${r.runs === 1 ? 'run' : 'runs'}`
}

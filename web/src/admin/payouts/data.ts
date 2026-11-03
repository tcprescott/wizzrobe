import type { Schemas } from '../../api/client'
import type { Tone } from '../../components'

export type PayoutBoard = Schemas['PayoutBoardResponse']
export type PayoutRow = Schemas['PayoutRowResponse']
export type PayoutDetail = Schemas['PayoutDetailResponse']

export const STATUS_TONE: Record<string, Tone> = { ready: 'ok', unnamed: 'pending', none: 'neutral' }

export type Unit = '%' | '$'

export interface DraftLine {
  key: string
  place: string
  unit: Unit
  share: string
  entrant_id: number | null
  note: string | null
}

export interface Draft {
  pool: string
  bonus: string
  lines: DraftLine[]
}

/** "40.00" → "40", "12.50" → "12.5": what a person would have typed. */
export function plain(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  const n = Number(value)
  return Number.isFinite(n) ? String(n) : String(value)
}

export function draftFrom(d: PayoutDetail): Draft {
  return {
    pool: d.pool == null ? '' : plain(d.pool),
    bonus: d.bonus == null ? '' : plain(d.bonus),
    lines: d.lines.map((l, i) => ({
      key: `saved-${i}`,
      place: String(l.place),
      unit: l.is_fixed ? '$' : '%',
      share: plain(l.is_fixed ? l.amount : l.percentage),
      entrant_id: l.entrant_id ?? null,
      note: l.note ?? null,
    })),
  }
}

/** A typed amount or share as a number, or null when blank or not a number. */
export function parseNumber(raw: string): number | null {
  const text = raw.trim().replace(/^\$/, '').replace(/%$/, '').replace(/,/g, '')
  if (!text) return null
  const n = Number(text)
  return Number.isFinite(n) ? n : null
}

/** Cents, rounded half to even like the server's `quantize`. */
function toCents(dollars: number): number {
  const raw = dollars * 100
  const floor = Math.floor(raw)
  const diff = raw - floor
  if (Math.abs(diff - 0.5) < 1e-9) return floor % 2 === 0 ? floor : floor + 1
  return Math.round(raw)
}

export function totalCents(draft: Draft): number {
  return toCents(parseNumber(draft.pool) ?? 0) + toCents(parseNumber(draft.bonus) ?? 0)
}

/** What one line pays, in cents, or null while its share isn't a number. */
export function lineCents(line: DraftLine, total: number): number | null {
  const n = parseNumber(line.share)
  if (n === null) return null
  return line.unit === '$' ? toCents(n) : toCents((total / 100) * (n / 100))
}

export function money(cents: number): string {
  return (cents / 100).toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return `${n}${s[(v - 20) % 10] ?? s[v] ?? s[0]}`
}

export interface Allocation {
  total: number
  placed: number
  percent: number
  fixed: number
}

export function allocation(draft: Draft): Allocation {
  const total = totalCents(draft)
  let placed = 0
  let percent = 0
  let fixed = 0
  for (const line of draft.lines) {
    const cents = lineCents(line, total) ?? 0
    placed += cents
    if (line.unit === '%') percent += parseNumber(line.share) ?? 0
    else fixed += cents
  }
  return { total, placed, percent, fixed }
}

/** The body for `PUT /tournaments/{id}/payouts`, or an error to show. */
export function splitBody(draft: Draft): { lines: Schemas['PayoutLineRequest'][] } | string {
  const lines: Schemas['PayoutLineRequest'][] = []
  for (const line of draft.lines) {
    const place = Number(line.place)
    if (!Number.isInteger(place) || place < 1) return 'Every place needs a number, 1 or higher.'
    const n = parseNumber(line.share)
    if (n === null || n <= 0) return `${ordinal(place)} place needs a share above zero.`
    lines.push({
      place,
      percentage: line.unit === '%' ? n.toFixed(2) : null,
      amount: line.unit === '$' ? n.toFixed(2) : null,
      entrant_id: line.entrant_id,
      note: line.note,
    })
  }
  return { lines }
}

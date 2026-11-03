import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { Schemas } from '../../api/client'
import { zonedInstant } from '../../lib/time'

export type ReportsMenu = Schemas['ReportsMenuResponse']

/** A local-date range, both ends inclusive, as `YYYY-MM-DD`. */
export interface DateRange {
  start: string
  end: string
}

const DAY = /^\d{4}-\d{2}-\d{2}$/

export function addDays(day: string, days: number): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y!, m! - 1, d! + days)).toISOString().slice(0, 10)
}

/** The range as UTC instants: midnight at the start, to midnight after the last day, on `zone`'s clock. */
export function rangeBounds(range: DateRange, zone: string): { start: string; end: string } {
  return {
    start: zonedInstant(range.start, '00:00', zone).toISOString(),
    end: zonedInstant(addDays(range.end, 1), '00:00', zone).toISOString(),
  }
}

/** The range in the address (`?from=&to=`), defaulting to the event window, so a link opens the same view. */
export function useRange(menu: ReportsMenu | undefined): [DateRange | null, (next: DateRange) => void] {
  const [params, setParams] = useSearchParams()
  const from = params.get('from')
  const to = params.get('to')
  const range =
    from && to && DAY.test(from) && DAY.test(to) && from <= to
      ? { start: from, end: to }
      : menu
        ? { start: menu.event_start, end: menu.event_end }
        : null
  const set = useCallback(
    (next: DateRange) =>
      setParams(
        (prev) => {
          const out = new URLSearchParams(prev)
          out.set('from', next.start)
          out.set('to', next.end)
          return out
        },
        { replace: true },
      ),
    [setParams],
  )
  return [range, set]
}

export function shortDay(day: string): string {
  const [y, m, d] = day.split('-').map(Number)
  return new Date(Date.UTC(y!, m! - 1, d!)).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}

export function rangeLabel(range: DateRange): string {
  return range.start === range.end ? shortDay(range.start) : `${shortDay(range.start)} – ${shortDay(range.end)}`
}

export const pct = (value: number | null | undefined) => (value == null ? '—' : `${Math.round(value)}%`)
export const mins = (value: number | null | undefined) => (value == null ? '—' : `${Math.round(value)} min`)
export const hours = (value: number | null | undefined) =>
  value == null ? '—' : `${value.toFixed(value < 100 && value % 1 ? 1 : 0)} h`

/** A number of minutes for a table cell whose header carries the unit. */
export const num = (value: number | null | undefined) => (value == null ? '—' : String(Math.round(value)))

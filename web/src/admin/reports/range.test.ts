import { describe, expect, it } from 'vitest'
import { addDays, hours, pct, rangeBounds } from './range'

describe('reports range', () => {
  it('adds days across a month end', () => {
    expect(addDays('2026-10-31', 1)).toBe('2026-11-01')
    expect(addDays('2026-03-01', -1)).toBe('2026-02-28')
  })
  it('reads the range on the given clock, to midnight after the last day', () => {
    expect(rangeBounds({ start: '2026-10-24', end: '2026-10-24' }, 'America/New_York')).toEqual({
      start: '2026-10-24T04:00:00.000Z',
      end: '2026-10-25T04:00:00.000Z',
    })
  })
  it('crosses the DST change with the day still 24 wall-clock hours', () => {
    const b = rangeBounds({ start: '2026-11-01', end: '2026-11-01' }, 'America/New_York')
    expect(b.end).toBe('2026-11-02T05:00:00.000Z')
  })
  it('formats missing numbers as a dash', () => {
    expect(pct(null)).toBe('—')
    expect(pct(78.5)).toBe('79%')
    expect(hours(7.5)).toBe('7.5 h')
    expect(hours(20.5)).toBe('20.5 h')
    expect(hours(208)).toBe('208 h')
  })
})

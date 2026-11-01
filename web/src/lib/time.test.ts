import { describe, expect, it } from 'vitest'
import { countdown, dayKey, wallClock, zonedInstant } from './time'

describe('zonedInstant', () => {
  it('reads a wall time in the named zone', () => {
    expect(zonedInstant('2026-11-06', '13:30', 'America/New_York').toISOString()).toBe('2026-11-06T18:30:00.000Z')
    expect(zonedInstant('2026-07-06', '13:30', 'America/New_York').toISOString()).toBe('2026-07-06T17:30:00.000Z')
    expect(zonedInstant('2026-11-06', '09:00', 'UTC').toISOString()).toBe('2026-11-06T09:00:00.000Z')
  })

  it('round-trips with wallClock', () => {
    const at = zonedInstant('2026-03-08', '10:15', 'America/Los_Angeles').toISOString()
    expect(wallClock(at, 'America/Los_Angeles')).toEqual({ date: '2026-03-08', time: '10:15' })
  })
})

describe('dayKey', () => {
  it('uses the zone, not UTC', () => {
    expect(dayKey('2026-11-07T02:00:00Z', 'America/New_York')).toBe('2026-11-06')
    expect(dayKey('2026-11-07T02:00:00Z', 'UTC')).toBe('2026-11-07')
  })
})

describe('countdown', () => {
  const now = new Date('2026-11-06T12:00:00Z')
  it('says minutes, then hours', () => {
    expect(countdown('2026-11-06T12:42:00Z', now)).toBe('42 min')
    expect(countdown('2026-11-06T14:05:00Z', now)).toBe('2 h 5 min')
    expect(countdown('2026-11-06T11:00:00Z', now)).toBe('now')
    expect(countdown('2026-11-08T12:00:00Z', now)).toBeNull()
  })
})

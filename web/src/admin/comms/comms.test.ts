import { describe, expect, it } from 'vitest'
import { safePath } from '../feedback/FeedbackScreen'
import { summary } from '../notifications/NotificationsScreen'

describe('safePath', () => {
  it('links only paths on this site', () => {
    expect(safePath('/t/sgl/home')).toBe('/t/sgl/home')
    expect(safePath('//evil.example/x')).toBeNull()
    expect(safePath('https://evil.example')).toBeNull()
    expect(safePath('/\\evil.example')).toBeNull()
    expect(safePath('')).toBeNull()
  })
})

describe('summary', () => {
  it('names each recipient and folds staff into its choice', () => {
    const cell = (column: string, label: string, mode: 'off' | 'silent' | 'ping', staff_label = '') => ({
      column, label, who: '', mode, default: mode, changed: false, staff_label, staff_options: [], staff_mix: '',
    })
    const message = {
      notice: 'x', title: 'X', when: '', changed: false,
      cells: [cell('players', 'Players', 'ping'), cell('watchers', 'Watchers', 'off'), cell('staff_alerts', 'Staff', 'ping', 'Ping on-duty')],
    }
    expect(summary(message)).toBe('Players ping · Watchers off · Staff ping on-duty')
  })
})

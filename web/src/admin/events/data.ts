import type { Schemas } from '../../api/client'
import type { Tone } from '../../components'

export type EventRow = Schemas['EventResponse']

export const STATUS: Record<EventRow['status'], { label: string; tone: Tone }> = {
  live: { label: 'Live', tone: 'ok' },
  draft: { label: 'Draft', tone: 'info' },
  archived: { label: 'Archived', tone: 'pending' },
}

export const COPY_GROUPS: { key: string; label: string; hint: string }[] = [
  {
    key: 'tournaments',
    label: 'Tournaments',
    hint: 'Names, formats, presets, Discord role mappings. No players, brackets, round schedules or Challonge/start.gg links.',
  },
  { key: 'positions', label: 'Volunteer positions', hint: 'With who is qualified for each, and the hour reward tiers.' },
  { key: 'stages', label: 'Stages and stations', hint: 'With how stations are numbered.' },
  { key: 'check_in', label: 'Check-in tiers', hint: 'Badge tiers and comp rules. No attendees.' },
  { key: 'discord_roles', label: 'Discord roles from Wizzrobe', hint: 'The rules, not the roles already given.' },
]

const MONTH = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' })

function parts(iso: string) {
  const d = new Date(`${iso}T00:00:00Z`)
  return { d, month: MONTH.format(d), day: d.getUTCDate(), year: d.getUTCFullYear() }
}

/** "Oct 22 – 25, 2026", "Oct 30 – Nov 2, 2026", "Dec 30, 2026 – Jan 2, 2027", or "" (dates are calendar days, no zone). */
export function datesLabel(starts?: string | null, ends?: string | null): string {
  if (!starts && !ends) return ''
  if (!starts || !ends || starts === ends) {
    const p = parts((starts || ends) as string)
    return `${p.month} ${p.day}, ${p.year}`
  }
  const a = parts(starts)
  const b = parts(ends)
  if (a.year !== b.year) return `${a.month} ${a.day}, ${a.year} – ${b.month} ${b.day}, ${b.year}`
  if (a.month !== b.month) return `${a.month} ${a.day} – ${b.month} ${b.day}, ${b.year}`
  return `${a.month} ${a.day} – ${b.day}, ${b.year}`
}

export const count = (n: number, one: string, many: string) => `${n.toLocaleString()} ${n === 1 ? one : many}`

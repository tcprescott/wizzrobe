import type { IconName } from '../components'
import { formatTime } from '../lib/time'
import type { WorkAreas } from './data'

/**
 * The Work tab's areas, in list order. Each says when the viewer has it and
 * the live line under its name. A new Work screen adds itself here; Admin
 * keeps its own /admin URLs, so its entry links out of Work's split view.
 */
export interface WorkArea {
  key: string
  label: string
  icon: IconName
  /** Path under the community base: `work/<key>`, or `admin` for Admin. */
  path: string
  shown: (areas: WorkAreas, ctx: AreaContext) => boolean
  sub?: (areas: WorkAreas, ctx: AreaContext) => string | undefined
}

export interface AreaContext {
  zone: string
  hasAdmin: boolean
  now: Date
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export const WORK_AREAS: WorkArea[] = [
  {
    key: 'desk',
    label: 'Check-in desk',
    icon: 'how_to_reg',
    path: 'work/desk',
    shown: (a) => a.desk != null,
    sub: (a) => {
      const desk = a.desk
      if (!desk) return undefined
      if (desk.open_events === 0) return 'No event open for check-in'
      if (desk.open_events > 1) return `${desk.open_events} events open`
      return `${desk.checked_in} of ${desk.expected} checked in`
    },
  },
  {
    key: 'schedule',
    label: 'Volunteer schedule',
    icon: 'calendar_view_week',
    path: 'work/schedule',
    shown: (a) => a.schedule != null,
    sub: (a, ctx) => {
      const s = a.schedule
      if (!s) return undefined
      const now = `${s.on_shift_now} on shift now`
      return s.my_next_shift_at ? `${now} · you're on at ${formatTime(s.my_next_shift_at, ctx.zone)}` : now
    },
  },
  {
    key: 'availability',
    label: 'My availability',
    icon: 'edit_calendar',
    path: 'work/availability',
    shown: (a) => a.availability != null,
    sub: (a) => (a.availability ? (a.availability.windows ? plural(a.availability.windows, 'window') : 'Not set yet') : undefined),
  },
  {
    key: 'proctor',
    label: 'Proctor Station',
    icon: 'sports_esports',
    path: 'work/proctor',
    shown: (a) => a.proctor,
    sub: () => 'Check in, start and finish matches',
  },
  {
    key: 'admin',
    label: 'Admin',
    icon: 'admin_panel_settings',
    path: 'admin',
    shown: (_, ctx) => ctx.hasAdmin,
    sub: () => 'Run the community and its events',
  },
]

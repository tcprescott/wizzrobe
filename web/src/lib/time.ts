/**
 * Times arrive from the API as UTC instants. The server says which zone to
 * print them in (a community can pin one, a person can choose one); null
 * means the device's own. These helpers are the only place the app turns an
 * instant into words, so every screen agrees.
 */

export function deviceZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export function effectiveZone(zone: string | null | undefined): string {
  return zone || deviceZone()
}

function parts(at: Date, zone: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const p of new Intl.DateTimeFormat('en-US', {
    timeZone: zone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(at)) {
    out[p.type] = p.value
  }
  return out
}

/** `2026-11-06`, the calendar day `iso` falls on in `zone`. */
export function dayKey(iso: string, zone: string): string {
  const p = parts(new Date(iso), zone)
  return `${p.year}-${p.month}-${p.day}`
}

/** `1:30 PM` (or `13:30`, following the device's locale). */
export function formatTime(iso: string, zone: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone: zone, hour: 'numeric', minute: '2-digit' }).format(new Date(iso))
}

/** `Fri Nov 6`. */
export function formatDay(iso: string, zone: string): string {
  return new Intl.DateTimeFormat(undefined, { timeZone: zone, weekday: 'short', month: 'short', day: 'numeric' }).format(
    new Date(iso),
  )
}

/** `Fri Nov 6, 1:30 PM`. */
export function formatDayTime(iso: string, zone: string): string {
  return `${formatDay(iso, zone)}, ${formatTime(iso, zone)}`
}

/** `Today`, `Tomorrow`, or the day. */
export function dayLabel(iso: string, zone: string, now: Date = new Date()): string {
  const key = dayKey(iso, zone)
  if (key === dayKey(now.toISOString(), zone)) return 'Today'
  const tomorrow = new Date(now.getTime() + 24 * 3600 * 1000)
  if (key === dayKey(tomorrow.toISOString(), zone)) return 'Tomorrow'
  return formatDay(iso, zone)
}

/** `42 min`, `2 h 5 min`, `now`: how long until `iso`. Null once it's more than a day away. */
export function countdown(iso: string, now: Date = new Date()): string | null {
  const minutes = Math.round((new Date(iso).getTime() - now.getTime()) / 60000)
  if (minutes <= 0) return 'now'
  if (minutes < 60) return `${minutes} min`
  if (minutes >= 24 * 60) return null
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h} h ${m} min` : `${h} h`
}

/**
 * The instant a wall-clock `date` + `time` names in `zone`. Two passes settle
 * the zone's offset at that moment, so it holds across a DST change.
 */
export function zonedInstant(date: string, time: string, zone: string): Date {
  const [y = 1970, mo = 1, d = 1] = date.split('-').map(Number)
  const [h = 0, mi = 0] = time.split(':').map(Number)
  const wall = Date.UTC(y, mo - 1, d, h, mi)
  let guess = wall
  for (let i = 0; i < 2; i++) {
    const p = parts(new Date(guess), zone)
    const seen = Date.UTC(Number(p.year), Number(p.month) - 1, Number(p.day), Number(p.hour), Number(p.minute))
    guess += wall - seen
  }
  return new Date(guess)
}

/** The `YYYY-MM-DD` and `HH:MM` a date and a time input should show for `iso` in `zone`. */
export function wallClock(iso: string, zone: string): { date: string; time: string } {
  const p = parts(new Date(iso), zone)
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` }
}

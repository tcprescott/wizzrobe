import { useCallback, useEffect, useMemo, useState } from 'react'
import { Banner, Button, EmptyState, PageHeader, Panel, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { dayKey, effectiveZone, zonedInstant } from '../lib/time'
import { useFlash } from '../player/HomeScreen'
import { BackToEvent } from './BackLink'
import { sidesLabel, useRunSheet, type Row } from './data'
import { RunSheetRow } from './parts'
import './event.css'

function eventDays(startsOn?: string | null, endsOn?: string | null): string[] {
  if (!startsOn || !endsOn) return []
  const out: string[] = []
  const d = new Date(`${startsOn}T00:00:00Z`)
  const end = new Date(`${endsOn}T00:00:00Z`)
  while (d <= end && out.length < 14) {
    out.push(d.toISOString().slice(0, 10))
    d.setUTCDate(d.getUTCDate() + 1)
  }
  return out
}

function nextDay(day: string): string {
  const d = new Date(`${day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + 1)
  return d.toISOString().slice(0, 10)
}

function dayName(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', timeZone: 'UTC' })
}

type Toggle = 'crew' | 'streamed' | 'watching'
const TOGGLES: { key: Toggle; label: string }[] = [
  { key: 'crew', label: 'Needs crew' },
  { key: 'streamed', label: 'Streamed' },
  { key: 'watching', label: 'Watching' },
]

function keep(row: Row, on: Set<Toggle>, tournament: string, search: string): boolean {
  if (on.has('crew') && !row.crew.some((c) => c.filled < c.needed)) return false
  if (on.has('streamed') && !row.streamed) return false
  if (on.has('watching') && !row.watching) return false
  if (tournament && String(row.tournament_id) !== tournament) return false
  if (search) {
    const hay = `${row.tournament} ${row.round} ${sidesLabel(row.sides)} ${row.stage}`.toLowerCase()
    if (!hay.includes(search.toLowerCase())) return false
  }
  return true
}

/** Every match in the event, one day at a time, with crew sign-up from the row. */
export function RunSheetScreen() {
  const { event } = useCommunity()
  const [zoneHint, setZoneHint] = useState<string | null | undefined>(undefined)
  const zone = effectiveZone(zoneHint)
  const days = useMemo(() => eventDays(event?.starts_on, event?.ends_on), [event?.starts_on, event?.ends_on])
  const today = dayKey(new Date().toISOString(), zone)
  const [day, setDay] = useState<string>(() => (days.includes(today) ? today : (days[0] ?? today)))
  const start = useMemo(() => zonedInstant(day, '00:00', zone), [day, zone])
  const end = useMemo(() => zonedInstant(nextDay(day), '00:00', zone), [day, zone])
  const { data, error, loading, reload } = useRunSheet(start, end)
  const [on, setOn] = useState<Set<Toggle>>(new Set())
  const [tournament, setTournament] = useState('')
  const [search, setSearch] = useState('')
  const [flash, setFlash] = useFlash()
  const done = useCallback(
    (msg: string) => {
      setFlash(msg)
      reload()
    },
    [reload, setFlash],
  )

  useEffect(() => {
    if (data && data.zone !== zoneHint) setZoneHint(data.zone)
  }, [data, zoneHint])

  const rows = data?.rows ?? []
  const tournaments = [...new Map(rows.filter((r) => r.tournament_id != null).map((r) => [r.tournament_id!, r.tournament])).entries()].sort(
    (a, b) => a[1].localeCompare(b[1]),
  )
  const shown = rows.filter((r) => keep(r, on, tournament, search))

  return (
    <>
      <BackToEvent />
      <PageHeader title="Schedule" lede={`Every match at ${event?.name ?? 'this event'}.`} />
      {flash && <Banner tone="info">{flash}</Banner>}
      <div className="rs-toolbar">
        {days.length > 1 && (
          <div className="segmented" role="radiogroup" aria-label="Day">
            {days.map((d) => (
              <span key={d}>
                <input type="radio" id={`day-${d}`} name="rs-day" checked={day === d} onChange={() => setDay(d)} />
                <label htmlFor={`day-${d}`}>{dayName(d)}</label>
              </span>
            ))}
          </div>
        )}
        <label className="rs-search">
          <span className="visually-hidden">Find a player or tournament</span>
          <input
            className="input"
            type="search"
            placeholder="Player or tournament"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>
      <div className="rs-filters">
        <div className="filter-chips" role="group" aria-label="Show only">
          {TOGGLES.map((t) => (
            <span key={t.key}>
              <input
                type="checkbox"
                id={`rs-${t.key}`}
                checked={on.has(t.key)}
                onChange={(e) => {
                  const next = new Set(on)
                  if (e.target.checked) next.add(t.key)
                  else next.delete(t.key)
                  setOn(next)
                }}
              />
              <label htmlFor={`rs-${t.key}`}>{t.label}</label>
            </span>
          ))}
        </div>
        {tournaments.length > 1 && (
          <label className="rs-select">
            <span className="visually-hidden">Tournament</span>
            <select className="input" value={tournament} onChange={(e) => setTournament(e.target.value)}>
              <option value="">All tournaments</option>
              {tournaments.map(([id, name]) => (
                <option key={id} value={String(id)}>
                  {name}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {error && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading the schedule" />}
      {data &&
        (shown.length === 0 ? (
          <Panel>
            <EmptyState icon="calendar_month" title={rows.length ? 'No matches match those filters' : 'No matches this day'} />
          </Panel>
        ) : (
          <section className="panel" aria-label={`Matches on ${dayName(day)}`}>
            {shown.map((row) => (
              <RunSheetRow key={row.match_id} row={row} zone={zone} onDone={done} />
            ))}
          </section>
        ))}
    </>
  )
}

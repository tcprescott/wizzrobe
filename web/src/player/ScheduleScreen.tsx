import { useCallback, useState } from 'react'
import { Banner, Button, EmptyState, PageHeader, Panel, Spinner } from '../components'
import { dayKey, dayLabel, effectiveZone } from '../lib/time'
import { useNow, useSchedule, type Entry } from './data'
import { useFlash } from './HomeScreen'
import { ScheduleRow } from './parts'
import './player.css'

type Kind = 'all' | 'match' | 'crew' | 'shift'

const FILTERS: { value: Kind; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'match', label: 'Matches' },
  { value: 'crew', label: 'Crew' },
  { value: 'shift', label: 'Shifts' },
]

function byDay(entries: Entry[], zone: string): [string, Entry[]][] {
  const days = new Map<string, Entry[]>()
  for (const entry of entries) {
    const key = dayKey(entry.starts_at, zone)
    days.set(key, [...(days.get(key) ?? []), entry])
  }
  return [...days.entries()]
}

/** Everything of yours in this event, one run sheet grouped by day. */
export function ScheduleScreen() {
  const [past, setPast] = useState(false)
  const [kind, setKind] = useState<Kind>('all')
  const { data, error, loading, reload } = useSchedule(past)
  const now = useNow(60_000)
  const [flash, setFlash] = useFlash()
  const done = useCallback(
    (message: string) => {
      setFlash(message)
      reload()
    },
    [reload, setFlash],
  )

  const zone = effectiveZone(data?.zone)
  const items = data?.items ?? []
  const counts = { all: items.length, match: 0, crew: 0, shift: 0 } as Record<Kind, number>
  for (const item of items) counts[item.kind as Kind] = (counts[item.kind as Kind] ?? 0) + 1
  const shown = kind === 'all' ? items : items.filter((i) => i.kind === kind)
  const filters = FILTERS.filter((f) => f.value === 'all' || counts[f.value] > 0)

  return (
    <>
      <PageHeader title="My schedule" lede="Your matches, crew slots and shifts." />
      {flash && <Banner tone="info">{flash}</Banner>}
      <div className="sched-toolbar">
        <div className="segmented" role="radiogroup" aria-label="Which part of the event">
          {[
            { value: false, label: 'Upcoming' },
            { value: true, label: 'Past' },
          ].map((opt) => (
            <span key={opt.label}>
              <input
                type="radio"
                id={`when-${opt.label}`}
                name="when"
                checked={past === opt.value}
                onChange={() => setPast(opt.value)}
              />
              <label htmlFor={`when-${opt.label}`}>{opt.label}</label>
            </span>
          ))}
        </div>
        {filters.length > 2 && (
          <div className="filter-chips" role="radiogroup" aria-label="Show">
            {filters.map((f) => (
              <span key={f.value}>
                <input
                  type="radio"
                  id={`kind-${f.value}`}
                  name="kind"
                  checked={kind === f.value}
                  onChange={() => setKind(f.value)}
                />
                <label htmlFor={`kind-${f.value}`}>
                  {f.label}
                  {f.value !== 'all' && <span className="filter-chips__n">{counts[f.value]}</span>}
                </label>
              </span>
            ))}
          </div>
        )}
      </div>
      {error && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading your schedule" />}
      {data && shown.length === 0 && (
        <Panel>
          <EmptyState icon={past ? 'history' : 'calendar_month'} title={past ? 'Nothing here yet' : 'Nothing coming up'}>
            {past
              ? 'Matches, crew slots and shifts move here once they end.'
              : 'Matches you play, crew slots you take on and volunteer shifts show here once they have a time.'}
          </EmptyState>
        </Panel>
      )}
      {shown.length > 0 && (
        <section className="panel">
          {byDay(shown, zone).map(([key, entries]) => (
            <div key={key} role="group" aria-labelledby={`day-${key}`}>
              <h2 className="sched-day" id={`day-${key}`}>
                {dayLabel(entries[0]?.starts_at ?? key, zone, now)}
              </h2>
              {entries.map((entry) => (
                <ScheduleRow
                  key={`${entry.kind}-${entry.match_id ?? entry.assignment_id}-${entry.starts_at}`}
                  entry={entry}
                  zone={zone}
                  onDone={done}
                />
              ))}
            </div>
          ))}
        </section>
      )}
    </>
  )
}

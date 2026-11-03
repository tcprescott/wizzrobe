import { useEffect, useState } from 'react'
import { NavLink, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ActionsMenu, Banner, Button, EmptyState, Icon, PageHeader, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { dayKey, effectiveZone } from '../lib/time'
import { usePhone } from '../lib/usePhone'
import { actions, useBoard, type BoardRow } from '../admin/match-schedule/data'
import { DaySwitch, Row, useFilters, useFolded, matchupText } from '../admin/match-schedule/MatchScheduleScreen'
import { MatchPane } from '../admin/match-schedule/MatchPane'
import { usePolling } from './data'
import '../admin/match-schedule/schedule.css'

const ALL = 'all'

const STEP_ICON = { seat: 'how_to_reg', roll: 'casino', start: 'play_arrow', finish: 'check' } as const
type StepEvent = keyof typeof STEP_ICON

const CALL: Record<StepEvent, (id: number) => Promise<unknown>> = {
  seat: actions.seat,
  roll: actions.roll,
  start: actions.start,
  finish: actions.finish,
}

/** The line's one press: the next lifecycle step, or the two "won" buttons while a head-to-head is live. */
function StepButtons({ row, onDone }: { row: BoardRow; onDone: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!row.can.run) return null

  async function run(key: string, call: () => Promise<unknown>) {
    setBusy(key)
    setError(null)
    try {
      await call()
      onDone()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const winners = row.winners ?? []
  const step = row.next_step
  let buttons = null
  if (row.pending_result) {
    buttons = <span className="ps-step__note">{row.pending_result}</span>
  } else if (winners.length > 0) {
    buttons = (
      <span role="group" aria-label={`Who won ${matchupText(row)}?`} className="ps-step__group">
        {winners.map((w) => (
          <Button
            key={w.id}
            size="sm"
            variant="outline"
            busy={busy === `win-${w.id}`}
            onClick={() => run(`win-${w.id}`, () => actions.winner(row.id, w.id))}
          >
            {w.name} won
          </Button>
        ))}
      </span>
    )
  } else if (step?.rolling) {
    buttons = <span className="ps-step__note">Rolling the seed…</span>
  } else if (step?.event && step.event in CALL) {
    const event = step.event as StepEvent
    buttons = (
      <Button
        size="sm"
        variant={step.primary ? 'primary' : 'outline'}
        icon={STEP_ICON[event]}
        busy={busy === event}
        aria-label={`${step.label}: ${matchupText(row)}`}
        onClick={() => run(event, () => CALL[event](row.id))}
      >
        {step.label}
      </Button>
    )
  }
  if (!buttons && !error) return null
  return (
    <div className="ps-step">
      {buttons}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

/**
 * Proctor Station, Thomas's layout A: the admin Match Schedule's list beside the
 * match, grouped by what each match needs from a proctor next, with that step as
 * a button on its line. No crew and no editing. On a phone a match opens as its
 * own screen with a back link.
 */
export function ProctorScreen() {
  const { base } = useCommunity()
  const { match } = useParams()
  const matchId = Number(match) || null
  const phone = usePhone()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [filters, setFilters] = useFilters()
  const [search, setSearch] = useState(filters.q)
  const [folded, setFolded] = useFolded('wiz.proctor-station.folded')
  const today = dayKey(new Date().toISOString(), effectiveZone(null))
  const day = filters.day ?? today
  const { data, error, loading, reload } = useBoard({ ...filters, day: day === ALL ? null : day }, 'proctor')
  const [rev, setRev] = useState(0)
  usePolling(reload)
  const zone = effectiveZone(data?.zone)

  useEffect(() => {
    const id = window.setTimeout(() => {
      if (search !== filters.q) setFilters({ q: search })
    }, 300)
    return () => window.clearTimeout(id)
  }, [search, filters.q, setFilters])

  const query = params.toString()
  const home = `${base}/work/proctor`
  const withQuery = (path: string) => `${path}${query ? `?${query}` : ''}`
  const backTo = withQuery(home)
  const count = data?.groups.reduce((n, g) => n + g.rows.length, 0) ?? 0
  const changed = () => {
    reload()
    setRev((r) => r + 1)
  }

  const pane = (standalone: boolean) =>
    matchId ? (
      <MatchPane
        key={`${matchId}-${rev}`}
        matchId={matchId}
        board={data}
        onChanged={reload}
        onGone={() => navigate(backTo)}
        closeTo={standalone ? undefined : backTo}
        standalone={standalone}
        station
      />
    ) : null

  if (phone && matchId) {
    return (
      <>
        <NavLink to={backTo} className="ms-back">
          <Icon name="chevron_left" size={20} />
          Proctor Station
        </NavLink>
        {pane(true)}
      </>
    )
  }

  const isFolded = (label: string, byDefault: boolean) => folded[label] ?? byDefault

  return (
    <>
      <PageHeader
        title="Proctor Station"
        lede="Every match in this room, grouped by what it needs from you next."
        actions={<ActionsMenu items={[{ label: 'Refresh', icon: 'restart_alt', onSelect: changed }]} />}
      />
      {data && (
        <div className="ms-filters">
          <DaySwitch board={data} day={day} zone={zone} onDay={(d) => setFilters({ day: d === today ? null : d })} />
          <select
            className="input ms-select"
            aria-label="Tournament"
            value={filters.tournament ?? ''}
            onChange={(e) => setFilters({ tournament: Number(e.target.value) || null })}
          >
            <option value="">All tournaments</option>
            {data.tournaments.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
          {data.stages.length > 0 && (
            <select
              className="input ms-select"
              aria-label="Stage"
              value={filters.stage ?? ''}
              onChange={(e) => setFilters({ stage: Number(e.target.value) || null })}
            >
              <option value="">All stages</option>
              {data.stages.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          )}
          <label className="ms-search">
            <Icon name="search" size={18} />
            <input
              type="search"
              className="ms-search__input"
              placeholder="Player, tournament, stage"
              aria-label="Search matches"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </label>
        </div>
      )}
      {error && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading Proctor Station" />}
      {data && (
        <div className={phone ? 'ms-phone' : 'ms-split'}>
          <section className="ms-list" aria-label={`Matches, ${count}`}>
            {count === 0 && (
              <EmptyState icon="sports_esports" title="No matches here">
                {filters.q || filters.tournament || filters.stage
                  ? 'Nothing matches these filters.'
                  : day === ALL
                    ? 'No on-site matches are booked in this event yet.'
                    : 'No on-site matches are booked on this day.'}
              </EmptyState>
            )}
            {data.groups.map((group) => {
              const shut = isFolded(group.label, group.folded_by_default)
              const id = `ps-group-${group.label.replace(/\W+/g, '-')}`
              return (
                <div key={group.label} role="group" aria-labelledby={id}>
                  <h2 className="ms-group" id={id}>
                    <button
                      type="button"
                      className="ms-group__toggle"
                      aria-expanded={!shut}
                      onClick={() => setFolded(group.label, !shut)}
                    >
                      <Icon name={shut ? 'chevron_right' : 'keyboard_arrow_down'} size={20} />
                      {group.label}
                      <span className="ms-group__n">· {group.rows.length}</span>
                    </button>
                  </h2>
                  {!shut &&
                    group.rows.map((row) => (
                      <div key={row.id} className={`ps-item ps-item--${row.tone}${row.id === matchId ? ' is-selected' : ''}`}>
                        <Row row={row} zone={zone} selected={row.id === matchId} to={withQuery(`${home}/${row.id}`)} stations />
                        <StepButtons row={row} onDone={changed} />
                      </div>
                    ))}
                </div>
              )
            })}
          </section>
          {!phone && (
            <section className="ms-detail" aria-label="Selected match">
              {pane(false) ?? (
                <EmptyState icon="event" title="Pick a match">
                  Its checklist, stations and comments open here.
                </EmptyState>
              )}
            </section>
          )}
        </div>
      )}
    </>
  )
}

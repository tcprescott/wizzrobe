import { useCallback, useEffect, useMemo, useState } from 'react'
import { NavLink, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ActionsMenu, Banner, Button, Chip, EmptyState, Icon, PageHeader, Spinner, type Tone } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { useAdmin } from '../kit/useAdminNav'
import { dayKey, effectiveZone, formatDay, formatTime } from '../../lib/time'
import { readStored, writeStored } from '../../lib/storage'
import { useBoard, useUnbooked, type Board, type BoardFilters, type BoardRow, type Unbooked } from './data'
import { MatchForm } from './MatchForm'
import { MatchPane } from './MatchPane'
import { BookPane, UnbookedList, unbookedKey } from './Unbooked'
import { NeedsYou } from './NeedsYou'
import './schedule.css'

const ALL = 'all'
const FOLD_KEY = 'wiz.admin-schedule.folded'

/** The board's address, so a match link (or a DM's) opens the same filters. */
function useFilters(): [BoardFilters, (next: Partial<BoardFilters>) => void] {
  const [params, setParams] = useSearchParams()
  const filters: BoardFilters = {
    day: params.get('day'),
    tournament: Number(params.get('tournament')) || null,
    stage: Number(params.get('stage')) || null,
    q: params.get('q') ?? '',
  }
  const set = useCallback(
    (next: Partial<BoardFilters>) => {
      setParams(
        (prev) => {
          const out = new URLSearchParams(prev)
          const merged = { ...Object.fromEntries(prev), ...next }
          for (const key of ['day', 'tournament', 'stage', 'q'] as const) {
            const value = (merged as Record<string, unknown>)[key]
            if (value === null || value === undefined || value === '') out.delete(key)
            else out.set(key, String(value))
          }
          return out
        },
        { replace: true },
      )
    },
    [setParams],
  )
  return [filters, set]
}

function useFolded(): [Record<string, boolean>, (label: string, folded: boolean) => void] {
  const [folded, setFolded] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(readStored(FOLD_KEY) ?? '{}') as Record<string, boolean>
    } catch {
      return {}
    }
  })
  const set = useCallback((label: string, value: boolean) => {
    setFolded((prev) => {
      const next = { ...prev, [label]: value }
      writeStored(FOLD_KEY, JSON.stringify(next))
      return next
    })
  }, [])
  return [folded, set]
}

export function matchupText(row: BoardRow): string {
  const names = [...row.players.map((p) => p.name), ...(row.placeholders ?? [])]
  if (names.length === 0) return 'Players to be decided'
  return names.length === 2 ? `${names[0]} vs ${names[1]}` : names.join(', ')
}

export function metaText(row: BoardRow): string {
  const parts = [row.tournament]
  if (row.bracket) parts.push(row.bracket)
  if (row.game) parts.push(row.best_of && row.best_of > 1 ? `Game ${row.game} of ${row.best_of}` : `Game ${row.game}`)
  if (row.set_label) parts.push(row.set_label)
  return parts.join(' · ')
}

function Matchup({ row }: { row: BoardRow }) {
  const names = [...row.players.map((p) => p.name), ...(row.placeholders ?? [])]
  if (names.length === 0) return <span className="ms-row__tbd">Players to be decided</span>
  if (names.length !== 2) return <>{names.join(', ')}</>
  return (
    <>
      {names[0]}
      <span className="ms-row__vs"> vs </span>
      {names[1]}
    </>
  )
}

function Row({ row, zone, selected, to }: { row: BoardRow; zone: string; selected: boolean; to: string }) {
  const comments = (row.comments?.player ?? 0) + (row.comments?.staff ?? 0)
  const time = row.started_at ?? row.scheduled_at
  return (
    <NavLink
      to={to}
      className={['ms-row', `ms-row--${row.tone}`, selected && 'is-selected'].filter(Boolean).join(' ')}
      aria-current={selected ? 'true' : undefined}
    >
      <span className="ms-row__time">{time ? formatTime(time, zone) : 'No time'}</span>
      <span className="ms-row__main">
        <span className="ms-row__meta">{metaText(row)}</span>
        <span className="ms-row__who">
          <Matchup row={row} />
        </span>
        <span className="ms-row__line">
          <span className="ms-row__place">
            <Icon name={row.stage ? 'videocam' : 'tv'} size={16} />
            {row.stage ?? row.seat_line ?? (row.stream_candidate ? 'Stream candidate' : 'No stage')}
          </span>
          <Chip tone={row.tone as Tone}>{row.status}</Chip>
        </span>
        {(row.late.length > 0 || comments > 0 || row.players.some((p) => p.ada) || row.crew.some((c) => c.short)) && (
          <span className="ms-row__tags">
            {row.late.map((l) => (
              <Chip key={l.label} tone="pending">
                {l.label}
              </Chip>
            ))}
            {row.crew
              .filter((c) => c.short)
              .map((c) => (
                <Chip key={c.key} tone="pending">
                  {c.label} {c.count}
                </Chip>
              ))}
            {row.players.some((p) => p.ada) && <Chip tone="info">ADA</Chip>}
            {comments > 0 && (
              <Chip tone="info">
                {comments} {comments === 1 ? 'comment' : 'comments'}
              </Chip>
            )}
          </span>
        )}
      </span>
      <Icon name="chevron_right" className="ms-row__go" />
    </NavLink>
  )
}

function DaySwitch({ board, day, zone, onDay }: { board: Board; day: string; zone: string; onDay: (d: string) => void }) {
  const options = [
    ...board.days.map((d) => ({ value: d, label: formatDay(`${d}T12:00:00Z`, 'UTC') })),
    { value: ALL, label: 'All days' },
  ]
  if (day !== ALL && !board.days.includes(day)) options.unshift({ value: day, label: formatDay(`${day}T12:00:00Z`, 'UTC') })
  return (
    <div className="segmented ms-days" role="radiogroup" aria-label="Day">
      {options.map((o) => (
        <span key={o.value}>
          <input type="radio" id={`ms-day-${o.value}`} name="ms-day" checked={day === o.value} onChange={() => onDay(o.value)} />
          <label htmlFor={`ms-day-${o.value}`}>{o.label}</label>
        </span>
      ))}
      <span className="visually-hidden">{zone}</span>
    </div>
  )
}

function ViewSwitch({ unbooked, matches, count }: { unbooked: boolean; matches: number; count: number | null }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { adminBase } = useAdmin()
  const pick = (value: boolean) => {
    if (value === unbooked) return
    const out = new URLSearchParams(params)
    if (value) out.set('view', 'unbooked')
    else out.delete('view')
    navigate(`${adminBase}/match-schedule${out.size ? `?${out}` : ''}`, { replace: true })
  }
  return (
    <div className="ms-view">
      <div className="segmented" role="radiogroup" aria-label="Show">
        <span>
          <input type="radio" id="ms-view-matches" name="ms-view" checked={!unbooked} onChange={() => pick(false)} />
          <label htmlFor="ms-view-matches">Matches · {matches}</label>
        </span>
        <span>
          <input type="radio" id="ms-view-unbooked" name="ms-view" checked={unbooked} onChange={() => pick(true)} />
          <label htmlFor="ms-view-unbooked">Not booked yet{count !== null ? ` · ${count}` : ''}</label>
        </span>
      </div>
    </div>
  )
}

type Mode = 'new' | 'edit' | 'unbooked'

/**
 * The admin Match Schedule, layout C: the run sheet on the left with its groups,
 * the selected match beside it. On a phone the list is one screen and a match
 * opens as its own, with a back link. New and Edit match, and booking a bracket
 * matchup from "Not booked yet", take the same right pane.
 */
export function MatchScheduleScreen({ mode }: { mode?: Mode }) {
  const { adminBase } = useAdmin()
  const { match, source, key } = useParams()
  const matchId = Number(match) || null
  const bookingKey = mode === 'unbooked' && source && key ? `${source}/${key}` : null
  const phone = usePhone()
  const navigate = useNavigate()
  const [filters, setFilters] = useFilters()
  const [search, setSearch] = useState(filters.q)
  const [folded, setFolded] = useFolded()
  const today = dayKey(new Date().toISOString(), effectiveZone(null))
  const day = filters.day ?? today
  const { data, error, loading, reload } = useBoard({ ...filters, day: day === ALL ? null : day })
  const zone = effectiveZone(data?.zone)
  const [params] = useSearchParams()
  const showUnbooked = params.get('view') === 'unbooked' || mode === 'unbooked'
  const unbooked = useUnbooked(Boolean(data?.can_create))
  const unbookedItems = unbooked.data ?? []
  const booking = bookingKey ? unbookedItems.find((i) => unbookedKey(i) === bookingKey) ?? null : null

  useEffect(() => {
    const id = window.setTimeout(() => {
      if (search !== filters.q) setFilters({ q: search })
    }, 300)
    return () => window.clearTimeout(id)
  }, [search, filters.q, setFilters])

  const query = params.toString()
  const home = `${adminBase}/match-schedule`
  const withQuery = (path: string, q: string = query) => `${path}${q ? `?${q}` : ''}`
  const linkFor = (id: number) => withQuery(`${home}/${id}`)
  const bookFor = (item: Unbooked) => withQuery(`${home}/unbooked/${item.source}/${item.key}`)
  const backTo = withQuery(home)
  const matchesQuery = (() => {
    const out = new URLSearchParams(params)
    out.delete('view')
    return out.toString()
  })()
  const count = useMemo(() => data?.groups.reduce((n, g) => n + g.rows.length, 0) ?? 0, [data])

  const onSaved = (id: number) => {
    reload()
    unbooked.reload()
    navigate(withQuery(`${home}/${id}`, matchesQuery))
  }
  const onBooked = (id: number | null) => {
    reload()
    unbooked.reload()
    navigate(id ? withQuery(`${home}/${id}`, matchesQuery) : backTo)
  }
  const onEdit = (id: number) => navigate(withQuery(`${home}/${id}/edit`))

  function pane(standalone: boolean) {
    const closeTo = standalone ? undefined : backTo
    if (mode === 'new' || mode === 'edit') {
      return (
        <MatchForm
          matchId={mode === 'edit' ? matchId : null}
          zone={data?.zone}
          backTo={mode === 'edit' && matchId ? linkFor(matchId) : backTo}
          onSaved={onSaved}
          standalone={standalone}
        />
      )
    }
    if (mode === 'unbooked') {
      if (booking) return <BookPane key={bookingKey} item={booking} zone={zone} closeTo={closeTo} standalone={standalone} onBooked={onBooked} />
      if (unbooked.loading || !unbooked.data) return <Spinner label="Loading the matchup" />
      return (
        <EmptyState icon="event_available" title="Already booked">
          This matchup has a time now, or the bracket moved on.
        </EmptyState>
      )
    }
    if (matchId) {
      return (
        <MatchPane
          matchId={matchId}
          board={data}
          onChanged={reload}
          onGone={() => navigate(backTo)}
          onEdit={() => onEdit(matchId)}
          closeTo={closeTo}
          standalone={standalone}
        />
      )
    }
    return null
  }

  if (phone && (matchId || mode)) {
    return (
      <>
        <NavLink to={backTo} className="ms-back">
          <Icon name="chevron_left" size={20} />
          Match Schedule
        </NavLink>
        {pane(true)}
      </>
    )
  }

  const isFolded = (label: string, byDefault: boolean) => folded[label] ?? byDefault

  return (
    <>
      <PageHeader
        title="Match Schedule"
        lede={
          data?.scoped_tournaments
            ? `Showing only your ${data.scoped_tournaments === 1 ? 'tournament' : `${data.scoped_tournaments} tournaments`}`
            : undefined
        }
        actions={
          <>
            {data?.can_create && (
              <Button icon="add" asChild>
                <NavLink to={withQuery(`${home}/new`)}>New match</NavLink>
              </Button>
            )}
            <ActionsMenu
              items={[
                {
                  label: 'Refresh',
                  icon: 'restart_alt',
                  onSelect: () => {
                    reload()
                    unbooked.reload()
                  },
                },
              ]}
            />
          </>
        }
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
      {loading && !data && <Spinner label="Loading the schedule" />}
      {data && (
        <div className={phone ? 'ms-phone' : 'ms-split'}>
          <section className="ms-list" aria-label={showUnbooked ? 'Not booked yet' : `Matches, ${count}`}>
            {data.can_create && (
              <ViewSwitch unbooked={showUnbooked} matches={count} count={unbooked.data ? unbookedItems.length : null} />
            )}
            {showUnbooked ? (
              <>
                {unbooked.error && !unbooked.data && (
                  <Banner tone="error" action={<Button variant="outline" onClick={unbooked.reload}>Try again</Button>}>
                    {unbooked.error.message}
                  </Banner>
                )}
                {unbooked.loading && !unbooked.data && <Spinner label="Loading bracket matchups" />}
                {unbooked.data && <UnbookedList items={unbookedItems} selected={bookingKey} linkFor={bookFor} />}
              </>
            ) : (
              <>
                <NeedsYou board={data} zone={zone} linkFor={linkFor} onChanged={reload} />
                {count === 0 && (
                  <EmptyState icon="calendar_month" title="No matches here">
                    {filters.q || filters.tournament || filters.stage
                      ? 'Nothing matches these filters.'
                      : day === ALL
                        ? 'No matches are booked in this event yet.'
                        : 'No matches are booked on this day.'}
                  </EmptyState>
                )}
                {data.groups.map((group) => {
                  const shut = isFolded(group.label, group.folded_by_default)
                  const id = `ms-group-${group.label.replace(/\W+/g, '-')}`
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
                          <Row key={row.id} row={row} zone={zone} selected={row.id === matchId} to={linkFor(row.id)} />
                        ))}
                    </div>
                  )
                })}
              </>
            )}
          </section>
          {!phone && (
            <section className="ms-detail" aria-label="Selected match">
              {pane(false) ?? (
                <EmptyState icon="event" title={showUnbooked ? 'Pick a matchup' : 'Pick a match'}>
                  {showUnbooked ? 'Pick a time and book it here.' : 'Its checklist, crew and run buttons open here.'}
                </EmptyState>
              )}
            </section>
          )}
        </div>
      )}
    </>
  )
}

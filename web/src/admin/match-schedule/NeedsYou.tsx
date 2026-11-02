import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { Banner, Button, Chip, FormDialog, Icon } from '../../components'
import { formatDayTime, formatTime, wallClock, zonedInstant } from '../../lib/time'
import { actions, type Board, type BoardRow, type Need, type RescheduleNeed } from './data'
import { matchupText } from './MatchScheduleScreen'

const SHOWN = 3

const KIND_LABEL: Record<string, string> = {
  held: 'Racetime result held',
  result: 'Result to confirm',
  late: 'Nobody checked in',
}

export function requestTitle(r: RescheduleNeed): string {
  const names = r.players.length === 2 ? `${r.players[0]} vs ${r.players[1]}` : r.players.join(', ') || r.title || 'a match'
  return `${r.requested_by} wants to ${r.kind === 'cancel' ? 'call off' : 'move'} ${names}`
}

export function requestDetail(r: RescheduleNeed, zone: string): string {
  const now = r.scheduled_at ? formatDayTime(r.scheduled_at, zone) : 'no time set'
  if (r.kind === 'cancel') return `${r.tournament} · ${now}`
  if (!r.proposed_at) return `${r.tournament} · ${now}, no new time suggested`
  return `${r.tournament} · ${now} to ${formatDayTime(r.proposed_at, zone)}`
}

function siteTitle(row: BoardRow): string {
  const site = row.played_on_site
  if (!site) return matchupText(row)
  if (site.no_winner) return `Closed on ${site.site} with no winner`
  const winner = row.players.find((p) => site.winner_user_id && p.user_id === site.winner_user_id)
  const name = winner?.name ?? site.winner ?? 'Someone'
  const others = row.players.filter((p) => p !== winner)
  return others.length === 1 && winner ? `${name} beat ${others[0]!.name} on ${site.site}` : `${name} won on ${site.site}`
}

function MatchNeed({ need, row, zone, to }: { need: Need; row: BoardRow; zone: string; to: string }) {
  let title = matchupText(row)
  let kind = KIND_LABEL[need.kind] ?? ''
  let detail = row.tournament
  let chip: string | null = null
  if (need.kind === 'site') {
    title = siteTitle(row)
    kind = `Played on ${row.played_on_site?.site ?? 'the bracket site'}`
    if (row.scheduled_at) detail += ` · booked ${formatTime(row.scheduled_at, zone)}`
  } else if (need.kind === 'held') {
    chip = row.racetime_held?.label ?? null
    if (row.racetime_held?.detail) detail += ` · ${row.racetime_held.detail}`
  } else if (need.kind === 'result') {
    chip = row.needs_review ? 'Flagged' : need.has_result ? null : 'No winner'
    if (row.until_at && !row.until_estimated) detail += ` · finished ${formatTime(row.until_at, zone)}`
  } else if (need.kind === 'late' && row.scheduled_at) {
    detail += ` · was ${formatTime(row.scheduled_at, zone)}`
    if (row.late.length) kind = row.late.map((l) => l.label).join(', ')
  }
  return (
    <li className="ms-need">
      <Icon name="priority_high" className="ms-need__icon" size={20} />
      <span className="ms-need__text">
        <span className="ms-need__title">
          {title}
          {chip && <Chip tone="pending">{chip}</Chip>}
        </span>
        <span className="ms-need__meta">
          {kind} · {detail}
        </span>
      </span>
      <Button variant="outline" size="sm" asChild>
        <NavLink to={to} aria-label={`Open ${matchupText(row)}`}>
          Open
        </NavLink>
      </Button>
    </li>
  )
}

function RequestNeed({
  request,
  zone,
  onReview,
  onDecline,
  onApproved,
}: {
  request: RescheduleNeed
  zone: string
  onReview: () => void
  onDecline: () => void
  onApproved: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const quick = request.kind !== 'cancel' && request.proposed_at && request.clashes.length === 0
  async function approve() {
    setBusy(true)
    setFailure(null)
    try {
      await actions.approveRequest(request.id)
      onApproved()
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <li className="ms-need">
      <Icon name="event_busy" className="ms-need__icon" size={20} />
      <span className="ms-need__text">
        <span className="ms-need__title">{requestTitle(request)}</span>
        <span className="ms-need__meta">{requestDetail(request, zone)}</span>
        {request.opponent_agreed && <span className="ms-need__meta ms-ok">The other player agreed</span>}
        {request.clashes.length > 0 && (
          <span className="ms-need__meta ms-warn">
            {request.clashes.length === 1 ? 'The new time might not work' : `${request.clashes.length} reasons the new time might not work`}
          </span>
        )}
        {failure && <span className="ms-need__meta ms-warn">{failure}</span>}
        <span className="ms-need__acts">
          <Button variant="outline" size="sm" onClick={onDecline}>
            Decline
          </Button>
          {quick ? (
            <Button size="sm" busy={busy} busyLabel="Moving…" onClick={approve}>
              Approve
            </Button>
          ) : (
            <Button size="sm" onClick={onReview}>
              Review
            </Button>
          )}
        </span>
      </span>
    </li>
  )
}

/** "Needs you", pinned above the run sheet: requests to decide, results to check, matches nobody seated. */
export function NeedsYou({
  board,
  zone,
  linkFor,
  onChanged,
}: {
  board: Board
  zone: string
  linkFor: (id: number) => string
  onChanged: () => void
}) {
  const [all, setAll] = useState(false)
  const [open, setOpen] = useState<{ request: RescheduleNeed; mode: RequestMode } | null>(null)
  const needs = board.needs_you
  if (needs.length === 0) return null
  const rows = new Map(board.groups.flatMap((g) => g.rows).map((r) => [r.id, r]))
  const shown = all ? needs : needs.slice(0, SHOWN)
  return (
    <div role="group" aria-labelledby="ms-needs-title" className="ms-needs">
      <h2 className="ms-group ms-needs__head" id="ms-needs-title">
        <span className="ms-group__toggle">
          <Icon name="inbox" size={20} />
          Needs you
          <span className="ms-group__n">· {needs.length}</span>
        </span>
      </h2>
      <ul className="ms-needs__list">
        {shown.map((need) => {
          if (need.kind === 'request' && need.request) {
            const request = need.request
            return (
              <RequestNeed
                key={`r${request.id}`}
                request={request}
                zone={zone}
                onReview={() => setOpen({ request, mode: 'review' })}
                onDecline={() => setOpen({ request, mode: 'decline' })}
                onApproved={onChanged}
              />
            )
          }
          const row = rows.get(need.match_id)
          return row ? <MatchNeed key={`${need.kind}${need.match_id}`} need={need} row={row} zone={zone} to={linkFor(row.id)} /> : null
        })}
      </ul>
      {needs.length > SHOWN && (
        <button type="button" className="ms-needs__more" onClick={() => setAll(!all)}>
          {all ? 'Show fewer' : `Show ${needs.length - SHOWN} more`}
        </button>
      )}
      {open && (
        <RequestDialog
          request={open.request}
          mode={open.mode}
          zone={zone}
          onClose={() => setOpen(null)}
          onDone={() => {
            setOpen(null)
            onChanged()
          }}
        />
      )}
    </div>
  )
}

export type RequestMode = 'review' | 'decline'

/** One reschedule request: read the player's reason and the clashes, then move, call off or decline. */
export function RequestDialog({
  request: r,
  mode: initial,
  zone,
  onClose,
  onDone,
}: {
  request: RescheduleNeed
  mode: RequestMode
  zone: string
  onClose: () => void
  onDone: () => void
}) {
  const [mode, setMode] = useState(initial)
  const [note, setNote] = useState('')
  const start = r.proposed_at ?? r.scheduled_at
  const [when, setWhen] = useState(() => (start ? wallClock(start, zone) : { date: '', time: '' }))
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const cancel = r.kind === 'cancel'

  async function submit() {
    setFailure(null)
    if (mode === 'decline' && !note.trim()) {
      setFailure('Say why, so the player knows what to do next.')
      return
    }
    let at: string | null = null
    if (mode === 'review' && !cancel && !r.proposed_at) {
      if (!when.date || !when.time) {
        setFailure('Pick the time to move it to.')
        return
      }
      at = zonedInstant(when.date, when.time, zone).toISOString()
    }
    setBusy(true)
    try {
      if (mode === 'decline') await actions.declineRequest(r.id, note.trim())
      else await actions.approveRequest(r.id, at)
      onDone()
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const primary =
    mode === 'decline' ? 'Decline request' : cancel ? 'Call off match' : r.clashes.length ? 'Move it anyway' : 'Move match'
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={mode === 'decline' ? 'Decline this request?' : cancel ? 'Call off this match?' : 'Move this match?'}
      size="sm"
      primaryLabel={primary}
      destructive={mode === 'review' && cancel}
      busy={busy}
      busyLabel={mode === 'decline' ? 'Declining…' : cancel ? 'Calling off…' : 'Moving…'}
      onPrimary={submit}
      extra={mode === 'review' ? [{ label: 'Decline instead…', icon: 'block', onSelect: () => setMode('decline') }] : undefined}
    >
      <div className="ms-stack">
        <div>
          <p className="ms-need__title">{requestTitle(r)}</p>
          <p className="ms-need__meta">{requestDetail(r, zone)}</p>
          {r.opponent_agreed && <p className="ms-need__meta ms-ok">The other player agreed</p>}
        </div>
        <blockquote className="ms-quote">{r.reason}</blockquote>
        {mode === 'review' && !cancel && r.clashes.length > 0 && (
          <div>
            <p className="ms-check__label">This time might not work:</p>
            <ul className="ms-clashes">
              {r.clashes.map((c) => (
                <li key={c}>{c}</li>
              ))}
            </ul>
          </div>
        )}
        {mode === 'review' && !cancel && !r.proposed_at && (
          <div className="ms-when">
            <label className="field">
              <span className="field__label">Date</span>
              <input className="input" type="date" value={when.date} onChange={(e) => setWhen({ ...when, date: e.target.value })} />
            </label>
            <label className="field">
              <span className="field__label">Time</span>
              <input className="input" type="time" value={when.time} onChange={(e) => setWhen({ ...when, time: e.target.value })} />
            </label>
          </div>
        )}
        {mode === 'review' && (
          <p className="ms-note">
            {cancel
              ? 'The match is deleted and its players, crew and watchers are told.'
              : 'Everyone on the match is told the new time.'}
          </p>
        )}
        {mode === 'decline' && (
          <label className="field">
            <span className="field__label">Tell {r.requested_by} why</span>
            <textarea className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
            <span className="field__hint">Sent to them in a DM.</span>
          </label>
        )}
        {failure && <Banner tone="error">{failure}</Banner>}
      </div>
    </FormDialog>
  )
}

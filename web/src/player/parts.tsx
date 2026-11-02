import { useState, type ReactNode } from 'react'
import { api, unwrap } from '../api/client'
import { Button, Chip, FormDialog, Icon, RowMenu, type IconName, type MenuItem } from '../components'
import { countdown, dayLabel, formatTime } from '../lib/time'
import { AgreeDialog } from './AgreeDialog'
import type { Entry, Need } from './data'
import { PickTimeDialog } from './PickTimeDialog'
import { RunningLateDialog } from './RunningLateDialog'

const KIND_ICON: Record<string, IconName> = { match: 'sports_esports', crew: 'mic', shift: 'badge' }
const KIND_LABEL: Record<string, string> = { match: 'Match', crew: 'Crew', shift: 'Shift' }

function statusChip(entry: Entry): ReactNode {
  if (entry.kind === 'match') {
    if (entry.status === 'live') return <Chip tone="live">Live</Chip>
    if (entry.status === 'checked_in') return <Chip tone="ok">Checked in</Chip>
    if (entry.status === 'finished') return <Chip>Finished</Chip>
    if (entry.needs_acknowledgment) return <Chip tone="pending">Not confirmed</Chip>
    return null
  }
  if (entry.kind === 'crew') {
    return entry.approved ? <Chip tone="ok">Approved</Chip> : <Chip tone="pending">Awaiting approval</Chip>
  }
  if (entry.called_off) return <Chip>Called off</Chip>
  return entry.checked_in ? <Chip tone="ok">Checked in</Chip> : null
}

function lateChip(entry: Entry): ReactNode {
  if (!entry.late_minutes) return null
  return <Chip tone="info">~{entry.late_minutes >= 30 ? '30+' : entry.late_minutes} min late</Chip>
}

function where(entry: Entry): string {
  return [entry.detail, entry.stage, entry.station && `Station ${entry.station}`].filter(Boolean).join(' · ')
}

/** One line of a run sheet: time, what it is, and at most one button. */
export function ScheduleRow({ entry, zone, onDone }: { entry: Entry; zone: string; onDone: (msg: string) => void }) {
  const [late, setLate] = useState(false)
  return (
    <div className="sched-row">
      <time className="sched-row__time" dateTime={entry.starts_at}>
        {formatTime(entry.starts_at, zone)}
      </time>
      <div className="sched-row__main">
        <div className="sched-row__title">
          <Icon name={KIND_ICON[entry.kind] ?? 'event'} size={18} />
          <span className="visually-hidden">{KIND_LABEL[entry.kind]}: </span>
          <span>{entry.title}</span>
          {statusChip(entry)}
          {lateChip(entry)}
        </div>
        <div className="sched-row__sub">{where(entry)}</div>
      </div>
      {entry.kind === 'shift' ? (
        <ShiftActions entry={entry} onDone={onDone} />
      ) : (
        (entry.can_report_late || entry.room_url) && (
          <div className="sched-row__acts">
            {entry.room_url && (
              <Button asChild variant="outline" size="sm">
                <a href={entry.room_url} target="_blank" rel="noopener noreferrer">
                  Join room
                </a>
              </Button>
            )}
            {entry.can_report_late && (
              <>
                <Button variant="outline" size="sm" onClick={() => setLate(true)}>
                  Running late
                </Button>
                <RunningLateDialog entry={entry} open={late} onOpenChange={setLate} onSent={onDone} />
              </>
            )}
          </div>
        )
      )}
    </div>
  )
}

/**
 * A shift's one button ("I'm here" from 30 minutes before, else "Got it"
 * until acknowledged) and its ⋮: running late, undo, can't make it.
 */
function ShiftActions({ entry, onDone }: { entry: Entry; onDone: (msg: string) => void }) {
  const [late, setLate] = useState(false)
  const [release, setRelease] = useState(false)
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const id = entry.assignment_id
  if (id == null) return null
  const path = { params: { path: { assignment_id: id } } }

  async function act(work: () => Promise<unknown>, ok: string) {
    setBusy(true)
    setError(null)
    try {
      await work()
      onDone(ok)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const items: MenuItem[] = []
  if (entry.can_report_late) items.push({ label: 'Running late…', icon: 'schedule', onSelect: () => setLate(true) })
  if (entry.can_undo_check_in)
    items.push({
      label: 'Undo check-in',
      icon: 'undo',
      onSelect: () =>
        void act(() => api.DELETE('/api/volunteers/me/assignments/{assignment_id}/arrival', path).then(unwrap), 'Check-in taken back.'),
    })
  if (entry.can_release) items.push({ label: "Can't make it…", icon: 'event_busy', destructive: true, onSelect: () => setRelease(true) })

  return (
    <div className="sched-row__acts">
      {entry.shift_action === 'arrive' && (
        <Button
          variant="primary"
          size="sm"
          busy={busy}
          busyLabel="Checking in…"
          onClick={() =>
            void act(
              () => api.POST('/api/volunteers/me/assignments/{assignment_id}/arrival', path).then(unwrap),
              "You're checked in. Thanks for coming.",
            )
          }
        >
          I'm here
        </Button>
      )}
      {entry.shift_action === 'ack' && (
        <Button
          variant="outline"
          size="sm"
          busy={busy}
          busyLabel="Saving…"
          onClick={() =>
            void act(
              () => api.POST('/api/volunteers/assignments/{assignment_id}/acknowledge', path).then(unwrap),
              'Thanks. The coordinators can see you have it.',
            )
          }
        >
          Got it
        </Button>
      )}
      {items.length > 0 && <RowMenu label={`More for ${entry.title}`} items={items} />}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <RunningLateDialog entry={entry} open={late} onOpenChange={setLate} onSent={onDone} />
      <FormDialog
        open={release}
        onOpenChange={setRelease}
        size="sm"
        title="Can't make this shift?"
        description="You come off the shift and the volunteer coordinators get a DM so they can find cover."
        primaryLabel="Give it back"
        destructive
        busy={busy}
        busyLabel="Sending…"
        onPrimary={() =>
          void act(async () => {
            const result = await api.DELETE('/api/volunteers/me/assignments/{assignment_id}', {
              ...path,
              body: reason.trim() ? { reason: reason.trim() } : undefined,
            })
            if (!result.response.ok) unwrap(result)
            setRelease(false)
          }, 'Shift given back. The coordinators have been told.')
        }
      >
        <label className="field">
          <span className="field__label">Reason (optional)</span>
          <input className="input" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
        </label>
      </FormDialog>
    </div>
  )
}

/** The player's next match, big: countdown, where, the room, Running late. */
export function NextUpCard({ entry, zone, now, onDone }: { entry: Entry; zone: string; now: Date; onDone: (msg: string) => void }) {
  const [late, setLate] = useState(false)
  const left = entry.status === 'scheduled' ? countdown(entry.starts_at, now) : null
  const day = dayLabel(entry.starts_at, zone, now)
  return (
    <section className="next-up" aria-labelledby="next-up-title">
      <div className="next-up__head">
        <div className="next-up__who">
          <p className="next-up__label">Next up</p>
          <h2 className="next-up__title" id="next-up-title">
            {entry.title}
          </h2>
          <p className="next-up__sub">{entry.detail}</p>
        </div>
        {left && (
          <div className="next-up__count">
            <span className="next-up__count-label">{left === 'now' ? 'starts' : 'starts in'}</span>
            <span className="next-up__count-value">{left}</span>
          </div>
        )}
        {!left && statusChip(entry)}
      </div>
      <ul className="next-up__facts">
        <li>
          <Icon name="schedule" size={18} />
          {day === 'Today' ? '' : `${day}, `}
          {formatTime(entry.starts_at, zone)}
        </li>
        {entry.stage && (
          <li>
            <Icon name="tv" size={18} />
            {entry.stage}
          </li>
        )}
        {entry.station && (
          <li>
            <Icon name="desk" size={18} />
            Station {entry.station}
          </li>
        )}
        {entry.late_minutes ? <li>{lateChip(entry)}</li> : null}
        {left && entry.needs_acknowledgment ? <li>{statusChip(entry)}</li> : null}
      </ul>
      {(entry.room_url || entry.can_report_late) && (
        <div className="next-up__acts">
          {entry.room_url && (
            <Button asChild variant="primary">
              <a href={entry.room_url} target="_blank" rel="noopener noreferrer">
                <Icon name="open_in_new" size={18} />
                Open race room
              </a>
            </Button>
          )}
          {entry.can_report_late && (
            <>
              <Button variant="outline" onClick={() => setLate(true)}>
                Running late
              </Button>
              <RunningLateDialog entry={entry} open={late} onOpenChange={setLate} onSent={onDone} />
            </>
          )}
        </div>
      )}
    </section>
  )
}

const NEED_ICON: Record<string, IconName> = { book: 'event_available', agree: 'swap_horiz', acknowledge: 'task_alt' }

/** One thing waiting on the player, with the one button that does it. */
export function NeedCard({ need, zone, onDone }: { need: Need; zone: string; onDone: (msg: string) => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bookable = need.bookable

  async function acknowledge() {
    if (need.match_id == null) return
    setBusy(true)
    setError(null)
    try {
      await api.POST('/api/matches/{match_id}/acknowledge', { params: { path: { match_id: need.match_id } } }).then(unwrap)
      onDone("Thanks. Your opponent and staff can see you've confirmed.")
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  let action: ReactNode = null
  if (need.kind === 'book' && bookable && !bookable.blocked) {
    action = (
      <>
        <Button variant="primary" onClick={() => setOpen(true)}>
          Pick a time
        </Button>
        <PickTimeDialog item={bookable} zone={zone} open={open} onOpenChange={setOpen} onBooked={onDone} />
      </>
    )
  } else if (need.kind === 'agree') {
    action = (
      <>
        <Button variant="outline" onClick={() => setOpen(true)}>
          {need.agreed ? 'See request' : 'Review'}
        </Button>
        <AgreeDialog need={need} zone={zone} open={open} onOpenChange={setOpen} onAgreed={onDone} />
      </>
    )
  } else if (need.kind === 'acknowledge') {
    action = (
      <Button variant="outline" busy={busy} busyLabel="Saving…" onClick={() => void acknowledge()}>
        I can play then
      </Button>
    )
  }

  return (
    <article className="need-card">
      <h3 className="need-card__title">
        <Icon name={NEED_ICON[need.kind] ?? 'info'} size={20} />
        <span>{need.title}</span>
      </h3>
      {need.detail && <p className="need-card__sub">{need.detail}</p>}
      {bookable?.source_label && (
        <p className="need-card__sub">
          <Chip>{bookable.source_label}</Chip>
        </p>
      )}
      {bookable?.blocked && <p className="need-card__blocked">{bookable.blocked}</p>}
      {need.kind === 'agree' && need.agreed && <Chip tone="ok">You agreed</Chip>}
      {action && <div className="need-card__acts">{action}</div>}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </article>
  )
}

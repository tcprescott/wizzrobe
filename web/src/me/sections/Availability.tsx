import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Button, EmptyState, FormDialog, Icon, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { useCommunity } from '../../community/CommunityProvider'
import { effectiveZone, formatDayTime, wallClock, zonedInstant } from '../../lib/time'
import { message, useSection } from '../data'
import { useProfile } from '../MeLayout'

type Window = Schemas['PlayerAvailabilityResponse']
type Status = 'unavailable' | 'preferred'

const DAY_START_HOUR = 6

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

function dayLabel(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', timeZone: 'UTC' })
}

/** When you can't play: blocked times opponents and staff see, drawn as one bar per event day. */
export function Availability() {
  const { profile } = useProfile()
  const { event } = useCommunity()
  const zone = effectiveZone(profile.zone_pinned ? profile.community_zone : profile.timezone)
  const { data, reload } = useSection('availability', () => api.GET('/api/users/me/availability').then(unwrap))
  const [editing, setEditing] = useState<Window | 'new' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  async function save(windows: Window[], ok: string) {
    setError(null)
    try {
      await api
        .PUT('/api/users/me/availability', {
          body: { windows: windows.map((w) => ({ starts_at: w.starts_at, ends_at: w.ends_at, status: w.status, note: w.note ?? null })) },
        })
        .then(unwrap)
      setSaved(ok)
      reload()
    } catch (err) {
      setError(message(err))
      throw err
    }
  }

  if (!data) return <Spinner label="Loading your availability" />
  const windows = [...data].sort((a, b) => a.starts_at.localeCompare(b.starts_at))
  const days = eventDays(event?.starts_on, event?.ends_on)

  return (
    <div className="me-stack">
      <Panel
        title="When you can't play"
        actions={
          <Button variant="primary" icon="add" onClick={() => setEditing('new')}>
            Block a time
          </Button>
        }
      >
        <PanelRow>
          <p className="me-note">
            You count as free for the whole event until you block a time out. Opponents and staff see these when they pick a time.
            Times are in {zone.replace(/_/g, ' ')}.
          </p>
        </PanelRow>
        {days.length > 0 && (
          <PanelRow>
            <div className="me-bars" aria-hidden="true">
              {days.map((day) => {
                const start = zonedInstant(day, `${String(DAY_START_HOUR).padStart(2, '0')}:00`, zone).getTime()
                const span = 24 * 3600 * 1000
                return (
                  <div className="me-bar" key={day}>
                    <span>{dayLabel(day)}</span>
                    <div className="me-bar__track">
                      {windows.map((w) => {
                        const a = Math.max(new Date(w.starts_at).getTime(), start)
                        const b = Math.min(new Date(w.ends_at).getTime(), start + span)
                        if (b <= a) return null
                        return (
                          <i
                            key={w.id}
                            className={`me-bar__block${w.status === 'preferred' ? ' me-bar__block--preferred' : ''}`}
                            style={{ left: `${((a - start) / span) * 100}%`, width: `${((b - a) / span) * 100}%` }}
                          />
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
          </PanelRow>
        )}
        {windows.length === 0 ? (
          <PanelRow>
            <EmptyState icon="event_available" title="Free all event">
              Block out the times you can't play and leave the rest alone.
            </EmptyState>
          </PanelRow>
        ) : (
          windows.map((w) => (
            <PanelRow key={w.id}>
              <div className="me-row">
                <Icon name={w.status === 'preferred' ? 'check_circle' : 'block'} />
                <span className="me-row__text">
                  <span className="me-row__title">
                    {formatDayTime(w.starts_at, zone)} – {formatDayTime(w.ends_at, zone)}
                  </span>
                  <span className="me-row__sub">
                    {w.status === 'preferred' ? 'Would rather play then' : "Can't play"}
                    {w.note ? ` · ${w.note}` : ''}
                  </span>
                </span>
                <RowMenu
                  label={`More for ${formatDayTime(w.starts_at, zone)}`}
                  items={[
                    { label: 'Edit…', icon: 'edit', onSelect: () => setEditing(w) },
                    {
                      label: 'Remove',
                      icon: 'delete',
                      destructive: true,
                      onSelect: () => void save(windows.filter((x) => x.id !== w.id), 'Removed.').catch(() => undefined),
                    },
                  ]}
                />
              </div>
            </PanelRow>
          ))
        )}
      </Panel>
      {saved && !error && (
        <p className="me-saved" role="status">
          {saved}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {editing && (
        <WindowDialog
          zone={zone}
          window={editing === 'new' ? null : editing}
          defaultDay={days[0]}
          onClose={() => setEditing(null)}
          onSave={async (w) => {
            const rest = editing === 'new' ? windows : windows.filter((x) => x.id !== editing.id)
            await save([...rest, w], editing === 'new' ? 'Blocked.' : 'Saved.')
            setEditing(null)
          }}
        />
      )}
    </div>
  )
}

function WindowDialog({
  zone,
  window: w,
  defaultDay,
  onClose,
  onSave,
}: {
  zone: string
  window: Window | null
  defaultDay?: string
  onClose: () => void
  onSave: (w: Window) => Promise<void>
}) {
  const start = w ? wallClock(w.starts_at, zone) : { date: defaultDay ?? new Date().toISOString().slice(0, 10), time: '09:00' }
  const end = w ? wallClock(w.ends_at, zone) : { date: start.date, time: '12:00' }
  const [startDate, setStartDate] = useState(start.date)
  const [startTime, setStartTime] = useState(start.time)
  const [endDate, setEndDate] = useState(end.date)
  const [endTime, setEndTime] = useState(end.time)
  const [status, setStatus] = useState<Status>((w?.status as Status) ?? 'unavailable')
  const [note, setNote] = useState(w?.note ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    const a = zonedInstant(startDate, startTime, zone)
    const b = zonedInstant(endDate, endTime, zone)
    if (b <= a) {
      setError('The end has to be after the start.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await onSave({ id: w?.id ?? 0, starts_at: a.toISOString(), ends_at: b.toISOString(), status, note: note.trim() || null })
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      size="sm"
      title={w ? 'Edit blocked time' : 'Block a time'}
      primaryLabel="Save"
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void submit()}
    >
      <div className="me-form">
        <div className="field-grid">
          <label className="field">
            <span className="field__label">From</span>
            <input className="input" type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">
              <span className="visually-hidden">From </span>Time
            </span>
            <input className="input" type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">Until</span>
            <input className="input" type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">
              <span className="visually-hidden">Until </span>Time
            </span>
            <input className="input" type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />
          </label>
        </div>
        <div className="field">
          <span className="field__label" id="win-status">
            This time is
          </span>
          <div className="segmented" role="radiogroup" aria-labelledby="win-status">
            {(
              [
                ['unavailable', "Can't play"],
                ['preferred', 'Would rather play'],
              ] as const
            ).map(([value, label]) => (
              <span key={value}>
                <input type="radio" id={`win-${value}`} name="win-status" checked={status === value} onChange={() => setStatus(value)} />
                <label htmlFor={`win-${value}`}>{label}</label>
              </span>
            ))}
          </div>
        </div>
        <label className="field">
          <span className="field__label">Note (optional)</span>
          <input className="input" value={note} maxLength={200} onChange={(e) => setNote(e.target.value)} />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}

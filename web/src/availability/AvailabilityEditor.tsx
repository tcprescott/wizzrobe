import { useState } from 'react'
import { Button, EmptyState, FormDialog, Icon, Panel, PanelRow, RowMenu, Spinner, type IconName } from '../components'
import { formatDayTime, wallClock, zonedInstant } from '../lib/time'
import { message } from '../me/data'
import '../me/me.css'

export type Status = 'available' | 'unavailable' | 'preferred'

export interface AvailabilityWindow {
  id: number
  starts_at: string
  ends_at: string
  status: Status
  note?: string | null
}

/**
 * How one caller's windows read. A player blocks out times (free until they
 * say otherwise); a volunteer declares them (not available until they do).
 */
export interface EditorMode {
  title: string
  intro: string
  addLabel: string
  editTitle: string
  newTitle: string
  added: string
  emptyIcon: IconName
  emptyTitle: string
  emptyBody: string
  statuses: { value: Status; label: string; row: string; icon: IconName }[]
  newStatus: Status
  /** Unstated time counts as free (player) rather than not offered (volunteer). */
  freeByDefault: boolean
}

export const BLOCK_MODE: EditorMode = {
  title: "When you can't play",
  intro: 'You count as free for the whole event until you block a time out. Opponents and staff see these when they pick a time.',
  addLabel: 'Block a time',
  editTitle: 'Edit blocked time',
  newTitle: 'Block a time',
  added: 'Blocked.',
  emptyIcon: 'event_available',
  emptyTitle: 'Free all event',
  emptyBody: "Block out the times you can't play and leave the rest alone.",
  statuses: [
    { value: 'unavailable', label: "Can't play", row: "Can't play", icon: 'block' },
    { value: 'preferred', label: 'Would rather play', row: 'Would rather play then', icon: 'check_circle' },
  ],
  newStatus: 'unavailable',
  freeByDefault: true,
}

export const DECLARE_MODE: EditorMode = {
  title: 'When you can work',
  intro: "Add the times you can volunteer. Coordinators see these when they fill shifts, and time you haven't added counts as not available.",
  addLabel: 'Add a time',
  editTitle: 'Edit time',
  newTitle: 'Add a time',
  added: 'Added.',
  emptyIcon: 'edit_calendar',
  emptyTitle: 'No times yet',
  emptyBody: 'Add the stretches you can work, and mark the ones you would most like.',
  statuses: [
    { value: 'available', label: 'Available', row: 'Available', icon: 'check' },
    { value: 'preferred', label: 'Preferred', row: 'Preferred', icon: 'check_circle' },
    { value: 'unavailable', label: "Can't", row: "Can't work", icon: 'block' },
  ],
  newStatus: 'available',
  freeByDefault: false,
}

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

type Window = AvailabilityWindow

interface EditorProps {
  mode: EditorMode
  zone: string
  startsOn?: string | null
  endsOn?: string | null
  data: Window[] | undefined
  reload: () => void
  put: (windows: { starts_at: string; ends_at: string; status: Status; note: string | null }[]) => Promise<unknown>
}

/** An availability editor: one bar per event day, then each window with Edit and Remove. */
export function AvailabilityEditor({ mode, zone, startsOn, endsOn, data, reload, put }: EditorProps) {
  const [editing, setEditing] = useState<Window | 'new' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  async function save(windows: Window[], ok: string) {
    setError(null)
    try {
      await put(windows.map((w) => ({ starts_at: w.starts_at, ends_at: w.ends_at, status: w.status, note: w.note ?? null })))
      setSaved(ok)
      reload()
    } catch (err) {
      setError(message(err))
      throw err
    }
  }

  if (!data) return <Spinner label="Loading your availability" />
  const windows = [...data].sort((a, b) => a.starts_at.localeCompare(b.starts_at))
  const days = eventDays(startsOn, endsOn)
  const label = (status: Status) => mode.statuses.find((x) => x.value === status)

  return (
    <div className="me-stack">
      <Panel
        title={mode.title}
        actions={
          <Button variant="primary" icon="add" onClick={() => setEditing('new')}>
            {mode.addLabel}
          </Button>
        }
      >
        <PanelRow>
          <p className="me-note">
            {mode.intro} Times are in {zone.replace(/_/g, ' ')}.
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
                    <div className={`me-bar__track${mode.freeByDefault ? '' : ' me-bar__track--declare'}`}>
                      {windows.map((w) => {
                        const a = Math.max(new Date(w.starts_at).getTime(), start)
                        const b = Math.min(new Date(w.ends_at).getTime(), start + span)
                        if (b <= a) return null
                        return (
                          <i
                            key={w.id}
                            className={`me-bar__block me-bar__block--${w.status}`}
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
            <EmptyState icon={mode.emptyIcon} title={mode.emptyTitle}>
              {mode.emptyBody}
            </EmptyState>
          </PanelRow>
        ) : (
          windows.map((w) => (
            <PanelRow key={w.id}>
              <div className="me-row">
                <Icon name={label(w.status)?.icon ?? 'event'} />
                <span className="me-row__text">
                  <span className="me-row__title">
                    {formatDayTime(w.starts_at, zone)} – {formatDayTime(w.ends_at, zone)}
                  </span>
                  <span className="me-row__sub">
                    {label(w.status)?.row ?? w.status}
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
          mode={mode}
          zone={zone}
          window={editing === 'new' ? null : editing}
          defaultDay={days[0]}
          onClose={() => setEditing(null)}
          onSave={async (w) => {
            const rest = editing === 'new' ? windows : windows.filter((x) => x.id !== editing.id)
            await save([...rest, w], editing === 'new' ? mode.added : 'Saved.')
            setEditing(null)
          }}
        />
      )}
    </div>
  )
}

function WindowDialog({
  mode,
  zone,
  window: w,
  defaultDay,
  onClose,
  onSave,
}: {
  mode: EditorMode
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
  const [status, setStatus] = useState<Status>(w?.status ?? mode.newStatus)
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
      title={w ? mode.editTitle : mode.newTitle}
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
            {mode.statuses.map(({ value, label }) => (
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

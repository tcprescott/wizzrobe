import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { Banner, Button, Dialog, EmptyState, FormDialog, IconButton, PageHeader, Panel, Spinner } from '../components'
import { effectiveZone, formatTime } from '../lib/time'
import { usePhone } from '../lib/usePhone'
import { done, message, useEventQuery } from '../me/data'
import { usePolling, useWorkQuery, type GridRole, type GridShift, type VolunteerSchedule } from './data'

const HOUR_PX = 56
const HOUR_MS = 3600 * 1000

function dayChip(day: string, phone: boolean): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString(undefined, { weekday: 'short', ...(phone ? {} : { day: 'numeric' }), timeZone: 'UTC' })
}

/** `3 PM` (or `15`) for the hour row `hour` hours after `midnight`, read on `zone` so a DST day stays right. */
function hourLabel(midnight: string, hour: number, zone: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', timeZone: zone }).format(new Date(new Date(midnight).getTime() + hour * HOUR_MS))
}

/** Who's on which role, hour by hour, for one event day: today's Full Schedule. */
export function VolunteerScheduleScreen() {
  const phone = usePhone()
  const query = useEventQuery()
  const [params, setParams] = useSearchParams()
  const day = params.get('day') ?? undefined
  const schedule = useWorkQuery(`schedule/${day ?? ''}`, () =>
    api.GET('/api/work/schedule', { params: { query: { ...query, ...(day ? { day } : {}) } } }).then(unwrap),
  )
  usePolling(schedule.reload, 60_000)
  const [open, setOpen] = useState<{ shift: GridShift; position: string } | null>(null)
  const [columns, setColumns] = useState(false)
  const data = schedule.data

  return (
    <>
      <PageHeader title="Volunteer schedule" lede="Who’s on which role, hour by hour." />
      {schedule.error && !data && (
        <Banner tone="error" action={<Button variant="outline" onClick={schedule.reload}>Try again</Button>}>
          {schedule.error.message}
        </Banner>
      )}
      {!data && !schedule.error && <Spinner label="Loading the schedule" />}
      {data && (
        <>
          <div className="work-toolbar">
            {data.days.length > 0 && (
              <div className="segmented" role="radiogroup" aria-label="Day">
                {data.days.map((d) => (
                  <span key={d}>
                    <input
                      type="radio"
                      id={`vday-${d}`}
                      name="vday"
                      checked={d === data.day}
                      onChange={() => setParams((p) => {
                        p.set('day', d)
                        return p
                      }, { replace: true })}
                    />
                    <label htmlFor={`vday-${d}`}>{dayChip(d, phone)}</label>
                  </span>
                ))}
              </div>
            )}
            <span className="work-toolbar__grow" />
            {data.roles.length > 0 &&
              (phone ? (
                <IconButton icon="view_column" label="Columns" onClick={() => setColumns(true)} />
              ) : (
                <Button variant="outline" icon="view_column" onClick={() => setColumns(true)}>
                  Columns{data.customized ? ' (yours)' : ''}
                </Button>
              ))}
          </div>
          <ScheduleBody data={data} onOpen={(shift, position) => setOpen({ shift, position })} />
          {(data.columns ?? []).length > 0 && <p className="vgrid__note">Times are in {effectiveZone(data.zone).replace(/_/g, ' ')}. {phone ? 'Swipe sideways for more roles. ' : ''}Tap a shift for everyone on it.</p>}
        </>
      )}
      {open && data && <ShiftDialog shift={open.shift} position={open.position} zone={effectiveZone(data.zone)} onClose={() => setOpen(null)} />}
      {columns && data && (
        <ColumnsDialog
          roles={data.roles}
          onClose={() => setColumns(false)}
          onSaved={() => {
            setColumns(false)
            schedule.reload()
          }}
        />
      )}
    </>
  )
}

function ScheduleBody({ data, onOpen }: { data: VolunteerSchedule; onOpen: (shift: GridShift, position: string) => void }) {
  const zone = effectiveZone(data.zone)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(id)
  }, [])

  if (!data.day) {
    return (
      <Panel>
        <EmptyState icon="event_busy" title="No event dates are set yet" />
      </Panel>
    )
  }
  const label = dayChip(data.day, false)
  if ((data.columns ?? []).length === 0) {
    return (
      <Panel>
        <EmptyState icon={data.hidden_with_shifts ? 'visibility_off' : 'event_busy'} title={data.hidden_with_shifts ? `Every role with shifts on ${label} is hidden` : `No shifts on ${label}`}>
          {data.hidden_with_shifts ? 'Use Columns to show them.' : undefined}
        </EmptyState>
      </Panel>
    )
  }
  const midnight = data.midnight ?? ''
  const hours = Array.from({ length: data.last_hour - data.first_hour }, (_, i) => data.first_hour + i)
  const height = hours.length * HOUR_PX
  const nowHour = midnight ? (now - new Date(midnight).getTime()) / HOUR_MS : -1
  const showNow = nowHour >= data.first_hour && nowHour < data.last_hour
  const nowTop = (nowHour - data.first_hour) * HOUR_PX

  return (
    <Panel>
      <div className="vgrid-scroll" tabIndex={0} aria-label={`Volunteer schedule for ${label}`} role="region">
        <div className="vgrid" style={{ gridTemplateColumns: `60px repeat(${(data.columns ?? []).length}, minmax(140px, 1fr))` }}>
          <div className="vgrid__corner" />
          {(data.columns ?? []).map((c) => (
            <div key={c.position_id} className="vgrid__head">
              {c.position}
            </div>
          ))}
          <div className="vgrid__hours" style={{ height }}>
            {hours.map((h, i) => (
              <span key={h} className="vgrid__hour" style={{ top: i * HOUR_PX }}>
                {hourLabel(midnight, h, zone)}
              </span>
            ))}
          </div>
          {(data.columns ?? []).map((c) => (
            <div key={c.position_id} className="vgrid__col" style={{ height }}>
              {hours.map((h, i) => (
                <span key={h} className="vgrid__line" style={{ top: i * HOUR_PX }} />
              ))}
              {showNow && <span className="vgrid__now" style={{ top: nowTop }} />}
              {c.shifts.map((s) => {
                const mine = s.people.some((p) => p.is_viewer)
                const top = (s.start_hour - data.first_hour) * HOUR_PX + 2
                const tall = (s.end_hour - s.start_hour) * HOUR_PX - 4
                const width = 100 / s.lanes
                const names = s.people.filter((p) => !p.called_off).map((p) => (p.is_viewer ? `${p.name} (you)` : p.name))
                const range = `${formatTime(s.starts_at, zone)}–${formatTime(s.ends_at, zone)}`
                return (
                  <button
                    key={`${s.starts_at}-${s.lane}-${s.label}`}
                    type="button"
                    className={`vgrid__shift${mine ? ' vgrid__shift--mine' : s.open_slots > 0 ? ' vgrid__shift--short' : ''}`}
                    style={{ top, height: tall, left: `calc(${s.lane * width}% + 3px)`, width: `calc(${width}% - 6px)` }}
                    onClick={() => onOpen(s, c.position)}
                    aria-label={`${c.position}, ${range}: ${names.join(', ') || 'nobody yet'}${s.open_slots ? `, ${s.open_slots} open` : ''}`}
                  >
                    <b>{range}</b>
                    {names.join(', ') || 'Nobody yet'}
                    {s.open_slots > 0 && names.length > 0 && ` · ${s.open_slots} open`}
                  </button>
                )
              })}
            </div>
          ))}
        </div>
      </div>
    </Panel>
  )
}

function ShiftDialog({ shift, position, zone, onClose }: { shift: GridShift; position: string; zone: string; onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} size="sm" title={position} description={`${formatTime(shift.starts_at, zone)}–${formatTime(shift.ends_at, zone)}${shift.label ? ` · ${shift.label}` : ''}`}>
      <ul className="member-results">
        {shift.people.map((p) => (
          <li key={p.name} className={p.is_viewer ? 'desk-row__name' : undefined}>
            {p.is_viewer ? `${p.name} (you)` : p.name}
            {p.called_off ? ' · sent home' : ''}
          </li>
        ))}
      </ul>
      {shift.open_slots > 0 && (
        <p className="desk-row__sub">
          {shift.open_slots} of {shift.slots_needed} {shift.slots_needed === 1 ? 'place' : 'places'} still open.
        </p>
      )}
    </Dialog>
  )
}

function ColumnsDialog({ roles, onClose, onSaved }: { roles: GridRole[]; onClose: () => void; onSaved: () => void }) {
  const query = useEventQuery()
  const [list, setList] = useState(roles)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function move(i: number, by: number) {
    const j = i + by
    if (j < 0 || j >= list.length) return
    const next = [...list]
    const [item] = next.splice(i, 1)
    if (item) next.splice(j, 0, item)
    setList(next)
  }

  async function run(write: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await write()
      onSaved()
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
      title="Columns"
      description="Pick the roles to show and their order. Saved for you, here and on the website."
      primaryLabel="Save"
      busy={busy}
      busyLabel="Saving…"
      extra={[{ label: 'Reset to everyone’s default', icon: 'restart_alt', onSelect: () => void run(() => api.DELETE('/api/work/schedule/columns', { params: { query } }).then(done)) }]}
      onPrimary={() =>
        void run(() =>
          api
            .PUT('/api/work/schedule/columns', { params: { query }, body: { columns: list.map((r) => ({ position_id: r.position_id, visible: r.visible })) } })
            .then(unwrap),
        )
      }
    >
      <ul className="columns-list">
        {list.map((r, i) => (
          <li key={r.position_id}>
            <label>
              <input type="checkbox" checked={r.visible} onChange={(e) => setList(list.map((x) => (x.position_id === r.position_id ? { ...x, visible: e.target.checked } : x)))} />
              {r.name}
            </label>
            <IconButton icon="arrow_upward" label={`Move ${r.name} up`} disabled={i === 0} onClick={() => move(i, -1)} />
            <IconButton icon="arrow_downward" label={`Move ${r.name} down`} disabled={i === list.length - 1} onClick={() => move(i, 1)} />
          </li>
        ))}
      </ul>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </FormDialog>
  )
}

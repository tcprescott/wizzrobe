import { useId, useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Button, Chip, Dialog, FormDialog, RowMenu, Spinner, type MenuItem } from '../../../components'
import { effectiveZone, formatDayTime } from '../../../lib/time'
import { ListCard, LoadError, SettingsCard, useDraft } from '../../kit'
import { useEventParams, useEventSetting } from '../data'

type Station = Schemas['StationResponse']
type Screen = Schemas['RoomScreenResponse']
type Side = NonNullable<Station['side']>

const SIDES: { value: Side; label: string }[] = [
  { value: 'left', label: 'Left' },
  { value: 'right', label: 'Right' },
]

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function stationSub(s: Station): string {
  const side = SIDES.find((x) => x.value === s.side)?.label
  return [s.section, side && `${side} side`, s.position != null && `seat ${s.position}`].filter(Boolean).join(' · ') || 'No section, side or seat'
}

interface StationForm {
  name: string
  section: string
  side: Side | ''
  position: string
  sort_order: string
}

function StationDialog({
  station,
  onClose,
  onDone,
}: {
  station: Station | null
  onClose: () => void
  onDone: (note: string) => void
}) {
  const id = useId()
  const params = useEventParams()
  const [form, setForm] = useState<StationForm>({
    name: station?.name ?? '',
    section: station?.section ?? '',
    side: station?.side ?? '',
    position: station?.position != null ? String(station.position) : '',
    sort_order: String(station?.sort_order ?? 0),
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const patch = (part: Partial<StationForm>) => setForm({ ...form, ...part })

  async function submit() {
    setBusy(true)
    setError(null)
    const body = {
      name: form.name,
      section: form.section || null,
      side: form.side || null,
      position: form.position === '' ? null : Math.trunc(Number(form.position)),
      sort_order: Math.trunc(Number(form.sort_order) || 0),
    }
    try {
      if (station) {
        await api
          .PATCH('/api/event-settings/stations/{station_id}', {
            params: { ...params.params, path: { station_id: station.id } },
            body,
          })
          .then(unwrap)
        onDone(`${body.name} saved.`)
      } else {
        await api.POST('/api/event-settings/stations', { ...params, body }).then(unwrap)
        onDone(`${body.name} added.`)
      }
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={station ? `Edit ${station.name}` : 'Add a station'}
      primaryLabel={station ? 'Save' : 'Add station'}
      busy={busy}
      busyLabel={station ? 'Saving…' : 'Adding…'}
      onPrimary={() => void submit()}
    >
      <form
        className="settings-stack"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <div className="field-grid">
          <label className="field">
            <span className="field__label">Name</span>
            <input className="input" maxLength={50} required value={form.name} onChange={(e) => patch({ name: e.target.value })} />
          </label>
          <label className="field">
            <span className="field__label">Section</span>
            <input className="input" maxLength={50} value={form.section} onChange={(e) => patch({ section: e.target.value })} />
          </label>
          <label className="field">
            <span className="field__label">Side</span>
            <select className="input" value={form.side} onChange={(e) => patch({ side: e.target.value as Side | '' })}>
              <option value="">Not set</option>
              {SIDES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field__label">Seat number</span>
            <input
              className="input"
              type="number"
              inputMode="numeric"
              min={1}
              value={form.position}
              onChange={(e) => patch({ position: e.target.value })}
            />
          </label>
          <div className="field">
            <label className="field__label" htmlFor={`${id}-order`}>
              Order
            </label>
            <input
              id={`${id}-order`}
              className="input"
              type="number"
              inputMode="numeric"
              value={form.sort_order}
              aria-describedby={`${id}-order-hint`}
              onChange={(e) => patch({ sort_order: e.target.value })}
            />
            <span className="field__hint" id={`${id}-order-hint`}>
              Lower numbers come first in proctors' lists.
            </span>
          </div>
        </div>
        <p className="settings-note">
          Check-in uses side and seat number to put a match's two players on opposite sides with free neighbours. Both are
          optional.
        </p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </FormDialog>
  )
}

function StationsCard() {
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('stations', () => api.GET('/api/event-settings/stations', params).then(unwrap))
  const [editing, setEditing] = useState<Station | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Station | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function run(action: () => Promise<string>) {
    setBusy(true)
    setFailure(null)
    try {
      setNote(await action())
      setDeleting(null)
      reload()
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  const path = (s: Station) => ({ params: { ...params.params, path: { station_id: s.id } } })
  const toggle = (s: Station) =>
    run(async () => {
      await api.PATCH('/api/event-settings/stations/{station_id}', { ...path(s), body: { is_active: !s.is_active } }).then(unwrap)
      return s.is_active ? `${s.name} is off. Proctors can't pick it.` : `${s.name} is back on.`
    })
  const remove = (s: Station) =>
    run(async () => {
      const r = await api.DELETE('/api/event-settings/stations/{station_id}', path(s))
      if (!r.response.ok) unwrap(r)
      return `${s.name} deleted.`
    })

  const unsided = (data ?? []).filter((s) => s.is_active && !s.side)
  return (
    <ListCard
      title="Stations"
      icon="chair"
      subtitle="Once any exist, proctors pick from this list, and a station a live match is using can't be given out twice. Turn one off rather than deleting it to keep past matches readable."
      action={
        <Button icon="add" onClick={() => setEditing('new')}>
          Add station
        </Button>
      }
    >
      {error && !data ? (
        <div className="settings-card__body">
          <LoadError error={error} onRetry={reload} />
        </div>
      ) : !data ? (
        <div className="settings-card__body">
          <Spinner label="Loading stations" />
        </div>
      ) : data.length === 0 ? (
        <div className="settings-card__body">
          <p className="settings-note">
            No stations yet. Proctors type the station by hand; add stations to pick from a list and block double-booking.
          </p>
        </div>
      ) : (
        <ul className="settings-list">
          {data.map((s) => {
            const items: MenuItem[] = [
              { label: 'Edit…', icon: 'edit', onSelect: () => setEditing(s) },
              s.is_active
                ? { label: 'Turn off', icon: 'toggle_off', onSelect: () => void toggle(s) }
                : { label: 'Turn on', icon: 'toggle_on', onSelect: () => void toggle(s) },
              { label: 'Delete…', icon: 'delete', destructive: true, onSelect: () => setDeleting(s) },
            ]
            return (
              <li key={s.id} className="settings-list__row">
                <span className="settings-row__text">
                  <span className="settings-row__title">
                    {s.name}
                    {!s.is_active && <Chip tone="neutral">Off</Chip>}
                  </span>
                  <span className="settings-row__sub">{stationSub(s)}</span>
                </span>
                <RowMenu label={`Actions for ${s.name}`} items={items} />
              </li>
            )
          })}
        </ul>
      )}
      {(unsided.length > 0 || note || (failure && !deleting)) && (
        <div className="settings-card__body">
          {unsided.length > 0 && (
            <p className="settings-warning">
              {unsided.length === 1 ? 'One station has' : `${unsided.length} stations have`} no side, so check-in can't seat
              players across the room there: {unsided.map((s) => s.name).join(', ')}.
            </p>
          )}
          {note && (
            <p className="settings-saved" role="status">
              {note}
            </p>
          )}
          {failure && !deleting && (
            <p className="form-error" role="alert">
              {failure}
            </p>
          )}
        </div>
      )}
      {editing && (
        <StationDialog
          station={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onDone={(n) => {
            setEditing(null)
            setNote(n)
            reload()
          }}
        />
      )}
      <FormDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title={deleting ? `Delete ${deleting.name}?` : 'Delete station'}
        description="Past matches keep showing where they were played. Turning it off keeps it for later instead."
        primaryLabel="Delete"
        destructive
        busy={busy}
        onPrimary={() => deleting && void remove(deleting)}
        size="sm"
      >
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </FormDialog>
    </ListCard>
  )
}

function StationNamesCard() {
  const id = useId()
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('station-format', () =>
    api.GET('/api/event-settings/station-format', params).then(unwrap),
  )
  const draft = useDraft(data && { format: data.format })
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading station names" />
  const value = draft.value
  return (
    <SettingsCard
      title="Station names"
      icon="pin"
      subtitle="What a proctor may type when they give a station by hand."
      dirty={draft.dirty}
      onDiscard={draft.discard}
      onSave={async () => {
        await api.PUT('/api/event-settings/station-format', { ...params, body: value }).then(unwrap)
        draft.discard()
        reload()
      }}
    >
      <div className="field settings-text-field">
        <label className="field__label" htmlFor={id}>
          Format
        </label>
        <select
          id={id}
          className="input"
          value={value.format}
          onChange={(e) => draft.set({ format: e.target.value as typeof value.format })}
        >
          {data.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
    </SettingsCard>
  )
}

function RoomScreensCard() {
  const labelId = useId()
  const params = useEventParams()
  const { data, error, reload } = useEventSetting('room-screens', () =>
    api.GET('/api/event-settings/room-screens', params).then(unwrap),
  )
  const [issuing, setIssuing] = useState(false)
  const [label, setLabel] = useState('')
  const [url, setUrl] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [revoking, setRevoking] = useState<Screen | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function issue() {
    setBusy(true)
    setFailure(null)
    try {
      const issued = await api.POST('/api/event-settings/room-screens', { ...params, body: { label } }).then(unwrap)
      setIssuing(false)
      setLabel('')
      setCopied(false)
      setUrl(issued.url)
      reload()
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  async function revoke(s: Screen) {
    setBusy(true)
    setFailure(null)
    try {
      const r = await api.DELETE('/api/event-settings/room-screens/{screen_id}', {
        params: { ...params.params, path: { screen_id: s.id } },
      })
      if (!r.response.ok) unwrap(r)
      setRevoking(null)
      setNote(`${s.label} revoked. That machine gets a "not found" page on its next refresh.`)
      reload()
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ListCard
      title="Room screens"
      icon="desktop_windows"
      subtitle="A read-only board of matches whose seed is rolled and not finished, for a machine in the room nobody signs in on. Each one is an unlisted link that opens that page and nothing else."
      action={
        <Button
          icon="add"
          onClick={() => {
            setFailure(null)
            setIssuing(true)
          }}
        >
          Add screen
        </Button>
      }
    >
      {error && !data ? (
        <div className="settings-card__body">
          <LoadError error={error} onRetry={reload} />
        </div>
      ) : !data ? (
        <div className="settings-card__body">
          <Spinner label="Loading room screens" />
        </div>
      ) : data.length === 0 ? (
        <div className="settings-card__body">
          <p className="settings-note">No room screens yet.</p>
        </div>
      ) : (
        <ul className="settings-list">
          {data.map((s) => (
            <li key={s.id} className="settings-list__row">
              <span className="settings-row__text">
                <span className="settings-row__title">
                  {s.label}
                  {s.revoked && <Chip tone="neutral">Revoked</Chip>}
                </span>
                <span className="settings-row__sub">
                  {s.last_used_at ? `Last used ${formatDayTime(s.last_used_at, effectiveZone(null))}` : 'Never used'} · added by{' '}
                  {s.issued_by ?? 'someone since removed'}
                </span>
              </span>
              {!s.revoked && (
                <RowMenu
                  label={`Actions for ${s.label}`}
                  items={[{ label: 'Revoke…', icon: 'block', destructive: true, onSelect: () => setRevoking(s) }]}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {note && (
        <div className="settings-card__body">
          <p className="settings-saved" role="status">
            {note}
          </p>
        </div>
      )}
      <FormDialog
        open={issuing}
        onOpenChange={setIssuing}
        title="Add a room screen"
        primaryLabel="Add screen"
        busy={busy}
        busyLabel="Adding…"
        onPrimary={() => void issue()}
        size="sm"
      >
        <form
          className="settings-stack"
          onSubmit={(e) => {
            e.preventDefault()
            void issue()
          }}
        >
          <div className="field">
            <label className="field__label" htmlFor={labelId}>
              Label
            </label>
            <input
              id={labelId}
              className="input"
              maxLength={100}
              placeholder="Room A desk PC"
              value={label}
              aria-describedby={`${labelId}-hint`}
              onChange={(e) => setLabel(e.target.value)}
            />
            <span className="field__hint" id={`${labelId}-hint`}>
              So you can tell the machines apart later.
            </span>
          </div>
          {failure && (
            <p className="form-error" role="alert">
              {failure}
            </p>
          )}
        </form>
      </FormDialog>
      <Dialog
        open={url !== null}
        onOpenChange={(open) => !open && setUrl(null)}
        title="The room screen's link"
        description="Copy it now: this is the only time it's shown. Anyone with the link can read the seeds board, so bookmark it on that machine rather than posting it anywhere."
        size="sm"
        footer={
          <>
            <span className="dialog__spacer" />
            <Button
              variant="ghost"
              icon="content_copy"
              onClick={() => void navigator.clipboard?.writeText(url ?? '').then(() => setCopied(true))}
            >
              {copied ? 'Copied' : 'Copy link'}
            </Button>
            <Button variant="primary" onClick={() => setUrl(null)}>
              Done
            </Button>
          </>
        }
      >
        <input className="input" readOnly aria-label="Room screen link" value={url ?? ''} onFocus={(e) => e.target.select()} />
      </Dialog>
      <FormDialog
        open={revoking !== null}
        onOpenChange={(open) => !open && setRevoking(null)}
        title={revoking ? `Revoke ${revoking.label}?` : 'Revoke room screen'}
        description="The machine using it gets a “not found” page on its next refresh, and the link can't be brought back."
        primaryLabel="Revoke"
        destructive
        busy={busy}
        onPrimary={() => revoking && void revoke(revoking)}
        size="sm"
      >
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </FormDialog>
    </ListCard>
  )
}

export function Venue() {
  return (
    <>
      <StationsCard />
      <StationNamesCard />
      <RoomScreensCard />
    </>
  )
}

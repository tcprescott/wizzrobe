import { useId, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { FormDialog } from '../../components'
import { COPY_GROUPS, count, type EventRow } from './data'

type Mode = { kind: 'create' } | { kind: 'copy'; from: EventRow } | { kind: 'rename'; event: EventRow }

/** New event (optionally copying setup from another), or rename and re-date one. */
export function EventFormDialog({
  mode,
  defaultName,
  onClose,
  onDone,
}: {
  mode: Mode
  defaultName?: string
  onClose: () => void
  onDone: (event: EventRow, note: string) => void
}) {
  const id = useId()
  const editing = mode.kind === 'rename' ? mode.event : null
  const [name, setName] = useState(editing?.name ?? '')
  const [slug, setSlug] = useState(editing?.slug ?? '')
  const [starts, setStarts] = useState(editing?.starts_on ?? '')
  const [ends, setEnds] = useState(editing?.ends_on ?? '')
  const [copy, setCopy] = useState<string[]>(COPY_GROUPS.map((g) => g.key))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const slugLocked = editing !== null && editing.status !== 'draft'

  async function submit() {
    setBusy(true)
    setError(null)
    try {
      if (editing) {
        const body: Schemas['EventUpdateRequest'] = { name, starts_on: starts || null, ends_on: ends || null }
        if (!slugLocked && slug !== editing.slug) body.slug = slug
        const event = await api.PATCH('/api/events/{slug}', { params: { path: { slug: editing.slug } }, body }).then(unwrap)
        onDone(event, `${event.name} saved.`)
      } else {
        const result = await api
          .POST('/api/events', {
            body: {
              name,
              slug: slug || null,
              starts_on: starts || null,
              ends_on: ends || null,
              copy_from: mode.kind === 'copy' ? mode.from.slug : null,
              copy: mode.kind === 'copy' ? copy : [],
            },
          })
          .then(unwrap)
        onDone(result.event, `${result.event.name} created as a draft.`)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const title = editing ? `Rename ${editing.name}` : mode.kind === 'copy' ? `New event from ${mode.from.name}` : 'New event'
  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={title}
      primaryLabel={editing ? 'Save' : 'Create draft'}
      busy={busy}
      busyLabel={editing ? 'Saving…' : 'Creating…'}
      onPrimary={() => void submit()}
    >
      <form
        className="settings-stack"
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
      >
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" maxLength={120} value={name} required onChange={(e) => setName(e.target.value)} />
        </label>
        <div className="field">
          <label className="field__label" htmlFor={`${id}-slug`}>
            Link name
          </label>
          <input
            id={`${id}-slug`}
            className="input"
            maxLength={64}
            value={slug}
            disabled={slugLocked}
            placeholder={editing ? undefined : 'Made from the name when left blank'}
            aria-describedby={`${id}-slug-hint`}
            onChange={(e) => setSlug(e.target.value.toLowerCase())}
          />
          <span className="field__hint" id={`${id}-slug-hint`}>
            {slugLocked
              ? 'Links already point at this event, so its link name stays.'
              : "Appears in links as /e/<link name>. It can't change once the event is live."}
          </span>
        </div>
        <div className="field-grid">
          <label className="field">
            <span className="field__label">Starts</span>
            <input className="input" type="date" value={starts} onChange={(e) => setStarts(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">Ends</span>
            <input
              className="input"
              type="date"
              value={ends}
              min={starts || undefined}
              onChange={(e) => setEnds(e.target.value)}
            />
          </label>
        </div>
        {mode.kind === 'copy' && (
          <fieldset className="settings-radios">
            <legend className="field__label">Copy from {mode.from.name}</legend>
            {COPY_GROUPS.map((g) => (
              <label key={g.key} className="settings-row settings-row--radio">
                <input
                  type="checkbox"
                  checked={copy.includes(g.key)}
                  onChange={(e) => setCopy(e.target.checked ? [...copy, g.key] : copy.filter((k) => k !== g.key))}
                />
                <span className="settings-row__text">
                  <span className="settings-row__title">{g.label}</span>
                  <span className="settings-row__sub">{g.hint}</span>
                </span>
              </label>
            ))}
            <span className="field__hint">People, matches and shifts are never copied.</span>
          </fieldset>
        )}
        {!editing && (
          <p className="settings-note">
            Starts as a draft only staff can see. Go live when you're ready
            {defaultName ? `; ${defaultName} keeps running until you archive it` : ''}.
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </form>
    </FormDialog>
  )
}

export type Confirm =
  | { kind: 'go-live'; event: EventRow }
  | { kind: 'archive'; event: EventRow; summary?: Schemas['EventSummaryResponse'] }
  | { kind: 'unarchive'; event: EventRow }
  | { kind: 'delete'; event: EventRow }

/** Go live, archive, unarchive and delete: each says what happens and confirms first. */
export function ConfirmEventDialog({
  confirm,
  events,
  onClose,
  onDone,
}: {
  confirm: Confirm
  events: EventRow[]
  onClose: () => void
  onDone: (note: string, gone?: boolean) => void
}) {
  const { event } = confirm
  const others = events.filter((e) => e.status === 'live' && e.slug !== event.slug)
  const [newDefault, setNewDefault] = useState(others[0]?.slug ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const path = { params: { path: { slug: event.slug } } }

  async function run() {
    setBusy(true)
    setError(null)
    try {
      if (confirm.kind === 'go-live') {
        await api.POST('/api/events/{slug}/go-live', path).then(unwrap)
        onDone(`${event.name} is live.`)
      } else if (confirm.kind === 'archive') {
        await api
          .POST('/api/events/{slug}/archive', { ...path, body: { new_default: event.is_default ? newDefault || null : null } })
          .then(unwrap)
        onDone(`${event.name} is archived.`)
      } else if (confirm.kind === 'unarchive') {
        await api.POST('/api/events/{slug}/unarchive', path).then(unwrap)
        onDone(`${event.name} is live again.`)
      } else {
        const r = await api.DELETE('/api/events/{slug}', path)
        if (!r.response.ok) unwrap(r)
        onDone(`${event.name} deleted.`, true)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  let title: string
  let primary: string
  let body: string
  let destructive = false
  if (confirm.kind === 'go-live') {
    title = `Go live with ${event.name}?`
    primary = 'Go live'
    body = `Everyone in the community can see ${event.name} once it's live. The default event stays the default until you make ${event.name} the default.`
  } else if (confirm.kind === 'archive') {
    title = `Archive ${event.name}?`
    primary = 'Archive'
    destructive = true
    body = `${event.name} stays visible to everyone and becomes read-only. Its race rooms close, and the Discord roles its rules gave are taken back. You can unarchive it later.`
  } else if (confirm.kind === 'unarchive') {
    title = `Unarchive ${event.name}?`
    primary = 'Unarchive'
    body = `${event.name} goes back to live and can be edited again.`
  } else {
    title = `Delete ${event.name}?`
    primary = 'Delete'
    destructive = true
    body = `${event.name} and the setup copied into it (tournaments, positions, stages) are deleted. This can't be undone.`
  }

  const s = confirm.kind === 'archive' ? confirm.summary : undefined
  const open = s
    ? [
        s.open_matches ? `${count(s.open_matches, 'match', 'matches')} not finished` : '',
        s.future_shifts ? `${count(s.future_shifts, 'volunteer shift', 'volunteer shifts')} still to come` : '',
        s.open_race_rooms ? `${count(s.open_race_rooms, 'race room', 'race rooms')} open` : '',
      ].filter(Boolean)
    : []

  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={title}
      description={body}
      primaryLabel={primary}
      destructive={destructive}
      busy={busy}
      onPrimary={() => void run()}
      size="sm"
    >
      {open.length > 0 && <p className="settings-warning">Still open: {open.join(', ')}.</p>}
      {confirm.kind === 'archive' && event.is_default && (
        <label className="field">
          <span className="field__label">New default event</span>
          {others.length > 0 ? (
            <select className="input" value={newDefault} onChange={(e) => setNewDefault(e.target.value)}>
              {others.map((o) => (
                <option key={o.slug} value={o.slug}>
                  {o.name}
                </option>
              ))}
            </select>
          ) : (
            <span className="field__hint">Make another event live first: the default needs a live event to take over.</span>
          )}
        </label>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </FormDialog>
  )
}

import { useEffect, useState } from 'react'
import { Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Chip, EmptyState, FormDialog, PageHeader, Panel, PanelRow, RowMenu, Spinner, type MenuItem, type Tone } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { Detail, DetailEmpty, DetailLine, DetailSection, SideListCard, SideListLayout, SideListRow } from '../kit/SideList'
import { useAdmin } from '../kit/useAdminNav'
import { message, useAction, useAdminData } from '../peopleKit'

type Board = Schemas['CheckInBoardResponse']
type CheckInEvent = Schemas['CheckInAdminEventResponse']
type Status = Schemas['CheckInEventStatus']

const STATUS: Record<string, { label: string; tone: Tone }> = {
  draft: { label: 'Draft', tone: 'neutral' },
  open: { label: 'Open', tone: 'ok' },
  closed: { label: 'Closed', tone: 'cancelled' },
}

function StatusChip({ status }: { status: string }) {
  const s = STATUS[status] ?? { label: status, tone: 'neutral' as Tone }
  return <Chip tone={s.tone}>{s.label}</Chip>
}

function FormError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p className="form-error" role="alert">
      {error}
    </p>
  )
}

function syncLine(e: CheckInEvent, zone: string): string {
  if (!e.matcherino_venue_id) return 'Walk-ups and comps only'
  if (!e.last_synced_at) return 'Not synced yet'
  return `Synced ${formatDayTime(e.last_synced_at, zone)}${e.last_sync_count !== null && e.last_sync_count !== undefined ? ` · ${e.last_sync_count} with badges` : ''}`
}

function EventDialog({
  open,
  onOpenChange,
  event,
  board,
  onSaved,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  event: CheckInEvent | null
  board: Board
  onSaved: (id: number) => void
}) {
  const blank = { name: '', venue: '', status: 'draft' as Status, interval: '5', comp_roles: [] as string[], comp_volunteers: false }
  const [form, setForm] = useState(blank)
  const [venue, setVenue] = useState<string | null>(null)
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (!open) return
    setError(null)
    setVenue(null)
    setForm(
      event
        ? {
            name: event.name,
            venue: event.matcherino_venue_id?.toString() ?? '',
            status: event.status as Status,
            interval: String(event.sync_interval_minutes),
            comp_roles: event.comp_roles,
            comp_volunteers: event.comp_volunteers,
          }
        : blank,
    )
  }, [open, event, setError])
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }))

  async function checkVenue() {
    const id = Number(form.venue)
    if (!id) return
    setVenue(null)
    await run('venue', async () => {
      const v = await api.GET('/api/admin/check-in/venues/{venue_id}', { params: { path: { venue_id: id } } }).then(unwrap)
      setVenue(`${v.title}: ${v.badge_types.join(', ') || 'no badge types yet'}`)
    })
  }
  async function save() {
    const body = {
      name: form.name.trim(),
      matcherino_venue_id: form.venue ? Number(form.venue) : null,
      status: form.status,
      sync_interval_minutes: Number(form.interval) || 5,
      comp_roles: form.comp_roles,
      comp_volunteers: form.comp_volunteers,
    }
    let id = event?.id ?? 0
    const ok = await run('save', async () => {
      const saved = event
        ? await api.PUT('/api/admin/check-in/events/{event_id}', { params: { path: { event_id: event.id } }, body }).then(unwrap)
        : await api.POST('/api/admin/check-in/events', { body }).then(unwrap)
      id = saved.id
    })
    if (ok) {
      onOpenChange(false)
      onSaved(id)
    }
  }
  const toggleRole = (role: string, on: boolean) =>
    set('comp_roles', on ? [...form.comp_roles, role] : form.comp_roles.filter((r) => r !== role))

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={event ? `Edit ${event.name}` : 'New check-in event'}
      primaryLabel={event ? 'Save' : 'Create'}
      busy={busy === 'save'}
      onPrimary={() => void save()}
    >
      <label className="field">
        <span className="field__label">Name</span>
        <input className="input" value={form.name} maxLength={120} onChange={(e) => set('name', e.target.value)} />
      </label>
      <div className="field">
        <label className="field__label" htmlFor="ci-venue">
          Matcherino venue
        </label>
        <div className="admin-inline">
          <input id="ci-venue" className="input" inputMode="numeric" value={form.venue} onChange={(e) => set('venue', e.target.value.replace(/\D/g, ''))} />
          <Button variant="outline" size="sm" disabled={!form.venue} busy={busy === 'venue'} onClick={() => void checkVenue()}>
            Check
          </Button>
        </div>
        <span className="field__hint">{venue ?? 'The number in the venue’s Matcherino link. Leave empty for walk-ups and comps only.'}</span>
      </div>
      <div className="admin-fields">
        <label className="field">
          <span className="field__label">Status</span>
          <select className="input" value={form.status} onChange={(e) => set('status', e.target.value as Status)}>
            <option value="draft">Draft</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
          </select>
          <span className="field__hint">Only open events are on the desk.</span>
        </label>
        <label className="field">
          <span className="field__label">Sync every (minutes)</span>
          <input className="input" type="number" min={1} max={120} value={form.interval} onChange={(e) => set('interval', e.target.value)} />
        </label>
      </div>
      <div className="field" role="group" aria-labelledby="ci-comps">
        <span className="field__label" id="ci-comps">
          Comp badges for
        </span>
        <div className="admin-checks">
          {board.comp_role_options.map((o) => (
            <label key={o.value} className="admin-check">
              <input type="checkbox" checked={form.comp_roles.includes(o.value)} onChange={(e) => toggleRole(o.value, e.target.checked)} />
              {o.label}
            </label>
          ))}
          <label className="admin-check">
            <input type="checkbox" checked={form.comp_volunteers} onChange={(e) => set('comp_volunteers', e.target.checked)} />
            Volunteers{board.volunteer_comp_hours ? ` with ${board.volunteer_comp_hours}+ hours` : ''}
          </label>
        </div>
      </div>
      <FormError error={error} />
    </FormDialog>
  )
}

function Tiers({ event, board }: { event: CheckInEvent; board: Board }) {
  const { data, error, reload } = useAdminData(`check-in/${event.id}/tiers`, () =>
    api.GET('/api/admin/check-in/events/{event_id}/tiers', { params: { path: { event_id: event.id } } }).then(unwrap),
  )
  const { busy, error: saveError, run } = useAction()
  if (!event.matcherino_venue_id) return null
  async function setLanyard(tierId: number, lanyard: string) {
    if (
      await run(String(tierId), () =>
        api
          .PUT('/api/admin/check-in/tiers/{tier_id}/lanyard', {
            params: { path: { tier_id: tierId } },
            body: { lanyard: lanyard as Schemas['CheckInTierLanyard'] },
          })
          .then(unwrap),
      )
    )
      reload()
  }
  return (
    <DetailSection title="Badge types">
      {error && !data ? (
        <Banner tone="error">{error.message}</Banner>
      ) : !data ? (
        <Spinner label="Loading badge types" />
      ) : data.length === 0 ? (
        <p className="admin-muted">None yet. They show up after the first sync.</p>
      ) : (
        data.map((t) => (
          <DetailLine
            key={t.id}
            icon="badge"
            sub={`$${(t.amount_cents / 100).toFixed(2)}`}
            end={
              <select
                className="input admin-select-sm"
                aria-label={`Lanyard for ${t.title}`}
                value={t.lanyard}
                disabled={busy === String(t.id)}
                onChange={(e) => void setLanyard(t.id, e.target.value)}
              >
                {board.lanyard_options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            }
          >
            {t.title}
          </DetailLine>
        ))
      )}
      <FormError error={saveError} />
    </DetailSection>
  )
}

function EventDetail({ event, board, onChange, onDeleted }: { event: CheckInEvent; board: Board; onChange: () => void; onDeleted: () => void }) {
  const zone = effectiveZone(board.zone)
  const [dialog, setDialog] = useState<'edit' | 'delete' | null>(null)
  const { busy, error, run } = useAction()
  const roleLabels = new Map(board.comp_role_options.map((o) => [o.value, o.label]))
  const comps = event.comp_roles.map((r) => roleLabels.get(r) ?? r)
  if (event.comp_volunteers) comps.push(board.volunteer_comp_hours ? `volunteers with ${board.volunteer_comp_hours}+ hours` : 'volunteers')

  async function setStatus(status: Status) {
    const body = {
      name: event.name,
      matcherino_venue_id: event.matcherino_venue_id ?? null,
      status,
      sync_interval_minutes: event.sync_interval_minutes,
      comp_roles: event.comp_roles,
      comp_volunteers: event.comp_volunteers,
    }
    if (await run('status', () => api.PUT('/api/admin/check-in/events/{event_id}', { params: { path: { event_id: event.id } }, body }).then(unwrap))) onChange()
  }
  async function remove() {
    if (await run('delete', () => api.DELETE('/api/admin/check-in/events/{event_id}', { params: { path: { event_id: event.id } } }).then(unwrap))) {
      setDialog(null)
      onDeleted()
    }
  }

  const menu: MenuItem[] = []
  if (event.status !== 'closed') menu.push({ label: 'Close check-in', icon: 'block', onSelect: () => void setStatus('closed') })
  menu.push({ label: 'Delete…', icon: 'delete', destructive: true, onSelect: () => setDialog('delete') })

  return (
    <Detail
      title={event.name}
      sub={
        <>
          {event.matcherino_venue_id ? `Matcherino venue ${event.matcherino_venue_id}` : 'No Matcherino venue'} · <StatusChip status={event.status} />
        </>
      }
      actions={
        <>
          {event.status !== 'open' && (
            <Button variant="primary" size="sm" icon="how_to_reg" busy={busy === 'status'} onClick={() => void setStatus('open')}>
              Open for check-in
            </Button>
          )}
          <Button variant="outline" size="sm" icon="edit" onClick={() => setDialog('edit')}>
            Edit
          </Button>
          <RowMenu label={`More for ${event.name}`} items={menu} />
        </>
      }
    >
      {error && !dialog && <Banner tone="error">{error}</Banner>}
      {event.status === 'open' && <Banner tone="info">This roster is on the check-in desk now.</Banner>}
      <DetailSection title="Sync">
        <DetailLine icon="sync" sub={event.matcherino_venue_id ? `Every ${event.sync_interval_minutes} min while it's open` : undefined}>
          {syncLine(event, zone)}
        </DetailLine>
        {event.last_sync_error && (
          <DetailLine icon="error" sub="The last sync failed">
            {event.last_sync_error}
          </DetailLine>
        )}
      </DetailSection>
      <DetailSection title="Comp badges">
        <DetailLine icon="badge">{comps.length ? `For ${comps.join(', ')}` : 'No comps'}</DetailLine>
      </DetailSection>
      <Tiers event={event} board={board} />

      <EventDialog open={dialog === 'edit'} onOpenChange={(o) => setDialog(o ? 'edit' : null)} event={event} board={board} onSaved={onChange} />
      <FormDialog
        open={dialog === 'delete'}
        onOpenChange={(o) => setDialog(o ? 'delete' : null)}
        title={`Delete ${event.name}?`}
        size="sm"
        primaryLabel="Delete"
        destructive
        busy={busy === 'delete'}
        onPrimary={() => void remove()}
      >
        <p>Its roster and every check-in on it go too. Close it instead to keep the record.</p>
        <FormError error={error} />
      </FormDialog>
    </Detail>
  )
}

function LoginPanel({ board, onChange }: { board: Board; onChange: () => void }) {
  const login = board.login
  const zone = effectiveZone(board.zone)
  const [dialog, setDialog] = useState<'token' | 'alert' | 'signout' | null>(null)
  const [token, setToken] = useState('')
  const [alertUser, setAlertUser] = useState<number | null>(login.alert_user_id ?? null)
  const [candidates, setCandidates] = useState<Schemas['PersonOption'][] | null>(null)
  const { busy, error, run, setError } = useAction()

  useEffect(() => {
    if (dialog !== 'alert') return
    setAlertUser(login.alert_user_id ?? null)
    api
      .GET('/api/admin/check-in/matcherino-login/alert-candidates')
      .then(unwrap)
      .then(setCandidates)
      .catch((e: unknown) => setError(message(e)))
  }, [dialog, login.alert_user_id, setError])

  const open = (d: typeof dialog) => {
    setError(null)
    setToken('')
    setDialog(d)
  }
  const done = () => {
    setDialog(null)
    onChange()
  }
  const menu: MenuItem[] = [
    { label: login.configured ? 'Replace the login…' : 'Add a login…', icon: 'key', onSelect: () => open('token') },
    { label: 'Who hears when it stops working…', icon: 'notifications', disabled: !login.configured, onSelect: () => open('alert') },
  ]
  if (login.configured) menu.push({ label: 'Remove the login…', icon: 'delete', destructive: true, onSelect: () => open('signout') })

  return (
    <Panel>
      <PanelRow>
        <DetailLine
          icon="key"
          sub={
            login.configured
              ? `Saved${login.updated_by ? ` by ${login.updated_by}` : ''}${login.updated_at ? ` ${formatDayTime(login.updated_at, zone)}` : ''} · alerts go to ${login.alert_user ?? 'nobody'}`
              : 'Needed to pull badge sales from Matcherino'
          }
          end={
            <>
              <Chip tone={login.configured ? 'ok' : 'pending'}>{login.configured ? 'Saved' : 'Not set'}</Chip>
              <RowMenu label="Matcherino login" items={menu} />
            </>
          }
        >
          Matcherino login
        </DetailLine>
      </PanelRow>
      <FormDialog
        open={dialog === 'token'}
        onOpenChange={(o) => setDialog(o ? 'token' : null)}
        title="Matcherino login"
        description="Sign in to matcherino.com in a private window, open DevTools → Application → Cookies → matcherino.com, copy the whole value of the credentials cookie, then close the window without signing out. Signing out, or leaving a Matcherino tab idle for six hours, throws the token away. Treat it like a password."
        primaryLabel="Save"
        busy={busy === 'token'}
        onPrimary={() =>
          void run('token', () => api.PUT('/api/admin/check-in/matcherino-login', { body: { refresh_token: token.trim() } }).then(unwrap)).then((ok) => ok && done())
        }
      >
        <label className="field">
          <span className="field__label">Refresh token</span>
          <textarea className="input admin-mono" rows={3} value={token} onChange={(e) => setToken(e.target.value)} />
        </label>
        <FormError error={error} />
      </FormDialog>
      <FormDialog
        open={dialog === 'alert'}
        onOpenChange={(o) => setDialog(o ? 'alert' : null)}
        title="Who hears when it stops working"
        size="sm"
        description="They get a Discord DM when Matcherino refuses the login."
        primaryLabel="Save"
        busy={busy === 'alert'}
        onPrimary={() =>
          void run('alert', () => api.PUT('/api/admin/check-in/matcherino-login/alert-user', { body: { user_id: alertUser } }).then(unwrap)).then((ok) => ok && done())
        }
      >
        {!candidates && !error ? (
          <Spinner label="Loading staff" />
        ) : (
          <label className="field">
            <span className="field__label">Staff member</span>
            <select className="input" value={alertUser ?? ''} onChange={(e) => setAlertUser(e.target.value ? Number(e.target.value) : null)}>
              <option value="">Nobody</option>
              {(candidates ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <FormError error={error} />
      </FormDialog>
      <FormDialog
        open={dialog === 'signout'}
        onOpenChange={(o) => setDialog(o ? 'signout' : null)}
        title="Remove the Matcherino login?"
        size="sm"
        primaryLabel="Remove"
        destructive
        busy={busy === 'signout'}
        onPrimary={() => void run('signout', () => api.DELETE('/api/admin/check-in/matcherino-login').then(unwrap)).then((ok) => ok && done())}
      >
        <p>Rosters stop syncing until someone adds a login again. Check-ins already made stay.</p>
        <FormError error={error} />
      </FormDialog>
    </Panel>
  )
}

function RosterPage({ board, reload, base }: { board: Board; reload: () => void; base: string }) {
  const { eventId } = useParams()
  const id = eventId ? Number(eventId) : null
  const navigate = useNavigate()
  const zone = effectiveZone(board.zone)
  const [creating, setCreating] = useState(false)
  const open = board.events.find((e) => e.id === id)
  return (
    <>
      <PageHeader
        title="Check-in Roster"
        lede="Each event pulls its roster from a Matcherino venue's badge sales. Open one to put it on the check-in desk."
        actions={
          <Button variant="primary" icon="add" onClick={() => setCreating(true)}>
            New event
          </Button>
        }
      />
      <SideListLayout
        open={id !== null}
        backTo={base}
        backLabel="Check-in Roster"
        list={
          <div className="admin-stack">
            <LoginPanel board={board} onChange={reload} />
            {board.events.length === 0 ? (
              <EmptyState icon="fact_check" title="No check-in events yet" />
            ) : (
              <SideListCard label="Check-in events">
                {board.events.map((e) => (
                  <SideListRow key={e.id} to={`${base}/${e.id}`} title={e.name} sub={syncLine(e, zone)} end={<StatusChip status={e.status} />} />
                ))}
              </SideListCard>
            )}
          </div>
        }
        detail={
          open ? (
            <EventDetail
              key={open.id}
              event={open}
              board={board}
              onChange={reload}
              onDeleted={() => {
                reload()
                navigate(base)
              }}
            />
          ) : id !== null ? (
            <Banner tone="warning">That check-in event isn't here any more.</Banner>
          ) : (
            <DetailEmpty>Pick an event to see its sync, comps and badge types.</DetailEmpty>
          )
        }
      />
      <EventDialog
        open={creating}
        onOpenChange={setCreating}
        event={null}
        board={board}
        onSaved={(newId) => {
          reload()
          navigate(`${base}/${newId}`)
        }}
      />
    </>
  )
}

/** Admin → Check-in Roster: check-in events beside the open one, and the Matcherino login. */
export function CheckInRosterScreen() {
  const { adminBase } = useAdmin()
  const base = `${adminBase}/check-in-roster`
  const { data, error, reload } = useAdminData('check-in', () => api.GET('/api/admin/check-in').then(unwrap))
  if (error && !data) {
    return (
      <>
        <PageHeader title="Check-in Roster" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (!data) return <Spinner label="Loading check-in" />
  return (
    <Routes>
      <Route path=":eventId" element={<RosterPage board={data} reload={reload} base={base} />} />
      <Route path="*" element={<RosterPage board={data} reload={reload} base={base} />} />
    </Routes>
  )
}

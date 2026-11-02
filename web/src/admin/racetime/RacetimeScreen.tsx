import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, EmptyState, FormDialog, PageHeader, Panel, PanelRow, Spinner } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { NumberField, SideList, useAdmin, useAdminQuery } from '../kit'

type Profile = Schemas['RaceRoomProfileResponse']

const SWITCHES = [
  ['auto_start', 'Start the race when everyone is ready'],
  ['invitational', 'Invite only'],
  ['unlisted', 'Unlisted on racetime.gg'],
  ['streaming_required', 'Entrants must stream'],
  ['allow_comments', 'Entrants can leave comments'],
  ['allow_midrace_chat', 'Chat during the race'],
  ['allow_non_entrant_chat', 'Spectators can chat'],
] as const

type Draft = Omit<Profile, 'id' | 'created_at' | 'updated_at' | 'goal'> & { goal: string }

const toDraft = (p: Profile): Draft => {
  const { id: _id, created_at: _c, updated_at: _u, ...rest } = p
  return { ...rest, goal: p.goal ?? '' }
}

const BLANK: Draft = {
  name: '',
  goal: '',
  auto_start: true,
  invitational: false,
  unlisted: false,
  streaming_required: false,
  allow_comments: true,
  allow_midrace_chat: true,
  allow_non_entrant_chat: true,
  chat_message_delay: 0,
  start_delay: 15,
  time_limit: 24,
}

/** Admin → Racetime: reusable race room profiles a tournament points at. */
export function RacetimeScreen() {
  return (
    <Routes>
      <Route index element={<ProfilesBoard />} />
      <Route path=":id" element={<ProfilesBoard />} />
    </Routes>
  )
}

function ProfilesBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const navigate = useNavigate()
  const profiles = useAdminQuery('race-room-profiles', () => api.GET('/api/race-room-profiles').then(unwrap))
  const [adding, setAdding] = useState(false)
  const base = `${adminBase}/racetime`

  if (profiles.error && !profiles.data) {
    return (
      <>
        <PageHeader title="Racetime" />
        <Banner tone="error" action={<Button variant="outline" onClick={profiles.reload}>Try again</Button>}>
          {profiles.error.message}
        </Banner>
      </>
    )
  }
  if (!profiles.data) return <Spinner label="Loading race room profiles" />
  const openId = id ? Number(id) : null
  const open = profiles.data.find((p) => p.id === openId) ?? null
  if (!phone && openId == null && profiles.data[0]) return <Navigate to={`${base}/${profiles.data[0].id}`} replace />

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="Racetime"
          lede="Race room profiles: the racetime.gg room settings a tournament uses when it opens a room."
          actions={
            <Button icon="add" onClick={() => setAdding(true)}>
              Add profile
            </Button>
          }
        />
      )}
      <SideList
        label="Race room profiles"
        items={profiles.data}
        itemKey={(p) => p.id}
        itemTo={(p) => `${base}/${p.id}`}
        title={(p) => p.name}
        sub={(p) => p.goal || 'No goal set'}
        openKey={openId}
        back={{ to: base, label: 'Racetime' }}
        detail={
          open ? (
            <ProfileDetail key={open.id} profile={open} onChange={profiles.reload} onDeleted={() => { profiles.reload(); navigate(base) }} />
          ) : (
            <EmptyState icon="info" title="That profile isn't here">
              It may have been deleted.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="flag" title="Pick a profile">
            Its room settings open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="flag" title="No race room profiles yet">
            Add one, then pick it under a tournament's racetime settings.
          </EmptyState>
        }
      />
      {adding && (
        <AddProfile
          onClose={() => setAdding(false)}
          onAdded={(p) => {
            profiles.reload()
            navigate(`${base}/${p.id}`)
          }}
        />
      )}
    </>
  )
}

function AddProfile({ onClose, onAdded }: { onClose: () => void; onAdded: (p: Profile) => void }) {
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function add() {
    setBusy(true)
    setError(null)
    try {
      onAdded(await api.POST('/api/race-room-profiles', { body: { ...BLANK, name: name.trim() } }).then(unwrap))
      onClose()
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
      title="Add race room profile"
      description="It starts with racetime.gg's usual room settings; change them after."
      primaryLabel="Add profile"
      onPrimary={() => void add()}
      busy={busy}
      busyLabel="Adding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} placeholder="e.g. League race" onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

function ProfileDetail({ profile, onChange, onDeleted }: { profile: Profile; onChange: () => void; onDeleted: () => void }) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const [draft, setDraft] = useState(toDraft(profile))
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  useEffect(() => setDraft(toDraft(profile)), [profile])
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(profile))
  const path = { params: { path: { profile_id: profile.id } } }

  async function save() {
    setBusy(true)
    setNote(null)
    try {
      await api.PATCH('/api/race-room-profiles/{profile_id}', { ...path, body: { ...draft, name: draft.name.trim() } }).then(unwrap)
      setNote({ tone: 'info', text: 'Saved.' })
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{profile.name}</Heading>
          <div className="detail-head__sub">{profile.goal || 'No goal set'}</div>
        </div>
        <ActionsMenu label="Actions" items={[{ label: 'Delete profile…', icon: 'delete', destructive: true, onSelect: () => setDeleting(true) }]} />
      </div>
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      <form
        className="t-form"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <Panel title="Room">
          <PanelRow>
            <div className="t-form__grid">
              <label className="field">
                <span className="field__label">Name</span>
                <input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
              </label>
              <label className="field">
                <span className="field__label">Goal</span>
                <input className="input" value={draft.goal} placeholder="The category's default goal" onChange={(e) => setDraft({ ...draft, goal: e.target.value })} />
              </label>
            </div>
          </PanelRow>
        </Panel>
        <Panel title="Rules">
          <PanelRow>
            <div className="t-form__grid">
              {SWITCHES.map(([key, label]) => (
                <label key={key} className="t-check">
                  <input type="checkbox" checked={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: e.target.checked })} />
                  {label}
                </label>
              ))}
            </div>
          </PanelRow>
        </Panel>
        <Panel title="Timers">
          <PanelRow>
            <div className="t-form__grid">
              <NumberField label="Countdown (seconds)" value={draft.start_delay} onChange={(n) => setDraft({ ...draft, start_delay: n ?? 0 })} />
              <NumberField label="Time limit (hours)" min={1} value={draft.time_limit} onChange={(n) => setDraft({ ...draft, time_limit: n ?? 24 })} />
              <NumberField label="Chat delay (seconds)" value={draft.chat_message_delay} onChange={(n) => setDraft({ ...draft, chat_message_delay: n ?? 0 })} />
            </div>
          </PanelRow>
        </Panel>
        <div className="t-actions">
          <Button variant="ghost" disabled={!dirty || busy} onClick={() => setDraft(toDraft(profile))}>
            Undo changes
          </Button>
          <Button type="submit" disabled={!dirty || !draft.name.trim()} busy={busy} busyLabel="Saving…">
            Save
          </Button>
        </div>
      </form>
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(false)}
          title={`Delete ${profile.name}?`}
          description="Tournaments using it go back to racetime.gg's usual room settings."
          primaryLabel="Delete profile"
          destructive
          busy={busy}
          busyLabel="Deleting…"
          onPrimary={async () => {
            setBusy(true)
            try {
              done(await api.DELETE('/api/race-room-profiles/{profile_id}', path))
              setDeleting(false)
              onDeleted()
            } catch (err) {
              setNote({ tone: 'error', text: message(err) })
              setDeleting(false)
            } finally {
              setBusy(false)
            }
          }}
        >
          <p style={{ margin: 0 }}>This can't be undone.</p>
        </FormDialog>
      )}
    </>
  )
}

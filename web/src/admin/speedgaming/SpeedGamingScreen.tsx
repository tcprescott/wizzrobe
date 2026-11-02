import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, FormDialog, PageHeader, Panel, PanelRow, Spinner, type Tone } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { NumberField, SideList, useAdmin, useAdminQuery } from '../kit'

type Link = Schemas['SpeedGamingLinkResponse']
type Tournament = Schemas['TournamentResponse']

const STATUS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: 'Synced', tone: 'ok' },
  partial: { label: 'Some failed', tone: 'pending' },
  error: { label: 'Sync failed', tone: 'cancelled' },
}

/** Admin → SpeedGaming: the SpeedGaming events pulled into tournaments as matches. */
export function SpeedGamingScreen() {
  return (
    <Routes>
      <Route index element={<LinksBoard />} />
      <Route path=":id" element={<LinksBoard />} />
    </Routes>
  )
}

function LinksBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const navigate = useNavigate()
  const links = useAdminQuery('speedgaming/links', () => api.GET('/api/speedgaming/links').then(unwrap))
  const tournaments = useAdminQuery('tournaments/all', () => api.GET('/api/tournaments').then(unwrap))
  const [adding, setAdding] = useState(false)
  const base = `${adminBase}/speedgaming`

  if (links.error && !links.data) {
    return (
      <>
        <PageHeader title="SpeedGaming" />
        <Banner tone="error" action={<Button variant="outline" onClick={links.reload}>Try again</Button>}>
          {links.error.message}
        </Banner>
      </>
    )
  }
  if (!links.data) return <Spinner label="Loading SpeedGaming links" />

  const names = new Map((tournaments.data ?? []).map((t) => [t.id, t.name]))
  const nameOf = (l: Link) => names.get(l.tournament_id) ?? `Tournament ${l.tournament_id}`
  const openId = id ? Number(id) : null
  const open = links.data.find((l) => l.id === openId) ?? null
  if (!phone && openId == null && links.data[0]) return <Navigate to={`${base}/${links.data[0].id}`} replace />

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="SpeedGaming"
          lede="Episodes on a linked SpeedGaming event become matches in its tournament."
          actions={
            <Button icon="add" onClick={() => setAdding(true)}>
              Link an event
            </Button>
          }
        />
      )}
      <SideList
        label="SpeedGaming links"
        items={links.data}
        itemKey={(l) => l.id}
        itemTo={(l) => `${base}/${l.id}`}
        title={(l) => l.event_slug}
        sub={(l) => (
          <>
            <span>{nameOf(l)}</span>
            {!l.active ? <Chip>Paused</Chip> : <StatusChip status={l.last_status} />}
          </>
        )}
        openKey={openId}
        back={{ to: base, label: 'SpeedGaming' }}
        detail={
          open ? (
            <LinkDetail key={open.id} link={open} tournament={nameOf(open)} onChange={links.reload} onDeleted={() => { links.reload(); navigate(base) }} />
          ) : (
            <EmptyState icon="info" title="That link isn't here">
              It may have been deleted.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="sync_alt" title="Pick a link">
            Its sync settings and episodes open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="sync_alt" title="No SpeedGaming events linked">
            Link one to pull its schedule in as matches.
          </EmptyState>
        }
      />
      {adding && (
        <AddLink
          tournaments={tournaments.data ?? []}
          onClose={() => setAdding(false)}
          onAdded={(l) => {
            links.reload()
            navigate(`${base}/${l.id}`)
          }}
        />
      )}
    </>
  )
}

function StatusChip({ status }: { status?: string | null }) {
  const s = status ? STATUS[status] : undefined
  return s ? <Chip tone={s.tone}>{s.label}</Chip> : null
}

type Draft = { event_slug: string; content_type: string; sync_interval_minutes: number | null; lookahead_hours: number | null }
const toDraft = (l: Link): Draft => ({
  event_slug: l.event_slug,
  content_type: l.content_type ?? '',
  sync_interval_minutes: l.sync_interval_minutes,
  lookahead_hours: l.lookahead_hours,
})

function LinkDetail({ link, tournament, onChange, onDeleted }: { link: Link; tournament: string; onChange: () => void; onDeleted: () => void }) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const episodes = useAdminQuery(`speedgaming/${link.id}/episodes`, () =>
    api.GET('/api/speedgaming/links/{link_id}/episodes', { params: { path: { link_id: link.id } } }).then(unwrap),
  )
  const [draft, setDraft] = useState(toDraft(link))
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  useEffect(() => setDraft(toDraft(link)), [link])
  const zone = effectiveZone(null)
  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(link))
  const path = { params: { path: { link_id: link.id } } }

  async function run(key: string, call: () => Promise<string>) {
    setBusy(key)
    setNote(null)
    try {
      setNote({ tone: 'info', text: await call() })
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(null)
    }
  }
  const patch = (body: Schemas['SpeedGamingLinkUpdateRequest'], text: string) =>
    run('save', async () => {
      await api.PATCH('/api/speedgaming/links/{link_id}', { ...path, body }).then(unwrap)
      return text
    })
  const sync = () =>
    run('sync', async () => {
      const r = await api.POST('/api/speedgaming/links/{link_id}/sync', path).then(unwrap)
      episodes.reload()
      return `Synced: ${r.imported} imported, ${r.unchanged} unchanged, ${r.skipped} left alone, ${r.cancelled} detached${r.errors ? `, ${r.errors} failed` : ''}.`
    })

  return (
    <>
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{link.event_slug}</Heading>
          <div className="detail-head__sub">
            <span>Into {tournament}</span>
            {link.active ? <Chip tone="ok">Syncing</Chip> : <Chip>Paused</Chip>}
          </div>
        </div>
        <ActionsMenu
          label="Actions"
          items={[
            link.active
              ? { label: 'Pause sync', icon: 'close', onSelect: () => void patch({ active: false }, 'Sync paused.') }
              : { label: 'Resume sync', icon: 'sync', onSelect: () => void patch({ active: true }, 'Sync resumed.') },
            { label: 'Delete link…', icon: 'delete', destructive: true, onSelect: () => setDeleting(true) },
          ]}
        />
      </div>
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      <Panel title="Last sync">
        <PanelRow>
          <dl className="t-kv">
            <dt>When</dt>
            <dd>{link.last_synced_at ? formatDayTime(link.last_synced_at, zone) : 'Never'}</dd>
            <dt>Result</dt>
            <dd>{link.last_status ? (STATUS[link.last_status]?.label ?? link.last_status) : '—'}</dd>
            {link.last_error && (
              <>
                <dt>Error</dt>
                <dd>{link.last_error}</dd>
              </>
            )}
          </dl>
        </PanelRow>
        <PanelRow>
          <Button icon="sync" onClick={() => void sync()} busy={busy === 'sync'} busyLabel="Syncing…">
            Sync now
          </Button>
        </PanelRow>
      </Panel>
      <form
        className="t-form"
        onSubmit={(e) => {
          e.preventDefault()
          void patch(
            {
              event_slug: draft.event_slug.trim(),
              content_type: draft.content_type.trim(),
              sync_interval_minutes: draft.sync_interval_minutes ?? undefined,
              lookahead_hours: draft.lookahead_hours ?? undefined,
            },
            'Saved.',
          )
        }}
      >
        <Panel title="Settings">
          <PanelRow>
            <div className="t-form__grid">
              <label className="field">
                <span className="field__label">SpeedGaming event</span>
                <input className="input mono" value={draft.event_slug} onChange={(e) => setDraft({ ...draft, event_slug: e.target.value })} />
              </label>
              <label className="field">
                <span className="field__label">Content type</span>
                <input className="input" value={draft.content_type} placeholder="Every bracket" onChange={(e) => setDraft({ ...draft, content_type: e.target.value })} />
              </label>
              <NumberField label="Sync every (minutes)" min={1} value={draft.sync_interval_minutes} onChange={(n) => setDraft({ ...draft, sync_interval_minutes: n })} />
              <NumberField label="Look ahead (hours)" min={1} value={draft.lookahead_hours} onChange={(n) => setDraft({ ...draft, lookahead_hours: n })} />
            </div>
          </PanelRow>
        </Panel>
        <div className="t-actions">
          <Button variant="ghost" disabled={!dirty || !!busy} onClick={() => setDraft(toDraft(link))}>
            Undo changes
          </Button>
          <Button type="submit" disabled={!dirty || !draft.event_slug.trim()} busy={busy === 'save'} busyLabel="Saving…">
            Save
          </Button>
        </div>
      </form>
      <Panel title={`Episodes${episodes.data ? ` · ${episodes.data.length}` : ''}`}>
        {!episodes.data ? (
          <PanelRow>
            <Spinner label="Loading episodes" />
          </PanelRow>
        ) : episodes.data.length === 0 ? (
          <PanelRow>
            <EmptyState icon="event" title="No episodes pulled yet">
              Sync now to fetch them.
            </EmptyState>
          </PanelRow>
        ) : (
          episodes.data.map((ep) => (
            <PanelRow key={ep.id}>
              <div className="t-row">
                <span className="t-row__text">
                  <span className="t-row__title">{ep.title || `Episode ${ep.sg_episode_id}`}</span>
                  <span className="t-row__sub">
                    {ep.scheduled_at ? formatDayTime(ep.scheduled_at, zone) : 'No time yet'}
                    {ep.sync_error && ` · ${ep.sync_error}`}
                  </span>
                </span>
                <Chip tone={ep.sync_status === 'synced' ? 'ok' : ep.sync_status === 'error' ? 'cancelled' : 'neutral'}>{ep.sync_status}</Chip>
              </div>
            </PanelRow>
          ))
        )}
      </Panel>
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(false)}
          title={`Delete the ${link.event_slug} link?`}
          description="Matches it already imported stay on the schedule."
          primaryLabel="Delete link"
          destructive
          busy={busy === 'delete'}
          busyLabel="Deleting…"
          onPrimary={() =>
            void run('delete', async () => {
              done(await api.DELETE('/api/speedgaming/links/{link_id}', path))
              setDeleting(false)
              onDeleted()
              return 'Link deleted.'
            })
          }
        >
          <p style={{ margin: 0 }}>Pause it instead to stop syncing and keep the link.</p>
        </FormDialog>
      )}
    </>
  )
}

function AddLink({ tournaments, onClose, onAdded }: { tournaments: Tournament[]; onClose: () => void; onAdded: (l: Link) => void }) {
  const [tournamentId, setTournamentId] = useState<number | ''>(tournaments.find((t) => t.is_active)?.id ?? '')
  const [slug, setSlug] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function add() {
    if (tournamentId === '') {
      setError('Pick a tournament.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const created = await api
        .POST('/api/speedgaming/links', {
          body: { tournament_id: tournamentId, event_slug: slug.trim(), active: true, sync_interval_minutes: 15, lookahead_hours: 72 },
        })
        .then(unwrap)
      onAdded(created)
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
      title="Link a SpeedGaming event"
      description="It syncs every 15 minutes, looking 72 hours ahead. You can change both after."
      primaryLabel="Link event"
      onPrimary={() => void add()}
      busy={busy}
      busyLabel="Linking…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Tournament</span>
          <select className="input" value={tournamentId} onChange={(e) => setTournamentId(e.target.value ? Number(e.target.value) : '')}>
            <option value="">Pick one</option>
            {tournaments.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="field__label">SpeedGaming event slug</span>
          <input className="input mono" value={slug} placeholder="e.g. alttprleague" onChange={(e) => setSlug(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, FormDialog, PageHeader, Panel, PanelRow, Spinner } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { SideList, useAdmin, useAdminQuery } from '../kit'

type Stage = Schemas['StageResponse']

/** Admin → Stages: the event's stream stages as a side list, the open one editable beside it. */
export function StagesScreen() {
  return (
    <Routes>
      <Route index element={<StagesBoard />} />
      <Route path=":id" element={<StagesBoard />} />
    </Routes>
  )
}

function StagesBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const navigate = useNavigate()
  const stages = useAdminQuery('stages', () => api.GET('/api/stages').then(unwrap))
  const [retired, setRetired] = useState(false)
  const [adding, setAdding] = useState(false)
  const base = `${adminBase}/stages`

  if (stages.error && !stages.data) {
    return (
      <>
        <PageHeader title="Stages" />
        <Banner tone="error" action={<Button variant="outline" onClick={stages.reload}>Try again</Button>}>
          {stages.error.message}
        </Banner>
      </>
    )
  }
  if (!stages.data) return <Spinner label="Loading stages" />

  const all = stages.data
  const openId = id ? Number(id) : null
  const open = all.find((s) => s.id === openId) ?? null
  const shown = all.filter((s) => s.is_active !== retired)
  if (!phone && openId == null && shown[0]) return <Navigate to={`${base}/${shown[0].id}`} replace />

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="Stages"
          lede="Where streamed matches are played. Matches on a stage show on the watch page."
          actions={
            <Button icon="add" onClick={() => setAdding(true)}>
              Add stage
            </Button>
          }
        />
      )}
      <SideList
        label="Stages"
        items={shown}
        itemKey={(s) => s.id}
        itemTo={(s) => `${base}/${s.id}`}
        title={(s) => s.name}
        sub={(s) => (s.is_active ? (s.stream_url ? hostOf(s.stream_url) : 'No stream link') : <Chip>Retired</Chip>)}
        openKey={openId}
        back={{ to: base, label: 'Stages' }}
        toolbar={
          <div className="filter-chips" role="group" aria-label="Show">
            <button type="button" className="filter-chip" aria-pressed={!retired} onClick={() => setRetired(false)}>
              In use · {all.filter((s) => s.is_active).length}
            </button>
            <button type="button" className="filter-chip" aria-pressed={retired} onClick={() => setRetired(true)}>
              Retired · {all.filter((s) => !s.is_active).length}
            </button>
          </div>
        }
        detail={
          open ? (
            <StageDetail key={open.id} stage={open} onChange={stages.reload} onDeleted={() => { stages.reload(); navigate(base) }} />
          ) : (
            <EmptyState icon="info" title="That stage isn't here">
              It may have been deleted.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="stadium" title="Pick a stage">
            Its name, stream link and status open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="stadium" title={retired ? 'No retired stages' : 'No stages yet'}>
            {retired ? '' : 'Add one for each stream you run.'}
          </EmptyState>
        }
      />
      {adding && (
        <AddStage
          onClose={() => setAdding(false)}
          onAdded={(s) => {
            stages.reload()
            navigate(`${base}/${s.id}`)
          }}
        />
      )}
    </>
  )
}

function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

function StageDetail({ stage, onChange, onDeleted }: { stage: Stage; onChange: () => void; onDeleted: () => void }) {
  const phone = usePhone()
  const Heading = phone ? 'h1' : 'h2'
  const [name, setName] = useState(stage.name)
  const [url, setUrl] = useState(stage.stream_url ?? '')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [deleting, setDeleting] = useState(false)
  useEffect(() => {
    setName(stage.name)
    setUrl(stage.stream_url ?? '')
  }, [stage])
  const dirty = name !== stage.name || url !== (stage.stream_url ?? '')

  async function patch(body: Schemas['StageUpdateRequest'], saved: string) {
    setBusy(true)
    setNote(null)
    try {
      await api.PATCH('/api/stages/{stage_id}', { params: { path: { stage_id: stage.id } }, body }).then(unwrap)
      setNote({ tone: 'info', text: saved })
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
          <Heading>{stage.name}</Heading>
          <div className="detail-head__sub">{stage.is_active ? <Chip tone="ok">In use</Chip> : <Chip>Retired</Chip>}</div>
        </div>
        <ActionsMenu
          label="Actions"
          items={[
            ...(stage.stream_url
              ? [{ label: 'Open stream', icon: 'open_in_new' as const, onSelect: () => window.open(stage.stream_url!, '_blank', 'noopener') }]
              : []),
            stage.is_active
              ? { label: 'Retire stage', icon: 'link_off' as const, onSelect: () => void patch({ is_active: false }, `${stage.name} is retired.`) }
              : { label: 'Put back in use', icon: 'check_circle' as const, onSelect: () => void patch({ is_active: true }, `${stage.name} is back in use.`) },
            { label: 'Delete stage…', icon: 'delete', destructive: true, onSelect: () => setDeleting(true) },
          ]}
        />
      </div>
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      <form
        className="t-form"
        onSubmit={(e) => {
          e.preventDefault()
          void patch({ name: name.trim(), stream_url: url.trim() }, 'Saved.')
        }}
      >
        <Panel title="Stage">
          <PanelRow>
            <div className="t-form__grid">
              <label className="field">
                <span className="field__label">Name</span>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="field">
                <span className="field__label">Stream link</span>
                <input className="input" type="url" placeholder="https://twitch.tv/…" value={url} onChange={(e) => setUrl(e.target.value)} />
              </label>
            </div>
          </PanelRow>
        </Panel>
        <div className="t-actions">
          <Button variant="ghost" disabled={!dirty || busy} onClick={() => { setName(stage.name); setUrl(stage.stream_url ?? '') }}>
            Undo changes
          </Button>
          <Button type="submit" disabled={!dirty || !name.trim()} busy={busy} busyLabel="Saving…">
            Save
          </Button>
        </div>
      </form>
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(false)}
          title={`Delete ${stage.name}?`}
          description="Matches assigned to it lose their stage. Retire it instead to keep its history."
          primaryLabel="Delete stage"
          destructive
          busy={busy}
          busyLabel="Deleting…"
          onPrimary={async () => {
            setBusy(true)
            try {
              done(await api.DELETE('/api/stages/{stage_id}', { params: { path: { stage_id: stage.id } } }))
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

function AddStage({ onClose, onAdded }: { onClose: () => void; onAdded: (s: Stage) => void }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function add() {
    setBusy(true)
    setError(null)
    try {
      const created = await api
        .POST('/api/stages', { body: { name: name.trim(), stream_url: url.trim() || null, is_active: true } })
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
    <FormDialog open onOpenChange={(o) => !o && onClose()} title="Add stage" primaryLabel="Add stage" onPrimary={() => void add()} busy={busy} busyLabel="Adding…">
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} placeholder="e.g. Main stage" onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Stream link</span>
          <input className="input" type="url" placeholder="https://twitch.tv/…" value={url} onChange={(e) => setUrl(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

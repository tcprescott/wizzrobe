import { useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, FormDialog, PageHeader, Spinner, type MenuItem } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { useCommunity } from '../../community/CommunityProvider'
import { SideList, useAdmin, useAdminQuery } from '../kit'
import { PresetDetail } from './PresetDetail'

export type Preset = Schemas['PresetResponse']
export type Editor = Schemas['PresetEditorResponse']
export type RandomizerInfo = Schemas['RandomizerEditorResponse']

/** Admin → Seed Presets: every preset as a side list, the open one editable and test-rollable. */
export function SeedPresetsScreen() {
  return (
    <Routes>
      <Route index element={<PresetsBoard />} />
      <Route path=":id" element={<PresetsBoard />} />
    </Routes>
  )
}

function PresetsBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const { base: communityBase } = useCommunity()
  const phone = usePhone()
  const navigate = useNavigate()
  const presets = useAdminQuery('presets', () => api.GET('/api/presets').then(unwrap))
  const editor = useAdminQuery('presets/editor', () => api.GET('/api/admin/presets/editor').then(unwrap))
  const [randomizer, setRandomizer] = useState('')
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [importing, setImporting] = useState<RandomizerInfo | null>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const base = `${adminBase}/seed-presets`

  const error = presets.error ?? editor.error
  if (error && !(presets.data && editor.data)) {
    return (
      <>
        <PageHeader title="Seed Presets" />
        <Banner tone="error" action={<Button variant="outline" onClick={() => { presets.reload(); editor.reload() }}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (!presets.data || !editor.data) return <Spinner label="Loading presets" />

  const info = new Map(editor.data.randomizers.map((r) => [r.slug, r]))
  const usage = new Map(editor.data.usage.map((u) => [u.preset_id, u.tournaments]))
  const reload = () => {
    presets.reload()
    editor.reload()
  }
  const q = query.trim().toLowerCase()
  const shown = presets.data
    .filter((p) => !randomizer || p.randomizer === randomizer)
    .filter((p) => !q || p.name.toLowerCase().includes(q) || (p.description ?? '').toLowerCase().includes(q))
  const openId = id ? Number(id) : null
  const open = presets.data.find((p) => p.id === openId) ?? null
  if (!phone && openId == null && shown[0]) return <Navigate to={`${base}/${shown[0].id}`} replace />
  const inUse = [...new Set(presets.data.map((p) => p.randomizer))].sort()

  async function importBuiltins() {
    setNote(null)
    try {
      const added = await api.POST('/api/presets/import-builtins').then(unwrap)
      setNote({ tone: 'info', text: added.length ? `Imported ${added.length} built-in presets.` : 'You already have every built-in preset.' })
      reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    }
  }
  const actions: MenuItem[] = [
    { label: 'Browse the preset library', icon: 'public', onSelect: () => navigate(`${communityBase}/presets`) },
    { label: 'Import built-in presets', icon: 'add', onSelect: () => void importBuiltins() },
    ...editor.data.randomizers
      .filter((r) => r.offers_remote_presets && r.available)
      .map((r) => ({ label: `Import from ${r.name}…`, icon: 'add_link' as const, onSelect: () => setImporting(r) })),
  ]

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="Seed Presets"
          lede="Saved randomizer settings. A tournament rolls its seeds from the preset it points at."
          actions={
            <>
              <Button icon="add" onClick={() => setAdding(true)}>
                Add preset
              </Button>
              <ActionsMenu items={actions} />
            </>
          }
        />
      )}
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <SideList
        label="Presets"
        items={shown}
        itemKey={(p) => p.id}
        itemTo={(p) => `${base}/${p.id}`}
        title={(p) => p.name}
        sub={(p) => (
          <>
            <span>{info.get(p.randomizer)?.name ?? p.randomizer}</span>
            {(usage.get(p.id)?.length ?? 0) > 0 && <Chip tone="info">{usage.get(p.id)!.length} in use</Chip>}
          </>
        )}
        openKey={openId}
        back={{ to: base, label: 'Seed Presets' }}
        toolbar={
          <div className="t-form__grid">
            <label className="field">
              <span className="visually-hidden">Randomizer</span>
              <select className="input" value={randomizer} onChange={(e) => setRandomizer(e.target.value)}>
                <option value="">Every randomizer</option>
                {inUse.map((slug) => (
                  <option key={slug} value={slug}>
                    {info.get(slug)?.name ?? slug}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="visually-hidden">Search presets</span>
              <input className="input" type="search" placeholder="Search presets" value={query} onChange={(e) => setQuery(e.target.value)} />
            </label>
          </div>
        }
        detail={
          open ? (
            <PresetDetail
              key={open.id}
              preset={open}
              info={info.get(open.randomizer)}
              tournaments={usage.get(open.id) ?? []}
              onChange={reload}
              onDeleted={() => {
                reload()
                navigate(base)
              }}
            />
          ) : (
            <EmptyState icon="info" title="That preset isn't here">
              It may have been deleted.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="tune" title="Pick a preset">
            Its settings open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="tune" title={presets.data.length ? 'Nothing matches' : 'No presets yet'}>
            {presets.data.length ? 'Try another randomizer or search.' : 'Add one, import the built-in presets, or copy one from the preset library (all under Actions).'}
          </EmptyState>
        }
      />
      {adding && (
        <AddPreset
          editor={editor.data}
          onClose={() => setAdding(false)}
          onAdded={(p) => {
            reload()
            navigate(`${base}/${p.id}`)
          }}
        />
      )}
      {importing && (
        <ImportRemote
          randomizer={importing}
          onClose={() => setImporting(null)}
          onImported={(n) => {
            setNote({ tone: 'info', text: `Imported ${n} ${importing.name} ${n === 1 ? 'preset' : 'presets'}.` })
            reload()
          }}
        />
      )}
    </>
  )
}

function AddPreset({ editor, onClose, onAdded }: { editor: Editor; onClose: () => void; onAdded: (p: Preset) => void }) {
  const [name, setName] = useState('')
  const [slug, setSlug] = useState(editor.randomizers.find((r) => r.available)?.slug ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function add() {
    setBusy(true)
    setError(null)
    try {
      onAdded(await api.POST('/api/presets', { body: { name: name.trim(), randomizer: slug, settings: {} } }).then(unwrap))
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
      title="Add preset"
      description="Name it and pick the randomizer; its settings are set on the next screen."
      primaryLabel="Add preset"
      onPrimary={() => void add()}
      busy={busy}
      busyLabel="Adding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Randomizer</span>
          <select className="input" value={slug} onChange={(e) => setSlug(e.target.value)}>
            {editor.randomizers.map((r) => (
              <option key={r.slug} value={r.slug}>
                {r.name}
                {r.available ? '' : ' (needs an API key)'}
              </option>
            ))}
          </select>
        </label>
      </div>
    </FormDialog>
  )
}

function ImportRemote({ randomizer, onClose, onImported }: { randomizer: RandomizerInfo; onClose: () => void; onImported: (n: number) => void }) {
  const [branch, setBranch] = useState(randomizer.remote_branches[0] ?? '')
  const remote = useAdminQuery(`presets/remote/${randomizer.slug}/${branch}`, () =>
    api.GET('/api/admin/presets/remote', { params: { query: { randomizer: randomizer.slug, branch: branch || null } } }).then(unwrap),
  )
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const toggle = (name: string) => {
    const next = new Set(picked)
    if (next.has(name)) next.delete(name)
    else next.add(name)
    setPicked(next)
  }
  async function run() {
    if (picked.size === 0) {
      setError('Pick at least one preset.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const added = await api
        .POST('/api/admin/presets/import-remote', { body: { randomizer: randomizer.slug, names: [...picked], branch: branch || null } })
        .then(unwrap)
      onImported(added.length)
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
      title={`Import from ${randomizer.name}`}
      description="A preset you already have by that name is updated."
      primaryLabel={picked.size ? `Import ${picked.size}` : 'Import'}
      onPrimary={() => void run()}
      busy={busy}
      busyLabel="Importing…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {randomizer.remote_branches.length > 1 && (
          <label className="field">
            <span className="field__label">Catalogue</span>
            <select className="input" value={branch} onChange={(e) => { setBranch(e.target.value); setPicked(new Set()) }}>
              {randomizer.remote_branches.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
          </label>
        )}
        {remote.error && !remote.data ? (
          <Banner tone="error">{remote.error.message}</Banner>
        ) : !remote.data ? (
          <Spinner label={`Asking ${randomizer.name} for its presets`} />
        ) : (
          <fieldset className="field t-instant">
            <legend className="field__label">Presets</legend>
            {remote.data.map((r) => (
              <label key={r.name} className="t-check">
                <input type="checkbox" checked={picked.has(r.name)} onChange={() => toggle(r.name)} />
                <span>
                  {r.name}
                  {r.description && <span className="t-row__sub"> · {r.description}</span>}
                </span>
              </label>
            ))}
          </fieldset>
        )}
      </div>
    </FormDialog>
  )
}

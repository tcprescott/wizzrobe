import { useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../api/client'
import { useQuery } from '../api/useQuery'
import { ActionsMenu, Banner, Button, Chip, EmptyState, FormDialog, Icon, PageHeader, SearchField, Spinner, type MenuItem } from '../components'
import { Detail, DetailEmpty, DetailSection, SideListCard, SideListLayout, SideListRow } from '../admin/kit/SideList'
import { useCommunity } from '../community/CommunityProvider'
import { done, message } from '../me/data'
import { usePhone } from '../lib/usePhone'
import { CopyToMineDialog, PresetFormDialog, SahasrahBotImportDialog, SpaceDialog } from './PersonalDialogs'
import './presets.css'

type Row = Schemas['LibraryPresetResponse']
type Roll = Schemas['LibraryRollResponse']
type Space = Schemas['PresetSpaceResponse']
type Scope = 'yours' | 'community' | 'everything'

function useSpaces() {
  return useQuery('preset-library/spaces', () => api.GET('/api/preset-library/spaces').then(unwrap))
}

/**
 * Presets, layout A: every preset anyone listed, in one side list grouped by
 * your own, this community's and everyone else's, with the open preset beside
 * it. Anyone can roll one or keep presets of their own; the community's preset
 * managers copy one in before a tournament can use it.
 */
export function PresetLibraryScreen() {
  const { id } = useParams()
  const { community, base } = useCommunity()
  const navigate = useNavigate()
  const phone = usePhone()
  const presets = useQuery('preset-library', () => api.GET('/api/preset-library').then(unwrap))
  const spaces = useSpaces()
  const [scope, setScope] = useState<Scope>('everything')
  const [creating, setCreating] = useState(false)
  const [spaceOpen, setSpaceOpen] = useState<Space | null>(null)
  const [importing, setImporting] = useState(false)
  const [game, setGame] = useState('')
  const [query, setQuery] = useState('')
  const libraryBase = `${base}/presets`
  const openId = id ? Number(id) : null

  const rows = useMemo(() => presets.data ?? [], [presets.data])
  const games = useMemo(() => {
    const seen = new Map<string, string>()
    for (const r of rows) seen.set(r.randomizer, r.randomizer_name)
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]))
  }, [rows])

  if (presets.error && !presets.data) {
    return (
      <>
        <PageHeader title="Presets" />
        <Banner tone="error" action={<Button variant="outline" onClick={presets.reload}>Try again</Button>}>
          {presets.error.message}
        </Banner>
      </>
    )
  }
  if (!presets.data) return <Spinner label="Loading presets" />

  const q = query.trim().toLowerCase()
  const shown = rows
    .filter((r) => scope === 'everything' || (scope === 'yours' ? r.can_edit : r.mine))
    .filter((r) => !game || r.randomizer === game)
    .filter((r) => !q || [r.reference, r.description ?? '', r.space_name].some((t) => t.toLowerCase().includes(q)))
  const yours = shown.filter((r) => r.can_edit)
  const ours = shown.filter((r) => r.mine && !r.can_edit)
  const others = shown.filter((r) => !r.mine && !r.can_edit)
  const manages = rows.some((r) => r.can_manage)
  const mySpaces = spaces.data ?? []
  const reloadAll = () => {
    presets.reload()
    spaces.reload()
  }
  const headerActions: MenuItem[] = mySpaces.map((s) => ({
    label: s.is_owner ? 'My preset space…' : `${s.slug} (co-editor)…`,
    icon: s.is_owner ? 'manage_accounts' : 'group_add',
    onSelect: () => setSpaceOpen(s),
  }))
  headerActions.push({ label: 'Import from SahasrahBot…', icon: 'download', onSelect: () => setImporting(true) })
  if (manages) {
    headerActions.push({ label: 'Manage this community’s presets', icon: 'tune', onSelect: () => navigate(`${base}/admin/seed-presets`) })
  }

  const row = (r: Row) => (
    <SideListRow
      key={r.id}
      to={`${libraryBase}/${r.id}`}
      title={
        <>
          {r.name}
          {!r.listed && <Chip tone="neutral">Hidden</Chip>}
        </>
      }
      sub={<span className="preset-ref">{r.reference}</span>}
      end={
        <span className="preset-row-end">
          <span>{r.randomizer_name}</span>
          <span className="preset-rolls" aria-label={`Rolled ${r.roll_count} times`}>
            <Icon name="casino" size={16} />
            {r.roll_count.toLocaleString()}
          </span>
        </span>
      }
    />
  )

  const list = (
    <div className="admin-stack">
      <div className="preset-filters">
        <SearchField label="Search presets" value={query} onChange={setQuery} />
        <div className="segmented" role="radiogroup" aria-label="Whose presets">
          {(
            [
              ['yours', 'Yours'],
              ['community', community.name],
              ['everything', 'Everything'],
            ] as const
          ).map(([value, label]) => (
            <span key={value}>
              <input type="radio" id={`pl-${value}`} name="pl-scope" checked={scope === value} onChange={() => setScope(value)} />
              <label htmlFor={`pl-${value}`}>{label}</label>
            </span>
          ))}
        </div>
        <div className="admin-pills" role="group" aria-label="Game">
          <button type="button" className="admin-pill" aria-pressed={game === ''} onClick={() => setGame('')}>
            All games
          </button>
          {games.map(([slug, name]) => (
            <button key={slug} type="button" className="admin-pill" aria-pressed={game === slug} onClick={() => setGame(slug)}>
              {name}
            </button>
          ))}
        </div>
      </div>
      {shown.length === 0 ? (
        scope === 'yours' && !q && !game ? (
          <EmptyState icon="casino" title="No presets of your own yet">
            Press New preset, or open anyone's preset and copy it to your presets.
          </EmptyState>
        ) : (
          <EmptyState icon="tune" title={rows.length ? 'Nothing matches' : 'No presets yet'}>
            {rows.length ? 'Try another game or search.' : 'Presets a community lists show up here.'}
          </EmptyState>
        )
      ) : (
        <>
          {yours.length > 0 && (
            <SideListCard label="Yours" title="Yours">
              {yours.map(row)}
            </SideListCard>
          )}
          {ours.length > 0 && (
            <SideListCard label={community.name} title={community.name}>
              {ours.map(row)}
            </SideListCard>
          )}
          {others.length > 0 && (
            <SideListCard label="Everyone else" title="Everyone else">
              {others.map(row)}
            </SideListCard>
          )}
        </>
      )}
    </div>
  )

  return (
    <>
      {phone && openId !== null ? (
        <h1 className="visually-hidden">Presets</h1>
      ) : (
        <PageHeader
          title="Presets"
          lede="Roll any shared preset, or keep your own. Tournaments use presets copied into their community."
          actions={
            <>
              <Button icon="add" onClick={() => setCreating(true)}>
                New preset
              </Button>
              <ActionsMenu items={headerActions} />
            </>
          }
        />
      )}
      <SideListLayout
        open={openId !== null}
        backTo={libraryBase}
        backLabel="Presets"
        list={list}
        detail={
          openId !== null ? (
            <PresetView key={openId} id={openId} spaces={mySpaces} onChange={reloadAll} />
          ) : (
            <DetailEmpty>Pick a preset to see its settings and roll a seed.</DetailEmpty>
          )
        }
      />
      {creating && (
        <PresetFormDialog
          spaces={mySpaces}
          onClose={() => setCreating(false)}
          onSaved={(p) => {
            setCreating(false)
            reloadAll()
            navigate(`${libraryBase}/${p.id}`)
          }}
        />
      )}
      {importing && <SahasrahBotImportDialog spaces={mySpaces} onClose={() => setImporting(false)} onImported={reloadAll} />}
      {spaceOpen && <SpaceDialog space={spaceOpen} onClose={() => setSpaceOpen(null)} onChange={reloadAll} />}
    </>
  )
}

function PresetView({ id, spaces, onChange }: { id: number; spaces: Space[]; onChange: () => void }) {
  const { community, base } = useCommunity()
  const navigate = useNavigate()
  const preset = useQuery(`preset-library/${id}`, () =>
    api.GET('/api/preset-library/{preset_id}', { params: { path: { preset_id: id } } }).then(unwrap),
  )
  const [roll, setRoll] = useState<Roll | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [copying, setCopying] = useState(false)
  const [copyingToMine, setCopyingToMine] = useState(false)
  const [editing, setEditing] = useState(false)
  const [deleting, setDeleting] = useState(false)

  if (preset.error && !preset.data) {
    return (
      <EmptyState icon="info" title="That preset isn't here">
        It may have been deleted or hidden from the library.
      </EmptyState>
    )
  }
  const p = preset.data
  if (!p) return <Spinner label="Loading preset" />

  async function doRoll() {
    setBusy('roll')
    setNote(null)
    try {
      setRoll(await api.POST('/api/preset-library/{preset_id}/roll', { params: { path: { preset_id: id } } }).then(unwrap))
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(null)
    }
  }

  async function updateCopy() {
    setBusy('update')
    setNote(null)
    try {
      await api.POST('/api/preset-library/{preset_id}/update-copy', { params: { path: { preset_id: id } } }).then(unwrap)
      setNote({ tone: 'info', text: 'Updated from the original.' })
      preset.reload()
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(null)
    }
  }

  const items: MenuItem[] = []
  if (p.can_edit) {
    items.push({ label: 'Edit…', icon: 'edit', onSelect: () => setEditing(true) })
  } else {
    items.push({ label: 'Copy to my presets…', icon: 'content_copy', onSelect: () => setCopyingToMine(true) })
  }
  if (p.can_manage && !p.mine) {
    items.push({ label: `Copy to ${community.name}…`, icon: 'content_copy', onSelect: () => setCopying(true) })
  }
  if (p.can_manage && p.mine) {
    items.push({ label: 'Edit in Seed Presets', icon: 'edit', onSelect: () => navigate(`${base}/admin/seed-presets/${p.id}`) })
  }
  items.push({
    label: `Copy name ${p.reference}`,
    icon: 'link',
    onSelect: () => void navigator.clipboard?.writeText(p.reference),
  })
  if (p.can_edit) {
    items.push({ label: 'Delete preset…', icon: 'delete', destructive: true, onSelect: () => setDeleting(true) })
  }
  const canUpdateCopy = p.can_edit || (p.can_manage && p.mine)

  return (
    <Detail
      title={p.name}
      sub={<span className="preset-ref">{p.reference}</span>}
      actions={
        <>
          <Button icon="casino" onClick={() => void doRoll()} busy={busy === 'roll'} disabled={!p.rollable || busy !== null}>
            Roll a seed
          </Button>
          <ActionsMenu items={items} />
        </>
      }
    >
      {p.description && (
        <DetailSection>
          <p className="prewrap">{p.description}</p>
        </DetailSection>
      )}
      <DetailSection>
        <p className="preset-meta">
          <span>
            <Icon name="sports_esports" size={18} />
            {p.randomizer_name}
          </span>
          <span>
            <Icon name={p.space_kind === 'user' ? 'person' : 'groups'} size={18} />
            {p.space_name}
          </span>
          <span>
            <Icon name="casino" size={18} />
            Rolled {p.roll_count.toLocaleString()} {p.roll_count === 1 ? 'time' : 'times'}
          </span>
          {!p.listed && (
            <span>
              <Icon name="visibility_off" size={18} />
              Hidden from the library
            </span>
          )}
        </p>
        {!p.rollable && (
          <Banner tone="warning">
            {community.name} hasn't added the key {p.randomizer_name} needs, so you can't roll it here.
          </Banner>
        )}
        {p.copied_from && (
          <p className="preset-meta">
            <span>
              <Icon name="content_copy" size={18} />
              Copied from <span className="preset-ref">{p.copied_from}</span>
            </span>
          </p>
        )}
        {p.changed_since_copy && canUpdateCopy && (
          <Banner
            tone="info"
            action={
              <Button variant="outline" size="sm" busy={busy === 'update'} onClick={() => void updateCopy()}>
                Update copy
              </Button>
            }
          >
            {p.can_edit
              ? "The original's settings changed since you copied it. Yours stays as it is until you update it."
              : "The original's settings changed since this copy was made. Tournaments keep rolling this copy until you update it."}
          </Banner>
        )}
        {note && <Banner tone={note.tone}>{note.text}</Banner>}
        {roll && (
          <div className="preset-roll" role="status">
            <strong>
              <Icon name="check_circle" size={18} /> Seed ready
            </strong>
            <a href={roll.seed_url} target="_blank" rel="noreferrer noopener">
              {roll.seed_url}
            </a>
            {roll.code && <span className="preset-ref">Code: {roll.code.join(' · ')}</span>}
          </div>
        )}
      </DetailSection>
      <DetailSection title="Settings">
        <Settings value={p.settings} />
      </DetailSection>
      {copyingToMine && (
        <CopyToMineDialog
          preset={p}
          spaces={spaces}
          onClose={() => setCopyingToMine(false)}
          onCopied={(made) => {
            setCopyingToMine(false)
            onChange()
            navigate(`${base}/presets/${made.id}`)
          }}
        />
      )}
      {editing && (
        <PresetFormDialog
          preset={p}
          spaces={spaces}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            preset.reload()
            onChange()
          }}
        />
      )}
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(false)}
          title={`Delete ${p.reference}?`}
          size="sm"
          description="Copies other people made keep their settings. This can't be undone."
          primaryLabel="Delete preset"
          destructive
          busy={busy === 'delete'}
          busyLabel="Deleting…"
          onPrimary={async () => {
            setBusy('delete')
            try {
              done(await api.DELETE('/api/preset-library/personal/{preset_id}', { params: { path: { preset_id: p.id } } }))
              setDeleting(false)
              onChange()
              navigate(`${base}/presets`)
            } catch (err) {
              setNote({ tone: 'error', text: message(err) })
              setDeleting(false)
            } finally {
              setBusy(null)
            }
          }}
        >
          {null}
        </FormDialog>
      )}
      {copying && (
        <CopyDialog
          preset={p}
          onClose={() => setCopying(false)}
          onCopied={(newId) => {
            setCopying(false)
            onChange()
            navigate(`${base}/presets/${newId}`)
          }}
        />
      )}
    </Detail>
  )
}

function flatten(value: unknown, prefix = ''): [string, string][] {
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0) return prefix ? [[prefix, '{}']] : []
    return entries.flatMap(([k, v]) => flatten(v, prefix ? `${prefix}.${k}` : k))
  }
  return [[prefix, Array.isArray(value) ? value.map(String).join(', ') : String(value)]]
}

function Settings({ value }: { value: Record<string, unknown> }) {
  const pairs = flatten(value)
  if (pairs.length === 0) return <p className="preset-muted">No settings: the randomizer's defaults.</p>
  return (
    <dl className="preset-settings">
      {pairs.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  )
}

function CopyDialog({ preset, onClose, onCopied }: { preset: Row; onClose: () => void; onCopied: (id: number) => void }) {
  const { community } = useCommunity()
  const [name, setName] = useState(preset.name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function copy() {
    setBusy(true)
    setError(null)
    try {
      const made = await api
        .POST('/api/preset-library/{preset_id}/copy', { params: { path: { preset_id: preset.id } }, body: { name: name.trim() } })
        .then(unwrap)
      onCopied(made.id)
    } catch (err) {
      setError(message(err))
      setBusy(false)
    }
  }
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Copy to ${community.name}`}
      size="sm"
      primaryLabel="Copy preset"
      busy={busy}
      onPrimary={() => void copy()}
      description={
        <>
          The copy lands in Seed Presets, hidden from the library, ready to attach to a tournament. If{' '}
          <span className="preset-ref">{preset.reference}</span> changes later, you'll be offered an update.
        </>
      }
    >
      <label className="field">
        <span className="field__label">Name in {community.name}</span>
        <input className="input" value={name} maxLength={255} onChange={(e) => setName(e.target.value)} />
      </label>
      {error && <Banner tone="error">{error}</Banner>}
    </FormDialog>
  )
}

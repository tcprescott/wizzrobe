import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Chip, ConfirmDialog, FormDialog, PageHeader, Panel, PanelRow, Spinner } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { LoadError } from './LoadError'
import { DialogError, done, message, useNotice, usePlatformQuery } from './parts'

type Group = Schemas['FeatureGroupResponse']
type Catalog = Schemas['FeatureCatalogResponse'][]

/** Tiers of features: which ones a community on each group may use. */
export function FeatureGroupsSection() {
  const groups = usePlatformQuery('feature-groups', () => api.GET('/api/platform/feature-groups').then(unwrap))
  const catalog = usePlatformQuery('features', () => api.GET('/api/platform/features').then(unwrap))
  const [editing, setEditing] = useState<Group | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Group | null>(null)
  const { banner, run, setNotice } = useNotice()
  const label = (flag: string) => catalog.data?.find((f) => f.flag === flag)?.label ?? flag

  const columns: Column<Group>[] = [
    {
      key: 'name',
      header: 'Group',
      primary: true,
      cell: (g) => (
        <span className="pf-name">
          {g.name}
          {g.is_default && <Chip tone="info">Default</Chip>}
        </span>
      ),
    },
    {
      key: 'features',
      header: 'Features',
      cell: (g) => (g.flags.length ? g.flags.map(label).join(', ') : <span className="pf-muted">None</span>),
    },
    { key: 'communities', header: 'Communities', align: 'end', cell: (g) => g.community_count },
  ]

  const error = groups.error ?? catalog.error
  return (
    <>
      <PageHeader
        title="Feature groups"
        lede="A community gets the features of its group. One group is the default for communities with none."
        actions={
          <Button icon="add" onClick={() => setEditing('new')}>
            New group
          </Button>
        }
      />
      {banner}
      {error && !groups.data ? (
        <LoadError error={error} reload={groups.reload} />
      ) : !groups.data || !catalog.data ? (
        <Spinner label="Loading feature groups" />
      ) : (
        <DataTable
          label="Feature groups"
          columns={columns}
          rows={groups.data}
          rowKey={(g) => g.id}
          onOpen={(g) => setEditing(g)}
          openLabel={(g) => `Edit ${g.name}`}
          rowMenuLabel={(g) => `More for ${g.name}`}
          rowMenu={(g) => [
            { label: 'Edit group', icon: 'edit', onSelect: () => setEditing(g) },
            { label: 'Delete group…', icon: 'delete', destructive: true, onSelect: () => setDeleting(g) },
          ]}
          empty={{ icon: 'toggle_on', title: 'No feature groups yet' }}
        />
      )}
      {editing && catalog.data && (
        <GroupDialog
          group={editing === 'new' ? null : editing}
          catalog={catalog.data}
          onClose={() => setEditing(null)}
          onSaved={(text) => {
            setEditing(null)
            setNotice({ tone: 'info', text })
            groups.reload()
          }}
        />
      )}
      {deleting && (
        <ConfirmDialog
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title={`Delete ${deleting.name}?`}
          action="Delete group"
          onConfirm={async () => {
            const ok = await run(
              () => api.DELETE('/api/platform/feature-groups/{group_id}', { params: { path: { group_id: deleting.id } } }).then(done),
              `${deleting.name} deleted.`,
            )
            if (ok) groups.reload()
            return ok
          }}
        >
          {deleting.community_count
            ? `Its ${deleting.community_count} ${deleting.community_count === 1 ? 'community falls' : 'communities fall'} back to the default group straight away, which can switch features off for them.`
            : 'No community is on it.'}
        </ConfirmDialog>
      )}
    </>
  )
}

function GroupDialog({
  group,
  catalog,
  onClose,
  onSaved,
}: {
  group: Group | null
  catalog: Catalog
  onClose: () => void
  onSaved: (text: string) => void
}) {
  const [name, setName] = useState(group?.name ?? '')
  const [description, setDescription] = useState(group?.description ?? '')
  const [flags, setFlags] = useState<Set<string>>(new Set(group?.flags ?? []))
  const [isDefault, setIsDefault] = useState(group?.is_default ?? false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const categories = [...new Set(catalog.map((f) => f.category))]

  function toggle(flag: string, on: boolean) {
    const next = new Set(flags)
    if (on) next.add(flag)
    else next.delete(flag)
    setFlags(next)
  }

  async function save() {
    setBusy(true)
    setError(null)
    const body = { name, description, flags: [...flags], is_default: isDefault }
    try {
      if (group) {
        await api.PATCH('/api/platform/feature-groups/{group_id}', { params: { path: { group_id: group.id } }, body }).then(unwrap)
        onSaved(`${name} saved.`)
      } else {
        await api.POST('/api/platform/feature-groups', { body }).then(unwrap)
        onSaved(`${name} created.`)
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
      title={group ? `Edit ${group.name}` : 'New feature group'}
      size="lg"
      primaryLabel={group ? 'Save' : 'Create group'}
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      <div className="pf-form">
        <DialogError error={error} />
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} maxLength={64} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Note</span>
          <input className="input" value={description} maxLength={255} onChange={(e) => setDescription(e.target.value)} />
        </label>
        {categories.map((cat) => (
          <fieldset key={cat} className="field">
            <legend className="field__label">{cat}</legend>
            <div className="pf-checks">
              {catalog
                .filter((f) => f.category === cat)
                .map((f) => (
                  <label key={f.flag} className="pf-check" title={f.description}>
                    <input type="checkbox" checked={flags.has(f.flag)} onChange={(e) => toggle(f.flag, e.target.checked)} />
                    <span>{f.label}</span>
                  </label>
                ))}
            </div>
          </fieldset>
        ))}
        <Panel>
          <PanelRow>
            <label className="pf-switch-row">
              <span className="pf-switch-row__text">
                <span>Default group</span>
                <span className="pf-muted">Communities with no group get these features. The old default stops being one.</span>
              </span>
              <input type="checkbox" role="switch" className="switch" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} />
            </label>
          </PanelRow>
        </Panel>
      </div>
    </FormDialog>
  )
}

import { useMemo, useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Chip, FormDialog, PageHeader, Panel, PanelRow, SearchField, Spinner } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { CommunityFeaturesDialog } from './CommunityFeaturesDialog'
import { CommunityStaffDialog } from './CommunityStaffDialog'
import { DialogError, Toolbar, message, useNotice, usePlatformQuery } from './parts'
import { LoadError } from './LoadError'

type Community = Schemas['CommunityResponse']

const COLUMNS: Column<Community>[] = [
  {
    key: 'name',
    header: 'Community',
    primary: true,
    cell: (c) => (
      <span className="pf-name">
        {c.name}
        {!c.is_active && <Chip>Inactive</Chip>}
      </span>
    ),
  },
  {
    key: 'address',
    header: 'Address',
    cell: (c) => (
      <span className="pf-stack">
        <span className="pf-mono">/t/{c.slug}</span>
        {c.domain && <span className="pf-muted">{c.domain}</span>}
      </span>
    ),
  },
  { key: 'group', header: 'Feature group', cell: (c) => c.feature_group ?? 'Default' },
  {
    key: 'setup',
    header: 'Setup',
    cell: (c) =>
      c.setup_ready ? (
        <Chip tone="ok">Ready</Chip>
      ) : (
        <span title={c.setup_missing.join(', ')}>
          <Chip tone="pending">
            {c.setup_missing.length} {c.setup_missing.length === 1 ? 'step' : 'steps'} left
          </Chip>
        </span>
      ),
  },
]

/** Every community, inactive ones too: create, edit, switch off, first staff, features. */
export function CommunitiesSection() {
  const { data, error, loading, reload } = usePlatformQuery('communities', () =>
    api.GET('/api/platform/communities').then(unwrap),
  )
  const { banner, setNotice } = useNotice()
  const [q, setQ] = useState('')
  const [editing, setEditing] = useState<Community | 'new' | null>(null)
  const [staffFor, setStaffFor] = useState<Community | null>(null)
  const [featuresFor, setFeaturesFor] = useState<Community | null>(null)

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return (data ?? []).filter(
      (c) => !needle || [c.name, c.slug, c.domain ?? ''].some((v) => v.toLowerCase().includes(needle)),
    )
  }, [data, q])

  return (
    <>
      <PageHeader
        title="Communities"
        lede="Name, address, Discord server and who runs each one."
        actions={
          <Button icon="add" onClick={() => setEditing('new')}>
            New community
          </Button>
        }
      />
      {banner}
      {error && !data ? (
        <LoadError error={error} reload={reload} />
      ) : loading && !data ? (
        <Spinner label="Loading communities" />
      ) : (
        <>
          <Toolbar>
            <SearchField value={q} onChange={setQ} label="Search communities" />
          </Toolbar>
          <DataTable
            label="Communities"
            columns={COLUMNS}
            rows={rows}
            rowKey={(c) => c.id}
            onOpen={(c) => setEditing(c)}
            openLabel={(c) => `Edit ${c.name}`}
            rowAction={(c) => (
              <Button variant="outline" size="sm" onClick={() => setFeaturesFor(c)}>
                Features
              </Button>
            )}
            rowMenuLabel={(c) => `More for ${c.name}`}
            rowMenu={(c) => [
              { label: 'Edit community', icon: 'edit', onSelect: () => setEditing(c) },
              { label: 'Staff', icon: 'admin_panel_settings', onSelect: () => setStaffFor(c) },
            ]}
            empty={{ icon: 'groups', title: q ? 'No community matches that' : 'No communities yet' }}
          />
        </>
      )}
      {editing && (
        <CommunityDialog
          community={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(text) => {
            setEditing(null)
            setNotice({ tone: 'info', text })
            reload()
          }}
        />
      )}
      {staffFor && <CommunityStaffDialog community={staffFor} onClose={() => setStaffFor(null)} />}
      {featuresFor && (
        <CommunityFeaturesDialog
          community={featuresFor}
          onClose={() => {
            setFeaturesFor(null)
            reload()
          }}
        />
      )}
    </>
  )
}

// A Discord id is larger than a JS number holds exactly, so it travels as a digit string the server coerces.
function guildValue(raw: string): number | null {
  const trimmed = raw.trim()
  return (trimmed || null) as unknown as number | null
}

function CommunityDialog({
  community,
  onClose,
  onSaved,
}: {
  community: Community | null
  onClose: () => void
  onSaved: (text: string) => void
}) {
  const [name, setName] = useState(community?.name ?? '')
  const [slug, setSlug] = useState(community?.slug ?? '')
  const [domain, setDomain] = useState(community?.domain ?? '')
  const [guild, setGuild] = useState(community?.discord_guild_id_str ?? '')
  const [active, setActive] = useState(community?.is_active ?? true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    if (guild.trim() && !/^\d+$/.test(guild.trim())) {
      setError('The Discord server id is the long number from Discord (Server Settings → Widget, or Copy Server ID).')
      return
    }
    setBusy(true)
    setError(null)
    try {
      const body = { name, slug, domain: domain.trim() || null, discord_guild_id: guildValue(guild) }
      if (community) {
        await api
          .PATCH('/api/platform/communities/{tenant_id}', {
            params: { path: { tenant_id: community.id } },
            body: { ...body, is_active: active },
          })
          .then(unwrap)
        onSaved(`${name} saved.`)
      } else {
        await api.POST('/api/platform/communities', { body }).then(unwrap)
        onSaved(`${name} created. Give it its first staff member from its ⋮ menu.`)
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
      title={community ? `Edit ${community.name}` : 'New community'}
      primaryLabel={community ? 'Save' : 'Create community'}
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      <form
        className="pf-form"
        onSubmit={(e) => {
          e.preventDefault()
          void save()
        }}
      >
        <DialogError error={error} />
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Address</span>
          <input className="input" value={slug} maxLength={64} onChange={(e) => setSlug(e.target.value.toLowerCase())} />
          <span className="field__hint">Lowercase letters, digits and hyphens: /t/{slug || 'slug'}</span>
        </label>
        <label className="field">
          <span className="field__label">Custom domain</span>
          <input className="input" value={domain} placeholder="e.g. example.com" onChange={(e) => setDomain(e.target.value)} />
          <span className="field__hint">Optional. No https:// or path.</span>
        </label>
        <label className="field">
          <span className="field__label">Discord server id</span>
          <input className="input" inputMode="numeric" value={guild} onChange={(e) => setGuild(e.target.value)} />
          <span className="field__hint">Optional. One server can back several communities.</span>
        </label>
        {community && (
          <Panel>
            <PanelRow>
              <label className="pf-switch-row">
                <span className="pf-switch-row__text">
                  <span>Active</span>
                  <span className="pf-muted">Switched off, it's hidden everywhere and its tokens stop working.</span>
                </span>
                <input type="checkbox" role="switch" className="switch" checked={active} onChange={(e) => setActive(e.target.checked)} />
              </label>
            </PanelRow>
          </Panel>
        )}
        <button type="submit" hidden />
      </form>
    </FormDialog>
  )
}

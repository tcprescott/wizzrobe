import { useState } from 'react'
import { api, unwrap, type Schemas } from '../api/client'
import { Button, Chip, ConfirmDialog, Dialog, FormDialog, PageHeader, Panel, PanelRow, Spinner, type Tone } from '../components'
import { DataTable, type Column } from '../admin/kit'
import { LoadError } from './LoadError'
import { DialogError, done, message, useNotice, usePlatformQuery } from './parts'

type Bot = Schemas['RacetimeBotResponse']

const STATUS: Record<Bot['status'], { tone: Tone; label: string }> = {
  connected: { tone: 'ok', label: 'Connected' },
  disconnected: { tone: 'pending', label: 'Disconnected' },
  error: { tone: 'cancelled', label: 'Error' },
  unknown: { tone: 'neutral', label: 'Unknown' },
}

const COLUMNS: Column<Bot>[] = [
  {
    key: 'name',
    header: 'Bot',
    primary: true,
    cell: (b) => (
      <span className="pf-name">
        {b.name}
        {!b.is_active && <Chip>Off</Chip>}
      </span>
    ),
  },
  { key: 'category', header: 'Category', cell: (b) => <span className="pf-mono">{b.category}</span> },
  {
    key: 'status',
    header: 'Status',
    cell: (b) => (
      <span className="pf-stack">
        <Chip tone={STATUS[b.status].tone}>{STATUS[b.status].label}</Chip>
        {b.status_message && <span className="pf-muted">{b.status_message}</span>}
      </span>
    ),
  },
]

/** racetime.gg bot accounts, one per category, shared by the communities granted each. */
export function BotsSection() {
  const { data, error, loading, reload } = usePlatformQuery('bots', () => api.GET('/api/racetime-bots').then(unwrap))
  const [editing, setEditing] = useState<Bot | 'new' | null>(null)
  const [grantsFor, setGrantsFor] = useState<Bot | null>(null)
  const [deleting, setDeleting] = useState<Bot | null>(null)
  const { banner, run, setNotice } = useNotice()

  async function restart(b: Bot) {
    if (await run(() => api.POST('/api/racetime-bots/{bot_id}/restart', { params: { path: { bot_id: b.id } } }).then(unwrap), `${b.name} is reconnecting.`)) reload()
  }

  return (
    <>
      <PageHeader
        title="Racetime bots"
        lede="One bot per racetime.gg category opens and runs race rooms. A community can use a bot once you grant it."
        actions={
          <Button icon="add" onClick={() => setEditing('new')}>
            New bot
          </Button>
        }
      />
      {banner}
      {error && !data ? (
        <LoadError error={error} reload={reload} />
      ) : loading && !data ? (
        <Spinner label="Loading bots" />
      ) : (
        <DataTable
          label="Racetime bots"
          columns={COLUMNS}
          rows={data ?? []}
          rowKey={(b) => b.id}
          onOpen={(b) => setEditing(b)}
          openLabel={(b) => `Edit ${b.name}`}
          rowAction={(b) => (
            <Button variant="outline" size="sm" aria-label={`Communities for ${b.name}`} onClick={() => setGrantsFor(b)}>
              Communities
            </Button>
          )}
          rowMenuLabel={(b) => `More for ${b.name}`}
          rowMenu={(b) => [
            { label: 'Edit bot', icon: 'edit', onSelect: () => setEditing(b) },
            { label: 'Restart', icon: 'restart_alt', onSelect: () => void restart(b) },
            { label: 'Delete bot…', icon: 'delete', destructive: true, onSelect: () => setDeleting(b) },
          ]}
          empty={{ icon: 'smart_toy', title: 'No bots yet' }}
        />
      )}
      {editing && (
        <BotDialog
          bot={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(text) => {
            setEditing(null)
            setNotice({ tone: 'info', text })
            reload()
          }}
        />
      )}
      {grantsFor && <GrantsDialog bot={grantsFor} onClose={() => setGrantsFor(null)} />}
      {deleting && (
        <ConfirmDialog
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title={`Delete ${deleting.name}?`}
          action="Delete bot"
          onConfirm={async () => {
            const ok = await run(
              () => api.DELETE('/api/racetime-bots/{bot_id}', { params: { path: { bot_id: deleting.id } } }).then(done),
              `${deleting.name} deleted.`,
            )
            if (ok) reload()
            return ok
          }}
        >
          It disconnects now and every community loses it. Rooms it already opened stay on racetime.gg without a bot.
        </ConfirmDialog>
      )}
    </>
  )
}

function BotDialog({ bot, onClose, onSaved }: { bot: Bot | null; onClose: () => void; onSaved: (text: string) => void }) {
  const [name, setName] = useState(bot?.name ?? '')
  const [category, setCategory] = useState(bot?.category ?? '')
  const [clientId, setClientId] = useState(bot?.client_id ?? '')
  const [secret, setSecret] = useState('')
  const [description, setDescription] = useState(bot?.description ?? '')
  const [handler, setHandler] = useState(bot?.handler_class ?? '')
  const [active, setActive] = useState(bot?.is_active ?? true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    const common = { name, category, client_id: clientId, description, handler_class: handler || null, is_active: active }
    try {
      if (bot) {
        await api
          .PATCH('/api/racetime-bots/{bot_id}', {
            params: { path: { bot_id: bot.id } },
            body: { ...common, ...(secret ? { client_secret: secret } : {}) },
          })
          .then(unwrap)
        onSaved(`${name} saved.`)
      } else {
        await api.POST('/api/racetime-bots', { body: { ...common, client_secret: secret } }).then(unwrap)
        onSaved(`${name} created. Grant it to a community from Communities.`)
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
      title={bot ? `Edit ${bot.name}` : 'New racetime bot'}
      primaryLabel={bot ? 'Save' : 'Create bot'}
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      <div className="pf-form">
        <DialogError error={error} />
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">racetime.gg category</span>
          <input className="input" value={category} placeholder="e.g. alttpr" onChange={(e) => setCategory(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Client id</span>
          <input className="input" value={clientId} autoComplete="off" onChange={(e) => setClientId(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Client secret</span>
          <input className="input" type="password" value={secret} autoComplete="new-password" onChange={(e) => setSecret(e.target.value)} />
          <span className="field__hint">{bot ? 'Leave it empty to keep the one stored.' : "From the category's bot page on racetime.gg. It's stored and never shown again."}</span>
        </label>
        <label className="field">
          <span className="field__label">Note</span>
          <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Handler</span>
          <input className="input pf-mono" value={handler} placeholder="Default" onChange={(e) => setHandler(e.target.value)} />
          <span className="field__hint">Only for a category with its own room logic.</span>
        </label>
        <Panel>
          <PanelRow>
            <label className="pf-switch-row">
              <span className="pf-switch-row__text">
                <span>On</span>
                <span className="pf-muted">Switched off, it disconnects and opens no rooms.</span>
              </span>
              <input type="checkbox" role="switch" className="switch" checked={active} onChange={(e) => setActive(e.target.checked)} />
            </label>
          </PanelRow>
        </Panel>
      </div>
    </FormDialog>
  )
}

function GrantsDialog({ bot, onClose }: { bot: Bot; onClose: () => void }) {
  const communities = usePlatformQuery('communities', () => api.GET('/api/platform/communities').then(unwrap))
  const grants = usePlatformQuery(`bots/${bot.id}/grants`, () =>
    api.GET('/api/racetime-bots/{bot_id}/grants', { params: { path: { bot_id: bot.id } } }).then(unwrap),
  )
  const [granted, setGranted] = useState<Set<number> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const current = granted ?? new Set((grants.data ?? []).map((g) => g.tenant_id))

  async function toggle(tenantId: number, on: boolean) {
    setError(null)
    try {
      const path = { bot_id: bot.id }
      if (on) await api.POST('/api/racetime-bots/{bot_id}/grants', { params: { path }, body: { tenant_id: tenantId } }).then(unwrap)
      else await api.DELETE('/api/racetime-bots/{bot_id}/grants/{tenant_id}', { params: { path: { ...path, tenant_id: tenantId } } }).then(done)
      const next = new Set(current)
      if (on) next.add(tenantId)
      else next.delete(tenantId)
      setGranted(next)
    } catch (err) {
      setError(message(err))
    }
  }

  const loadError = communities.error ?? grants.error
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()} title={`Communities for ${bot.name}`} description="Communities switched on here can open race rooms with this bot.">
      <div className="pf-form">
        <DialogError error={error} />
        {loadError && !communities.data ? (
          <LoadError error={loadError} reload={communities.reload} />
        ) : !communities.data || !grants.data ? (
          <Spinner label="Loading communities" />
        ) : (
          <ul className="pf-list" aria-label="Communities">
            {communities.data.map((c) => (
              <li key={c.id}>
                <label className="pf-switch-row">
                  <span className="pf-switch-row__text">
                    <span>{c.name}</span>
                    {!c.is_active && <span className="pf-muted">Inactive</span>}
                  </span>
                  <input
                    type="checkbox"
                    role="switch"
                    className="switch"
                    checked={current.has(c.id)}
                    onChange={(e) => void toggle(c.id, e.target.checked)}
                  />
                </label>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Dialog>
  )
}

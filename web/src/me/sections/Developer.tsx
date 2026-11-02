import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Chip, EmptyState, FormDialog, Icon, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { done, message, useSection } from '../data'
import { useProfile } from '../MeLayout'

type App = Schemas['OAuthAppResponse']
type Hook = Schemas['PersonalWebhookResponse']

const SCOPES: { value: string; label: string }[] = [
  { value: 'profile', label: 'See your Wizzrobe profile and availability' },
  { value: 'profile:write', label: 'Update your profile, availability and notification settings' },
  { value: 'schedule:read', label: 'See tournaments, matches, brackets and the schedule' },
  { value: 'play', label: 'Act as you as a player' },
  { value: 'crew', label: 'Sign you up or withdraw you as crew' },
  { value: 'volunteer', label: 'See and manage your volunteer shifts' },
  { value: 'staff:read', label: 'See staff-only information in this community' },
  { value: 'staff:write', label: 'Make staff changes in this community' },
]

function when(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'never'
}

/** A secret shown once, with a Copy button. Closing it is the only way out, on purpose. */
function SecretDialog({ title, secret, onClose }: { title: string; secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      size="sm"
      title={title}
      description="Copy it now. You won't see it again; if you lose it, make a new one."
      primaryLabel={copied ? 'Copied' : 'Copy'}
      cancelLabel="Done"
      onPrimary={() => {
        void navigator.clipboard?.writeText(secret).then(() => setCopied(true))
      }}
    >
      <p className="me-secret">{secret}</p>
    </FormDialog>
  )
}

function useRun(after: () => void) {
  const { reload } = useProfile()
  const [error, setError] = useState<string | null>(null)
  const run = async <T,>(work: () => Promise<T>): Promise<T | undefined> => {
    setError(null)
    try {
      const out = await work()
      after()
      reload()
      return out
    } catch (err) {
      setError(message(err))
      return undefined
    }
  }
  return { run, error, setError }
}

export function Tokens() {
  const { data, reload } = useSection('tokens', () => api.GET('/api/tokens').then(unwrap))
  const { run, error } = useRun(reload)
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [readOnly, setReadOnly] = useState(true)
  const [secret, setSecret] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (!data) return <Spinner label="Loading your tokens" />
  return (
    <div className="me-stack">
      <Panel
        title="API tokens"
        actions={
          <Button variant="primary" icon="add" onClick={() => setCreating(true)}>
            New token
          </Button>
        }
      >
        <PanelRow>
          <p className="me-note">A token lets your own scripts and overlays call Wizzrobe as you, in this community.</p>
        </PanelRow>
        {data.length === 0 ? (
          <PanelRow>
            <EmptyState icon="key" title="No tokens">
              Make one when a script or overlay needs to read your schedule.
            </EmptyState>
          </PanelRow>
        ) : (
          data.map((t) => (
            <PanelRow key={t.id}>
              <div className="me-row">
                <Icon name="key" />
                <span className="me-row__text">
                  <span className="me-row__title">{t.name}</span>
                  <span className="me-row__sub">
                    {t.token_prefix}… · {t.read_only ? 'Read only' : 'Read and write'} · last used {when(t.last_used_at)}
                    {t.expires_at ? ` · expires ${when(t.expires_at)}` : ''}
                  </span>
                </span>
                <RowMenu
                  label={`More for ${t.name}`}
                  items={[
                    {
                      label: 'Revoke',
                      icon: 'delete',
                      destructive: true,
                      onSelect: () => void run(async () => done(await api.DELETE('/api/tokens/{token_id}', { params: { path: { token_id: t.id } } }))),
                    },
                  ]}
                />
              </div>
            </PanelRow>
          ))
        )}
      </Panel>
      {error && <Banner tone="error">{error}</Banner>}
      <FormDialog
        open={creating}
        onOpenChange={setCreating}
        size="sm"
        title="New API token"
        primaryLabel="Create"
        busy={busy}
        busyLabel="Creating…"
        onPrimary={() => {
          setBusy(true)
          void run(() => api.POST('/api/tokens', { body: { name, read_only: readOnly } }).then(unwrap))
            .then((t) => {
              if (t) {
                setCreating(false)
                setName('')
                setSecret(t.token)
              }
            })
            .finally(() => setBusy(false))
        }}
      >
        <div className="me-form">
          <label className="field">
            <span className="field__label">Name</span>
            <input className="input" value={name} maxLength={80} placeholder="e.g. OBS overlay" onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="me-checks">
            <span>
              <input type="checkbox" checked={readOnly} onChange={(e) => setReadOnly(e.target.checked)} /> Read only
            </span>
          </label>
        </div>
      </FormDialog>
      {secret && <SecretDialog title="Your new token" secret={secret} onClose={() => setSecret(null)} />}
    </div>
  )
}

export function Apps() {
  const { data, reload } = useSection('apps', () => api.GET('/api/me/apps').then(unwrap))
  const { run, error } = useRun(reload)
  const [editing, setEditing] = useState<App | 'new' | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<App | null>(null)

  if (!data) return <Spinner label="Loading your apps" />
  return (
    <div className="me-stack">
      <Panel
        title="Developer apps"
        actions={
          <Button variant="primary" icon="add" onClick={() => setEditing('new')}>
            Register an app
          </Button>
        }
      >
        <PanelRow>
          <p className="me-note">Register an app so other people can sign in to it with Wizzrobe and let it act for them.</p>
        </PanelRow>
        {data.length === 0 ? (
          <PanelRow>
            <EmptyState icon="apps" title="No apps">
              Apps you register show here with their client id.
            </EmptyState>
          </PanelRow>
        ) : (
          data.map((a) => (
            <PanelRow key={a.id}>
              <div className="me-row">
                <Icon name="apps" />
                <span className="me-row__text">
                  <span className="me-row__title">{a.name}</span>
                  <span className="me-row__sub" style={{ fontFamily: 'var(--font-mono)' }}>
                    {a.client_id}
                  </span>
                  <span className="me-row__sub">
                    {a.client_type === 'confidential' ? 'Keeps a secret' : 'Public (no secret)'} · {a.allowed_scopes.join(', ')}
                  </span>
                </span>
                {a.disabled_reason && <Chip tone="cancelled">Disabled</Chip>}
                <RowMenu
                  label={`More for ${a.name}`}
                  items={[
                    { label: 'Edit…', icon: 'edit', onSelect: () => setEditing(a) },
                    ...(a.client_type === 'confidential'
                      ? [
                          {
                            label: 'New client secret',
                            icon: 'key' as const,
                            onSelect: () =>
                              void run(() => api.POST('/api/me/apps/{app_id}/secret', { params: { path: { app_id: a.id } } }).then(unwrap)).then(
                                (r) => r && setSecret(r.secret),
                              ),
                          },
                        ]
                      : []),
                    { label: 'Delete…', icon: 'delete', destructive: true, onSelect: () => setDeleting(a) },
                  ]}
                />
              </div>
            </PanelRow>
          ))
        )}
      </Panel>
      {error && <Banner tone="error">{error}</Banner>}
      {editing && (
        <AppDialog
          app={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(s) => {
            setEditing(null)
            reload()
            if (s) setSecret(s)
          }}
        />
      )}
      {secret && <SecretDialog title="Client secret" secret={secret} onClose={() => setSecret(null)} />}
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(null)}
          size="sm"
          title={`Delete ${deleting.name}?`}
          description="Everyone signed in to it is signed out, and its client id stops working. This can't be undone."
          primaryLabel="Delete"
          destructive
          onPrimary={() =>
            void run(async () => done(await api.DELETE('/api/me/apps/{app_id}', { params: { path: { app_id: deleting.id } } }))).then(() =>
              setDeleting(null),
            )
          }
        >
          {null}
        </FormDialog>
      )}
    </div>
  )
}

function AppDialog({ app, onClose, onSaved }: { app: App | null; onClose: () => void; onSaved: (secret?: string | null) => void }) {
  const [name, setName] = useState(app?.name ?? '')
  const [description, setDescription] = useState(app?.description ?? '')
  const [homepage, setHomepage] = useState(app?.homepage_url ?? '')
  const [redirects, setRedirects] = useState((app?.redirect_uris ?? []).join('\n'))
  const [kind, setKind] = useState(app?.client_type ?? 'confidential')
  const [scopes, setScopes] = useState<Set<string>>(new Set(app?.allowed_scopes ?? ['profile', 'schedule:read']))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    const uris = redirects.split(/\s+/).filter(Boolean)
    try {
      if (app) {
        await api
          .PATCH('/api/me/apps/{app_id}', {
            params: { path: { app_id: app.id } },
            body: { name, description, homepage_url: homepage, redirect_uris: uris, allowed_scopes: [...scopes] },
          })
          .then(unwrap)
        onSaved()
      } else {
        const created = await api
          .POST('/api/me/apps', {
            body: { name, description, homepage_url: homepage || null, client_type: kind, redirect_uris: uris, allowed_scopes: [...scopes] },
          })
          .then(unwrap)
        onSaved(created.client_secret)
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
      onOpenChange={(o) => !o && onClose()}
      title={app ? `Edit ${app.name}` : 'Register an app'}
      primaryLabel={app ? 'Save' : 'Register'}
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      <div className="me-form">
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Description (optional)</span>
          <input className="input" value={description} maxLength={300} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Homepage (optional)</span>
          <input className="input" type="url" value={homepage} onChange={(e) => setHomepage(e.target.value)} />
        </label>
        {!app && (
          <div className="field">
            <span className="field__label" id="app-kind">
              Can the app keep a secret?
            </span>
            <div className="segmented" role="radiogroup" aria-labelledby="app-kind">
              {(
                [
                  ['confidential', 'Yes, it runs on a server'],
                  ['public', 'No, it runs on a device'],
                ] as const
              ).map(([value, label]) => (
                <span key={value}>
                  <input type="radio" id={`kind-${value}`} name="app-kind" checked={kind === value} onChange={() => setKind(value)} />
                  <label htmlFor={`kind-${value}`}>{label}</label>
                </span>
              ))}
            </div>
          </div>
        )}
        <label className="field">
          <span className="field__label">Redirect URIs</span>
          <textarea className="input" value={redirects} onChange={(e) => setRedirects(e.target.value)} />
          <span className="field__hint">One per line. Sign-in only ever sends people back to one of these.</span>
        </label>
        <fieldset className="me-checks" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend className="field__label">What it may ask for</legend>
          {SCOPES.map((s) => (
            <label key={s.value}>
              <input
                type="checkbox"
                checked={scopes.has(s.value)}
                onChange={(e) => {
                  const next = new Set(scopes)
                  if (e.target.checked) next.add(s.value)
                  else next.delete(s.value)
                  setScopes(next)
                }}
              />
              {s.label}
            </label>
          ))}
        </fieldset>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}

export function Webhooks() {
  const { data, reload } = useSection('webhooks', () => api.GET('/api/me/webhooks').then(unwrap))
  const { run, error } = useRun(reload)
  const [editing, setEditing] = useState<Hook | 'new' | null>(null)
  const [secret, setSecret] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Hook | null>(null)
  const [flash, setFlash] = useState<string | null>(null)

  if (!data) return <Spinner label="Loading your webhooks" />
  const full = data.webhooks.length >= data.max_webhooks
  return (
    <div className="me-stack">
      <Panel
        title="Personal webhooks"
        actions={
          <Button variant="primary" icon="add" disabled={full} onClick={() => setEditing('new')}>
            Add webhook
          </Button>
        }
      >
        <PanelRow>
          <p className="me-note">
            Post your own updates to a Discord channel or your server. You can have {data.max_webhooks} here.
          </p>
        </PanelRow>
        {data.webhooks.length === 0 ? (
          <PanelRow>
            <EmptyState icon="webhook" title="No webhooks">
              Add one to get your match bookings, results and shifts posted somewhere you already look.
            </EmptyState>
          </PanelRow>
        ) : (
          data.webhooks.map((w) => (
            <PanelRow key={w.id}>
              <div className="me-row">
                <Icon name="webhook" />
                <span className="me-row__text">
                  <span className="me-row__title">{w.name}</span>
                  <span className="me-row__sub" style={{ overflowWrap: 'anywhere' }}>
                    {w.url_masked}
                  </span>
                  <span className="me-row__sub">
                    {w.event_types.length} event{w.event_types.length === 1 ? '' : 's'} · {w.only_mine ? 'only yours' : 'everyone’s'} ·{' '}
                    {w.delivered_count} sent, {w.failed_count} failed
                  </span>
                  {w.paused_reason && <span className="me-row__sub">Paused: {w.paused_reason}</span>}
                </span>
                {!w.is_active && <Chip tone="pending">Paused</Chip>}
                <RowMenu
                  label={`More for ${w.name}`}
                  items={[
                    { label: 'Edit…', icon: 'edit', onSelect: () => setEditing(w) },
                    {
                      label: 'Send a test',
                      icon: 'send',
                      onSelect: () =>
                        void run(async () =>
                          done(await api.POST('/api/me/webhooks/{webhook_id}/test', { params: { path: { webhook_id: w.id } } })),
                        ).then(() => setFlash(`Test sent to ${w.name}.`)),
                    },
                    {
                      label: w.is_active ? 'Pause' : 'Resume',
                      icon: w.is_active ? 'pause' : 'play_arrow',
                      onSelect: () =>
                        void run(() =>
                          api
                            .PUT('/api/me/webhooks/{webhook_id}/active', { params: { path: { webhook_id: w.id } }, body: { enabled: !w.is_active } })
                            .then(unwrap),
                        ),
                    },
                    ...(w.discord
                      ? []
                      : [
                          {
                            label: 'New signing secret',
                            icon: 'key' as const,
                            onSelect: () =>
                              void run(() =>
                                api.POST('/api/me/webhooks/{webhook_id}/secret', { params: { path: { webhook_id: w.id } } }).then(unwrap),
                              ).then((r) => r && setSecret(r.secret)),
                          },
                        ]),
                    { label: 'Delete…', icon: 'delete', destructive: true, onSelect: () => setDeleting(w) },
                  ]}
                />
              </div>
            </PanelRow>
          ))
        )}
      </Panel>
      {flash && !error && (
        <p className="me-saved" role="status">
          {flash}
        </p>
      )}
      {error && <Banner tone="error">{error}</Banner>}
      {editing && (
        <HookDialog
          hook={editing === 'new' ? null : editing}
          data={data}
          onClose={() => setEditing(null)}
          onSaved={(s) => {
            setEditing(null)
            reload()
            if (s) setSecret(s)
          }}
        />
      )}
      {secret && <SecretDialog title="Signing secret" secret={secret} onClose={() => setSecret(null)} />}
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(null)}
          size="sm"
          title={`Delete ${deleting.name}?`}
          description="Nothing more is posted to it."
          primaryLabel="Delete"
          destructive
          onPrimary={() =>
            void run(async () =>
              done(await api.DELETE('/api/me/webhooks/{webhook_id}', { params: { path: { webhook_id: deleting.id } } })),
            ).then(() => setDeleting(null))
          }
        >
          {null}
        </FormDialog>
      )}
    </div>
  )
}

function HookDialog({
  hook,
  data,
  onClose,
  onSaved,
}: {
  hook: Hook | null
  data: Schemas['PersonalWebhooksResponse']
  onClose: () => void
  onSaved: (secret?: string | null) => void
}) {
  const [name, setName] = useState(hook?.name ?? '')
  const [url, setUrl] = useState('')
  const [events, setEvents] = useState<Set<string>>(new Set(hook?.event_types ?? []))
  const [onlyMine, setOnlyMine] = useState(hook?.only_mine ?? true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      if (hook) {
        await api
          .PUT('/api/me/webhooks/{webhook_id}', {
            params: { path: { webhook_id: hook.id } },
            body: { name, url: url.trim() || null, event_types: [...events], only_mine: onlyMine },
          })
          .then(unwrap)
        onSaved()
      } else {
        const created = await api.POST('/api/me/webhooks', { body: { name, url, event_types: [...events], only_mine: onlyMine } }).then(unwrap)
        onSaved(created.secret)
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
      onOpenChange={(o) => !o && onClose()}
      title={hook ? `Edit ${hook.name}` : 'Add a webhook'}
      primaryLabel="Save"
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      <div className="me-form">
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">URL</span>
          <input
            className="input"
            type="url"
            value={url}
            placeholder={hook ? `Keep ${hook.url_masked}` : 'https://discord.com/api/webhooks/…'}
            onChange={(e) => setUrl(e.target.value)}
          />
          <span className="field__hint">A Discord channel webhook, or any https address that takes JSON.</span>
        </label>
        <label className="me-row" htmlFor="hook-mine">
          <span className="me-row__text">
            <span className="me-row__title">Only things about me</span>
            <span className="me-row__sub">Off sends every match in the areas you pick.</span>
          </span>
          <input id="hook-mine" type="checkbox" role="switch" className="switch" checked={onlyMine} onChange={(e) => setOnlyMine(e.target.checked)} />
        </label>
        {data.areas.map((area) => (
          <fieldset key={area.key} className="me-checks" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="field__label">
              {area.label} <span className="field__hint">{area.hint}</span>
            </legend>
            {data.events
              .filter((e) => e.area === area.key)
              .map((e) => (
                <label key={e.event_type}>
                  <input
                    type="checkbox"
                    checked={events.has(e.event_type)}
                    onChange={(ev) => {
                      const next = new Set(events)
                      if (ev.target.checked) next.add(e.event_type)
                      else next.delete(e.event_type)
                      setEvents(next)
                    }}
                  />
                  {e.label}
                </label>
              ))}
          </fieldset>
        ))}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}

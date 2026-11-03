import { useId, useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { useCommunity } from '../../../community/CommunityProvider'
import { Button, Chip, Dialog, FormDialog, Icon, RowMenu, Spinner, type MenuItem, type Tone } from '../../../components'
import { effectiveZone, formatDayTime } from '../../../lib/time'
import { websiteUrl } from '../../../reading/data'
import { CheckList, ListCard, LoadError, SettingsCard, useAdminQuery, useDraft } from '../../kit'

type Webhook = Schemas['WebhookRow']
type KeyRow = Schemas['RandomizerKeyRow']

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

const AUTO_SYNC: Record<number, string> = {
  0: 'Off',
  15: 'Every 15 minutes',
  30: 'Every 30 minutes',
  60: 'Every hour',
  120: 'Every 2 hours',
  360: 'Every 6 hours',
}

function Notes({ note, failure }: { note?: string | null; failure?: string | null }) {
  if (!note && !failure) return null
  return (
    <div className="settings-card__body">
      {note && (
        <p className="settings-saved" role="status">
          {note}
        </p>
      )}
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
    </div>
  )
}

/** Sign-ins come back to the website, so connecting opens it there. */
function SignInButton({ label }: { label: string }) {
  const { community } = useCommunity()
  return (
    <Button variant="outline" icon="open_in_new" asChild>
      <a href={websiteUrl('/admin/settings/integrations', community.slug)} target="_blank" rel="noreferrer">
        {label}
      </a>
    </Button>
  )
}

function useAction(reload: () => void) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  async function run(action: () => Promise<string | null>): Promise<boolean> {
    setBusy(true)
    setFailure(null)
    setNote(null)
    try {
      setNote(await action())
      reload()
      return true
    } catch (err) {
      setFailure(message(err))
      return false
    } finally {
      setBusy(false)
    }
  }
  return { busy, note, failure, run, clearFailure: () => setFailure(null) }
}

async function noContent(r: { response: Response; error?: unknown }) {
  if (!r.response.ok) unwrap(r as never)
}

function DisconnectDialog({
  service,
  open,
  onOpenChange,
  description,
  busy,
  failure,
  onConfirm,
}: {
  service: string
  open: boolean
  onOpenChange: (open: boolean) => void
  description: string
  busy: boolean
  failure: string | null
  onConfirm: () => void
}) {
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Disconnect ${service}?`}
      description={description}
      primaryLabel="Disconnect"
      destructive
      busy={busy}
      busyLabel="Disconnecting…"
      onPrimary={onConfirm}
      size="sm"
    >
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
    </FormDialog>
  )
}

function ChallongeCard() {
  const id = useId()
  const { data, error, reload } = useAdminQuery('settings/integrations/challonge', () =>
    api.GET('/api/settings/integrations/challonge').then(unwrap),
  )
  const [confirming, setConfirming] = useState(false)
  const act = useAction(reload)

  const body = () => {
    if (error && !data) return <LoadError error={error} onRetry={reload} />
    if (!data) return <Spinner label="Loading Challonge" />
    return null
  }
  const waiting = body()
  return (
    <ListCard title="Challonge" icon="account_tree" subtitle="Brackets synced from Challonge, and sign-ups written back.">
      {waiting ? (
        <div className="settings-card__body">{waiting}</div>
      ) : data && !data.configured ? (
        <div className="settings-card__body">
          <p className="settings-note">Challonge sign-in isn't set up on this server. Ask the platform admin to turn it on.</p>
        </div>
      ) : data ? (
        <>
          <div className="settings-list__row">
            <Icon
              name={data.connected ? 'check_circle' : 'link_off'}
              size={20}
              className={data.connected ? 'settings-status--ok' : 'settings-status--muted'}
            />
            <span className="settings-row__text">
              <span className="settings-row__title">
                {data.connected ? `Signed in as ${data.username ?? 'an unknown account'}` : 'Not signed in'}
              </span>
              <span className="settings-row__sub">
                {data.connected
                  ? `${plural(data.linked_tournaments, 'tournament')} linked · ${data.can_write_signups ? 'can write sign-ups' : "can't write sign-ups"} · ${data.request_usage.toLocaleString()} API requests this month`
                  : 'Signing in opens the website, then Challonge, and comes back there.'}
              </span>
            </span>
            {data.connected ? (
              <RowMenu
                label="Challonge actions"
                items={[{ label: 'Disconnect…', icon: 'link_off', destructive: true, onSelect: () => setConfirming(true) }]}
              />
            ) : (
              <SignInButton label="Sign in" />
            )}
          </div>
          {data.connected && (
            <div className="settings-card__body">
              {!data.can_write_signups && (
                <p className="settings-warning">
                  Players can't sign up for Challonge brackets until someone signs in again.{' '}
                </p>
              )}
              {!data.can_write_signups && <SignInButton label="Sign in again" />}
              <div className="field settings-number">
                <label className="field__label" htmlFor={`${id}-sync`}>
                  Sync automatically
                </label>
                <select
                  id={`${id}-sync`}
                  className="input"
                  value={data.auto_sync_minutes}
                  disabled={act.busy}
                  onChange={(e) => {
                    const minutes = Number(e.target.value)
                    void act.run(async () => {
                      await noContent(await api.PUT('/api/settings/integrations/challonge/auto-sync', { body: { minutes } }))
                      return minutes ? `Brackets now sync ${AUTO_SYNC[minutes]?.toLowerCase() ?? `every ${minutes} minutes`}.` : 'Automatic sync is off.'
                    })
                  }}
                >
                  {data.auto_sync_choices.map((m) => (
                    <option key={m} value={m}>
                      {AUTO_SYNC[m] ?? `Every ${m} minutes`}
                    </option>
                  ))}
                </select>
                <span className="field__hint">
                  Picks up results reported on Challonge. Finished brackets are skipped.
                  {data.auto_sync_minutes > 0 &&
                    ` With ${plural(data.auto_sync_brackets, 'unfinished bracket')}, that's about ${data.auto_sync_requests_per_day.toLocaleString()} API requests a day.`}
                </span>
              </div>
              {data.communities.length > 0 && (
                <p className="settings-note">
                  Communities asked for at sign-in:{' '}
                  {data.communities.map((c) => (c.tournaments ? `${c.slug} (${plural(c.tournaments, 'tournament')})` : c.slug)).join(', ')}.
                </p>
              )}
            </div>
          )}
        </>
      ) : null}
      <Notes note={act.note} failure={!confirming ? act.failure : null} />
      <DisconnectDialog
        service="Challonge"
        open={confirming}
        onOpenChange={(open) => {
          setConfirming(open)
          act.clearFailure()
        }}
        description="Every linked tournament stops syncing until someone signs in again."
        busy={act.busy}
        failure={act.failure}
        onConfirm={() =>
          void act
            .run(async () => {
              await noContent(await api.POST('/api/settings/integrations/challonge/disconnect'))
              return 'Challonge disconnected.'
            })
            .then((ok) => ok && setConfirming(false))
        }
      />
    </ListCard>
  )
}

const ACCESS: Record<string, { label: string; tone: Tone }> = {
  true: { label: 'Can report', tone: 'ok' },
  false: { label: 'Read only', tone: 'pending' },
  null: { label: "Couldn't check", tone: 'neutral' },
}

function StartggCard() {
  const { data, error, reload } = useAdminQuery('settings/integrations/startgg', () =>
    api.GET('/api/settings/integrations/startgg').then(unwrap),
  )
  const [confirming, setConfirming] = useState(false)
  const act = useAction(reload)
  const zone = effectiveZone(null)

  return (
    <ListCard title="start.gg" icon="sports_esports" subtitle="Brackets mirrored from start.gg, and confirmed results reported back.">
      {error && !data ? (
        <div className="settings-card__body">
          <LoadError error={error} onRetry={reload} />
        </div>
      ) : !data ? (
        <div className="settings-card__body">
          <Spinner label="Loading start.gg" />
        </div>
      ) : !data.configured && !data.connected ? (
        <div className="settings-card__body">
          <p className="settings-note">
            start.gg sign-in isn't set up on this server yet.
            {data.platform_token && " Linked events still sync with the server's token, but results are reported only with a community sign-in."}
          </p>
        </div>
      ) : (
        <>
          <div className="settings-list__row">
            <Icon
              name={data.connected && !data.expired ? 'check_circle' : data.connected ? 'warning' : 'link_off'}
              size={20}
              className={
                data.connected && !data.expired ? 'settings-status--ok' : data.connected ? 'settings-status--warn' : 'settings-status--muted'
              }
            />
            <span className="settings-row__text">
              <span className="settings-row__title">
                {data.connected ? `Signed in as ${data.username ?? 'an unknown account'}` : 'Not signed in'}
              </span>
              <span className="settings-row__sub">
                {!data.connected
                  ? 'Sign in with an account that is Reporter or higher on your start.gg tournaments. It opens the website, then start.gg, and comes back there.'
                  : data.expired
                    ? "The sign-in lapsed and couldn't be renewed. Sign in again to keep reporting results."
                    : `Renews on its own${data.renewed_at ? `, last on ${formatDayTime(data.renewed_at, zone)}` : ''}.`}
              </span>
            </span>
            {data.connected && !data.expired ? (
              <RowMenu
                label="start.gg actions"
                items={[{ label: 'Disconnect…', icon: 'link_off', destructive: true, onSelect: () => setConfirming(true) }]}
              />
            ) : (
              <SignInButton label={data.connected ? 'Sign in again' : 'Sign in'} />
            )}
          </div>
          {data.connected && (
            <ul className="settings-list">
              {data.linked.length === 0 && (
                <li className="settings-list__row">
                  <span className="settings-row__sub">No tournaments are linked to start.gg yet. Link one from the tournament's settings.</span>
                </li>
              )}
              {data.linked.map((t) => {
                const access = ACCESS[String(t.can_report)] ?? ACCESS.null!
                return (
                  <li key={t.tournament_id} className="settings-list__row">
                    <span className="settings-row__text">
                      <span className="settings-row__title">
                        <a href={t.event_url} target="_blank" rel="noreferrer">
                          {t.name}
                        </a>
                        <Chip tone={access.tone}>{access.label}</Chip>
                      </span>
                      <span className="settings-row__sub">
                        {t.can_report === false
                          ? `${data.username ?? 'This account'} isn't an admin there. Ask one of its admins to add them as Reporter, then check again.`
                          : `Reporting confirmed results: ${t.reports_results ? 'on' : 'off'}`}
                      </span>
                    </span>
                  </li>
                )
              })}
            </ul>
          )}
          {data.connected && data.linked.some((t) => t.can_report !== true) && (
            <div className="settings-card__body">
              <Button variant="ghost" icon="refresh" onClick={reload}>
                Check again
              </Button>
            </div>
          )}
        </>
      )}
      <Notes note={act.note} failure={!confirming ? act.failure : null} />
      <DisconnectDialog
        service="start.gg"
        open={confirming}
        onOpenChange={(open) => {
          setConfirming(open)
          act.clearFailure()
        }}
        description="Results stop being reported to start.gg, and syncs fall back to the server's token if it has one."
        busy={act.busy}
        failure={act.failure}
        onConfirm={() =>
          void act
            .run(async () => {
              await noContent(await api.POST('/api/settings/integrations/startgg/disconnect'))
              return 'start.gg disconnected.'
            })
            .then((ok) => ok && setConfirming(false))
        }
      />
    </ListCard>
  )
}

function host(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function ago(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return `${plural(hours, 'hour')} ago`
}

function webhookSub(w: Webhook): string {
  const events = w.event_types.includes('*') ? 'all updates' : plural(w.event_types.length, 'event')
  const sent = w.last_sent_at ? `last sent ${ago(w.last_sent_at)}` : 'nothing sent today'
  return [host(w.url), events, sent].join(' · ')
}

function WebhookDialog({
  webhook,
  onClose,
  onDone,
}: {
  webhook: Webhook | null
  onClose: () => void
  onDone: (note: string, secret?: string) => void
}) {
  const id = useId()
  const { data, error, reload } = useAdminQuery('settings/integrations/webhook-options', () =>
    api.GET('/api/settings/integrations/webhook-options').then(unwrap),
  )
  const [name, setName] = useState(webhook?.name ?? '')
  const [url, setUrl] = useState(webhook?.url ?? '')
  const [types, setTypes] = useState<string[]>(webhook?.event_types ?? [])
  const [events, setEvents] = useState<string[]>((webhook?.event_ids ?? []).map(String))
  const [allEvents, setAllEvents] = useState(webhook ? webhook.event_ids == null : true)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  async function submit() {
    setBusy(true)
    setFailure(null)
    const body = {
      name,
      url,
      event_types: types.includes('*') ? ['*'] : types,
      event_ids: allEvents ? null : events.map(Number),
    }
    try {
      if (webhook) {
        await api.PUT('/api/webhooks/{webhook_id}', { params: { path: { webhook_id: webhook.id } }, body }).then(unwrap)
        onDone(`${name} saved.`)
      } else {
        const created = await api.POST('/api/webhooks', { body: { ...body, is_active: true } }).then(unwrap)
        onDone(`${name} added.`, created.secret)
      }
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={webhook ? `Edit ${webhook.name}` : 'Add a webhook'}
      description="Wizzrobe posts each picked event to this address, signed with a secret only you and the receiver know."
      primaryLabel={webhook ? 'Save' : 'Add webhook'}
      busy={busy}
      busyLabel={webhook ? 'Saving…' : 'Adding…'}
      onPrimary={() => void submit()}
    >
      {error && !data ? (
        <LoadError error={error} onRetry={reload} />
      ) : !data ? (
        <Spinner label="Loading events" />
      ) : (
        <div className="settings-stack">
          <label className="field">
            <span className="field__label">Name</span>
            <input className="input" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">Address</span>
            <input
              className="input"
              type="url"
              inputMode="url"
              placeholder="https://"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
            <span className="field__hint">Must start with https://.</span>
          </label>
          <CheckList
            legend="Events to send"
            items={data.event_types.map((t) => ({ value: t, label: t === '*' ? 'All updates' : t }))}
            value={types}
            onChange={setTypes}
          />
          <div className="field">
            <label className="t-check" htmlFor={`${id}-all`}>
              <input id={`${id}-all`} type="checkbox" checked={allEvents} onChange={(e) => setAllEvents(e.target.checked)} />
              From every event in the community
            </label>
          </div>
          {!allEvents && (
            <CheckList
              legend="Only from these events"
              items={data.events.map((e) => ({ value: String(e.id), label: e.name }))}
              value={events}
              onChange={setEvents}
            />
          )}
          {failure && (
            <p className="form-error" role="alert">
              {failure}
            </p>
          )}
        </div>
      )}
    </FormDialog>
  )
}

function SecretDialog({ title, secret, onClose }: { title: string; secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={title}
      size="sm"
      description="Give this signing secret to the receiving app now. It isn't shown again."
      footer={
        <>
          <Button variant="outline" icon="content_copy" onClick={() => void navigator.clipboard?.writeText(secret).then(() => setCopied(true))}>
            {copied ? 'Copied' : 'Copy secret'}
          </Button>
          <span className="dialog__spacer" />
          <Button onClick={onClose}>Done</Button>
        </>
      }
    >
      <code className="settings-secret">{secret}</code>
    </Dialog>
  )
}

function WebhooksCard() {
  const { data, error, reload } = useAdminQuery('settings/integrations/webhooks', () =>
    api.GET('/api/settings/integrations/webhooks').then(unwrap),
  )
  const [editing, setEditing] = useState<Webhook | 'new' | null>(null)
  const [deleting, setDeleting] = useState<Webhook | null>(null)
  const [rotating, setRotating] = useState<Webhook | null>(null)
  const [secret, setSecret] = useState<{ title: string; value: string } | null>(null)
  const act = useAction(reload)
  const path = (w: Webhook) => ({ params: { path: { webhook_id: w.id } } })

  const toggle = (w: Webhook) =>
    act.run(async () => {
      await api.PUT('/api/webhooks/{webhook_id}', { ...path(w), body: { is_active: !w.is_active } }).then(unwrap)
      return w.is_active ? `${w.name} is paused.` : `${w.name} is sending again.`
    })

  return (
    <ListCard
      title="Webhooks"
      icon="webhook"
      subtitle="Wizzrobe posts event updates to these addresses, signed."
      action={
        <Button icon="add" onClick={() => setEditing('new')}>
          Add
        </Button>
      }
    >
      {error && !data ? (
        <div className="settings-card__body">
          <LoadError error={error} onRetry={reload} />
        </div>
      ) : !data ? (
        <div className="settings-card__body">
          <Spinner label="Loading webhooks" />
        </div>
      ) : data.length === 0 ? (
        <div className="settings-card__body">
          <p className="settings-note">No webhooks yet. Add one to send updates to another app.</p>
        </div>
      ) : (
        <ul className="settings-list">
          {data.map((w) => {
            const items: MenuItem[] = [
              { label: 'Edit…', icon: 'edit', onSelect: () => setEditing(w) },
              w.is_active
                ? { label: 'Pause', icon: 'pause', onSelect: () => void toggle(w) }
                : { label: 'Resume', icon: 'play_arrow', onSelect: () => void toggle(w) },
              { label: 'New secret…', icon: 'key', onSelect: () => setRotating(w) },
              { label: 'Delete…', icon: 'delete', destructive: true, onSelect: () => setDeleting(w) },
            ]
            return (
              <li key={w.id} className="settings-list__row">
                <span className="settings-row__text">
                  <span className="settings-row__title">
                    {w.name}
                    {!w.is_active && <Chip tone="pending">Paused</Chip>}
                    {w.failed > 0 && <Chip tone="cancelled">{`${w.failed} of ${w.failed + w.delivered} failed today`}</Chip>}
                  </span>
                  <span className="settings-row__sub">{webhookSub(w)}</span>
                </span>
                <RowMenu label={`Actions for ${w.name}`} items={items} />
              </li>
            )
          })}
        </ul>
      )}
      <Notes note={act.note} failure={!deleting && !rotating ? act.failure : null} />
      {editing && (
        <WebhookDialog
          webhook={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onDone={(note, created) => {
            setEditing(null)
            void act.run(async () => note)
            if (created) setSecret({ title: 'Webhook added', value: created })
          }}
        />
      )}
      <FormDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) setDeleting(null)
          act.clearFailure()
        }}
        title={deleting ? `Delete ${deleting.name}?` : 'Delete webhook'}
        description="It stops getting updates straight away, and its delivery history goes with it."
        primaryLabel="Delete"
        destructive
        busy={act.busy}
        busyLabel="Deleting…"
        onPrimary={() =>
          deleting &&
          void act
            .run(async () => {
              await noContent(await api.DELETE('/api/webhooks/{webhook_id}', path(deleting)))
              return `${deleting.name} deleted.`
            })
            .then((ok) => ok && setDeleting(null))
        }
        size="sm"
      >
        {act.failure && (
          <p className="form-error" role="alert">
            {act.failure}
          </p>
        )}
      </FormDialog>
      <FormDialog
        open={rotating !== null}
        onOpenChange={(open) => {
          if (!open) setRotating(null)
          act.clearFailure()
        }}
        title={rotating ? `New secret for ${rotating.name}?` : 'New secret'}
        description="The old secret stops working straight away, so the receiving app needs the new one before it accepts another update."
        primaryLabel="Make a new secret"
        busy={act.busy}
        busyLabel="Making…"
        onPrimary={() => {
          const w = rotating
          if (!w) return
          void act.run(async () => {
            const r = await api.POST('/api/webhooks/{webhook_id}/regenerate-secret', path(w)).then(unwrap)
            setRotating(null)
            setSecret({ title: `New secret for ${w.name}`, value: r.secret })
            return null
          })
        }}
        size="sm"
      >
        {act.failure && (
          <p className="form-error" role="alert">
            {act.failure}
          </p>
        )}
      </FormDialog>
      {secret && <SecretDialog title={secret.title} secret={secret.value} onClose={() => setSecret(null)} />}
    </ListCard>
  )
}

function RandomizerKeysCard() {
  const { data, error, reload } = useAdminQuery('settings/integrations/randomizer-keys', () =>
    api.GET('/api/settings/integrations/randomizer-keys').then(unwrap),
  )
  const empty = Object.fromEntries((data ?? []).map((r) => [`${r.randomizer}/${r.key}`, '']))
  const draft = useDraft<Record<string, string>>(data ? empty : undefined)
  const [clearing, setClearing] = useState<KeyRow | null>(null)
  const act = useAction(reload)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading randomizer keys" />
  const value = draft.value
  return (
    <>
      <SettingsCard
        title="Randomizer keys"
        icon="key"
        subtitle="For randomizers that need an API key to roll seeds. A randomizer stays out of the preset and tournament pickers until its key is set. Stored keys are never shown again."
        dirty={draft.dirty}
        onDiscard={draft.discard}
        onSave={async () => {
          for (const row of data) {
            const typed = value[`${row.randomizer}/${row.key}`]?.trim()
            if (!typed) continue
            await noContent(
              await api.PUT('/api/settings/integrations/randomizer-keys/{randomizer}/{key}', {
                params: { path: { randomizer: row.randomizer, key: row.key } },
                body: { value: typed },
              }),
            )
          }
          draft.discard()
          reload()
        }}
      >
        {data.map((row) => {
          const k = `${row.randomizer}/${row.key}`
          return (
            <div key={k} className="field">
              <label className="field__label" htmlFor={`key-${k}`}>
                {row.label}
              </label>
              <input
                id={`key-${k}`}
                className="input"
                type="password"
                autoComplete="off"
                placeholder={row.configured ? 'Set. Paste a new one to replace it' : 'Not set'}
                value={value[k] ?? ''}
                onChange={(e) => draft.patch({ [k]: e.target.value })}
              />
              <span className="field__hint">
                {row.help_text}
                {row.configured && (
                  <>
                    {' '}
                    <button type="button" className="settings-link-button" onClick={() => setClearing(row)}>
                      Clear this key…
                    </button>
                  </>
                )}
              </span>
            </div>
          )
        })}
        <Notes note={act.note} failure={!clearing ? act.failure : null} />
      </SettingsCard>
      <FormDialog
        open={clearing !== null}
        onOpenChange={(open) => {
          if (!open) setClearing(null)
          act.clearFailure()
        }}
        title={clearing ? `Clear the ${clearing.label}?` : 'Clear key'}
        description="Seeds for it can't be rolled, and it leaves the preset and tournament pickers, until someone enters a key again."
        primaryLabel="Clear"
        destructive
        busy={act.busy}
        busyLabel="Clearing…"
        onPrimary={() => {
          const row = clearing
          if (!row) return
          void act
            .run(async () => {
              await noContent(
                await api.DELETE('/api/settings/integrations/randomizer-keys/{randomizer}/{key}', {
                  params: { path: { randomizer: row.randomizer, key: row.key } },
                }),
              )
              return `${row.label} cleared.`
            })
            .then((ok) => ok && setClearing(null))
        }}
        size="sm"
      >
        {act.failure && (
          <p className="form-error" role="alert">
            {act.failure}
          </p>
        )}
      </FormDialog>
    </>
  )
}

function AiCard() {
  const id = useId()
  const { data, error, reload } = useAdminQuery('settings/integrations/ai', () => api.GET('/api/settings/integrations/ai').then(unwrap))
  const draft = useDraft(
    data && {
      provider: data.provider,
      model: data.model,
      base_url: data.base_url ?? '',
      api_key: '',
      daily_limit: data.daily_limit,
    },
  )
  const [clearing, setClearing] = useState(false)
  const act = useAction(reload)
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data || !draft.value) return <Spinner label="Loading AI writing help" />
  const value = draft.value
  const compatible = value.provider === 'openai_compatible'
  const state = data.configured ? (data.key_readable ? 'A key is set.' : 'The stored key needs entering again.') : 'No provider yet.'
  return (
    <>
      <SettingsCard
        title="AI writing help"
        icon="lightbulb"
        subtitle={`Suggests survey questions. Each press is one request, usually a few cents, on your own account. Only the event's structure and totals are sent, never names or anything people wrote. ${state}`}
        dirty={draft.dirty}
        onDiscard={draft.discard}
        onSave={async () => {
          await api
            .PUT('/api/settings/integrations/ai', {
              body: {
                provider: value.provider,
                model: value.model,
                base_url: compatible ? value.base_url || null : null,
                api_key: value.api_key || null,
                daily_limit: value.daily_limit,
              },
            })
            .then(unwrap)
          draft.discard()
          reload()
        }}
      >
        <div className="field settings-number">
          <label className="field__label" htmlFor={`${id}-provider`}>
            Provider
          </label>
          <select
            id={`${id}-provider`}
            className="input"
            value={value.provider}
            onChange={(e) => {
              const provider = e.target.value as Schemas['AiProvider']
              const defaults = Object.values(data.default_models)
              draft.patch({
                provider,
                model: !value.model || defaults.includes(value.model) ? (data.default_models[provider] ?? '') : value.model,
              })
            }}
          >
            <option value="anthropic">Claude (Anthropic)</option>
            <option value="openai_compatible">OpenAI-compatible</option>
          </select>
        </div>
        <label className="field">
          <span className="field__label">Model</span>
          <input className="input" maxLength={120} value={value.model} onChange={(e) => draft.patch({ model: e.target.value })} />
        </label>
        {compatible && (
          <label className="field">
            <span className="field__label">Provider address</span>
            <input
              className="input"
              type="url"
              inputMode="url"
              maxLength={255}
              placeholder="https://api.openai.com/v1"
              value={value.base_url}
              onChange={(e) => draft.patch({ base_url: e.target.value })}
            />
          </label>
        )}
        <label className="field">
          <span className="field__label">API key</span>
          <input
            className="input"
            type="password"
            autoComplete="off"
            placeholder={data.configured && data.key_readable ? 'Set. Leave blank to keep it' : 'Paste the key'}
            value={value.api_key}
            onChange={(e) => draft.patch({ api_key: e.target.value })}
          />
        </label>
        <label className="field settings-number">
          <span className="field__label">Requests a day</span>
          <input
            className="input"
            type="number"
            inputMode="numeric"
            min={1}
            max={200}
            value={value.daily_limit}
            onChange={(e) => draft.patch({ daily_limit: Math.trunc(Number(e.target.value) || 0) })}
          />
          <span className="field__hint">All staff together, 1 to 200.</span>
        </label>
        {data.configured && (
          <p className="settings-note">
            <button type="button" className="settings-link-button" onClick={() => setClearing(true)}>
              Remove the provider…
            </button>
          </p>
        )}
        <Notes note={act.note} failure={!clearing ? act.failure : null} />
      </SettingsCard>
      <FormDialog
        open={clearing}
        onOpenChange={(open) => {
          setClearing(open)
          act.clearFailure()
        }}
        title="Remove the AI provider?"
        description="Its key is forgotten, and Suggest questions leaves the survey builder until someone sets one up again."
        primaryLabel="Remove"
        destructive
        busy={act.busy}
        busyLabel="Removing…"
        onPrimary={() =>
          void act
            .run(async () => {
              await noContent(await api.DELETE('/api/settings/integrations/ai'))
              return 'AI provider removed.'
            })
            .then((ok) => ok && setClearing(false))
        }
        size="sm"
      >
        {act.failure && (
          <p className="form-error" role="alert">
            {act.failure}
          </p>
        )}
      </FormDialog>
    </>
  )
}

/** Settings → Integrations: one stack of cards, one per service this community signs in to or sends to. */
export function Integrations() {
  const { data, error, reload } = useAdminQuery('settings/integrations', () => api.GET('/api/settings/integrations').then(unwrap))
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data) return <Spinner label="Loading integrations" />
  return (
    <>
      {data.challonge && <ChallongeCard />}
      {data.startgg && <StartggCard />}
      {data.webhooks && <WebhooksCard />}
      {data.randomizer_keys && <RandomizerKeysCard />}
      {data.ai && <AiCard />}
    </>
  )
}

import { useId, useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { useCommunity } from '../../../community/CommunityProvider'
import { Button, Chip, Dialog, FormDialog, Icon, RowMenu, Spinner } from '../../../components'
import { useEventQuery } from '../../../me/data'
import { websiteUrl } from '../../../reading/data'
import { CheckList, ListCard, LoadError, useAdminQuery } from '../../kit'

type Link = Schemas['DiscordLinkResponse']
type Mapping = Schemas['DiscordMappingRow']
type Options = Schemas['DiscordMappingOptionsResponse']
type SyncResult = Schemas['DiscordSyncResponse']

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

function Notes({ note, failure }: { note: string | null; failure: string | null }) {
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

function ServerCard({ link, onChanged }: { link: Link; onChanged: () => void }) {
  const { community } = useCommunity()
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const label = link.server_name ?? 'the linked server'

  async function disconnect() {
    setBusy(true)
    setFailure(null)
    try {
      const r = await api.POST('/api/settings/discord/disconnect')
      if (!r.response.ok) unwrap(r)
      setConfirming(false)
      onChanged()
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ListCard title="Server" icon="forum" subtitle="The Discord server this community is linked to.">
      <div className="settings-list__row">
        {link.linked ? (
          <>
            <Icon name={link.reachable ? 'check_circle' : 'warning'} size={20} className={link.reachable ? 'settings-status--ok' : 'settings-status--warn'} />
            <span className="settings-row__text">
              <span className="settings-row__title">{link.server_name ?? 'Linked server'}</span>
              <span className="settings-row__sub">
                {link.reachable
                  ? 'The bot is in the server.'
                  : "The bot can't reach this server right now. Role sync picks up again once it can."}
              </span>
            </span>
            <Button variant="outline" icon="link_off" onClick={() => setConfirming(true)}>
              Disconnect…
            </Button>
          </>
        ) : (
          <>
            <Icon name="link_off" size={20} className="settings-status--muted" />
            <span className="settings-row__text">
              <span className="settings-row__title">No server linked</span>
              <span className="settings-row__sub">
                Connecting adds the bot to a server you manage. It opens the website, because Discord's sign-in comes
                back there.
              </span>
            </span>
            <Button variant="outline" icon="open_in_new" asChild>
              <a href={websiteUrl('/admin/settings/discord', community.slug)} target="_blank" rel="noreferrer">
                Connect
              </a>
            </Button>
          </>
        )}
      </div>
      <Notes note={null} failure={!confirming ? failure : null} />
      <FormDialog
        open={confirming}
        onOpenChange={setConfirming}
        title={`Disconnect ${label}?`}
        description="Role sync and Discord events stop until someone connects a server again. The bot stays in the server."
        primaryLabel="Disconnect"
        destructive
        busy={busy}
        busyLabel="Disconnecting…"
        onPrimary={() => void disconnect()}
        size="sm"
      >
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </FormDialog>
    </ListCard>
  )
}

function AddMappingsDialog({ onClose, onDone }: { onClose: () => void; onDone: (note: string) => void }) {
  const id = useId()
  const { data, error, reload } = useAdminQuery('settings/discord/options', () =>
    api.GET('/api/settings/discord/mapping-options').then(unwrap),
  )
  const [roles, setRoles] = useState<string[]>([])
  const [grants, setGrants] = useState<string[]>([])
  const [tournament, setTournament] = useState('')
  const [event, setEvent] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const kindOf = (opts: Options, v: string) => opts.grants.find((g) => g.value === v)?.kind
  const wantsTournament = !!data && grants.some((g) => kindOf(data, g) === 'tournament')
  const wantsEvent = !!data && grants.some((g) => kindOf(data, g) === 'event')
  const eventValue = event ?? (data?.default_event_id != null ? String(data.default_event_id) : '')

  async function submit() {
    if (!data) return
    setFailure(null)
    if (roles.length === 0 || grants.length === 0) {
      setFailure('Pick one or more Discord roles and what they grant.')
      return
    }
    if (wantsTournament && !tournament) {
      setFailure('Pick the tournament for the tournament grants.')
      return
    }
    setBusy(true)
    try {
      const picked = data.discord_roles.filter((r) => roles.includes(r.id))
      const result = await api
        .POST('/api/settings/discord/mappings', {
          body: {
            discord_roles: picked,
            app_roles: grants.filter((g) => kindOf(data, g) !== 'tournament') as Schemas['Role'][],
            tournament_grants: grants.filter((g) => kindOf(data, g) === 'tournament') as Schemas['TournamentGrant'][],
            tournament_id: wantsTournament ? Number(tournament) : null,
            event_id: wantsEvent && eventValue ? Number(eventValue) : null,
          },
        })
        .then(unwrap)
      onDone(
        `Added ${plural(result.created, 'mapping')}${result.skipped ? ` (${result.skipped} already existed)` : ''}. Members pick them up at their next sign-in, or sync everyone now.`,
      )
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  const pairs = roles.length * grants.length
  return (
    <FormDialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Add role mappings"
      description="Every Discord role you pick gets every grant you pick."
      primaryLabel={pairs > 1 ? `Add ${pairs} mappings` : 'Add mapping'}
      busy={busy}
      busyLabel="Adding…"
      onPrimary={() => void submit()}
    >
      {error && !data ? (
        <LoadError error={error} onRetry={reload} />
      ) : !data ? (
        <Spinner label="Loading the server's roles" />
      ) : (
        <div className="settings-stack">
          <CheckList
            legend="Discord roles"
            items={data.discord_roles.map((r) => ({ value: r.id, label: r.name, color: r.color }))}
            value={roles}
            onChange={setRoles}
          />
          <CheckList
            legend="Grants"
            items={data.grants.map((g) => ({
              value: g.value,
              label: g.label,
              hint: g.kind === 'event' ? 'in one event' : g.kind === 'tournament' ? 'on one tournament' : undefined,
            }))}
            value={grants}
            onChange={setGrants}
          />
          {wantsTournament && (
            <div className="field">
              <label className="field__label" htmlFor={`${id}-t`}>
                Tournament
              </label>
              <select id={`${id}-t`} className="input" value={tournament} onChange={(e) => setTournament(e.target.value)}>
                <option value="">Pick a tournament</option>
                {data.tournaments.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
              {data.tournaments.length === 0 && <span className="field__hint">No active tournaments. Create one first.</span>}
            </div>
          )}
          {wantsEvent && (
            <div className="field">
              <label className="field__label" htmlFor={`${id}-e`}>
                Event
              </label>
              <select id={`${id}-e`} className="input" value={eventValue} onChange={(e) => setEvent(e.target.value)}>
                {data.events.map((ev) => (
                  <option key={ev.id} value={ev.id}>
                    {ev.name}
                  </option>
                ))}
              </select>
            </div>
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

function SyncResultDialog({ result, onClose }: { result: SyncResult; onClose: () => void }) {
  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title="Roles synced"
      size="sm"
      description={`Checked ${plural(result.users_processed, 'person')}: ${result.granted} granted, ${result.revoked} taken away${result.skipped ? `, ${result.skipped} couldn't be checked` : ''}.`}
      footer={
        <>
          <span className="dialog__spacer" />
          <Button onClick={onClose}>Done</Button>
        </>
      }
    >
      {result.created.length > 0 && (
        <p className="settings-note">New accounts for server members who'd never signed in: {result.created.join(', ')}.</p>
      )}
      {result.changed.length === 0 ? (
        <p className="settings-note">Nobody's roles changed.</p>
      ) : (
        <ul className="settings-list">
          {result.changed.map((c) => (
            <li key={c.name} className="settings-list__row">
              <span className="settings-row__text">
                <span className="settings-row__title">{c.name}</span>
                <span className="settings-row__sub">
                  {[c.granted.length && `Gained ${c.granted.join(', ')}`, c.revoked.length && `Lost ${c.revoked.join(', ')}`]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  )
}

function MappingsCard() {
  const { data, error, reload } = useAdminQuery('settings/discord/mappings', () =>
    api.GET('/api/settings/discord/mappings').then(unwrap),
  )
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<Mapping | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [synced, setSynced] = useState<SyncResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function remove(m: Mapping) {
    setBusy(true)
    setFailure(null)
    try {
      const r = await api.DELETE('/api/discord-role-mappings/{mapping_id}', { params: { path: { mapping_id: m.id } } })
      if (!r.response.ok) unwrap(r)
      setRemoving(null)
      setNote(`Removed the mapping for ${m.discord_role_name}. Members lose it at their next sign-in or sync.`)
      reload()
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  async function sync() {
    setBusy(true)
    setFailure(null)
    try {
      setSynced(await api.POST('/api/settings/discord/sync').then(unwrap))
      setSyncing(false)
    } catch (err) {
      setFailure(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ListCard
      title="Role mappings"
      icon="badge"
      subtitle="When someone signs in, their Discord roles grant these. Roles staff gave by hand stay."
      action={
        <span className="settings-card__actions">
          <Button variant="outline" icon="sync" onClick={() => setSyncing(true)}>
            Sync everyone
          </Button>
          <Button icon="add" onClick={() => setAdding(true)}>
            Add
          </Button>
        </span>
      }
    >
      {error && !data ? (
        <div className="settings-card__body">
          <LoadError error={error} onRetry={reload} />
        </div>
      ) : !data ? (
        <div className="settings-card__body">
          <Spinner label="Loading role mappings" />
        </div>
      ) : data.length === 0 ? (
        <div className="settings-card__body">
          <p className="settings-note">No mappings yet. Add one to hand out roles here from Discord roles.</p>
        </div>
      ) : (
        <ul className="settings-list">
          {data.map((m) => (
            <li key={m.id} className="settings-list__row">
              <span className="settings-row__text">
                <span className="settings-row__title">
                  @{m.discord_role_name}
                  {m.grants_nothing && <Chip tone="pending">Grants nothing</Chip>}
                </span>
                <span className="settings-row__sub">
                  Grants {m.grants} · {m.applies_to}
                </span>
              </span>
              <RowMenu
                label={`Actions for ${m.discord_role_name} mapping`}
                items={[{ label: 'Remove…', icon: 'delete', destructive: true, onSelect: () => setRemoving(m) }]}
              />
            </li>
          ))}
        </ul>
      )}
      <Notes note={note} failure={!removing && !syncing ? failure : null} />
      {adding && (
        <AddMappingsDialog
          onClose={() => setAdding(false)}
          onDone={(n) => {
            setAdding(false)
            setNote(n)
            reload()
          }}
        />
      )}
      <FormDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={removing ? `Remove the mapping for @${removing.discord_role_name}?` : 'Remove mapping'}
        description="Members lose what it granted at their next sign-in or the next sync."
        primaryLabel="Remove"
        destructive
        busy={busy}
        busyLabel="Removing…"
        onPrimary={() => removing && void remove(removing)}
        size="sm"
      >
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </FormDialog>
      <FormDialog
        open={syncing}
        onOpenChange={setSyncing}
        title="Sync everyone's roles now?"
        description="This applies the mappings to the community's members and to anyone in the server holding a mapped role, and creates accounts for those who've never signed in. It can take a minute."
        primaryLabel="Sync"
        busy={busy}
        busyLabel="Syncing roles…"
        onPrimary={() => void sync()}
        size="sm"
      >
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </FormDialog>
      {synced && <SyncResultDialog result={synced} onClose={() => setSynced(null)} />}
    </ListCard>
  )
}

function EventsCard() {
  const query = useEventQuery()
  const { data, error, reload } = useAdminQuery('settings/discord/events', () =>
    api.GET('/api/settings/discord/events', { params: { query } }).then(unwrap),
  )
  const [pending, setPending] = useState<number | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  async function toggle(id: number, enabled: boolean) {
    setPending(id)
    setFailure(null)
    try {
      await api
        .PATCH('/api/discord-events/tournaments/{tournament_id}', {
          params: { query, path: { tournament_id: id } },
          body: { enabled },
        })
        .then(unwrap)
      reload()
    } catch (err) {
      setFailure(message(err))
    } finally {
      setPending(null)
    }
  }

  return (
    <ListCard title="Discord events" icon="event" subtitle="Mirror a tournament's scheduled matches as events in the server.">
      {error && !data ? (
        <div className="settings-card__body">
          <LoadError error={error} onRetry={reload} />
        </div>
      ) : !data ? (
        <div className="settings-card__body">
          <Spinner label="Loading Discord events" />
        </div>
      ) : (
        <>
          {data.problem && (
            <div className="settings-card__body">
              <p className="settings-warning">{data.problem} The switches are locked and syncing is paused until that's fixed.</p>
            </div>
          )}
          {data.tournaments.length === 0 ? (
            <div className="settings-card__body">
              <p className="settings-note">This event has no tournaments yet.</p>
            </div>
          ) : (
            <ul className="settings-list">
              {data.tournaments.map((t) => (
                <li key={t.id} className="settings-list__row">
                  <label className="settings-list__switch">
                    <span className="settings-row__text">
                      <span className="settings-row__title">{t.name}</span>
                      <span className="settings-row__sub">
                        {t.enabled ? `${plural(t.mirrored, 'event')} in Discord` : 'Off'}
                      </span>
                    </span>
                    <input
                      type="checkbox"
                      role="switch"
                      className="switch"
                      checked={t.enabled}
                      disabled={!!data.problem || pending === t.id}
                      onChange={(e) => void toggle(t.id, e.target.checked)}
                    />
                  </label>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <Notes note={null} failure={failure} />
    </ListCard>
  )
}

/** Settings → Discord: the linked server, its role mappings and the events mirror, each acting straight away. */
export function Discord() {
  const { data, error, reload } = useAdminQuery('settings/discord', () => api.GET('/api/settings/discord').then(unwrap))
  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data) return <Spinner label="Loading Discord settings" />
  return (
    <>
      {data.can_manage_roles && <ServerCard link={data} onChanged={reload} />}
      {data.can_manage_roles && data.linked && <MappingsCard />}
      {data.can_manage_events && <EventsCard />}
    </>
  )
}

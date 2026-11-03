import { useState } from 'react'
import { api, unwrap, type Schemas } from '../../../api/client'
import { Button, Chip, FormDialog, Icon, IconButton, RowMenu, Spinner } from '../../../components'
import { openOutsideSignIn, useReloadOnReturn } from '../../../lib/outsideSignIn'
import { effectiveZone, formatDay } from '../../../lib/time'
import { ListCard, LoadError, useAdminQuery } from '../../kit'
import { ago, DisconnectDialog, message, noContent, Notes, plural, useAction } from './integrationKit'

type Campaign = Schemas['PatreonCampaignResponse']
type Tier = Schemas['PatreonTierInfo']

const MAX_LABEL = 40

function labelSummary(tiers: Tier[]): string {
  const named = tiers.filter((t) => t.badge_label)
  if (named.length === 0) return 'Every patron gets “Patron”.'
  const parts = named.map((t) => `${t.title} → “${t.badge_label}”`)
  return named.length === tiers.length ? parts.join(', ') : `${parts.join(', ')}, everyone else “Patron”`
}

function TierLabelsDialog({ tiers, onClose, onSaved }: { tiers: Tier[]; onClose: () => void; onSaved: () => void }) {
  const [rows, setRows] = useState(() => tiers.map((t) => ({ ...t, badge_label: t.badge_label ?? '' })))
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  function move(index: number, by: -1 | 1) {
    setRows((prev) => {
      const next = [...prev]
      const [row] = next.splice(index, 1)
      next.splice(index + by, 0, row!)
      return next
    })
  }

  async function save() {
    setBusy(true)
    setFailure(null)
    try {
      unwrap(
        await api.PUT('/api/settings/integrations/patreon/tiers', {
          body: { tiers: rows.map((r) => ({ tier_id: r.tier_id, badge_label: r.badge_label.trim() || null })) },
        }),
      )
      onSaved()
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
      title="Badge labels"
      description="What each tier's patrons see beside their name. Leave one empty for “Patron”."
      primaryLabel="Save"
      busy={busy}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      {rows.length === 0 ? (
        <p className="settings-note">Your campaign has no tiers yet. Every patron gets “Patron”.</p>
      ) : (
        <ul className="settings-list patreon-tiers">
          {rows.map((r, i) => (
            <li key={r.tier_id} className="settings-list__row">
              <span className="patreon-tiers__move">
                <IconButton icon="arrow_upward" label={`Move ${r.title} up`} disabled={i === 0} onClick={() => move(i, -1)} />
                <IconButton
                  icon="arrow_downward"
                  label={`Move ${r.title} down`}
                  disabled={i === rows.length - 1}
                  onClick={() => move(i, 1)}
                />
              </span>
              <span className="settings-row__text">
                <span className="settings-row__title">{r.title}</span>
                <span className="settings-row__sub">Patreon tier</span>
              </span>
              <input
                className="input patreon-tiers__input"
                aria-label={`Badge label for ${r.title}`}
                placeholder="Patron"
                maxLength={MAX_LABEL}
                value={r.badge_label}
                onChange={(e) =>
                  setRows((prev) => prev.map((row) => (row.tier_id === r.tier_id ? { ...row, badge_label: e.target.value } : row)))
                }
              />
            </li>
          ))}
        </ul>
      )}
      <p className="settings-note">Top of the list wins when someone is in more than one tier. Free members never get a badge.</p>
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
    </FormDialog>
  )
}

function Connected({ data, zone }: { data: Campaign; zone: string }) {
  const lastSync = data.last_synced_at ? ago(data.last_synced_at) : 'not yet'
  return (
    <dl className="patreon-facts">
      <dt>Campaign</dt>
      <dd>{data.campaign_name ?? 'Your campaign'}</dd>
      <dt>Connected by</dt>
      <dd>{[data.connected_by, data.connected_at ? formatDay(data.connected_at, zone) : null].filter(Boolean).join(' · ') || '—'}</dd>
      <dt>Last sync</dt>
      <dd>
        {`${lastSync} · ${plural(data.patrons, 'patron')}, ${data.linked_members} linked here`}
        {data.sync_pending && <span className="settings-row__sub">A full sync runs within five minutes.</span>}
      </dd>
      <dt>Badge labels</dt>
      <dd>{labelSummary(data.tiers ?? [])}</dd>
    </dl>
  )
}

/** Settings → Integrations: the community's Patreon campaign, which gives linked patrons their badge. */
export function PatreonCard() {
  const { data, error, reload } = useAdminQuery('settings/integrations/patreon', () =>
    api.GET('/api/settings/integrations/patreon').then(unwrap),
  )
  const [confirming, setConfirming] = useState(false)
  const [labelling, setLabelling] = useState(false)
  const act = useAction(reload)
  const zone = effectiveZone(null)
  useReloadOnReturn(reload)

  const connect = () =>
    void act.run(async () => {
      await openOutsideSignIn(async () => unwrap(await api.POST('/api/settings/integrations/patreon/connect')).authorize_url)
      return 'Finish on Patreon, then come back here.'
    })

  const header = data?.connected ? (
    <span className="patreon-head">
      <Chip tone="ok">
        <Icon name="check" size={14} />
        Connected
      </Chip>
      <RowMenu
        label="Patreon actions"
        items={[{ label: 'Disconnect…', icon: 'link_off', destructive: true, onSelect: () => setConfirming(true) }]}
      />
    </span>
  ) : undefined

  return (
    <ListCard title="Patreon" icon="favorite" subtitle="Patron badges for members who back your campaign." action={header}>
      {error && !data ? (
        <div className="settings-card__body">
          <LoadError error={error} onRetry={reload} />
        </div>
      ) : !data ? (
        <div className="settings-card__body">
          <Spinner label="Loading Patreon" />
        </div>
      ) : !data.configured && !data.connected ? (
        <div className="settings-card__body">
          <p className="settings-note">Patreon sign-in isn't set up on this server. Ask the platform admin to turn it on.</p>
        </div>
      ) : !data.connected ? (
        <div className="settings-list__row">
          <Icon name="link_off" size={20} className="settings-status--muted" />
          <span className="settings-row__text">
            <span className="settings-row__title">Not connected</span>
            <span className="settings-row__sub">Sign in to Patreon as the campaign's creator. It opens in a new tab.</span>
          </span>
          <Button variant="outline" icon="open_in_new" busy={act.busy} busyLabel="Opening…" onClick={connect}>
            Connect
          </Button>
        </div>
      ) : (
        <>
          <div className="settings-card__body">
            <Connected data={data} zone={zone} />
            {data.last_sync_error && (
              <p className="settings-warning" role="status">
                {data.last_sync_error}
              </p>
            )}
          </div>
          <div className="settings-card__body patreon-actions">
            <Button variant="outline" icon="edit" onClick={() => setLabelling(true)}>
              Badge labels…
            </Button>
            <Button
              variant="ghost"
              icon="sync"
              busy={act.busy}
              busyLabel="Queuing…"
              disabled={data.sync_pending}
              onClick={() =>
                void act.run(async () => {
                  unwrap(await api.POST('/api/settings/integrations/patreon/sync'))
                  return 'Sync queued. It runs within five minutes.'
                })
              }
            >
              Sync now
            </Button>
          </div>
          <div className="settings-card__body">
            <p className="settings-note">
              Members get the badge after linking Patreon on Me → Accounts. Wizzrobe never sees pledge amounts.
            </p>
          </div>
        </>
      )}
      <Notes note={act.note} failure={!confirming ? act.failure : null} />
      {labelling && data && (
        <TierLabelsDialog
          tiers={data.tiers ?? []}
          onClose={() => setLabelling(false)}
          onSaved={() => {
            setLabelling(false)
            reload()
          }}
        />
      )}
      <DisconnectDialog
        service="Patreon"
        open={confirming}
        onOpenChange={(open) => {
          setConfirming(open)
          act.clearFailure()
        }}
        description={`Every patron badge here goes away${data ? ` (${plural(data.patrons, 'badge')})` : ''}, along with the badge labels. Members stay linked, so their badges come back if you connect again.`}
        busy={act.busy}
        failure={act.failure}
        onConfirm={() =>
          void act
            .run(async () => {
              await noContent(await api.DELETE('/api/settings/integrations/patreon'))
              return 'Patreon disconnected.'
            })
            .then((ok) => ok && setConfirming(false))
        }
      />
    </ListCard>
  )
}

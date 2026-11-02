import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../../api/client'
import { useQuery } from '../../../api/useQuery'
import { Chip, FormDialog, RowMenu, Spinner } from '../../../components'
import { LoadError, useAdmin } from '../../kit'

type App = Schemas['ConnectedApp']

const members = (n: number) => `${n} ${n === 1 ? 'member' : 'members'}`

export function ConnectedApps() {
  const { adminBase } = useAdmin()
  const { data, error, reload } = useQuery('settings-apps', () => api.GET('/api/settings/apps').then(unwrap))
  const [blocking, setBlocking] = useState<App | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)

  if (error && !data) return <LoadError error={error} onRetry={reload} />
  if (!data) return <Spinner label="Loading connected apps" />

  async function run(action: () => Promise<string>) {
    setBusy(true)
    setFailure(null)
    try {
      setNote(await action())
      setBlocking(null)
      reload()
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const block = (app: App) =>
    run(async () => {
      const { disconnected } = await api
        .POST('/api/settings/apps/{app_id}/block', { params: { path: { app_id: app.app_id } } })
        .then(unwrap)
      return `${app.name} blocked, ${members(disconnected)} disconnected.`
    })
  const unblock = (app: App) =>
    run(async () => {
      const r = await api.DELETE('/api/settings/apps/{app_id}/block', { params: { path: { app_id: app.app_id } } })
      if (!r.response.ok) unwrap(r)
      return `${app.name} unblocked. Members can connect it again.`
    })

  return (
    <>
      <section className="panel settings-card" aria-labelledby="apps-title">
        <header className="settings-card__head">
          <div className="settings-card__heading">
            <h2 className="settings-card__title" id="apps-title">
              Connected apps
            </h2>
            <p className="settings-card__sub">
              Apps members of this community have connected. Blocking one disconnects everyone here and stops new connections.
            </p>
          </div>
        </header>
        {data.length === 0 ? (
          <div className="settings-card__body">
            <p className="settings-note">No member has connected an app yet.</p>
          </div>
        ) : (
          <ul className="settings-list">
            {data.map((app) => (
              <li key={app.app_id} className="settings-list__row">
                <span className="settings-row__text">
                  <span className="settings-row__title">
                    {app.name} <Chip tone="pending">Unverified</Chip>
                    {app.blocked && <Chip tone="cancelled">Blocked here</Chip>}
                    {app.disabled && <Chip tone="neutral">Disabled by Wizzrobe</Chip>}
                  </span>
                  <span className="settings-row__sub">
                    by {app.owner_name} · {members(app.members)}
                    {app.homepage_host ? ` · ${app.homepage_host}` : ''}
                  </span>
                </span>
                <RowMenu
                  label={`Actions for ${app.name}`}
                  items={
                    app.blocked
                      ? [{ label: 'Unblock', icon: 'check_circle', onSelect: () => void unblock(app) }]
                      : [{ label: 'Block here…', icon: 'block', destructive: true, onSelect: () => setBlocking(app) }]
                  }
                />
              </li>
            ))}
          </ul>
        )}
      </section>
      <p className="settings-note">
        To stop every app in this community, turn off Third-party apps under{' '}
        <Link to={`${adminBase}/settings/features`}>Features</Link>.
      </p>
      {note && (
        <p className="settings-saved" role="status">
          {note}
        </p>
      )}
      {failure && !blocking && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
      <FormDialog
        open={blocking !== null}
        onOpenChange={(open) => !open && setBlocking(null)}
        title={blocking ? `Block ${blocking.name} here?` : 'Block app'}
        description={
          blocking
            ? `${members(blocking.members)} are disconnected from it now, and nobody here can connect it again until you unblock it. Other communities aren't affected.`
            : undefined
        }
        primaryLabel="Block"
        destructive
        busy={busy}
        busyLabel="Blocking…"
        onPrimary={() => blocking && void block(blocking)}
        size="sm"
      >
        {failure && (
          <p className="form-error" role="alert">
            {failure}
          </p>
        )}
      </FormDialog>
    </>
  )
}

import { useCallback, useState } from 'react'
import { ApiError, api, unwrap, type Schemas } from '../../api/client'
import { useCommunity } from '../../community/CommunityProvider'
import { Button, Chip, FormDialog, Icon, Panel, PanelRow, PatronBadge, RowMenu } from '../../components'
import { openOutsideSignIn, useReloadOnReturn } from '../../lib/outsideSignIn'
import { done, message, useSection } from '../data'

type Mine = Schemas['MyPatreonResponse']

/** Your Patreon link, on Me → Accounts. Absent when the community has Patreon turned off. */
export function PatreonPanel() {
  const { community } = useCommunity()
  const { data, reload } = useSection<Mine | null>('patreon', async () => {
    try {
      return unwrap(await api.GET('/api/me/patreon'))
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null
      throw err
    }
  })
  const [unlinking, setUnlinking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const refresh = useCallback(() => reload(), [reload])
  useReloadOnReturn(refresh)

  if (!data || (!data.configured && !data.linked)) return null

  async function run(action: () => Promise<string | null>) {
    setBusy(true)
    setError(null)
    setNote(null)
    try {
      setNote(await action())
      reload()
      return true
    } catch (err) {
      setError(message(err))
      return false
    } finally {
      setBusy(false)
    }
  }

  const link = () =>
    void run(async () => {
      await openOutsideSignIn(async () => unwrap(await api.POST('/api/me/patreon/link')).authorize_url)
      return 'Finish on Patreon, then come back here.'
    })

  const title = (
    <span className="me-patreon__title">
      <Icon name="favorite" size={20} />
      Patreon
      {data.linked && (
        <Chip tone="ok">
          <Icon name="check" size={14} />
          Linked
        </Chip>
      )}
    </span>
  )
  const actions = data.linked ? (
    <RowMenu
      label="Patreon actions"
      items={[{ label: 'Unlink…', icon: 'link_off', destructive: true, onSelect: () => setUnlinking(true) }]}
    />
  ) : undefined
  const elsewhere = (data.communities ?? []).filter((c) => c.community !== community.name)

  return (
    <Panel title={title} actions={actions}>
      {!data.linked ? (
        <PanelRow>
          <div className="me-row">
            <span className="me-row__text">
              <span className="me-row__sub">
                {data.connected_here
                  ? `Back ${community.name} on Patreon? Link your account and a patron badge shows beside your name here.`
                  : `${community.name} hasn't connected Patreon yet. Link now and your badge shows once it does.`}
              </span>
            </span>
            <Button variant="outline" icon="open_in_new" busy={busy} busyLabel="Opening…" onClick={link}>
              Link
            </Button>
          </div>
        </PanelRow>
      ) : (
        <>
          <PanelRow>
            <dl className="me-patreon__facts">
              <dt>Linked as</dt>
              <dd>{data.username ?? 'Your Patreon account'}</dd>
              <dt>Your badges</dt>
              <dd>
                <span>
                  {community.name}{' '}
                  {data.here.is_patron && data.here.badge_label ? (
                    <PatronBadge label={data.here.badge_label} />
                  ) : (
                    <span className="me-row__sub">
                      · {data.connected_here ? 'not a patron' : "hasn't connected Patreon"}
                    </span>
                  )}
                </span>
                {elsewhere.map((c) => (
                  <span key={c.community} className="me-row__sub">
                    {c.community} · {c.is_patron ? (c.badge_label ?? 'Patron') : 'not a patron'}
                  </span>
                ))}
              </dd>
            </dl>
          </PanelRow>
          <PanelRow>
            <label className="me-row me-patreon__switch">
              <span className="me-row__text">
                <span className="me-row__title">Show my badge</span>
                <span className="me-row__sub">Hide it and other members won't see the badge. Perks still work.</span>
              </span>
              <input
                type="checkbox"
                role="switch"
                className="switch"
                checked={!data.badge_hidden}
                disabled={busy}
                onChange={(e) => {
                  const hidden = !e.target.checked
                  void run(async () => {
                    unwrap(await api.PATCH('/api/me/patreon', { body: { badge_hidden: hidden } }))
                    return hidden ? 'Your badge is hidden.' : 'Your badge shows again.'
                  })
                }}
              />
            </label>
          </PanelRow>
        </>
      )}
      {(note || (error && !unlinking)) && (
        <PanelRow>
          {note && (
            <p className="me-saved" role="status">
              {note}
            </p>
          )}
          {error && !unlinking && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
        </PanelRow>
      )}
      {unlinking && (
        <FormDialog
          open
          onOpenChange={(o) => {
            if (!o) {
              setUnlinking(false)
              setError(null)
            }
          }}
          size="sm"
          title="Unlink Patreon?"
          description="Your patron badge goes away in every community. You can link again any time."
          primaryLabel="Unlink"
          destructive
          busy={busy}
          busyLabel="Unlinking…"
          onPrimary={() =>
            void run(async () => {
              done(await api.DELETE('/api/me/patreon'))
              return 'Patreon unlinked.'
            }).then((ok) => ok && setUnlinking(false))
          }
        >
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
        </FormDialog>
      )}
    </Panel>
  )
}

import { useEffect, useState } from 'react'
import { api, unwrap } from '../../api/client'
import { AUTH_ORIGIN, API_BASE } from '../../auth/config'
import { Banner, Button, Chip, EmptyState, FormDialog, Icon, Panel, PanelRow, Spinner } from '../../components'
import { done, message, useSection } from '../data'
import { useProfile } from '../MeLayout'
import { PatreonPanel } from './Patreon'

/** The website's origin: linking an outside account still runs through its sign-in pages. */
function site(path: string): string {
  return `${AUTH_ORIGIN || API_BASE || window.location.origin}${path}`
}

function day(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : ''
}

export function Accounts() {
  const { reload: reloadProfile } = useProfile()
  const { data, reload } = useSection('accounts', () => api.GET('/api/me/accounts').then(unwrap))
  const [confirm, setConfirm] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const back = () => {
      if (document.visibilityState === 'visible') {
        reload()
        reloadProfile()
      }
    }
    document.addEventListener('visibilitychange', back)
    return () => document.removeEventListener('visibilitychange', back)
  }, [reload, reloadProfile])

  if (!data) return <Spinner label="Loading your accounts" />
  const target = data.find((a) => a.key === confirm)

  async function unlink() {
    if (!target) return
    setBusy(true)
    setError(null)
    try {
      done(await api.DELETE('/api/me/accounts/{provider}', { params: { path: { provider: target.key as 'challonge' } } }))
      setConfirm(null)
      reload()
      reloadProfile()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="me-stack">
      <PatreonPanel />
      <Panel title="Connected accounts">
        <PanelRow>
          <p className="me-note">Link outside accounts so we can find your bracket and race matches for scheduling.</p>
        </PanelRow>
        {data.map((a) => (
          <PanelRow key={a.key}>
            <div className="me-row">
              <Icon name={a.linked ? 'check_circle' : 'link'} />
              <span className="me-row__text">
                <span className="me-row__title">{a.title}</span>
                <span className="me-row__sub">
                  {a.linked ? `Linked as ${a.username}${a.linked_at ? ` on ${day(a.linked_at)}` : ''}` : a.description}
                </span>
              </span>
              {a.linked ? (
                <Button variant="ghost" icon="link_off" aria-label={`Unlink ${a.title}`} onClick={() => setConfirm(a.key)}>
                  Unlink
                </Button>
              ) : (
                <Button asChild variant="outline">
                  <a href={site(a.link_path)} target="_blank" rel="noopener noreferrer" aria-label={`Link ${a.title} (opens the website)`}>
                    Link
                    <Icon name="open_in_new" size={18} />
                  </a>
                </Button>
              )}
            </div>
          </PanelRow>
        ))}
      </Panel>
      {data.some((a) => !a.linked && a.sign_in_first_url) && (
        <p className="me-note">Linking Challonge? Sign in to Challonge in the same browser first, or it can't find your account.</p>
      )}
      <p className="me-note">Linking opens the website in a new tab. Come back here when it's done; the row updates when you return.</p>
      {target && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setConfirm(null)}
          size="sm"
          title={`Unlink ${target.title}?`}
          description={target.unlink_warning ?? undefined}
          primaryLabel="Unlink"
          destructive
          busy={busy}
          busyLabel="Unlinking…"
          onPrimary={() => void unlink()}
        >
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
        </FormDialog>
      )}
    </div>
  )
}

export function StaffAlerts() {
  const { data, reload } = useSection('staff-alerts', () => api.GET('/api/me/staff-alerts').then(unwrap))
  const [error, setError] = useState<string | null>(null)
  if (!data) return <Spinner label="Loading your alerts" />
  return (
    <div className="me-stack">
      {data.duty_position && (
        <Panel title="On duty">
          <PanelRow>
            <div className="me-row">
              <Icon name="campaign" />
              <span className="me-row__text">
                <span className="me-row__title">{data.duty_position}</span>
                <span className="me-row__sub">
                  {data.on_duty_now
                    ? "You're on duty now: player comments and reschedule requests come to you."
                    : data.next_duty_start
                      ? `Your next duty shift starts ${new Date(data.next_duty_start).toLocaleString()}.`
                      : 'Alerts reach you while your duty shift runs.'}
                </span>
              </span>
              {data.on_duty_now && <Chip tone="live">On now</Chip>}
            </div>
          </PanelRow>
        </Panel>
      )}
      {data.tournaments.length > 0 && (
        <Panel title="Tournaments you run">
          <PanelRow>
            <p className="me-note">Player comments and reschedule requests on these come to you too. Turn one off to stop them.</p>
          </PanelRow>
          {data.tournaments.map((t) => (
            <PanelRow key={t.id}>
              <div className="me-row">
                <label className="me-row__text" htmlFor={`alert-${t.id}`}>
                  <span className="me-row__title">{t.name}</span>
                </label>
                <input
                  id={`alert-${t.id}`}
                  type="checkbox"
                  role="switch"
                  className="switch"
                  checked={t.on}
                  onChange={(e) => {
                    setError(null)
                    api
                      .PUT('/api/me/staff-alerts/{tournament_id}', {
                        params: { path: { tournament_id: t.id } },
                        body: { on: e.target.checked },
                      })
                      .then(unwrap)
                      .then(reload)
                      .catch((err: unknown) => setError(message(err)))
                  }}
                />
              </div>
            </PanelRow>
          ))}
        </Panel>
      )}
      {error && <Banner tone="error">{error}</Banner>}
    </div>
  )
}

export function Equipment() {
  const { data } = useSection('equipment', () => api.GET('/api/equipment/me/checkouts').then(unwrap))
  if (!data) return <Spinner label="Loading your equipment" />
  return (
    <Panel title="Checked out to you">
      {data.length === 0 ? (
        <PanelRow>
          <EmptyState icon="inventory_2" title="Nothing checked out">
            Anything you borrow from the equipment desk shows here until you hand it back.
          </EmptyState>
        </PanelRow>
      ) : (
        data.map((loan) => (
          <PanelRow key={loan.loan_id}>
            <div className="me-row">
              <Icon name="inventory_2" />
              <span className="me-row__text">
                <span className="me-row__title">
                  #{loan.asset_number} {loan.name}
                </span>
                {loan.checked_out_at && <span className="me-row__sub">Since {new Date(loan.checked_out_at).toLocaleString()}</span>}
              </span>
            </div>
          </PanelRow>
        ))
      )}
    </Panel>
  )
}

const CATEGORY: Record<string, string> = { bug: 'Problem', suggestion: 'Idea', praise: 'Praise', other: 'Other' }

export function Feedback() {
  const { data } = useSection('feedback', () => api.GET('/api/feedback/mine').then(unwrap))
  if (!data) return <Spinner label="Loading your feedback" />
  return (
    <Panel title="What you've sent">
      {data.length === 0 ? (
        <PanelRow>
          <EmptyState icon="feedback" title="No feedback yet">
            Notes you send from the app show here, with whether staff have read them.
          </EmptyState>
        </PanelRow>
      ) : (
        data.map((f) => (
          <PanelRow key={f.id}>
            <div className="me-row">
              <span className="me-row__text">
                <span className="me-row__title">{CATEGORY[f.category] ?? f.category}</span>
                <span className="me-row__sub" style={{ whiteSpace: 'pre-wrap' }}>
                  {f.message}
                </span>
                <span className="me-row__sub">{day(f.created_at)}</span>
              </span>
              {f.status === 'reviewed' ? <Chip tone="ok">Read by staff</Chip> : <Chip tone="pending">Not read yet</Chip>}
            </div>
          </PanelRow>
        ))
      )}
    </Panel>
  )
}

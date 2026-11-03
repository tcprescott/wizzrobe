import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { Banner, Button, EmptyState, Icon, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { message, useSection } from '../me/data'
import { useEventLine } from './data'
import './tickets.css'

/** A claim link someone sent: who it's from, which badge, and one button to take it. */
export function ClaimScreen() {
  const { token = '' } = useParams()
  const { base } = useCommunity()
  const line = useEventLine()
  const navigate = useNavigate()
  const preview = useSection(`tickets/claim/${token}`, () =>
    api.GET('/api/tickets/claims/{token}', { params: { path: { token } } }).then(unwrap),
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function claim() {
    setBusy(true)
    setError(null)
    try {
      await api.POST('/api/tickets/claims/{token}', { params: { path: { token } } }).then(unwrap)
      navigate(`${base}/me/tickets`, { replace: true })
    } catch (err) {
      setError(message(err))
      setBusy(false)
    }
  }

  if (preview.error && !preview.data) {
    return (
      <div className="tk-claim">
        <EmptyState icon="local_activity" title="That link doesn't work">
          {preview.error.message} Ask whoever sent it for a new one, or <Link to={`${base}/event/badges`}>buy a badge</Link>.
        </EmptyState>
      </div>
    )
  }
  if (!preview.data) return <Spinner label="Opening the badge" />
  const p = preview.data

  return (
    <div className="tk-claim">
      <h1 className="tk-claim__title">{p.buyer_name} sent you a badge</h1>
      <p className="tk-claim__line">{line}</p>
      <section className="panel tk-claim__card" aria-label={p.ticket_type_name}>
        <div className="tk-claim__body">
          <span className="tk-claim__mark" aria-hidden="true">
            <Icon name="local_activity" size={36} />
          </span>
          <span className="tk-badge__text">
            <strong className="tk-badge__name">{p.ticket_type_name}</strong>
            <span className="tk-sale__sub">
              {p.claimable ? 'Claim it and it goes on your account. Your check-in QR shows up under Me → Tickets.' : p.reason}
            </span>
          </span>
        </div>
        {error && <Banner tone="error">{error}</Banner>}
        <div className="tk-claim__actions">
          <Button variant="ghost" onClick={() => navigate(`${base}/event`)}>
            {p.claimable ? 'Not me' : 'Back to the event'}
          </Button>
          {p.claimable && (
            <Button icon="check" busy={busy} busyLabel="Claiming…" onClick={() => void claim()}>
              Claim badge
            </Button>
          )}
        </div>
      </section>
      <p className="tk-fine tk-fine--center">
        A badge can be claimed once. Ask {p.buyer_name} for a new link if this one's already used.
      </p>
    </div>
  )
}

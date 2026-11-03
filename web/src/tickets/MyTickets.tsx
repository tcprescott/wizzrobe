import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { Banner, Button, Chip, CountBadge, EmptyState, Panel, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { effectiveZone, formatDay } from '../lib/time'
import { useSection } from '../me/data'
import { useEventLine } from './data'
import './tickets.css'

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** Me → Tickets: your own badge with its check-in QR, and a claim link for each extra you bought. */
export function MyTickets() {
  const { base } = useCommunity()
  const line = useEventLine()
  const zone = effectiveZone(null)
  const { data, error, reload } = useSection('tickets/mine', () => api.GET('/api/me/tickets').then(unwrap))
  const [copied, setCopied] = useState<number | null>(null)
  const [copyFailed, setCopyFailed] = useState<string | null>(null)

  if (error && !data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
        {error.message}
      </Banner>
    )
  }
  if (!data) return <Spinner label="Loading your badges" />

  const mine = data.own.filter((b) => b.status === 'valid')
  const refunded = data.own.filter((b) => b.status === 'refunded')
  const waiting = data.extras.filter((e) => !e.badge.owner_id).length

  async function copyLink(id: number, url: string) {
    if (await copy(url)) {
      setCopied(id)
      setCopyFailed(null)
      window.setTimeout(() => setCopied((c) => (c === id ? null : c)), 4000)
    } else {
      setCopyFailed(url)
    }
  }

  return (
    <div className="tk-mine">
      <p className="tk-mine__line">{line}</p>
      {mine.length === 0 && refunded.length === 0 && data.extras.length === 0 && (
        <EmptyState icon="local_activity" title="No badge yet">
          <Link to={`${base}/event/badges`}>Buy badges</Link> and yours shows up here with its check-in QR.
        </EmptyState>
      )}
      {mine.map((b) => (
        <section key={b.id} className="panel tk-badge" aria-label={b.ticket_type_name}>
          {b.qr && <img className="tk-badge__qr" src={b.qr} alt={`Check-in QR for your ${b.ticket_type_name}, code ${b.code}`} />}
          <span className="tk-badge__text">
            <strong className="tk-badge__name">{b.ticket_type_name}</strong>
            <span className="tk-sale__sub">
              {b.owner_name ? `${b.owner_name} (you)` : 'Yours'} · code <span className="tk-code">{b.code}</span>
            </span>
            <span className="tk-badge__chips">
              <Chip tone="ok">Paid</Chip>
              <Chip tone="neutral">Show this QR at the desk</Chip>
            </span>
          </span>
        </section>
      ))}
      {refunded.map((b) => (
        <section key={b.id} className="panel tk-badge" aria-label={`${b.ticket_type_name}, refunded`}>
          <span className="tk-badge__text">
            <strong className="tk-badge__name">{b.ticket_type_name}</strong>
            <span className="tk-badge__chips">
              <Chip tone="neutral">Refunded</Chip>
            </span>
            <span className="tk-sale__sub">This badge no longer checks in.</span>
          </span>
        </section>
      ))}

      {data.extras.length > 0 && (
        <Panel
          title={
            <>
              Extra badges <CountBadge count={waiting} label={`${waiting} not claimed yet`} />
            </>
          }
          actions={<span className="tk-panel-note">Send each link to the person it's for</span>}
        >
          {copyFailed && (
            <Banner tone="warning">
              Couldn't copy. Here's the link to send: <span className="tk-code">{copyFailed}</span>
            </Banner>
          )}
          <ul className="tk-list" aria-label="Extra badges">
            {data.extras.map(({ badge, claim_url }) => (
              <li key={badge.id} className="panel__row tk-row">
                <span className="tk-row__text">
                  <strong>{badge.ticket_type_name}</strong>
                  <span className="tk-row__sub">
                    {badge.owner_id
                      ? `Claimed by ${badge.owner_name ?? 'someone'}${badge.claimed_at ? ` · ${formatDay(badge.claimed_at, zone)}` : ''}`
                      : 'Not claimed yet'}
                  </span>
                </span>
                {badge.owner_id ? (
                  <Chip tone="ok">Claimed</Chip>
                ) : claim_url ? (
                  <Button variant="outline" size="sm" icon={copied === badge.id ? 'check' : 'content_copy'} onClick={() => void copyLink(badge.id, claim_url)}>
                    {copied === badge.id ? 'Copied' : 'Copy link'}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        </Panel>
      )}
      <p className="tk-fine">
        Need another? <Link to={`${base}/event/badges`}>Buy badges</Link>
      </p>
    </div>
  )
}

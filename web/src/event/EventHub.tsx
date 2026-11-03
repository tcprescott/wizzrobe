import { useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Banner, Button, EmptyState, Icon, PageHeader, Panel, Spinner } from '../components'
import type { Schemas } from '../api/client'
import { useCommunity } from '../community/CommunityProvider'
import { money } from '../lib/money'
import { effectiveZone } from '../lib/time'
import { useFlash } from '../player/HomeScreen'
import { useEventInfo } from '../reading/data'
import { useHub } from './data'
import { RunSheetRow, StageCard, TournamentCardView } from './parts'
import './event.css'

/** Buying a badge, or where yours is once you have one. */
function BadgesCard({ card, base }: { card: Schemas['BadgesCardResponse']; base: string }) {
  const selling = card.on_sale > 0
  const sub = [
    selling && card.from_cents != null ? `From ${money(card.from_cents, card.currency ?? 'usd')}` : null,
    card.owned ? (card.owned === 1 ? 'You have yours' : `You hold ${card.owned}`) : null,
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <Link to={selling ? `${base}/event/badges` : `${base}/me/tickets`} className="hub-info">
      <Icon name="local_activity" size={22} />
      <span className="hub-info__text">
        <strong>{selling ? 'Badges' : 'Your badge'}</strong>
        <span>{sub || 'Show its QR at the check-in desk'}</span>
      </span>
      <Icon name="chevron_right" size={20} />
    </Link>
  )
}

/** The Event tab, layout B: what's on each stage now, what's next anywhere, and each tournament's bracket. */
export function EventHub() {
  const { event, base } = useCommunity()
  const { data, error, loading, reload } = useHub()
  const info = useEventInfo()
  const hasInfo = !!info.data && (info.data.articles.length > 0 || !!info.data.guide)
  const [flash, setFlash] = useFlash()
  const done = useCallback(
    (msg: string) => {
      setFlash(msg)
      reload()
    },
    [reload, setFlash],
  )
  const zone = effectiveZone(data?.zone)

  return (
    <>
      <PageHeader title={event?.name ?? 'Event'} lede="What's on now, what's next and every bracket." />
      {flash && <Banner tone="info">{flash}</Banner>}
      {error && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading the event" />}
      {hasInfo && (
        <Link to={`${base}/event-info`} className="hub-info">
          <Icon name="menu_book" size={22} />
          <span className="hub-info__text">
            <strong>Event information</strong>
            <span>The organisers' handbook for this event</span>
          </span>
          <Icon name="chevron_right" size={20} />
        </Link>
      )}
      {data?.badges && <BadgesCard card={data.badges} base={base} />}
      {data && (
        <>
          <section aria-labelledby="hub-now">
            <div className="hub-sect">
              <h2 id="hub-now">On now</h2>
              <Link to={`${base}/event/watch`}>
                Watch
                <Icon name="chevron_right" size={18} />
              </Link>
            </div>
            {data.stages.length === 0 ? (
              <Panel>
                <EmptyState icon="videocam" title="No stages yet">
                  Stage cards show here once staff set up the event's stages.
                </EmptyState>
              </Panel>
            ) : (
              <div className="stage-cards" role="region" aria-label="Stages" tabIndex={0}>
                {data.stages.map((s) => (
                  <StageCard key={s.id} stage={s} zone={zone} />
                ))}
              </div>
            )}
          </section>
          <section aria-labelledby="hub-next">
            <div className="hub-sect">
              <h2 id="hub-next">Up next</h2>
              <Link to={`${base}/event/schedule`}>
                Full schedule
                <Icon name="chevron_right" size={18} />
              </Link>
            </div>
            <section className="panel">
              {data.up_next.length === 0 ? (
                <EmptyState icon="calendar_month" title="Nothing else booked today" />
              ) : (
                data.up_next.map((row) => <RunSheetRow key={row.match_id} row={row} zone={zone} onDone={done} />)
              )}
            </section>
          </section>
          {data.tournaments.length > 0 && (
            <section aria-labelledby="hub-t">
              <div className="hub-sect">
                <h2 id="hub-t">Tournaments</h2>
              </div>
              <div className="t-cards">
                {data.tournaments.map((t) => (
                  <TournamentCardView key={t.id} card={t} />
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </>
  )
}

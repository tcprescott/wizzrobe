import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Banner, Button, CountBadge, EmptyState, PageHeader, Panel, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { effectiveZone } from '../lib/time'
import { useMe } from '../screens/useMe'
import { useHome, useNow } from './data'
import { NeedCard, NextUpCard, ScheduleRow } from './parts'
import './player.css'

/** A short "done" line at the top of a screen after an action, cleared after a few seconds. */
export function useFlash(): [string | null, (message: string) => void] {
  const [message, setMessage] = useState<string | null>(null)
  useEffect(() => {
    if (!message) return
    const id = window.setTimeout(() => setMessage(null), 6000)
    return () => window.clearTimeout(id)
  }, [message])
  return [message, setMessage]
}

/**
 * Home, layout B ("next up first"): the player's next match leads, the
 * things waiting on them sit beside it as cards, then the rest of today.
 */
export function HomeScreen() {
  const { community, event, base } = useCommunity()
  const { me } = useMe()
  const { data, error, loading, reload } = useHome()
  const now = useNow()
  const [flash, setFlash] = useFlash()
  const done = useCallback(
    (message: string) => {
      setFlash(message)
      reload()
    },
    [reload, setFlash],
  )

  const name = me?.display_name || me?.username
  const zone = effectiveZone(data?.zone)
  const needs = data?.needs_you ?? []
  const lede = [community.name, event?.name].filter(Boolean).join(' · ')

  return (
    <>
      <PageHeader title={name ? `Hi, ${name}` : 'Home'} lede={lede} />
      {flash && <Banner tone="info">{flash}</Banner>}
      {error && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading your day" />}
      {data && (
        <div className="home-grid">
          <div className="home-grid__next">
            {data.next_up ? (
              <NextUpCard entry={data.next_up} zone={zone} now={now} onDone={done} />
            ) : (
              <Panel>
                <EmptyState icon="event_available" title="No matches coming up">
                  When a match is booked for you it shows here, with a countdown and where to go.
                </EmptyState>
              </Panel>
            )}
          </div>
          <div className="home-grid__today">
            {data.today.length > 0 && (
              <Panel title={data.next_up ? 'Later today' : 'Today'}>
                {data.today.map((entry) => (
                  <ScheduleRow key={`${entry.kind}-${entry.match_id ?? entry.assignment_id}`} entry={entry} zone={zone} onDone={done} />
                ))}
              </Panel>
            )}
            <p className="home-more">
              <Link to={`${base}/schedule`}>See your whole schedule</Link>
            </p>
          </div>
          <section className="home-grid__side" aria-labelledby="needs-title">
            <h2 className="side-title" id="needs-title">
              Needs you <CountBadge count={needs.length} label={`${needs.length} waiting on you`} />
            </h2>
            {needs.length === 0 ? (
              <p className="side-empty">Nothing's waiting on you.</p>
            ) : (
              needs.map((need) => (
                <NeedCard
                  key={`${need.kind}-${need.request_id ?? need.match_id ?? `${need.bookable?.source}-${need.bookable?.key}-${need.bookable?.round_label}`}`}
                  need={need}
                  zone={zone}
                  onDone={done}
                />
              ))
            )}
          </section>
        </div>
      )}
    </>
  )
}

import { useState } from 'react'
import { Banner, Button, EmptyState, PageHeader, Panel, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { dayKey, dayLabel, effectiveZone } from '../lib/time'
import { BackToEvent } from './BackLink'
import { useWatch, type StreamMatch } from './data'
import { StageCard, StreamRow, TournamentCardView } from './parts'
import './event.css'

function byDay(matches: StreamMatch[], zone: string): [string, StreamMatch[]][] {
  const days = new Map<string, StreamMatch[]>()
  for (const m of matches) {
    const key = m.scheduled_at ? dayKey(m.scheduled_at, zone) : 'tbd'
    days.set(key, [...(days.get(key) ?? []), m])
  }
  return [...days.entries()]
}

/** The streams: a player for the picked stage, every stage's now and next, then what's coming up on stream. */
export function WatchScreen() {
  const { event } = useCommunity()
  const { data, error, loading, reload } = useWatch()
  const streamed = (data?.stages ?? []).filter((s) => s.channel)
  const [picked, setPicked] = useState<number | null>(null)
  const zone = effectiveZone(data?.zone)
  const channel = streamed.find((s) => s.id === picked) ?? streamed.find((s) => s.on_now) ?? streamed[0]

  return (
    <>
      <BackToEvent />
      <PageHeader title="Watch" lede={`Live from ${event?.name ?? 'the event'}.`} />
      {error && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading the streams" />}
      {data && (
        <div className="watch-grid">
          <div>
            {channel ? (
              <>
                <div className="watch-player">
                  <iframe
                    title={`${channel.name} stream (${channel.channel_name})`}
                    src={`https://player.twitch.tv/?channel=${encodeURIComponent(channel.channel!)}&parent=${window.location.hostname}&muted=true`}
                    allow="autoplay; fullscreen"
                    allowFullScreen
                  />
                </div>
                {streamed.length > 1 && (
                  <div className="filter-chips watch-channels" role="radiogroup" aria-label="Stream">
                    {streamed.map((s) => (
                      <span key={s.id}>
                        <input
                          type="radio"
                          id={`ch-${s.id}`}
                          name="channel"
                          checked={channel.id === s.id}
                          onChange={() => setPicked(s.id)}
                        />
                        <label htmlFor={`ch-${s.id}`}>{s.name}</label>
                      </span>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <Panel>
                <EmptyState icon="live_tv" title="No streams set up">
                  Streams show here once staff add a Twitch link to a stage.
                </EmptyState>
              </Panel>
            )}
            {data.stages.length > 0 && (
              <div className="stage-cards stage-cards--watch" role="region" aria-label="Stages" tabIndex={0}>
                {data.stages.map((s) => (
                  <StageCard key={s.id} stage={s} zone={zone} />
                ))}
              </div>
            )}
          </div>
          <div>
            <Panel title="Coming up on stream">
              {data.coming_up.length === 0 ? (
                <EmptyState icon="live_tv" title="Nothing else booked on stream this week" />
              ) : (
                byDay(data.coming_up, zone).map(([key, matches]) => (
                  <div key={key} role="group" aria-labelledby={`cu-${key}`}>
                    <h3 className="sched-day" id={`cu-${key}`}>
                      {matches[0]?.scheduled_at ? dayLabel(matches[0].scheduled_at, zone) : 'Time to be set'}
                    </h3>
                    {matches.map((m) => (
                      <StreamRow key={m.match_id} match={m} zone={zone} />
                    ))}
                  </div>
                ))
              )}
            </Panel>
            {data.tournaments.length > 0 && (
              <section aria-labelledby="watch-t">
                <h2 className="hub-sect__h" id="watch-t">
                  Tournaments
                </h2>
                <div className="t-cards t-cards--stack">
                  {data.tournaments.map((t) => (
                    <TournamentCardView key={t.id} card={t} />
                  ))}
                </div>
              </section>
            )}
          </div>
        </div>
      )}
    </>
  )
}

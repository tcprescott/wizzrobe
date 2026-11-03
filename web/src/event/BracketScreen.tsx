import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { type Schemas } from '../api/client'
import { Banner, Button, Chip, EmptyState, PageHeader, Panel, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { effectiveZone, formatDayTime } from '../lib/time'
import { usePhone } from '../me/data'
import { BackToEvent } from './BackLink'
import { useBracket } from './data'
import './event.css'

type Pool = Schemas['BracketPoolResponse']
type BSet = Schemas['BracketSetResponse']

interface Round {
  key: string
  label: string
  sets: BSet[]
}

function rounds(sets: BSet[]): Round[] {
  const byRound = new Map<number, BSet[]>()
  for (const s of sets) byRound.set(s.round ?? 0, [...(byRound.get(s.round ?? 0) ?? []), s])
  const order = [...byRound.keys()].sort((a, b) => (a < 0) === (b < 0) ? Math.abs(a) - Math.abs(b) : a < 0 ? 1 : -1)
  return order.map((r) => {
    const list = byRound.get(r)!
    return { key: String(r), label: list[0]?.round_label || `Round ${r}`, sets: list }
  })
}

function SetCard({ set, mine, zone }: { set: BSet; mine: boolean; zone: string }) {
  const { base } = useCommunity()
  const live = set.bookings.find((b) => b.status === 'live')
  const next = set.bookings.find((b) => b.status !== 'complete' && b.scheduled_at)
  let meta: React.ReactNode = null
  if (live) {
    meta = (
      <>
        <Chip tone="live">Live</Chip> {[live.stage, live.game].filter(Boolean).join(' · ')}
      </>
    )
  } else if (set.state === 'complete') {
    meta = 'Finished'
  } else if (next?.scheduled_at) {
    meta = [formatDayTime(next.scheduled_at, zone), next.stage].filter(Boolean).join(' · ')
  } else if (mine && set.state === 'open') {
    meta = <Link to={`${base}/home`}>Pick a time</Link>
  } else if (set.state === 'open') {
    meta = 'Not booked yet'
  }
  const label = set.slots.map((s) => s.name || s.placeholder || 'TBD').join(' vs ')
  return (
    <article className={`b-set${live ? ' b-set--live' : ''}${mine ? ' b-set--mine' : ''}`} aria-label={`${set.identifier}: ${label}${mine ? ' (your match)' : ''}`}>
      {set.slots.map((slot, i) => {
        const won = set.winner_entrant_id != null && slot.entrant_id === set.winner_entrant_id
        const lost = set.winner_entrant_id != null && slot.entrant_id != null && !won
        const name = slot.name || slot.placeholder || 'TBD'
        return (
          <div key={i} className={`b-slot${won ? ' b-slot--won' : ''}${lost ? ' b-slot--lost' : ''}${slot.name ? '' : ' b-slot--tbd'}`}>
            <span className="b-disc" aria-hidden="true">
              {slot.name ? slot.name[0] : '?'}
            </span>
            <span className="b-slot__name">{name}</span>
            <span className="b-slot__score">{slot.score ?? ''}</span>
          </div>
        )
      })}
      {meta && <div className="b-set__meta">{meta}</div>}
    </article>
  )
}

function PoolView({ pool, mine, zone, showName }: { pool: Pool; mine: Set<number>; zone: string; showName: boolean }) {
  const phone = usePhone()
  const list = rounds(pool.sets)
  const [picked, setPicked] = useState<string | null>(null)
  const current = list.find((r) => r.key === picked) ?? list[0]
  const shown = phone && current ? [current] : list
  const isMine = (s: BSet) => s.slots.some((slot) => slot.entrant_id != null && mine.has(slot.entrant_id))
  const elim = /elim/i.test(pool.format ?? '')
  return (
    <section className="b-pool" aria-label={showName ? pool.name : undefined}>
      {showName && <h2 className="b-pool__name">{pool.name}</h2>}
      {!elim && pool.standings.length > 0 && (
        <Panel title="Standings">
          <table className="b-standings">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Player</th>
                <th scope="col">W</th>
                <th scope="col">L</th>
                {pool.standings.some((s) => s.points != null) && <th scope="col">Pts</th>}
              </tr>
            </thead>
            <tbody>
              {pool.standings.map((s, i) => (
                <tr key={s.entrant_id ?? i} className={s.entrant_id != null && mine.has(s.entrant_id) ? 'is-mine' : undefined}>
                  <td>{s.placement ?? i + 1}</td>
                  <td>{s.name}</td>
                  <td>{s.wins}</td>
                  <td>{s.losses}</td>
                  {pool.standings.some((x) => x.points != null) && <td>{s.points ?? ''}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
      {phone && list.length > 1 && (
        <div className="filter-chips b-rounds" role="radiogroup" aria-label="Round">
          {list.map((r) => (
            <span key={r.key}>
              <input
                type="radio"
                id={`r-${pool.id ?? 'p'}-${r.key}`}
                name={`round-${pool.id ?? 'p'}`}
                checked={current?.key === r.key}
                onChange={() => setPicked(r.key)}
              />
              <label htmlFor={`r-${pool.id ?? 'p'}-${r.key}`}>{r.label}</label>
            </span>
          ))}
        </div>
      )}
      {list.length === 0 ? (
        <Panel>
          <EmptyState icon="account_tree" title="No matches drawn yet" />
        </Panel>
      ) : (
        <div className="b-tree">
          {shown.map((r) => (
            <div className="b-round" key={r.key}>
              <h3 className="b-round__label">{r.label}</h3>
              {r.sets.map((s) => (
                <SetCard key={s.id} set={s} mine={isMine(s)} zone={zone} />
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

/** One tournament's bracket, from Wizzrobe, start.gg or Challonge in the same shape. */
export function BracketScreen() {
  const { tournamentId } = useParams()
  const id = Number(tournamentId)
  const { data, error, loading, reload } = useBracket(id)
  const [phaseId, setPhaseId] = useState<string | null>(null)
  const zone = effectiveZone(null)
  const mine = new Set(data?.your_entrant_ids ?? [])
  const phases = data?.phases ?? []
  const phase = phases.find((p) => String(p.id) === phaseId) ?? phases.find((p) => p.state === 'active') ?? phases[0]

  return (
    <>
      <BackToEvent />
      <PageHeader
        title={data?.tournament ?? 'Bracket'}
        lede={
          data?.source && data.source !== 'native' ? (
            <>
              From {data.source === 'startgg' ? 'start.gg' : 'Challonge'}
              {data.url && (
                <>
                  {' · '}
                  <a href={data.url} target="_blank" rel="noopener noreferrer">
                    Open there
                  </a>
                </>
              )}
            </>
          ) : undefined
        }
      />
      {error && (
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      )}
      {loading && !data && <Spinner label="Loading the bracket" />}
      {data && phases.length > 1 && (
        <div className="filter-chips b-phases" role="radiogroup" aria-label="Stage">
          {phases.map((p) => (
            <span key={String(p.id)}>
              <input
                type="radio"
                id={`ph-${p.id}`}
                name="phase"
                checked={phase?.id === p.id}
                onChange={() => setPhaseId(String(p.id))}
              />
              <label htmlFor={`ph-${p.id}`}>{p.name}</label>
            </span>
          ))}
        </div>
      )}
      {data && !phase && (
        <Panel>
          <EmptyState icon="account_tree" title="No bracket yet" />
        </Panel>
      )}
      {phase?.pools.map((pool) => (
        <PoolView key={`${phase.id}-${pool.id ?? pool.name}`} pool={pool} mine={mine} zone={zone} showName={phase.pools.length > 1} />
      ))}
    </>
  )
}

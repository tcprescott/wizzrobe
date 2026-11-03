import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { Button, Chip, Dialog, Icon, RowMenu, type MenuItem, type Tone } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { formatTime } from '../lib/time'
import { sidesLabel, type Row, type Stage, type StreamMatch, type TournamentCard } from './data'

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function Sides({ sides }: { sides: Row['sides'] }) {
  if (sides.length === 0) return <span>TBD</span>
  const shown = sides.slice(0, 2)
  return (
    <span className="rs-row__vs">
      {shown.map((s, i) => (
        <span key={`${s.name}-${i}`}>
          {i > 0 && <span className="rs-row__x">{'\u00a0vs '}</span>}
          <span className={s.undecided ? 'rs-row__tbd' : undefined}>{s.name}</span>
        </span>
      ))}
      {sides.length > 2 && <span className="rs-row__x"> +{sides.length - 2} more</span>}
    </span>
  )
}

const ROLE_NAME: Record<string, string> = { commentator: 'commentary', tracker: 'tracking', broadcaster: 'broadcast' }
const CHOICE: Record<string, string> = { commentator: 'Commentary', tracker: 'Tracking', broadcaster: 'Broadcast' }

/**
 * One run-sheet line: time, matchup, where, crew. One button (sign up for
 * crew when a seat is open) and a ⋮ for watching and taking a signup back.
 */
export function RunSheetRow({ row, zone, onDone }: { row: Row; zone: string; onDone: (msg: string) => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [choose, setChoose] = useState(false)
  const label = sidesLabel(row.sides)
  const open = row.crew.filter((c) => c.can_sign_up)

  async function act(work: () => Promise<unknown>, ok: string) {
    setBusy(true)
    setError(null)
    try {
      await work()
      setChoose(false)
      onDone(ok)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  const path = { params: { path: { match_id: row.match_id } } }
  const signUp = (role: string) =>
    act(
      () => api.POST('/api/matches/{match_id}/crew', { ...path, body: { role } }).then(unwrap),
      `Signed up for ${ROLE_NAME[role] ?? role} on ${label}. Staff will approve it.`,
    )

  const items: MenuItem[] = [
    row.watching
      ? {
          label: 'Stop watching',
          icon: 'visibility_off',
          onSelect: () => void act(() => api.DELETE('/api/matches/{match_id}/watch', path).then(unwrap), `Stopped watching ${label}.`),
        }
      : {
          label: 'Watch this match',
          icon: 'visibility',
          onSelect: () =>
            void act(() => api.POST('/api/matches/{match_id}/watch', path).then(unwrap), `Watching ${label}. We'll tell you when it changes.`),
        },
  ]
  for (const slot of row.crew) {
    if (!slot.yours) continue
    items.push({
      label: `Drop my ${ROLE_NAME[slot.role] ?? slot.role} signup`,
      icon: 'undo',
      destructive: true,
      onSelect: () =>
        void act(
          () =>
            api
              .DELETE('/api/matches/{match_id}/crew/{role}', { params: { path: { match_id: row.match_id, role: slot.role } } })
              .then(unwrap),
          `Dropped your ${ROLE_NAME[slot.role] ?? slot.role} signup.`,
        ),
    })
  }

  const where = [row.stage, row.stations.length ? `Station ${row.stations.join(', ')}` : ''].filter(Boolean).join(' · ')

  return (
    <div className={`rs-row${row.status === 'live' ? ' rs-row--live' : ''}${row.playing ? ' rs-row--mine' : ''}`}>
      <div className="rs-row__time">
        {row.scheduled_at ? <time dateTime={row.scheduled_at}>{formatTime(row.scheduled_at, zone)}</time> : 'TBD'}
        {row.status === 'live' && <Chip tone="live">Live</Chip>}
        {row.status === 'checked_in' && <Chip tone="ok">Checked in</Chip>}
        {row.status === 'finished' && <Chip>Finished</Chip>}
      </div>
      <div className="rs-row__main">
        <div className="rs-row__tn">{[row.tournament, row.round].filter(Boolean).join(' · ')}</div>
        <div className="rs-row__who">
          <Sides sides={row.sides} />
          {row.playing && <Chip tone="info">You play</Chip>}
          {row.watching && (
            <span className="rs-row__watch" title="You're watching this">
              <Icon name="visibility" size={16} />
              <span className="visually-hidden">Watching</span>
            </span>
          )}
        </div>
        {where && (
          <div className="rs-row__where">
            <Icon name={row.streamed ? 'videocam' : 'tv'} size={16} />
            {where}
          </div>
        )}
        {row.crew.length > 0 && (
          <ul className="rs-row__crew" aria-label="Crew">
            {row.crew.map((c) => (
              <li key={c.role} className={`crew-slot${c.filled < c.needed ? ' crew-slot--open' : ''}`}>
                {c.label} {c.filled}/{c.needed}
                {c.yours && <span className="crew-slot__you"> · you{c.yours === 'pending' ? ' (pending)' : ''}</span>}
                {c.names.length > 0 && <span className="visually-hidden">: {c.names.join(', ')}</span>}
              </li>
            ))}
          </ul>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
      <div className="rs-row__acts">
        {open.length > 0 && (
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => (open.length === 1 ? void signUp(open[0]!.role) : setChoose(true))}
          >
            {open.length === 1 ? `Sign up: ${open[0]!.label}` : 'Sign up…'}
          </Button>
        )}
        <RowMenu label={`More for ${label}`} items={items} />
      </div>
      {choose && (
        <Dialog open onOpenChange={(o) => !o && setChoose(false)} size="sm" title="Sign up as crew" description={label}>
          <div className="crew-choices">
            {open.map((c) => (
              <Button key={c.role} variant="outline" disabled={busy} onClick={() => void signUp(c.role)}>
                {CHOICE[c.role] ?? c.label} ({c.filled}/{c.needed})
              </Button>
            ))}
          </div>
        </Dialog>
      )}
    </div>
  )
}

export function StageCard({ stage, zone }: { stage: Stage; zone: string }) {
  const now = stage.on_now
  const next = stage.up_next[0]
  return (
    <article className="stage-card" aria-labelledby={`stage-${stage.id}`}>
      <header className="stage-card__head">
        <Icon name="videocam" size={18} />
        <h3 id={`stage-${stage.id}`}>{stage.name}</h3>
        {now && <Chip tone="live">Live</Chip>}
      </header>
      {now ? (
        <>
          <div className="rs-row__tn">{now.tournament}</div>
          <div className="stage-card__who">{sidesLabel(now.sides)}</div>
        </>
      ) : next ? (
        <>
          <div className="rs-row__tn">Between matches</div>
          <div className="stage-card__who">
            {next.tournament} at {next.scheduled_at ? formatTime(next.scheduled_at, zone) : 'TBD'}
          </div>
        </>
      ) : (
        <div className="stage-card__who stage-card__idle">Nothing else today</div>
      )}
      {next && (
        <div className="stage-card__next">
          {now ? 'Next' : 'Then'} {next.scheduled_at ? formatTime(next.scheduled_at, zone) : 'TBD'} · {sidesLabel(next.sides)}
        </div>
      )}
    </article>
  )
}

const TONE: Record<string, Tone> = { live: 'live', ok: 'ok', pending: 'pending', neutral: 'neutral' }

export function TournamentCardView({ card }: { card: TournamentCard }) {
  const { base } = useCommunity()
  return (
    <article className="t-card" aria-labelledby={`t-${card.id}`}>
      <header className="t-card__head">
        <Icon name="account_tree" size={18} />
        <h3 id={`t-${card.id}`}>{card.name}</h3>
        {card.status && <Chip tone={TONE[card.tone] ?? 'neutral'}>{card.status}</Chip>}
      </header>
      {card.format && <div className="t-card__sub">{card.format}</div>}
      <div className="t-card__links">
        {card.has_bracket ? (
          <Link to={`${base}/event/tournament/${card.id}`}>
            Bracket<span className="visually-hidden"> for {card.name}</span>
          </Link>
        ) : card.bracket_url ? (
          <a href={card.bracket_url} target="_blank" rel="noopener noreferrer">
            Bracket<span className="visually-hidden"> for {card.name} (opens the bracket site)</span>
            <Icon name="open_in_new" size={14} />
          </a>
        ) : null}
        {card.rules_url && (
          <a href={card.rules_url} target="_blank" rel="noopener noreferrer">
            Rules<span className="visually-hidden"> for {card.name} (opens in a new tab)</span>
            <Icon name="open_in_new" size={14} />
          </a>
        )}
      </div>
    </article>
  )
}

export function StreamRow({ match, zone }: { match: StreamMatch; zone: string }) {
  return (
    <div className="sched-row">
      <time className="sched-row__time" dateTime={match.scheduled_at ?? undefined}>
        {match.scheduled_at ? formatTime(match.scheduled_at, zone) : 'TBD'}
      </time>
      <div className="sched-row__main">
        <div className="rs-row__tn">{match.tournament}</div>
        <div className="sched-row__title">{sidesLabel(match.sides)}</div>
      </div>
      {match.stage && <div className="sched-row__sub">{match.stage}</div>}
    </div>
  )
}

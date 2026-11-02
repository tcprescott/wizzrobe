import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { Banner, Button, Chip, EmptyState, FormDialog, Icon, RowMenu, type MenuItem } from '../../components'
import { formatDayTime } from '../../lib/time'
import { useScheduling, type Unbooked } from './data'
import { instantOf, useTimeCheck, whenOf, WhenFields, type When } from './TimeCheck'

const STATE: Record<string, { label: string; tone: 'ok' | 'pending' | 'neutral' }> = {
  ready: { label: 'Ready to book', tone: 'ok' },
  needs_account: { label: 'Needs account', tone: 'pending' },
  tbd: { label: 'Players TBD', tone: 'neutral' },
}

export function unbookedKey(item: Pick<Unbooked, 'source' | 'key'>): string {
  return `${item.source}/${item.key}`
}

function names(item: Unbooked): string {
  if (item.players.length === 0) return 'Players to be decided'
  return item.players.length === 2 ? `${item.players[0]} vs ${item.players[1]}` : item.players.join(', ')
}

function detail(item: Unbooked): string {
  const parts = [item.round_label]
  if (item.best_of > 1) parts.push(`Best of ${item.best_of}, ${item.games_booked} booked`)
  if (item.unlinked.length) parts.push(`${item.unlinked.join(', ')} ${item.unlinked.length === 1 ? "isn't" : "aren't"} matched to a Wizzrobe account yet`)
  if (item.window_text) parts.push(item.window_text)
  return parts.filter(Boolean).join(' · ')
}

/** Bracket matchups nobody has booked, grouped by tournament with their bracket site. */
export function UnbookedList({
  items,
  selected,
  linkFor,
}: {
  items: Unbooked[]
  selected: string | null
  linkFor: (item: Unbooked) => string
}) {
  if (items.length === 0) {
    return (
      <EmptyState icon="event_available" title="Everything's booked">
        Every open bracket matchup in this event has a time.
      </EmptyState>
    )
  }
  const groups = new Map<string, Unbooked[]>()
  for (const item of items) {
    const k = `${item.tournament}\u0000${item.source_label}`
    groups.set(k, [...(groups.get(k) ?? []), item])
  }
  return (
    <>
      {[...groups.entries()].map(([k, rows]) => {
        const first = rows[0]!
        const id = `ms-ub-${first.tournament_id}-${first.source}`
        return (
          <div key={k} role="group" aria-labelledby={id}>
            <h2 className="ms-group" id={id}>
              <span className="ms-group__toggle">
                {first.tournament}
                <span className="ms-group__n">· {rows.length}</span>
                <span className="ms-source">{first.source_label}</span>
              </span>
            </h2>
            {rows.map((item) => {
              const on = selected === unbookedKey(item)
              const state = STATE[item.state] ?? STATE.tbd!
              return (
                <NavLink
                  key={unbookedKey(item)}
                  to={linkFor(item)}
                  className={['ms-row', 'ms-row--ub', on && 'is-selected'].filter(Boolean).join(' ')}
                  aria-current={on ? 'true' : undefined}
                >
                  <span className="ms-row__main">
                    <span className="ms-row__who">{names(item)}</span>
                    <span className="ms-row__meta ms-row__meta--plain">{detail(item)}</span>
                    <span className="ms-row__line">
                      <Chip tone={state.tone}>{state.label}</Chip>
                    </span>
                  </span>
                  <Icon name="chevron_right" className="ms-row__go" />
                </NavLink>
              )
            })}
          </div>
        )
      })}
    </>
  )
}

/** Book one unbooked matchup: date and time, Suggest a time, Check this time, Book. */
export function BookPane({
  item,
  zone,
  closeTo,
  standalone,
  onBooked,
}: {
  item: Unbooked
  zone: string
  closeTo?: string
  standalone?: boolean
  onBooked: (matchId: number | null) => void
}) {
  const scheduling = useScheduling()
  const ref = { source: item.source, key: item.key }
  const [when, setWhen] = useState<When>({ date: '', time: '' })
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [linking, setLinking] = useState(false)
  const at = instantOf(when, zone)
  const check = useTimeCheck({ at, tournamentId: item.tournament_id, playerIds: item.player_ids, ref })
  const outside = check?.window ?? null
  const blocked = item.state === 'needs_account' || (outside !== null && !outside.can_override)
  const tbd = item.state === 'tbd'
  const title = names(item)

  async function book() {
    setFailure(null)
    if (!at) return setFailure('Choose a date and a time.')
    setBusy(true)
    try {
      const { match_id } = await scheduling.book(ref, at, outside !== null)
      onBooked(match_id)
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  async function suggest() {
    const { scheduled_at } = await scheduling.suggest({ tournament_id: item.tournament_id, player_ids: item.player_ids, ...ref })
    setWhen(whenOf(scheduled_at, zone))
  }

  const menu: MenuItem[] = []
  if (item.link_candidates.length > 0) {
    menu.push({ label: tbd ? 'Re-attach a booking…' : 'Link an existing match…', icon: 'add_link', onSelect: () => setLinking(true) })
  }
  if (item.bracket_url) {
    menu.push({ label: 'Open bracket', icon: 'open_in_new', onSelect: () => window.open(item.bracket_url!, '_blank', 'noopener') })
  }
  const state = STATE[item.state] ?? STATE.tbd!
  const Heading = standalone ? 'h1' : 'h2'
  const primary = outside ? 'Book anyway' : item.best_of > 1 ? `Book game ${item.games_booked + 1}` : tbd ? 'Hold this slot' : 'Book match'

  return (
    <article className="ms-pane" aria-labelledby="ms-book-title">
      <header className="ms-pane__head">
        <div className="ms-pane__top">
          <span className="ms-pane__meta">
            {item.tournament} · {item.round_label} · {item.source_label}
          </span>
          <Chip tone={state.tone}>{state.label}</Chip>
          {menu.length > 0 && <RowMenu label={`More for ${title}`} items={menu} />}
          {closeTo && (
            <Button variant="ghost" size="sm" asChild>
              <NavLink to={closeTo}>Close</NavLink>
            </Button>
          )}
        </div>
        <Heading className="ms-pane__title" id="ms-book-title">
          {title}
        </Heading>
        <p className="ms-pane__since">
          {tbd
            ? 'Book a slot now; the players fill in when the bracket decides them.'
            : item.state === 'needs_account'
              ? `${item.unlinked.join(', ')} ${item.unlinked.length === 1 ? "isn't" : "aren't"} matched to a Wizzrobe account yet. Link the entrant on the website's Tournaments page first.`
              : 'Either player can book it from their Schedule. Book it for them here.'}
        </p>
        {item.window_text && <p className="ms-pane__since">{item.window_text}</p>}
      </header>
      <div className="ms-pane__body ms-stack ms-book">
        <WhenFields
          when={when}
          onWhen={setWhen}
          zone={zone}
          check={check}
          onSuggest={item.player_ids.length > 0 && !tbd ? suggest : null}
        />
        {failure && <Banner tone="error">{failure}</Banner>}
        <div className="ms-form__foot ms-form__foot--inline">
          {closeTo && (
            <Button variant="ghost" asChild>
              <NavLink to={closeTo}>Cancel</NavLink>
            </Button>
          )}
          <Button busy={busy} busyLabel="Booking…" disabled={blocked || !at} onClick={book}>
            {primary}
          </Button>
        </div>
      </div>
      {linking && (
        <LinkDialog item={item} zone={zone} onClose={() => setLinking(false)} onLinked={() => onBooked(null)} />
      )}
    </article>
  )
}

function LinkDialog({
  item,
  zone,
  onClose,
  onLinked,
}: {
  item: Unbooked
  zone: string
  onClose: () => void
  onLinked: () => void
}) {
  const scheduling = useScheduling()
  const [pick, setPick] = useState<number | null>(item.link_candidates[0]?.id ?? null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const tbd = item.state === 'tbd'
  async function link() {
    if (pick === null) return
    setBusy(true)
    setFailure(null)
    try {
      await scheduling.link({ source: item.source, key: item.key }, pick, tbd)
      onLinked()
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={tbd ? 'Re-attach a booking?' : 'Link an existing match?'}
      size="sm"
      description={
        tbd
          ? 'Pick the booking this matchup lost, and it follows the bracket again.'
          : 'Pick the match already on the schedule for this matchup. Its result then goes back to the bracket.'
      }
      primaryLabel="Link match"
      busy={busy}
      busyLabel="Linking…"
      onPrimary={link}
    >
      <fieldset className="ms-stack ms-link">
        <legend className="visually-hidden">Matches</legend>
        {item.link_candidates.map((c) => (
          <label key={c.id} className="t-check">
            <input type="radio" name="ms-link" checked={pick === c.id} onChange={() => setPick(c.id)} />
            {c.scheduled_at ? formatDayTime(c.scheduled_at, zone) : 'No time'}
            {c.stage ? ` · ${c.stage}` : ''}
          </label>
        ))}
      </fieldset>
      {failure && <Banner tone="error">{failure}</Banner>}
    </FormDialog>
  )
}

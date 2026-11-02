import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, EmptyState, FormDialog, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { effectiveZone, formatDay, formatDayTime } from '../../lib/time'
import { done, message } from '../../me/data'
import { useAdminQuery, type BoardRow, type Person, type Player } from './data'

/** Who's enrolled, adding a member, and dropping a player (with what that settles). */
export function PlayersTab({ row, zone, onChange }: { row: BoardRow; zone: string | null; onChange: () => void }) {
  const players = useAdminQuery(`tournaments/${row.id}/players`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/players', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const [adding, setAdding] = useState(false)
  const [dropping, setDropping] = useState<Player | null>(null)
  const z = effectiveZone(zone)

  if (players.error && !players.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={players.reload}>Try again</Button>}>
        {players.error.message}
      </Banner>
    )
  }
  if (!players.data) return <Spinner label="Loading players" />
  const list = players.data
  const changed = () => {
    players.reload()
    onChange()
  }

  return (
    <>
      <Panel
        title={`Players · ${list.length}`}
        actions={
          <Button variant="outline" size="sm" icon="person_add" onClick={() => setAdding(true)}>
            Add player
          </Button>
        }
      >
        {list.length === 0 ? (
          <PanelRow>
            <EmptyState icon="groups" title="No players yet">
              Players join from Home › Tournaments while signups are open, or you can add them here.
            </EmptyState>
          </PanelRow>
        ) : (
          list.map((p) => (
            <PanelRow key={p.user.id}>
              <div className="t-row">
                <span className="t-row__text">
                  <span className="t-row__title">{p.user.name}</span>
                  {p.enrolled_at && <span className="t-row__sub">Joined {formatDay(p.enrolled_at, z)}</span>}
                </span>
                <RowMenu
                  label={`More for ${p.user.name}`}
                  items={[{ label: 'Drop from tournament…', icon: 'person_remove', destructive: true, onSelect: () => setDropping(p) }]}
                />
              </div>
            </PanelRow>
          ))
        )}
      </Panel>
      {adding && <AddPlayer row={row} enrolled={new Set(list.map((p) => p.user.id))} onClose={() => setAdding(false)} onAdded={changed} />}
      {dropping && <DropPlayer row={row} player={dropping} zone={z} onClose={() => setDropping(null)} onDropped={changed} />}
    </>
  )
}

function AddPlayer({ row, enrolled, onClose, onAdded }: { row: BoardRow; enrolled: Set<number>; onClose: () => void; onAdded: () => void }) {
  const people = useAdminQuery(`tournaments/${row.id}/people`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/people', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<Person | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const q = query.trim().toLowerCase()
  const matches = (people.data ?? [])
    .filter((p) => !enrolled.has(p.id))
    .filter((p) => !q || p.name.toLowerCase().includes(q) || (p.username ?? '').toLowerCase().includes(q))
    .slice(0, 8)

  async function add() {
    if (!picked) {
      setError('Pick a member first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api
        .POST('/api/tournaments/{tournament_id}/players', { params: { path: { tournament_id: row.id } }, body: { user_id: picked.id } })
        .then(unwrap)
      onAdded()
      onClose()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Add a player to ${row.name}`}
      description="Adding someone here ignores the signup window."
      primaryLabel="Add player"
      onPrimary={() => void add()}
      busy={busy}
      busyLabel="Adding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Find a member</span>
          <input className="input" type="search" value={query} placeholder="Name or Discord name" onChange={(e) => setQuery(e.target.value)} />
        </label>
        {people.loading && !people.data ? (
          <Spinner label="Loading members" />
        ) : (
          <div role="radiogroup" aria-label="Members" className="t-form">
            {matches.map((p) => (
              <label key={p.id} className="t-check">
                <input type="radio" name="member" checked={picked?.id === p.id} onChange={() => setPicked(p)} />
                <span>
                  {p.name}
                  {p.username && p.username !== p.name && <span className="t-row__sub"> · {p.username}</span>}
                </span>
              </label>
            ))}
            {matches.length === 0 && <span className="t-row__sub">No members match.</span>}
          </div>
        )}
      </div>
    </FormDialog>
  )
}

function DropPlayer({
  row,
  player,
  zone,
  onClose,
  onDropped,
}: {
  row: BoardRow
  player: Player
  zone: string
  onClose: () => void
  onDropped: () => void
}) {
  const plan = useAdminQuery(`tournaments/${row.id}/drop/${player.user.id}`, () =>
    api
      .GET('/api/admin/tournaments/{tournament_id}/players/{user_id}/drop-plan', {
        params: { path: { tournament_id: row.id, user_id: player.user.id } },
      })
      .then(unwrap),
  )
  const [forfeit, setForfeit] = useState(true)
  const [reason, setReason] = useState<'withdrew' | 'no_show' | 'disqualified'>('withdrew')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function drop() {
    setBusy(true)
    setError(null)
    try {
      done(
        await api.POST('/api/admin/tournaments/{tournament_id}/players/{user_id}/drop', {
          params: { path: { tournament_id: row.id, user_id: player.user.id } },
          body: { reason, forfeit_matches: forfeit },
        }),
      )
      onDropped()
      onClose()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  const p = plan.data
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Drop ${player.user.name}?`}
      description="They come off the roster and get one DM saying so."
      primaryLabel="Drop player"
      destructive
      onPrimary={() => void drop()}
      busy={busy}
      busyLabel="Dropping…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        {!p ? (
          <Spinner label="Checking their matches" />
        ) : (
          <>
            {p.on_bracket_site && <Banner tone="warning">They're dropped on the Challonge bracket too.</Banner>}
            {p.head_to_head.length === 0 && p.group.length === 0 ? (
              <p style={{ margin: 0 }}>They have no unplayed matches.</p>
            ) : (
              <>
                {p.head_to_head.length > 0 && (
                  <>
                    <p style={{ margin: 0 }}>
                      {p.head_to_head.length} head-to-head {p.head_to_head.length === 1 ? 'match' : 'matches'}:
                    </p>
                    <ul style={{ margin: 0 }}>
                      {p.head_to_head.map((m) => (
                        <li key={m.id}>
                          {m.title}
                          {m.scheduled_at ? ` · ${formatDayTime(m.scheduled_at, zone)}` : ''}
                        </li>
                      ))}
                    </ul>
                    <fieldset className="field t-instant">
                      <legend className="field__label">What happens to them</legend>
                      <label className="t-check">
                        <input type="radio" name="forfeit" checked={forfeit} onChange={() => setForfeit(true)} />
                        Forfeit to the opponent
                      </label>
                      <label className="t-check">
                        <input type="radio" name="forfeit" checked={!forfeit} onChange={() => setForfeit(false)} />
                        Call them off
                      </label>
                    </fieldset>
                    {forfeit && (
                      <label className="field">
                        <span className="field__label">Reason</span>
                        <select className="input" value={reason} onChange={(e) => setReason(e.target.value as typeof reason)}>
                          <option value="withdrew">Withdrew</option>
                          <option value="no_show">No-show</option>
                          <option value="disqualified">Disqualified</option>
                        </select>
                      </label>
                    )}
                  </>
                )}
                {p.group.length > 0 && (
                  <p style={{ margin: 0 }}>
                    They come off the line-up of {p.group.length} larger {p.group.length === 1 ? 'match' : 'matches'}.
                  </p>
                )}
              </>
            )}
          </>
        )}
      </div>
    </FormDialog>
  )
}

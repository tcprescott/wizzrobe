import { api, unwrap } from '../../api/client'
import { Banner, Chip, Panel, PanelRow, Spinner, type Tone } from '../../components'
import { useAdminQuery } from '../kit'

/** The whole board, best first. */
export function LeaderboardPage({ qualifierId }: { qualifierId: number }) {
  const board = useAdminQuery(`qualifiers/${qualifierId}/leaderboard`, () =>
    api.GET('/api/async-qualifiers/{qualifier_id}/leaderboard', { params: { path: { qualifier_id: qualifierId } } }).then(unwrap),
  )
  if (board.error && !board.data) return <Banner tone="error">{board.error.message}</Banner>
  if (!board.data) return <Spinner label="Loading the leaderboard" />
  return (
    <Panel title={`${board.data.length} on the board`}>
      {board.data.length === 0 && (
        <PanelRow>
          <span className="t-row__sub">Nobody's on the board yet.</span>
        </PanelRow>
      )}
      {board.data.map((e) => (
        <PanelRow key={e.user_id}>
          <div className="t-row">
            <span className="q-rank">{e.rank}</span>
            <span className="t-row__text">
              <span className="t-row__title">{e.username}</span>
              <span className="t-row__sub">
                {e.slots_filled}/{e.slots_total} slots · estimate {e.estimate.toFixed(1)}
              </span>
            </span>
            <span className="q-score">{e.actual.toFixed(1)}</span>
          </div>
        </PanelRow>
      ))}
    </Panel>
  )
}

const RACE_TONE: Record<string, Tone> = {
  scheduled: 'neutral',
  pending: 'info',
  in_progress: 'live',
  finished: 'ok',
  cancelled: 'cancelled',
}

/** Live races on the qualifier's seeds, newest first. */
export function LiveRacesPage({ qualifierId }: { qualifierId: number }) {
  const races = useAdminQuery(`qualifiers/${qualifierId}/live-races`, () =>
    api.GET('/api/admin/qualifiers/{qualifier_id}/live-races', { params: { path: { qualifier_id: qualifierId } } }).then(unwrap),
  )
  if (races.error && !races.data) return <Banner tone="error">{races.error.message}</Banner>
  if (!races.data) return <Spinner label="Loading live races" />
  return (
    <Panel title={`${races.data.length} ${races.data.length === 1 ? 'race' : 'races'}`}>
      {races.data.length === 0 && (
        <PanelRow>
          <span className="t-row__sub">No live races. A pool needs a seed kept for live races before you can run one.</span>
        </PanelRow>
      )}
      {races.data.map((r) => (
        <PanelRow key={r.id}>
          <div className="t-row">
            <span className="t-row__text">
              <span className="t-row__title">{r.title}</span>
              <span className="t-row__sub">
                {r.pool}
                {r.has_seed ? '' : ' · no seed yet'}
                {r.racetime_slug ? (
                  <>
                    {' · '}
                    <a href={`https://racetime.gg/${r.racetime_slug}`} target="_blank" rel="noopener noreferrer">
                      racetime room
                    </a>
                  </>
                ) : null}
              </span>
            </span>
            <Chip tone={RACE_TONE[r.status] ?? 'neutral'}>{r.status_label}</Chip>
          </div>
        </PanelRow>
      ))}
    </Panel>
  )
}

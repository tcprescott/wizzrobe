import { useState, type ReactNode } from 'react'
import { Link, Navigate, Route, Routes } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, FormDialog, Icon, Panel, PanelRow, Spinner, type IconName, type MenuItem } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { useAdminQuery } from '../kit'
import { STATE_TONE, type QualifierDetail, type QualifierRow } from './data'
import { LeaderboardPage, LiveRacesPage } from './LeaderboardPage'
import { PoolsPage } from './PoolsPage'
import { QueuePanel } from './QueuePanel'
import { ReviewersPage } from './ReviewersPage'
import { SettingsPage } from './QualifierForm'

interface Props {
  row: QualifierRow
  /** `<adminBase>/qualifiers/<id>`. */
  base: string
  zone: string | null
  onBoardChange: () => void
  onDeleted: () => void
}

/** One qualifier: the review queue first, then the top of the board, then setup rows. */
export function QualifierPane({ row, base, zone, onBoardChange, onDeleted }: Props) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const detail = useAdminQuery(`qualifiers/${row.id}`, () =>
    api.GET('/api/admin/qualifiers/{qualifier_id}', { params: { path: { qualifier_id: row.id } } }).then(unwrap),
  )
  const [deleting, setDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const reload = () => {
    detail.reload()
    onBoardChange()
  }

  const z = effectiveZone(zone)
  const when = [
    row.opens_at && row.state === 'upcoming' ? `Opens ${formatDayTime(row.opens_at, z)}` : null,
    row.closes_at ? `${row.state === 'closed' ? 'Closed' : 'Closes'} ${formatDayTime(row.closes_at, z)}` : null,
  ].filter(Boolean)

  const menu: MenuItem[] = [{ label: 'Delete qualifier…', icon: 'delete', destructive: true, onSelect: () => setDeleting(true) }]

  const head = (
    <div className="detail-head">
      <div className="detail-head__text">
        <Heading>{row.name}</Heading>
        <div className="detail-head__sub">
          <Chip tone={STATE_TONE[row.state]}>{row.state_label}</Chip>
          <span>{[...when, `${row.runs} ${row.runs === 1 ? 'run' : 'runs'}`].join(' · ')}</span>
        </div>
      </div>
      <ActionsMenu label="Actions" items={menu} />
    </div>
  )

  if (detail.error && !detail.data) {
    return (
      <>
        {head}
        <Banner tone="error" action={<Button variant="outline" onClick={detail.reload}>Try again</Button>}>
          {detail.error.message}
        </Banner>
      </>
    )
  }
  if (!detail.data) return <>{head}<Spinner label="Loading the qualifier" /></>
  const d = detail.data

  return (
    <>
      {head}
      <Routes>
        <Route index element={<Overview d={d} base={base} zone={z} onChange={reload} />} />
        <Route path="leaderboard" element={<SubPage base={base} title="Leaderboard"><LeaderboardPage qualifierId={row.id} /></SubPage>} />
        <Route path="pools" element={<SubPage base={base} title="Pools and seeds"><PoolsPage qualifierId={row.id} onChange={reload} /></SubPage>} />
        <Route path="live-races" element={<SubPage base={base} title="Live races"><LiveRacesPage qualifierId={row.id} /></SubPage>} />
        <Route path="reviewers" element={<SubPage base={base} title="Reviewers"><ReviewersPage qualifierId={row.id} onChange={reload} /></SubPage>} />
        <Route path="settings" element={<SubPage base={base} title="Settings"><SettingsPage row={row} detail={d} zone={zone} onSaved={reload} /></SubPage>} />
        <Route path="*" element={<Navigate to={base} replace />} />
      </Routes>
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(false)}
          title={`Delete ${row.name}?`}
          primaryLabel="Delete qualifier"
          destructive
          onPrimary={() =>
            void (async () => {
              try {
                done(await api.DELETE('/api/async-qualifiers/{qualifier_id}', { params: { path: { qualifier_id: row.id } } }))
                setDeleting(false)
                onDeleted()
              } catch (err) {
                setDeleteError(message(err))
              }
            })()
          }
        >
          <div className="t-form">
            {deleteError && <Banner tone="error">{deleteError}</Banner>}
            <p style={{ margin: 0 }}>Its pools, seeds and all {row.runs} runs go with it. This can't be undone.</p>
          </div>
        </FormDialog>
      )}
    </>
  )
}

function SubPage({ base, title, children }: { base: string; title: string; children: ReactNode }) {
  return (
    <section className="q-sub" aria-label={title}>
      <Link to={base} className="q-sub__back">
        <Icon name="arrow_back" size={18} />
        Back to the review queue
      </Link>
      <h3 className="q-sub__title">{title}</h3>
      {children}
    </section>
  )
}

function Overview({ d, base, zone, onChange }: { d: QualifierDetail; base: string; zone: string; onChange: () => void }) {
  const s = d.setup
  const rows: { slug: string; icon: IconName; title: string; sub: string }[] = [
    { slug: 'pools', icon: 'casino', title: 'Pools and seeds', sub: `${s.pools} ${s.pools === 1 ? 'pool' : 'pools'} · ${s.seeds} ${s.seeds === 1 ? 'seed' : 'seeds'}` },
    { slug: 'live-races', icon: 'flag', title: 'Live races', sub: s.live_races ? `${s.live_races} · ${s.live_races_open} open or coming up` : 'None' },
    { slug: 'reviewers', icon: 'groups', title: 'Reviewers', sub: s.reviewers ? `${s.reviewers} for this qualifier, plus Staff and Qualifier Admins` : 'Staff and Qualifier Admins only' },
    { slug: 'settings', icon: 'tune', title: 'Settings', sub: `Window, ${d.settings.runs_per_pool} per pool, ${d.settings.allowed_reattempts} reattempts` },
  ]
  return (
    <>
      <QueuePanel cards={d.queue} zone={zone} onChange={onChange} />
      <Panel
        title="Leaderboard"
        actions={
          d.leaderboard_total > d.leaderboard.length && (
            <Link className="q-link" to={`${base}/leaderboard`}>
              Full board · {d.leaderboard_total}
            </Link>
          )
        }
      >
        {d.leaderboard.length === 0 && (
          <PanelRow>
            <span className="t-row__sub">Nobody's on the board yet.</span>
          </PanelRow>
        )}
        {d.leaderboard.map((l) => (
          <PanelRow key={`${l.rank}-${l.name}`}>
            <div className="t-row">
              <span className="q-rank">{l.rank}</span>
              <span className="t-row__text">
                <span className="t-row__title">{l.name}</span>
                <span className="t-row__sub">{l.slots} slots</span>
              </span>
              <span className="q-score">{l.score.toFixed(1)}</span>
            </div>
          </PanelRow>
        ))}
      </Panel>
      <Panel title="Setup">
        <ul className="q-setup" aria-label="Setup">
          {rows.map((r) => (
            <li key={r.slug}>
              <Link to={`${base}/${r.slug}`} className="q-setup__row">
                <Icon name={r.icon} size={20} />
                <span className="t-row__text">
                  <span className="t-row__title">{r.title}</span>
                  <span className="t-row__sub">{r.sub}</span>
                </span>
                <Icon name="chevron_right" size={20} />
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
    </>
  )
}

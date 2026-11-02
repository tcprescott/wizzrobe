import { useState } from 'react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { Banner, Button, Chip, EmptyState, FormDialog, PageHeader, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { SideList, useAdmin, useAdminQuery } from '../kit'
import { useBoard, type BoardRow } from '../tournaments/data'

type Text = Schemas['TriforceTextResponse']
type Filter = 'pending' | 'approved' | 'rejected'

/** Admin → Triforce Texts: tournaments whose seeds can carry texts, and each one's moderation queue. */
export function TriforceTextsScreen() {
  return (
    <Routes>
      <Route index element={<TriforceBoard />} />
      <Route path=":id" element={<TriforceBoard />} />
    </Routes>
  )
}

function TriforceBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const board = useBoard()
  const randomizers = useAdminQuery('seeds/randomizers', () => api.GET('/api/seeds/randomizers').then(unwrap))
  const base = `${adminBase}/triforce-texts`

  const error = board.error ?? randomizers.error
  if (error && !(board.data && randomizers.data)) {
    return (
      <>
        <PageHeader title="Triforce Texts" />
        <Banner tone="error" action={<Button variant="outline" onClick={() => { board.reload(); randomizers.reload() }}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (!board.data || !randomizers.data) return <Spinner label="Loading tournaments" />

  const capable = new Set(randomizers.data.filter((r) => r.supports_triforce_texts).map((r) => r.randomizer))
  const rows = board.data.rows.filter((r) => r.is_active && r.seed_generator && capable.has(r.seed_generator))
  const openId = id ? Number(id) : null
  const open = rows.find((r) => r.id === openId) ?? null
  if (!phone && openId == null && rows[0]) return <Navigate to={`${base}/${rows[0].id}`} replace />

  return (
    <>
      {!(phone && open) && <PageHeader title="Triforce Texts" lede="Players send three-line messages; approved ones can show up in their tournament's seeds." />}
      <SideList
        label="Tournaments"
        items={rows}
        itemKey={(r) => r.id}
        itemTo={(r) => `${base}/${r.id}`}
        title={(r) => r.name}
        sub={(r) => r.seed_generator}
        openKey={openId}
        back={{ to: base, label: 'Triforce Texts' }}
        detail={
          open ? (
            <Queue key={open.id} row={open} zone={board.data.zone ?? null} />
          ) : (
            <EmptyState icon="info" title="That tournament isn't here">
              Its randomizer may not take texts, or you may not run it.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="trophy" title="Pick a tournament">
            Its submitted texts open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="trophy" title="No tournaments take texts">
            Only active tournaments on a randomizer that can embed texts (like ALTTPR) appear here.
          </EmptyState>
        }
      />
    </>
  )
}

const FILTERS: [Filter, string][] = [
  ['pending', 'Waiting'],
  ['approved', 'Approved'],
  ['rejected', 'Rejected'],
]

function Queue({ row, zone }: { row: BoardRow; zone: string | null }) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const texts = useAdminQuery(`triforce/${row.id}`, () =>
    api.GET('/api/triforce-texts', { params: { query: { tournament_id: row.id } } }).then(unwrap),
  )
  const [filter, setFilter] = useState<Filter>('pending')
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Text | null>(null)
  const z = effectiveZone(zone)
  const stateOf = (t: Text): Filter => (t.approved == null ? 'pending' : t.approved ? 'approved' : 'rejected')

  async function act(call: () => Promise<{ error?: unknown; response: Response }>) {
    setError(null)
    try {
      done(await call())
      texts.reload()
    } catch (err) {
      setError(message(err))
    }
  }
  const moderate = (t: Text, approved: boolean) =>
    act(() => api.POST('/api/triforce-texts/{text_id}/moderate', { params: { path: { text_id: t.id } }, body: { approved } }))

  const all = texts.data ?? []
  const shown = all.filter((t) => stateOf(t) === filter)
  return (
    <>
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{row.name}</Heading>
          <div className="detail-head__sub">{all.length} submitted</div>
        </div>
      </div>
      <div className="filter-chips" role="group" aria-label="Show">
        {FILTERS.map(([key, label]) => (
          <button key={key} type="button" className="filter-chip" aria-pressed={filter === key} onClick={() => setFilter(key)}>
            {label} · {all.filter((t) => stateOf(t) === key).length}
          </button>
        ))}
      </div>
      {error && <Banner tone="error">{error}</Banner>}
      {texts.error && !texts.data ? (
        <Banner tone="error" action={<Button variant="outline" onClick={texts.reload}>Try again</Button>}>
          {texts.error.message}
        </Banner>
      ) : !texts.data ? (
        <Spinner label="Loading texts" />
      ) : shown.length === 0 ? (
        <EmptyState icon="check_circle" title={filter === 'pending' ? 'Nothing waiting' : `No ${filter} texts`}>
          {filter === 'pending' ? 'New submissions show up here.' : ''}
        </EmptyState>
      ) : (
        <Panel title={FILTERS.find(([k]) => k === filter)![1]}>
          {shown.map((t) => (
            <PanelRow key={t.id}>
              <div className="t-row">
                <span className="t-row__text">
                  <span className="t-row__title mono" style={{ whiteSpace: 'pre-line' }}>
                    {t.text}
                  </span>
                  <span className="t-row__sub">
                    {t.author ?? 'Deleted account'} · {formatDayTime(t.created_at, z)}
                  </span>
                  {filter === 'pending' && (
                    <span className="t-actions" style={{ marginTop: 8 }}>
                      <Button size="sm" variant="outline" onClick={() => void moderate(t, false)}>
                        Reject
                      </Button>
                      <Button size="sm" onClick={() => void moderate(t, true)}>
                        Approve
                      </Button>
                    </span>
                  )}
                </span>
                {filter !== 'pending' && <Chip tone={filter === 'approved' ? 'ok' : 'cancelled'}>{filter === 'approved' ? 'Approved' : 'Rejected'}</Chip>}
                <RowMenu
                  label={`More for the text by ${t.author ?? 'a deleted account'}`}
                  items={[
                    ...(filter === 'approved' ? [{ label: 'Reject instead', icon: 'close' as const, onSelect: () => void moderate(t, false) }] : []),
                    ...(filter === 'rejected' ? [{ label: 'Approve instead', icon: 'check' as const, onSelect: () => void moderate(t, true) }] : []),
                    {
                      label: 'Delete',
                      icon: 'delete',
                      destructive: true,
                      onSelect: () => setDeleting(t),
                    },
                  ]}
                />
              </div>
            </PanelRow>
          ))}
        </Panel>
      )}
      {deleting && (
        <FormDialog
          open
          onOpenChange={(o) => !o && setDeleting(null)}
          title="Delete this text?"
          description="It's gone for good. Reject it instead to keep a record."
          primaryLabel="Delete"
          destructive
          onPrimary={() => {
            const t = deleting
            setDeleting(null)
            void act(() => api.DELETE('/api/triforce-texts/{text_id}', { params: { path: { text_id: t.id } } }))
          }}
        >
          <p className="mono" style={{ margin: 0, whiteSpace: 'pre-line' }}>
            {deleting.text}
          </p>
        </FormDialog>
      )}
    </>
  )
}

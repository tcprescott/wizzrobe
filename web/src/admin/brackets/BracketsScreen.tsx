import { useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Chip, EmptyState, PageHeader, Spinner } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { SideList, useAdmin, useAdminQuery } from '../kit'
import { STATE_TONE, type StageRow } from './data'
import { StageDetailPane } from './StageDetail'
import { StageForm } from './StageForm'
import './brackets.css'

type Filter = 'running' | 'all'

/** Admin → Brackets: every native bracket stage as a side list; a stage opens on its next step. */
export function BracketsScreen() {
  return (
    <Routes>
      <Route index element={<BracketsBoard />} />
      <Route path=":id" element={<BracketsBoard />} />
    </Routes>
  )
}

function BracketsBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const navigate = useNavigate()
  const board = useAdminQuery('brackets/board', () => api.GET('/api/admin/brackets/board').then(unwrap))
  const [filter, setFilter] = useState<Filter>('running')
  const [adding, setAdding] = useState(false)
  const base = `${adminBase}/brackets`

  if (board.error && !board.data) {
    return (
      <>
        <PageHeader title="Brackets" />
        <Banner tone="error" action={<Button variant="outline" onClick={board.reload}>Try again</Button>}>
          {board.error.message}
        </Banner>
      </>
    )
  }
  if (!board.data) return <Spinner label="Loading brackets" />

  const names = new Map(board.data.tournaments.map((t) => [t.id, t.name]))
  const all: StageRow[] = board.data.tournaments.flatMap((t) => t.stages)
  const running = all.filter((s) => s.state === 'draft' || s.state === 'active' || s.next_step.key === 'advance')
  const shown = filter === 'running' ? running : all
  const openId = id ? Number(id) : null
  const open = all.find((s) => s.id === openId) ?? null
  if (!phone && openId == null && shown[0]) return <Navigate to={`${base}/${shown[0].id}`} replace />

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="Brackets"
          lede="Native brackets you run here. Each stage opens on the one step it's up to."
          actions={
            <Button icon="add" onClick={() => setAdding(true)}>
              Add stage
            </Button>
          }
        />
      )}
      <SideList
        label="Bracket stages"
        items={shown}
        itemKey={(s) => s.id}
        itemTo={(s) => `${base}/${s.id}`}
        title={(s) => `${names.get(s.tournament_id) ?? 'Tournament'} · ${s.name}`}
        sub={(s) => (
          <span className="b-sub">
            <Chip tone={STATE_TONE[s.state]}>{s.state_label}</Chip>
            <span>{s.next_step.key ? `${s.next_step.title} · ${s.next_step.detail}` : s.next_step.detail}</span>
          </span>
        )}
        openKey={openId}
        back={{ to: base, label: 'Brackets' }}
        toolbar={
          <div className="filter-chips" role="group" aria-label="Show">
            <button type="button" className="filter-chip" aria-pressed={filter === 'running'} onClick={() => setFilter('running')}>
              In progress · {running.length}
            </button>
            <button type="button" className="filter-chip" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
              All · {all.length}
            </button>
          </div>
        }
        detail={
          open ? (
            <StageDetailPane
              key={open.id}
              row={open}
              stages={all.filter((s) => s.tournament_id === open.tournament_id)}
              formats={board.data.formats}
              onBoardChange={board.reload}
              onDeleted={() => {
                board.reload()
                navigate(base)
              }}
            />
          ) : (
            <EmptyState icon="info" title="That stage isn't here">
              It may have been deleted, or belong to another event.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="account_tree" title="Pick a stage">
            Its next step, seeds and matches open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="account_tree" title={all.length ? 'Nothing in progress' : 'No bracket stages yet'}>
            {all.length ? 'Every stage is complete. Switch to All to see them.' : 'Add a stage to a tournament to start a bracket.'}
          </EmptyState>
        }
      />
      {adding && (
        <StageForm
          tournaments={board.data.tournaments}
          formats={board.data.formats}
          onClose={() => setAdding(false)}
          onSaved={(stageId) => {
            board.reload()
            navigate(`${base}/${stageId}`)
          }}
        />
      )}
    </>
  )
}

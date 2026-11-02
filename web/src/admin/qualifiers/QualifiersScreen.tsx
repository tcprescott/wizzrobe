import { useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Chip, EmptyState, PageHeader, Spinner } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { SideList, useAdmin, useAdminQuery } from '../kit'
import { rowSub, STATE_TONE } from './data'
import { QualifierForm } from './QualifierForm'
import { QualifierPane } from './QualifierPane'
import './qualifiers.css'

type Filter = 'running' | 'all'

/** Admin → Qualifiers: the qualifiers you run, as a side list; one opens on its review queue. */
export function QualifiersScreen() {
  return (
    <Routes>
      <Route index element={<QualifiersBoard />} />
      <Route path=":id/*" element={<QualifiersBoard />} />
    </Routes>
  )
}

function QualifiersBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const navigate = useNavigate()
  const board = useAdminQuery('qualifiers/board', () => api.GET('/api/admin/qualifiers/board').then(unwrap))
  const [filter, setFilter] = useState<Filter>('running')
  const [adding, setAdding] = useState(false)
  const base = `${adminBase}/qualifiers`

  if (board.error && !board.data) {
    return (
      <>
        <PageHeader title="Qualifiers" />
        <Banner tone="error" action={<Button variant="outline" onClick={board.reload}>Try again</Button>}>
          {board.error.message}
        </Banner>
      </>
    )
  }
  if (!board.data) return <Spinner label="Loading qualifiers" />

  const all = board.data.qualifiers
  const running = all.filter((q) => q.state === 'open' || q.state === 'upcoming' || q.to_review > 0)
  const shown = filter === 'running' ? running : all
  const openId = id ? Number(id) : null
  const open = all.find((q) => q.id === openId) ?? null
  if (!phone && openId == null && shown[0]) return <Navigate to={`${base}/${shown[0].id}`} replace />

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="Qualifiers"
          lede="Async qualifiers: runs waiting for review come first."
          actions={
            board.data.can_create && (
              <Button icon="add" onClick={() => setAdding(true)}>
                Add qualifier
              </Button>
            )
          }
        />
      )}
      <SideList
        label="Qualifiers"
        items={shown}
        itemKey={(q) => q.id}
        itemTo={(q) => `${base}/${q.id}`}
        title={(q) => q.name}
        sub={(q) => (
          <span className="side-list__meta">
            <Chip tone={STATE_TONE[q.state]}>{q.state_label}</Chip>
            <span>{rowSub(q)}</span>
          </span>
        )}
        openKey={openId}
        back={{ to: base, label: 'Qualifiers' }}
        toolbar={
          <div className="filter-chips" role="group" aria-label="Show">
            <button type="button" className="filter-chip" aria-pressed={filter === 'running'} onClick={() => setFilter('running')}>
              Open or waiting · {running.length}
            </button>
            <button type="button" className="filter-chip" aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>
              All · {all.length}
            </button>
          </div>
        }
        detail={
          open ? (
            <QualifierPane
              key={open.id}
              row={open}
              base={`${base}/${open.id}`}
              zone={board.data.zone ?? null}
              onBoardChange={board.reload}
              onDeleted={() => {
                board.reload()
                navigate(base)
              }}
            />
          ) : (
            <EmptyState icon="info" title="That qualifier isn't here">
              It may have been deleted, or belong to another event.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="timer" title="Pick a qualifier">
            Its review queue, leaderboard and setup open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="timer" title={all.length ? 'Nothing open' : 'No qualifiers yet'}>
            {all.length ? 'Every qualifier is closed and reviewed. Switch to All to see them.' : 'Add a qualifier to start taking runs.'}
          </EmptyState>
        }
      />
      {adding && (
        <QualifierForm
          zone={board.data.zone ?? null}
          onClose={() => setAdding(false)}
          onSaved={(qid) => {
            board.reload()
            navigate(`${base}/${qid}`)
          }}
        />
      )}
    </>
  )
}

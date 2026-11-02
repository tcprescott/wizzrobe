import { useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, Icon, PageHeader, Spinner } from '../../components'
import { effectiveZone, formatDayTime } from '../../lib/time'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { SideListCard, SideListLayout, SideListRow, UnsavedChangesGuard, useAdmin, useAdminQuery } from '../kit'
import { saveFile, type Board, type LayoutLine, type StageLine } from './data'
import { LayoutEditor } from './LayoutEditor'
import { LayoutPane } from './LayoutPane'
import { NewLayoutDialog } from './LayoutDialogs'
import { StagePane } from './StagePane'
import './stream-layouts.css'

/** Admin → Stream Layouts: stages and layouts in one side list; a layout's box editor takes the full width. */
export function StreamLayoutsScreen() {
  return (
    <UnsavedChangesGuard>
      <Routes>
        <Route index element={<LayoutsBoard />} />
        <Route path="stages/:id" element={<LayoutsBoard kind="stage" />} />
        <Route path="layouts/:id" element={<LayoutsBoard kind="layout" />} />
        <Route path="layouts/:id/edit" element={<EditorRoute />} />
      </Routes>
    </UnsavedChangesGuard>
  )
}

export function useBoard() {
  return useAdminQuery('stream-layouts/board', () => api.GET('/api/admin/stream-layouts/board').then(unwrap))
}

function stageSub(s: StageLine, zone: string) {
  const layout = s.layout_name ?? 'No layout'
  if (s.showing) {
    const live = !!s.showing.started_at
    return (
      <span className="side-list__meta">
        {s.pinned ? <Chip tone="pending">Pinned</Chip> : live ? <Chip tone="live">Live</Chip> : null}
        <span>
          {!s.pinned && !live && s.showing.scheduled_at ? `Next: ${s.showing.who} at ${formatDayTime(s.showing.scheduled_at, zone)}` : s.showing.who} · {layout}
        </span>
      </span>
    )
  }
  return <span>{layout}</span>
}

function layoutSub(l: LayoutLine) {
  const parts = [`${l.width}×${l.height}`, `${l.box_count} ${l.box_count === 1 ? 'box' : 'boxes'}`]
  if (l.stages.length) parts.push(`${l.stages.length} ${l.stages.length === 1 ? 'stage' : 'stages'}`)
  if (!l.art_url) parts.push('no art yet')
  return parts.join(' · ')
}

export function LayoutThumb({ layout }: { layout: LayoutLine }) {
  return (
    <span className="sl-thumb" aria-hidden="true">
      {layout.art_url ? <img src={layout.art_url} alt="" /> : <Icon name="dashboard" size={20} />}
    </span>
  )
}

function LayoutsBoard({ kind }: { kind?: 'stage' | 'layout' }) {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const navigate = useNavigate()
  const board = useBoard()
  const [creating, setCreating] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const base = `${adminBase}/stream-layouts`

  async function downloadAll() {
    setNote(null)
    try {
      const r = await api.GET('/api/admin/stream-layouts/obs-scenes').then(unwrap)
      saveFile(r.filename, r.content)
      setNote({ tone: 'info', text: 'In OBS: Scene Collection → Import, then put each capture into its "Feed ·" scene.' })
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    }
  }

  if (board.error && !board.data) {
    return (
      <>
        <PageHeader title="Stream Layouts" />
        <Banner tone="error" action={<Button variant="outline" onClick={board.reload}>Try again</Button>}>
          {board.error.message}
        </Banner>
      </>
    )
  }
  if (!board.data) return <Spinner label="Loading stream layouts" />
  const data: Board = board.data
  const zone = effectiveZone(data.zone ?? null)
  const openId = id ? Number(id) : null
  const stage = kind === 'stage' ? data.stages.find((s) => s.id === openId) ?? null : null
  const layout = kind === 'layout' ? data.layouts.find((l) => l.id === openId) ?? null : null
  if (!phone && !kind) {
    if (data.stages[0]) return <Navigate to={`${base}/stages/${data.stages[0].id}`} replace />
    if (data.layouts[0]) return <Navigate to={`${base}/layouts/${data.layouts[0].id}`} replace />
  }
  const open = !!kind

  const list = (
    <>
      <SideListCard label="Stages" title="Stages" more={data.stages.length === 0 ? 'No stages yet. Add one under Stages and it shows up here.' : undefined}>
        {data.stages.map((s) => (
          <SideListRow key={s.id} to={`${base}/stages/${s.id}`} title={s.name} sub={stageSub(s, zone)} />
        ))}
      </SideListCard>
      <SideListCard
        label="Layouts"
        title={phone ? 'Layouts (edit on a computer)' : 'Layouts'}
        more={data.layouts.length === 0 ? 'No layouts for this event yet. Press New layout to start one.' : undefined}
      >
        {data.layouts.map((l) => (
          <SideListRow key={l.id} to={`${base}/layouts/${l.id}`} title={l.name} sub={layoutSub(l)} leading={<LayoutThumb layout={l} />} />
        ))}
      </SideListCard>
    </>
  )

  const detail = stage ? (
    <StagePane key={`s${stage.id}`} stage={stage} layouts={data.layouts} zone={zone} onChange={board.reload} />
  ) : layout ? (
    <LayoutPane key={`l${layout.id}`} layout={layout} base={base} onChange={board.reload} onDeleted={() => { board.reload(); navigate(base) }} />
  ) : open ? (
    <EmptyState icon="info" title={kind === 'stage' ? "That stage isn't here" : "That layout isn't here"}>
      It may have been deleted, or belong to another event.
    </EmptyState>
  ) : (
    <EmptyState icon="live_tv" title="Nothing to show yet">
      Add a stage, or press New layout to design an overlay.
    </EmptyState>
  )

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="Stream Layouts"
          lede="Overlays OBS loads for each stage. Each one follows the match on its stage."
          actions={
            <>
              <Button icon="add" onClick={() => setCreating(true)}>
                New layout
              </Button>
              <ActionsMenu label="Actions" items={[{ label: 'Download OBS scenes', icon: 'download', onSelect: () => void downloadAll() }]} />
            </>
          }
        />
      )}
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <SideListLayout list={list} detail={detail} open={open} backTo={base} backLabel="Stream Layouts" />
      {creating && (
        <NewLayoutDialog
          onClose={() => setCreating(false)}
          onCreated={(lid) => {
            setCreating(false)
            board.reload()
            navigate(`${base}/layouts/${lid}`)
          }}
        />
      )}
    </>
  )
}

function EditorRoute() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const location = useLocation()
  const back = location.pathname.replace(/\/edit$/, '')
  if (phone) return <Navigate to={back} replace />
  return <LayoutEditor layoutId={Number(id)} back={back} base={`${adminBase}/stream-layouts`} />
}

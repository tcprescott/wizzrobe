import { useState } from 'react'
import { Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, FormDialog, PageHeader, Spinner } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { SideList } from '../kit'
import { useAdmin } from '../kit/useAdminNav'
import './tournaments.css'
import { SIGNUP_LABEL, SIGNUP_TONE, useBoard, type Board, type BoardRow } from './data'
import { TournamentDetail } from './TournamentDetail'

type Filter = 'active' | 'inactive' | 'attention'

function needsAttention(row: BoardRow): boolean {
  return (row.bracket?.unmatched ?? 0) > 0
}

/** Admin → Tournaments: every tournament in this event, as a side list. */
export function TournamentsScreen() {
  return (
    <Routes>
      <Route index element={<TournamentsBoard />} />
      <Route path=":id/*" element={<TournamentsBoard />} />
    </Routes>
  )
}

function TournamentsBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useBoard()
  const [filter, setFilter] = useState<Filter>('active')
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const base = `${adminBase}/tournaments`

  if (error && !data) {
    return (
      <>
        <PageHeader title="Tournaments" />
        <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
          {error.message}
        </Banner>
      </>
    )
  }
  if (loading && !data) return <Spinner label="Loading tournaments" />
  if (!data) return null

  const openId = id ? Number(id) : null
  const open = data.rows.find((r) => r.id === openId) ?? null
  const counts = {
    active: data.rows.filter((r) => r.is_active).length,
    inactive: data.rows.filter((r) => !r.is_active).length,
    attention: data.rows.filter(needsAttention).length,
  }
  const q = query.trim().toLowerCase()
  const shown = data.rows
    .filter((r) => (filter === 'active' ? r.is_active : filter === 'inactive' ? !r.is_active : needsAttention(r)))
    .filter((r) => !q || r.name.toLowerCase().includes(q) || (r.tournament_format ?? '').toLowerCase().includes(q))

  if (!phone && openId == null && shown[0]) return <Navigate to={`${base}/${shown[0].id}`} replace />

  async function syncAll() {
    setSyncing(true)
    setNote(null)
    try {
      const results = await api.POST('/api/admin/tournaments/sync').then(unwrap)
      const failed = results.filter((r) => r.error)
      if (results.length === 0) setNote({ tone: 'error', text: 'No tournaments here are linked to a bracket site.' })
      else
        setNote({
          tone: failed.length ? 'error' : 'info',
          text:
            `Synced ${results.length - failed.length} of ${results.length} brackets.` +
            (failed.length ? ` Failed: ${failed.map((r) => `${r.tournament_name}: ${r.error}`).join('; ')}` : ''),
        })
      reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setSyncing(false)
    }
  }

  const header = (
    <PageHeader
      title="Tournaments"
      lede={phone ? 'Tap a tournament to open it.' : undefined}
      actions={
        <>
          {data.can_create && (
            <Button icon="add" onClick={() => setAdding(true)}>
              Add tournament
            </Button>
          )}
          <ActionsMenu
            items={[{ label: syncing ? 'Syncing brackets…' : 'Sync every bracket', icon: 'sync', onSelect: () => void syncAll(), disabled: syncing }]}
          />
        </>
      }
    />
  )

  const toolbar = (
    <>
      <div className="filter-chips" role="group" aria-label="Show">
        {(
          [
            ['active', 'Active'],
            ['inactive', 'Inactive'],
            ['attention', 'Needs attention'],
          ] as const
        ).map(([key, label]) => (
          <button key={key} type="button" className="filter-chip" aria-pressed={filter === key} onClick={() => setFilter(key)}>
            {label} · {counts[key]}
          </button>
        ))}
      </div>
      <label className="field">
        <span className="visually-hidden">Search tournaments</span>
        <input className="input" type="search" placeholder="Search tournaments" value={query} onChange={(e) => setQuery(e.target.value)} />
      </label>
    </>
  )

  return (
    <>
      {!(phone && open) && header}
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <SideList
        label="Tournaments"
        items={shown}
        itemKey={(r) => r.id}
        itemTo={(r) => `${base}/${r.id}`}
        title={(r) => r.name}
        sub={(r) => <RowChips row={r} />}
        openKey={open ? open.id : openId != null ? openId : null}
        back={{ to: base, label: 'Tournaments' }}
        toolbar={toolbar}
        detail={
          open ? (
            <TournamentDetail key={open.id} row={open} board={data} reloadBoard={reload} base={`${base}/${open.id}`} />
          ) : (
            <EmptyState icon="info" title="That tournament isn't here">
              It may be in another event, or you may not run it.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="trophy" title="Pick a tournament">
            Its settings, players, bracket and round schedule open here.
          </EmptyState>
        }
        empty={
          <EmptyState icon="trophy" title={data.rows.length ? 'Nothing matches' : 'No tournaments yet'}>
            {data.rows.length ? 'Try another filter or search.' : data.can_create ? 'Add one to get started.' : ''}
          </EmptyState>
        }
      />
      {adding && <AddTournament board={data} onClose={() => setAdding(false)} onAdded={(newId) => { reload(); navigate(`${base}/${newId}/settings`) }} />}
    </>
  )
}

export function RowChips({ row }: { row: BoardRow }) {
  return (
    <>
      {!row.is_active && <Chip>Inactive</Chip>}
      {row.is_test && <Chip tone="pending">Test</Chip>}
      <Chip tone={SIGNUP_TONE[row.signup_state]}>{SIGNUP_LABEL[row.signup_state]}</Chip>
      {row.bracket && <Chip tone="info">{row.bracket.label + (row.bracket.stage ? ` · ${row.bracket.stage}` : '')}</Chip>}
      {(row.bracket?.unmatched ?? 0) > 0 && <Chip tone="pending">{row.bracket?.unmatched} unmatched</Chip>}
      <span>
        {row.player_count} {row.player_count === 1 ? 'player' : 'players'}
      </span>
    </>
  )
}

function AddTournament({ onClose, onAdded }: { board: Board; onClose: () => void; onAdded: (id: number) => void }) {
  const [name, setName] = useState('')
  const [format, setFormat] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function add() {
    setBusy(true)
    setError(null)
    try {
      const created = await api
        .POST('/api/tournaments', { body: { name, tournament_format: format || null } as Schemas['TournamentCreateRequest'] })
        .then(unwrap)
      onAdded(created.id)
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
      title="Add tournament"
      description="Name it now; seeds, signups and the bracket are set on its page."
      primaryLabel="Add tournament"
      onPrimary={() => void add()}
      busy={busy}
      busyLabel="Adding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Format</span>
          <input className="input" value={format} placeholder="e.g. Double elimination" onChange={(e) => setFormat(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

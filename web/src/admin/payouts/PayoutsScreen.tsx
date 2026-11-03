import { useState } from 'react'
import { Navigate, Route, Routes, useParams } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, EmptyState, PageHeader, Spinner } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { message } from '../../me/data'
import { SideList, UnsavedChangesGuard, useAdmin, useAdminQuery } from '../kit'
import { money, STATUS_TONE, type PayoutRow } from './data'
import { PayoutEditor } from './PayoutEditor'
import { SgliveDialog } from './SgliveDialog'
import './payouts.css'

/** Admin → Payouts: each tournament as a side list; the open one is its own editor. */
export function PayoutsScreen() {
  return (
    <UnsavedChangesGuard>
      <Routes>
        <Route index element={<PayoutsBoard />} />
        <Route path=":id" element={<PayoutsBoard />} />
      </Routes>
    </UnsavedChangesGuard>
  )
}

function rowSub(t: PayoutRow): string {
  const parts = [t.has_pool ? money(Math.round(Number(t.total) * 100)) : 'No pool yet']
  if (t.place_count) parts.push(`${t.place_count} ${t.place_count === 1 ? 'place' : 'places'}`)
  if (t.sglive_slug) parts.push('sglive')
  return parts.join(' · ')
}

function PayoutsBoard() {
  const { id } = useParams()
  const { adminBase } = useAdmin()
  const phone = usePhone()
  const board = useAdminQuery('payouts/board', () => api.GET('/api/admin/payouts/board').then(unwrap))
  const [linking, setLinking] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'warning' | 'error'; text: string } | null>(null)
  const [syncing, setSyncing] = useState(false)
  const base = `${adminBase}/payouts`

  async function syncNow() {
    setSyncing(true)
    setNote(null)
    try {
      const r = await api.POST('/api/admin/payouts/sglive/sync').then(unwrap)
      setNote(
        r.missing.length
          ? { tone: 'warning', text: `sglive no longer lists ${r.missing.join(', ')}. Those pools kept their last amount.` }
          : { tone: 'info', text: 'Prize pools updated from sglive.' },
      )
      board.reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setSyncing(false)
    }
  }

  if (board.error && !board.data) {
    return (
      <>
        <PageHeader title="Payouts" />
        <Banner tone="error" action={<Button variant="outline" onClick={board.reload}>Try again</Button>}>
          {board.error.message}
        </Banner>
      </>
    )
  }
  if (!board.data) return <Spinner label="Loading payouts" />

  const all = board.data.tournaments
  const openId = id ? Number(id) : null
  const open = all.find((t) => t.id === openId) ?? null
  if (!phone && openId == null && all[0]) return <Navigate to={`${base}/${all[0].id}`} replace />

  return (
    <>
      {!(phone && open) && (
        <PageHeader
          title="Payouts"
          lede="What each place wins, as a share of the pool or a fixed amount."
          actions={
            <ActionsMenu
              label="Actions"
              items={[
                { label: 'Link sglive pages…', icon: 'link', onSelect: () => setLinking(true) },
                { label: syncing ? 'Syncing from sglive…' : 'Sync pools from sglive now', icon: 'sync', disabled: syncing, onSelect: () => void syncNow() },
              ]}
            />
          }
        />
      )}
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <SideList
        label="Tournaments"
        items={all}
        itemKey={(t) => t.id}
        itemTo={(t) => `${base}/${t.id}`}
        title={(t) => t.name}
        sub={(t) => (
          <span className="side-list__meta">
            <Chip tone={STATUS_TONE[t.status] ?? 'neutral'}>{t.status_label}</Chip>
            <span>{rowSub(t)}</span>
          </span>
        )}
        openKey={openId}
        back={{ to: base, label: 'Payouts' }}
        detail={
          open ? (
            <PayoutEditor key={open.id} row={open} zone={board.data.zone ?? null} onSaved={board.reload} />
          ) : (
            <EmptyState icon="info" title="That tournament isn't here">
              It may have been deleted, or you may not run its payouts.
            </EmptyState>
          )
        }
        placeholder={
          <EmptyState icon="payments" title="Pick a tournament">
            Its pool and split open here, ready to edit.
          </EmptyState>
        }
        empty={
          <EmptyState icon="payments" title="No tournaments to pay out">
            Tournaments you run show up here once they exist in this event.
          </EmptyState>
        }
      />
      {linking && (
        <SgliveDialog
          onClose={() => setLinking(false)}
          onSaved={() => {
            setLinking(false)
            setNote({ tone: 'info', text: 'sglive links saved. Linked pools update every 15 minutes.' })
            board.reload()
          }}
        />
      )}
    </>
  )
}

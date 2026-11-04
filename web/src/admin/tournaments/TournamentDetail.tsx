import { useState } from 'react'
import { NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { api, unwrap } from '../../api/client'
import { ActionsMenu, Banner, Chip, FormDialog, type MenuItem } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { useAdmin } from '../kit'
import { done, message } from '../../me/data'
import { BracketTab } from './BracketTab'
import { SIGNUP_LABEL, SIGNUP_TONE, useAdminQuery, type Board, type BoardRow } from './data'
import { PlayersTab } from './PlayersTab'
import { RacetimeTab } from './RacetimeTab'
import { RoundScheduleTab } from './RoundScheduleTab'
import { SettingsTab } from './SettingsTab'

interface Props {
  row: BoardRow
  board: Board
  reloadBoard: () => void
  /** `<adminBase>/tournaments/<id>`. */
  base: string
}

/** One tournament: header, Actions, and its four tabs. */
export function TournamentDetail({ row, board, reloadBoard, base }: Props) {
  const [deleting, setDeleting] = useState(false)
  const Heading = usePhone() ? 'h1' : 'h2'
  const racetime = useAdmin().nav.areas.some((a) => a.slug === 'racetime')
  const tabs = [
    { slug: 'settings', label: 'Settings' },
    ...(racetime ? [{ slug: 'racetime', label: 'Racetime' }] : []),
    { slug: 'players', label: `Players · ${row.player_count}` },
    { slug: 'bracket', label: 'Bracket' },
    ...(row.has_round_schedule ? [{ slug: 'schedule', label: 'Round schedule' }] : []),
  ]
  const links: MenuItem[] = []
  if (row.rules_url) links.push({ label: 'Open rules', icon: 'open_in_new', onSelect: () => window.open(row.rules_url!, '_blank', 'noopener') })
  const bracketUrl = row.bracket?.url ?? row.bracket_url
  if (bracketUrl)
    links.push({
      label: row.bracket ? `Open on ${row.bracket.label}` : 'Open bracket',
      icon: 'open_in_new',
      onSelect: () => window.open(bracketUrl, '_blank', 'noopener'),
    })
  if (row.can_delete) links.push({ label: 'Delete tournament…', icon: 'delete', destructive: true, onSelect: () => setDeleting(true) })

  return (
    <>
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>{row.name}</Heading>
          <div className="detail-head__sub">
            <span>{row.tournament_format || 'No format set'}</span>
            <Chip tone={SIGNUP_TONE[row.signup_state]}>{SIGNUP_LABEL[row.signup_state]}</Chip>
            {!row.is_active && <Chip>Inactive</Chip>}
            {row.is_test && <Chip tone="pending">Test</Chip>}
          </div>
        </div>
        {links.length > 0 && <ActionsMenu items={links} label="Actions" />}
      </div>
      <nav className="detail-tabs" aria-label={`${row.name} sections`}>
        {tabs.map((t) => (
          <NavLink key={t.slug} to={`${base}/${t.slug}`}>
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Routes>
        <Route index element={<Navigate to="settings" replace />} />
        <Route path="settings" element={<SettingsTab row={row} zone={board.zone ?? null} onSaved={reloadBoard} />} />
        {racetime && <Route path="racetime" element={<RacetimeTab row={row} base={base} />} />}
        <Route path="players" element={<PlayersTab row={row} zone={board.zone ?? null} onChange={reloadBoard} />} />
        <Route path="bracket" element={<BracketTab row={row} board={board} onChange={reloadBoard} />} />
        <Route path="schedule" element={<RoundScheduleTab row={row} zone={board.zone ?? null} />} />
        <Route path="*" element={<Navigate to="settings" replace />} />
      </Routes>
      {deleting && <DeleteTournament row={row} onClose={() => setDeleting(false)} onDeleted={reloadBoard} />}
    </>
  )
}

const PHRASE = 'permanently delete'

function DeleteTournament({ row, onClose, onDeleted }: { row: BoardRow; onClose: () => void; onDeleted: () => void }) {
  const preview = useAdminQuery(`tournaments/${row.id}/deletion-preview`, () =>
    api.GET('/api/admin/tournaments/{tournament_id}/deletion-preview', { params: { path: { tournament_id: row.id } } }).then(unwrap),
  )
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  async function remove() {
    if (typed.trim().toLowerCase() !== PHRASE) {
      setError(`Type "${PHRASE}" to confirm.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      done(
        await api.DELETE('/api/tournaments/{tournament_id}', {
          params: { path: { tournament_id: row.id }, query: { confirmation: PHRASE } },
        }),
      )
      onClose()
      onDeleted()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  const p = preview.data
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Delete ${row.name}?`}
      description="This can't be undone."
      primaryLabel="Delete tournament"
      destructive
      onPrimary={() => void remove()}
      busy={busy}
      busyLabel="Deleting…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <p style={{ margin: 0 }}>
          {p
            ? `It takes ${p.matches} matches, ${p.players} players, ${p.brackets} brackets and ${p.triforce_texts} triforce texts with it.`
            : 'Counting what goes with it…'}
        </p>
        <label className="field">
          <span className="field__label">Type “{PHRASE}” to confirm</span>
          <input className="input" value={typed} autoComplete="off" onChange={(e) => setTyped(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

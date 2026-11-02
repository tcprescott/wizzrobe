import { useState, type ReactNode } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { ActionsMenu, Banner, Button, Chip, FormDialog, Panel, PanelRow, Spinner, type MenuItem } from '../../components'
import { usePhone } from '../../lib/usePhone'
import { done, message } from '../../me/data'
import { useAdminQuery } from '../kit'
import { STATE_TONE, type StageDetail, type StageMatch, type StageRow } from './data'
import { ResultDialog } from './ResultDialog'
import { RosterPanel, SeedsPanel } from './SeedsPanel'
import { StageForm } from './StageForm'

type Note = { tone: 'info' | 'error'; text: string } | null
type Confirm = 'start' | 'complete' | 'advance' | 'cancel' | 'delete' | null

interface Props {
  row: StageRow
  stages: StageRow[]
  formats: Schemas['BracketFormatOption'][]
  onBoardChange: () => void
  onDeleted: () => void
}

/** One stage: its next step first, then only what that step needs. */
export function StageDetailPane({ row, stages, formats, onBoardChange, onDeleted }: Props) {
  const Heading = usePhone() ? 'h1' : 'h2'
  const detail = useAdminQuery(`brackets/${row.id}`, () =>
    api.GET('/api/admin/brackets/{bracket_id}', { params: { path: { bracket_id: row.id } } }).then(unwrap),
  )
  const [note, setNote] = useState<Note>(null)
  const [confirm, setConfirm] = useState<Confirm>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [reporting, setReporting] = useState<StageMatch | null>(null)

  const reload = () => {
    detail.reload()
    onBoardChange()
  }

  if (detail.error && !detail.data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={detail.reload}>Try again</Button>}>
        {detail.error.message}
      </Banner>
    )
  }
  if (!detail.data) return <Spinner label="Loading the stage" />
  const d: StageDetail = detail.data
  const r = d.row
  const next = stages.find((s) => s.stage_order === r.stage_order + 1)
  const waiting = d.roster.filter((x) => !x.enrolled && x.status === 'active')

  async function run(call: () => Promise<unknown>, text: string) {
    setBusy(true)
    setNote(null)
    try {
      await call()
      setNote({ tone: 'info', text })
      setConfirm(null)
      reload()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
      setConfirm(null)
    } finally {
      setBusy(false)
    }
  }
  const path = { params: { path: { bracket_id: r.id } } }

  async function enrollEveryone() {
    await run(async () => {
      for (const x of waiting) {
        await api.POST('/api/brackets/{bracket_id}/entries', { ...path, body: { entrant_id: x.id } }).then(unwrap)
      }
    }, `Enrolled ${waiting.length} from the roster.`)
  }

  const firstOpen = d.matches.find((m) => m.state === 'open') ?? null
  const stepButtons: ReactNode = (() => {
    switch (r.next_step.key) {
      case 'enroll':
      case 'start':
        return (
          <>
            {r.next_step.key === 'start' && (
              <Button icon="check" onClick={() => setConfirm('start')}>
                Start bracket
              </Button>
            )}
            {waiting.length > 0 && (
              <Button variant={r.next_step.key === 'enroll' ? 'primary' : 'outline'} onClick={() => void enrollEveryone()} busy={busy} busyLabel="Enrolling…">
                Enroll everyone ({waiting.length})
              </Button>
            )}
          </>
        )
      case 'report':
        return firstOpen ? <Button onClick={() => setReporting(firstOpen)}>Report a result</Button> : null
      case 'complete':
        return <Button onClick={() => setConfirm('complete')}>Complete stage</Button>
      case 'advance':
        return <Button onClick={() => setConfirm('advance')}>Advance to {next?.name ?? 'the next stage'}</Button>
      default:
        return null
    }
  })()

  const menu: MenuItem[] = []
  if (r.state === 'draft') menu.push({ label: 'Edit stage…', icon: 'tune', onSelect: () => setEditing(true) })
  if (r.state === 'active' || r.state === 'draft') menu.push({ label: 'Cancel stage…', icon: 'close', onSelect: () => setConfirm('cancel') })
  if (r.state === 'draft' || r.state === 'cancelled') menu.push({ label: 'Delete stage…', icon: 'delete', destructive: true, onSelect: () => setConfirm('delete') })

  const pct = r.next_step.total ? Math.min(100, Math.round((r.next_step.done / r.next_step.total) * 100)) : 0
  const results = d.matches.filter((m) => m.state === 'complete')
  const open = d.matches.filter((m) => m.state === 'open')

  return (
    <>
      <div className="detail-head">
        <div className="detail-head__text">
          <Heading>
            {d.tournament_name} · {r.name}
          </Heading>
          <div className="detail-head__sub">
            <Chip tone={STATE_TONE[r.state]}>{r.state_label}</Chip>
            <span>
              {r.stage_label} · {r.format_label}
              {r.config.length ? ` · ${r.config.join(' · ')}` : ''}
            </span>
          </div>
        </div>
        {menu.length > 0 && <ActionsMenu label="Actions" items={menu} />}
      </div>
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <section className="next-step" aria-label="Next step">
        <span className="next-step__kicker">{r.next_step.key ? 'Next step' : 'Status'}</span>
        <span className="next-step__title">{r.next_step.title}</span>
        {r.next_step.total > 0 && (
          <div className="next-step__meter" role="progressbar" aria-label={r.next_step.detail} aria-valuemin={0} aria-valuemax={r.next_step.total} aria-valuenow={r.next_step.done}>
            <span style={{ width: `${pct}%` }} />
          </div>
        )}
        <span className="t-row__sub">{r.next_step.detail}</span>
        {stepButtons && <div className="t-actions">{stepButtons}</div>}
      </section>

      {r.state === 'draft' ? (
        <>
          <SeedsPanel stage={d} onChange={reload} onNote={setNote} />
          <RosterPanel stage={d} onChange={reload} onNote={setNote} />
        </>
      ) : (
        <>
          {open.length > 0 && (
            <Panel title={`Open matches · ${open.length}`}>
              {open.map((m) => (
                <MatchRow key={m.id} match={m} onReport={() => setReporting(m)} />
              ))}
            </Panel>
          )}
          {results.length > 0 && (
            <Panel title={`Results · ${results.length}`}>
              {results.map((m) => (
                <MatchRow key={m.id} match={m} onReport={() => setReporting(m)} />
              ))}
            </Panel>
          )}
        </>
      )}

      {editing && (
        <StageForm
          tournaments={[{ id: r.tournament_id, name: d.tournament_name, is_active: true, stages }]}
          formats={formats}
          stage={r}
          onClose={() => setEditing(false)}
          onSaved={() => reload()}
        />
      )}
      {reporting && (
        <ResultDialog
          match={reporting}
          onClose={() => setReporting(null)}
          onSaved={(text) => {
            setReporting(null)
            setNote({ tone: 'info', text })
            reload()
          }}
        />
      )}
      {confirm && (
        <ConfirmStep
          kind={confirm}
          row={r}
          next={next}
          detail={d}
          busy={busy}
          onClose={() => setConfirm(null)}
          onConfirm={() => {
            if (confirm === 'start')
              void run(() => api.POST('/api/brackets/{bracket_id}/start', path).then(unwrap), `${r.name} has started. Open matches are ready to book.`)
            if (confirm === 'complete')
              void run(() => api.POST('/api/brackets/{bracket_id}/complete', { ...path, body: null }).then(unwrap), `${r.name} is complete.`)
            if (confirm === 'cancel')
              void run(() => api.POST('/api/brackets/{bracket_id}/cancel', path).then(unwrap), `${r.name} is cancelled.`)
            if (confirm === 'advance')
              void run(
                () =>
                  api
                    .POST('/api/brackets/advance-stage', {
                      params: { query: { tournament_id: r.tournament_id } },
                      body: { from_stage_order: r.stage_order },
                    })
                    .then(unwrap),
                `The field advanced into ${next?.name ?? 'the next stage'}.`,
              )
            if (confirm === 'delete')
              void (async () => {
                setBusy(true)
                try {
                  done(await api.DELETE('/api/brackets/{bracket_id}', path))
                  setConfirm(null)
                  onDeleted()
                } catch (err) {
                  setNote({ tone: 'error', text: message(err) })
                  setConfirm(null)
                } finally {
                  setBusy(false)
                }
              })()
          }}
        />
      )}
    </>
  )
}

function MatchRow({ match: m, onReport }: { match: StageMatch; onReport: () => void }) {
  const won = (entry: number | null | undefined) => m.state === 'complete' && entry != null && entry === m.winner_entry_id
  const score = m.state === 'complete' && m.side1.score != null && m.side2.score != null ? `${m.side1.score}–${m.side2.score}` : null
  return (
    <PanelRow>
      <div className="t-row">
        <span className="t-row__text">
          <span className="t-row__title">
            <span className={won(m.side1.entry_id) ? 'b-winner' : undefined}>{m.side1.name}</span>
            <span className="t-row__sub"> vs </span>
            <span className={won(m.side2.entry_id) ? 'b-winner' : undefined}>{m.side2.name}</span>
          </span>
          <span className="t-row__sub">
            {m.label}
            {m.best_of ? ` · best of ${m.best_of}` : ''}
            {m.forfeit ? ' · forfeit' : ''}
          </span>
        </span>
        {m.state === 'open' ? (
          <Button size="sm" variant="outline" onClick={onReport}>
            Report
          </Button>
        ) : (
          <>
            {score && <Chip tone="ok">{score}</Chip>}
            <Button size="sm" variant="ghost" onClick={onReport}>
              Correct
            </Button>
          </>
        )}
      </div>
    </PanelRow>
  )
}

function ConfirmStep({
  kind,
  row,
  next,
  detail,
  busy,
  onClose,
  onConfirm,
}: {
  kind: Exclude<Confirm, null>
  row: StageRow
  next: StageRow | undefined
  detail: StageDetail
  busy: boolean
  onClose: () => void
  onConfirm: () => void
}) {
  const preview = useAdminQuery(kind === 'advance' ? `brackets/${row.id}/advance-preview` : null, () =>
    api
      .GET('/api/brackets/advancing-preview', { params: { query: { tournament_id: row.tournament_id, from_stage_order: row.stage_order } } })
      .then(unwrap),
  )
  const names = new Map(detail.entries.map((e) => [e.id, e.name]))
  const copy: Record<Exclude<Confirm, null>, { title: string; label: string; body: ReactNode; destructive?: boolean }> = {
    start: {
      title: `Start ${row.name}?`,
      label: 'Start bracket',
      body: `This draws the bracket from the ${detail.entries.length} enrolled entrants and their seeds. You can't change who's in or the seeds after.`,
    },
    complete: {
      title: `Complete ${row.name}?`,
      label: 'Complete stage',
      body: 'This ranks the field by the stage’s tiebreakers and locks its results.',
    },
    advance: {
      title: `Advance into ${next?.name ?? 'the next stage'}?`,
      label: 'Advance',
      body: !preview.data ? (
        <Spinner label="Working out who advances" />
      ) : (
        <>
          <p style={{ margin: 0 }}>These {preview.data.length} go through, in seed order:</p>
          <ol style={{ margin: 0 }}>
            {preview.data.map((e) => (
              <li key={e.id}>{names.get(e.id) ?? `Entry ${e.id}`}</li>
            ))}
          </ol>
        </>
      ),
    },
    cancel: {
      title: `Cancel ${row.name}?`,
      label: 'Cancel stage',
      body: 'Results so far are kept, but nobody is ranked and the stage stops taking results.',
      destructive: true,
    },
    delete: {
      title: `Delete ${row.name}?`,
      label: 'Delete stage',
      body: "This can't be undone.",
      destructive: true,
    },
  }
  const c = copy[kind]
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={c.title}
      primaryLabel={c.label}
      destructive={c.destructive}
      busy={busy}
      busyLabel="Working…"
      onPrimary={onConfirm}
    >
      <div className="t-form">{typeof c.body === 'string' ? <p style={{ margin: 0 }}>{c.body}</p> : c.body}</div>
    </FormDialog>
  )
}

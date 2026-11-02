import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import {
  Banner,
  Button,
  Chip,
  EmptyState,
  FormDialog,
  Icon,
  RowMenu,
  Spinner,
  type MenuItem,
  type Tone,
} from '../../components'
import { effectiveZone, formatDayTime, formatTime } from '../../lib/time'
import { actions, usePane, type Board, type Pane } from './data'
import { matchupText, metaText } from './MatchScheduleScreen'

interface PaneProps {
  matchId: number
  board: Board | undefined
  onChanged: () => void
  onGone: () => void
  /** Desktop: where the pane's close button goes. */
  closeTo?: string
  /** On a phone the pane is the whole screen, so its title is the page's heading. */
  standalone?: boolean
}

const STEP_LABEL: Record<string, string> = {
  scheduled: 'Scheduled',
  checked_in: 'Checked in',
  started: 'Started',
  finished: 'Finished',
}

/** One match beside the board (its own screen on a phone): run it, check it, staff its crew. */
export function MatchPane({ matchId, board, onChanged, onGone, closeTo, standalone = false }: PaneProps) {
  const { data, error, loading, reload } = usePane(matchId)
  const [busy, setBusy] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [dialog, setDialog] = useState<null | 'rewind' | 'calloff' | 'delete' | 'stations'>(null)

  if (error && !data) {
    return (
      <Banner tone="error" action={<Button variant="outline" onClick={reload}>Try again</Button>}>
        {error.message}
      </Banner>
    )
  }
  if (loading && !data) return <Spinner label="Loading the match" />
  if (!data) return null
  const m = data
  const zone = effectiveZone(m.zone)

  async function run(key: string, call: () => Promise<unknown>, gone = false) {
    setBusy(key)
    setFailure(null)
    try {
      await call()
      setDialog(null)
      if (gone) onGone()
      else reload()
      onChanged()
    } catch (err) {
      setFailure(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const menu: MenuItem[] = []
  if (m.can.run && m.seed_url && m.state !== 'Finished' && m.state !== 'Confirmed') {
    menu.push({ label: 'Clear seed', icon: 'restart_alt', onSelect: () => run('clear-seed', () => actions.clearSeed(m.id)) })
  }
  if (m.can.edit && m.rewind_to.length > 0) {
    menu.push({ label: 'Rewind…', icon: 'undo', onSelect: () => setDialog('rewind') })
  }
  if (m.can.edit && m.state !== 'Confirmed') {
    menu.push({ label: 'Call off match…', icon: 'block', destructive: true, onSelect: () => setDialog('calloff') })
  }
  if (m.can.edit) {
    menu.push({ label: 'Delete match…', icon: 'delete', destructive: true, onSelect: () => setDialog('delete') })
  }

  const when = m.scheduled_at ? formatDayTime(m.scheduled_at, zone) : 'No time yet'
  const title = matchupText(m)

  return (
    <article className="ms-pane" aria-labelledby="ms-pane-title">
      <header className="ms-pane__head">
        <div className="ms-pane__top">
          <span className="ms-pane__meta">
            {metaText(m)} · {when}
          </span>
          <Chip tone={m.tone as Tone}>{m.status}</Chip>
          {menu.length > 0 && <RowMenu label={`More for ${title}`} items={menu} />}
          {closeTo && (
            <Button variant="ghost" size="sm" asChild>
              <NavLink to={closeTo}>Close</NavLink>
            </Button>
          )}
        </div>
        {standalone ? (
          <h1 className="ms-pane__title" id="ms-pane-title">
            {title}
          </h1>
        ) : (
          <h2 className="ms-pane__title" id="ms-pane-title">
            {title}
          </h2>
        )}
        {m.state_since && (
          <p className="ms-pane__since">
            {STEP_LABEL[m.state.toLowerCase().replace(' ', '_')] ?? m.state} at {formatTime(m.state_since, zone)}
          </p>
        )}
        {(m.late.length > 0 || m.players.some((p) => p.ada)) && (
          <div className="ms-row__tags">
            {m.late.map((l) => (
              <Chip key={l.label} tone="pending">
                {l.label}
              </Chip>
            ))}
            {m.players
              .filter((p) => p.ada)
              .map((p) => (
                <Chip key={p.id ?? p.name} tone="info">
                  ADA: {p.name}
                </Chip>
              ))}
          </div>
        )}
        {m.late.some((l) => l.note) && (
          <p className="ms-note">{m.late.filter((l) => l.note).map((l) => `${l.label}: ${l.note}`).join(' · ')}</p>
        )}
        {Object.entries(m.ada_notes ?? {}).map(([uid, note]) => (
          <p key={uid} className="ms-note">
            ADA, {m.players.find((p) => String(p.user_id) === uid)?.name}: {note}
          </p>
        ))}
        {failure && <Banner tone="error">{failure}</Banner>}
        <Primary pane={m} busy={busy} run={run} />
      </header>

      <div className="ms-pane__body">
        <h3 className="ms-pane__section">Ready to start</h3>
        <ul className="ms-check">
          {m.checklist.map((item) => (
            <li key={item.key} className="ms-check__item">
              <Icon
                name={item.ready === true ? 'check_circle' : item.ready === false ? 'error' : 'info'}
                className={item.ready === true ? 'ms-ok' : item.ready === false ? 'ms-warn' : 'ms-muted'}
              />
              <span className="ms-check__text">
                <span className="ms-check__label">{item.label}</span>
                <span className="ms-check__detail">{item.detail}</span>
              </span>
              <CheckAction pane={m} item={item.key} board={board} busy={busy} run={run} onStations={() => setDialog('stations')} />
            </li>
          ))}
        </ul>
        {m.seed_url && (
          <p className="ms-seed">
            <Icon name="casino" size={18} />
            <a href={m.seed_url} target="_blank" rel="noreferrer">
              Seed
            </a>
            {m.seed_code && <span className="ms-seed__code">{m.seed_code}</span>}
          </p>
        )}
        {m.seed_dm_blocked.length > 0 && m.seed_url && (
          <p className="ms-note">Couldn't DM {m.seed_dm_blocked.join(', ')}. Hand them the seed.</p>
        )}

        {m.crew.length > 0 && (
          <>
            <h3 className="ms-pane__section">Crew</h3>
            {m.crew.map((role) => {
              const approved = role.people.filter((p) => p.approved)
              const waiting = role.people.filter((p) => !p.approved)
              return (
                <div key={role.key} className="ms-role">
                  <div className="ms-role__head">
                    <span className="ms-role__label">{role.label}</span>
                    {role.short ? <Chip tone="pending">{role.count} · more needed</Chip> : <span className="ms-role__count">{role.count}</span>}
                  </div>
                  {approved.length > 0 ? (
                    <div className="ms-pills">
                      {approved.map((p) => (
                        <span key={p.id ?? p.name} className="ms-pill">
                          {p.name}
                          {p.with && <span className="ms-muted"> · with {p.with}</span>}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <p className="ms-note">{role.people.length ? 'Nobody approved yet' : 'No signups yet'}</p>
                  )}
                  {waiting.map((p) => (
                    <div key={p.id ?? p.name} className="ms-waiting">
                      <span>{p.name} signed up</span>
                      {m.can.crew && role.approvable && p.id && (
                        <span className="ms-waiting__acts">
                          <Button
                            variant="outline"
                            size="sm"
                            busy={busy === `crew-no-${p.id}`}
                            onClick={() => run(`crew-no-${p.id}`, () => actions.crew(role.key, p.id as number, false))}
                          >
                            Decline
                          </Button>
                          <Button
                            size="sm"
                            busy={busy === `crew-yes-${p.id}`}
                            onClick={() => run(`crew-yes-${p.id}`, () => actions.crew(role.key, p.id as number, true))}
                          >
                            Approve
                          </Button>
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )
            })}
          </>
        )}
        {(m.comments?.player || m.comments?.staff) && (
          <p className="ms-note">
            {(m.comments?.player ?? 0) + (m.comments?.staff ?? 0)} comments on this match. Reading and writing them here is
            coming next.
          </p>
        )}
      </div>

      <RewindDialog pane={m} open={dialog === 'rewind'} busy={busy} onClose={() => setDialog(null)} run={run} />
      <CallOffDialog pane={m} open={dialog === 'calloff'} busy={busy} onClose={() => setDialog(null)} run={run} />
      <FormDialog
        open={dialog === 'delete'}
        onOpenChange={(o) => !o && setDialog(null)}
        title="Delete this match?"
        size="sm"
        description={`${title} is removed with its crew and acknowledgments, and nobody is told. To tell everyone, call it off instead.`}
        primaryLabel="Delete match"
        destructive
        busy={busy === 'delete'}
        busyLabel="Deleting…"
        onPrimary={() => run('delete', () => actions.remove(m.id), true)}
      >
        {null}
      </FormDialog>
      <StationsDialog pane={m} open={dialog === 'stations'} busy={busy} onClose={() => setDialog(null)} run={run} />
    </article>
  )
}

type Run = (key: string, call: () => Promise<unknown>, gone?: boolean) => Promise<void>

function Primary({ pane: m, busy, run }: { pane: Pane; busy: string | null; run: Run }) {
  if (m.pending_result) return <p className="ms-note">{m.pending_result}</p>
  if (m.played_on_site && m.can.confirm) {
    const site = m.played_on_site
    const winner = m.players.find((p) => site.winner_user_id && p.user_id === site.winner_user_id)
    return (
      <div className="ms-primary">
        <p className="ms-note">
          {site.no_winner ? `Played on ${site.site} with no winner yet.` : `${site.site} says ${site.winner} won.`}
        </p>
        {winner?.id && (
          <Button busy={busy === 'site'} onClick={() => run('site', () => actions.winner(m.id, winner.id as number))}>
            Record {winner.name} won
          </Button>
        )}
      </div>
    )
  }
  if (m.racetime_held) {
    return (
      <div className="ms-primary">
        <p className="ms-note">
          {m.racetime_held.label}
          {m.racetime_held.detail ? `: ${m.racetime_held.detail}` : ''}
        </p>
        {m.racetime_held.url && (
          <Button variant="outline" icon="open_in_new" asChild>
            <a href={m.racetime_held.url} target="_blank" rel="noreferrer">
              Open race room
            </a>
          </Button>
        )}
      </div>
    )
  }
  if (m.state === 'Finished' && m.can.confirm) {
    return (
      <div className="ms-primary">
        <Button icon="check" busy={busy === 'confirm'} onClick={() => run('confirm', () => actions.confirm(m.id))}>
          {m.needs_review ? 'Review and confirm result' : 'Confirm result'}
        </Button>
      </div>
    )
  }
  const winners = m.winners ?? []
  if (winners.length > 0 && m.can.confirm) {
    return (
      <div className="ms-primary" role="group" aria-label="Who won?">
        <span className="ms-primary__ask">Who won?</span>
        {winners.map((w) => (
          <Button key={w.id} variant="outline" busy={busy === `win-${w.id}`} onClick={() => run(`win-${w.id}`, () => actions.winner(m.id, w.id))}>
            {w.name} won
          </Button>
        ))}
      </div>
    )
  }
  const step = m.next_step
  if (!step || !m.can.run) return null
  if (step.rolling) return <p className="ms-note">Rolling the seed…</p>
  const call: Record<string, () => Promise<unknown>> = {
    seat: () => actions.seat(m.id),
    roll: () => actions.roll(m.id),
    start: () => actions.start(m.id),
    finish: () => actions.finish(m.id),
  }
  const icon = { seat: 'how_to_reg', roll: 'casino', start: 'play_arrow', finish: 'check' } as const
  const event = step.event as keyof typeof icon | null
  const act = event ? call[event] : undefined
  if (!event || !act) return null
  return (
    <div className="ms-primary">
      <Button icon={icon[event]} busy={busy === event} onClick={() => run(event, act)}>
        {step.label}
      </Button>
    </div>
  )
}

function CheckAction({
  pane: m,
  item,
  board,
  busy,
  run,
  onStations,
}: {
  pane: Pane
  item: string
  board: Board | undefined
  busy: string | null
  run: Run
  onStations: () => void
}) {
  const open = m.state === 'Scheduled' || m.state === 'Checked In'
  if (item === 'stage' && m.can.stage && board && board.stages.length > 0) {
    return (
      <select
        className="input ms-select ms-select--sm"
        aria-label="Stage"
        value={m.stage_id ?? ''}
        disabled={busy === 'stage'}
        onChange={(e) => run('stage', () => actions.stage(m.id, Number(e.target.value) || null))}
      >
        <option value="">No stage</option>
        {board.stages.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    )
  }
  if (item === 'stations' && m.can.run && open && !m.is_racetime) {
    return (
      <Button variant="outline" size="sm" onClick={onStations}>
        {m.players.some((p) => p.station) ? 'Change' : 'Assign'}
      </Button>
    )
  }
  if (item === 'seed' && m.next_step?.event !== 'roll' && m.can.run && open && !m.seed_url && !m.seed_rolling && !m.is_racetime && m.players.length > 0) {
    return (
      <Button variant="outline" size="sm" busy={busy === 'roll'} busyLabel="Rolling…" onClick={() => run('roll', () => actions.roll(m.id))}>
        Roll seed
      </Button>
    )
  }
  return null
}

function RewindDialog({ pane: m, open, busy, onClose, run }: { pane: Pane; open: boolean; busy: string | null; onClose: () => void; run: Run }) {
  const [step, setStep] = useState(m.rewind_to[0]?.step ?? '')
  return (
    <FormDialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Rewind match"
      size="sm"
      primaryLabel="Rewind"
      busy={busy === 'rewind'}
      busyLabel="Rewinding…"
      onPrimary={() => run('rewind', () => actions.rewind(m.id, step))}
    >
      <fieldset className="ms-radios">
        <legend className="field__label">Take it back to</legend>
        {m.rewind_to.map((r) => (
          <label key={r.step} className="ms-radio">
            <input type="radio" name="rewind" checked={step === r.step} onChange={() => setStep(r.step)} />
            <span>
              <span className="ms-check__label">{STEP_LABEL[r.step] ?? r.step}</span>
              <span className="ms-check__detail">{r.explanation}</span>
            </span>
          </label>
        ))}
      </fieldset>
    </FormDialog>
  )
}

function CallOffDialog({ pane: m, open, busy, onClose, run }: { pane: Pane; open: boolean; busy: string | null; onClose: () => void; run: Run }) {
  const [reason, setReason] = useState('')
  return (
    <FormDialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Call off this match?"
      size="sm"
      description={`${matchupText(m)} is cancelled and its players, crew and watchers are told.`}
      primaryLabel="Call off match"
      destructive
      busy={busy === 'calloff'}
      busyLabel="Calling off…"
      onPrimary={() => run('calloff', () => actions.callOff(m.id, reason), true)}
    >
      <label className="field">
        <span className="field__label">Reason</span>
        <textarea className="input" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        <span className="field__hint">Optional. Sent with the message.</span>
      </label>
    </FormDialog>
  )
}

function StationsDialog({ pane: m, open, busy, onClose, run }: { pane: Pane; open: boolean; busy: string | null; onClose: () => void; run: Run }) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(m.players.filter((p) => p.id).map((p) => [String(p.id), p.station ?? ''])),
  )
  const [note, setNote] = useState<string | null>(null)
  async function suggest() {
    try {
      const out = await actions.suggestStations(m.id)
      setValues((prev) => ({ ...prev, ...Object.fromEntries(Object.entries(out.assignments).map(([k, v]) => [k, v])) }))
      setNote((out.relaxations ?? []).join(' ') || null)
    } catch (err) {
      setNote(err instanceof Error ? err.message : String(err))
    }
  }
  return (
    <FormDialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Stations"
      size="sm"
      primaryLabel="Save stations"
      busy={busy === 'stations'}
      busyLabel="Saving…"
      extra={[{ label: 'Suggest stations', icon: 'swap_horiz', onSelect: suggest }]}
      onPrimary={() =>
        run('stations', () =>
          actions.stations(m.id, Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim() || null]))),
        )
      }
    >
      <div className="ms-stack">
        {m.players
          .filter((p) => p.id)
          .map((p) => (
            <label key={p.id} className="field">
              <span className="field__label">{p.name}</span>
              <input
                className="input"
                inputMode="numeric"
                value={values[String(p.id)] ?? ''}
                onChange={(e) => setValues((prev) => ({ ...prev, [String(p.id)]: e.target.value }))}
              />
            </label>
          ))}
        {note && <p className="ms-note">{note}</p>}
      </div>
    </FormDialog>
  )
}

export function PaneEmpty() {
  return (
    <EmptyState icon="event" title="Pick a match">
      Its checklist, crew and run buttons open here.
    </EmptyState>
  )
}

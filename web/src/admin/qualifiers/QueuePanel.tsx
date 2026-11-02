import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Chip, CountBadge, FormDialog, Panel, PanelRow, RowMenu, type MenuItem } from '../../components'
import { formatDayTime } from '../../lib/time'
import { message } from '../../me/data'
import { hms, type QueueCard } from './data'

type Verdict = { card: QueueCard; approved: boolean } | null

/** Runs waiting for a verdict, oldest finish first; each one approves or rejects in place. */
export function QueuePanel({ cards, zone, onChange }: { cards: QueueCard[]; zone: string; onChange: () => void }) {
  const [verdict, setVerdict] = useState<Verdict>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const [busyRun, setBusyRun] = useState<number | null>(null)

  async function lock(card: QueueCard, claim: boolean) {
    setBusyRun(card.run_id)
    setNote(null)
    const params = { params: { path: { run_id: card.run_id } } }
    try {
      if (claim) await api.POST('/api/async-qualifiers/runs/{run_id}/claim', params).then(unwrap)
      else await api.POST('/api/async-qualifiers/runs/{run_id}/release', params).then(unwrap)
      onChange()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusyRun(null)
    }
  }

  return (
    <Panel
      title={
        <span className="q-queue-title">
          Waiting for review {cards.length > 0 && <CountBadge count={cards.length} label={`${cards.length} waiting`} />}
        </span>
      }
    >
      {note && (
        <PanelRow>
          <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
            {note.text}
          </Banner>
        </PanelRow>
      )}
      {cards.length === 0 && (
        <PanelRow>
          <span className="t-row__sub">Nothing waiting. New runs land here as runners submit them.</span>
        </PanelRow>
      )}
      {cards.map((c) => {
        const heldByOther = !!c.held_by && !c.held_by_me
        const items: MenuItem[] = []
        if (c.permalink_url) items.push({ label: 'Open the seed', icon: 'open_in_new', onSelect: () => window.open(c.permalink_url!, '_blank', 'noopener') })
        if (c.vod_url) items.push({ label: 'Open the VoD', icon: 'tv', onSelect: () => window.open(c.vod_url!, '_blank', 'noopener') })
        if (!c.held_by) items.push({ label: 'Claim it so nobody else reviews it', icon: 'lock', onSelect: () => void lock(c, true) })
        if (c.held_by) items.push({ label: heldByOther ? `Release ${c.held_by}'s claim` : 'Release my claim', icon: 'lock_open', onSelect: () => void lock(c, false) })
        const details = [
          c.pool,
          `timed ${hms(c.measured_seconds)}`,
          c.finished_at ? `finished ${formatDayTime(c.finished_at, zone)}` : null,
          c.vod_url ? 'VoD' : null,
        ].filter(Boolean)
        return (
          <PanelRow key={c.run_id}>
            <div className="q-card">
              <div className="q-card__head">
                <span className="t-row__text q-card__text">
                  <span className="t-row__title">
                    {c.runner} <span className="q-time">{hms(c.claimed_seconds)}</span>
                  </span>
                  <span className="t-row__sub">{details.join(' · ')}</span>
                </span>
                {heldByOther ? (
                  <span className="q-card__verdict">
                    <Chip tone="pending">{c.held_by} has this</Chip>
                  </span>
                ) : (
                  <span className="q-card__verdict">
                    <Button size="sm" variant="outline" disabled={busyRun === c.run_id} onClick={() => setVerdict({ card: c, approved: false })}>
                      Reject
                    </Button>
                    <Button size="sm" disabled={busyRun === c.run_id} onClick={() => setVerdict({ card: c, approved: true })}>
                      Approve
                    </Button>
                  </span>
                )}
                <span className="q-card__menu">
                  <RowMenu label={`More for ${c.runner}'s run`} items={items} />
                </span>
              </div>
              {(c.implausible || c.held_by_me || c.other_runs || c.notes.length > 0) && (
                <div className="q-card__extra">
                  {c.implausible && <Chip tone="pending">Claimed time is under the wall clock</Chip>}
                  {c.held_by_me && <Chip tone="info">You have this</Chip>}
                  {c.other_runs && <span className="t-row__sub">{c.other_runs}</span>}
                  {c.notes.map((n, i) => (
                    <span key={i} className="t-row__sub q-note">
                      Note: {n}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </PanelRow>
        )
      })}
      {verdict && (
        <VerdictDialog
          verdict={verdict}
          onClose={() => setVerdict(null)}
          onDone={(text) => {
            setVerdict(null)
            setNote({ tone: 'info', text })
            onChange()
          }}
        />
      )}
    </Panel>
  )
}

function VerdictDialog({ verdict, onClose, onDone }: { verdict: NonNullable<Verdict>; onClose: () => void; onDone: (text: string) => void }) {
  const { card, approved } = verdict
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
    if (!approved && !text.trim()) {
      setError("A rejection needs a reason. The runner is told what you write.")
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api
        .POST('/api/async-qualifiers/runs/{run_id}/review', {
          params: { path: { run_id: card.run_id } },
          body: { approved, note: text.trim() || null },
        })
        .then(unwrap)
      onDone(approved ? `Approved ${card.runner}'s ${hms(card.claimed_seconds)}.` : `Rejected ${card.runner}'s run. They've been told why.`)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(o) => !o && onClose()}
      title={`${approved ? 'Approve' : 'Reject'} ${card.runner}'s run`}
      description={`${hms(card.claimed_seconds)} on ${card.pool || 'their seed'}`}
      primaryLabel={approved ? 'Approve run' : 'Reject run'}
      destructive={!approved}
      onPrimary={() => void submit()}
      busy={busy}
      busyLabel="Saving…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">{approved ? 'Note (optional)' : 'Reason (shown to the runner)'}</span>
          <textarea className="input" rows={3} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

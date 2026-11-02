import { useState } from 'react'
import { api, unwrap } from '../api/client'
import { FormDialog } from '../components'
import type { Entry } from './data'

const CHOICES = [10, 20, 30]

interface Props {
  entry: Entry
  open: boolean
  onOpenChange: (open: boolean) => void
  onSent: (message: string) => void
}

/** Tell the match's staff you're on your way. Saying it again with a new number replaces the old one. */
export function RunningLateDialog({ entry, open, onOpenChange, onSent }: Props) {
  const [minutes, setMinutes] = useState(entry.late_minutes ?? 10)
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const shift = entry.kind === 'shift'

  async function send() {
    setBusy(true)
    setError(null)
    try {
      if (shift && entry.assignment_id != null) {
        await api
          .POST('/api/volunteers/me/assignments/{assignment_id}/running-late', {
            params: { path: { assignment_id: entry.assignment_id } },
            body: { minutes },
          })
          .then(unwrap)
      } else if (entry.match_id != null) {
        await api
          .POST('/api/matches/{match_id}/running-late', {
            params: { path: { match_id: entry.match_id } },
            body: { minutes, note: note.trim() || null },
          })
          .then(unwrap)
      } else return
      onOpenChange(false)
      onSent(`Sent. ${shift ? 'The coordinators' : 'Staff'} know you're about ${minutes} minutes away.`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Running late"
      description={`${entry.title} · ${entry.detail}`}
      primaryLabel="Send heads-up"
      busy={busy}
      busyLabel="Sending…"
      onPrimary={() => void send()}
      size="sm"
    >
      <div className="pick-time">
        <div className="field">
          <span className="field__label" id="late-label">
            How late?
          </span>
          <div className="segmented" role="radiogroup" aria-labelledby="late-label">
            {CHOICES.map((m) => (
              <span key={m}>
                <input
                  type="radio"
                  id={`late-${m}`}
                  name="late"
                  value={m}
                  checked={minutes === m}
                  onChange={() => setMinutes(m)}
                />
                <label htmlFor={`late-${m}`}>{m === 30 ? '30+ min' : `${m} min`}</label>
              </span>
            ))}
          </div>
        </div>
        {!shift && (
          <label className="field">
            <span className="field__label">Note for staff (optional)</span>
            <textarea className="input" maxLength={280} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </FormDialog>
  )
}

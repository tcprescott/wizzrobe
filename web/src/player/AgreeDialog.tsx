import { useState } from 'react'
import { api, unwrap } from '../api/client'
import { Button, Dialog, FormDialog } from '../components'
import { formatDayTime } from '../lib/time'
import type { Need } from './data'

interface Props {
  need: Need
  zone: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onAgreed: (message: string) => void
}

/** Your opponent asked staff to move or call off your match. Agreeing tells staff you're fine with it; they still decide. */
export function AgreeDialog({ need, zone, open, onOpenChange, onAgreed }: Props) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const body = (
    <div className="pick-time">
      <dl className="facts-list">
        {need.original_at && (
          <>
            <dt>Now</dt>
            <dd>{formatDayTime(need.original_at, zone)}</dd>
          </>
        )}
        <dt>{need.proposed_at ? 'Asked for' : 'Asked to'}</dt>
        <dd>{need.proposed_at ? formatDayTime(need.proposed_at, zone) : 'Call it off'}</dd>
        {need.reason && (
          <>
            <dt>Why</dt>
            <dd className="facts-list__free">{need.reason}</dd>
          </>
        )}
      </dl>
      <p className="field__hint">Staff make the call. Agreeing tells them you're fine with it.</p>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  )

  if (need.agreed) {
    return (
      <Dialog
        open={open}
        onOpenChange={onOpenChange}
        title={need.title}
        description={need.detail}
        size="sm"
        footer={
          <>
            <span className="dialog__spacer" />
            <Button variant="primary" onClick={() => onOpenChange(false)}>
              Done
            </Button>
          </>
        }
      >
        {body}
        <p>You agreed. Staff haven't decided yet.</p>
      </Dialog>
    )
  }

  async function agree() {
    if (need.request_id == null) return
    setBusy(true)
    setError(null)
    try {
      await api
        .POST('/api/reschedule-requests/{request_id}/agree', { params: { path: { request_id: need.request_id } } })
        .then(unwrap)
      onOpenChange(false)
      onAgreed('Agreed. Staff will decide and let you both know.')
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
      title={need.title}
      description={need.detail}
      primaryLabel="Agree"
      busy={busy}
      busyLabel="Saving…"
      cancelLabel="Not now"
      onPrimary={() => void agree()}
      size="sm"
    >
      {body}
    </FormDialog>
  )
}

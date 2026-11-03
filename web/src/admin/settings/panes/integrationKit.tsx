import { useState } from 'react'
import { unwrap } from '../../../api/client'
import { FormDialog } from '../../../components'

export function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

export const plural = (n: number, noun: string) => `${n} ${noun}${n === 1 ? '' : 's'}`

export function Notes({ note, failure }: { note?: string | null; failure?: string | null }) {
  if (!note && !failure) return null
  return (
    <div className="settings-card__body">
      {note && (
        <p className="settings-saved" role="status">
          {note}
        </p>
      )}
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
    </div>
  )
}

export function useAction(reload: () => void) {
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  async function run(action: () => Promise<string | null>): Promise<boolean> {
    setBusy(true)
    setFailure(null)
    setNote(null)
    try {
      setNote(await action())
      reload()
      return true
    } catch (err) {
      setFailure(message(err))
      return false
    } finally {
      setBusy(false)
    }
  }
  return { busy, note, failure, run, clearFailure: () => setFailure(null) }
}

export async function noContent(r: { response: Response; error?: unknown }) {
  if (!r.response.ok) unwrap(r as never)
}

export function DisconnectDialog({
  service,
  open,
  onOpenChange,
  description,
  busy,
  failure,
  onConfirm,
}: {
  service: string
  open: boolean
  onOpenChange: (open: boolean) => void
  description: string
  busy: boolean
  failure: string | null
  onConfirm: () => void
}) {
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Disconnect ${service}?`}
      description={description}
      primaryLabel="Disconnect"
      destructive
      busy={busy}
      busyLabel="Disconnecting…"
      onPrimary={onConfirm}
      size="sm"
    >
      {failure && (
        <p className="form-error" role="alert">
          {failure}
        </p>
      )}
    </FormDialog>
  )
}

export function ago(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  return `${plural(hours, 'hour')} ago`
}

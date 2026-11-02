import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, FormDialog } from '../../components'
import { message } from '../../me/data'
import { InstantField, NumberField } from '../kit'
import type { QualifierDetail, QualifierRow } from './data'

interface Draft {
  name: string
  description: string
  opens_at: string | null
  closes_at: string | null
  runs_per_pool: number | null
  allowed_reattempts: number | null
  is_active: boolean
}

function Fields({ draft, set, zone, editing }: { draft: Draft; set: (patch: Partial<Draft>) => void; zone: string | null; editing: boolean }) {
  return (
    <>
      <label className="field">
        <span className="field__label">Name</span>
        <input className="input" value={draft.name} placeholder="e.g. SGL27 ALTTPR Qualifier" onChange={(e) => set({ name: e.target.value })} />
      </label>
      <label className="field">
        <span className="field__label">Description</span>
        <textarea className="input" rows={3} value={draft.description} onChange={(e) => set({ description: e.target.value })} />
        <span className="field__hint">Shown to runners on the qualifier page.</span>
      </label>
      <div className="t-form__grid">
        <InstantField label="Opens" value={draft.opens_at} zone={zone} onChange={(v) => set({ opens_at: v })} hint="Leave empty to open now." />
        <InstantField label="Closes" value={draft.closes_at} zone={zone} onChange={(v) => set({ closes_at: v })} hint="Leave empty to never close." />
        <NumberField label="Runs per pool" min={1} value={draft.runs_per_pool} onChange={(n) => set({ runs_per_pool: n })} />
        <NumberField label="Reattempts allowed" value={draft.allowed_reattempts} onChange={(n) => set({ allowed_reattempts: n })} />
      </div>
      {editing && (
        <label className="t-check">
          <input type="checkbox" checked={draft.is_active} onChange={(e) => set({ is_active: e.target.checked })} />
          Taking runs (untick to switch the qualifier off)
        </label>
      )}
    </>
  )
}

/** Add a qualifier: name, window, and how many runs each runner gets. */
export function QualifierForm({ zone, onClose, onSaved }: { zone: string | null; onClose: () => void; onSaved: (id: number) => void }) {
  const [draft, setDraft] = useState<Draft>({
    name: '',
    description: '',
    opens_at: null,
    closes_at: null,
    runs_per_pool: 1,
    allowed_reattempts: 0,
    is_active: true,
  })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function save() {
    setBusy(true)
    setError(null)
    try {
      const q = await api
        .POST('/api/async-qualifiers', {
          body: {
            name: draft.name.trim(),
            description: draft.description.trim() || null,
            opens_at: draft.opens_at,
            closes_at: draft.closes_at,
            runs_per_pool: draft.runs_per_pool ?? 1,
            allowed_reattempts: draft.allowed_reattempts ?? 0,
          },
        })
        .then(unwrap)
      onSaved(q.id)
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
      title="Add qualifier"
      description="Add pools and seeds next; runners can't start until a pool has a seed."
      primaryLabel="Add qualifier"
      onPrimary={() => void save()}
      busy={busy}
      busyLabel="Adding…"
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <Fields draft={draft} set={(p) => setDraft({ ...draft, ...p })} zone={zone} editing={false} />
      </div>
    </FormDialog>
  )
}

/** The Settings page: the same fields, saved in place. */
export function SettingsPage({ row, detail, zone, onSaved }: { row: QualifierRow; detail: QualifierDetail; zone: string | null; onSaved: () => void }) {
  const initial: Draft = {
    name: row.name,
    description: detail.settings.description ?? '',
    opens_at: row.opens_at ?? null,
    closes_at: row.closes_at ?? null,
    runs_per_pool: detail.settings.runs_per_pool,
    allowed_reattempts: detail.settings.allowed_reattempts,
    is_active: detail.settings.is_active,
  }
  const [draft, setDraft] = useState<Draft>(initial)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)

  async function save() {
    setBusy(true)
    setNote(null)
    const clearing = (initial.opens_at && !draft.opens_at) || (initial.closes_at && !draft.closes_at)
    try {
      await api
        .PATCH('/api/async-qualifiers/{qualifier_id}', {
          params: { path: { qualifier_id: row.id } },
          body: {
            name: draft.name.trim(),
            description: draft.description.trim(),
            opens_at: draft.opens_at,
            closes_at: draft.closes_at,
            clear_window: !!clearing,
            runs_per_pool: draft.runs_per_pool,
            allowed_reattempts: draft.allowed_reattempts,
            is_active: draft.is_active,
          },
        })
        .then(unwrap)
      setNote({ tone: 'info', text: 'Saved.' })
      onSaved()
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="t-form">
      {note && <Banner tone={note.tone}>{note.text}</Banner>}
      <Fields draft={draft} set={(p) => setDraft({ ...draft, ...p })} zone={zone} editing />
      <div className="t-actions">
        <Button variant="outline" onClick={() => setDraft(initial)} disabled={busy}>
          Undo changes
        </Button>
        <Button onClick={() => void save()} busy={busy} busyLabel="Saving…">
          Save
        </Button>
      </div>
    </div>
  )
}

import { useState } from 'react'
import { api, unwrap } from '../../api/client'
import { Banner, Button, Chip, FormDialog, Panel, PanelRow, RowMenu, Spinner } from '../../components'
import { done, message } from '../../me/data'
import { NumberField, useAdminQuery } from '../kit'
import { hms, type PoolLine } from './data'

type Dialog = { kind: 'add-pool' } | { kind: 'paste'; pool: PoolLine } | { kind: 'roll'; pool: PoolLine } | { kind: 'delete'; pool: PoolLine } | null

/** Each pool and its seeds: add a pool, paste or roll seeds, remove one. */
export function PoolsPage({ qualifierId, onChange }: { qualifierId: number; onChange: () => void }) {
  const pools = useAdminQuery(`qualifiers/${qualifierId}/pools`, () =>
    api.GET('/api/admin/qualifiers/{qualifier_id}/pools', { params: { path: { qualifier_id: qualifierId } } }).then(unwrap),
  )
  const [dialog, setDialog] = useState<Dialog>(null)
  const [note, setNote] = useState<{ tone: 'info' | 'error'; text: string } | null>(null)
  const changed = (text: string) => {
    setDialog(null)
    setNote({ tone: 'info', text })
    pools.reload()
    onChange()
  }

  async function removeSeed(id: number) {
    setNote(null)
    try {
      done(await api.DELETE('/api/async-qualifiers/permalinks/{permalink_id}', { params: { path: { permalink_id: id } } }))
      changed('Seed removed.')
    } catch (err) {
      setNote({ tone: 'error', text: message(err) })
    }
  }

  if (pools.error && !pools.data) return <Banner tone="error">{pools.error.message}</Banner>
  if (!pools.data) return <Spinner label="Loading pools" />
  return (
    <>
      {note && (
        <Banner tone={note.tone} action={<Button variant="ghost" onClick={() => setNote(null)}>Dismiss</Button>}>
          {note.text}
        </Banner>
      )}
      <div className="t-actions">
        <Button icon="add" onClick={() => setDialog({ kind: 'add-pool' })}>
          Add pool
        </Button>
      </div>
      {pools.data.length === 0 && (
        <Panel>
          <PanelRow>
            <span className="t-row__sub">No pools yet. A runner draws one seed from each pool.</span>
          </PanelRow>
        </Panel>
      )}
      {pools.data.map((p) => (
        <Panel
          key={p.id}
          title={`${p.name} · ${p.permalinks.length} ${p.permalinks.length === 1 ? 'seed' : 'seeds'}`}
          actions={
            <RowMenu
              label={`Actions for ${p.name}`}
              items={[
                { label: 'Paste seeds…', icon: 'add', onSelect: () => setDialog({ kind: 'paste', pool: p }) },
                { label: 'Roll seeds…', icon: 'casino', disabled: !!p.roll_refusal, onSelect: () => setDialog({ kind: 'roll', pool: p }) },
                { label: 'Delete pool…', icon: 'delete', destructive: true, onSelect: () => setDialog({ kind: 'delete', pool: p }) },
              ]}
            />
          }
        >
          <PanelRow>
            <span className="t-row__sub">
              {p.preset_name ? `Rolls from ${p.preset_name}` : 'No preset: paste seeds in'}
              {p.preset_name && p.roll_refusal ? ` · ${p.roll_refusal}` : ''}
            </span>
          </PanelRow>
          {p.permalinks.map((l) => (
            <PanelRow key={l.id}>
              <div className="t-row">
                <span className="t-row__text">
                  <a className="t-row__title q-url" href={l.url} target="_blank" rel="noopener noreferrer">
                    {l.url}
                  </a>
                  <span className="t-row__sub">
                    {[l.par_seconds ? `par ${hms(l.par_seconds)}` : null, l.notes].filter(Boolean).join(' · ') || 'No par yet'}
                  </span>
                </span>
                {l.live_race && <Chip tone="info">Live races</Chip>}
                <RowMenu
                  label={`Actions for seed ${l.id}`}
                  items={[{ label: 'Remove seed', icon: 'delete', destructive: true, onSelect: () => void removeSeed(l.id) }]}
                />
              </div>
            </PanelRow>
          ))}
        </Panel>
      ))}
      {dialog?.kind === 'add-pool' && <AddPool qualifierId={qualifierId} onClose={() => setDialog(null)} onDone={changed} />}
      {dialog?.kind === 'paste' && <PasteSeeds pool={dialog.pool} onClose={() => setDialog(null)} onDone={changed} />}
      {dialog?.kind === 'roll' && <RollSeeds pool={dialog.pool} onClose={() => setDialog(null)} onDone={changed} />}
      {dialog?.kind === 'delete' && <DeletePool pool={dialog.pool} onClose={() => setDialog(null)} onDone={changed} />}
    </>
  )
}

interface DialogProps {
  onClose: () => void
  onDone: (text: string) => void
}

function useSubmit(onDone: (text: string) => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const submit = async (call: () => Promise<string>) => {
    setBusy(true)
    setError(null)
    try {
      onDone(await call())
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, setError, submit }
}

function AddPool({ qualifierId, onClose, onDone }: DialogProps & { qualifierId: number }) {
  const [name, setName] = useState('')
  const { busy, error, submit } = useSubmit(onDone)
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(o) => !o && onClose()}
      title="Add pool"
      primaryLabel="Add pool"
      busy={busy}
      busyLabel="Adding…"
      onPrimary={() =>
        void submit(async () => {
          await api.POST('/api/async-qualifiers/{qualifier_id}/pools', { params: { path: { qualifier_id: qualifierId } }, body: { name: name.trim() } }).then(unwrap)
          return `Added ${name.trim()}. Paste or roll its seeds next.`
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" value={name} placeholder="e.g. Open" onChange={(e) => setName(e.target.value)} />
        </label>
        <span className="field__hint">Pick the preset it rolls from on the website for now.</span>
      </div>
    </FormDialog>
  )
}

function PasteSeeds({ pool, onClose, onDone }: DialogProps & { pool: PoolLine }) {
  const [text, setText] = useState('')
  const { busy, error, submit } = useSubmit(onDone)
  return (
    <FormDialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Paste seeds into ${pool.name}`}
      description="One permalink per line."
      primaryLabel="Add seeds"
      busy={busy}
      busyLabel="Adding…"
      onPrimary={() =>
        void submit(async () => {
          const urls = text.split('\n').map((l) => l.trim()).filter(Boolean)
          const r = await api.POST('/api/async-qualifiers/pools/{pool_id}/permalinks/bulk', { params: { path: { pool_id: pool.id } }, body: { urls } }).then(unwrap)
          const skipped = (r.rejected ?? []).map((x) => `line ${x.line}: ${x.reason}`)
          return `Added ${r.created.length}.${skipped.length ? ` Skipped ${skipped.join('; ')}.` : ''}`
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <label className="field">
          <span className="field__label">Permalinks</span>
          <textarea className="input" rows={6} value={text} onChange={(e) => setText(e.target.value)} />
        </label>
      </div>
    </FormDialog>
  )
}

function RollSeeds({ pool, onClose, onDone }: DialogProps & { pool: PoolLine }) {
  const [count, setCount] = useState<number | null>(5)
  const { busy, error, submit } = useSubmit(onDone)
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(o) => !o && onClose()}
      title={`Roll seeds into ${pool.name}`}
      description={`From ${pool.preset_name ?? 'its preset'}. Up to 25 at a time.`}
      primaryLabel="Roll"
      busy={busy}
      busyLabel="Rolling…"
      onPrimary={() =>
        void submit(async () => {
          const r = await api.POST('/api/async-qualifiers/pools/{pool_id}/permalinks/roll', { params: { path: { pool_id: pool.id } }, body: { count: count ?? 1 } }).then(unwrap)
          return `Rolled ${r.length} into ${pool.name}.`
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <NumberField label="How many" min={1} value={count} onChange={setCount} />
      </div>
    </FormDialog>
  )
}

function DeletePool({ pool, onClose, onDone }: DialogProps & { pool: PoolLine }) {
  const { busy, error, submit } = useSubmit(onDone)
  return (
    <FormDialog
      open
      size="sm"
      onOpenChange={(o) => !o && onClose()}
      title={`Delete ${pool.name}?`}
      primaryLabel="Delete pool"
      destructive
      busy={busy}
      busyLabel="Deleting…"
      onPrimary={() =>
        void submit(async () => {
          done(await api.DELETE('/api/async-qualifiers/pools/{pool_id}', { params: { path: { pool_id: pool.id } } }))
          return `${pool.name} is gone.`
        })
      }
    >
      <div className="t-form">
        {error && <Banner tone="error">{error}</Banner>}
        <p style={{ margin: 0 }}>Its {pool.permalinks.length} seeds and its live races go with it. Runs already drawn from it are kept but lose their link to the seed. This can't be undone.</p>
      </div>
    </FormDialog>
  )
}

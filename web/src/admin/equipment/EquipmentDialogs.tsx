import { useEffect, useId, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { FormDialog, Spinner } from '../../components'
import { message, useAction } from '../peopleKit'

type Asset = Schemas['EquipmentResponse']
type Person = Schemas['PersonPickResponse']

interface OpenProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

function FormError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <p className="form-error" role="alert">
      {error}
    </p>
  )
}

function usePeople(open: boolean, everyone: boolean) {
  const [people, setPeople] = useState<Person[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!open) return
    let live = true
    setPeople(null)
    api
      .GET('/api/admin/equipment/people', { params: { query: { everyone } } })
      .then(unwrap)
      .then((p) => live && setPeople(p))
      .catch((e: unknown) => live && setError(message(e)))
    return () => {
      live = false
    }
  }, [open, everyone])
  return { people, error }
}

/** Search and pick one person; `none` adds a first row for "nobody" (e.g. community-owned). */
function PersonPicker({
  people,
  value,
  onChange,
  none,
  label,
}: {
  people: Person[] | null
  value: number | null
  onChange: (id: number | null) => void
  none?: string
  label: string
}) {
  const [search, setSearch] = useState('')
  const labelId = useId()
  const q = search.trim().toLowerCase()
  const shown = (people ?? []).filter((p) => !q || p.name.toLowerCase().includes(q) || p.username.toLowerCase().includes(q)).slice(0, 40)
  const picked = people?.find((p) => p.id === value)
  return (
    <div className="field" role="group" aria-labelledby={labelId}>
      <span className="field__label" id={labelId}>
        {label}
      </span>
      <input className="input" type="search" placeholder="Search names" aria-label={`Search for ${label.toLowerCase()}`} value={search} onChange={(e) => setSearch(e.target.value)} />
      {!people ? (
        <Spinner label="Loading people" />
      ) : (
        <ul className="pick-list">
          {none && (
            <li>
              <label className="pick-list__row">
                <input type="radio" checked={value === null} onChange={() => onChange(null)} />
                <span>{none}</span>
              </label>
            </li>
          )}
          {picked && !shown.includes(picked) && (
            <li>
              <label className="pick-list__row">
                <input type="radio" checked readOnly />
                <span className="admin-name">
                  <span>{picked.name}</span>
                  <span className="admin-name__sub">@{picked.username}</span>
                </span>
              </label>
            </li>
          )}
          {shown.map((p) => (
            <li key={p.id}>
              <label className="pick-list__row">
                <input type="radio" checked={value === p.id} onChange={() => onChange(p.id)} />
                <span className="admin-name">
                  <span>{p.name}</span>
                  <span className="admin-name__sub">@{p.username}</span>
                </span>
              </label>
            </li>
          ))}
          {shown.length === 0 && <li className="admin-muted">No one matches.</li>}
        </ul>
      )}
    </div>
  )
}

export function AssetDialog({
  open,
  onOpenChange,
  asset,
  onSaved,
}: OpenProps & { asset: Asset | null; onSaved: (id: number | null) => void }) {
  const blank = { name: '', description: '', private_notes: '', owner_user_id: null as number | null, count: '1' }
  const [form, setForm] = useState(blank)
  const { people, error: peopleError } = usePeople(open, false)
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (!open) return
    setError(null)
    setForm(
      asset
        ? { name: asset.name, description: asset.description ?? '', private_notes: asset.private_notes ?? '', owner_user_id: asset.owner_user_id ?? null, count: '1' }
        : blank,
    )
  }, [open, asset, setError])
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }))
  const count = Math.max(1, Number(form.count) || 1)

  async function save() {
    const body = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      private_notes: form.private_notes.trim() || null,
      owner_user_id: form.owner_user_id,
    }
    let id: number | null = null
    const ok = await run('save', async () => {
      if (asset) {
        id = (await api.PUT('/api/equipment/{equipment_id}', { params: { path: { equipment_id: asset.id } }, body }).then(unwrap)).id
      } else if (count > 1) {
        await api.POST('/api/equipment/bulk', { body: { ...body, count } }).then(unwrap)
      } else {
        id = (await api.POST('/api/equipment', { body }).then(unwrap)).id
      }
    })
    if (ok) {
      onOpenChange(false)
      onSaved(id)
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={asset ? `Edit #${asset.asset_number}` : 'Add equipment'}
      primaryLabel={asset ? 'Save' : count > 1 ? `Add ${count}` : 'Add'}
      busy={busy === 'save'}
      onPrimary={() => void save()}
    >
      <label className="field">
        <span className="field__label">Name</span>
        <input className="input" value={form.name} maxLength={200} onChange={(e) => set('name', e.target.value)} />
      </label>
      {!asset && (
        <label className="field">
          <span className="field__label">How many</span>
          <input className="input" type="number" min={1} max={200} value={form.count} onChange={(e) => set('count', e.target.value)} />
          <span className="field__hint">More than one gets the next asset numbers, all with this name.</span>
        </label>
      )}
      <label className="field">
        <span className="field__label">Description</span>
        <textarea className="input" rows={2} value={form.description} onChange={(e) => set('description', e.target.value)} />
        <span className="field__hint">Anyone who scans the label sees this.</span>
      </label>
      <label className="field">
        <span className="field__label">Private notes</span>
        <textarea className="input" rows={2} value={form.private_notes} onChange={(e) => set('private_notes', e.target.value)} />
        <span className="field__hint">Only staff and equipment managers see these.</span>
      </label>
      <PersonPicker label="Owner" people={people} value={form.owner_user_id} onChange={(v) => set('owner_user_id', v)} none="The community" />
      <FormError error={error ?? peopleError} />
    </FormDialog>
  )
}

export function CheckoutDialog({ open, onOpenChange, asset, onDone }: OpenProps & { asset: Asset; onDone: () => void }) {
  const [everyone, setEveryone] = useState(false)
  const [borrower, setBorrower] = useState<number | null>(null)
  const { people, error: peopleError } = usePeople(open, everyone)
  const { busy, error, run, setError } = useAction()
  useEffect(() => {
    if (open) {
      setBorrower(null)
      setEveryone(false)
      setError(null)
    }
  }, [open, setError])
  async function go() {
    const ok = await run('out', () =>
      api.POST('/api/equipment/{equipment_id}/checkout', { params: { path: { equipment_id: asset.id } }, body: { borrower_id: borrower } }).then(unwrap),
    )
    if (ok) {
      onOpenChange(false)
      onDone()
    }
  }
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Check out #${asset.asset_number}`}
      description={asset.name}
      primaryLabel="Check out"
      busy={busy === 'out'}
      onPrimary={() => void go()}
    >
      <PersonPicker label="Who has it" people={people} value={borrower} onChange={setBorrower} none="Me" />
      <label className="admin-switch">
        <span>Show people outside this community</span>
        <input type="checkbox" role="switch" className="switch" checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />
      </label>
      <FormError error={error ?? peopleError} />
    </FormDialog>
  )
}

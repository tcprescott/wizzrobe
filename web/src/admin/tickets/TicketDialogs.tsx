import { useEffect, useState } from 'react'
import { api, unwrap, type Schemas } from '../../api/client'
import { FormDialog } from '../../components'
import { InstantField } from '../kit'
import { useAction } from '../peopleKit'
import { fromCents, LANYARDS, providerName, toCents, type BadgeType, type Provider } from './data'

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

/** Paste a Stripe restricted key, or a PayPal REST app's client ID and secret (sandbox until switched off). */
export function StoreDialog({ open, onOpenChange, provider, replacing, onSaved }: OpenProps & { provider: Provider; replacing: boolean; onSaved: () => void }) {
  const [secret, setSecret] = useState('')
  const [clientId, setClientId] = useState('')
  const [sandbox, setSandbox] = useState(true)
  const { busy, error, setError, run } = useAction()
  useEffect(() => {
    if (!open) return
    setSecret('')
    setClientId('')
    setSandbox(true)
    setError(null)
  }, [open, setError])

  const paypal = provider === 'paypal'
  async function save() {
    if (!secret.trim() || (paypal && !clientId.trim())) {
      setError(paypal ? 'Paste both the client ID and the secret.' : 'Paste the restricted key.')
      return
    }
    const body = paypal ? { secret: secret.trim(), client_id: clientId.trim(), sandbox } : { secret: secret.trim(), sandbox: true }
    const ok = await run('save', () => api.PUT('/api/tickets/stores/{provider}', { params: { path: { provider } }, body }).then(unwrap))
    if (ok) {
      onOpenChange(false)
      onSaved()
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={`${replacing ? 'Replace' : 'Connect'} ${providerName(provider)}`}
      description={
        paypal
          ? 'From your PayPal developer dashboard: Apps & Credentials, then the REST app you made for badges. Wizzrobe registers its own webhook.'
          : 'From your Stripe dashboard: Developers, API keys, Create restricted key. Wizzrobe registers its own webhook.'
      }
      primaryLabel={replacing ? 'Replace' : 'Connect'}
      busy={busy === 'save'}
      busyLabel="Checking with the store…"
      onPrimary={() => void save()}
    >
      <div className="t-form">
        {paypal && (
          <label className="field">
            <span className="field__label">Client ID</span>
            <input className="input" autoComplete="off" spellCheck={false} value={clientId} onChange={(e) => setClientId(e.target.value)} />
          </label>
        )}
        <label className="field">
          <span className="field__label">{paypal ? 'Secret' : 'Restricted key'}</span>
          <input className="input" type="password" autoComplete="off" spellCheck={false} value={secret} onChange={(e) => setSecret(e.target.value)} />
          <span className="field__hint">
            {paypal
              ? "Stored sealed for this community only. Nobody can read it back, staff included."
              : 'Starts rk_live_ or rk_test_. Give it write access to Checkout Sessions, Refunds and Webhook Endpoints, nothing else. Stored sealed for this community only.'}
          </span>
        </label>
        {paypal && (
          <label className="settings-row">
            <span className="settings-row__text">
              <span className="settings-row__title">Sandbox</span>
              <span className="settings-row__sub">
                {sandbox ? 'Test money only: use your sandbox app’s credentials. Turn this off to take real payments.' : 'Live: buyers pay real money. Use the live app’s credentials.'}
              </span>
            </span>
            <input type="checkbox" role="switch" className="switch" checked={sandbox} onChange={(e) => setSandbox(e.target.checked)} />
          </label>
        )}
        <FormError error={error} />
      </div>
    </FormDialog>
  )
}

type Lanyard = Schemas['CheckInTierLanyard']

interface TypeForm {
  name: string
  description: string
  price: string
  currency: string
  quantity: string
  max_per_order: string
  sales_open_at: string | null
  sales_close_at: string | null
  lanyard: Lanyard
  covers_comp: boolean
  is_active: boolean
}

function blankForm(t: BadgeType | null): TypeForm {
  return {
    name: t?.name ?? '',
    description: t?.description ?? '',
    price: t ? fromCents(t.price_cents, t.currency) : '',
    currency: t?.currency ?? 'usd',
    quantity: t?.quantity != null ? String(t.quantity) : '',
    max_per_order: String(t?.max_per_order ?? 10),
    sales_open_at: t?.sales_open_at ?? null,
    sales_close_at: t?.sales_close_at ?? null,
    lanyard: (t?.lanyard as Lanyard | undefined) ?? 'base',
    covers_comp: t?.covers_comp ?? false,
    is_active: t?.is_active ?? true,
  }
}

/** New or edit a badge type. Currency is set once, on create. */
export function TypeDialog({ open, onOpenChange, type, zone, onSaved }: OpenProps & { type: BadgeType | null; zone: string | null; onSaved: () => void }) {
  const [form, setForm] = useState<TypeForm>(() => blankForm(type))
  const { busy, error, setError, run } = useAction()
  useEffect(() => {
    if (!open) return
    setForm(blankForm(type))
    setError(null)
  }, [open, type, setError])
  const set = <K extends keyof TypeForm>(key: K, value: TypeForm[K]) => setForm((f) => ({ ...f, [key]: value }))

  async function save() {
    const price = toCents(form.price, form.currency)
    if (!form.name.trim()) return setError('Give the badge a name.')
    if (price == null || price < 50) return setError('Price it at 0.50 or more.')
    const quantity = form.quantity.trim() ? Math.trunc(Number(form.quantity)) : null
    if (quantity != null && !(quantity >= 1)) return setError('Quantity is a whole number of 1 or more, or empty for no limit.')
    const perOrder = Math.trunc(Number(form.max_per_order))
    if (!(perOrder >= 1 && perOrder <= 20)) return setError('A buyer can take between 1 and 20 per order.')
    const fields = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      price_cents: price,
      quantity,
      max_per_order: perOrder,
      sales_open_at: form.sales_open_at,
      sales_close_at: form.sales_close_at,
      lanyard: form.lanyard,
      covers_comp: form.covers_comp,
      is_active: form.is_active,
    }
    const ok = await run('save', () =>
      type
        ? api.PATCH('/api/tickets/types/{type_id}', { params: { path: { type_id: type.id } }, body: fields }).then(unwrap)
        : api.POST('/api/tickets/types', { body: { ...fields, currency: form.currency.trim().toLowerCase(), sort_order: 0 } }).then(unwrap),
    )
    if (ok) {
      onOpenChange(false)
      onSaved()
    }
  }

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title={type ? `Edit ${type.name}` : 'New badge type'}
      primaryLabel={type ? 'Save' : 'Create'}
      busy={busy === 'save'}
      busyLabel="Saving…"
      onPrimary={() => void save()}
    >
      <div className="t-form">
        <label className="field">
          <span className="field__label">Name</span>
          <input className="input" maxLength={100} value={form.name} onChange={(e) => set('name', e.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">Description</span>
          <textarea className="input" rows={2} maxLength={500} value={form.description} onChange={(e) => set('description', e.target.value)} />
          <span className="field__hint">Buyers see this under the name.</span>
        </label>
        <div className="t-form__grid">
          <label className="field">
            <span className="field__label">Price</span>
            <input className="input" inputMode="decimal" value={form.price} onChange={(e) => set('price', e.target.value)} />
          </label>
          <label className="field">
            <span className="field__label">Currency</span>
            <input
              className="input tk-currency"
              maxLength={3}
              disabled={!!type}
              value={form.currency.toUpperCase()}
              onChange={(e) => set('currency', e.target.value.toLowerCase())}
            />
            {type && <span className="field__hint">Fixed once a badge type exists.</span>}
          </label>
          <label className="field">
            <span className="field__label">How many</span>
            <input className="input" type="number" inputMode="numeric" min={1} value={form.quantity} onChange={(e) => set('quantity', e.target.value)} />
            <span className="field__hint">Leave empty for no limit.</span>
          </label>
          <label className="field">
            <span className="field__label">Most per order</span>
            <input className="input" type="number" inputMode="numeric" min={1} max={20} value={form.max_per_order} onChange={(e) => set('max_per_order', e.target.value)} />
          </label>
        </div>
        <InstantField label="Sales open" value={form.sales_open_at} zone={zone} onChange={(v) => set('sales_open_at', v)} hint="Leave empty to sell now." />
        <InstantField label="Sales close" value={form.sales_close_at} zone={zone} onChange={(v) => set('sales_close_at', v)} hint="Leave empty to sell until it's switched off." />
        <label className="field">
          <span className="field__label">Check-in lanyard</span>
          <select className="input" value={form.lanyard} onChange={(e) => set('lanyard', e.target.value as TypeForm['lanyard'])}>
            {LANYARDS.map((l) => (
              <option key={l.value} value={l.value}>
                {l.label}
              </option>
            ))}
          </select>
          <span className="field__hint">What the check-in desk hands its holders.</span>
        </label>
        <label className="settings-row">
          <span className="settings-row__text">
            <span className="settings-row__title">A comp covers this badge</span>
            <span className="settings-row__sub">Comped staff and volunteers who bought a badge are owed up to its price. Turning this on takes it off any other badge type.</span>
          </span>
          <input type="checkbox" role="switch" className="switch" checked={form.covers_comp} onChange={(e) => set('covers_comp', e.target.checked)} />
        </label>
        <label className="settings-row">
          <span className="settings-row__text">
            <span className="settings-row__title">On sale</span>
            <span className="settings-row__sub">Off hides it from buyers. Badges already sold stay valid.</span>
          </span>
          <input type="checkbox" role="switch" className="switch" checked={form.is_active} onChange={(e) => set('is_active', e.target.checked)} />
        </label>
        <FormError error={error} />
      </div>
    </FormDialog>
  )
}

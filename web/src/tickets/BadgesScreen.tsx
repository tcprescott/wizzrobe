import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, unwrap } from '../api/client'
import { Banner, Button, Chip, EmptyState, Icon, PageHeader, Spinner } from '../components'
import { useCommunity } from '../community/CommunityProvider'
import { BackToEvent } from '../event/BackLink'
import { money } from '../lib/money'
import { message, useSection } from '../me/data'
import { openCheckout } from '../native/checkout'
import { providerName, useEventLine, type Order, type Provider, type SaleType } from './data'
import './tickets.css'

const POLL_MS = 2000
const POLL_FOR_MS = 90_000

/** After the store sends the buyer back: confirming until the store's webhook lands, then paid. */
function ReturnBanner({ orderId, cancelled, onSettled }: { orderId: number; cancelled: boolean; onSettled: () => void }) {
  const { base } = useCommunity()
  const [order, setOrder] = useState<Order | null>(null)
  const [gaveUp, setGaveUp] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    let timer = 0
    const started = Date.now()
    async function poll() {
      try {
        const o = await api.GET('/api/me/tickets/orders/{order_id}', { params: { path: { order_id: orderId } } }).then(unwrap)
        if (!live) return
        setOrder(o)
        if (o.status !== 'pending') {
          onSettled()
          return
        }
      } catch (err) {
        if (live) setError(message(err))
        return
      }
      if (cancelled) return
      if (Date.now() - started > POLL_FOR_MS) setGaveUp(true)
      else timer = window.setTimeout(() => void poll(), POLL_MS)
    }
    void poll()
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [orderId, cancelled, onSettled])

  if (error) return <Banner tone="error">{error}</Banner>
  if (!order) return <Spinner label="Checking your order" />
  const n = order.badges.length
  if (order.status === 'paid') {
    return (
      <Banner tone="info" action={<Button asChild variant="outline"><Link to={`${base}/me/tickets`}>My tickets</Link></Button>}>
        Paid. {n === 1 ? 'Your badge is' : `Your ${n} badges are`} under Me → Tickets{n > 1 ? ', with a link to send for each extra' : ''}.
      </Banner>
    )
  }
  if (order.status === 'refunded') return <Banner tone="info">That order was refunded.</Banner>
  if (order.status === 'expired') {
    return <Banner tone="warning">That checkout ran out before it was paid, so nothing was charged. Pick your badges again below.</Banner>
  }
  if (cancelled) return <Banner tone="info">You left the checkout, so nothing was charged.</Banner>
  if (gaveUp) {
    return (
      <Banner tone="warning">
        {providerName(order.provider)} hasn't confirmed the payment yet. If you paid, your badges show up under Me → Tickets as soon as it does, and you'll get a DM.
      </Banner>
    )
  }
  return (
    <Banner tone="info">
      <span className="tk-confirming">
        <Icon name="progress_activity" size={18} className="spin" /> Confirming your payment with {providerName(order.provider)}…
      </span>
    </Banner>
  )
}

function Stepper({ type, value, onChange }: { type: SaleType; value: number; onChange: (n: number) => void }) {
  const most = Math.min(type.max_per_order, type.remaining ?? type.max_per_order)
  return (
    <span className="tk-stepper" role="group" aria-label={`How many ${type.name}`}>
      <button type="button" className="tk-stepper__btn" aria-label={`One fewer ${type.name}`} disabled={value <= 0} onClick={() => onChange(value - 1)}>
        <Icon name="remove" size={20} />
      </button>
      <span className="tk-stepper__value" aria-live="polite">
        {value}
      </span>
      <button type="button" className="tk-stepper__btn" aria-label={`One more ${type.name}`} disabled={value >= most} onClick={() => onChange(value + 1)}>
        <Icon name="add" size={20} />
      </button>
    </span>
  )
}

const LOW_STOCK = 20

/** Event → Badges: every badge type with a stepper, and a pay bar that sticks to the bottom. */
export function BadgesScreen() {
  const { community } = useCommunity()
  const line = useEventLine()
  const [params] = useSearchParams()
  const orderId = Number(params.get('order')) || null
  const cancelled = params.get('cancelled') === '1'
  const sale = useSection('tickets/on-sale', () => api.GET('/api/tickets/on-sale').then(unwrap))
  const [qty, setQty] = useState<Record<number, number>>({})
  const [paying, setPaying] = useState<Provider | null>(null)
  const [error, setError] = useState<string | null>(null)

  const types = sale.data?.types ?? []
  const providers = (sale.data?.providers ?? []) as Provider[]
  const picked = types.filter((t) => (qty[t.id] ?? 0) > 0)
  const count = picked.reduce((n, t) => n + (qty[t.id] ?? 0), 0)
  const total = picked.reduce((sum, t) => sum + t.price_cents * (qty[t.id] ?? 0), 0)
  const currency = picked[0]?.currency ?? types[0]?.currency ?? 'usd'

  async function pay(provider: Provider) {
    setPaying(provider)
    setError(null)
    try {
      const quantities = Object.fromEntries(picked.map((t) => [String(t.id), qty[t.id] ?? 0]))
      const start = await api.POST('/api/tickets/checkout', { body: { provider, quantities } }).then(unwrap)
      await openCheckout(start.checkout_url)
    } catch (err) {
      setError(message(err))
      sale.reload()
    } finally {
      setPaying(null)
    }
  }

  return (
    <>
      <BackToEvent />
      <PageHeader title="Badges" lede={line} />
      {orderId && <ReturnBanner orderId={orderId} cancelled={cancelled} onSettled={sale.reload} />}
      {sale.error && !sale.data && (
        <Banner tone="error" action={<Button variant="outline" onClick={sale.reload}>Try again</Button>}>
          {sale.error.message}
        </Banner>
      )}
      {!sale.data && !sale.error && <Spinner label="Loading badges" />}
      {sale.data && types.length === 0 && (
        <EmptyState icon="local_activity" title="No badges on sale right now">
          Check back closer to the event, or ask {community.name} staff.
        </EmptyState>
      )}
      {types.length > 0 && (
        <>
          <ul className="panel tk-sale" aria-label="Badge types">
            {types.map((t) => {
              const off = !t.on_sale
              return (
                <li key={t.id} className={off ? 'tk-sale__row tk-sale__row--off' : 'tk-sale__row'}>
                  <span className="tk-sale__text">
                    <strong className="tk-sale__name">{t.name}</strong>
                    <span className="tk-sale__sub">
                      {t.description}
                      {off ? (
                        <Chip tone="neutral">{t.not_on_sale_reason ?? 'Not on sale'}</Chip>
                      ) : t.remaining != null && t.remaining <= LOW_STOCK ? (
                        <Chip tone="pending">{t.remaining} left</Chip>
                      ) : null}
                    </span>
                  </span>
                  <span className="tk-sale__buy">
                    <strong className="tk-sale__price">{money(t.price_cents, t.currency)}</strong>
                    {!off && <Stepper type={t} value={qty[t.id] ?? 0} onChange={(n) => setQty({ ...qty, [t.id]: n })} />}
                  </span>
                </li>
              )
            })}
          </ul>
          <p className="tk-fine">
            You'll pay on {providers.map(providerName).join("'s or ")}'s own page, then come straight back here. Badges go on your account, and each extra gets a link you
            send to the person it's for. Refunds are up to {community.name} staff.
          </p>
          {error && <Banner tone="error">{error}</Banner>}
          <div className="tk-paybar">
            <span className="tk-paybar__text">
              <strong>
                {count === 0 ? 'No badges picked' : `${count} ${count === 1 ? 'badge' : 'badges'} · ${money(total, currency)}`}
              </strong>
              <span className="tk-sale__sub">{count === 0 ? 'Use + to add one' : count === 1 ? 'Just you' : `You + ${count - 1} to hand out`}</span>
            </span>
            <span className="tk-paybar__actions">
              {providers.map((p, i) => (
                <Button
                  key={p}
                  variant={i === 0 ? 'primary' : 'outline'}
                  icon={p === 'stripe' ? 'credit_card' : undefined}
                  disabled={count === 0 || paying !== null}
                  busy={paying === p}
                  busyLabel="Opening checkout…"
                  onClick={() => void pay(p)}
                >
                  {p === 'stripe' ? 'Pay by card' : 'PayPal'}
                </Button>
              ))}
            </span>
          </div>
        </>
      )}
    </>
  )
}

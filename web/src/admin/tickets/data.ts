import type { Schemas } from '../../api/client'

export type Store = Schemas['StoreResponse']
export type BadgeType = Schemas['TicketTypeResponse']
export type Order = Schemas['OrderResponse']
export type CompRow = Schemas['CompRefundRow']
export type Provider = Schemas['TicketProvider']

export const PROVIDERS: { id: Provider; name: string }[] = [
  { id: 'stripe', name: 'Stripe' },
  { id: 'paypal', name: 'PayPal' },
]

export const providerName = (id: string): string => PROVIDERS.find((p) => p.id === id)?.name ?? id

export const LANYARDS: { value: Schemas['CheckInTierLanyard']; label: string }[] = [
  { value: 'base', label: 'Base' },
  { value: 'vip', label: 'VIP' },
  { value: 'day_pass', label: 'Day pass' },
]

export const lanyardLabel = (v: string): string => LANYARDS.find((l) => l.value === v)?.label ?? v

const ZERO_DECIMAL = new Set(['jpy', 'krw', 'vnd', 'clp', 'pyg', 'ugx', 'xaf', 'xof'])

/** Smallest-unit amount → "$60" or "$12.50", in the order's own currency. */
export function money(cents: number, currency = 'usd'): string {
  const zero = ZERO_DECIMAL.has(currency.toLowerCase())
  const amount = zero ? cents : cents / 100
  const whole = zero || cents % 100 === 0
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency.toUpperCase(),
      minimumFractionDigits: whole ? 0 : 2,
      maximumFractionDigits: whole ? 0 : 2,
    }).format(amount)
  } catch {
    return `${amount.toFixed(whole ? 0 : 2)} ${currency.toUpperCase()}`
  }
}

/** "60" or "12.5" typed in a price box → smallest units, or null when it isn't a price. */
export function toCents(typed: string, currency: string): number | null {
  const n = Number(typed.trim().replace(/^\$/, ''))
  if (!typed.trim() || !Number.isFinite(n) || n < 0) return null
  return ZERO_DECIMAL.has(currency.toLowerCase()) ? Math.round(n) : Math.round(n * 100)
}

export function fromCents(cents: number, currency: string): string {
  if (ZERO_DECIMAL.has(currency.toLowerCase())) return String(cents)
  return cents % 100 === 0 ? String(cents / 100) : (cents / 100).toFixed(2)
}

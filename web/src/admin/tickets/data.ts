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

export { fromCents, money, toCents } from '../../lib/money'

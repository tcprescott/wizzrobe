import type { Schemas } from '../api/client'
import { datesLabel } from '../admin/events/data'
import { useCommunity } from '../community/CommunityProvider'

export type OnSale = Schemas['OnSaleResponse']
export type SaleType = Schemas['TicketTypeResponse']
export type MyBadges = Schemas['MyBadgesResponse']
export type Order = Schemas['OrderResponse']
export type Provider = Schemas['TicketProvider']

export const providerName = (id: string): string => (id === 'paypal' ? 'PayPal' : id === 'stripe' ? 'Stripe' : id)

/** "SpeedGaming Live · Oct 22 – 25, 2026": the line under each ticket screen's title. */
export function useEventLine(): string {
  const { community, event } = useCommunity()
  return [community.name, datesLabel(event?.starts_on, event?.ends_on)].filter(Boolean).join(' · ')
}

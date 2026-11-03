/** a11y checks for the attendee ticket screens: buying, coming back from the store, your badges, a claim link. See ../a11y.mjs. */
export const targets = [
  { name: 'event-badges', path: '/t/sgl/event/badges', act: (p) => p.getByRole('button', { name: 'One more Weekend badge' }).click().then(() => p.getByRole('button', { name: 'One more Weekend badge' }).click()), wait: 'text=You + 1 to hand out' },
  { name: 'event-badges-return', path: '/t/sgl/event/badges?order=77', wait: 'text=Your 2 badges are' },
  { name: 'me-tickets', path: '/t/sgl/me/tickets', wait: 'text=Show this QR at the desk' },
  { name: 'badge-claim', path: '/t/sgl/badges/claim/fixture-token', wait: 'text=Sam Ruiz sent you a badge' },
]

const QR =
  'data:image/svg+xml;base64,' +
  Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21"><rect width="21" height="21" fill="#fff"/><path d="M0 0h7v7H0zM14 0h7v7h-7zM0 14h7v7H0zM9 9h3v3H9z"/></svg>',
  ).toString('base64')

const type = (id, name, description, price, quantity, sold, extra = {}) => ({
  id,
  name,
  description,
  price_cents: price,
  currency: 'usd',
  quantity,
  sold,
  remaining: quantity - sold,
  on_sale: sold < quantity,
  not_on_sale_reason: sold < quantity ? null : 'Sold out',
  max_per_order: 10,
  sales_open_at: null,
  sales_close_at: null,
  lanyard: 'base',
  covers_comp: false,
  is_active: true,
  sort_order: id,
  ...extra,
})

const badge = (id, name, owner, claimed = null) => ({
  id,
  ticket_type_id: 1,
  ticket_type_name: name,
  status: 'valid',
  code: `WZ7K${id}Q`,
  price_cents: 6000,
  owner_id: owner?.[0] ?? null,
  owner_name: owner?.[1] ?? null,
  claimed_at: claimed,
  comp_refund_cents: 0,
})

export const fixtures = {
  '/api/tickets/on-sale': {
    types: [
      type(1, 'Weekend badge', 'Thu–Sun, all four days', 6000, 300, 182),
      type(2, 'VIP badge', 'Weekend badge + reserved seat at the main stage and a shirt', 12000, 50, 38),
      type(3, 'Saturday only', 'One day, Sat 24 Oct', 3000, 100, 41),
      type(4, 'Sunday only', 'One day, Sun 25 Oct', 3000, 100, 100),
    ],
    providers: ['stripe', 'paypal'],
  },
  '/api/me/tickets/orders/77': {
    id: 77,
    buyer_id: 7,
    buyer_name: 'Sam Ruiz',
    provider: 'stripe',
    status: 'paid',
    total_cents: 12000,
    refunded_cents: 0,
    currency: 'usd',
    created_at: '2026-10-28T18:00:00Z',
    paid_at: '2026-10-28T18:02:00Z',
    badges: [badge(31, 'Weekend badge', [7, 'Sam Ruiz']), badge(32, 'Weekend badge', null)],
  },
  '/api/me/tickets': {
    own: [{ ...badge(31, 'Weekend badge', [7, 'Sam Ruiz']), qr: QR }],
    extras: [
      { badge: badge(32, 'Weekend badge', [9, 'Alex Chen'], '2026-10-28T20:00:00Z'), claim_path: null, claim_url: null },
      { badge: badge(33, 'Weekend badge', null), claim_path: '/badges/claim/abc', claim_url: 'https://wizzrobe.example/t/sgl/badges/claim/abc' },
    ],
  },
  '/api/tickets/claims/fixture-token': {
    ticket_type_name: 'Weekend badge',
    buyer_name: 'Sam Ruiz',
    claimable: true,
    reason: null,
  },
}

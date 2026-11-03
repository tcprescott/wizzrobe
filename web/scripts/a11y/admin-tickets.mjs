/** a11y checks for Admin → Tickets, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-tickets', path: '/t/sgl/admin/tickets', wait: 'text=Comp refunds' },
  {
    name: 'admin-tickets-comp-confirm',
    path: '/t/sgl/admin/tickets',
    act: (p) =>
      p.getByRole('checkbox', { name: 'Select Alex Chen' }).check()
        .then(() => p.getByRole('checkbox', { name: 'Select Jordan Lee' }).check())
        .then(() => p.getByRole('button', { name: 'Refund selected' }).click()),
    wait: 'text=Refund 2 comped badges?',
  },
  {
    name: 'admin-tickets-connect-paypal',
    path: '/t/sgl/admin/tickets',
    act: (p) => p.getByRole('button', { name: 'Connect PayPal' }).click(),
    wait: 'text=Test money only',
  },
  {
    name: 'admin-tickets-badge-type',
    path: '/t/sgl/admin/tickets',
    act: (p) => p.getByRole('button', { name: 'New badge type' }).click(),
    wait: 'text=A comp covers this badge',
  },
]

const badge = (id, name, price, owner, claimed = null) => ({
  id,
  ticket_type_id: 1,
  ticket_type_name: name,
  status: 'valid',
  code: `WZ-${id}`,
  price_cents: price,
  owner_id: owner?.[0] ?? null,
  owner_name: owner?.[1] ?? null,
  claimed_at: claimed,
  comp_refund_cents: 0,
})

const type = (id, name, price, quantity, sold, extra = {}) => ({
  id,
  name,
  description: null,
  price_cents: price,
  currency: 'usd',
  quantity,
  sold,
  remaining: quantity == null ? null : quantity - sold,
  on_sale: quantity == null || sold < quantity,
  not_on_sale_reason: quantity != null && sold >= quantity ? 'Sold out' : null,
  max_per_order: 10,
  sales_open_at: null,
  sales_close_at: '2026-10-21T23:59:00Z',
  lanyard: 'base',
  covers_comp: false,
  is_active: true,
  sort_order: id,
  ...extra,
})

const comp = (ticket_id, user_name, type_name, paid, amount, hours, provider = 'stripe') => ({
  ticket_id,
  user_id: ticket_id + 100,
  user_name,
  ticket_type_name: type_name,
  reasons: ['Volunteer'],
  scheduled_hours: hours,
  paid_cents: paid,
  amount_cents: amount,
  currency: 'usd',
  provider,
})

export const fixtures = {
  '/api/tickets/stores': [
    { provider: 'stripe', account_label: 'SpeedGaming LLC', test_mode: false, connected_by: 'Thomas' },
  ],
  '/api/tickets/types': [
    type(1, 'Weekend badge', 6000, 300, 182, { covers_comp: true }),
    type(2, 'VIP badge', 12000, 50, 38, { lanyard: 'vip' }),
    type(3, 'Saturday only', 3000, 100, 41, { lanyard: 'day_pass' }),
    type(4, 'Sunday only', 3000, 100, 100, { lanyard: 'day_pass' }),
  ],
  '/api/tickets/orders': [
    {
      id: 51,
      buyer_id: 7,
      buyer_name: 'Sam Ruiz',
      provider: 'stripe',
      status: 'paid',
      total_cents: 12000,
      refunded_cents: 0,
      currency: 'usd',
      created_at: '2026-10-28T18:00:00Z',
      paid_at: '2026-10-28T18:02:00Z',
      badges: [badge(301, 'Weekend badge', 6000, [7, 'Sam Ruiz']), badge(302, 'Weekend badge', 6000, [9, 'Alex Chen'], '2026-10-29T12:00:00Z')],
    },
    {
      id: 50,
      buyer_id: 8,
      buyer_name: 'Priya N.',
      provider: 'paypal',
      status: 'refunded',
      total_cents: 12000,
      refunded_cents: 12000,
      currency: 'usd',
      created_at: '2026-10-27T13:40:00Z',
      paid_at: '2026-10-27T13:40:00Z',
      badges: [badge(300, 'VIP badge', 12000, [8, 'Priya N.'])],
    },
  ],
  '/api/tickets/comp-refunds': {
    rows: [
      comp(201, 'Alex Chen', 'Weekend badge', 6000, 6000, 12),
      comp(202, 'Jordan Lee', 'VIP badge', 12000, 6000, 9),
      comp(203, 'Riley', 'Weekend badge', 6000, 6000, 8, 'paypal'),
      comp(204, 'Dana Ortiz', 'Saturday only', 3000, 3000, 10),
    ],
    comp_type_id: 1,
    has_comp_rules: true,
  },
}

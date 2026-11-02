# Event ticket sales

Status: Draft · Size: full · PR: — · Requested: 2026-11-02, Thomas Prescott

## Problem

Communities sell event badges somewhere else (Matcherino for SGL26), then the
check-in desk syncs that roster back and matches buyers to Wizzrobe accounts by
hand. Attendees should be able to buy a badge inside Wizzrobe, paying through
the community's own Stripe or PayPal account, and have it land on their account
and the check-in roster with no matching step.

## Scope

In:
- Staff define badge types per event in Wizzrobe: name, description, price,
  currency, quantity, sale window, check-in lanyard, max per order.
- Each community connects its own Stripe and/or PayPal account. Keys are sealed
  per community (`secret_box`, purpose `ticket_store`, context tenant id) and
  never leave the server. Wizzrobe takes no fee and never holds money.
- Signed-in attendees pick badges and quantities, then pay on the store's
  hosted page (Stripe Checkout / PayPal approval) and come back to Wizzrobe.
- The store's webhook (signature-checked) is what marks an order paid; the
  return page only shows "confirming…" until it arrives.
- The buyer's first badge is theirs. Every extra badge gets a one-time claim
  link; whoever opens it signed in owns that badge.
- Each owned badge shows a check-in QR, and appears on the event's check-in
  roster as a new entrant source (`wizzrobe`), already linked to the account,
  beside Matcherino rows.
- Staff see orders, can refund (Wizzrobe calls the store's refund API) and
  refunds made in the store's dashboard are mirrored back by webhook.
- Comp refunds: anyone comped by the check-in comp rules (comp roles, or
  volunteers whose scheduled hours reach the comp tier) who also paid for their
  own badge is listed as owed a refund as soon as they qualify, with hours
  scheduled and worked. Staff press Refund on a row, or tick rows and press
  Refund selected; a confirm lists each person and amount first. The amount is
  up to the comp badge's price (a VIP buyer keeps paying the difference);
  extras bought for others are never touched. The badge stays valid, and the
  person gets a DM (a catalogue notice) once the store confirms.
- Gated behind a new `TICKET_SALES` feature flag (page, REST, worker and
  service halves).
- Built in the new app (`web/`) only. Native shells use outside payment, which
  Apple and Google allow for tickets to a real-world event.

Out:
- Shopify, Square, Tito and other stores (the store layer is a plug-in shape so
  they can follow).
- Card forms inside Wizzrobe, guest checkout, discount codes, taxes beyond what
  the store adds, seating, merch.
- Transfers after a badge is claimed (staff can reassign on the roster).
- Any change to the NiceGUI app.
- Anything digital-only: virtual or stream-access passes, VOD, in-app perks.
  Those would need Apple/Google in-app purchase, so a badge type must be for
  in-person attendance (physical extras like a shirt are fine). The admin form
  says so.

## Flow

1. Staff connect a store under Work → Admin → Tickets → Stores, add badge
   types, and turn sales on.
2. Attendee opens badges (placement depends on the mockup pick), sets
   quantities, taps Pay by card or PayPal.
3. Wizzrobe creates a pending order, reserves the quantity for 30 minutes, and
   redirects to the store's hosted page.
4. Store webhook arrives → order paid → badges issued (first to the buyer,
   the rest unclaimed with links) → check-in roster rows created → DM to the
   buyer with a link to their tickets.
5. Abandoned checkouts release their hold after 30 minutes (worker tick).
6. Claim link → signed-in viewer taps Claim → badge moves to them, roster row
   relinks, buyer gets a DM that it was claimed.
7. Desk scans the QR or searches the name and checks the badge in as today.

## Mockup pick

Thomas picked A (Tickets screen). Comp refunds: Thomas picked A, a panel at
the top of Admin → Tickets while anyone is owed (`mockups/compA-*`, confirm
sheet `mockups/compconfirm-*`).

Three placements for buying and owning badges:
`/mnt/project-files/ticket-sales/mockups/{A,B,C}-{buy,mine}-{phone,desktop}-{light,dark}.png`.
Shared by all three: Admin → Tickets (`all-admin-*`) and the claim page
(`all-claim-*`).

- A, Tickets screen: Event hub card → full Badges screen with steppers and a
  sticky pay bar; owned badges under Me → Tickets.
- B, quick sheet: Get badges opens a sheet/dialog over the Event hub; your
  badge and QR sit on Home.
- C, one wallet: Me → Tickets holds owned badges, extras and the on-sale list.

## Data and permissions

New event-owned models (`tenant_id` + `event_id`, `event_scoped` reads):
- `TicketType`: name, description, price_cents, currency, quantity, sold,
  held, sales_open_at/close_at, max_per_order, lanyard, sort, active.
- `TicketOrder`: buyer, provider (`stripe`/`paypal`), provider ids, status
  (pending/paid/refunded/partially_refunded/expired), total, hold_expires_at.
- `Ticket`: order, type, owner (nullable until claimed), claim_token hash,
  claimed_at, refunded_at, check-in entrant FK.

Community-owned:
- `TicketStore`: provider, sealed secret(s), webhook secret, account label,
  connected_by, status.

Permissions: STAFF manage stores, badge types, orders and refunds (audited,
`ticket.*` AuditActions + EventTypes). CHECK_IN_DESK sees badges on the
roster only. Members buy and claim their own. Webhook endpoints are
unauthenticated but signature-verified and resolve the community from the
store row, never from the payload. Claim tokens are single-use, stored hashed,
and rate limited.

## PR plan

1. Spec + models + migration + store plug-in interface + Stripe adapter +
   webhook endpoint + service + REST + flag (no UI).
2. PayPal adapter.
3. Admin → Tickets in `web/` (stores, badge types, orders, refunds).
4. Attendee buy, return, my badges and claim screens in `web/`.
5. Check-in roster source + QR scan on the desk; seed data; docs; delete spec.

## Acceptance

- [ ] With `TICKET_SALES` off, no ticket page, REST route or worker runs for
      that community, and service calls raise not-found.
- [ ] Staff can connect Stripe and PayPal; keys never appear in any API
      response, log or audit row, and another community's staff can't see them.
- [ ] A signed-in member buys 2 Weekend badges with Stripe test mode and
      PayPal sandbox; both show as paid only after the webhook.
- [ ] A forged or replayed webhook is rejected and changes nothing.
- [ ] Quantity never oversells under concurrent checkouts; an abandoned
      checkout frees its hold after 30 minutes.
- [ ] The extra badge's claim link works once, for a signed-in viewer, and the
      buyer is told who claimed it.
- [ ] Each badge appears on the check-in roster already linked to its owner,
      and the desk can check it in by QR or name.
- [ ] A refund from Wizzrobe or from the store's dashboard marks the badge
      refunded and pulls it from the roster.
- [ ] A volunteer who paid $60 and reaches the comp tier appears under Comp
      refunds; Refund selected on two rows shows both amounts, refunds them
      through each store, DMs each person, and drops them from the list. A
      VIP buyer gets back only the comp badge price.
- [ ] On a phone and on desktop, light and dark, the buy, my-badges, claim and
      admin screens match the picked mockup and pass `npm run a11y`.

## Open questions



## Decisions

- 2026-11-02 — Thomas picked store's hosted checkout: buyers pay on Stripe's or
  PayPal's own page.
- 2026-11-02 — Thomas picked Stripe + one more; then PayPal as the second.
- 2026-11-02 — Thomas picked badge types defined by staff in Wizzrobe.
- 2026-11-02 — Thomas picked extras handed out by claim link.
- 2026-11-02 — Thomas picked sign in before buying (no guest checkout).
- 2026-11-02 — Thomas picked a per-community feature flag.
- 2026-11-02 — Thomas confirmed badges must be for in-person attendance (no
  digital-only passes) to stay clear of app-store in-app purchase rules.
- 2026-11-02 — Thomas picked layout A: Badges screen off the Event hub, owned
  badges under Me → Tickets.
- 2026-11-02 — Thomas picked Stripe connected by pasting a restricted key.
- 2026-11-02 — Thomas asked for simple, manually triggered comp refunds; picked
  owed-when-qualified, up to comp badge price, per row + Refund selected, DM.
- 2026-11-02 — Thomas picked comp refunds as a panel on Admin → Tickets.

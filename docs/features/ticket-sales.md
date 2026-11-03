# Ticket sales

Attendees buy event badges inside the app, paying through the community's own
Stripe or PayPal account. The badge lands on their account with a check-in QR,
and on the event's check-in roster already linked, so the desk never has to
match a buyer by hand. Wizzrobe takes no fee and never holds money: buyers pay
on the store's hosted page, and refunds go through the store's API.

Gated by `FeatureFlag.TICKET_SALES` (pages, REST and service halves; see
[feature-flags.md](feature-flags.md)). Built only in the new app (`web/`); the
NiceGUI app has no ticket screens.

## Where it lives

| Surface | What |
|---|---|
| Work → Admin → Tickets (`web/src/admin/tickets/`) | STAFF only. Stores (connect Stripe with a pasted restricted key, PayPal with a REST app's client id + secret, sandbox by default with one switch to go live), badge types, orders with refunds, and the **Comp refunds** panel |
| Event → Badges (`web/src/tickets/BadgesScreen.tsx`, route `event/badges`) | Every badge type with a stepper and a sticky pay bar. The Event hub's Badges card links here while anything is on sale or the viewer owns one |
| Me → Tickets (`MyTickets.tsx`) | Your own badge with its check-in QR, and a copyable claim link for each extra you bought |
| Claim (`ClaimScreen.tsx`, route `badges/claim/:token`) | Who sent it, which badge, one Claim button. Sign-in required |
| Check-in desk (`web/src/work/DeskScreen.tsx`) | Badges show on the roster like any other badge. The search matches badge codes, so a handheld scanner works anywhere; a camera Scan button (`ScanBadge.tsx`) appears where the browser has `BarcodeDetector` |

Services: `TicketStoreService` (stores, sealed secrets), `TicketService` (badge
types, on-sale list, my badges, claims, refunds), `TicketCheckoutService`
(checkout and the store webhooks), `TicketCompRefundService` (who's owed a comp
refund). Store adapters sit behind `application/services/ticket_gateways.py`, so
another store is one adapter. Models: `models/ticket.py`, described in
[data-model.md](../reference/data-model.md#ticket-sales).

## Buying

1. The buyer picks quantities and taps Pay by card or PayPal. Wizzrobe creates a
   `PENDING` order, holds the quantity for 30 minutes (`hold_expires_at`), and
   opens the store's hosted page (an in-app browser in the native shells, which
   Apple and Google allow for tickets to a real-world event).
2. The store sends the buyer back to Event → Badges, which shows "Confirming…"
   and polls the order. Only the store's **signed webhook** marks it `PAID`; the
   return URL proves nothing.
3. On payment the buyer's first badge is theirs. Every extra starts with no
   owner and a single-use claim link (the token is sealed so the buyer can copy
   it again, and looked up by its sha256). The buyer gets a DM.
4. An abandoned checkout releases its hold by itself: availability counts only
   holds that haven't lapsed, and the next checkout marks lapsed orders
   `EXPIRED`. No worker is involved.

Webhooks land on `/api/ticket-webhooks/{provider}/{token}`. The token names the
store row, and the community comes from that row, never from the payload. A
forged signature, a stale timestamp or a replayed event changes nothing.

Badge types are for in-person attendance only (physical extras such as a shirt
are fine). Digital-only passes would need Apple and Google in-app purchase, and
the admin form says so.

## Refunds

Staff refund an order from Admin → Tickets; Wizzrobe calls the store's refund
API. A refund made in the store's own dashboard comes back by webhook. Either
way the badges go `REFUNDED`, and the next roster sync marks them refunded on
the desk.

**Comp refunds.** Anyone comped by the check-in comp rules (comp roles, or
volunteers whose scheduled hours reach the comp tier) who also paid for their
own badge is listed as owed a refund, with hours scheduled and worked. Staff
refund a row, or tick rows and press Refund selected; a confirm lists each
person and amount first. The amount is at most the price of the badge type
marked **covers comps** (a VIP buyer keeps paying the difference), extras bought
for other people are never touched, and the badge stays valid. The person gets a
DM once the store confirms.

## On the check-in roster

`CheckInService.sync_event` mirrors every owned badge in the check-in event's
event (`check_in_tickets.apply_tickets`), after the Matcherino sales and before
the comps:

- Each badge type becomes a `CheckInTier` (`ticket_type` set, title, price and
  lanyard following the type) and each owned badge a `CheckInPass` (`ticket`
  set, `code` = the badge code the QR encodes). So the lanyard, the desk search,
  the filters and the badge list on a card work unchanged.
- An owner with no roster row gets one with source `WIZZROBE` and link method
  `TICKET`, already linked to their account. Someone already on the roster (a
  Matcherino buyer, a walk-up) keeps their row and gains the badge; a `COMP` row
  becomes `WIZZROBE`, so it outlives the comp.
- A Matcherino buyer who also holds a Wizzrobe badge isn't withdrawn when their
  Matcherino purchase is refunded.
- A `WIZZROBE` row whose owner no longer holds a badge is withdrawn, or goes
  back to `COMP` if they're comped. A refunded badge keeps its pass, marked
  refunded. A claimed extra moves to its new owner's row.
- An unclaimed extra has no owner, so it isn't on the roster until claimed.

The check-in sync worker scans events with badges as well as Matcherino venues
and comp rules, and syncs an event on the next tick (about a minute) after any
of its badges is bought, claimed or refunded. Staff can still press Sync.

With `TICKET_SALES` off the sync ignores badges, and a check-in event with no
venue and no comp rules has nothing to sync.

## Security

- Store secrets are sealed per community (`secret_box`, purposes `ticket_store`
  and `ticket_store_webhook`, context the tenant id) and never appear in a
  response, log or audit row. Stripe takes only a restricted key (`rk_`); a
  secret key is refused with a pointer to restricted keys.
- STAFF manage stores, types, orders and refunds (audited, `ticket.*`
  `AuditActions` and `EventType`s). CHECK_IN_DESK sees badges on the roster
  only. Members buy and claim their own.
- Claim tokens are random (144 bits), single-use and looked up by their hash.

## Dev data

`scripts/seed_tickets.py` seeds a test-mode Stripe store and a sandbox PayPal
store (fake credentials; run with `MOCK_STRIPE=1` / `MOCK_PAYPAL=1`), three
badge types and orders for `player_one` in every state. The check-in seed runs
after it, so `player_one`'s held badges show on their roster row (the mock
Matcherino sales already link a row to them).

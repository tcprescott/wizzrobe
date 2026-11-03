# Event check-in

Behind `FeatureFlag.EVENT_CHECK_IN`, off by default. For in-person events whose
registration runs on Matcherino.

Staff create a check-in event and give it the Matcherino **venue** ID: the
ticketed event that sells badges (SpeedGaming Live 2026 is venue 182105), not
the tournaments bounty attached to it (182107). Wizzrobe mirrors the venue's
badge sales into a local roster, one row per buyer with the badges they hold,
matches each buyer to a Wizzrobe account where it can, and volunteers check
people in at a phone-friendly desk. Matcherino only tells us who bought a
badge; who has *arrived* is recorded here.

Matching to an account is a nice-to-have. An unlinked buyer checks in like
anyone else.

Until October 2026 the roster came from the bounty's participant list
(`/bounties/participants`). That path is gone: the bounty had no badge tiers,
and every badge buyer is auto-joined to the tournaments bounty anyway.

## The Matcherino API is unofficial

Matcherino has no public API. `application/utils/clients/matcherino_client.py`
calls the endpoints its own web app uses, with Matcherino's go-ahead but no
support promise. Full wire shapes and everything else we know about the API:
[matcherino-api.md](../reference/matcherino-api.md).

| Endpoint | Use |
|---|---|
| `POST /__api/auth/token` `{"appName": "WEB", "refreshToken"}` | Mints the access token (a JWT, 24 h) sent as `x-mno-auth: Bearer …` |
| `GET /__api/venues/admin/purchaseData?venueId=` | Every badge purchase, in one unpaged list. Venue admins only |
| `POST /__api/venues/pass/listPrivate` `{"venueId"}` | The venue's badge types (Matcherino *passes*) with `qtySold`. Venue admins only |
| `GET /__api/bounties/findById?id=` | The venue's title (`kind: "venue"`), so staff can confirm an ID before saving |

All answer `{"status", "body"}` envelopes; failures come back as
`{"status": 4xx/5xx, "error": {"message"}}`. The two POSTs send JSON as
`text/plain`, as the web app does.

### The stored login

The two venue endpoints need a signed-in **admin of the venue**. Each community
saves its own login, a `MatcherinoLogin` row holding the refresh token of a
Matcherino account that administers that community's venues. Staff paste it in
the **Matcherino login** card at the top of **Admin → Check-in**
(`theme/matcherino_login_card.py`), which also explains where to find it: sign
in to matcherino.com as that account in a private window, click the card's
**Copy Matcherino login** bookmarklet there, close the window without signing
out, and paste. Signing out of Matcherino (or a Matcherino tab idling for six
hours, which signs out on its own) revokes the token, so a session nobody comes
back to keeps it alive (detail in
[matcherino-api.md](../reference/matcherino-api.md#authentication)). The card
accepts the bare token, the `/auth/token` request payload, or the
`credentials` cookie's value, URL-encoded or not.

The bookmarklet copies to the clipboard rather than opening Wizzrobe: a private
window isn't signed in to Wizzrobe, and a URL that pre-filled the card could be
used to plant someone else's token. Off matcherino.com it only says to drag it
to the bookmarks bar.

Saving signs in to Matcherino once with the pasted token and refuses it if
Matcherino does, so a typo or a pasted access token fails at the form rather
than at the desk. The card then shows which Matcherino account the login
belongs to, when it was saved and by whom, and never the token itself. When
Matcherino stops accepting a saved token, syncs fail with a message pointing
back at the card; paste a fresh one there. The login is write-only, audited as
`matcherino_login.set` / `.cleared` without the value, and read unmasked only by
`CheckInService` (`MatcherinoLoginService.resolve`). It replaces the former
deployment-wide `MATCHERINO_REFRESH_TOKEN` environment variable, which is no
longer read.

**Refused-login alert.** The card's **DM when Matcherino refuses it** picker
names one Staff member (or nobody). After each Matcherino read the sync calls
`MatcherinoLoginService.note_sync_result`: a `MatcherinoAuthError` DMs that
person once, with an **Update the Matcherino login** button to Admin →
Check-in; a good read or a freshly saved token re-arms it. The once-only claim
is one conditional `UPDATE` of `MatcherinoLogin.alerted_at`, so two syncs
failing together send one DM. An outage (anything that isn't a refusal) alerts
nobody, since a new token wouldn't fix it; the desk shows it. A recipient who
has lost Staff is skipped. Removing the login clears the recipient with it.

The client mints an access token on first use and caches it in the process,
keyed by a hash of the refresh token, until five minutes before it expires: two
communities never share an access token, and replacing a login misses the
cache. A 401/403 re-mints once and retries; a second refusal raises
`MatcherinoAuthError` saying the account must administer the venue. With no
login saved, a sync fails before calling Matcherino and the desk shows
"Matcherino ticket sync isn't set up". Neither token ever appears in an error
message.

### Built to break safely

- Each parse is strict about what check-in needs (purchase `id`, `passId`,
  `userId`, `code`, the buyer's `id` and `displayName`) and raises
  `MatcherinoAPIError` rather than guessing.
- **Contact details never reach the database.** Purchases carry the buyer's
  name, email, phone and address fields (empty today, because SG Live doesn't
  require them) plus a payment ledger. `source_data` is an allowlist of the
  fields check-in can use, so a venue that starts collecting addresses doesn't
  start storing them here.
- **A short read is a failure.** The purchase list is compared with the sum of
  each badge type's `qtySold`; fewer purchases than that means the list was cut
  off, and a cut-off list would look like people being refunded.
- A sync that fails, **or comes back with no badges while the roster has live
  ones**, records `CheckInEvent.last_sync_error` and changes nothing.
- The desk shows the last good sync time and the error in its header, so
  volunteers can see the roster is stale.
- Check-in itself never touches Matcherino. If the endpoint dies mid-event the
  desk keeps working on the last roster, and staff add late buyers as walk-ups.
  Matcherino's own door scan (`venues/pass/use`) is deliberately not called.

`MOCK_MATCHERINO` swaps in `MockMatcherinoClient` (refused under
`ENVIRONMENT=production`). `./start.sh mock` and `validate` set it.

## The model

| Model | What |
|---|---|
| `CheckInEvent` | Tenant-scoped. `name`, nullable `matcherino_venue_id` (unique per tenant), the comp rules `comp_roles` (list of `Role` values) and `comp_volunteers`, `status` (`DRAFT`/`OPEN`/`CLOSED`), `sync_interval_minutes`, and the sync bookkeeping `last_synced_at`/`last_sync_error`/`last_sync_count` (buyers holding a badge) |
| `CheckInEntrant` | One person on a roster. `source` (`MATCHERINO`/`WALK_UP`/`COMP`), `matcherino_user_id`, `display_name`, the identity hints (`auth_provider`, `auth_id`), `registered_at` (their first badge purchase), `comp_reasons` (why they're comped: `Role` values and `'volunteer'`), `source_data` (their trimmed Matcherino account), the matched `user` + `link_method` + `linked_by`, `withdrawn_at`, `checked_in_at` + `checked_in_by` |
| `CheckInTier` | Tenant-scoped. One badge type on the venue: `matcherino_pass_id`, `title`, `amount_cents`, `role` (`player`/`spectator`) |
| `CheckInPass` | Tenant-scoped. One purchase: `tier`, the buyer's `entrant`, `buyer_matcherino_user_id`, the door `code`, `purchased_at`, `refunded_at`, `removed_at`, trimmed `source_data` |
| `User.matcherino_user_id` | Matcherino's numeric account id. Global and **unique**, unlike the self-entered `matcherino_username` |

`unique_together` on the entrant is `(event, matcherino_user_id)` and
`(event, user)`: one row per buyer, and one row per account. Postgres treats
`NULL`s as distinct, so walk-ups and unlinked rows don't collide. Badge types are
unique per `(event, matcherino_pass_id)` and badges per
`(event, matcherino_purchase_id)`.

**Badges are per purchase, not per person.** A buyer can hold several: in the
SG Live 2026 sales three buyers bought two badges the same day (Base with Super
VIP, Base with VIP, Base with a Day Pass), almost certainly one for a friend.
The venue doesn't name the friend, so the badge's `code` is how the desk finds
them. Check-in stays one tap per *person*; the card lists every badge they hold
so the volunteer hands over both.

A buyer whose badges are all refunded, or gone from the feed, gets
`withdrawn_at`, never a delete, so a check-in already recorded against them
survives. A new badge clears it. A badge that drops out of the feed gets
`removed_at` for the same reason. A badge type the venue deletes keeps its row;
badges sold under it still point at it (the purchase embeds its `pass`).

`link_method = MANUAL` with `user = NULL` means staff unlinked the row on
purpose. The sync never auto-links such a row again; staff unlink exactly when
an automatic match was wrong.

## Comps

Some people get in without buying a badge. Each check-in event sets its own
comp rules in the event dialog:

- **Comp roles** (`CheckInEvent.comp_roles`, a list of `Role` values): everyone
  holding one of these roles in the community. Any role a community can grant
  is allowed; `SUPER_ADMIN` isn't.
- **Comp volunteers** (`comp_volunteers`): anyone whose published volunteer
  shifts inside the community's event window add up to the **lowest volunteer
  comp tier** (Admin → Settings → Volunteers; 8 hours for SGL). These are *scheduled* hours,
  the same total the volunteer roster shows, because most shifts haven't
  happened yet when the desk opens. Unpublished auto-scheduler drafts don't
  count, and nobody is comped this way while volunteering is turned off.

Each sync works out who is comped (`check_in_comps.comped_users`) before it
touches the roster, then:

- A comped person who isn't on the roster gets a `COMP` row (source and link
  method `COMP`) tied to their account.
- A comped person who is already there (a buyer, a walk-up) keeps their row and
  gains `comp_reasons`. A comped buyer is never withdrawn for having no badge.
- Someone who stops qualifying loses their comp. A `COMP` row is withdrawn,
  unless they've already checked in, in which case they stay present as with a
  refund. A buyer keeps their row and just loses the Comp chip.
- A comp who later turns out to be a buyer (the purchase auto-links to their
  account, or staff link it by hand) stays one person: the purchase row takes
  over the comp and any check-in already recorded, and the `COMP` row is
  deleted (`check_in_comps.absorb_comp_row`). So the link dialog's suggestions
  and member search still offer someone who is on the roster only as a comp
  (`search_members(..., for_link=True)`); the walk-up search doesn't.

A `COMP` row can't be relinked, unlinked or removed at the desk; change the
event's comp rules instead. An event with comp rules but no venue still syncs
(the worker polls it too), so a staff-and-volunteers-only door works. A
Matcherino failure stops the whole sync, comps included, so the roster never
changes halfway.

## Lanyards

The lanyard tells the desk what to hand someone, so it's the one thing the desk
shows loudly. Each person gets exactly one, the highest they qualify for
(`check_in_rules.lanyard_for`):

| Lanyard | Who |
|---|---|
| **Staff** | Comped by one of the event's comp roles |
| **Volunteer** | Has any published volunteer assignment in the community (or a volunteer comp) |
| **VIP** / **Base** / **Day Pass** | The best badge type they still hold, by that badge type's lanyard |

So a staffer who bought a VIP badge wears Staff, and a Day Pass only shows for
someone who holds no other badge. Walk-ups with no comp or badge get none.

Each badge type (`CheckInTier.lanyard`, a `CheckInTierLanyard`: VIP, Base or
Day Pass) has its own lanyard. The sync guesses it once when the badge type
first appears (`check_in_sales.guess_lanyard`: "VIP" in the title → VIP, "Day"
→ Day Pass, else Base), and staff can change it in the event dialog
(`CheckInService.set_tier_lanyard`, audited as `check_in_tier.updated`). The
sync never overwrites a lanyard after that. Migration 85 applied the same guess
to badge types already synced.

### Handouts are a follow-up

Tiers tell the desk *which* badge someone gets. What each tier is handed beyond
the badge (lanyard, VIP swag), and who has received it, is not tracked. The
intended extension is two tables, `CheckInTierItem` (what a `CheckInTier` is
handed) and `CheckInHandout` (what an entrant has received). It changes no
column that exists now. Tiers come only from Matcherino; there is no manual
tier for walk-ups or door sales, which should be sold as Matcherino badges.

## Matching buyers to accounts

Each purchase carries the account the buyer signed in to Matcherino with:
`authProvider` + `authId`. For `discord` the id is the Discord snowflake; for
`twitch` it is the Twitch user id. Of SG Live 2026's 169 buyers, 137 had one of
the two (70 Twitch, 67 Discord); the rest signed in with Google (29), Facebook,
YouTube, Battle.net or Twitter.

`CheckInService._auto_link` tries these in order and takes the first hit:

| Rule | Matches |
|---|---|
| `MATCHERINO_ID` | `User.matcherino_user_id` = the buyer's `userId` (a link remembered from an earlier event) |
| `DISCORD_ID` | Discord sign-in, `authId` = `User.discord_id` |
| `TWITCH_ID` | Twitch sign-in, `authId` = `User.twitch_user_id` |
| `MATCHERINO_HANDLE` | the `#id` suffix of a self-entered `User.matcherino_username` = `userId` |

The bounty feed also listed linked Twitch logins (`socials`), which a
`TWITCH_LOGIN` rule matched against `User.twitch_username`. Purchases carry no
socials, so that rule went with the bounty path; migration 83 folds the rows it
had linked into `TWITCH_ID`, which the desk shows the same way.

Exact identifiers only, matched across all of Wizzrobe: a buyer's Discord or
Twitch id is the account they signed in to Matcherino with, and staff who can
run the desk can already see the sale. Name similarity is only ever **suggested** (`suggest_users`, ranked
`difflib` against community members) for staff to confirm.

Two buyers that resolve to one account link only the first.

**What a link writes to the account.** `matcherino_username` is where prize money
is sent, so this is deliberately narrow (`check_in_accounts.remember_matcherino_account`):

- Only a `DISCORD_ID` or `TWITCH_ID` match (OAuth-verified ids)
  or a manual link (a person at the desk confirmed it) records anything. A
  `MATCHERINO_HANDLE` match is **not** promoted: the handle is self-typed, and it
  keeps matching on its own.
- It fills `User.matcherino_user_id` and `matcherino_username` **only when
  empty**, never takes an id another account holds, and writes a
  `user.profile_updated` audit row with `source: check_in`.
- Unlinking undoes it (`forget_matcherino_account`): staff unlink when a match
  was wrong, and a wrong id left in place would re-link that person at every
  later event and lock the real owner out. The id is cleared if it matches the
  row; the handle only if it is still exactly the one check-in filled in.

## Who may do what

| Action | Who |
|---|---|
| Create, edit, open/close, delete events; look up a venue | `STAFF` (`AuthService.can_manage_check_in`) |
| Check in, undo, link, unlink, sync | `STAFF` or `CHECK_IN_DESK` (`can_run_check_in_desk`); the system user for the worker |
| Add a walk-up, remove a walk-up | `STAFF` only |
| Check in someone withdrawn (refunded, no badge left, comp lapsed) | `STAFF` only, via **Check in anyway** (`check_in(..., despite_withdrawn=True)`), audited with `despite_withdrawn` |

`Role.CHECK_IN_DESK` is a per-tenant role for check-in volunteers. It appears in
the Users dialog and the Discord role-mapping picker on its own (both read
`Role.tenant_grantable()`).

Only walk-ups can be removed: a Matcherino row would come back on the next sync
(refund the badge instead). An event's venue can't change once it has a
Matcherino roster (the next sync would withdraw everyone); create a new event
instead.

## Concurrency

Several phones work one event, and the worker polls it too:

- **Check-in and undo are conditional updates** (`claim_check_in` /
  `release_check_in`). Two phones tapping the same person record one check-in,
  one audit row and one event; the second gets "already checked in by …".
- **One sync per event at a time.** An in-process `asyncio.Lock` per event
  serialises the worker, staff and every desk's Sync (single process, see
  [scaling-roadmap.md](../scaling-roadmap.md)). The roster is read *after* the
  Matcherino fetch, so desk actions made during it are seen.
- **Auto-links are conditional** (`claim_auto_link`): a row a desk linked or
  unlinked mid-sync is left as the desk set it.
- **The sync writes only its bookkeeping columns** on the event
  (`record_sync`), so a staff edit made during a slow fetch is not reverted.
- **A short read is a failure** (see above), so a cut-off list never looks like
  people being refunded.
- A unique-constraint race that slips through anyway (a walk-up, an event's
  venue, a link) surfaces as a `ValueError` message, not a raw error.

## The service

`application/services/check_in_service.py`. Every public method carries
`@requires_feature(FeatureFlag.EVENT_CHECK_IN)`.

| Method | Does |
|---|---|
| `create_event` / `update_event` / `delete_event` | Event CRUD. One event per venue per community |
| `preview_venue` → `VenuePreview(venue, tiers)` | The venue's title and badge types for the admin dialog. Reading the badge types needs the stored login, so a venue that previews is one the sync can read |
| `list_events` / `list_open_events` / `get_event` / `has_open_event` | Reads |
| `sync_event(actor, event_id, audit=True)` | Work out comps, then upsert buyers by `matcherino_user_id`, withdraw/rejoin on badges held, auto-link, then upsert badge types and badges. then reconcile comp rows. Needs a venue or comp rules. Returns `SyncResult` (`total` buyers holding a badge, `badges` held, `comped`). `audit=False` is the worker's poll |
| `roster` / `get_entrant` | Reads, with each entrant's badges and their tiers prefetched |
| `tiers_for(event)` | The badge types, dearest first, for the desk's chips |
| `volunteer_user_ids()` | Accounts with a published volunteer assignment in this community (drafts excluded), for the **Volunteer** chip. An empty set when `FeatureFlag.VOLUNTEERS` is off, never an error |
| `suggestion_counts(event, entrants)` | How many suggestions `suggest_users` would give each unlinked, non-comp row, for the whole roster in one community read; the scoring runs in a thread. The desk caches the answer until a row is linked, unlinked, added or renamed, and shows it on the clickable **Not linked** button |
| `suggest_users` / `search_members(event, query, for_link=False)` | Candidate accounts for the link and walk-up dialogs, community members not already on the roster. Linking (`suggest_users`, `for_link=True`) also offers comp-only members, since the link folds their comp in |
| `check_in` → `CheckInOutcome(entrant, already)` / `undo_check_in` | The desk's main actions |
| `link` / `unlink` | Manual matching |
| `add_walk_up(event_id, user_id= or name=, check_in=True)` / `remove_entrant` | Walk-ups (staff) |

`application/services/check_in_desk_service.py` (`CheckInDeskService`) holds the
desk's two gated reads for the REST and MCP surfaces, which have no page gate in
front of them: `events_for(actor)` (every event for staff, open ones for the
desk) and `roster_for(actor, event_id)` (a draft event is `404` for a desk
volunteer). `CheckInService` is at its file-length limit, so they live beside it.

`api/routers/check_in.py` (`/api/check-in`, behind the flag) and the MCP tools
`list_check_in_events`, `get_check_in_roster`, `check_in_attendee`,
`undo_attendee_check_in`, `link_attendee`, `unlink_attendee`, `add_walk_up` and
`sync_check_in_event` expose the desk's actions. They call the same service
methods the desk does, so the withdrawn-row rule and its staff override apply
unchanged. See [rest-api.md](../reference/rest-api.md).

`application/services/check_in_comps.py` works out and applies comps (see
[Comps](#comps)); `check_in_accounts.py` holds the identity lookups and what a
link writes back to the account.

`application/services/check_in_sales.py` is the data half of the sync:
`buyers_of(sales)`, `buyer_fields`, and the `apply_tiers` / `apply_badges`
upserts.

`application/services/check_in_rules.py` holds the pure rules the desk and the
service share: `lanyard_for` (see [Lanyards](#lanyards)), `entrant_filters(entrant)`
(the roster states a row belongs to, over `ROSTER_FILTERS`; the desk offers only
`DESK_FILTERS`), `active_badges` (unrefunded, unremoved, dearest first),
`summarize(entrants) -> RosterCounts`, `handle_id`, and `resolve_link` (the
auto-link precedence). `summarize` gives the desk's headline numbers; withdrawn
rows count only as withdrawn.

## Sync worker

`application/services/check_in_sync_worker.py`, a 60 s `run_worker_loop` tick.
It scans open events with a venue (cross-tenant), and syncs each one inside its
tenant's scope as the system user once `sync_interval_minutes` has passed. After
a failure it backs off from the failure time rather than retrying every tick.
Tenants with the flag off are skipped. No environment switch: it only touches
events staff have opened. Run one tick by hand with
`poetry run python scripts/run_worker_tick.py check_in_sync`.

## Audit and events

| Action | Audit | Event |
|---|---|---|
| `check_in_event.created` / `updated` / `deleted` | yes | no (tenant-internal config) |
| `check_in_tier.updated` (a badge type's lanyard) | yes | no (tenant-internal config) |
| `check_in_event.synced` | manual syncs only | yes, polls included |
| `check_in_entrant.checked_in` / `check_in_undone` | yes | yes |
| `check_in_entrant.linked` / `unlinked` | yes | yes |
| `check_in_entrant.walk_up_added` / `removed` | yes | yes |

## The desk

`pages/check_in.py`, gated on `roles=[STAFF, CHECK_IN_DESK]` and the flag.

- The desk is the **Check-in Desk** tab of the Volunteer hub
  (`/volunteer/check-in-desk`). It opens the only open event, or lists them when
  several are open; `?event=<id>` picks one. The tab shows while an event is open
  (or always, for a desk role with nothing else on the hub, where it says no
  event is open). Volunteer in the menu shows to a desk role whenever check-in
  is on, so desk workers can find it before doors open (`BaseLayout` via
  `AuthService.can_view_check_in_desk`). `/checkin` and
  `/checkin/{event_id}` redirect there.
- The desk is phone-first. The event title, help, Sync and Add walk-up sit at
  the top and scroll away; the search box with the filter menu beside it (All /
  Not yet / Checked in / Comped / Walk-ups, each with its count) and the progress
  line ("7 of 14 checked in · synced 09:13") stick under the app header, about
  110px. The full sync line shows under them only when the roster may be stale
  (an error, or never synced). Pinning the title and a wrapping filter switch too
  left a phone room for one card and none above the keyboard. **Comped** is everyone with a comp,
  buyers and walk-ups comped on top of a badge included. All also
  lists people whose badge was refunded, so a search still finds them, marked
  **Refunded**; they don't count towards the progress bar. Below
  `md` each person is a card with a full-width 48 px Check in button;
  Link/Unlink/Undo/Remove sit behind a ⋮ menu. On desktop it's a table with the
  same actions.
- Each person shows **one lanyard** in its own colour (Staff red, Volunteer
  purple, VIP gold, Base blue, Day Pass teal): what the volunteer hands over. A
  buyer holding more than one badge gets a note naming the extra ones ("+ Base
  badge"), since each needs its own lanyard.
- Search runs in the browser (Quasar `filter-method`), so it doesn't wait on the
  server. It matches badge codes as well as names.
- Tapping Check in on a Matcherino buyer with no linked account opens the link
  dialog first, titled "Check in <name>": pick their account (link, then check
  in) or **Skip, just check in**. Cancel records nothing. Named walk-ups and
  rows staff unlinked on purpose (`link_method = MANUAL`) skip the prompt, and
  so does a row another desk linked or checked in since the card was drawn.
- A withdrawn row (Refunded, No badge, No longer comped) has no Check in button.
  Desk volunteers see "Send them to staff"; staff get an outlined **Check in
  anyway** behind a confirmation. The service refuses it too, and the claim is
  conditional on the row still being active, so a sync that withdraws someone
  mid-tap can't slip a check-in through.
- **Can we DM them?** Checking in someone linked to an account sends them a
  short "you're checked in" Discord DM (`check_in_dm.start_dm_check`, through
  `discord_queue`), and the card shows how it went a moment later:
  "Discord DMs reach them", "Discord won't let us DM them" (DMs closed or no
  shared server: ask them to join and allow DMs from server members), "turned
  Discord DMs off in Wizzrobe" (not sent; ask them to turn it on), or "Discord
  didn't answer" (our side, says nothing about them). Every match call, crew
  ask and shift reminder goes out the same way, so this is the moment to fix
  it, while they're at the desk. **Send the test DM again** in the ⋮ menu
  (`CheckInDeskService.send_test_dm`) retries after they've changed their
  settings. The result is stored on the row (`dm_status`, `dm_checked_at`),
  exported in the CSV and returned by the REST/MCP roster. Named walk-ups and
  unlinked buyers have no one to DM, so nothing is sent.
- After a check-in an Undo bar shows for eight seconds with "Hand over: VIP
  lanyard" (plus any extra badges).
- Staff get an **Add walk-up** button (person icon) beside Sync in the sticky toolbar.
- Export CSV downloads the whole roster, not just the filtered view.

**Several phones at once.** Every roster change publishes on
`application/events/check_in_live.py`. Each open desk subscribes through
`theme/realtime.register_view(..., channel=check_in_live)`, ignores other
events, re-reads its roster and flashes the changed card. A sync sends one
`ROSTER` signal, not one per row. `theme/realtime.refresh_on_reconnect`
re-reads after a socket blip, so a phone that was locked catches up. Both are
in-process and share the match board's single-worker constraint
([scaling-roadmap.md](../scaling-roadmap.md)).

## Admin

Admin → **Check-in** (Operations group, STAFF + flag): the **Matcherino login**
card ([the stored login](#the-stored-login)), the events table, a
New/Edit dialog with a **Look up** button that confirms the venue by title and
lists its badge types (and refuses a plain bounty ID, the usual mix-up), and a
**Desk** shortcut per event.

## Dev seed

`scripts/seed_check_in.py` syncs the `MOCK_MATCHERINO` venue through the real
service, so pressing Sync in dev changes nothing. The sales are lined up with
fixtures so one sync produces every link method (see the comment above
`MOCK_PURCHASES`), all four badge types, a two-badge buyer and a refunded buyer,
comps for Staff and volunteers (staff_user as a `COMP` row; player_one at exactly
8 scheduled hours and player_two above every tier comped on top of their badges),
and the seed adds a manual link, a buyer whose badge left the feed, a
check-in by `checkin_desk` (the fixture holding only `CHECK_IN_DESK`), a member
walk-up and a named walk-up, a draft event and a closed one, and a placeholder
Matcherino login so the card shows its saved state. Tenants without the flag are
skipped.

## Tests

- `tests/services/test_check_in_service.py`: matching rules and precedence, sync
  idempotency, withdraw/rejoin, badges per purchase, refunds, removed badges,
  renamed and deleted badge types, that contact details aren't stored, the
  empty-response and API-error guards, permissions, walk-ups, the worker.
- `tests/utils/test_matcherino_client.py`: the wire shape through
  `httpx.MockTransport`, the sign-in and token cache, 401 re-minting, the
  missing-login and short-read errors, and that neither token leaks into a
  message.
- `tests/services/test_check_in_badges_and_comps.py`: badges and tiers, the
  volunteer chip, and comps (roles, the hour threshold, drafts, the flag off,
  losing and regaining a comp, a comped buyer, a comp merging into a later
  purchase or a manual link, what the desk can't do to a comp row, a
  comps-only event under the worker). Shared fixtures live in
  `tests/services/check_in_support.py`.
- `tests/tenancy/test_check_in_tenant_isolation.py`: all four models.

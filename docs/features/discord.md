# Discord Integration

What the Discord integration does for users: match DMs, guild-role → app-role sync,
and the mock mode that makes all of it developable offline. Implementation mechanics
(bot singleton, DM queue, button handlers, `custom_id` formats) are in
[reference/discord-integration.md](../reference/discord-integration.md).

## Match notifications

DMs fire on match lifecycle transitions. Approved crew are left out of every match DM below while the match has no stage; the stage-cleared DM is the exception (see [match participation](match-participation.md)).

| Event | Recipients |
|---|---|
| Scheduled / rescheduled | players (with Acknowledge), approved crew and watchers (info; watchers get Unwatch), tournament subscribers (with crew-signup buttons) |
| Players changed, time unchanged | players only — a fresh Acknowledge request |
| Race room opened (players) | Join the race room | the racetime.gg room itself — off-site, like the seed link, because that is where the race is. Safe to link only because the room's slug is the one racetime returned from `startrace` |
| Harder preset offered | players, when the tournament has a `hard_preset` and the seed is not yet rolled |
| Checked in / confirmed | players, approved crew and watchers — one DM each via `notify_match_participants`. The check-in DM lists every assigned seat, and a seated player's copy leads with **Your seat** (`player_copies`) |
| Started / finished | approved crew and watchers only, sent **silent** (no ping or phone buzz, Discord's `@silent`): the players are at the station and already know |
| Cancelled | players, approved crew and watchers, resolved before the row is deleted |
| Stage assigned / cleared | players, approved crew, watchers |
| Stage reminder | the same audience, `stage_reminder_minutes` before the match |
| Stream candidate | tournament subscribers, with crew-signup buttons |
| Crew signup | the tournament's admins and crew coordinators (`get_crew_owners`), so a pending signup doesn't wait for someone to open the board. Silent by default, with commentator and tracker as separate notices. Every copy is edited in place once the signup is approved, removed by staff, or withdrawn by the volunteer |
| Matchup ready to schedule | both entrants of a bracket matchup (also re-sent as a *rebook* if its game is later called off), sent **silent**: the matchup also waits in the player's Pick-a-time list |

That last one is the only bracket-specific message — there are deliberately no
advancement or elimination DMs. See
[brackets.md → Notifications](brackets.md#notifications). Its button opens the
schedule dialog directly; see [Calls to action](#calls-to-action).

The table shows the shipped behaviour. Each community can override any row per
kind of recipient (off, silent, or ping) on Admin → Notifications; the defaults,
including the silent matchup-ready DM and silent started/finished copies, live in
the [notification catalogue](#notification-settings-and-the-catalogue).

**Series context.** A match that is one game of a bracket matchup leads its info
block with `Round: Semifinals · Game 2 of 3 · Series 1-0`, resolved once by
`BracketService.match_dm_context` and threaded through `_match_descriptor`. Empty
for the vast majority of matches, which no bracket scheduled.

## Calls to action

Every DM that asks the recipient to do something carries a **link button** to the
control that does it. `DMLink(label, url)` goes to `send_dm(..., link=...)`, which
appends a Discord link button to whatever buttons the DM already has — a player
gets **Acknowledge** and **View your match** side by side — and hands the same
URL to the web-push mirror as its `navigate` target, so the phone notification
opens the same place.

Build the URL with `application/services/notification_links.py`. It resolves the
tenant in scope and returns an absolute, tenant-qualified link (a custom domain
wins over the platform's `/t/<slug>` prefix). It **never raises**: an unresolvable
tenant costs the button, not the DM.

| DM | Button | Lands on |
|---|---|---|
| Matchup ready to schedule | Pick a time | `/home/player?schedule=<matchup id>` — the picker, open |
| …its rebook variant | Pick a new time | the same |
| Match scheduled / rescheduled (players) | View your match | `/home/player?match=<id>`, beside Acknowledge — the match, not the fifteen-row board it sits on |
| Match scheduled / rescheduled (crew) | View your crew slot | `/home/my-crew?match=<id>` — their card under Crew you signed up for, outlined and scrolled to |
| Match scheduled / rescheduled (watchers) | — | Unwatch only; there is nothing to do |
| Match scheduled (subscribers), stream candidate | View the schedule | `/home/schedule`, beside the crew buttons |
| Seed ready | Open your seed | the randomizer — the one deliberately off-site link. A seed that isn't a URL (a Wind Waker permalink, Z1R seed and flags) rides in a code block instead, and the button reads **Copy it from Wizzrobe** and opens the match on the Player tab |
| Crew assignment | View your crew slot | `/home/my-crew?match=<id>`, beside Acknowledge — the card that also carries Confirm and Withdraw |
| Crew signup (to admins) | Review the signup | `/admin/match-schedule?match_id=<id>` — the approval toggle is on that row |
| Crew withdrew (to admins) | Fill the slot | `/admin/match-schedule?match_id=<id>` |
| Volunteer released (to coordinators) | Find cover | `/admin/volunteer-shifts?day=<shift day>` |
| Volunteer shift reminder | Acknowledge (only if not yet acknowledged) · Can't make it | `/home/my-schedule?release=<assignment id>` — the give-back dialog for that shift, open; a gone, finished or checked-in shift says so instead |
| Reschedule request (to staff) | Review the request | `/admin/match-schedule?reschedule_request=<id>` — the decision dialog, open. Not the board filtered to the match: the proposed time and the player's reason are the whole message and live in the dialog |
| Reschedule request (to the opponent) | Agree · Open the request | Discord's Agree button is the control; the link is `/home/player?agree=<request id>`, the same request open in a dialog with its own **Agree** — the web-push copy of this DM has no Discord button, so the tap target has to carry one |
| Reschedule declined (to the requester) | Ask again | `/home/player?reschedule=<match id>` — the request form, open, because a different time is the real next step after a refusal |
| Join request (to staff) | Review the request | `/admin/members` |
| Join declined (to requester) | Join the Discord server | the community's `discord_invite_url`, when set; otherwise no button. The date a new request opens is `<t:…:f>` (date and time); with auto-join on, the copy leads with "joining the server gets you in" |
| Added by staff / request closed by a role grant (to the person) | Find a tournament | `/home/tournaments` |
| ADA request opened / changed after arranging (to staff) | Open the request | `/admin/members?ada_request=<id>` — that request's dialog, open |
| Matcherino refused the check-in login (to the one person picked on the card) | Update the Matcherino login | `/admin/check-in` — the Matcherino login card is its first control |
| Checked in at the desk (to the person checked in) | See the schedule | `/home/schedule` — the DM asks nothing; its job is proving the bot can reach them before their first match call |
| Stage assigned / cleared / reminder | View your match · View your crew slot · View the schedule | per audience: players `/home/player?match=<id>`, crew `/home/my-crew?match=<id>`, watchers `/home/schedule` |
| Harder preset offered | Play the harder preset (or Back out) · Choose your settings | the action button *is* the control; the link is `/home/player?hard=<match id>`, the opt-in open |
| Harder preset agreed / broken | View your match | `/home/player?match=<id>` |
| Match preset set by staff / handed back | View your match · Choose your settings | the first when staff forced a preset, the second (`?hard=`) when the choice is the players' again |
| Tournament signup confirmed | View tournaments | `/home/tournaments` |
| Join approved (to requester) | Find a tournament | `/home/tournaments` — a new member's next step is entering something, not the match board |
| Qualifier reviewed / expiring / expired / reattempt | Submit or forfeit · View the leaderboard · Start your next run | `/qualifiers/<id>` |
| Qualifier review queue waiting (to reviewers) | Open the review queue | `/admin/qualifiers?qualifier=<id>&tab=queue` |

A DM with no ask gets no button — checked in, cancelled, state changed, volunteer
unassigned, and an **approved** reschedule (the match moved, and the reschedule
notice both players already got is the news). A button there is noise, not help.

Two things that are easy to get wrong:

**The embed suppresses the text.** `send_dm` sends the embed *instead of* the
content, so a markdown link written into the message string never renders on
Discord. It still reaches the web-push mirror (which strips the markdown), so a
text link is a fallback, never the route.

**Link the surface the recipient can use.** The matchup-ready DM pointed at
`/brackets/<id>` for a long time. That page's Schedule button is `is_staff`-gated,
so the two entrants it addressed arrived at a dialog whose only control was Close.
Check the role gate on a target before linking it.

The `?schedule=` param is honoured by `pages/home_tabs/player.py`, which opens the
dialog from the same handler the on-page button uses and matches the id against
that viewer's *own* open matchups — a forwarded link cannot open somebody else's.
A matchup that has since been booked, cancelled, or finished gets
"That matchup is no longer waiting to be scheduled." rather than a silent no-op:
a DM outlives what it points at, and a dead button reads as a broken app.

`?match=` narrows the same tab to one match for the stage DMs, with a chip
saying so and a "Show all my matches" button beside it — a one-row board with no
explanation reads as a board that lost rows. It narrows only when the viewer
plays in that match (`MatchService.viewer_relation`). Otherwise the board stays
whole and the page says what the match is to them: crew get their slot (every
role's card) outlined and scrolled to under Crew you signed up for, with past
slots shown and a note when the match's start has passed, a watcher gets a chip
with **Find it on the schedule**, and anyone else gets "That match isn't one of
yours". Filtering regardless used to show a commentator "No matches to show yet".

`?agree=<request id>` opens the opponent's reschedule request with an **Agree**
button (`theme/dialog/reschedule_agree_dialog.py`), gated by the same
`record_opponent_agreement` the Discord button calls. A stale link says why:
already agreed, closed (decided or withdrawn), or not a request waiting on this
viewer (`MatchRescheduleService.agreement_link_state`).

### Who a match DM is for

One lifecycle change reaches up to four kinds of reader, and copy written for
one is false for the others. `collect_match_audience` returns each recipient's
strongest tie (player, then crew, then watcher; the Unwatch button stays
independent), and the builders in `discord_messages.py` take an `audience`
(`AUDIENCE_PLAYER` / `_CREW` / `_WATCHER` / `_SUBSCRIBER`). Only a player reads
"Your match"; crew read "A match you're on crew for", watchers "A match you're
watching", and a tournament subscriber "A match was just scheduled", pointed at
the signup buttons under the card. Players never get the scheduled/rescheduled
builders at all: their news is the acknowledgment request.

### Stage calls

Assigning a stage tells the people it concerns. `MatchService.assign_stage`
enqueues `notify_stage_changed`, which reads the stage back off the match and
DMs the players, the **approved** crew and the watchers — a pending commentator
has not been given the job and is not told to be anywhere. Each audience gets
its own copy, card title and button (see *Who a match DM is for*). Clearing the stage
sends its own retraction ("back in the tournament room"): telling someone they
are on Kraid and never taking it back sends them to an empty stage.

`application/services/match/stage_reminder.py` sends the second DM shortly
before the match. It scans every tenant over a fixed 24-hour window
(`MatchRepository.due_for_stage_reminder`, deliberately unscoped), then
re-checks each match against its own tournament's `stage_reminder_minutes` —
0 sends nothing, and anything still outside its lead is left unstamped for a
later tick. The stamp (`Match.stage_reminder_sent_at`) is written *before* the
send, so a delivery failure or a restart cannot re-fire it, and
`assign_stage` clears it on every change so a reassignment re-arms the reminder
for the stage the match actually ended up on.

All three carry `COLOR_STAGE` rather than the scheduled/rescheduled colours: the
time did not move, where to stand did.

### Fan-out and dedup

`MatchScheduleService.notify_*` gathers the match's players, its `MatchWatcher`
rows, and tournament subscribers, then deduplicates — someone who is both a player
and a subscriber gets one DM, not two.

**Cancellation is the exception.** `notify_match_cancelled` takes a pre-resolved
`{discord_id: is_watcher}` map rather than a `Match`. Cancelling deletes the row,
and every other notifier re-queries its recipients when the queue worker finally
awaits it — by which point the cascade has removed them and the DM would reach
nobody. So `MatchService._cancel_match` resolves recipients and builds the message
*before* the delete. Recipients get a plain DM rather than the watcher variant,
whose Unwatch button would carry the id of a match that no longer exists. The card
uses `COLOR_CANCELLED`, the palette's only negative colour.

## Notification settings and the catalogue

[`application/services/notification_catalog.py`](../../application/services/notification_catalog.py)
is the source of truth for every DM Wizzrobe sends: one `Notice` per message
(key, group, title, what sends it) with a default `NotificationMode` (`off`,
`silent`, `ping`) for each kind of recipient it reaches. A community stores only
what it changed from those defaults (`NotificationSetting`); staff edit them on
Admin → Notifications. Full detail:
[notification-settings.md](notification-settings.md).

`send_dm` (and every `send_dm_with_*`) takes `notice=`, a `NoticeTarget` such as
`MATCH_SCHEDULED.to(PLAYERS)`. When given, the community's mode decides:
`off` returns `(True, DM_NOTICE_OFF)` before anything is sent or mirrored to
devices, email or Telegram; `silent` forces Discord's `@silent` (and quiet
device and Telegram copies) whatever `silent=` the caller passed; `ping` forces
a normal message. The user's own Discord-DM switch still applies afterwards.

**Adding a DM.** Declare a `Notice` with `_notice(...)` in the catalogue (a new
key, never a reused one: keys are stored, so renaming one drops every override
saved against it), give each recipient column a default, and pass
`notice=YOUR_NOTICE.to(<recipient>)` on the `send_dm` call. A sender that skips
`notice=` can't be turned off or quietened by any community.

**Staff alerts are tagged.** A match alert (a player's comment, a reschedule
request) or a volunteer alert (running late, shift given back) resolves its
recipients through `StaffAlertService.tagged_match_alert_recipients` /
`tagged_volunteer_alert_recipients`, which pair each person with the column that
governs their copy: `ON_DUTY` for whoever holds a shift with staff alerts on right
now (even if they also admin the tournament), `ADMINS` for the tournament's
admins or the volunteer coordinators, and `STAFF` only for the fallback when
nobody is on shift. The sender passes that tag to `notice.to(...)`, so a
community can, say, keep the on-shift desk pinging and silence the fallback.

## Guild-role → app-role sync

Maps a user's Discord guild roles onto application roles at login, granting what
they should have and revoking what they should not — without disturbing roles a
staff member granted by hand. The bot reads guild membership directly via the
`members` intent, so login keeps the `identify` scope and needs no extra permission.

`pages/auth.py` calls `DiscordRoleMappingService().sync_user_roles(user)` after the
`User` is upserted and before the redirect. That call **fans out across every tenant
that has a `discord_guild_id`**, syncing each inside its own `tenant_scope` and
concurrently, since one person may belong to several communities' guilds — login
latency stays at roughly one Discord round-trip instead of scaling with tenant
count. Per-tenant mappings are `DiscordRoleMapping` rows managed on the admin
**Role Mappings** tab.

### Accounts for members who have never signed in

Holding a mapped role is enough to get a Wizzrobe account: staff shouldn't have
to wait for a volunteer to log in before they can put them on a shift. Two paths
create one, both through `UserService.provision_from_discord_role_sync`, which
audits `user.provisioned` with `source: discord_role_sync`:

- **Live** — `on_member_update` passes the member to `sync_member_roles`. An
  unknown member whose new roles include one a tenant sharing the guild maps gets
  an account, then the normal per-tenant sync grants the roles (and membership).
- **Sync All Users** — before the per-user loop, `sync_all_users` lists every
  member of the *current* community's guild holding a mapped role
  (`list_members_with_roles`) and provisions those without an account, so a role
  handed out before the mapping existed still lands. The loop then syncs this
  community only (`sync_user_roles_for_tenant`), over its members plus those
  role holders, not every account on the platform. The result dialog names the
  accounts it created and everyone whose roles changed.

Only a mapping the sync would honour counts: a grantable app role, or a
tournament grant on an active tournament. An unmapped role, leaving the server
(`on_member_remove` passes no member) and bots never create anything. An existing
account is never renamed by this path; the new one takes the member's Discord
username and global avatar hash, exactly what a first login would store.

### Importing the whole server

Admin → Users › Actions › **Import from Discord…** (`DiscordMemberImportService`,
staff only) goes further than role sync: every human in the community's linked
server gets an account and a membership (`MembershipSource.DISCORD_IMPORT`,
"Discord import" in the Via column), roles or not. It exists so staff can enroll
and map people (a Challonge entrant, a bracket seat) before they ever sign in.
The dialog previews the counts first: people to add, already members, bots
skipped, and people removed from the community skipped (a removal block is never
lifted). Accounts match on Discord id, existing ones keep their name, nobody is
DM'd, and one `tenant.members_imported` audit row and event covers the run. A
second run only picks up people who joined the server since.

### Membership for anyone in the server

Role sync only makes members of people holding a mapped role. A community that
wants its whole server in turns on **Discord auto-join** (Admin →
Settings): a signed-in non-member who opens any community page is checked
against the linked guild (`is_guild_member`) and, if they're in it, added on the
spot instead of seeing the join door. Membership only, no roles; leaving the
server doesn't undo it. The same settings block takes a **Discord invite link**
that the join door offers as a button. Turn **Take join requests** off in the
same block and the server becomes the only way in short of staff adding someone
by hand. Mechanics:
[multitenancy.md](multitenancy.md).

### Two kinds of mapping

A mapping grants **either** a community-wide `Role` **or** a `TournamentGrant`
(Tournament Admin / Crew Coordinator) on one named tournament. Both shapes live in
`DiscordRoleMapping` and appear together on the admin tab; `add_mapping` enforces
the either/or, since a cross-column rule is not something the database can express.

A tournament grant must name its tournament — a guild role is guild-wide, so there
is otherwise nothing to scope the authority to. Once the tournament is no longer
`is_active` the mapping counts as absent: authority ends with the event, and
re-activating the tournament hands it back on the next sync. What each grant
actually permits: [authentication.md](../reference/authentication.md#roles).

### Source tracking is the safety guard

`UserRole.source` (`RoleSource`: `manual` | `discord`) is what makes full-sync safe:

- Roles granted by a staff member are `manual` and are **never** auto-revoked.
- Roles granted by the sync are `discord` and are revoked when the Discord role goes away.
- A manual grant over a previously-synced role upgrades the row to `manual`, pinning it against future revocation.

Tournament grants get the same guarantee from a different mechanism.
`Tournament.admins` and `.crew_coordinators` are bare join tables with no column to
record who made a row, so the sync writes a `DiscordTournamentGrant` alongside each
grant it creates and revokes only what it finds there. A grant the user already
holds is left unclaimed, and `TournamentService.add_admin` /
`add_crew_coordinator` (and their removes) delete the provenance row, which pins
the grant as manual.

### Fail-open

`sync_user_roles` never raises. A bot that is not ready, or a Discord API error,
skips the sync and leaves roles untouched — an outage can never block login. A
failure in one tenant does not abort the others. A member definitively *not* in the
guild yields an empty role set, which correctly revokes their Discord-sourced roles.

**Operational prerequisite:** the privileged **Server Members Intent** must be
enabled for the bot in the Discord Developer Portal, and the bot invited to the
guild. The code already sets `intents.members = True`; without the portal toggle
`get_member_role_ids` returns empty and the sync is a safe no-op.

Audit actions: `discord_role.mapping_added` / `.mapping_removed` for staff edits,
`role.discord_sync_granted` / `.discord_sync_revoked` for the login sync (actor =
the signing-in user), `user.provisioned` (`source: discord_role_sync`) for an
account the sync created.

## App → guild-role sync (Discord Roles from Wizzrobe)

The reverse direction, behind `FeatureFlag.DISCORD_ROLE_SYNC` (off by default):
Wizzrobe hands out roles in the community's server from what it knows. Staff set
it up on **Settings → Discord → Discord Roles from Wizzrobe**, a checklist with a
role picker per rule; a blank picker means the rule is off.

| Rule (`DiscordRoleRuleKind`) | Who gets the role |
|---|---|
| `any_tournament` | Enrolled (`TournamentPlayers`) in any **active** tournament |
| `live_match` | A player of a match that has started and not finished, started within the last 12 h (`LIVE_MATCH_WINDOW`) |
| `tournament` | Enrolled in that one tournament |
| `badge` (`lanyard`) | Holds an unrefunded, unremoved badge of that lanyard (VIP, Base, Day pass) in an **open** check-in event, linked to their account. Needs `EVENT_CHECK_IN` |

Pieces: `models/discord_role_sync.py` (`DiscordRoleRule`, the `DiscordRoleGrant`
ledger, `DiscordRoleSyncState`), `DiscordRoleSyncRepository`,
`DiscordRoleSyncService`, `discord_role_sync_worker`, and the page
`pages/admin_tabs/admin_discord_role_sync.py`.

- **Only roles we gave come off.** A `DiscordRoleGrant` row is written after
  Discord confirms an add, and only those rows are ever removed. A role the
  person already held (a moderator gave it) gets no row, so it's never taken.
  Clearing a rule takes its roles back on the next sync; Actions → *Remove every
  role Wizzrobe gave…* clears every rule and syncs at once. Turning the feature
  off leaves roles where they are, so clear first.
- **Roles the bot can't manage are refused.** `role_problem()` checks Manage
  Roles, the role's position against the bot's top role (`bot_role_access`), and
  managed roles (boosts, bot and integration roles). `set_rule` refuses such a
  role; the picker greys it out with the reason. Every sync re-checks: a rule
  that broke later (roles reordered, permission removed) gets `problem` set, is
  shown in red, and is skipped, its grants kept so the roles come off once fixed.
- **No loops with Role Mappings.** A Discord role can't be both a mapping
  (Discord → app role) and a rule here: everyone the rule covers would be granted
  the mapped app role. Both services refuse the second use.
- **Outages change nothing.** A run that can't read the server's roles, the bot's
  access or the member snapshot records `last_error` and writes nothing. A
  "bot not connected" failure mid-run stops the run; a one-off refusal is counted
  and the rest carry on.
- **Cadence.** The worker ticks every 15 s. A rule change, `match.started` /
  `match.finished` / `match.deleted`, `tournament.enrolled` / `.withdrawn` and
  the check-in sync and link events mark the tenant for the next tick; every
  community with rules or grants also gets a full pass every 10 min, which
  catches Challonge/start.gg roster syncs that publish no event.
- People without a linked Discord account or not in the server are listed on the
  card ("See who"), capped at 300.

Audit actions: `discord_role.rule_set`, `.rule_cleared`, `.rules_reset`. The
role writes themselves aren't audited per person; Discord's own audit log shows
them with the reason "Wizzrobe role sync".

## Tournament notification preferences

Users subscribe per tournament to hear about matches beyond their own.
`MatchNotificationLevel` (`models/enums.py`) sets the volume:

| Level | DMs sent for |
|---|---|
| `none` | nothing |
| `streamed` | matches assigned to a stream stage |
| `streamed_and_candidates` | stream-assigned + stream-candidate matches |
| `all` | every scheduled match in the tournament |

A subscription is a **follow** and does not require enrollment — the fan-out never
checks the player pool — so the picker spans every active tournament and merely
badges the ones the user is enrolled in.

Stored as `TournamentNotificationPreference` (user × tournament × level), set inline
under Profile → Notifications → **Tournaments you follow**
(`pages/home_tabs/player_edit_info.py`) via
`TournamentNotificationService.upsert_preference`. The card's **Discord DMs**
switch (`User.dm_notifications`) only decides whether the Discord copy goes out:
`send_dm` checks it after mirroring to devices, so the subscriber queries don't
filter on it. Fan-out reads subscribers
directly from `TournamentNotificationRepository` —
`get_match_notification_subscribers` and `get_stream_candidate_subscribers`; there
is no service-level `get_subscribers()` abstraction.

`Match.is_stream_candidate` is toggled by admins in the match dialog; turning it on
fans out to `streamed_and_candidates` subscribers with crew-signup buttons attached.

## Log in from Discord

Discord deliberately won't hand a website's sign-in to its mobile app (its iOS
universal-link rules exclude `/oauth2/authorize` whenever `response_type` is
set), so a phone user signing in through OAuth has to log in to discord.com in
whatever browser they're in. The bot gets around that by vouching for the
person itself: a button press or slash command already tells us, signed by
Discord, who pressed it.

- **`/login`** (any channel in a linked server) replies privately with a
  one-time link per active community linked to that server.
- **`/wizzrobe login-panel`** posts a permanent *Log in* panel in the current
  channel. Discord only shows the command to members with Manage Server, and
  the poster must also be STAFF (or super-admin) in the community. When the
  server backs several communities, the `community` option picks which one.
  Each post is audited as `discord.login_panel_posted`.
- **Pressing the panel's button** gives the same private reply for that one
  community. A panel for a community that's since been unlinked from the
  server says so instead.

The reply says the link works once and expires in two minutes
(`LINK_TTL_SECONDS`), and deletes itself at that moment. The link opens
`/login/discord?token=…` on the community's own address, which signs the
person in (replacing any other identity signed in on that browser), syncs their
Discord roles, and lands them on the community home. Landing there runs the usual membership gate, so a newcomer joins on arrival when the community is open or lets its Discord server's members straight in (they just proved they're in it); a Request-only or Staff-only community, or someone staff removed, still gets the front door. The token is single-use,
bound to the link's host and community, and only read after the page's
websocket connects, so a link-preview fetcher can't burn it. It isn't bound to
a browser, which is the reason for the short lifetime. Someone who presses the
button before ever visiting gets an account the same way an OAuth login
creates one; a deactivated account gets a refusal in Discord instead of a link.

No feature flag: it's on wherever the bot is in a linked server. The token
store is in-process, like the Design B handoff's, so it assumes the
single-worker deployment ([scaling-roadmap.md](../scaling-roadmap.md)).

## Mock mode

`MOCK_DISCORD=true` makes the whole integration developable with no Discord
application, bot token, or network:

- **OAuth bypass** — `/login` renders a user picker instead of redirecting to Discord. Any user in the database can be impersonated, plus a "Create test user" shortcut. Picked and created users are real rows and persist across restarts.
- **`DiscordService` stubbed** — swapped for `MockDiscordService`, which mirrors the real public surface and logs to stdout, so notification code paths run end to end.
- **Server-connect offline** — the bot-authorization flow short-circuits straight to its own callback with a mock guild id, and `member_can_manage_guild` returns `True`, so `Tenant.discord_guild_id` gets set with no Discord round-trip.
- **Bot does not start** — `DISCORD_TOKEN` is not required.

```bash
./start.sh mock                     # development + MOCK_DISCORD, MOCK_SEEDGEN, MOCK_CHALLONGE, MOCK_TWITCH, MOCK_RACETIME
MOCK_DISCORD=true ./start.sh dev    # mock Discord only
```

Mock mode is a complete authentication bypass, so the app **refuses to start** when
it is enabled while `ENVIRONMENT=production`.

**Limit:** button interactions (acknowledgment, crew signup, watch) need a live bot
connection and cannot be exercised in mock mode — test those against a real Discord
dev server.

### Mock fixture data

Guilds, roles, and per-member role assignments live in one place —
[`application/utils/mocks/mock_discord_data.py`](../../application/utils/mocks/mock_discord_data.py)
— and every `MockDiscordService` method reads from it, so the data stays
self-consistent. It is kept in sync with `scripts/seed_dev.py`: the guild ids and
mapped role ids/names match the dev seed, so a mock member's roles resolve against
seeded `DiscordRoleMapping` rows and login sync does real work.
`get_member_role_ids` hands each user a deterministic, varied role set (everyone is
at least a Volunteer). `is_guild_member` says everyone is in every server except
the ids in `MOCK_NON_GUILD_MEMBERS` — the seeded `outsider`, so the join door stays
reachable in the seeded community that has Discord auto-join on. Edit
`MOCK_GUILDS`, a guild's `roles`, `MOCK_MEMBER_ROLES` or `MOCK_NON_GUILD_MEMBERS`
to enrich the fixtures.

## Testing

Fan-out and sync logic are covered by `tests/services/test_match_schedule_service.py`
and `test_discord_role_mapping_service.py`. Actual DM delivery and button
interactions are not unit-tested — they need a live connection.

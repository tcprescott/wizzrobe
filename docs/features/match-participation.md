# Match Participation: Crew, Acknowledgment, Watching

How people attach themselves to a match — signing up as crew, confirming they will
be there, and subscribing to updates. The DMs these flows send are documented in
[discord.md](discord.md); the button handlers behind them in
[reference/discord-integration.md](../reference/discord-integration.md).

## Crew signup and approval

Commentators and Trackers (collectively "crew") sign up per match, and a staff
member approves them. An event can add a third role, **Broadcasters** (Event
settings → Crew, stored as `Event.config['broadcasters']`); everything below
applies to them the same way. With the switch off, `CrewService.required_crew`
reads a tournament's `required_broadcasters` as `0`, so the boards hide the
column, the DM drops the Broadcaster button, and `signup_crew` /
`MatchService.create_match` / `update_match` refuse new broadcasters. Signup works from the web or from Discord DM buttons — both
land in the same service, so the rules and audit trail are identical.

1. Signup inserts a `Commentator` or `Tracker` row with `approved=False`, either from the web UI (one tap on the board's **Sign up**, no confirmation step) or from the buttons on a stream-candidate DM (`discordbot/crew_signup.py`). The tournament's admins and crew coordinators get a DM linking the match on the admin board, so approval doesn't wait on someone happening to look. Home's schedule has a **Needs crew** switch that narrows the board to matches still short of approved crew for a role the tournament uses.
2. Whoever `can_approve_crew` admits — staff, the tournament's admin, **or its crew coordinator** — approves from the **Approve** button beside a waiting name on the Admin → Match Schedule run sheet, or from Match details. Approving is one tap held behind Undo for `UNDO_SECONDS` (`application/pending_actions.py`): the write, its DM and its webhook wait out the window, so an undone approval never leaves the app; the clash warning dialog appears only when they're booked elsewhere. Un-approving still confirms first.
3. The approved crew member acknowledges — from **My Schedule**, the schedule board, or the DM button.

Each side has a surface of its own for the work it owns:

| Who | Where | What it carries |
|---|---|---|
| Staff / TA / crew coordinator | Admin → Match Schedule; Reports → Crew Activity | The Approve button beside each waiting name on the board, and a Pending-only filter in the report |
| The volunteer | Home → **My Schedule** → crew section (`home_tabs/my_crew.py`) | The player run sheet (`layout='player_run_sheet'`) narrowed to the matches they crew (`MatchTableView(crew_discord_id=...)`, approved or waiting), so their Withdraw and Confirm sit in their own role's tray as on Event › Schedule. A line they withdraw from leaves the board (`_still_crews`), and `?match=` narrows it to that match with a *Showing one match only* chip. It leads the tab for anyone entered in no tournament |

The crew list is on Home rather than the Volunteer hub deliberately: crew signup
is behind neither a role nor `FeatureFlag.VOLUNTEERS`, so anyone who can sign up
must be able to see what they signed up for. It is the crew twin of
the volunteer shifts section beside it, which replaced Volunteer → **My Shifts**. It was its
own **My Crew** tab until Home came down to four tabs; `/home/my-crew` still
resolves, onto My Schedule.

Both directions DM the crew member: approving sends the assignment DM with the
Acknowledge button, and withdrawing approval sends a withdrawal notice — a
withdrawal also clears `acknowledged_at`, so without it someone who confirmed
they would cover a match is dropped from it silently.

Crew an admin picks in the match create/edit dialog are written already approved
(`approved_by` = the admin), and `MatchService` hands each new row to
`CrewService.announce_admin_assignment`: the same `crew.approval_changed` event and
Acknowledge DM as approving a signup. Dropping an approved crew member from that
dialog goes through `announce_admin_removal`, which publishes
`crew.signup_removed` and sends the withdrawal notice.

**Crew hear about a match only while it is on a stage.** A match with no stage
isn't being broadcast, so neither DM goes out for it (the approval and its event
still happen), and `collect_match_audience` leaves crew out of every match DM
until a stage is assigned. The "You're on stage" DM is then their first message.
The one exception is the stage-cleared DM, which still reaches them because they
were told they were on stage. A crew member who also watches the match keeps the
watcher copy.

```python
from application.services import CrewService

await CrewService().signup_crew(match_id, user, 'commentator')
await CrewService().undo_crew_signup(match_id, user, 'commentator')

crew = await CrewService().get_crew_member_by_id(crew_id, 'commentator')
await CrewService().approve_crew_member(crew, 'commentator', actor=actor)
await CrewService().update_crew_approval(crew, 'commentator', approved=False, actor=actor)
await CrewService().acknowledge_crew_assignment(crew_id, 'commentator', user)

# The volunteer's own list (My Crew), both roles merged and sorted by when.
await CrewService().list_my_commitments(user, upcoming_only=True)
# What else this person is committed to while that match runs — players count,
# because someone racing at 19:00 cannot commentate at 19:00 either.
await CrewService().find_scheduling_conflicts(user, match, 'commentator')
```

**Signing up before a match is booked.** Event › Schedule ends with a
*Not scheduled yet* group (folded by default; open by default on My Schedule's
crew list) listing every open Challonge, start.gg or native bracket matchup with
no game booked, players known or not, in tournaments that use crew
(`bracket_sources.list_open_for_crew`). The first signup on one makes a **crew
hold** (`CrewHoldService.signup`): a `Match` with no players and no time whose
`crew_hold_source` / `crew_hold_key` name the matchup. It is never linked to the
matchup, so players and staff still see it as unbooked and book it the usual
way. The hold carries the signups like any match, so approval, withdrawal, the
owners' DMs and My Schedule all work unchanged; a matchup's players can't crew
it (`_crew_blocked` on the row, refused by the service).

Booking or linking the matchup's first game calls `CrewHoldService.absorb`
(from `bracket_sources.schedule` / `book_ahead` / `link_existing` /
`relink_ahead` and the native `schedule_bracket_match` /
`link_match_to_bracket_match`). It moves each signup onto the booking with its
id, approval and acknowledgment, drops one from a person now playing it or
already on it, deletes the hold, publishes `crew.approval_changed` for each
approved member on the booking, and DMs the crew owners (`crew_booked_busy`)
about any approved member `find_scheduling_conflicts` says is busy then.
`CrewHoldService.list_future` is the safety net: a hold whose matchup got booked
by a path that skipped the hand-over is absorbed the next time the list loads,
and a hold everyone withdrew from is deleted. Crew get no DM of their own when
the match is booked: the stage-only rule above still holds.

### Pair commentary

On a match whose tournament wants two or more commentators, a signed-up
commentator can invite a partner (`CommentaryInviteService.invite`, from
**Invite a partner** in their own tray on the player run sheet). The partner gets
a `crew_partner_invite` DM with Decline / Accept buttons (`discordbot/partner_invite.py`)
and the same pair under Commentary invites on My Schedule. Accepting calls the
ordinary `CrewService.signup_crew` (or `CrewHoldService.signup` on a crew hold),
so every signup rule applies and staff approve each commentator separately; the
new row's `Commentator.invited_by` lets the boards print "with *inviter*". The
inviter gets a silent `crew_partner_answered` DM either way.

One partner per inviter per match: a waiting invite, or an accepted partner
still on commentary, blocks another. Invites are capped at ten an hour per
person. When the inviter withdraws (or staff remove them), an accepted partner
stays signed up and every waiting invite is cancelled, with the partner's DM
edited to say so (`CrewService._cancel_partner_invites`). A crew hold's invites
move onto the booking with its crew. Invite, answer and cancel are audited
(`crew.partner_*`) but publish no event: accepting already publishes
`crew.signup_created`.

**Approving checks for a clash.** `find_scheduling_conflicts` answers, in one
query across all three roles, what else the person is committed to in the
match's window (the tournament's `average_match_duration`, or 90 minutes), and
the approval confirmation names what it found. Approval used to be decided from
a dialog showing one line — the person's name — and no conflict check existed
anywhere in the crew path.

`GET /matches/{id}` returns only `approved=True` crew — `serialize_match` in
`api/_match_view.py` drops the rest, so an unapproved signup never leaks through the
ordinary match read. The pending list is `GET /matches/{id}/crew`, behind
`require_admin`. Signup, undo, approval and acknowledgment audit through
`AuditActions.CREW_SIGNUP_CREATED` / `CREW_SIGNUP_REMOVED` / `CREW_APPROVAL_CHANGED` /
`CREW_ACKNOWLEDGED`, each with the matching `crew.*` event.

**A tournament can opt out of a role.** `Tournament.required_commentators` /
`required_trackers` (Tournament edit → Entry & administration → Stream crew)
say how much approved crew a streamed match needs; `0` means the tournament does
not use that role at all. The schedule board renders no **Sign up** for it, and
`signup_crew` refuses it — the Discord button and the REST route reach past the
hidden control, so the rule lives in the service. `undo_crew_signup` is
deliberately *not* gated: a signup made before the requirement changed has to
stay removable. The same numbers drive the coverage reports
([admin-reports](admin-reports.md#what-covered-means)).

**Signup closes once the match starts.** `signup_crew` refuses a match that has
started or finished, in the service so the web UI, REST and the Discord button
all honour it. A seated-but-not-started match stays open on purpose: that is
when a stream discovers it still needs a commentator. Withdrawing stays open in
every state.

## Acknowledgment

Two flows, both confirming someone has seen and accepted a match.

**Players** get a DM with an Acknowledge button (`match_ack:ack:<match_id>`) when a
match is scheduled. The handler in `discordbot/match_acknowledgment.py` calls
`MatchService.acknowledge_match(match_id, user)`, which upserts a
`MatchAcknowledgment` row — one per player per match.

**Crew** acknowledge after approval, from **My Schedule**, the schedule board's crew
cell, or the DM button in `discordbot/crew_acknowledgment.py` — all three route to
`CrewService.acknowledge_crew_assignment`.

The admin schedule highlights matches with unacknowledged players; per-player state
comes from `MatchAcknowledgmentRepository.list_for_match()` / `list_for_matches()`.

**A player with no acknowledgment row is invisible to all of it** — no icon in the
board's players cell, no Acknowledge button (it is gated on the row existing), and
an admin dialog that reports nobody assigned. The SpeedGaming sync used to produce
exactly that, syncing players and no rows; it now calls
`MatchParticipants.reconcile_acknowledgments`, which creates the missing rows and
drops the stale ones **without rewriting the answers already given**. That is the
distinction to keep: `seed_acknowledgments` is destructive by design (an admin
rewriting the roster is restarting the question), so a caller that runs repeatedly
over the same match wants the reconciling one.

## Watching

Any logged-in user can watch a match to get DMs on its state changes — watchers are
observers, not participants, and watching is independent of acknowledgment.

`MatchWatcherService.watch(match_id, user)` inserts a `MatchWatcher` row from the
schedule tab or the match dialog. Lifecycle DMs to a watcher carry an Unwatch button
(`match_watch:unwatch:<match_id>`) handled in `discordbot/watch_buttons.py`. Watching
itself is web-only — there is no Watch button on Discord, only Unwatch.

Someone who is both a player and a watcher gets one DM per event, not two; dedup
happens in `MatchScheduleService` before the send loop.

## Stage calls

Assigning a stage tells the people it concerns rather than leaving them to spot
it on the schedule board. `MatchService.assign_stage` DMs the players, the
approved crew and the watchers; a pending crew signup gets nothing, because
nobody has given that person the job. Clearing the stage sends its own
retraction, so a stage call is never left standing after it stops being true.

A second DM lands `Tournament.stage_reminder_minutes` before the match (default
30, `0` to send none) from `application/services/match/stage_reminder.py`. Each
reader gets copy and a button for their own tie to the match: players **View
your match** (`/home/player?match=<id>`, their board narrowed to it), crew **View
your crew slot** (`/home/my-crew?match=<id>`, their commitment card outlined and
scrolled to), watchers **View the schedule**. Most of them cannot open the admin
schedule at all, and the player filter is empty for anyone not playing. Detail:
[discord.md → Stage calls](discord.md#stage-calls).

## Check-in and the station pool

Check-in is **per match**, not per player: a proctor checks a match in once both
players are in the room, which stamps the single match-level `Match.seated_at`.
The same flow seats each player at a numbered station.

### The station pool

`Station` is the venue's fixed pool of physical seats — managed by STAFF on
**Admin → Settings → Venue → Stations** (`StationService`), beneath the
`StationFormat` control it belongs with. A station has a `name` (the label), an
optional free-text `section` ("North wall") that is display-only, a `sort_order`,
and an `is_active` flag. Names are unique per tenant.

`MatchPlayers.assigned_station` stores the **label**, not an FK. Two consequences
follow, and both are deliberate:

- **The pool is advisory until it exists.** A community with zero `Station` rows
  keeps the historical free-text field validated only by the `StationFormat`
  regex. This is why the station pool has no per-tenant feature flag — it
  self-gates.
- **Deleting a station does not rewrite history.** Past matches keep their text.
  Deactivate rather than delete.

### Double-booking

`MatchService.assign_stations` runs one validation ladder, reporting the most
specific problem first:

1. the same station assigned to both players of this match;
2. the label fails the tenant's `StationFormat` regex;
3. the label is not in the pool — *only* once the pool is non-empty;
4. the station is already in use by another match in play.

**Occupancy is derived, never stored.** A station is in use when some match that
is **seated and not finished** has a player assigned to it. It frees up when that
match *finishes*, not when an admin confirms it — the seat is physically empty at
that point. The lookup (`MatchRepository.occupied_stations`) excludes the match
being edited, so re-assigning a match to the station it already occupies is
allowed, and it is tenant-scoped, so two communities that both call a station "1"
never block each other.

### Randomizing the seating

The check-in dialog's **Randomize** button draws a station for each player
instead of the proctor picking two by hand. It calls
`MatchService.suggest_stations`, which **proposes and never writes** — the
values land in the pickers, the proctor can override any of them, and the
commit is still the same `assign_stations` call with its full validation ladder.

Two rules, both soft:

1. **Opposite sides.** The two players are drawn from different `Station.side`
   values, so they end up in different halves of the room.
2. **Spread out.** Within a side, a station whose neighbours are free is
   preferred over one beside a match already in play. Neighbours are same
   `side`, same `section`, `position` one apart.

Anything the draw could not honour comes back as a sentence and is shown as a
warning toast: one side full, no sides recorded on the pool at all, or a room
busy enough that somebody had to sit next to a live match. The draw refuses
outright only when it has nothing to work with — a match that does not have
exactly two players, a community with no station pool, or fewer than two free
stations. Which specific seat a player gets is deliberately arbitrary;
`secrets.choice` picks within whichever tier survived the rules above.

The button appears only for a two-player match on a community that has defined
a pool. `Station.side` and `Station.position` are set under Admin → Settings →
Venue → Stations and are both optional — a community that leaves them null gets a
plain random draw with a note explaining why the players were not split.

## Disputed results

The agreed model is *the proctor records their best guess and the admin
overrides during confirmation*. The dispute signal that makes that workable is
**a flag, not a workflow** — there is no dispute state, no assignment, no
thread, no note, and no notification beyond the domain event.

One column on `Match` carries it: `needs_review` (bool). The match stays
`Finished` throughout; nothing about the state machine changes.

**Raising it.** The proctor ticks *Flag for admin review* in the result dialog
(`theme/dialog/match_result_dialog.py`) when they record the winner. The
checkbox exists only in the dialog's `record` mode — the admin reaching the same
dialog in `edit` mode to correct a winner *is* the review, so offering them the
flag would let them raise a dispute with themselves. The dialog calls
`MatchService.flag_for_review` **after** its `on_submit` finishes the match,
because a match that is not finished yet cannot be flagged (`ValueError`). Gate:
`can_run_match` — this is the proctor's own action, and they are the one in the
room who saw the disagreement.

There is deliberately **no note field**. A proctor raising the flag is standing
in a room mid-event; what happened is a conversation with the admin, not a
textarea to fill in between matches. The audit trail records that the dispute
happened and who raised it.

**Seeing it.** `MatchDisplayService` puts `needs_review` on every table row. The
desktop State cell shows a "Needs review" chip above the Confirm button with
what the flag means on a tooltip; the mobile card renders the same chip **and
that sentence as text**, because a tooltip is unreachable on a touch screen. The
admin Schedule tab's review-queue strip counts flagged matches separately from
merely-unconfirmed ones, since a contested result needs a decision and an
uncontested one needs a click.

The **confirm dialog repeats it** rather than relying on the row behind it
(`confirm_result_message` in `theme/tables/match_lifecycle.py` — pure, so its
copy is unit-tested). That body names the winner (`Record <name> as the winner of
match #N?`), says who beat whom, and for a flagged result says the proctor
flagged it and warns that confirming clears the flag.

**Resolving it.** *Confirming the match is the resolution* — an admin
confirming has, by definition, looked at it, so `confirm_match` clears
`needs_review` and writes a second `match.review_cleared` audit row
(`resolved_by: 'confirmation'`). `MatchService.clear_review` drops the flag
without confirming, for "looked at it, nothing to fix, not confirming yet"; it
is gated on `can_confirm_match`, so a proctor cannot unflag their own dispute.

Both directions are also reachable over REST as `POST /matches/{id}/review`
(`{needs_review}`), with the same split gates. Both emit domain events
(`match.flagged_for_review`, `match.review_cleared`): a contested result is
exactly what an alerting webhook subscriber wants to hear about.

The dispute flag has **no per-tenant feature flag** — like the station pool it
self-gates, since a community that never ticks the box never sees it.

## Stream volunteering

Players can offer their own match for stream. It is a signal and nothing more:
staff read it while they build the stream schedule, and `is_stream_candidate`
— written by whoever `can_assign_match_stream` admits — remains the decision.
Every surface that shows the control says so, because a toggle sitting beside a
Stage column reads as booking one otherwise.

`MatchStreamVolunteer` holds one row per player, not a flag on the match, so a
board can say whether one player asked or both did. `MatchStreamVolunteerService`
enforces the two rules that keep it advisory:

- **Only the match's own players may offer it.** A non-player gets
  `ValueError('Only the players in a match can offer it for stream.')`.
- **Offering never writes `is_stream_candidate`.** A player's request must not
  be able to write the decision it is asking about.

Offering is refused once the match has started — there is nothing left for staff
to schedule — but **withdrawing is not**, so a player who changes their mind is
never stuck with a request they cannot unsay. Both directions audit
(`match.stream_volunteered` / `match.stream_volunteer_withdrawn`) and publish the
matching domain events.

**Where it appears.** The player's own board (Home → My Schedule) carries a
`Stream` column beside `Watch`: a `videocam` toggle for a player in that match,
and a count chip for anyone else. The mobile card carries a labelled *Offer for
stream* button in its actions row and names the volunteers under the Stage line.
The player match dialog has a switch with the full sentence under it. On the
staff side, `MatchDisplayService` puts `stream_volunteers` (names) on every row,
and the admin match dialog prints them directly under the *Stream candidate*
checkbox — the offer read where the decision is made.

No feature flag: a community whose players never offer a match never sees it.

## Harder settings opt-in

A tournament can name a second, harder preset beside its standard one
(`Tournament.hard_preset`, set in the tournament dialog under *Seeds &
randomizer*). Each player in a match can privately opt into it. The match rolls
the harder preset only when **every** player has opted in, and until then no
player can learn whether anyone else did.

That secrecy is the feature, not an implementation detail of it. If one player
could see the other's pending opt-in, opting in would become a challenge to
answer and declining would become a refusal to meet it — and the reason to offer
harder settings at all is that two people both want them, not that one dared the
other. So a lone opt-in notifies nobody, publishes no event, and appears on no
surface but its owner's. It simply lapses when the seed rolls.

**What that promises, precisely.** A player who has not opted in learns nothing
— that is the property the feature rests on. A player who *has* opted in learns
the outcome, because agreement is announced and its absence is informative; they
can conclude their opponent is not in. That is unavoidable once someone commits,
since they have to find out what they are playing. It also means a player
willing to opt in and withdraw straight away can probe the other's answer
invisibly. Closing that would mean adding friction to withdrawal, which the
window deliberately does not have. So the guarantee is asymmetric by design:
absolute for the player who has not committed, outcome-only for the one who has.

Four things follow, and each is load-bearing.

- **One read, and it answers about the caller.** `my_state` (and its bulk
  sibling `board_states`) is the only read any page, router or bot may call.
  `everyone_in` is computed only for a caller who opted in themselves, so it
  cannot become a way to ask about the opponent. A player whose opponent opted
  in and a player whose opponent did not see byte-identical state.
- **Audited, not published.** The individual opt-in writes
  `match.hard_preset_opted_in` / `_withdrawn` to the audit log and stops there;
  the eventless-ledger entry in `tests/services/test_event_audit_parity.py`
  records why. A webhook subscriber is an arbitrary outside listener. Only
  `match.hard_preset_agreed` / `_agreement_revoked` — the states the players
  themselves already know — reach the event bus.
- **Agreement is derived, never stored.** It is the opt-in set equalling the
  player set, recomputed on read, so a roster change re-answers the question
  instead of leaving a stale yes behind. A match with no players is never
  unanimous, or an empty roster would satisfy it vacuously.
- **The window shuts at the seed roll.** Both opting in and withdrawing are
  refused once `Match.generated_seed` exists, because from then on
  `GeneratedSeeds.preset` is the record of what was actually played. In practice
  that means the choice closes when the race room opens, since the auto-opener
  rolls in the same tick.

**Which preset a match actually rolls** is decided in one place,
`MatchHardPresetService.resolve_preset`, called by `generate_seed`:

1. `Match.preset_override == HARD` → the tournament's hard preset;
2. `Match.preset_override == STANDARD` → the tournament's standard preset;
3. every player opted in → the hard preset;
4. otherwise → the standard preset.

**Staff can overrule it per match**, in either direction, from the admin match
dialog's *Seed settings* select. That is deliberately visible — it is staff's
decision about the match and the players are DMed — and it still reveals
nothing, because forcing the harder preset says nothing about who had opted in.
Existing opt-ins survive an override, so clearing it restores what the players
had chosen rather than discarding agreements staff never asked to destroy. The
override is refused after the roll, like the players' own choice, and an
overridden match is never sent the offer DM — its button would be refused, and a
message promising a choice the reader cannot make is worse than no message.

The admin dialog applies the override **last**, after the rest of the edit has
saved. Setting it audits, publishes and DMs both players; doing that first meant
a later validation failure left the players told about settings on a save the
admin was told had failed.

**A match edit re-answers the agreement**, through one pair of calls. Every path
that rewrites a roster or reassigns a match — `MatchService.update_match` and the
SpeedGaming ETL's roster replace — takes a `MatchHardPresetService.snapshot`
*before* the write and calls `reconcile_edit` after it. The snapshot is not an
optimisation: afterwards a swapped-in player has no opt-in row, so every broken
agreement would look like one that never existed, and a reassigned match no
longer knows which tournament's preset its players had agreed to.

Two cases, and they are not the same.

- **The roster changed.** A departing player's row stops counting, which can
  cross the unanimity line either way: a swap breaks an agreement the remaining
  players were told about, and dropping the one holdout completes one nobody had
  announced. The resulting DM names no one, because nobody in that conversation
  backed out. It goes only to players who already held a row — telling a
  newly added player that an agreement lapsed would hand them both facts the
  feature withholds.
- **The match moved** to another tournament, or its tournament's hard preset
  changed underneath it. Every opt-in is discarded. Consent is to a named
  preset; it is not consent to whichever settings the match lands on next. If an
  agreement existed, its players are told in the *old* tournament's words —
  naming the preset they lost, not the one they never chose.

The ETL matters here more than it looks: those are the rosters that change with
nobody in the app touching them, so a stale row would come back opted in to a
roster its owner never saw.

**Where it appears.**

| Surface | What it shows |
|---|---|
| Player board (Home → My Schedule) | A `Settings` column: a `bolt` toggle for a player in the match, a `gavel` chip when staff overrode it, and the preset name once the match has rolled it. Renders nothing at all when the tournament offers no hard preset. |
| Mobile card | A labelled button in the actions row — *Harder settings*, *Opted in — waiting*, or *Playing &lt;preset&gt;*. |
| `HardPresetDialog` | The explanation and the two buttons. A dialog rather than a bare toggle: a switch whose effect depends on an answer you are not allowed to see needs a sentence before it is flipped. Opened by the board cell, by `?hard=<match_id>`, and by the DM's link button. |
| Discord | An offer DM when the match is scheduled, carrying **one** button chosen from the reader's own answer (*Play the harder preset* or *Back out*) — the only DM in the app whose button differs per recipient. Replies are ephemeral. Agreement, breakage and staff overrides each get their own DM to the whole match. The link button follows what the reader can still do: *Choose your settings* while the choice is theirs, *View your match* once staff have forced it. |
| Admin match dialog | The *Seed settings* override select. It shows what will be rolled, never who asked for it. |

No feature flag. The feature is dormant until a tournament has a hard preset,
which is what keeps it invisible to every community that has not configured one.

## Reschedule requests

Players never move or cancel their own matches. They **ask**, and someone who
could already have made the change decides — which is the whole point, because
the alternative players had was finding a staff member.

Two rules hold it together.

**Approving performs the change.** `approve` calls the existing
`MatchService.update_match` / `cancel_match` rather than writing `scheduled_at`
itself, so there is one implementation of "move a match" and the reschedule DM
fan-out, acknowledgment reseeding, racetime-room handling and
`match.rescheduled` event all come with it. A request that were merely *blessed*
would leave staff to go and do the work anyway.

**No new authority.** Deciding gates on `can_crud_match`, the same check
guarding `update_match`. Nobody can answer a request who could not already have
made the change by hand — the request is a route to staff, not a new power.

```python
from application.services import MatchRescheduleService

await MatchRescheduleService().submit(match_id, actor, reason='work clash', proposed_at=when)
await MatchRescheduleService().record_opponent_agreement(request_id, opponent)
await MatchRescheduleService().approve(request_id, staff, scheduled_at=None, note=None)
await MatchRescheduleService().decline(request_id, staff, 'the restream slot is locked in')
await MatchRescheduleService().withdraw(request_id, requester)
```

**Refusals happen at submission**, not in the queue. A match already under way
(talk to a proctor), a match with no time, a tournament with
`Tournament.allow_reschedule_requests` off, and a **SpeedGaming-sourced match**
— whose time belongs to the next sync, so nobody here could ever approve it —
are all turned away when asked. A request that can never be actioned is worse
than no button.

The same rules hide the control (`list_requestable_match_ids`, two bulk queries
for a whole board), and the service re-checks every one of them because the REST
route and a forwarded link both reach past whatever the UI rendered.

### The opponent's agreement

The match's other player gets a DM with an **Agree** button, and the same ask on
the web: My Schedule shows **Your opponent asked to reschedule** above the board
with an Agree button per request (and "You agreed. Waiting on staff." once they
have), and the DM's link button opens `/home/player?agree=<id>` with the request
in a dialog. Both call `record_opponent_agreement`. It stamps
`opponent_agreed_at`, shows on the decision dialog, and **gates nothing**: a
request with no agreement is still decidable and one with agreement can still be
declined. It exists so staff are not the ones chasing the other player.

One column, so it only means "the other player". A match with more than two
players skips the signal entirely rather than recording one person's yes as if
it were everyone's: no DM, no card, and `record_opponent_agreement` refuses it
on every route (Discord, web, REST).

### Each side's surface

| Who | Where | What it carries |
|---|---|---|
| The player | Home → My Schedule, the **Change** column | The ask, and below the board **Your change requests**: what they asked for, whether the opponent agreed, and staff's reply — with **Withdraw** while it is pending |
| The opponent | Home → My Schedule, **Your opponent asked to reschedule** above the board | The request (time now, proposed time, reason) with **Agree**; `MatchRescheduleService.list_awaiting_agreement`, two-player matches only |
| Staff | Admin → Match Schedule, **Needs you** | Each request named, with *Approve* for a move at the proposed time and *Decline* / *Review* opening the decision dialog |

The strip is **live**. Submitting, withdrawing, agreeing and declining all
publish on `match_live`, and the strip subscribes through
`theme/realtime.register_view`, so a staff member already sitting on the board
watches the count grow rather than discovering the request on their next page
load. It subscribes for itself rather than riding the table's
`on_rows_changed` the way the review and crew strips do: those two summarise the
*visible rows*, while this one summarises requests, and a request can land on a
match the board's day or state filter is hiding.

The decision dialog carries everything needed to decide — both times, the
reason, whether the opponent agreed, and either player having blocked the
proposed window out — because the alternative is closing it to
go and look. Staff can approve at the proposed time or counter with a different
one; the requester is told which they got either way.

### Who can read one

A request carries the player's own words about why they need the change, so the
reads are gated the way the feedback queue's are: your own are yours
(`list_mine`), a match's are readable by whoever could decide them or by a
player in that match, and the community-wide queue needs `can_view_admin`. It is
narrowed by tournament as well, so a tournament admin is never shown a request
they would then be refused for deciding.

### What is recorded, and what is told

Statuses distinguish three ways to stop being pending without being refused:
`WITHDRAWN` (the requester took it back), `SUPERSEDED` (another request settled
the match, so staff decided nothing), and `DECLINED` (someone looked and said
no). Only a decline needs a note, and it is **required** — a refusal with no
reason is what this replaces.

Only a decline and an approved **cancellation** DM the requester. An approved
reschedule already reached both players through `update_match`'s own
notification, and a second "your request was approved" would be the same news
twice. The decline DM's button opens the request form again for that match,
because asking with a different time is the real next step after a refusal.

Approving a cancellation **deletes the match**, and the request cascades with
it; the `match.reschedule_approved` audit row is the durable record, which is
why it is written for both kinds and why it carries the player's `reason` and
staff's `note` rather than only the ids. It also lists
`superseded_request_ids`, so the trail answers "what did this approval close?"
without a per-row audit entry for decisions nobody made.

Like the station pool and the dispute flag, this has **no per-tenant feature
flag** — it self-gates through `Tournament.allow_reschedule_requests`, which a
community sets per tournament.

## Match comments

Anyone on the match can leave a signed, markdown comment in the match sheet,
and who reads it depends on its kind (`MatchCommentKind`):

| Kind | Written by | Read by |
|---|---|---|
| `player` | one of the match's players | staff, and that player. **Never the opponent**: "I'll be late" is between the player and the people running the event |
| `staff_private` | staff ("Private note") | staff only |
| `staff_to_players` | staff ("Message players") | staff and every player in the match |

"Staff" is `AuthService.can_run_match`: STAFF, proctors and the tournament's
admins, so the thread works on Admin → Match Schedule and on Proctor Station. The two
staff kinds have separate buttons and separate composers so a private note can't
be sent to players by a mis-set toggle. Authors delete their own; staff delete
any. Bodies are capped at 2,000 characters and render through the help markdown
model, never `ui.markdown`.

Bodies never ride the shared board rows: the sheet fetches them per viewer via
`MatchCommentService.list_for_viewer`. Staff boards (`show_comment_counts=True`
on `MatchTableView`, honoured only for viewers with `access.run`) show a person
icon with the player-comment count and a shield with the staff count on each
run-sheet line; the staff count includes private notes, which is why players
never get counts.

A `staff_to_players` message DMs each player whose `User.dm_match_comments` is on
(default on; Profile → Notifications → Customize → "Staff comment on a match", which keeps the flag in step). The DM's button opens
`/home/player?comments=<match_id>`, which narrows My Schedule to that match and
opens its sheet. Every add and delete audits and emits `match.comment_added` /
`match.comment_deleted`, with `kind` in the payload so a webhook can drop private
notes. No feature flag.

A `player` comment DMs the match's alert recipients (below), whose button opens
`/admin/match-schedule?comments=<match_id>`: the board narrowed to that match
with its sheet open. The comment text is escaped (`player_comment_staff_dm`) so
a player's masked link or mention can't render in Discord.

## Running late

`PlayerLateService` (`application/services/match/player_late_service.py`) lets a
match's own player tell staff they're on their way. `report_late(match_id, user,
minutes, note)` takes 10, 20 or 30 (`LATE_CHOICES`, shared with volunteers) and an
optional note (280 chars), and writes `late_minutes` / `late_at` / `late_note` on
their `MatchPlayers` row; `take_back_late` is the player's Undo. Both audit and emit
`match.player_running_late` / `match.player_late_cleared` (no note in the event)
and nudge `match_live`. A report DMs the match's alert recipients (below) under
the `player_late` notice, with the name and note escaped. On a stage match it
also DMs the Broadcast Techs (`StaffAlertService.tagged_broadcast_tech_recipients`)
under `player_late_on_stage`: techs holding any published volunteer shift right
now (`ON_DUTY`), or every tech when none is (`STAFF`). A tech already told as
staff gets only the `player_late` copy, and Undo DMs nobody.

The window (`late_window_open`) opens 30 minutes before the match, or as early
as the tournament's `stage_reminder_minutes` for a stage match, and closes at
check-in; racetime matches never open it. Before the window the error points at
Ask to change.

Display rows carry `late` (only until check-in) and `late_window`. Staff boards
(`show_comment_counts` + `access.run`) keep every entry, drawn as
`run-sheet__late` tags and as Needs you → Running late items, which list a late
match before its start time too. Every other board runs `keep_own_late_only`,
so the opponent's browser never receives the heads-up; the viewer's own entry
becomes the player run sheet's `Staff know: ~20 min` status chip with Undo, and
`_can_late` draws the **Tell staff I'm late** button that opens `RunningLateDialog`.
The stage reminder DM gives players a **Tell staff I'm late** button
(`discordbot/player_late.py`, `match_late:` / `match_late_min:` custom ids).

## Ready for seed

A tournament with `players_release_seed` on (Admin → Tournaments → Edit →
Seeds & randomizer, off by default; also on REST and the MCP tournament tools)
lets the players of a checked-in match release their own seed.
`SeedReadyService` (`application/services/match/seed_ready_service.py`) owns it:
`set_ready(match_id, user)` stamps `MatchPlayers.seed_ready_at`, and the tap that
makes every player ready calls `MatchScheduleService.release_seed_to_ready_players`,
which rolls through the same locked path as `generate_seed` but swaps
`can_run_match` for `players_may_release` (the actor plays the match and every
player is ready). Rolling is what sends the seed, so the usual `seed_ready` DM
goes out. `set_not_ready` clears the stamp until the seed exists or a task-queue
roll is in flight. Both audit and emit `match.player_seed_ready` /
`match.player_seed_not_ready`. Tapping Ready again while everyone is ready retries
a roll that failed. Proctors and staff roll whenever they like, as before.

The window (`seed_ready_open` in `seed_ready.py`) is check-in until a seed exists,
never on a racetime tournament. Clearing the seed or the check-in
(`MatchService.update_match`) wipes every player's Ready
(`MatchRepository.clear_seed_ready`).

The check-in DM's player copies (`_checked_in_player_copies`) end by asking for
Ready and carry a **Ready for seed** button (`discordbot/seed_ready.py`,
`seed_ready:in:` / `seed_ready:out:` custom ids, the button flipping to **Not
ready** after a press). A player's first Ready DMs each player who isn't ready
under `opponent_seed_ready` (silent by default), once per player per match
(`seed_nudged_at`), so toggling can't spam the opponent.

Display rows carry `players[].seed_ready`, `seed_ready_open` and
`seed_ready_label` ("1 of 2 ready"). The staff run sheets put the label on the
seed line ("Seed not rolled · 1 of 2 ready"); the player run sheet
(`mark_my_seed_ready`) draws the viewer's own Ready panel: every player ticked or
"not yet", the big **I'm ready for my seed** button, then **Not ready** and who
they're waiting on.

## Match alerts: the admin on duty

A *match alert* is a staff DM about one match that someone has to pick up: a
player's comment, a reschedule request. `StaffAlertService.match_alert_recipients`
sends it to whoever holds a published shift **right now** on a volunteer position
with `VolunteerPosition.staff_alerts` on (Admin → Volunteers → Edit Position →
"Staff alerts while on shift"), plus the tournament's admins minus those who
switched that tournament off (`TournamentNotificationPreference.staff_alerts`).
Nobody on duty who can act means the alert falls back to every STAFF member, so
a community that never set the switch behaves as before. Every recipient must
pass the caller's permission check (`can_run_match` for comments,
`can_crud_match` for reschedule requests), so a volunteer on the desk without
the role is skipped rather than sent to a control that refuses them.
Community-wide requests (join, ADA, community events) still go to all staff.

The volunteer alerts (running late, gave a shift back) reuse the same on-duty
set through `StaffAlertService.volunteer_alert_recipients`: every volunteer
coordinator plus whoever is on duty and `can_manage_volunteers`, with all STAFF
standing in when nobody is.

## Models

| Model | Holds |
|---|---|
| `Commentator`, `Tracker` | crew signups; `approved` bool, `approved_by` (SET_NULL), `acknowledged_at` timestamp |
| `MatchAcknowledgment` | per-player acknowledgment state per match |
| `MatchWatcher` | user × match watch subscriptions |
| `Station` | the venue's pool of physical seats (per tenant; label-referenced) |
| `MatchComment` | a signed comment on a match; its `kind` decides who reads it |
| `MatchStreamVolunteer` | one player's offer to have their match streamed; advisory, never the decision |
| `MatchRescheduleRequest` | a player's ask to move or cancel a match; kind, proposed time, reason, status, and staff's decision |
| `MatchHardPresetOptIn` | one player's private agreement to the tournament's harder preset; unanimity is derived from the set, never stored |

Field-level detail: [reference/data-model.md](../reference/data-model.md).

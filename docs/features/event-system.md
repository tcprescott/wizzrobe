# Event System

An in-process publish/subscribe bus. Services publish a domain **event** after
they commit a change; any number of subscribers react. The publishing rules —
publish after commit, prefer `write_and_publish`, `EventType` names are an
external contract — are in CLAUDE.md; this doc is the machinery.

Source: [`application/events/`](../../application/events/). The package sits at the
`application/` root as a peer of `services/`, so both publishers (services) and
subscribers (presentation) can import it with no cycle.

## Pieces

| Module | Role |
|---|---|
| `event.py` | The immutable `Event` value object: `event_type`, `payload`, snapshotted `actor_id`/`actor_username`/`tenant_id`, `occurred_at` (UTC). `Event.create(type, payload, actor)` builds one; `to_wire()` is the JSON shape delivered to webhooks. |
| `event_types.py` | `EventType` — the `object.verb` name registry (mirrors `AuditActions`). `EventType.ALL` drives the webhook UI multiselect + validation; `'*'` is the wildcard. |
| `dispatch_queue.py` | A background worker (its own instance of the shared `CoroutineQueue` from `application/utils/coroutine_queue.py`, separate from `discord_queue`) that runs async subscribers off the request path. |
| `bus.py` | The core: `subscribe_sync`, `subscribe_async`, `unsubscribe`, `publish`. |
| `match_live.py` | The narrow predecessor: `(match_id, change_type)` nudges to open UI views. Deliberately **not** re-exported from `__init__.py`, so reaching for it takes an explicit `from application.events import match_live` and can never be mistaken for the bus. Its sibling `check_in_live.py` has the same shape for the check-in desk, `(event_id, entrant_id, change_type)` with `entrant_id=None` on the one `ROSTER` signal a sync sends; desks pass it as `register_view(..., channel=check_in_live)`. Views subscribe through `theme/realtime.register_view`, whose runner re-enters the view's client *and* rebinds NiceGUI's request contextvar to that client's request: the task inherits the publisher's contextvars, so without the rebind `app.storage.user` named the publisher (a staff member approving a move) as every board's viewer. A client whose session has no user storage is skipped. A publisher that changes state in two writes must publish after the last one; `MatchRescheduleService.approve` publishes again once the decision is recorded, and `MatchTableView` serialises single-row updates so an earlier, staler re-fetch cannot land last. |

## Two kinds of subscriber

- **Sync** (`subscribe_sync`) run **inline** during `publish` and must be fast and
  non-blocking — schedule work, never await. This is the fast path for UI
  live-refresh; [`theme/brackets/live.py`](../../theme/brackets/live.py) is the
  live example, repainting a bracket on `BRACKET_*` and `MATCH_*` events.
  [`pages/static_brackets.py`](../../pages/static_brackets.py) is the other one:
  it drops its cached static bracket pages on any `bracket.*` / `match.*` event.
- **Async** (`subscribe_async`) do I/O (webhook POSTs, the telemetry mirror).
  Their coroutine is scheduled onto the dispatch worker, wrapped in
  `tenant_scope(event.tenant_id)` so a subscriber can use scoped services.

`publish` filters each subscriber by its `event_types` (`None` = all) and swallows
every subscriber error, so one bad listener cannot break the others or the caller.

A cancelled match emits **both** `match.cancelled` and `match.deleted`:
cancellation deletes the row, so a subscriber listening for the deletion keeps
working, while `match.cancelled` carries the `reason` and marks the difference
between "called off" and "shouldn't have existed".

## Who publishes what

| Family | Published by |
|---|---|
| `match.*` | `match/match_service.py`, `match_schedule_service.py`, `match_cancellation.py`, `match_request.py`, `match_review.py`, `match_acknowledgment.py`, `match_stations.py`, `match_stream_volunteer_service.py`, `match_comment_service.py` (`match.comment_added` / `_deleted`, with `kind`; `body` only on a staff message to players, never on a player comment or private note), `match_hard_preset_service.py`; `match.reschedule_*` from `match_reschedule_service.py`, `match.seed_roll_failed` from `seed_roll_service.py`, and a racetime room's `match.result_recorded` from `race_room_service.py`. The hard-preset service is the one that deliberately publishes **less** than it audits: `match.hard_preset_agreed` / `_agreement_revoked` and the staff `match.preset_override_*` go out, but an individual player's opt-in does not. The feature rests on a lone opt-in reaching nobody, and a webhook subscriber is an arbitrary outside listener — so the opt-in stays audit-only, recorded in the eventless ledger in `tests/services/test_event_audit_parity.py` |
| `crew.*` | `crew_service.py` |
| `volunteer.*` | `volunteer/volunteer_schedule_service.py` |
| `bracket.*` | `bracket_service.py` (create, cancel, roster add/update/drop, entry retire) and the `_bracket/` mixins (generation, advancement, completion, multistage, scheduling, series) |
| `race_room.*` | `race_room_service.py` |
| `sg_sync.*` | `speedgaming_etl_service.py` |
| `discord_event.*` | `discord/discord_event_reconciler_service.py` |
| `async_qualifier.*` | `async_qualifier/`. Two groups. **Standings:** run submitted/reviewed/expired/forfeited/reattempted and live race recorded — every outcome that moves an entrant's total. (`run_started` is not one: a draw promises nothing. A reviewer's void publishes `run_reattempted` with `granted: True`, since to a subscriber it is the same fact as the runner spending their own allowance.) **The window:** `async_qualifier.opened` / `.closed`, published by `sync_window_state` — the worker observing the clock cross `opens_at`/`closes_at`, or the service when an admin's edit crosses it immediately. Those two have **no `AuditActions` mirror**, the `service_health.alert` shape: nobody performs an opening, so there is no actor to record, and `AsyncQualifier.window_state_notified` is what makes each crossing announce once. Qualifier/pool/permalink authoring stays audit-only — a permalink *is* the seed, revealed to a runner only when their slot is spent, so publishing one would hand out the pool |
| `tournament.enrolled`, `tournament.withdrawn` | `_tournament_signup.py`'s `record_enrolment_change`, the one funnel every enrolment change goes through: the player's own signup, the tournament's roster dialog, the per-user admin dialog, and the auto-enrol that fires when staff schedule someone into a tournament they were not in. Split by direction because a roster mirror needs to know which way it moved, while the audit trail keeps one action for all four. One event per tournament rather than one per screen-level edit, so a staff roster change reaches a subscriber at the same granularity as a self-signup. Payload carries `tournament_id` and `user_id` as routing keys |
| `tournament.round_schedule_updated` | `round_schedule_service.py` — staff saved a Challonge/start.gg tournament's per-stage booking windows; payload carries `tournament_id` and the `stages` keys that have a window |
| `tournament.prize_pool_updated`, `tournament.payout_updated` | `payout_service.py` — a changed pool or split moves what every placement is owed |
| `check_in_event.synced`, `check_in_entrant.checked_in` / `.check_in_undone` / `.linked` / `.unlinked` / `.walk_up_added` / `.removed` | `check_in_service.py` — someone arriving is what a subscriber wants to hear (a welcome post, a live attendance count), and the roster mutations around it keep a mirror honest. `synced` fires on every sync, including the worker's polls, which write no audit row; event create/edit/delete stays audit-only as tenant-internal config. See [event-check-in.md](event-check-in.md#audit-and-events) |
| `oauth_grant.created`, `oauth_grant.revoked` | `oauth_grant_service.py` — a member connecting a third-party app to this community, and that connection ending (the app revoking it, a replayed code, or refresh-token reuse). The payload names the app and the scope, never a token. App registration stays audit-only: it is global, not a community's |
| `service_health.alert` | `service_health_service.py` — platform-level (no tenant), so tenant-scoped webhooks never receive it |
| `tenant.member_*`, `tenant.join_*` | `tenant_membership_service.py` — who belongs to a community and who is asking to, which is what an external roster subscriber mirrors (and what routes "someone wants in" to a staff channel). The rest of `tenant.*` stays audit-only: it is platform-level super-admin work a tenant's own subscriber cannot see |

## Registering a subscriber at startup

Subscribers are registered in the FastAPI lifespan in [`main.py`](../../main.py),
alongside starting the dispatch worker:

```python
event_dispatch_queue.start()
event_bus.subscribe_async(WebhookService().deliver_event)
event_bus.subscribe_async(TelemetryService().record_event)  # no filter: mirror all
```

Current async subscribers: **[webhooks](webhooks.md)** (fans each event out to
staff-configured endpoints) and **[engagement telemetry](telemetry.md)** (mirrors
every event into `TelemetryEvent`). A new non-UI subscriber follows the same
shape.

## Adding a new event

1. Add a constant to `EventType` **and** to `EventType.ALL`.
2. Publish it at the commit point — normally via `write_and_publish`.
3. It is immediately selectable in the webhook admin multiselect.

## The remaining hand-rolled pairs

`check_dry_regressions.py` blocks a *new* `write_log` + `event_bus.publish`
sequence anywhere inside one function body. Three sites still hand-roll the
pair, because collapsing them into one call would change behaviour rather than
just shape:

| Site | Why it stays |
|---|---|
| `CrewService.update_crew_approval` | audits **inside** `async with in_transaction()`, publishes outside it. `write_and_publish` would move the publish inside the transaction, where a subscriber could read pre-commit state. |
| `CrewService.acknowledge_crew_assignment` | same transaction boundary. |
| `VolunteerScheduleService.assign` | audits every assignment, publishes only when `not auto_generated` — an unpublished draft is deliberately silent. One call cannot express the conditional half. |

`MatchService.create_match` and `update_match` were the last two convertible
ones. Both now use `write_and_publish` with `event_details`, so the event fires
before the acknowledgment reseed and notification fan-out are enqueued. A time
change on `update_match` audits `match.rescheduled`, matching the event it
publishes; any other edit audits and publishes `match.updated`.

If a future change moves the audit write out of the transaction, or makes the
draft assignment publish too, convert the site then.

### Converting a site without breaking its tests

Service unit tests hand-build their subject and hang a double off it. A blanket
`MagicMock()` swallows `write_and_publish` whole, so a converted service silently
stops publishing *under test only* — which is what kept this backlog open. Use
`tests.factories.make_audit_double()` instead: it mocks `write_log` (so existing
call-args assertions and DB-free tests keep working) while binding the real
`write_and_publish` body to the double, so the event still reaches the bus.

## Tests

[`tests/services/test_event_bus.py`](../../tests/services/test_event_bus.py):
inline sync delivery, event-type filtering, async enqueue onto the dispatch
worker, and subscriber-error isolation.

[`tests/services/test_event_audit_parity.py`](../../tests/services/test_event_audit_parity.py)
is a **drift guard**: because `EventType` names mirror `AuditActions` verbatim,
every audited action must either be emitted (in `EventType.ALL`) or listed in an
explicit eventless ledger (`_EVENT_CANDIDATES` / `_EXCLUDED_BY_DESIGN`). Adding a
new `AuditAction` without triaging it fails the test, so audit/event drift cannot
recur silently.

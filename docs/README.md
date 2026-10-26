# Wizzrobe Documentation

Wizzrobe runs tournament events — on-site restream events and online tournaments —
with match scheduling, crew and volunteer coordination, brackets, randomizer seed
generation, and Discord integration. FastAPI + NiceGUI on PostgreSQL, logically
multitenant, one deployment serving many communities.

Quick start (install, run, Docker) is in the [root README](../README.md).
Coding conventions and the rules to follow while writing code are in
[CLAUDE.md](../CLAUDE.md) — that file is canonical for anything behavioural.

## Start here

| Doc | What it covers |
|---|---|
| [product-context.md](product-context.md) | Who uses it, at what scale, what failure costs — the situation the code serves |
| [architecture.md](architecture.md) | Tech stack, process model, startup sequence, three-layer pattern, component diagram, directory map, key design decisions |
| [current-state.md](current-state.md) | Status snapshot: what works, known issues, what is deliberately deferred |
| [development.md](development.md) | Local setup, the `MOCK_DISCORD` loop, dev fixtures, migrations, tests, CI |
| [deployment.md](deployment.md) | Docker topology, the authoritative environment-variable table, GHCR, operations, backups |
| [refactoring-guide.md](refactoring-guide.md) | The three-layer pattern with worked examples from current code |
| [style-guide.md](style-guide.md) | UI style and accessibility rules: the Studio direction, tokens, which component to reach for, dark-mode traps, and the checks that enforce them |
| [timezone-handling.md](timezone-handling.md) | UTC storage, per-viewer local display, the tenant pin, and the utilities that convert |
| [scaling-roadmap.md](scaling-roadmap.md) | Measured capacity, why `--workers N` is unreachable, remaining phases |

## Code reference (`reference/`)

Layer-by-layer detail, kept in step with the source.

| Doc | Source area |
|---|---|
| [reference/data-model.md](reference/data-model.md) | `models/` (models, enums, ERD, match lifecycle), `application/repositories/`, `migrations/` |
| [reference/services.md](reference/services.md) | `application/services/`, `application/utils/` |
| [reference/rest-api.md](reference/rest-api.md) | `api/` — routers, schemas, token auth, rate limiting |
| [reference/authentication.md](reference/authentication.md) | Discord OAuth, `AuthMiddleware`, `protected_page` / `public_page`, `AuthService`, the role matrix |
| [reference/discord-integration.md](reference/discord-integration.md) | `discordbot/`, `application/services/discord/`, the DM queue, `racetimebot/` |
| [reference/seed-generation.md](reference/seed-generation.md) | Randomizer backends, `presets/`, per-tenant credentials |
| [reference/matcherino-api.md](reference/matcherino-api.md) | Matcherino's private API: auth, venue and badge endpoints, wire shapes, what's known from the bundle |
| [reference/dev-seed.md](reference/dev-seed.md) | `scripts/seed_*.py` — the fixture standard and what enforces it |
| [reference/frontend.md](reference/frontend.md) | `frontend.py`, `pages/`, `theme/`, `static/` |

## Features (`features/`)

How each shipped subsystem behaves.

| Doc | Feature |
|---|---|
| [features/brackets.md](features/brackets.md) | Native brackets: four formats, multi-stage chains, best-of-N series, the scheduling seam. Behind `FeatureFlag.BRACKETS` |
| [features/data-exports.md](features/data-exports.md) | The export queue: tournament participants + matches ZIP/JSON (versioned schema), volunteer data, logged table CSVs, 3-day retention, throttling |
| [features/match-participation.md](features/match-participation.md) | Crew signup and approval, player and crew acknowledgment, match watching |
| [features/discord.md](features/discord.md) | Match DMs and fan-out, guild-role → app-role sync, notification preferences, `MOCK_DISCORD` |
| [features/online-tournaments.md](features/online-tournaments.md) | SahasrahBot succession, user-definable tournament config, racetime/SpeedGaming/Discord-Events design rules |
| [features/multitenancy.md](features/multitenancy.md) | `/t/<slug>` and custom-domain addressing, tenant context, query scoping, per-tenant roles, `/platform` |
| [features/events.md](features/events.md) | Events inside a community: the `Event` model, event context, default event, which rows carry `event_id`, the events migration |
| [features/feature-flags.md](features/feature-flags.md) | Two-tier per-tenant flags: super-admin availability + tenant enable, and how to gate a subsystem |
| [features/mcp-server.md](features/mcp-server.md) | Remote MCP server at `/mcp`: typed tools, reads by default and writes on request, OAuth 2.1 only |
| [features/webhooks.md](features/webhooks.md) | Staff-managed outbound webhooks: signing, retries, delivery log |
| [features/event-system.md](features/event-system.md) | In-process event bus: publish/subscribe, `EventType` registry, `match_live` |
| [features/audit-logging.md](features/audit-logging.md) | `AuditService`, `AuditActions` naming, `write_and_publish` |
| [features/telemetry.md](features/telemetry.md) | Page views, curated interactions, domain-event mirror, Staff-only report |
| [features/observability.md](features/observability.md) | Sentry wiring (events, logs, traces, tenant/worker tags), which failures log at ERROR, runtime log levels on `/platform` |
| [features/web-push.md](features/web-push.md) | Declarative Web Push device notifications mirroring Discord DMs |
| [features/notification-channels.md](features/notification-channels.md) | Email (SendGrid) and Telegram copies of Discord DMs, confirmation, tests |
| [features/notification-settings.md](features/notification-settings.md) | Admin → Notifications: the catalogue of every DM, per-community off/silent/ping per recipient, on-shift staff alerts, `send_dm(notice=...)` |
| [features/admin-reports.md](features/admin-reports.md) | Crew hours, match export, audit viewer, trended insights |
| [features/ada-accommodations.md](features/ada-accommodations.md) | Per-community ADA accommodation requests from the profile, a STAFF queue with status and private notes. Behind `FeatureFlag.ADA_ACCOMMODATIONS` |
| [features/community-events.md](features/community-events.md) | Members-only `/community-events`: member-submitted, staff-approved events with Discord reminders for subscribers, and the reusable Tiptap markdown editor. Behind `FeatureFlag.COMMUNITY_EVENTS` |
| [features/startgg.md](features/startgg.md) | start.gg bracket mirror: linking, entrant mapping, booking ready sets, opt-in result writeback, the shared `bracket_sources` contract. Behind `FeatureFlag.STARTGG` |
| [features/event-check-in.md](features/event-check-in.md) | Matcherino-backed check-in desk for in-person events: badge-sale sync over the unofficial API, badge tiers, account matching, walk-ups, the `CHECK_IN_DESK` role. Behind `FeatureFlag.EVENT_CHECK_IN` |
| [features/payouts.md](features/payouts.md) | Prize pool, placement splits with ties, computed amounts, Matcherino handles and the payout block. Behind `FeatureFlag.PAYOUTS` |
| [features/profile-reminders.md](features/profile-reminders.md) | Staff-picked recommended profile items (Challonge linked, pronouns, ...) and the red banner that asks members to finish them |
| [features/triforce-texts.md](features/triforce-texts.md) | Player submission and admin moderation |
| [features/help.md](features/help.md) | Public `/help` articles + tappable help icons; the safe (non-`ui.markdown`) document model |
| [features/changelog.md](features/changelog.md) | Public `/changelog` ("What's new"): weekly user-facing entries in `application/changelog/changelog.md` plus per-PR `changes/` files, and the rule that keeps it current |
| [features/event-information.md](features/event-information.md) | Public `/event-info` — a per-community event handbook; role-gated articles, shared prose loader |

Some subsystems have no dedicated feature doc and are covered at the reference level
instead — **volunteering**, **equipment lending**, **Challonge**, **API tokens** and
**in-app feedback** all live across
[data-model](reference/data-model.md), [services](reference/services.md),
[frontend](reference/frontend.md) and [rest-api](reference/rest-api.md).

## Work in flight (`plans/`, `reviews/`)

Transient by the convention below — each is deleted once its work ships, and the
feature docs become the truth.

| Doc | What it is |
|---|---|
| [plans/home-ux/](plans/home-ux/README.md) | Cutting the player-facing home from ten tabs to four. Both PRs have shipped — PR 1 (the nav restructure) and PR 2 (the join page's public preview, and the token-reachable `/room/{token}/seeds` view for the tournament-room PC); the directory remains as a design record due for deletion |
| [plans/match-runner/](plans/match-runner/README.md) | Making "how a match is run" a first-class type instead of a scattered `is_racetime` boolean, so a third race-management system can be added without touching sixteen call sites |
| [plans/5-wwr-seedgen.md](plans/5-wwr-seedgen.md) | Research notes behind the `wwr` (Wind Waker) generator: how SGL25 rolled by hand, the permalink format, and why there's no seed hash. Done; the implementation is in [reference/seed-generation.md](reference/seed-generation.md#wwr) |
| [plans/sgl26-randomizers.md](plans/sgl26-randomizers.md) | Research notes: what each SGL 2026 on-site tournament races on, per its rules doc, with the open questions to settle before `Tournament.randomizer_notes` is filled in. Delete after the October event |
| [plans/pre-event-validation/](plans/pre-event-validation/README.md) | The manual test plan for everything the mocks and `/ui-validation` cannot reach — live Discord, real randomizers, racetime, push on real phones, PostgreSQL concurrency, deployment and load — plus the day-of runbook. Delete after the October event |
| [plans/sahasrahbot-lessons/](plans/sahasrahbot-lessons/README.md) | Status tracker for the seven findings in [reviews/sahasrahbot-lessons.md](reviews/sahasrahbot-lessons.md): which shipped (the seed-provider envelope, seed provenance, abandoned-run expiry, racetime token refresh, config prerequisites) and why the rest are still open |
| [reviews/](reviews/README.md) | UX evaluations, one flow each, measured against the running app — with the cross-cutting themes that recur across them |

## Conventions for this directory

- **CLAUDE.md is canonical for rules.** A doc here explains mechanism and gives examples; it should not restate a rule CLAUDE.md already states — that is how the two drift apart.
- **No hand-maintained counts.** Doc-stated totals of models, routers, services or repositories go stale within weeks — every such number in this tree was wrong before this was written, and two of them contradicted each other because the same count was maintained in two files. Say "every model", not a number, unless the number is the point and stable (the eleven roles, the four bracket formats).
- **Design records are not kept after they ship.** Once a plan is implemented, the feature doc is the truth and the plan is deleted — git history holds the rationale. The same goes for point-in-time audit reports.
- **Prose that restates code is what rots.** Prefer a table of names and one-line purposes over paragraphs describing what a function does.
- `.claude/` hooks nag on edits to `models/`, `application/`, `api/`, `pages/`, `theme/`, `discordbot/` and `mcpserver/` — see [`.claude/README.md`](../.claude/README.md).

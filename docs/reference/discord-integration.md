# Discord Integration Reference

*Implementation reference for the in-process Discord bot, the outbound DM queue, and the button interaction handlers. Part of the [documentation index](../README.md).*

This page documents mechanics only — singletons, method signatures, custom_id wire formats, error paths. Behavior-level documentation lives in [discord.md](../features/discord.md) (notifications, guild-role sync, mock mode) and [match-participation.md](../features/match-participation.md) (crew, acknowledgment, watching).

> **Sibling: the racetime bot.** The [`racetimebot/`](../../racetimebot/) package (connection, handler, manager, mock, transport) is the racetime.gg analogue of this Discord bot — a lifespan-managed connection running as a peer in the same single worker. Its runtime and health model are documented in [services.md § racetimebot](services.md); the one-bot-many-guilds tenant routing both bots follow is in [multitenancy.md](../features/multitenancy.md#discord-one-bot-many-guilds).

## Key files

| File | Contents |
|---|---|
| [`application/services/discord/discord_service.py`](../../application/services/discord/discord_service.py) | `get_discord_bot()` singleton factory, the handler/view-factory registries, the DM surface of `DiscordService` / `MockDiscordService`, mock selection |
| [`application/services/discord/discord_guild_ops.py`](../../application/services/discord/discord_guild_ops.py) | `GuildOpsMixin` + `MockGuildOpsMixin` — guild, role and member reads/writes |
| [`application/services/discord/discord_scheduled_events.py`](../../application/services/discord/discord_scheduled_events.py) | `ScheduledEventsMixin` + `MockScheduledEventsMixin` — the Scheduled Events wrappers the reconciler drives |
| [`application/services/discord/discord_member_events.py`](../../application/services/discord/discord_member_events.py) | The `GUILD_MEMBER_*` listeners' bodies: `sync_member_roles` and `sync_member_avatar` |
| [`application/services/discord/discord_queue.py`](../../application/services/discord/discord_queue.py) | Outbound send queue: `start()`, `stop()`, `enqueue()` over the shared `CoroutineQueue` |
| [`application/utils/coroutine_queue.py`](../../application/utils/coroutine_queue.py) | `CoroutineQueue` + `bind_module_state` — the serial-worker primitive `discord_queue` is one instance of |
| [`application/services/discord/discord_link_service.py`](../../application/services/discord/discord_link_service.py) | Verified tenant ↔ guild linking (bot-auth flow + server-side Manage Server re-check) |
| [`application/services/discord/discord_role_mapping_service.py`](../../application/services/discord/discord_role_mapping_service.py) | Discord role → app role mappings and the login-time/live `sync_user_roles` |
| [`application/services/discord/discord_event_sync_service.py`](../../application/services/discord/discord_event_sync_service.py) | Tenant-facing Scheduled Events config: per-tournament opt-in + "reconcile now" (gated by `can_manage_sync`) |
| [`application/services/discord/discord_event_reconciler_service.py`](../../application/services/discord/discord_event_reconciler_service.py) | Idempotent create/update/cancel of guild Scheduled Events; shared-guild safe (works only from this tenant's own link rows) |
| [`application/services/discord/discord_event_worker.py`](../../application/services/discord/discord_event_worker.py) | Background reconcile loop, gated by `DISCORD_EVENTS_SYNC_ENABLED` |
| [`discordbot/__init__.py`](../../discordbot/__init__.py) | Registers every interaction handler and view factory with `discord_service` at import |
| [`discordbot/_ack_common.py`](../../discordbot/_ack_common.py) | `run_dm_interaction` (the shared handler ladder), `make_acknowledged_view`, `send_ephemeral` |
| [`discordbot/_tenant.py`](../../discordbot/_tenant.py) | `match_tenant_id` / `crew_tenant_id` / `assignment_tenant_id` / `reschedule_request_tenant_id` — deliberately unscoped tenant discovery |
| [`discordbot/crew_signup.py`](../../discordbot/crew_signup.py) | Crew signup buttons + handler (`crew_signup:`) |
| [`discordbot/match_acknowledgment.py`](../../discordbot/match_acknowledgment.py) | Player Acknowledge button + handler (`match_ack:`) |
| [`discordbot/crew_acknowledgment.py`](../../discordbot/crew_acknowledgment.py) | Crew Acknowledge button + handler (`crew_ack:`) |
| [`discordbot/volunteer_acknowledgment.py`](../../discordbot/volunteer_acknowledgment.py) | Volunteer shift Acknowledge button + handler (`volunteer_ack:`) |
| [`discordbot/watch_buttons.py`](../../discordbot/watch_buttons.py) | Unwatch button + handler (`match_watch:`) |
| [`discordbot/reschedule_agreement.py`](../../discordbot/reschedule_agreement.py) | The opponent's Agree button + handler (`reschedule_agree:`) |
| [`discordbot/match_hard_preset.py`](../../discordbot/match_hard_preset.py) | The harder-settings opt-in / back-out button + handler (`match_hard:`) |
| [`discordbot/login.py`](../../discordbot/login.py) | `/login`, `/wizzrobe login-panel` and the panel's Log in button (`wiz_login:`) |
| [`application/services/discord/discord_login_service.py`](../../application/services/discord/discord_login_service.py) | `DiscordLoginService` — mints and redeems the one-time login links; the panel's STAFF gate |
| [`main.py`](../../main.py) | `init_discord_bot()` / `close_discord_bot()`, the `import discordbot` registration hook, queue and worker start/stop in the FastAPI lifespan |
| [`application/utils/mocks/mock_discord.py`](../../application/utils/mocks/mock_discord.py) | `is_mock_discord()` flag with production guard |
| [`application/services/match/_schedule_notifications.py`](../../application/services/match/_schedule_notifications.py) | `MatchNotificationMixin` — the match notification fan-out coroutines, composed into `MatchScheduleService` |
| [`application/services/notification_links.py`](../../application/services/notification_links.py) | `DMLink` builders (`player_schedule`, `player_match`, `player_hard_preset`, `admin_match`, …) — absolute, tenant-qualified, never raise |
| [`application/utils/discord_messages.py`](../../application/utils/discord_messages.py) | `DMLink`, plain-text DM builders (public functions) + ephemeral confirmation strings, for the match lifecycle, stage calls and harder-preset flow |
| `discord_messages_crew.py` / `_volunteer.py` / `_qualifier.py` / `_reschedule.py` / `_tenant.py` | The same builders for every other domain — one sibling module each |
| [`application/utils/discord_embeds.py`](../../application/utils/discord_embeds.py) | Embed-card builders (`match_embed`, `state_changed_embed`, `matchup_ready_embed`, `stage_embed`, `volunteer_embed`, `notification_embed`, `time_field`, `COLOR_*`) |
| [`application/services/crew_service.py`](../../application/services/crew_service.py) | Crew approval → crew acknowledgment DM |

## Architecture overview

The bot is a **discord.py `commands.Bot`** that lives inside the single Uvicorn worker (see [architecture.md](../architecture.md) for why one worker is a hard requirement). It is created lazily by `get_discord_bot()` in [`application/services/discord/discord_service.py`](../../application/services/discord/discord_service.py) and started during FastAPI lifespan startup in [`main.py`](../../main.py):

1. `import discordbot` — registers the interaction handlers and view factories (below) before any interaction can dispatch or DM can be sent.
2. `init_discord_bot()` — under `MOCK_DISCORD` it logs `MOCK_DISCORD enabled — skipping Discord bot start.` and returns. Otherwise it reads `DISCORD_TOKEN` and schedules `bot.start(token)` as an asyncio task with a done-callback that logs a crashed task. An unset token logs a warning and continues — the app runs, but the bot never connects.
3. `discord_queue.start()` — starts the outbound send worker; `discord_event_worker.start()` runs too when `DISCORD_EVENTS_SYNC_ENABLED` is on.
4. On shutdown, the reverse: `discord_queue.stop()` → `close_discord_bot()` (`bot.close()` + task cancel; no-op under mock) → DB close.

Degradation is graceful at every layer: when the bot is missing or not connected, every `DiscordService` method returns a `(False, reason)` tuple instead of raising, and the fan-out methods log the failure and move on.

**The import cycle is inverted into a registry.** `discord_service.py` owns `_interaction_handlers` / `_view_factories` plus `register_interaction_handler(prefix, fn)` and `register_view_factory(kind, factory)` (keyed by the `VIEW_*` constants), and never imports `discordbot` at all. [`discordbot/__init__.py`](../../discordbot/__init__.py) registers all seven handlers and all seven view factories at import; `main.py`'s lifespan imports the package once. The dependency runs one way: `discordbot` → `application.services`. This mirrors `application/events/match_live.py`.

> **One Discord library, and it is discord.py.** Never add **py-cord** alongside it. The two are hard forks that both install into the same top-level `discord/` package — neither uninstalls the other, so whichever unpacks last silently owns the ~107 modules they share. This repo shipped both for a while, py-cord won, and `discord.EntityType` / `discord.PrivacyLevel` quietly stopped existing: every scheduled-event create/edit raised `AttributeError`, which `_scheduled_event_op`'s `except Exception` swallowed into a `(False, …)` tuple. The suite stayed green because that path is only ever reached through `MockDiscordService`. `TestDiscordLibraryIdentity` in [`tests/services/test_discord_service.py`](../../tests/services/test_discord_service.py) now asserts the `discord` package has exactly one provider, and `.claude/scripts/enforce_safe_commands.py` blocks `poetry add py-cord`.
>
> Dropping py-cord from the lockfile is not enough to clear an environment that already has it: `poetry install` leaves packages that are no longer locked in place, so a cached container venv keeps serving py-cord's `discord/`. [`.claude/hooks/install-deps.sh`](../../.claude/hooks/install-deps.sh) therefore runs **`poetry sync`**, which removes it — and then repairs discord.py, because uninstalling either fork deletes the modules both recorded as their own and leaves the survivor importable but hollow. `poetry sync` alone will not fix that: Poetry still counts discord.py as installed, so the hook force-reinstalls it when `discord.__title__`/`discord.EntityType` come back wrong.

**Embeds vs. text.** Each notification is *sent* as a colour-coded `discord.Embed` card built in [`discord_embeds.py`](../../application/utils/discord_embeds.py) (state colour, Tournament/Players/Time/Stage field grid, native `<t:unix:F>·<t:unix:R>` timestamps, community-name footer via `TenantService.current_community_name()`). `send_dm(..., embed=embed)` sends the embed as the Discord representation but **always mirrors the plain-text `message`** (from [`discord_messages.py`](../../application/utils/discord_messages.py)) to the recipient's web-push devices — so the embed layer is purely additive and the text builders remain the mirror/fallback copy. The embed is built in the request's tenant context (at enqueue time), not in the serial `discord_queue` worker.

Under `MOCK_DISCORD=true` the entire service is stubbed: the bottom of `discord_service.py` rebinds the module-level name at import time —

```python
if is_mock_discord():
    DiscordService = MockDiscordService
```

— so every importer transparently gets the mock. `is_mock_discord()` returns true when `MOCK_DISCORD` is truthy (`env_flag`: `1`/`true`/`yes`/`on`), and **raises `RuntimeError` if combined with `ENVIRONMENT=production`** (the mock also bypasses OAuth — see [discord.md § Mock mode](../features/discord.md#mock-mode) and [authentication.md](authentication.md)).

The bot and the web UI are **peer presentation layers**: handlers in `discordbot/` call the same service-layer methods ([services.md](services.md)) as the NiceGUI pages, so both paths share validation, audit logging, and DB effects.

```mermaid
sequenceDiagram
    participant SVC as Service (MatchService, CrewService, ...)
    participant Q as discord_queue worker
    participant DS as DiscordService
    participant API as Discord API
    participant U as User (DM)
    participant BOT as bot on_interaction
    participant H as discordbot/ handler

    SVC->>Q: enqueue(notify coroutine) — returns immediately
    Q->>DS: await coroutine → send_dm_with_*()
    DS->>API: fetch_user(id), user.send(message, view)
    API->>U: DM with button(s)
    U->>API: click (component, custom_id)
    API->>BOT: interaction event
    BOT->>H: route by custom_id prefix
    H->>SVC: service call (acknowledge, signup, unwatch)
    H->>U: ephemeral confirmation reply
```

## The bot singleton

`get_discord_bot()` creates the bot once and stores it in module-level `_bot_instance`:

- **Intents**: `discord.Intents.default()` plus `guilds=True`, `members=True`, `dm_messages=True` (needed for DMs and guild/role visibility).
- **Constructor**: `commands.Bot(command_prefix='!', intents=intents)`. The prefix is set but no text commands are registered. The bot sends DMs, receives component interactions, and serves the slash commands below.
- **Slash commands**: the bot package hands each command (or group) to `register_app_command`; the bot's `setup_hook` adds them to `bot.tree` and runs one global `tree.sync()` at login. A failed sync is logged and the commands Discord already has keep working. Today that's `/login` and `/wizzrobe login-panel` ([`login.py`](../../discordbot/login.py)), both `guild_only`; the `wizzrobe` group carries `default_permissions=manage_guild`, so Discord shows it only to members with Manage Server.

Four gateway events are registered:

| Event | Behavior |
|---|---|
| `on_ready` | Logs `Discord bot ready. Logged in as <user>` |
| `on_interaction` | The single dispatch point for buttons: handles `InteractionType.component` only, reads `interaction.data['custom_id']`, and looks the prefix (text before the first `:`) up in `_interaction_handlers` |
| `on_member_update` | Fires `sync_member_avatar` when the member's global avatar changed, and `sync_member_roles` (with a `GuildMember` snapshot, so an unknown mapped-role holder can be provisioned) when role membership changed (the event also fires for nick/timeout edits, which are ignored) |
| `on_member_remove` | Left/kicked/banned — the same role re-sync, which strips their Discord-sourced roles |

Both live in [`discord_member_events.py`](../../application/services/discord/discord_member_events.py).

`sync_member_avatar(member)` stores `Member.avatar.key` (the **global** hash, matching what the OAuth login records) on `User.discord_avatar`, or `NULL` when the avatar was removed. Unknown users are a no-op — a presence event never provisions an account — and an unchanged hash writes nothing. This is the only avatar refresh someone who never signs in will get.

`sync_member_roles(guild_id, discord_user_id, member=None)` fans out across **every tenant sharing that guild** (`TenantService.list_tenants_for_guild`), each inside its own `tenant_scope`; an unknown guild is a no-op. An unknown user gets an account when the update event passes `member` and one of its roles is mapped by a tenant sharing the guild; a removal passes nothing, so it never provisions. It logs and swallows everything, so a bad event can never crash the gateway connection. Behavior: [discord.md § Guild-role → app-role sync](../features/discord.md#guild-role--app-role-sync).

Registered handler prefixes:

| `custom_id` prefix | Handler |
|---|---|
| `crew_signup:` | [`crew_signup.py`](../../discordbot/crew_signup.py) → `handle_crew_signup_interaction` |
| `match_ack:` | [`match_acknowledgment.py`](../../discordbot/match_acknowledgment.py) → `handle_match_acknowledgment_interaction` |
| `crew_ack:` | [`crew_acknowledgment.py`](../../discordbot/crew_acknowledgment.py) → `handle_crew_acknowledgment_interaction` |
| `volunteer_ack:` | [`volunteer_acknowledgment.py`](../../discordbot/volunteer_acknowledgment.py) → `handle_volunteer_acknowledgment_interaction` |
| `vol_here:` / `vol_late:` / `vol_late_min:` / `vol_cant:` / `vol_cant_reason:` | [`volunteer_arrival.py`](../../discordbot/volunteer_arrival.py) → the shift-start DM's buttons. `vol_cant:` answers with a **modal** (the reason form), so it does not defer; the modal's submit (`vol_cant_reason:`) is routed through the same registry because `on_interaction` dispatches `modal_submit` as well as `component` |
| `match_watch:` | [`watch_buttons.py`](../../discordbot/watch_buttons.py) → `handle_unwatch_interaction` |
| `reschedule_agree:` | [`reschedule_agreement.py`](../../discordbot/reschedule_agreement.py) → `handle_reschedule_agree_interaction` |
| `wiz_login:` | [`login.py`](../../discordbot/login.py) → `handle_login_panel_interaction`. `wiz_login:<tenant_id>` — the community travels in the id because one server can back several. It doesn't use `run_dm_interaction`. Like `/login` and `/wizzrobe login-panel`, it defers first (`defer(ephemeral=True, thinking=True)`), because minting provisions an account and the panel posts to the channel before answering, which can miss Discord's 3-second window (`10062 Unknown interaction`). The answer goes out as an ephemeral follow-up, and the link reply is deleted with `WebhookMessage.delete(delay=LINK_TTL_SECONDS)`. If the defer itself fails, it falls back to `response.send_message(..., delete_after=…)` |
| `match_hard:` | [`match_hard_preset.py`](../../discordbot/match_hard_preset.py) → `handle_hard_preset_interaction`. Two verbs, `match_hard:in:<match_id>` and `match_hard:out:<match_id>`; the pressed message's button is swapped for its opposite, so a second press is the way back rather than a repeat |

Because dispatch is raw-prefix routing rather than registered `discord.ui.View` callbacks, **buttons keep working across bot restarts**: all state is encoded in the `custom_id`, nothing is held in memory, and no `bot.add_view()` persistent-view registration is needed. All views are built with `timeout=None`.

## DiscordService API

`DiscordService` is instantiated per use (`DiscordService()`); construction just grabs the bot singleton. **No method raises** — each returns `Tuple[bool, Union[str, list, dict, set, bool, int]]`: `(True, payload)` on success (`"Message sent successfully."` for the send methods), `(False, reason)` on failure.

| Method | Signature | Behavior |
|---|---|---|
| `send_dm` | `(user_id: int, message: str, view_factory=None, embed=None, link: DMLink = None, on_sent=None)` | Plain DM via `fetch_user(user_id)` + `user.send(...)`. `view_factory` is a zero-arg callable invoked just before the send; `embed`, when present, is sent **instead of** the plain content (Discord would otherwise show both) while `message` stays the web-push/fallback text. `link` is appended to the view as a Discord link button (`_with_link_button`; a blank URL adds nothing) and becomes the web-push mirror's tap target. `on_sent(channel_id, message_id)` is awaited once Discord accepts the DM; see [Edited staff DMs](#edited-staff-dms). |
| `edit_dm` | `(channel_id: int, message_id: int, embed, link: DMLink = None)` | Rewrites a sent DM in place through `get_partial_messageable(...).get_partial_message(...).edit(...)`; `link` becomes the only button. Discord doesn't notify on an edit. |
| `send_dm_with_crew_buttons` | `(user_id, message, match_id: int, embed=None, link=None)` | DM with the two crew signup buttons (`VIEW_CREW_SIGNUP`). |
| `send_dm_with_acknowledgment_button` | `(user_id, message, match_id: int, embed=None, link=None)` | DM with the player Acknowledge button (`VIEW_MATCH_ACK`). |
| `send_dm_with_crew_acknowledgment_button` | `(user_id, message, crew_type: str, crew_id: int, embed=None, link=None)` | DM with the crew Acknowledge button (`VIEW_CREW_ACK`). `crew_type` is `'commentator'` or `'tracker'`; `crew_id` is the `Commentator`/`Tracker` row id. |
| `send_dm_with_volunteer_arrival_buttons` | `(user_id, message, assignment_id: int, embed=None, link=None)` | The shift-start DM: I'm here / Running late / I can't make it (`VIEW_VOLUNTEER_ARRIVAL`). |
| `send_dm_with_volunteer_acknowledgment_button` | `(user_id, message, assignment_id: int, embed=None, link=None)` | DM with the volunteer shift Acknowledge button (`VIEW_VOLUNTEER_ACK`). |
| `send_dm_with_unwatch_button` | `(user_id, message, match_id: int, embed=None, link=None)` | DM with the Unwatch button (`VIEW_UNWATCH`). |
| `send_dm_with_reschedule_agree_button` | `(user_id, message, request_id: int, embed=None, link=None)` | DM with the opponent's Agree button (`VIEW_RESCHEDULE_AGREE`). |
| `send_dm_with_hard_preset_buttons` | `(user_id, message, match_id: int, opted_in: bool, embed=None, link=None)` | DM with the harder-settings button (`VIEW_HARD_PRESET`). `opted_in` picks **which single button** the DM carries, so the message never shows a state the reader is not already in — the only sender whose view differs per recipient. |
| `get_bot` | `()` (sync) | Returns the bot instance (or `None` in the mock). |
| `list_guilds` | `()` | `(True, [{"id": int, "name": str}, ...])` from the bot's cached guild list. |
| `list_guild_roles` | `(guild_id: int)` | Roles as `[{"id", "name", "color", "mappable"}]` (`mappable` is False for `@everyone` and bot/integration-managed roles). Resolves the guild via cache then `fetch_guild`; prefers `guild.fetch_roles()`, falls back to cached `guild.roles`. |
| `get_guild_summary` | `(guild_id: int)` | `(True, {"id", "name"})` for a guild the bot can see — renders the connected server's name and confirms the bot actually joined after a link. |
| `is_guild_member` | `(guild_id: int, user_id: int)` | `(True, bool)` — whether the user is in the guild; `(False, error)` when the bot can't tell. Behind Discord auto-join at the membership gate. |
| `member_can_manage_guild` | `(guild_id: int, user_id: int)` | `(True, bool)` — owner / Administrator / Manage Server. The authorization proof for `DiscordLinkService`; an indeterminate answer returns `(False, error)` so callers fail **closed**. |
| `add_role_to_user` | `(guild_id, user_id, role_id, reason=None)` | Adds a guild role to a member. Resolves member via cache then `fetch_member`, role via cache then `fetch_roles`. `reason` goes to the Discord audit log. |
| `remove_role_from_user` | `(guild_id, user_id, role_id, reason=None)` | Mirror of `add_role_to_user` using `member.remove_roles`. |
| `get_member_role_ids` | `(guild_id, user_id)` | `(True, {role_id, ...})` for the member's current roles (`@everyone` excluded); `(True, set())` when the member is not in the guild. A hard failure (bot not ready, API error) returns `(False, reason)` so callers can fail open. Powers the role sync. |
| `list_members_with_roles` | `(guild_id, role_ids)` | `(True, [GuildMember, ...])` for every non-bot member holding one of `role_ids`. Uses the member cache when the guild is chunked, else pages `fetch_members`. Powers the Sync All Users account provisioning. |
| `create_scheduled_event` | `(guild_id, *, name, start_time, end_time, description=None, location='Stream')` | Creates an external Scheduled Event; `(True, event_id)`. |
| `edit_scheduled_event` | `(guild_id, event_id, *, name, start_time, end_time, description=None, location='Stream')` | Edits an existing Scheduled Event to match the current schedule. |
| `delete_scheduled_event` | `(guild_id, event_id)` | Cancels a Scheduled Event; treats an already-gone event as success. |

`add_role_to_user` / `remove_role_from_user` currently have no callers; they exist as API surface only. `get_member_role_ids` is called by `DiscordRoleMappingService.sync_user_roles`, `list_members_with_roles` by its `sync_all_users`. The three scheduled-event methods share one `_scheduled_event_op` guard/error wrapper and are driven by `DiscordEventReconcilerService`.

> **Privileged intent.** Reading guild members requires the **Server Members Intent**, enabled both in code (`intents.members = True`, already set) **and** toggled on for the bot application in the Discord Developer Portal. The bot must also be invited to the guild (`bot` scope). Without these, `get_member_role_ids` returns errors / empty sets and the sync is a safe no-op.

`send_dm` is the chokepoint every notification fan-out flows through; **before** the guard ladder it enqueues a fire-and-forget mirror of the message to the recipient's web-push devices (no-op unless [device notifications](../features/web-push.md) are configured), so pushes go out even when the bot is down or the user blocks DMs. Corollary: a `(False, ...)` return means only the *Discord* leg failed — devices may already have been notified, so do not blind-retry. `MockDiscordService.send_dm` deliberately skips the mirror so mock mode has no external side effects.

Every method runs the same guard ladder before touching the API, mapped to error strings:

| Condition | Returned `(False, ...)` message |
|---|---|
| Bot is `None` | `Discord bot not initialized` |
| `not bot.is_ready()` | `Discord bot is not connected. Please try again in a moment.` |
| `discord.NotFound` | `User not found` |
| `discord.Forbidden` | `Cannot send DM to this user (DMs may be disabled)` |
| `discord.HTTPException` | `Failed to send message: <detail>` |
| Any other exception | `Discord bot error: <detail>` |

Role and guild methods use the same ladder with their own messages: `Guild not found`, `The bot is not in this server.`, `Member not found in guild`, `Role not found in guild`, `Bot lacks permissions or role hierarchy prevents this action`.

Caller pattern: `from application.services.discord.discord_service import DiscordService` (or `from application.services import DiscordService`), then check the returned tuple and log — never wrap in `try`/`except`.

### MockDiscordService

`MockDiscordService` (same file, composing the mock mixin each split-out module carries) mirrors the public surface exactly. Selection happens once at import time via the `DiscordService = MockDiscordService` rebinding shown above — callers never branch on mock mode themselves.

- The seven button variants delegate to the single `send_dm` stub, which **prints to stdout** (`[MOCK Discord DM] -> <user_id>: <message> [embed: <title>] [button: <label> -> <url>]`, the suffixes only when present — the link is the half of a DM mock mode can verify) and returns `(True, "Message sent (mock)")`, so notification code paths run end-to-end without Discord.
- `get_bot()` returns `None`.
- `list_guilds` / `list_guild_roles` / `get_member_role_ids` / `list_members_with_roles` / `get_guild_summary` / `member_can_manage_guild` / `is_guild_member` answer from `application/utils/mocks/mock_discord_data.py` (`list_members_with_roles` draws from `MOCK_GUILD_ROSTER`, three members who have never signed in, so Sync All Users visibly creates accounts in mock mode); the role/event methods print a `[MOCK Discord] …` line and return success.

Button interactions are **not** testable in mock mode (no bot connection); see [discord.md § Mock mode](../features/discord.md#mock-mode).

## The async send queue

[`discord_queue.py`](../../application/services/discord/discord_queue.py) is one instance of the shared `CoroutineQueue` ([`application/utils/coroutine_queue.py`](../../application/utils/coroutine_queue.py)), bound with `bind_module_state(__name__, _q)`. A single fan-out can DM dozens of users (each a `fetch_user` + `send` round-trip), so services must never await it inline — UI handlers and the shared NiceGUI event loop would stall. Instead they hand the *coroutine object* to the queue and return immediately.

| Function | Signature | Behavior |
|---|---|---|
| `start` | `() -> None` (sync) | Delegates to `CoroutineQueue.start()` — creates the single worker task on the running loop. Called once from lifespan startup. |
| `stop` | `() -> None` (async) | Delegates to `CoroutineQueue.stop()`: logs `discord_queue stopping with %d item(s) still queued — they will not be sent`, cancels the worker, awaits it, then closes what was left (`discard_pending`). Called from lifespan shutdown. |
| `discard_pending` | `() -> int` (sync) | Closes every queued send without running it — for a process that never starts the worker, such as `scripts/seed_dev.py`. |
| `enqueue` | `(coro: Coroutine) -> None` (sync) | Wraps the coroutine in the caller's `tenant_scope` (below), then `put_nowait` onto the unbounded queue. Safe from sync or async code; never blocks. |

The worker awaits one coroutine at a time, so sends are strictly serialized in enqueue order. Failures are **logged** (reaching logs + Sentry), never swallowed silently, and the queue survives a bad send.

**Tenant scope across the queue boundary.** The worker task is created once at startup with **no tenant in scope**, and a coroutine handed to `asyncio.Queue` does not carry the enqueuer's context — so a `notify_*` coroutine reaching `require_tenant_id()` would raise in the worker and silently drop the DM. `enqueue` therefore snapshots `get_current_tenant_id()` at call time (request context) and re-establishes it via `tenant_scope` when the worker awaits the coroutine. Enqueue **from a tenant-scoped context**; a genuinely tenant-agnostic send may enqueue unscoped and is queued as-is.

```python
from application.services import discord_queue

# Build the coroutine object; the worker awaits it later.
discord_queue.enqueue(self.match_schedule_service.notify_match_participants(match, msg))
return match   # caller does not wait for any DM
```

What gets enqueued is usually a `MatchScheduleService.notify_*` fan-out coroutine, not an individual DM — the per-recipient loop and its own error logging run inside the worker. Variations: `MatchScheduleService.generate_seed` enqueues a locally defined `_send_seed_dms()` closure, `_bracket/notifications.py` enqueues one `_send_matchup_ready(...)` coroutine per entrant, and `CrewService` / `VolunteerScheduleService` / the reschedule, signup and membership notifiers enqueue single `DiscordService` calls directly.

Deliberate exception to the queue rule: the admin Send message dialog ([`theme/dialog/send_message_dialog.py`](../../theme/dialog/send_message_dialog.py)) awaits `DiscordService.send_dm()` directly so the admin sees the result immediately in a `ui.notify`.

Because `stop()` cancels the worker without draining, anything still queued at shutdown is dropped (the logged count is the only trace).

## Edited staff DMs

A community event to review, an ADA request, a join request and a reschedule request each DM every staff member (reschedules: every decider); a crew signup DMs the tournament's admins and crew coordinators. Once one decides it, [`staff_request_dms`](../../application/services/staff_request_dms.py) edits every copy to say who and when ("✅ Approved by Hurfy", "You" on the decider's own copy), recolours it, and swaps the button for one that opens the thing. Each landed copy is recorded in `StaffRequestDM` via `send_dm(on_sent=...)`; the edit runs on the same serial queue, so it always sees the records of sends queued before it. Only DMs sent after this shipped can be edited, and the web-push copy is never changed.

## Interaction handlers (`discordbot/`)

All views are `discord.ui.View(timeout=None)` holding plain `discord.ui.Button`s with static `custom_id`s and no callbacks — routing happens in `on_interaction`. A DM's `DMLink` is appended by `send_dm` as a link-style button, which has no `custom_id` and never reaches a handler. Each module exposes its prefix as `CUSTOM_ID_PREFIX`.

| `custom_id` | Produced by | Service called | View swap on click? |
|---|---|---|---|
| `crew_signup:commentator:<match_id>` / `crew_signup:tracker:<match_id>` | `make_crew_signup_view(match_id)` | `CrewService.signup_crew(match_id, user, role)` | No — buttons stay live for the other recipients of the same fan-out |
| `match_ack:ack:<match_id>` | `make_match_acknowledgment_view(match_id)` | `MatchService.acknowledge_match(match_id, user)` | Yes → `match_ack:acknowledged` |
| `crew_ack:commentator:<crew_id>` / `crew_ack:tracker:<crew_id>` | `make_crew_acknowledgment_view(crew_type, crew_id)` (`crew_id` is the `Commentator`/`Tracker` row id, not a match id) | `CrewService.acknowledge_crew_assignment(crew_id, crew_type, user)` | Yes → `crew_ack:acknowledged` |
| `volunteer_ack:<assignment_id>` | `make_volunteer_acknowledgment_view(assignment_id)` | `VolunteerScheduleService.acknowledge(assignment_id, user)` | Yes → `volunteer_ack:acknowledged` |
| `match_watch:unwatch:<match_id>` | `make_unwatch_view(match_id)` | `MatchWatcherService.unwatch(match_id, user)` | No — the button stays live |
| `reschedule_agree:agree:<request_id>` | `make_reschedule_agree_view(request_id)` | `MatchRescheduleService.record_opponent_agreement(request_id, user)` | No — agreement is advisory, and the confirmation says staff still decide |
| `match_hard:in:<match_id>` / `match_hard:out:<match_id>` | `make_hard_preset_view(match_id, opted_in)` — one button, whichever the reader can do next | `MatchHardPresetService.opt_in` / `.withdraw(match_id, user)` | Yes → the opposite button, with the `player_hard_preset` link button rebuilt beside it |
| `<prefix>:acknowledged` | `make_acknowledged_view(CUSTOM_ID_PREFIX)` — one shared factory in `_ack_common.py`, not redefined per module | — | Disabled placeholder; no handler action |

### The shared ladder (`_ack_common.py`)

Every handler supplies a `parse`, a `resolve_tenant`, a `not_found_message`, and a body to `run_dm_interaction`, which runs these steps in order:

1. **Defer ephemerally** — extends Discord's 3-second interaction deadline. Every handler defers; a failed defer is logged and the reply falls back from `interaction.followup` to `interaction.response` (`send_ephemeral`).
2. **Parse the `custom_id`** — raising `DMInteractionError` short-circuits with its text as the reply. Malformed → `Invalid interaction.`; non-integer id → `Invalid match ID.` / `Invalid crew ID.` / `Invalid assignment ID.` / `Invalid request ID.`; crew signup also validates the role token → `Invalid role.`.
3. **Resolve the tenant** from the referenced entity; `None` replies with the module's `not_found_message` (e.g. `Match not found.`).
4. **Inside `tenant_scope`, resolve the account** with `UserService().get_user_by_discord_id(str(interaction.user.id))`; a Discord user with no Wizzrobe account gets `MSG_NO_ACCOUNT`.
5. **Run the body**, then reply ephemerally. A service `ValueError` is relayed **verbatim**; anything else is logged and answered with the module's generic retry message.

Business rules live in the services, not the handlers — the "match already finished ⇒ crew signup closed" rule, for example, is raised as a `ValueError` from `CrewService.signup_crew` so the web UI and REST API enforce it identically.

[`_tenant.py`](../../discordbot/_tenant.py) holds `match_tenant_id`, `crew_tenant_id`, `assignment_tenant_id`, and `reschedule_request_tenant_id`. These are **deliberately unscoped global reads** — the sanctioned load-or-404 shape — because a DM button arrives with `interaction.guild_id is None` and the tenant has to be *discovered* from the referenced row before anything can be scoped to it. This is the one documented exception to the tenant-scoping rule in [multitenancy.md](../features/multitenancy.md).

Per-module notes beyond the table: the crew-signup reply is `crew_signup_confirmation(role, player_names)` and a duplicate signup surfaces the service's `User already signed up as <role>`; the three ack handlers edit the original DM's view to the disabled `Acknowledged` button (a failed edit only logs a warning); the Unwatch button rides along on lifecycle DMs to watchers — there is no "watch" button in Discord, watching starts from the web schedule.

## Message flows

Outbound notifications are coroutines enqueued via `discord_queue.enqueue(...)` from [`match_service.py`](../../application/services/match/match_service.py), [`match_schedule_service.py`](../../application/services/match/match_schedule_service.py) and its [`_schedule_notifications.py`](../../application/services/match/_schedule_notifications.py) mixin, [`match_cancellation.py`](../../application/services/match/match_cancellation.py), [`match_hard_preset_service.py`](../../application/services/match/match_hard_preset_service.py), [`stage_reminder.py`](../../application/services/match/stage_reminder.py), [`crew_service.py`](../../application/services/crew_service.py), [`_reschedule_notifications.py`](../../application/services/_reschedule_notifications.py), [`_tournament_signup.py`](../../application/services/_tournament_signup.py), [`tenant_membership_service.py`](../../application/services/tenant_membership_service.py), the volunteer services, and [`_bracket/notifications.py`](../../application/services/_bracket/notifications.py). The fan-out coroutines never raise: each skips recipients without a `discord_id`, logs per-DM failures (`send_dm` itself drops the Discord copy when `User.dm_notifications` is off and still mirrors to devices), and swallows unexpected errors.

Recipient selection helpers:

- `notify_match_participants` — players + approved crew + watchers, deduplicated by `discord_id`; watchers get the Unwatch-button variant, everyone else a plain DM.
- `notify_match_crew` — approved crew + watchers, **excluding players** (players get the ack-request DM instead); watchers again get the Unwatch variant.
- `notify_acknowledgment_request` — players with a pending `MatchAcknowledgment` row only.
- `notify_match_cancelled` — the recipient set collected *before* the delete, since the match row is gone by send time.
- `notify_stage_changed` / `notify_stage_reminder` — players + approved crew + watchers, via the shared `_notify_stage` body.
- `notify_tournament_subscribers_scheduled` / `notify_stream_candidate_subscribers` — tournament subscribers by notification level ([discord.md § Tournament notification preferences](../features/discord.md#tournament-notification-preferences)), minus `MatchScheduleService._collect_notified_discord_ids` (players + approved crew already DMed). The stream-candidate fan-out returns early if the match already has a stage.

### DM message builders

Message text comes from **public functions** in [`discord_messages.py`](../../application/utils/discord_messages.py) — not from methods on any service. Services import the builders they need and the interaction handlers import the ephemeral confirmation strings. Times are passed in as Discord's native `<t:unix:F>` markup (`discord_embeds.time_field`), so each recipient's own client renders them in their own zone — never a formatted string ([timezone-handling.md](../timezone-handling.md)); each builder takes optional fields and suppresses any that are empty/`None`.

| Builder | Used for | Content |
|---|---|---|
| `scheduled_dm` | New-match info DM (crew/subscribers) | Tournament name, players, scheduled time, stage |
| `rescheduled_dm` | Reschedule info DM (crew/subscribers) | Tournament name, players, new time, stage |
| `acknowledgment_request_dm` | Player ack request (scheduled/rescheduled variants via `rescheduled=`) | Match details plus optional stage and player names; ends with `Click **Acknowledge** below to confirm you've seen this.` |
| `stage_assigned_dm` / `stage_cleared_dm` / `stage_reminder_dm` | Stage call, its retraction, and the pre-match nudge | Tournament, stage, players, time; says to play at the stage (or back in the tournament room) |
| `checked_in_dm` | Seated transition | "checked in … about to begin" |
| `cancelled_dm` | Match cancellation | Tournament, match descriptor, optional reason, and whether the bracket matchup is released to reschedule |
| `state_changed_dm` | Started / Finished / Confirmed transitions | `Your match in **<tournament>** is now: **<state>**.` plus an optional info block |
| `matchup_ready_dm` | Bracket matchup ready / re-book | Tournament, round, opponent (with seed), best-of, schedule URL |
| `stream_candidate_dm` | Stream-candidate alert | Flag announcement + scheduled time + "Use the buttons below to sign up as crew." |
| `seed_dm` | Seed generation | Greeting, match/tournament, seed URL |
| `hard_preset_invite_dm` / `hard_preset_agreed_dm` / `hard_preset_broken_dm` / `hard_preset_override_dm` | Harder-preset offer, unanimity reached, unanimity broken, staff override | Tournament and preset names; the offer promises a lone opt-in stays private |
| `crew_assignment_dm` | Crew approval DM | Crew type, match title, players, scheduled time, stage; ends with "Please click below to acknowledge your assignment." |
| `crew_withdrawn_dm` | An approved crew member dropped out — **sent to the tournament's crew owners and the approver** | Who withdrew, hours of notice, the match block, "The slot is open again" |
| `crew_approval_withdrawn_dm` | Crew approval withdrawn | Same detail block as the approval DM; opens with the withdrawal and ends with "Check with an admin if this looks wrong." No buttons |
| `volunteer_assignment_dm` / `volunteer_reminder_dm` | Volunteer shift assigned / reminder | Position, label, shift start/end |
| `volunteer_unassigned_dm` | Coordinator took a volunteer off a shift | Position, label, start/end; no acknowledgment button — there is nothing to confirm |
| `volunteer_shift_changed_dm` | A shift a volunteer is on moved in time | Both windows (was → now), then "Tap **Acknowledge** to confirm you can still cover it." |
| `volunteer_released_dm` | A volunteer gave a shift back — **sent to the coordinators**, not the volunteer | Who dropped it, the shift block, hours of notice, their reason, "This slot is open again." |

The crew, volunteer, reschedule, qualifier and tenant builders live in the `discord_messages_*.py` siblings (`crew_*` in `_crew`, `volunteer_*` in `_volunteer`, `reschedule_requested_dm` / `reschedule_opponent_dm` / `reschedule_decided_dm` in `_reschedule`, `qualifier_*` in `_qualifier`, `join_requested_dm` / `join_decided_dm` in `_tenant`).

Ephemeral confirmation strings sit beside their domain's builders: `match_ack_confirmation`, `unwatch_confirmation(player_names, was_watching)`, `hard_preset_opt_in_confirmation` and `hard_preset_withdraw_confirmation` in `discord_messages.py`; `crew_signup_confirmation(role, player_names)` and `crew_ack_confirmation` in `_crew`; `volunteer_ack_confirmation` in `_volunteer`; `reschedule_agree_confirmation` in `_reschedule`.

### Flow table

| Flow | Triggering call site | `DiscordService` method | Buttons |
|---|---|---|---|
| Match scheduled — player ack request | `MatchService.create_match` / `submit_match_request` → `notify_acknowledgment_request(match, rescheduled=False)` | `send_dm_with_acknowledgment_button` | Acknowledge |
| Match rescheduled / players changed — ack request | `MatchService.update_match` → `notify_acknowledgment_request(match, rescheduled=<time changed>)` (acks re-seeded first) | `send_dm_with_acknowledgment_button` | Acknowledge |
| Harder settings offered | `notify_match_scheduled` (create / request / time change) → `MatchHardPresetService.send_offer(match)` (skipped when the tournament has no `hard_preset`, the seed is already rolled, or staff set a `preset_override`) | `send_dm_with_hard_preset_buttons` | Play the harder preset **or** Back out — one, chosen from the reader's own answer |
| Harder settings agreed / broken | `MatchHardPresetService` on the unanimity transition, and on a roster change that breaks one | `send_dm` via `notify_hard_preset_agreement` | Link only |
| Match preset set by staff | `MatchHardPresetService.set_override` | `send_dm` via `notify_hard_preset_override` | Link only |
| Scheduled/rescheduled — crew & watcher info | same call sites → `notify_match_crew(match, msg)`; the watcher copy is silent | `send_dm` (crew) / `send_dm_with_unwatch_button` (watchers) | Unwatch (watchers only) |
| Crew signup invitation (subscribers) | same call sites → `notify_tournament_subscribers_scheduled(match, msg, notified_ids)`, silent | `send_dm_with_crew_buttons` | Sign up as Commentator / Tracker |
| Stream candidate alert | `MatchService.create_match` (flagged) / `set_stream_candidate(flag=True)` → `notify_stream_candidate_subscribers` | `send_dm_with_crew_buttons` | Sign up as Commentator / Tracker |
| Crew approved — ack request | `CrewService.update_crew_approval(approved=True)` → `_request_crew_acknowledgment` | `send_dm_with_crew_acknowledgment_button` | Acknowledge |
| Match seated (checked in) | `MatchScheduleService.seat_match` → `notify_match_participants` | `send_dm` / `send_dm_with_unwatch_button` | Unwatch (watchers only) |
| Match started / finished | `MatchScheduleService.start_match` / `finish_match` → `notify_match_participants(silent=True, players=False)`: crew and watchers only, no ping | `send_dm` / `send_dm_with_unwatch_button` | Unwatch (watchers only) |
| Match confirmed | `MatchScheduleService.confirm_match` → `notify_match_participants(silent=True)`: everyone, no ping | `send_dm` / `send_dm_with_unwatch_button` | Unwatch (watchers only) |
| Match cancelled | `MatchService._cancel_match` (the `CancellationMixin` in `match_cancellation.py`) → `notify_match_cancelled(recipients, message, embed)` (recipients collected before the delete) | `send_dm` | none |
| Stage assigned / cleared | `MatchService.assign_stage` → `notify_stage_changed` (watcher copy silent) | `send_dm` | View your match (link) |
| Stage reminder | `stage_reminder` loop → `notify_stage_reminder` (watcher copy silent) | `send_dm` | View your match (link) |
| Crew withdrew (approved) | `CrewService.undo_crew_signup` → `_notify_crew_withdrawal` (tournament crew owners + approver) | `send_dm` | Fill the slot (link) |
| Crew approval withdrawn | `CrewService.update_crew_approval(approved=False)` → `_notify_crew_approval_withdrawn` | `send_dm` | none |
| Bracket matchup ready | `_bracket/notifications.py` → `_send_matchup_ready(...)` per entrant (opt-in only) | `send_dm` | Pick a time (link) |
| Seed generated | `MatchScheduleService.generate_seed` → inline `_send_seed_dms()` per opted-in player | `send_dm` | Open your seed (link, off-site); for a pasted seed (WWR, Z1R) Copy it from Wizzrobe → `player_match` |
| Volunteer shift assigned — ack request | `VolunteerScheduleService.assign(notify=True)` | `send_dm_with_volunteer_acknowledgment_button` | Acknowledge |
| Volunteer shift starting | `volunteer_reminder` loop (`_start_dms`), once per published assignment whose shift started in the last 30 minutes and that isn't checked in | `send_dm_with_volunteer_arrival_buttons` | I'm here / Running late / I can't make it, plus a My Shifts link |
| Volunteer running late / shift given back | `VolunteerArrivalService.report_late` / `VolunteerScheduleService.release` → `notify_coordinators`: coordinators + `StaffAlertService.volunteer_alert_recipients` (on duty, else all staff) | `send_dm` | Open the schedule |
| Volunteer shift reminder | `volunteer_reminder` loop, per un-reminded upcoming assignment | `send_dm_with_volunteer_acknowledgment_button` | Acknowledge |
| Admin direct message | `SendMessageDialog.send` — awaited inline, **not** queued | `send_dm` | none |

Note the asymmetry in lifecycle notifications: scheduling/rescheduling DMs are split (players get the Acknowledge DM via `notify_acknowledgment_request`; crew, watchers, and subscribers get informational variants), while seated/confirmed DMs go to everyone at once via `notify_match_participants` (confirmed silently) and started/finished skip the players and go out silent. A DM nobody has to act on goes out silent: watchers and tournament followers never get pinged for scheduling or stage news.

`send_dm(..., silent=True)` sends with Discord's `SUPPRESS_NOTIFICATIONS` flag (`@silent`): the DM lands but doesn't ping. The web-push copy carries `silent: true` and the Telegram copy `disable_notification`; email has no quiet mode.

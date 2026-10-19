# Notification settings

Each community decides, per message and per kind of recipient, whether a DM
pings, arrives silently, or isn't sent at all. Staff set it on
**Admin → Notifications** (System group, slug `notifications`). The list of
messages and their defaults is code; a community stores only what it changed.
No feature flag: every community has the page, and an untouched community sends
exactly the defaults.

| Piece | Where |
|---|---|
| Catalogue of every DM and its defaults | `application/services/notification_catalog.py` |
| Overrides, the lookup `send_dm` makes, staff writes | `application/services/notification_settings_service.py` (`NotificationSettingsService`) |
| Data | `NotificationSetting` (`models/notification_setting.py`, migration 117), `NotificationSettingRepository` |
| Enforcement | `send_dm(notice=...)` in `application/services/discord/discord_service.py` |
| Recipient tagging for staff alerts | `StaffAlertService.tagged_match_alert_recipients` / `tagged_volunteer_alert_recipients` |
| Page | `pages/admin_tabs/admin_notifications.py` |
| REST | `api/routers/notification_settings.py`, `api/schemas/notification_settings.py` |
| MCP | `list_notification_settings` (`mcpserver/tools/observability.py`), `set_notification_setting`, `reset_notification_settings` (`mcpserver/tools/admin_writes.py`) |
| A person's own choices | `application/services/notification_preference_service.py` (`NotificationPreferenceService`), `NotificationPreference` (`models/notification_preference.py`, migration 118), `NotificationPreferenceRepository` |
| Profile page | `pages/home_tabs/notification_preferences_section.py`, rendered by `player_edit_info.py` |
| Personal REST / MCP | `/api/notifications/messages*` in `api/routers/notifications.py`; `my_notification_settings`, `set_my_notification_preset`, `set_my_notification` |

## The catalogue

`notification_catalog.py` declares one `Notice` per message, 73 of them, in
eight `GROUPS`: Matches, Brackets, Reschedule requests, Crew, Volunteer shifts,
Community, Event check-in, Async qualifiers. Each carries a stable `key`
(`match_scheduled`), a `title` and `when` for the settings grid, a default mode
for every recipient kind it reaches, and an optional `who` note for a column
whose label would mislead (for a staff alert, "All staff" means the fallback
only). `CATALOG` maps key to `Notice`; `notices()` lists them in display order.

Keys are stored in `NotificationSetting.notice`. Renaming one silently drops
every override saved against it, so a new message gets a new key.

## Modes

`NotificationMode` has three values. `off` sends nothing on any channel: no
Discord DM, no web-push, email or Telegram copy. `silent` sends with Discord's
`@silent` and quiet device and Telegram copies (email can't be quiet). `ping` is
a normal message. The user's own Discord-DM switch still applies on top.

Some defaults are deliberately quiet: the matchup-ready and matchup-reopened DMs
are `silent` (the matchup also waits in the player's Pick-a-time list), started
and finished are `silent` for crew and watchers and `off` for players, who are at
the station.

## Recipients

`NotificationRecipient` is the grid's columns: `players`, `crew`, `watchers`,
`followers` (tournament subscribers), `volunteers`, `on_duty`, `admins`, `staff`,
`members`. A message only has the columns listed in its defaults.

Three of them split the staff-facing alerts (a player's comment, a reschedule
request, a volunteer running late or giving a shift back):

| Column | Who |
|---|---|
| `on_duty` ("On shift") | Whoever holds a published shift, right now, on a position with `staff_alerts` on (the Admin Desk) and can act on the alert. Wins over `admins` for someone who is both |
| `admins` | The tournament's admins (minus those who switched its alerts off), or the volunteer coordinators for a volunteer alert. Qualifier admins for the review-queue alert |
| `staff` ("All staff") | Every staff member, **only** when nobody is on shift |

`StaffAlertService` returns each recipient tagged with that column, and the
sender passes the tag on as `notice.to(tag)`.

## How a send is decided

A sender passes `notice=MATCH_SCHEDULED.to(PLAYERS)` (a `NoticeTarget`) to
`send_dm` or any `send_dm_with_*`. `send_dm` asks
`NotificationSettingsService.mode_for(target)` first: `off` returns
`(True, DM_NOTICE_OFF)` before anything is sent or mirrored, and `silent` or
`ping` overrides the caller's own `silent=` argument. Unless the community said
`off`, it then caps that with the recipient's own choice
(`NotificationPreferenceService.mode_for(discord_id, target)`, see
[Personal choices](#personal-choices)): the quieter of the two wins, and a
personal `off` returns `DM_NOTICE_OFF` the same way. `check_in_dm` makes the same
lookup up front so a turned-off check-in DM leaves no pending result on the desk.

`mode_for` never raises. With no tenant in scope, or when the read fails, it uses
the catalogue default rather than dropping or mis-pinging the message.

**Cache.** Overrides are read once per tenant and held in process for 30 seconds
(`_CACHE_SECONDS`), so a fan-out of DMs costs one query. A write through the
service drops that tenant's entry at once; the TTL only bounds a change made from
another process (the app runs [one worker](../scaling-roadmap.md)).

## Changing settings

Staff only, enforced in the service (`AuthService.is_staff`) for every surface:

- **Page.** One row per message, one column per recipient kind; tap a cell for
  Off, Silent, Ping or back to the default. It saves at once. Reset all to
  defaults sits in the Actions menu behind a confirmation. On a phone each row is
  a card of chips.
- **REST.** `GET /api/notification-settings`,
  `PUT /api/notification-settings/{notice}/{recipient}` (`{mode}`),
  `POST /api/notification-settings/reset`. See
  [rest-api.md](../reference/rest-api.md#notification-settings-apinotification-settings--notification_settingspy).
- **MCP.** `list_notification_settings`, `set_notification_setting`,
  `reset_notification_settings` (destructive). See [mcp-server.md](mcp-server.md).

Choosing the default deletes the override row. A real change audits
`notification_setting.changed` (`notice`, `title`, `recipient`, `old`, `new`); a
reset that removed anything audits `notification_settings.reset` (`count`). Neither
publishes an event-bus event: this is tenant-internal configuration.

## Personal choices

Every member also picks how much they hear on **Profile → Notifications → What
you hear about**. The community setting is a ceiling: a person can make any
message quieter, never louder, so a message the community sends silently shows
Ping locked and one it turned off shows "Off here".

A person starts from a `NotificationPreset`, then may override single messages:

| Preset | Pings | Silent | Off |
|---|---|---|---|
| `everything` (default) | everything | | |
| `needs_me` | targets in `notification_catalog._ACT` (acknowledge, pick a time, take a shift, staff alerts) plus the urgent ones | the rest | |
| `quiet` | targets in `notification_catalog._URGENT` (check-in, stage calls, race rooms, shift starts, cancellations) | the rest | watchers and followers |

`NotificationPreference` holds one row per (tenant, user): `preset` and
`overrides`, a JSON map of `"<notice>:<recipient>"` to a mode. Only real
differences are stored: choosing the preset's value, or `null`, removes the
key. No row means `everything`. Reset clears the overrides and keeps the preset.

The Profile page shows each preset's ping/silent/off counts against this
community's settings, then a **Customize** list grouped by what the person is
doing when the message arrives (`SECTIONS`: Your matches, Brackets and
requests, Async qualifiers, Matches you crew, Matches you watch, Tournaments and
events you follow, Volunteer shifts, Your membership, Staff alerts). Staff
alerts are listed only for someone who can see the admin side.

`staff_comment` → `players` stays in step with the older `User.dm_match_comments`
switch, which the comment sender still reads: setting it writes the flag, an
unset override reads it, and reset turns it back on.

`mode_for` never raises; with no tenant, no account, no row or a failed read it
answers `ping`, leaving the community setting in charge. Rows are cached per
(tenant, Discord id) for 30 seconds and dropped on the person's own write.
Changes aren't audited: they are a person's own settings, like their timezone.

REST (any member, own settings only): `GET /api/notifications/messages`,
`PUT /api/notifications/messages/preset` (`{preset}`),
`PUT /api/notifications/messages/{notice}/{recipient}` (`{mode}` or `null`),
`POST /api/notifications/messages/reset`. MCP: `my_notification_settings`,
`set_my_notification_preset`, `set_my_notification`.

### Opt-in messages

Some messages nobody gets until they turn them on: `notification_catalog._OPT_IN`
(`is_opt_in(target)`). The community column still holds the ceiling, so staff
can switch one off or make it silent for everybody, but `preset_mode` answers
`off` for an opt-in target in every preset and `mode_for` falls back to `off`
for one, so adding a message never changes what anyone already gets. The
Profile list captions them "Off unless you turn it on"; REST carries `opt_in`.

Senders live in `application/services/opt_in_notifications.py`, one coroutine
per message, handed to `discord_queue.enqueue`. Each starts from
`NotificationPreferenceService.opted_in_user_ids(target)` (one query), so a
message nobody chose loads nothing and sends nothing; `send_dm` still applies
the ceiling. Text is in `application/utils/discord_messages_opt_in.py`.

| Notice | To | Fired from |
|---|---|---|
| `opponent_acknowledged` | players | `acknowledge_match` |
| `station_assigned` | players | `assign_stations` |
| `bracket_result` | players | native bracket `_record_result` (not Challonge or start.gg) |
| `added_to_tournament` / `removed_from_tournament` | players | `record_enrolment_change(notify=True)`: roster dialog, admin user dialog; never a sync or scheduling auto-enrol |
| `player_withdrew` | admins (`StaffAlertService.tournament_alert_admins`) | self-withdraw, Challonge withdraw |
| `request_withdrawn` | the opponent (head-to-head only) | `MatchRescheduleService.withdraw` |
| `qualifier_opened` / `qualifier_closed` | members / players with a valid run | `sync_window_state` |
| `match_flagged`, `seed_failed`, `stream_offer` | staff alert columns | `flag_for_review`, `SeedRollService._fail`, `MatchStreamVolunteerService.volunteer` |
| `volunteer_arrived` | volunteer alert columns (silent) | `check_in_self` |

When adding a notification, decide whether it belongs in `_URGENT` (can't wait:
someone has to move now) or `_ACT` (asks the reader to do something); both feed
the presets.

## Adding a notification

1. Declare it in `notification_catalog.py` with `_notice(key, group, title, when,
   defaults, who)`: a new key, an existing group, and a default mode for every
   recipient kind the message goes to.
2. Pass `notice=YOUR_NOTICE.to(<recipient>)` on every `send_dm` call that sends
   it, one target per audience (players and watchers of the same event are two
   cells). For a staff alert, use the tag `StaffAlertService` returns.
3. Add the target to `_URGENT` or `_ACT` in the catalogue if it qualifies (see
   [Personal choices](#personal-choices)). A message nobody has had before goes
   in `_OPT_IN` with a sender in `opt_in_notifications.py`.
4. Nothing else: the grid, Profile list, REST and MCP read the catalogue. A DM sent without
   `notice=` can't be switched off or quietened by any community, so only a
   user-requested test message should skip it.

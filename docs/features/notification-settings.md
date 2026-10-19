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

## The catalogue

`notification_catalog.py` declares one `Notice` per message, 60 of them, in
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
`ping` overrides the caller's own `silent=` argument. `check_in_dm` makes the same
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

## Adding a notification

1. Declare it in `notification_catalog.py` with `_notice(key, group, title, when,
   defaults, who)`: a new key, an existing group, and a default mode for every
   recipient kind the message goes to.
2. Pass `notice=YOUR_NOTICE.to(<recipient>)` on every `send_dm` call that sends
   it, one target per audience (players and watchers of the same event are two
   cells). For a staff alert, use the tag `StaffAlertService` returns.
3. Nothing else: the grid, REST and MCP read the catalogue. A DM sent without
   `notice=` can't be switched off or quietened by any community, so only a
   user-requested test message should skip it.

# Email and Telegram notifications

Two extra places a person can get their notifications, beside Discord DMs and
[web push](web-push.md). Both are **copies of the Discord DM**: every DM that
goes through `DiscordService.send_dm` is mirrored to the recipient's confirmed
email and connected Telegram chat, so they reach people for exactly the
messages DMs do. Gated per community by `FeatureFlag.NOTIFICATION_CHANNELS`
(ships dark).

| Piece | Where |
|---|---|
| Settings, delivery, tests | `application/services/notification_channel_service.py` (`NotificationChannelService`) |
| DM fan-out | `_mirror_dm_to_channels` in `application/services/discord/discord_service.py` |
| Email transport | `application/utils/clients/sendgrid_client.py` (SendGrid v3 Mail Send over `httpx`) |
| Telegram transport | `application/utils/clients/telegram_client.py` (Bot API) |
| Telegram bot | `application/services/telegram_bot_worker.py` (long polls `getUpdates`, or takes `POST /telegram/webhook` when `TELEGRAM_WEBHOOK_SECRET` is set) |
| "Use my Discord email" | `application/services/discord/discord_email_service.py`, `pages/discord_email_oauth.py` |
| One-click unsubscribe | `pages/email_unsubscribe.py` (`/notifications/email/unsubscribe`) |
| Data | `User.email*`, `User.telegram_*`, `ContactVerification` |

## Email

An address is stored only once confirmed, two ways:

- **From Discord.** Sign-in keeps asking for `identify` only. "Use my Discord
  email" runs one extra authorization with `identify email` and accepts the
  address only when Discord's `verified` flag is true and the Discord id is the
  signed-in user's own. Discord's consent screen has no per-scope opt-out, which
  is why it is asked here rather than at sign-in. The callback
  (`/discord/email/callback`) must be registered in the Discord application.
- **By code.** A typed address gets a 6-digit code (HMAC-hashed with
  `STORAGE_SECRET`, 15-minute expiry, 5 tries). 5 codes per account per hour.

Every email carries `List-Unsubscribe` + `List-Unsubscribe-Post` (RFC 8058) and
a footer link to the same URL. `GET` shows a confirm button (link scanners
prefetch), `POST` turns email off. The token is signed and bound to the current
address, so an old email can't turn off a newer address.

## Telegram

"Connect" mints a one-time token (hashed, 15 minutes) and opens
`https://t.me/<bot>?start=<token>`. The bot worker sees `/start <token>` in a
private chat and binds that chat to the account; no phone number is involved. A
chat already bound to another account is refused. `/stop` turns Telegram off.
A 403 from Telegram (blocked bot, deleted chat) disconnects the chat. Link
buttons are only attached for `https` URLs; Telegram rejects anything else.

## Send a test

`send_test(user, channels)` sends a short test to any of Discord, devices,
email and Telegram. Discord is sent with `send_dm(..., mirror=False)` so a
Discord test doesn't also land everywhere else. 3 tests per account per 10
minutes.

## Flag and tenancy

The settings entry points carry `@requires_feature`. `mirror_dm` is a soft
integration point: it captures the sending community's id at enqueue time,
runs on the event dispatch worker inside that `tenant_scope`, clears the
long-lived worker's flag cache, and skips when the flag is off. The bot worker
has no community in scope; its tokens are only minted behind the flag. Email
and Telegram identity live on the global `User` row, like the rest of identity.

## Mock mode

Under `MOCK_DISCORD` both transports print (`[MOCK email]`, `[MOCK Telegram]`)
even when real keys are set, Connect binds a fake chat on the spot, and "Use my
Discord email" fills in `player@example.com`. The bot worker doesn't start.

## Setup

`SENDGRID_API_KEY`, `EMAIL_FROM_ADDRESS` (domain authenticated in SendGrid),
optional `EMAIL_FROM_NAME`; `TELEGRAM_BOT_TOKEN`; the Discord redirect above.
See [deployment.md](../deployment.md).

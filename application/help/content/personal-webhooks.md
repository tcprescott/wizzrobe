---
title: Your own webhooks
slug: personal-webhooks
icon: webhook
order: 72
feature: PERSONAL_WEBHOOKS
summary: Sending your matches, crew signups and shifts to a Discord channel or your own app, what gets sent, and why one might pause.
---

A webhook sends what happens here somewhere else as it happens: a Discord
channel you own, a stream overlay, a bot you wrote. You can have three in each
community.

## Adding one

:::snippet personal-webhooks
Open Profile and find the Webhooks card. Press **Add webhook**, give it a name,
and paste the URL.

For Discord, open the channel's settings, then Integrations → Webhooks → New
Webhook → Copy Webhook URL. Wizzrobe spots a Discord URL and sends a readable
Discord post. Any other https:// address gets signed JSON, and you see its
signing secret once, when you save.

Pick what to send under **Send these**. Each box is a whole area; **Choose single
events…** lets you pick one event at a time.
:::

## What gets sent

**Only things about me** is on to start with. It sends your own matches, the
matches you're crewing, your crew signups, your volunteer shifts and your
qualifier runs. Turn it off to also get other people's public matches and
bracket results, the same ones you can see on the schedule.

Each message is checked when it's sent, not when you set the webhook up. If you
leave the community, or a shift is taken off you, those messages stop. Staff
notes, comments, emergency contacts and anything else you can't see in
Wizzrobe are never included.

A match that's called off or deleted isn't sent: by the time the message would go
out, the match is gone.

## When one pauses

The dot beside each webhook is green when it's working, amber when the last try
failed, and grey when it's paused. Wizzrobe pauses a webhook after 20 failures in
a row, or straight away if Discord says the webhook was deleted. Fix the URL with
**Edit…**, then **Resume** it from the ⋮ menu.

**Send test** in the ⋮ menu sends a sample message right away and tells you if it
didn't arrive. You can send three tests every ten minutes.

## For developers

JSON messages are a POST with `Content-Type: application/json` and the same
headers staff webhooks use: `X-Wizzrobe-Event`, `X-Wizzrobe-Delivery`,
`X-Wizzrobe-Timestamp` and `X-Wizzrobe-Signature`. The signature is
`sha256=` followed by the HMAC-SHA256 of `{timestamp}.{body}` with your secret.
The body has `event_type`, `occurred_at`, `community`, `about_me`, `link` and
`data`. Times are UTC.

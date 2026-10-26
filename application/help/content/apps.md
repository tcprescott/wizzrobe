---
title: Apps that sign in with Wizzrobe
slug: apps
icon: apps
order: 74
feature: THIRD_PARTY_APPS
summary: Letting an outside app use your account, what it can and can't do, disconnecting it, and registering an app of your own.
---

Some tools people build for the community, like stream overlays, schedule
bots and desktop trackers, can sign you in with Wizzrobe. When they do, you
pick what they're allowed to do, and you can take it back at any time.

## Connecting an app

When an app sends you here, Wizzrobe shows one card: the app's name, who made
it, and the address approving sends you back to. Apps name themselves, so check
that address is where you started. Every app is marked **Unverified** for now.

Pick the community the app is for. It only works in that one; connect it again
for another. Then tick what it may do. What it can **see** starts ticked, and
what it can **do as you** (acknowledge matches, sign you up as crew, change your
availability) starts unticked, so approving without reading gives it reads only.

An app never gets more than you have. If you're Staff you'll also see the staff
items, tagged, and they're never ticked for you. Anything an app changes is
logged under your name.

## Disconnecting one

Apps you've connected sit in Profile under **API tokens & AI clients**, tagged
**App**, with what each may do. The bin button disconnects it at once.

Staff can block an app for the whole community under Admin → Settings →
Connected apps. That disconnects everyone here and stops anyone connecting it
again until they unblock it.

## Building your own

:::snippet developer-apps
Open Profile and find **Developer apps**. Press **New app** and answer **What
are you building?** first. A website with its own server gets a client secret,
shown once. A phone, desktop or browser-only app gets no secret and signs in
with PKCE.

Add every redirect URL your app uses: Wizzrobe only sends people back to an
exact match. Then tick what it may ask for. People still choose on the consent
card, so ask for what you need.

You can register 10 apps. You'll see how many people connected each one, never
who.
:::

The sign-in follows OAuth 2 with PKCE (S256). The server's details are at
`/.well-known/oauth-authorization-server/oauth`, and the API it unlocks is
documented at [/api/docs](/api/docs), with the scope each endpoint needs.

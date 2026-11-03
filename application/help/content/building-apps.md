---
title: Building an app on Wizzrobe
slug: building-apps
icon: code
order: 75
feature: THIRD_PARTY_APPS
summary: For developers. Registering an app, the OAuth 2 sign-in, what each scope reaches, token lifetimes, limits, errors, and a working example script.
---

This is the technical side of [Apps that sign in with Wizzrobe](/help/apps).
Wizzrobe is a standard OAuth 2 authorization server: authorization code flow,
PKCE required, no client-credentials or password grants. A token your app gets
works in the one community the person picked, and does only what they ticked
and their own roles allow.

## Register the app

Open Profile, expand **Developer apps** and press **New app**. Under **What are
you building?**:

| Pick | When | You get |
|---|---|---|
| **A website with its own server** | your code that holds the token runs on a server | a client id and a client secret, shown once |
| **An app on a phone or computer, or a browser-only site** | the code runs where users can read it | a client id only; PKCE proves it's you |

Add each redirect URL exactly as your app will send it. Wizzrobe compares them
character for character, including the port. A website uses https, or http to
`localhost` or `127.0.0.1` while you develop. A desktop or phone app may also
use its own scheme (`com.example.tracker:/callback`).

Under **It may ask for**, tick the most your app will ever request. Each person
still decides on the consent card.

## Sign someone in

1. Make a PKCE pair: a random verifier of 43 to 128 characters, and its
   challenge, the base64url SHA-256 of the verifier without padding.
2. Send the person's browser to `/oauth/authorize` with `response_type=code`,
   `client_id`, `redirect_uri`, `code_challenge`, `code_challenge_method=S256`,
   a `state` you'll check, and `scope` (space-separated). Leave `scope` out to
   ask for everything the app registered.
3. They sign in if they need to, pick a community and approve. Wizzrobe
   redirects to your URL with `code`, your `state` and `iss`. Check that `state`
   is yours and that `iss` is this site followed by `/oauth`. If they say no,
   you get `error=access_denied` instead.
4. Within five minutes, POST the code to `/oauth/token` as a form:
   `grant_type=authorization_code`, `code`, `redirect_uri`, `code_verifier`.
   A website sends its secret with HTTP Basic auth or as `client_secret`, not
   both. Other apps send `client_id` and no secret.

The answer has `access_token`, `refresh_token`, `expires_in`, `token_type`
(`Bearer`) and `scope`. That `scope` is what the person actually allowed, which
can be less than you asked for. Read it rather than assume.

The server's own description, with every endpoint and method it supports, is at
`/.well-known/oauth-authorization-server/oauth`.

## Call the API

Send `Authorization: Bearer <access token>` to the endpoints under `/api`.
Every endpoint in the [API reference](/api/docs) says which scope it needs, or
that apps can't call it. Tokens, webhooks, community settings and the platform
pages stay with personal tokens.

| Scope | Lets the app |
|---|---|
| `profile` | see the person's profile and availability |
| `profile:write` | update their profile, availability and notification settings |
| `schedule:read` | see tournaments, matches, brackets and the schedule |
| `play` | act as them as a player: request and acknowledge matches, sign up for tournaments |
| `crew` | sign them up or withdraw them as crew |
| `volunteer` | see and manage their volunteer shifts |
| `staff:read` | see staff-only information in this community |
| `staff:write` | make staff changes in this community |

A scope never adds to what the person can do. `staff:read` on a player's
connection reaches nothing, because the player can't see staff pages either.
Staff scopes are only offered to people who hold an admin-side role.

## Keep it working

An access token lasts an hour. Swap the refresh token for a new pair at
`/oauth/token` with `grant_type=refresh_token`, `refresh_token` and your
client details. You can pass a smaller `scope` to narrow the connection, never
a larger one.

**Store the new refresh token every time.** Each refresh token works once.
Sending one that has already been swapped ends the connection for good, because
it means two copies exist and one of them was stolen. A refresh token unused
for 30 days expires.

To disconnect, POST either token to `/oauth/revoke` with your client details.
The person can also disconnect your app from Profile, and staff can block it
for their whole community.

## When something goes wrong

| Answer | Means |
|---|---|
| `401` | the token expired, was revoked, or the app was disabled or blocked here. Refresh, or sign the person in again |
| `403` with `WWW-Authenticate: Bearer error="insufficient_scope"` | the person didn't allow the scope this endpoint needs; the header names it |
| `403` without that header | the scope is fine but the person's roles don't allow it |
| `404` on every call | this community has turned third-party apps off |
| `429` | too many requests; wait the `Retry-After` seconds |
| `invalid_grant` from `/oauth/token` | the code or refresh token is used, expired or not yours; sign in again |

## Limits

Each connection, and each IP address, gets 120 requests a minute. All of one
app's connections together get 600 a minute. You can register 10 apps, and 5
new ones a day.

A browser-only app can call `/api`, `/oauth/token` and `/oauth/revoke` from the
web address one of its redirect URLs is on: Wizzrobe sends the CORS headers for
that address and no other. Cookies are never sent or accepted that way, so the
token is the only credential.

## A working example

[wizzrobe_app_example.py](/static/developers/wizzrobe_app_example.py) signs
someone in from a terminal, calls `/api/users/me`, refreshes and disconnects.
It needs only Python 3.9 or later. Register an app on a phone or computer with
the redirect URL `http://127.0.0.1:8765/callback`, then run it with
`--site` and `--client-id`.

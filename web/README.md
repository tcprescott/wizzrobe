# Wizzrobe app (`web/`)

The new Wizzrobe frontend: one React + TypeScript app for phone and desktop,
replacing the NiceGUI pages at switch-over (spec: [docs/specs/mobile-app.md](../docs/specs/mobile-app.md)).
It talks to the backend only through the REST API and the `/oauth` server.

## Run it

```bash
# backend on :8000 with the dev seed (see docs/development.md), then:
cd web
npm install
npm run dev          # http://localhost:5173, proxies /api and /oauth to :8000
```

`WIZZROBE_API=http://host:port npm run dev` points the proxy elsewhere. Sign-in
goes through the backend's Discord (or mock) login, then straight back here: the
dev seed registers this app as the first-party client `wizzrobe-app` with
`http://localhost:5173/auth/callback`.

| Command | Does |
|---|---|
| `npm run typecheck` | `tsc` over everything |
| `npm test` | Vitest unit tests (`src/**/*.test.ts`) |
| `npm run build` | Typecheck, then a production build in `dist/` |
| `npm run gen:api` | Write `openapi.json` from the backend's routers, then `src/api/schema.d.ts` from it (needs `poetry install` at the repo root). Run it once after cloning and again after pulling a route change |
| `npm run a11y` | After `build`: axe + contrast + phone overflow on every screen, light and dark, with the API stubbed from `scripts/a11y/*.mjs` |

## The REST client

`openapi.json` is the backend's schema and `src/api/schema.d.ts` is generated
from it. Neither is committed: `npm run gen:api` builds both from the routers,
and CI does the same before it typechecks, so a route or response model change
that breaks the app fails the `web` job. Don't commit them (they're
gitignored); committed copies put every open PR into conflict on each merge.

```ts
import { api, unwrap } from '../api/client'
const me = await api.GET('/api/users/me').then(unwrap)
```

Every call carries the bearer token and the current community
(`X-Wizzrobe-Community`, set by the shell from `/t/<slug>/…`). A `401` refreshes
the session once and retries.

## Sign-in

`src/auth/session.ts`: authorization code + PKCE (S256) against `/oauth`, as a
public client (no secret). The server rotates refresh tokens and revokes the
grant if a spent one is replayed, so refreshes run under a cross-tab Web Lock.
Production registers the app once with `scripts/register_first_party_app.py`.

`VITE_API_BASE` (empty = same origin) and `VITE_OAUTH_CLIENT_ID` (default
`wizzrobe-app`) configure a build.

## Design system

The Studio tokens from `static/css/styles.css` live in `src/styles/tokens.css`,
light under `:root` and dark under `:root[data-theme='dark']`.
[docs/style-guide.md](../docs/style-guide.md) applies here as it does to the old
pages: colour from tokens only, the accent means "act here" or "you are here",
status is a tint, every control has a name, one main button plus an Actions
menu per toolbar, one ⋮ per row, destructive last.

Base components are in `src/components/` (Radix headless parts styled with the
tokens): `Button`, `IconButton` (label required), `Chip`, `CountBadge`,
`Panel`, `PageHeader`, `Banner`, `EmptyState`, `Spinner`, `RowMenu`,
`ActionsMenu`, `Menu`, `Dialog`, `FormDialog`, `Tooltip`. `/kit` shows each
of them in every state and is part of the a11y scan.

Icons are Material Symbols SVGs, bundled one by one: add a name to
`src/components/icons.ts` to use a new one.

## The shell

Shell A from the spec: Home, Schedule, Event, Work, Me as bottom tabs below
768px and a left rail above it. Work appears only for someone holding a role in
the community. Routes keep the old app's `/t/<slug>/…` and
`/t/<slug>/e/<event>/…` shapes so links in DMs already sent keep working.

## Store apps and the service worker

`ios/` and `android/` are the Capacitor shells for the App Store and Play Store
builds; `sw/sw.js` is the website's service worker (offline app files and web
push). Native push, sign-in through the system browser, app links and the build
steps are in [docs/features/native-apps.md](../docs/features/native-apps.md).
Code that only runs in the store apps lives in `src/native/` and checks
`isNative()` from `src/lib/platform.ts` first.

## Accessibility checks

`npm run a11y` reads every file in `scripts/a11y/`. Each one covers a screen
and exports `targets` (the pages and states to check) and `fixtures` (the stub
API response for each path those pages call). A new screen gets a new file
rather than an edit to a shared list, so parallel PRs don't collide. A target
name or fixture path defined in two files stops the run.

# Wizzrobe app (`web/`)

The new Wizzrobe frontend: one React + TypeScript app for phone and desktop,
replacing the NiceGUI pages at switch-over (spec: [docs/specs/mobile-app.md](../docs/specs/mobile-app.md)).
It talks to the backend only through the REST API and the `/oauth` server.

## Run it

```bash
# backend on :8080 with the dev seed (see docs/development.md), then:
cd web
npm install
npm run dev          # http://localhost:5173, proxies /api and /oauth to :8080
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
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from `openapi.json` |
| `npm run a11y` | After `build`: axe + contrast + phone overflow on every screen, light and dark, with the API stubbed from `scripts/a11y-fixtures.json` |

## The REST client

`openapi.json` is the backend's schema, written by
`poetry run python scripts/export_openapi.py` (from the repo root), and
`src/api/schema.d.ts` is generated from it. CI regenerates both and fails if the
committed copies differ, so **change a route or response model → run both
commands and commit both files**.

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

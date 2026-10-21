# Frontend / UI Architecture Reference

_Method-level reference for the presentation layer: [`frontend.py`](../../frontend.py), [`pages/`](../../pages/), [`theme/`](../../theme/), and [`static/`](../../static/). Part of the [documentation index](../README.md)._

The UI is built entirely with NiceGUI (Quasar/Vue under the hood) mounted into the FastAPI app. Pages call the service layer for all writes; see [services.md](services.md) for the service APIs and [../architecture.md](../architecture.md) for the three-layer pattern. Login and route protection are covered in [authentication.md](authentication.md).

## NiceGUI integration (`frontend.py`)

[`frontend.py`](../../frontend.py) wires NiceGUI into FastAPI.

**Middleware.** At import time three middlewares are registered on the NiceGUI `app`: `AuthMiddleware`, `TenantMiddleware`, `TransportPrefixMiddleware`. Starlette runs the last-added outermost, so execution is session → transport-strip → tenant → auth: transport paths (`/t/<slug>/_nicegui`, `/_nicegui_ws`, `/static`, `/sw.js`) are un-prefixed first so NiceGUI's assets and socket.io resolve, then the tenant is resolved and the ASGI path rewritten before `AuthMiddleware` reads it. `TransportPrefixMiddleware` is pure-ASGI, so it also covers the websocket scope that `BaseHTTPMiddleware` skips.

**`@app.on_exception _handle_unhandled_ui_exception`** — the backstop for event handlers that miss the `ValueError`→`ui.notify` wrap. Logs with traceback (so it reaches Sentry) and shows a generic negative notify. Never raises itself. A `ui.timer` that outlived its page (`application/utils/timer_teardown.py:is_timer_teardown_race`) is logged at debug and dropped, since nobody can act on it. NiceGUI's own default handler (`log.exception` on the `nicegui` logger) runs alongside this one, so `frontend.init()` also installs a filter on that logger for the same race.

**`VersionedStaticFiles`** — a `StaticFiles` subclass that replaces any `Cache-Control`/`Pragma`/`Expires` on HTTP responses with the policy from `theme.assets.cache_control`: `no-store` when `ENVIRONMENT` is `development` (the default), otherwise `immutable` for a year on a stamped URL and `must-revalidate` on an unstamped one.

**`init(fastapi_app)`** — the single entry point, called from `main.py`:

1. `validate_security_config()` ([`application/utils/environment.py`](../../application/utils/environment.py)) — aborts startup if `STORAGE_SECRET` is empty, and in production also requires `DB_USERNAME` / `DB_PASSWORD`. Then `validate_session_storage()` refuses a configured-but-unreachable session store, and the startup log names which store holds `app.storage.user` (Redis via `NICEGUI_REDIS_URL`, else the local `.nicegui` file) and the resolved `PLATFORM_HOST`.
2. Mounts `/static` → `VersionedStaticFiles(directory="static")`, whose `Cache-Control` comes from `theme.assets.cache_control`: `no-store` in development, and in production `immutable` for a stamped URL (`?v=<mtime>`) or `must-revalidate` for an unstamped one. See [Static assets & styling](#static-assets--styling-static).
3. `_register_root_routes(fastapi_app)` — the routes that cannot live under `/static`: **`/brand-logo/<tenant_id>/<sha256>.webp`** or **`.svg`** (a community's uploaded logo, from the database; see [`TenantLogoService`](services.md#tenant_logo_servicepy--tenantlogoservice)), **`/robots.txt`** (a blanket `Disallow: /` — signed-out surfaces are shareable by link, not published to search engines) and **`/sw.js`** (a worker served from `/static/` could only control `/static/*` clients, so root scope is required for PWA install).
4. Registers pages in source order: `auth.create()` (login/logout/OAuth, or the mock-Discord user picker — see [../features/discord.md](../features/discord.md)), `challonge_oauth.create()`, `twitch_oauth.create()`, `racetime_oauth.create()`, `startgg_oauth.create()`, `mcp_consent.create()`, `register_link_handoff_pages()`, `admin.create()`, `home.create()`, `volunteer.create()`, `equipment_labels.create()`, `equipment.create()`, `check_in.create()`, `brackets.create()`, `startgg_bracket.create()`, `room_seeds.create()`, `static_brackets.create()`, `platform.create()`, `qualifiers.create()`, `help_pages.create()` ([`pages/help.py`](../../pages/help.py)), `event_info.create()`, `community_events.create()`, `cat_facts.create()`, `changelog.create()`. Two orderings are load-bearing: the link-handoff routes come after the OAuth providers so their handoff configs are registered, and the static `/equipment/qr-labels` route comes before the dynamic `/equipment/{asset_id}`.
5. `ui.run_with(fastapi_app, …)` with the stripped `STORAGE_SECRET`, `viewport='…viewport-fit=cover'` (so the header/footer bleed into the iOS safe-area insets when installed), the PWA favicon, and `session_middleware_kwargs=_session_middleware_kwargs()` — the signed auth cookie is `Secure` in production, plain in local development, and lasts `SESSION_MAX_AGE_SECONDS` (90 days). Starlette re-signs it on every response, so that's 90 days since the last visit, not since sign-in. No `mount_path`, so `@ui.page` routes live at the site root.
6. `fastapi_app.add_middleware(PublicCacheMiddleware)` ([`middleware/public_cache.py`](../../middleware/public_cache.py)) — added *after* `ui.run_with`, so it sits outside the session middleware, which is the only place a `Set-Cookie` can be taken back off the publicly-cacheable spectator responses.
7. `register_error_handlers(fastapi_app)` ([`middleware/error_handlers.py`](../../middleware/error_handlers.py)) — installs the themed 40x/50x pages; see [Error pages](#error-pages-middlewareerror_handlerspy).

## Layout system (`theme/base.py`)

[`theme/base.py`](../../theme/base.py) defines **`BaseLayout`**, the shell used by every page.

Constructor: `BaseLayout(copyright_text=…, section=None, base_path=None, tabs=None, user=None, show_admin=None, show_volunteer=None, wordmark=None, chromeless=False, **_kwargs)`.

- `tabs` is a list of dicts `{'label', 'icon', 'content'}`, optionally `'slug'`, `'group'` and `'aliases'`. `content` is either a callable (sync or async, detected via `inspect.iscoroutinefunction`) or a tuple `(callable, args, kwargs)` for parameterized tabs — the admin page uses the tuple form to pass the board's `MatchBoardAccess` and the report query params. A `'group'` turns the drawer's flat tab list into labeled sections.
- `section` is the URL **slug** of the initially active tab (e.g. `reports`); it resolves to a label via the auto-derived slug map (`tab_slug()`, or an explicit `'slug'`). An unknown or missing slug falls back to the first tab.
- `'aliases'` is a tuple of **retired** slugs the tab absorbed. They resolve on read-in only (`_label_by_alias`, consulted *after* `_label_by_slug`, so a live tab's own slug can never be shadowed) and are never written back, so the URL normalizes to the canonical slug on the first tab switch. They exist for links already in the wild — a Discord DM outlives the app version that sent it. Home uses them; see [Home](#home--pageshomepy).
- `base_path` is the section URL base **including any `/t/<slug>` root_path** (e.g. `/t/foo/admin`); tab switches rewrite the URL to `{base_path}/{slug}`. `None` on tab-less pages, which never touch the URL.
- `show_admin` adds an "Admin" entry to the drawer's top menu (Home is always present). Only an explicit `True` (the admin page itself) is taken as given; otherwise `prepare()` resolves it via `AuthService.can_open_admin(user)`, which mirrors the admin page's tab gates so the link is offered exactly when `/admin` has a tab for the viewer. Pages no longer pass it.
- `show_volunteer` is **tri-state**. `None` — the default, and what every page but `/volunteer` uses — resolves it in `render()` via `AuthService.can_view_volunteer(user)`, so the link is never offered where the `VOLUNTEERS` flag or the role gate would reject it and dead-end on a 404/403. An explicit bool is honoured as-is, which keeps the synchronous error path working without a DB round-trip.
- The check-in desk is a Volunteer tab, so `prepare()` also resolves `_show_check_in`: a member for whom `AuthService.can_view_check_in_desk(user)` holds (`EVENT_CHECK_IN` live, Staff or `CHECK_IN_DESK`), whether or not an event is open, so a desk worker finds the desk before doors open. It also resolves `_show_proctor` from `AuthService.can_open_proctor_station(user)`, since a proctor keeps Proctor Station with `VOLUNTEERS` off. Any of the three puts **Volunteer** in the menu. False on the synchronous error path.
- The bottom nav renders only for two to four tabs: a hub with one tab (a desk-only volunteer's Volunteer) has nothing to switch to.
- `wordmark` is the header brand text; `None` resolves to the in-scope community's own name via `TenantService.current_community_name()`, falling back to `Wizzrobe`.
- `chromeless=True` is kiosk mode: the palette, fonts, head/body scripts and dark-mode preference are set up as usual, but no header, drawer or bottom nav is drawn — the page body gets the whole viewport. A floating `ui.page_sticky` dark-mode toggle stays in the top-right corner, because a room's lighting is not the viewer's system theme and there is no header left to hang the toggle on. Used by [the tournament-room seeds board](#the-tournament-room-seeds-board-pagesroom_seedspy), the one surface with no session to sign out of and nowhere else to navigate.
- Callers also pass `page_name=…`; `BaseLayout` absorbs it via `**_kwargs` and does not use it.

`async render()` drains any notice stashed before a redirect (`theme/notice.py`), then `await`s `prepare()` — the idempotent async half: it resolves the wordmark and Volunteer visibility, whether the viewer is a **member** of this community (`TenantService.is_member`, or a super-admin), the drawer's Feedback item (members only, `FEEDBACK` live) and Event Information item (`EVENT_INFO` live, signed in or not), and `await`s `_load_theme_colors()` (the tenant's brand palette via `TenantThemeService.get_current_theme()`), primes the viewer's saved table layouts (`TablePreferenceService().prime(user)`, see [Data tables](#data-tables)), then — inside that `table_prefs_scope` — calls the synchronous `render_chrome()`, draws the profile-reminder banner for a member (`theme/profile_nudge_banner.py`, skipped on chromeless pages; see [profile-reminders.md](../features/profile-reminders.md)), and `await`s `_render_tab_panels()` if tabs were supplied.

**Why the split.** `render_chrome()` is separate so non-async callers — notably the `on_page_exception` 50x path, which NiceGUI invokes synchronously — can draw the full themed shell without awaiting. That path skips the async palette load and falls back to the shipped defaults. The join door and every other error page are drawn synchronously too, but their callers are async: they `await layout.prepare()` (via `theme.error_page.prepared_layout(user)`) and hand the prepared layout in, so those pages carry the community's name, palette and drawer rather than Wizzrobe's defaults.

| Step | Method | Behavior |
|---|---|---|
| Dark mode | `render_chrome()` | `app.storage.user['dark_mode']` into `ui.dark_mode()` (`None` ⇒ follow the client's system theme), font preloads, `styles.css`, the `noindex` meta, the PWA manifest / theme-color metas / `/sw.js` registration, the Konami-code listener, `install_timezone_detection()` (the `wiz_tz` cookie), `install_connection_watch()`, and `js/table-columns.js` (drag-to-resize headers) |
| Palette | `render_chrome()` | `colors = self._theme_colors or DEFAULT_THEME` → `ui.colors(primary/secondary/accent=…)` (status colours stay fixed) plus an injected `<style>` re-pointing the `--wiz-*` brand vars. See [Per-tenant theme colours](#per-tenant-theme-colours) |
| Header | `_render_header()` | Burger, wordmark (the community name), `ui.space()` spacer, then either the user's name + Discord avatar (or an `account_circle` placeholder) + logout, or an icon-only-on-phones "Login with Discord" button that carries the current page as `/login?next=…` (see [Coming back to the page](authentication.md#coming-back-to-the-page)); finally the dark-mode toggle |
| Drawer | `_render_drawer()` | `ui.left_drawer` at `breakpoint=1023 show-if-above bordered width=224` (pinned ≥1024px, behind the burger below; 224px rather than Quasar's 300px default). Top menu (Home / Volunteer / Admin); one item per tab with a `.wiz-drawer-group` header per `'group'` — or, on a **tab-less** page viewed by a member, home's four sections (`HOME_SECTIONS`, linking `/home/<slug>`) so a bracket or an article is never a dead end for a player; then a `ui.space()` and, **only inside a community**, the **info card** (`.wiz-drawer-info`, an "Info & support" bordered card at the drawer's foot, set apart from the navigation) holding **Event Information** (`EVENT_INFO` live, not gated on a user), **Community Events** (members only, `COMMUNITY_EVENTS` live), **Help**, and **Feedback** (members only, `FEEDBACK` live — `FeedbackService.submit` refuses anyone else); and the GitHub link + copyright caption under it. Off any community (the bare host) the Help item would point at the page it sits on, so the section is omitted |
| Footer | `_render_footer()` | **Only** the mobile app-shell bottom nav (`.wiz-bottom-nav`), and only when **every** tab fits it (`len(tabs) <= BOTTOM_NAV_CAPACITY`, currently 4). Each tab takes an equal share of the bar and may shrink below its label (ellipsis), and Quasar's scroll arrows are hidden: Home's four natural widths summed to 373px in a 358px bar, which put **Profile** under a scroll arrow on every phone. Hidden ≥1024px, so desktop, tab-less pages, and hubs with more tabs than the bar holds render no footer. When it renders, it also stamps `body.wiz-has-bottom-nav`, which is what gives `--wiz-bottom-nav-h` its 72px (0 otherwise) |
| Tabs | `_render_tab_panels()` | Panel *containers* are all created up front; each tab's **content is built on first show** — see below |

**Lazy tab rendering.** Every `ui.tab_panel` container exists from page load (so panel switching, deep-linked sections, and swipe all work), but a tab's content is built the first time it is shown, by `_build_tab`. Unbuilt tabs sit in `_pending_tabs`; a label leaves it exactly once. A deferred build gets a spinner, is wrapped in `tenant_scope(self._tenant_id)` — the tenant captured at page-build time, because the deferred build runs in a websocket handler where the contextvar is unset — and falls back to an inline error label if the content raises. This is why the home hub is cheap: eager rendering spent roughly half of the page's render CPU on panels nobody looked at.

**Tab deep-linking.** Sections are addressed by **path segment** (`/admin/reports`), not a query param. Tab switching does not reload: `_switch_tab(label)` moves the tab-panels value, and `_handle_tab_change` → `_show_tab` moves the highlights and hands the new address to [`theme/url_state.py`](#addresses-titles-and-back), which **pushes** `{base_path}/{slug}` (`TAB_HISTORY`), so Back undoes a tab switch, swipes included (Thomas's call, 2026-10-21). The reverse direction works because each hub is registered under both `/base` and `/base/{section}` (via `protected_tab_page`, or three `ui.page()` calls for the public home page): the page function declares a `section: str = None` parameter that NiceGUI maps the path param onto. The home hub lives at `/` with sections at `/home/<slug>`. On load a named section normalizes to the tab's current slug (an alias like `/home/brackets` becomes `/home/event?view=brackets`); a bare hub path keeps its address until the first switch. Report filters (`report`, `start`, `end`, …) remain query params on top of the path.

### Addresses, titles and Back

[`theme/url_state.py`](../../theme/url_state.py): every view switch that redraws part of a page has its own address and browser-tab title, so Back undoes it, Forward redoes it, a reload stays put and a copied link opens the same view. One `UrlState` per client keeps the address, `document.title` and history in step; a `popstate` comes back to the server (`wiz_url_pop`), which moves the hub tab and hands each parameter whose value differs its `on_restore`.

- `bind_url(key, element, default=…, history='push'|'replace', label=…)` ties a `ui.tabs`/`ui.toggle`/`ui.select` whose value *is* the view to `?key=`, both ways. Call it after the element's own change handler.
- `url_param(key, element, default=…, value=…, label=…, on_restore=…)` is the general form, for a view that is not just the control's value (a day picked from chips, an open sheet). Read what the address asked for with `url_state().requested(key)` while building; call `param.set(value)` when the viewer changes it, and wrap a redraw that builds controls with parameters of their own in `async with param.changing(value):`. `TAIL` as the key carries the value as path segments under the tab (`/admin/settings/<pane>`).
- `'push'` for a real view change (tab, sub-view, day, sheet); `'replace'` for a filter, which the address keeps but Back skips.
- A parameter belongs to the hub tab its control was built in and lives as long as the control: switching tabs drops the old tab's parameters, and a view switched away drops its own. Parameters nothing owns (a DM's `?schedule=`) are kept on arrival and dropped at the first switch, so a reload doesn't reopen the DM's dialog forever.
- The title is `<view labels> — <tab> — <page title>`, e.g. `Timeline — My Schedule — SpeedGaming Live`; hub pages title themselves `Admin — <community>` so the parts read in order.
- `MatchSheet` records the open match as `?sheet=<id>` (`crew_sheet` for My Schedule's crew board, which shares a tab with the player board). Closing a sheet opened on the page is Back; closing one a link opened replaces the address.
- The `check_view_urls` hook (and `tests/theme/test_url_state.py`) fail a `ui.tabs`/`ui.toggle` in `pages/` or `theme/` that is neither bound nor marked `# url-state: exempt — <reason>` (a form input, not a view). `scripts/ui_history.js` walks Back/Forward/reload/deep-link scenarios in Chromium and WebKit, phone and desktop, in the CI `browser` job (`scripts/ui_history_ci.json`); add a scenario for a new *kind* of switcher.

**Anchors (`theme/anchors.py`, `static/js/anchors.js`).** Every `section_panel` and nested `page_header` gets an `id` from its title (`#crew-you-signed-up-for`); match rows and cards carry `data-wiz-row`, hour-grid shift blocks `data-wiz-shift`, and `#match-<id>` / `#shift-<id>` resolve to the first visible one. The script waits up to 20s for the target (most of a page arrives over the socket after the browser's own fragment jump has given up), scrolls it under the sticky header and flashes it (`.wiz-anchor-hit`).


### Per-tenant theme colours

The shipped "Phoenix" palette (gold/ember) is overridable per tenant on **Admin → Settings → General**. Storage, validation, presets and the contrast checks belong to [`TenantThemeService`](services.md#tenant_theme_servicepy--tenantthemeservice); this section covers only how the palette is *applied*.

`render_chrome()` applies it in two places, because `ui.colors()` alone recolours Quasar's palette and never reaches links, the active nav item, or the header's brand mark: `ui.colors(primary/secondary/accent=…)`, plus an injected `<style>` re-pointing `--wiz-gold-deep` / `--wiz-gold` / `--wiz-ember*` / `--wiz-header-bg` and the light-mode PWA `theme-color` meta. It is injected after `styles.css`, so it wins. `--accent` in `styles.css` is an alias of `--wiz-gold-deep` (light) / `--wiz-gold` (dark), so it follows the override too. The `header` colour no longer paints the bar: since the Studio restyle the header is a neutral surface and `--wiz-header-bg` fills only the small square before the wordmark (Appearance calls it **Brand mark**). Reads are fresh per page load, so a save shows on the next navigation (the Appearance form reloads the page).

`--wiz-*` is declared on `:root`; the bracket renderer's `--bracket-*` properties are declared on **`body`** instead, because `ui.colors()` writes the tenant `--q-primary` onto `body` — a `:root` declaration there would resolve `var(--q-primary)` against Quasar's stock blue.

`apply_brand_palette` declares every page's tab and home-screen icons through `theme/favicon.icon_links_html`, which the static bracket and Watch documents call too: the community's logo PNGs (plus the SVG, last, for browsers that draw one) when the palette carries a `logo`, else Wizzrobe's `icon-192.png` and `apple-touch-icon.png`. Pages never rely on NiceGUI's `/favicon.ico` link, because the production nginx answers that path itself with a 404. When the palette carries a `logo` URL ([`TenantLogoService`](services.md#tenant_logo_servicepy--tenantlogoservice)), `apply_brand_palette` also sets `--wiz-brand-logo`, and stamps `body.wiz-has-logo`, which grows the wordmark's `::before` to 28×28 and paints the logo in it instead of the colour. It stays a decorative pseudo-element, so the community name is still the header's only text. The upload control and its light/dark previews sit at the top of the Appearance section.

Custom colours apply only inside a tenant: the platform surfaces (community picker at `/`, `/platform`) and the synchronous error page keep the shipped palette.

### Accessibility (`theme/a11y.py`)

Target is WCAG 2.1 AA. The shared pieces that keep it:

| Piece | What it does |
|---|---|
| `ui.run_with(language='en-US')` | Sets `<html lang>` (screen-reader voice). |
| `page_header()` | Renders the title as `<h1>` (`<h2>` when nested in a Settings pane). |
| Skip link (`render_chrome`) | First tab stop, focuses `main.q-page`. |
| `install_tooltip_labels()` | `.tooltip(text)` on an icon-only `ui.button` also sets `aria-label`. A tooltip alone is not an accessible name. |
| `make_activatable(el, label=None, role=...)` | For a clickable card/icon: `tabindex=0`, a role, Enter/Space fire its `click`. |
| `label_field(caption, field)` | Turns a free-standing caption into `<label for>` the field's control. |
| `enable_mobile_grid(selectable=True)` | Names QTable's selection checkboxes. |

Vue slot templates are strings, so an icon-only `<q-btn>` there spells out `aria-label` (or `:aria-label`) itself; `tests/test_a11y_names.py` fails on one that doesn't. Contrast: badge tints are inset box-shadows, which axe ignores, so axe reports false failures on every badge. [`scripts/ui_a11y_scan.js`](../../scripts/ui_a11y_scan.js) (run by CI's `browser` job) turns axe's contrast rule off and runs a tint-aware check in its place; see [../style-guide.md](../style-guide.md#checking-your-change).

## Tenant-less chrome (`theme/chrome.py`)

Two surfaces run on the bare platform host with **no tenant in scope** and so cannot use `BaseLayout` — its drawer links into tenant routes and its palette is resolved from the in-scope tenant:

| Surface | Subtitle | Extra header content |
|---|---|---|
| `/platform` ([`pages/platform.py`](../../pages/platform.py)) | `Platform` | "Communities" link back to the picker at `/` |
| `/oauth/mcp/consent` ([`pages/mcp_consent.py`](../../pages/mcp_consent.py)) | `Authorize` | the signed-in user's name — the same identity the consent card names |

**`render_platform_chrome(subtitle=None, *, right=None)`** is the subset they share: font preloads, `styles.css`, the `noindex` meta, the shipped phoenix palette (never a tenant override — these surfaces belong to the platform, and an MCP grant is not scoped to one community), and the neutral `wiz-header` bar with the brand mark, the "Wizzrobe · *subtitle*" wordmark, an optional `right` callable, and the dark-mode toggle.

**`apply_brand_palette(colors)`** is the palette itself, factored out so all three branded surfaces apply it identically: `ui.colors(...)` plus the `--wiz-*` variable block that re-points the header, links, and section titles. `BaseLayout.render_chrome` passes the tenant's resolved palette, `render_platform_chrome` and the auth wait screens the shipped defaults or whatever tenant is in scope.

**`install_timezone_detection()`** writes the device's IANA zone to the `wiz_tz` cookie on every load (reloading once on a browser's first visit so the zone arrives with the HTTP request); `TenantMiddleware` reads it back. `BaseLayout.render_chrome` calls it. See [../timezone-handling.md](../timezone-handling.md).

**`dark_mode_button(dark)`** is shared with `BaseLayout._render_header` so the toggle behaves identically everywhere: `brightness_auto` until the user picks a side, then a sticky `app.storage.user['dark_mode']`. The consent screen's card uses the `.consent-*` family, deliberately sharing the error page's proportions so the two chrome-light screens read as one family.

## Error pages (`middleware/error_handlers.py`)

Branded 40x/50x pages, all rendered through the standard `BaseLayout` chrome by **`render_error_page(...)`** in [`theme/error_page.py`](../../theme/error_page.py). The renderer is **synchronous** because NiceGUI invokes the `on_page_exception` hook without awaiting it; the only async work (loading the user to file a report) happens lazily in a button click handler. Async callers pass a `layout=` they prepared (`prepared_layout(user)`) so the page carries the community's chrome and shows who is signed in, plus optional `actions=` buttons. The renderer sets the **response status** to the one on the card (`context.client.status_code`), so a not-found is an HTTP 404 and a forbidden an HTTP 403.

**One not-found page.** `render_not_found(user=…, message=…, actions=…)` is the async helper, and `NOT_FOUND_HEADLINE` / `NOT_FOUND_MESSAGE` are the words. Every "this isn't here" state uses it: an unknown route, a feature the community has off, a dead room token, a missing or unpublished bracket stage or tournament, a malformed or out-of-range id (`application.utils.route_params.parse_route_id`, which the public routes use instead of an `int` path type — that answered FastAPI's raw 422 JSON, or a Postgres range error as a 500), and a missing help or handbook article (with an "All help" / "All event information" action, and for a signed-out reader of the handbook, a Sign in action that says some pages need a role). None of them says more than an unknown route would. Two variants differ on purpose: an **unknown or inactive community slug** (`TenantMiddleware` marks the scope and lets the request fall through unrouted) reads "Community not found" with a **See all communities** button to the picker — it used to be a bare `Response` with no `Content-Type`, which Chrome downloaded — and a tenant page reached on the **bare host** reads "Pick a community" with the same path offered inside each one. The static bracket routes, which are not NiceGUI pages, answer with `render_not_found_document` — the same words in a small HTML page, `no-store`.

NiceGUI is mounted as a sub-application by `ui.run_with` (`app.mount('/', core.app)`) and ships its own "sad face" 404/500 pages, so `register_error_handlers()` installs the overrides on the NiceGUI `app`, not the host FastAPI app:

| Status | Mechanism | Behavior |
|---|---|---|
| **404** | `@app.exception_handler(404)` override | Keeps NiceGUI's guard that returns JSON for non-page endpoints (the REST API and raised `HTTPException`s); renders the themed not-found (or "Community not found" for an unknown slug) for real UI routes |
| **403** | `render_error_page(...)` call in [`middleware/auth.py`](../../middleware/auth.py) | The `@protected_page` denial path renders a themed "Not available to you" in the community's chrome, HTTP 403 |
| **500** | `app.on_page_exception` | NiceGUI calls this from `create_500_error_page` for exceptions raised inside `@ui.page` builders (where essentially all request handling runs) |

**Traceability.** Every unhandled 500 gets a `uuid4` `error_id` via `log_unhandled_error()`, which logs `UNHANDLED ERROR error_id=<uuid> path=<path>` with `exc_info` and tags `error_id` on the Sentry scope so logs and Sentry events correlate. The error page surfaces the UUID and, for logged-in users, a **"Report this error"** button that opens `FeedbackDialog` prefilled (category `BUG`, message containing `Error reference: <uuid>`).

**DEBUG vs production.** Outside production the 500 page also renders the full traceback; in production it shows only the UUID.

## Pages & routes

| Route | File | Auth |
|---|---|---|
| `/`, `/home`, `/home/{section}` | [`pages/home.py`](../../pages/home.py) | Bare `ui.page` (not `@protected_page`). Members only — `enforce_membership` renders the join page for everyone else, anonymous included. `/` = default section. On the bare platform host (no tenant) it renders the community picker instead |
| `/admin`, `/admin/{section}` | [`pages/admin.py`](../../pages/admin.py) | Login required (`@protected_tab_page`); content gated by role |
| `/volunteer`, `/volunteer/{section}` | [`pages/volunteer.py`](../../pages/volunteer.py) | Login required (`@protected_tab_page`, roles Volunteer/Proctor/Staff/Check-in Desk, `VOLUNTEERS` **or** `EVENT_CHECK_IN` flag); each tab keeps its own gate. `/volunteer/my-shifts` redirects to `/home/my-schedule` (`?release=` carried over) |
| `/equipment/qr-labels` | [`pages/equipment_labels.py`](../../pages/equipment_labels.py) | Staff / Equipment Manager (`EQUIPMENT` flag); printable QR-label sheet (`?ids=…&template=…&cols=…&paper=…&show=…`). `template` is `plain` (free grid, Letter/A4) or an Avery preset (`avery5160`/`5163`/`5162`) laid out on exact die-cut inches; `show` opts in owner/community/description lines. The toolbar names the host the QRs encode, and warns (`no-print`, above Print) when it is not the host being browsed — see [Encoded-host check](#encoded-host-check) . Registered **before** `/equipment/{asset_id}` so the static path wins |
| `/equipment/{asset_id}` | [`pages/equipment.py`](../../pages/equipment.py) | Login required (`@protected_page`, `EQUIPMENT` flag, path-param route); asset detail / QR target |
| `/checkin`, `/checkin/{event_id}` | [`pages/check_in.py`](../../pages/check_in.py) | Login required (`@protected_page`, roles Staff/Check-in Desk, `EVENT_CHECK_IN` flag). Redirects to the Volunteer hub's **Check-in Desk** tab (`?event=<id>` for the second), so old links and bookmarks still land. See [Check-in desk](#check-in-desk-volunteercheck-in-desk-pagescheck_inpy) |
| `/qualifiers`, `/qualifiers/{qualifier_id}` | [`pages/qualifiers.py`](../../pages/qualifiers.py) | Login required (`ASYNC_QUALIFIERS` flag); player async-qualifier pages (draw, timer, submit, leaderboard) |
| `/tournament/{tournament_id}/brackets`, `/brackets/{bracket_id}` | [`pages/brackets.py`](../../pages/brackets.py) | **No login** — `@public_page(feature=FeatureFlag.BRACKETS)`; read-only bracket index + per-stage detail |
| `/tournament/{tournament_id}/startgg` (`?phase=<start.gg phase id>`) | [`pages/startgg_bracket.py`](../../pages/startgg_bracket.py) | **No login** — `@public_page(feature=FeatureFlag.STARTGG)`; read-only mirror of a linked start.gg event: a tab per phase, pool cards for round robin, round columns for elimination. The viewer's own sets are marked and a ready one links to the My Schedule picker (`?startgg=<set id>`) |
| `/live/tournament/{tournament_id}/brackets`, `/live/brackets/{bracket_id}` | [`pages/static_brackets.py`](../../pages/static_brackets.py) | **No login, and not a NiceGUI page** — plain FastAPI routes returning server-rendered, heavily-cached HTML (no websocket) for spectators. Same tenant + `BRACKETS` gate and the same DRAFT/CANCELLED rule as the interactive twins above; see [brackets.md](../features/brackets.md#static-spectator-views--the-link-you-send-a-stream) |
| `/live` | [`pages/live_watch.py`](../../pages/live_watch.py) | **No login.** The Watch page, one page ([`theme/watch_live.py`](../../theme/watch_live.py); the static rendering draws the same layout) (sidebar **Watch**; it replaced the Event tab's On Air view, and `/home/on-air` redirects here): a Twitch player over the stage columns, a ⋮ **Open in Schedule** on each match `AuthService.schedule_board_scope` lets the viewer open, "Coming up on stream" for the next seven days, and each tournament's bracket/rules links with its status. Live NiceGUI page by default; `middleware/watch_mode.py` answers it with the cached static rendering ([`pages/static_watch.py`](../../pages/static_watch.py), also at `/live/static`) when Admin → Settings → Watch page sets it static or a rush of arrivals trips it ([`application/utils/live_audience.py`](../../application/utils/live_audience.py)). Content built once per change in [`pages/watch_data.py`](../../pages/watch_data.py). Player names always show. No feature flag |
| `/help`, `/help/{slug}` | [`pages/help.py`](../../pages/help.py) | **No login** — `@public_page`; the in-app help index (search over article cards) and article pages (sidebar nav + anchored headings). Articles are Markdown in `application/help/content/`, parsed to a closed block model and rendered with native elements — never `ui.markdown`. Reached from the **Help** drawer item, which is not gated on a signed-in user. See [help.md](../features/help.md) |
| `/event-info`, `/event-info/{slug}` | [`pages/event_info.py`](../../pages/event_info.py) | **No login** — `@public_page(feature=FeatureFlag.EVENT_INFO)`; one community's own event handbook (what's on, attending, who to ask, and role-gated floor procedure). Articles are Markdown in `application/event_info/content/<tenant-slug>/`, through the same closed block model as help. Article visibility additionally filters on `roles:`, so the page stays public while the room-staff procedure is not. Reached from the **Event Information** drawer item, above Help. See [event-information.md](../features/event-information.md) |
| `/room/{token}/seeds` | [`pages/room_seeds.py`](../../pages/room_seeds.py) | **No login** — `@public_page(telemetry_path='/room/seeds')`; authorizes on an unlisted `RoomToken` instead of a session, for a tournament-room machine nobody signs in on. Read-only board of matches whose seed is rolled and which nobody has started. An unknown, revoked, malformed or wrong-community token renders a plain 404. See [the tournament-room seeds board](#the-tournament-room-seeds-board-pagesroom_seedspy) |
| `/community-events`, `/community-events/{event_id}` | [`pages/community_events.py`](../../pages/community_events.py) | `@protected_page(feature=FeatureFlag.COMMUNITY_EVENTS)` — members only. `/community-events` redirects to `/home/community`, the Community view of Home's Event tab (`community_events_view`), which lists approved events still to come, grouped by local day, each with a **Remind me** / **Subscribed** toggle, then the viewer's own submissions with their status, and **Submit an event** (`theme/dialog/community_event_dialog.py`). The detail page is every DM's landing point; a pending/rejected event 404s for anyone but its host and staff. Descriptions render through the safe-markdown pipeline. The Event tab and the Community segment count events listed since the viewer last opened it, and those carry a New chip on that visit. See [community-events.md](../features/community-events.md) |
| `/messages` | [`pages/my_messages.py`](../../pages/my_messages.py) | `@protected_page` — members only, and only the viewer's own rows (`SentMessageService.list_mine` / `get_mine`). Messages sent to you: the viewer's DMs in this community, newest first, a **Show** select per catalogue group, 30 at a time with **Show older**. Tapping a row opens it in the match-sheet shell (side sheet on desktop, bottom sheet on phone) as it was sent, with its link button and an Off/Silent/Ping `.wiz-segmented` switch for that notice (`NotificationPreferenceService.set_mode`, capped by the community setting). `?message=<id>` opens one on arrival; it's where every catalogued DM's settings footnote lands. Reached from Profile → Notifications → Messages sent to you. See [notification-settings.md](../features/notification-settings.md#your-message-history) |
| `/changelog` | [`pages/changelog.py`](../../pages/changelog.py) | **No login** — `@public_page`; **What's new**, the weekly changelog entries from `application/changelog/changelog.md` and its per-PR `changes/` files, newest first, each anchored at its start date. Rendered with the help renderer, never `ui.markdown`. Reached from the **What's new** card at the top of `/help` (no drawer item). See [changelog.md](../features/changelog.md) |
| `/about`, `/about/privacy`, `/about/notices` | [`pages/about.py`](../../pages/about.py) | **No login** — `@public_page`. `/about` is a hub: what Wizzrobe is, GPL v3 + source link, the "Built with Claude" note, version and commit (`build_info`), and tiles to the other two plus What's new and Help. `/about/privacy` renders `application/about/privacy.md` through the help renderer (bump `PRIVACY_UPDATED` with any change). `/about/notices` lists third-party software from `application/about/notices.py`: main-group packages in `poetry.lock` (licence text read from each installed `.dist-info`), the editor bundle's `dist/THIRD_PARTY_LICENSES.txt`, the browser libraries NiceGUI serves (versions from their file banners) and our fonts; a row opens its licence. Reached from the **About Wizzrobe** card on `/help` (no drawer item). |
| `/cat-facts` | [`pages/cat_facts.py`](../../pages/cat_facts.py) | **No login** — `@public_page`; the easter egg's landing page: one featured fact with a shuffle button, then the whole bank grouped under the `CAT_FACT_SECTIONS` headings. Deliberately **absent from the drawer nav** — it is reached from `CatFactDialog`'s "See them all" button and from the facts in empty states, waiting panels and the 404 page |
| `/password-login` | [`pages/password_login.py`](../../pages/password_login.py) | Bare `ui.page` on every host; unlisted password sign-in (see [authentication.md](authentication.md#password-sign-in)) |
| `/platform` | [`pages/platform.py`](../../pages/platform.py) | Bare `ui.page`; super-admin only (themed 403 otherwise), **no tenant context** (a themed 404 when reached under `/t/<slug>`); six tabs (Communities, Users, Racetime bots, Feature groups, Logging, Service health; `?tab=communities|users|bots|features|logging|health` opens one); tenant CRUD, with each other tab in its own module — `platform_bots.py` (racetime bots), `platform_feature_groups.py` (tiers + a tenant's overrides), `platform_tenant_admins.py` (first staff member), `platform_users.py` (every account; set/remove passwords, New user, deactivate — see [authentication.md](authentication.md#password-sign-in)), `platform_logging.py` (runtime log levels, see [observability.md](../features/observability.md)), shared bits in `platform_shared.py`; the service-health board is `pages/service_health_view.py`, shared with Admin → Service Health (see [multitenancy.md](../features/multitenancy.md)) |
| `/login`, `/logout`, `/oauth/callback` | [`pages/auth.py`](../../pages/auth.py) | See [authentication.md](authentication.md). Replaced by the mock user picker under `MOCK_DISCORD` |
| `/oauth/start`, `/session/claim` | [`pages/auth.py`](../../pages/auth.py) | Custom-domain login handoff: `/oauth/start` begins Discord OAuth on the platform host for an allow-listed tenant domain, `/session/claim` sets the session back on the custom domain. `/oauth/start` falls back to `/login` unless the host OAuth handoff is enabled |
| `/oauth/discord/connect/callback` | [`pages/auth.py`](../../pages/auth.py) | Bot-authorization callback for Admin → Settings → Discord's "Connect Discord server" flow; runs on the platform host with the target tenant carried in the session |
| `/challonge/connect`, `/challonge/link`, `/challonge/oauth/callback`; `/twitch/link`, `/twitch/oauth/callback`; `/racetime/link`, `/racetime/oauth/callback`; `/startgg/link`, `/startgg/oauth/callback` | [`challonge_oauth.py`](../../pages/challonge_oauth.py), [`twitch_oauth.py`](../../pages/twitch_oauth.py), [`racetime_oauth.py`](../../pages/racetime_oauth.py) | Bare `ui.page` routes (they serve cross-host OAuth legs); one-time verified-identity linking on the global `User`. `/challonge/connect` is the staff service-account connection from Admin → Settings → Integrations. The Challonge callback lands on the bare platform host with no tenant, so initiation pins the community id beside the CSRF state and the callback completes inside `tenant_scope` (the service is feature-gated and its rows are tenant-scoped). The profile's Challonge **Link** button opens a sign-in-first dialog (`LinkSectionConfig.sign_in_first_url`) because a signed-out visitor to Challonge's authorize URL gets a bare 401 JSON `authentication required` page instead of a login form |
| `/oauth/link/start`, `/oauth/link/claim` | [`pages/_oauth_link.py`](../../pages/_oauth_link.py) | `register_link_handoff_pages()` — the provider-agnostic cross-host handoff for identity linking from a custom domain (start on the platform host, claim on the custom domain) |
| `/oauth/mcp/consent` | [`pages/mcp_consent.py`](../../pages/mcp_consent.py) | Bare `ui.page` that adds itself to `protected_routes`; the MCP OAuth approval screen (see below) |
| `/robots.txt`, `/sw.js`, `/brand-logo/…` | [`frontend.py`](../../frontend.py) | Plain FastAPI routes on the host app — see `_register_root_routes` above |

Each page module exposes a `create()` function that registers its routes; `frontend.init()` calls them.

### Home (`/`, `pages/home.py`)

`home(section=None, request=None, schedule=None, reschedule=None, match=None, hard=None, agree=None, focus=None)` — on the bare platform host (no tenant) it renders the community picker instead; otherwise it stashes the tenant + host mode onto the connection (so websocket handlers resolve them), sets the page title, resolves the current user, binds the display clock (`bind_display_timezone` — a bare `ui.page` gets nothing from `_tenant_page`, so it does this and the membership gate itself), and renders `BaseLayout`. `schedule`/`reschedule`/`match`/`hard`/`agree` are deep links forwarded to My Schedule's matches section; `focus` goes to the Profile tab, which scrolls to and outlines that card (the profile-reminder banner's target, see [profile-reminders.md](../features/profile-reminders.md)). If a `discord_id` is present but no matching active `User` exists, it shows an error, clears the session, and redirects to `/logout` after 2 seconds.

**Four tabs, on every community, whatever its feature flags.** The count used to run from five to ten, so no two communities' phone nav looked alike, and because the bottom bar carries `tabs[:4]` the player's own matches sat behind **More**. A feature that is off now removes a *view* or a *section*, never a tab.

| Tab | Content function | Holds | Absorbed slugs |
|---|---|---|---|
| Event | `home_tabs/event.py:event_tab` | Schedule board and Brackets, as a view switcher (`on-air` redirects to `/live`) | `schedule`, `brackets` |
| My Schedule | `home_tabs/my_schedule.py:my_schedule_tab` | List / Timeline switch; List is your matches, crew commitments, volunteer shifts, availability, equipment checkouts | `player`, `my-crew`, `availability`, `equipment` |
| Tournaments | `home_tabs/tournaments.py:tournaments_tab` | Signup cards; triforce text submission hangs off the matching card | `triforce-texts` |
| Profile | `home_tabs/player_edit_info.py:render_edit_info_tab` | Profile, notifications, linked accounts, API tokens | — |

Only the signed-in shape is listed because it is the only shape: `enforce_membership` renders the join page for anyone who is not a member, including anonymous visitors, before a single tab is built.

**Absorbed slugs.** A tab dict may carry `'aliases'`, a tuple of retired section slugs. `BaseLayout` resolves them on **read-in only** (`_label_by_alias`, consulted after `_label_by_slug`, so a live tab's own slug always wins), and the URL normalizes to the canonical slug on the first tab switch. They exist because Discord DMs outlive the app version that sent them and four of the six link targets in [../features/discord.md](../features/discord.md) address `/home/player`. `tests/test_home_nav.py` pins every one of them.

`show_admin` and `show_volunteer` are both left unset so `BaseLayout` resolves them.

## Home tabs (`pages/home_tabs/`)

| Module | Tab | Responsibility |
|---|---|---|
| [`event.py`](../../pages/home_tabs/event.py) | Event | The view switcher. `available_views` / `resolve_view` are pure (unit-tested); a view whose flag is off is absent, and a lone view renders no switcher. Styled `.wiz-segmented` and **never given `color=`** — on a `q-btn-toggle` that prop paints the *inactive* segments, so `color=primary` made all three gold and none of them look chosen |
| [`schedule.py`](../../pages/home_tabs/schedule.py) | Event → Schedule | Event schedule with crew signup, watch toggles |
| [`brackets.py`](../../pages/home_tabs/brackets.py) | Event → Brackets | Browse path into the public bracket pages: one card per tournament with stages. `group_by_tournament` is pure (unit-tested); the single read is `BracketService.list_all_brackets` |
| [`my_schedule.py`](../../pages/home_tabs/my_schedule.py) | My Schedule | A List / Timeline segmented switch (remembered in `app.storage.user['my_schedule_view']`; any deep-link param forces List for that visit). List stacks the sections below in commitment order, each a [section panel](#section-panels-themesectionpy) (Volunteer shifts only while `VOLUNTEERS` is live, Equipment only while `EQUIPMENT` is). Passes the `schedule`/`reschedule`/`match`/`hard` deep-link params through to the matches section |
| [`my_volunteer_shifts.py`](../../pages/home_tabs/my_volunteer_shifts.py) | My Schedule → Your volunteer shifts | Upcoming shifts by day: one button (I'm here / Acknowledge) and a ⋮ (Shift details, Undo check-in, Can't make it…). Not built when the viewer has no upcoming shift |
| [`my_timeline.py`](../../pages/home_tabs/my_timeline.py) | My Schedule → Timeline | One event day (06:00–06:00) of `MyTimelineService.items` in a single column on the volunteer grid's hour scale, clashes in lanes. A match or crew block links to the List view's `?match=`; a shift opens its details; an approved crew slot on a match with no stage is drawn dashed with a "No stage yet" tag; a community event (hosted, RSVP'd, or reminder-only drawn dashed, tagged Hosting/Going/Reminder, only with `COMMUNITY_EVENTS` live) opens `/community-events/{id}`. `MyTimelineService.unavailable` (the player's can't-play windows) draws as hatched bands behind the blocks; tapping one switches to List and scrolls to the availability editor |
| [`player.py`](../../pages/home_tabs/player.py) | My Schedule → Your matches | The player's own match list and match requests; bracket matches to schedule |
| [`my_crew.py`](../../pages/home_tabs/my_crew.py) | My Schedule → Crew | The commentator and tracker slots the viewer signed up for |
| [`availability.py`](../../pages/home_tabs/availability.py) | My Schedule → When you can't play | Self-service *blocked-out* windows (opt-out) with a live effective-availability graph |
| [`equipment.py`](../../pages/home_tabs/equipment.py) | Profile (top card) | `equipment_checkouts_section(user)` — the viewer's own checkouts, `EQUIPMENT` flag live; draws nothing when they hold nothing. Browsing the register is admin-side; borrowing is the QR scan into `/equipment/<id>` |
| [`tournaments.py`](../../pages/home_tabs/tournaments.py) | Tournaments | Signup cards per tournament, sectioned by what the reader can act on |
| [`triforce_texts.py`](../../pages/home_tabs/triforce_texts.py) | Tournaments → dialog | `open_triforce_dialog` submits/tracks triforce-screen text for one tournament; `supporting_tournament_ids` says which cards get the button |
| [`player_edit_info.py`](../../pages/home_tabs/player_edit_info.py) | Profile | Self-service profile and notification delivery; hosts the device-notification, identity-link, API-token, ADA-accommodation and feedback sections |

Several further modules render **inside** the Profile tab rather than as standalone tabs (all called from `player_edit_info.py`):

| Module | Renders | Responsibility |
|---|---|---|
| [`api_tokens_section.py`](../../pages/home_tabs/api_tokens_section.py) | "API tokens & AI clients" card | Create / list / revoke personal REST API tokens via `ApiTokenService`; the plaintext token is shown once at creation. Also lists connected MCP clients — a writing grant carries a `can make changes` badge, a read-only one carries none — and carries the copyable **MCP server URL** callout — an MCP connection is started from the client, so the URL is all this page can usefully offer |
| [`_link_section.py`](../../pages/home_tabs/_link_section.py) | the three identity cards | The shared engine (`render_connected_accounts_section`) behind the Challonge / Twitch / racetime.gg rows |
| [`challonge_link_section.py`](../../pages/home_tabs/challonge_link_section.py) | "Challonge" card | Verify-link / unlink via `ChallongeService`; hides itself when the integration isn't configured |
| [`twitch_link_section.py`](../../pages/home_tabs/twitch_link_section.py) | "Twitch" card | Verify-link / unlink via `TwitchService`; hides itself when unconfigured |
| [`racetime_link_section.py`](../../pages/home_tabs/racetime_link_section.py) | "racetime.gg" card | Verify-link / unlink via `RacetimeService`; hides itself when unconfigured |
| [`accommodation_section.py`](../../pages/home_tabs/accommodation_section.py) | "ADA accommodation" card | Request checkbox, optional details (autosaved), privacy disclaimer and staff status chip; rendered only with `ADA_ACCOMMODATIONS` live — see [../features/ada-accommodations.md](../features/ada-accommodations.md) |
| [`my_feedback_section.py`](../../pages/home_tabs/my_feedback_section.py) | "Your feedback" card | The submitter's own feedback and whether staff have read it — see [Profile](#profile-pageshome_tabsplayer_edit_infopy) |
| [`notification_preferences_section.py`](../../pages/home_tabs/notification_preferences_section.py) | Presets + Customize in "What you hear about" | A person's own Off / Silent / Ping per message via `NotificationPreferenceService` |
| [`web_push_section.py`](../../pages/home_tabs/web_push_section.py) | "Your devices" subsection of the Notifications card | Enable/disable web push on the current device via `WebPushService`. The buttons run [`static/js/web-push.js`](../../static/js/web-push.js) client-side via `js_handler` (permission prompts must stay inside the click gesture) and report back through `emitEvent` → `ui.on`. Hides itself when VAPID keys aren't configured ([../features/web-push.md](../features/web-push.md)) |

[`pages/mcp_consent.py`](../../pages/mcp_consent.py) registers `/oauth/mcp/consent`, the OAuth approval screen for MCP clients. It is a bespoke `@ui.page`, **not** `@protected_page`: every protected page is a tenant page and 404s without a tenant, but an MCP grant is platform-wide. It adds itself to `protected_routes` directly to get the sign-in redirect, and styles through [`render_platform_chrome`](#tenant-less-chrome-themechromepy). The grant list and its closing lock note live in one `@ui.refreshable` so ticking the "Let it make changes" box (unticked by default, and the only way write access is ever granted) redraws both together. Ticking it also reveals a warning banner and a field for the phrase "this is dangerous"; Approve stays disabled until it matches, and the service checks it again. See [features/mcp-server.md](../features/mcp-server.md).

### Section panels (`theme/section.py`)

A tab that stacks several independent blocks on one scroll — currently My
Schedule — builds each one with `section_panel(...)` rather than a
`section-title` heading over a `separator-spacing` rule.

```python
panel = await section_panel(
    'Crew you signed up for', icon='headset_mic',
    subtitle='Commentary and tracking slots you volunteered for.',
    help_topics=[('crew-status', 'Your crew slots'), 'crew-approval'],
    user=viewer,
)
with panel.aside:
    ...                                 # the section's own controls
with panel.body:
    ...                                 # the content
```

| Slot | Holds |
|---|---|
| `panel` | the `<section>` element itself (`.wiz-panel`) |
| `aside` | the header's right-hand control strip — a scope switch, a column-preferences gear |
| `body` | the content (`.wiz-panel__body`, one padding rule) |

`help_topics` takes a bare snippet name or `(name, label)`; they become the
panel's one "?" at the end of the title row, which lists them
([help icons](../features/help.md#help-icons)).
The stack itself is `ui.column().classes('page-container wiz-section-stack')` —
one gap between panels, no separators, since a panel already has an edge.

Two rules the classes exist to keep:

- **A section inside a stack does not carry `header-row`, `section-title`, or its
  own `page-container`.** That is the page-level heading pattern; four of them in
  a column reads as four competing page titles with ~4em of dead space above each
  one's content. `render_availability_editor(title='')` suppresses its heading for
  exactly this reason — the Volunteer hub still gives it a whole tab and keeps the
  default.
- **A nested block inside a panel body uses `.wiz-subcard` / `.wiz-subcard__title`**
  (the "waiting on you to pick a time" list, the change requests), which sit below
  the panel title in the hierarchy instead of competing with it.

`tests/theme/test_section_panels.py` pins both by source inspection.

### Schedule (`pages/home_tabs/schedule.py`)

The public event schedule with crew signup.

- No page title and no intro line (the Event tab's view switcher already names the view). The **Needs crew** switch and the help button (`schedule-columns`, `match-states`) go into the board's filter strip through `MatchTableView.toolbar_lead` / `toolbar_end`: one pair on the phone's Filters row, one inside the desktop filter card, kept in step. `.wiz-board-page` tightens the strip to the board. Every viewer is signed in, since home's membership gate runs first. Per-tournament notification preferences are managed on the Profile tab — see [../features/discord.md](../features/discord.md#tournament-notification-preferences).
- A `MatchTableView` with `admin_controls=False`, `table_key=TableKeys.HOME_SCHEDULE` (`home.schedule_v3`), `storage_key='home_schedule'` and `layout='player_run_sheet'` (`theme/tables/match_player_run_sheet.py`). The admin run sheet's line, with the viewer's own buttons left on it: `PLAYER_RUN_SHEET_COLUMNS` (Time, Match, Stage, Commentators, Trackers, Status, Watch; none sortable), groups Live now / Up next / Waiting for players / Finished. `arrange_player_run_sheet(rows, discord_id)` stamps per-viewer fields (`_roles` with each role's approved/need count, `mine`, `can_sign_up`, `can_withdraw`, `ack_idx`; `_ack` for a player who hasn't acknowledged), plus each role's `people` and `empty`, which the `body`/`item` slots turn into a per-role cell (top-aligned): the count, then the viewer's Withdraw/Sign up then Confirm in a `.wiz-crew-action` tray (a disabled Sign up plus `unavailable` when there's nothing to press, so the tray never empties), then the names (`match_run_sheet.CREW_NAMES`) or "No signups yet", Acknowledge in Status, and the bell. A tapped line opens `MatchSheet` with a spectator `MatchBoardAccess()` and `decorate=decorate_player_row`, so it shows details (checklist, seed, crew names) and no staff control.
- A match scheduled by a bracket shows a "<stage> · Game 2 of 3 · 1-0" link under the tournament name (`MatchDisplayService._bracket_ref`, rendered by `match_slots.py` / `match_grid.py`, handled by `_handle_open_bracket`). The series context comes off games the repository already prefetched, so it costs no extra query per row; the round *name* is deliberately absent, since naming a round needs the stage's whole graph. It emits `open_bracket` rather than carrying an `href`, so `ui.navigate.to` prepends the tenant `root_path`. Shared by every `MatchTableView`.
- `MatchTableView(show_accommodations=True)` (admin Schedule, Proctor Station only) marks players with an **Arranged** ADA accommodation: `_apply_accommodations` adds `ada`/`ada_note` to the player dicts via `AccommodationService.arranged_notes_for` (empty for any viewer but STAFF/PROCTOR), and the players cell's icon emits `show_accommodation` to open a staff-notes popup — see [../features/ada-accommodations.md](../features/ada-accommodations.md).
- `extra_slots` supply read-only state cells (per-state icon + timestamp) and a truncating seed-link cell.
- Read-only for editing: no `on_edit`, and neither an `id` nor an `edit` column. Players edit their own matches from My Schedule.
- Services: `MatchService.get_all_matches_for_schedule()`.

### Profile (`pages/home_tabs/player_edit_info.py`)

`render_edit_info_tab()` — self-service profile editing.

- Everything **autosaves**: the free-text fields debounce 0.8s (flushed on blur), and checkboxes/selects commit at once. A small status row under the identity header reads "Changes save automatically" → "Saving…" → "Saved", plus a bottom toast for when it has scrolled out of view. A `beforeunload` JavaScript guard prompts on unsaved edits; every `on_change` calls `mark_dirty()` and a successful save calls `mark_clean()`.
- **Identity header:** avatar, `preferred_name`, `@username`, a pronouns badge, and outline badges for the user's roles **in this tenant** (`AuthService.get_roles`, so no global `SUPER_ADMIN`).
- **Personal information:** Display name, Pronouns, and a **Timezone** select (saves via `TimezoneService.set_user_timezone`, then reloads so the new clock applies) — replaced by a caption naming the community's zone when Admin → Timezone pins one. Each carries its caption as its own Quasar label with `stack-label` plus a persistent `hint`, *not* a preceding `ui.label` — an empty `label` prop sets `q-field--labeled`, which suppresses the placeholder and blanks the input's accessible name.
- **Notifications:** two `.wiz-notify-group` blocks. **Where it goes** has one switch per channel: **Discord DMs** (`User.dm_notifications`, enforced inside `send_dm` after the web-push mirror, so it doesn't silence devices), then the device rows from [`web_push_section.py`](../../pages/home_tabs/web_push_section.py) (`render_device_rows`): a switch for the current browser, whose state comes from `wizWebPush.status()` on connect and whose `js_handler` subscribes or unsubscribes inside the click gesture, plus a row with Remove for every other subscribed device. Hidden without VAPID keys. A `banner` warns when Discord is off and there are no devices. **What you hear about** starts with [`notification_preferences_section.py`](../../pages/home_tabs/notification_preferences_section.py): three preset radio cards (Everything / Only what needs me / Quiet, each with ping/silent/off counts) and a **Customize** expansion listing every message by section, each with an Off / Silent / Ping chip menu (Ping locked where the community sends it quietly, "Off here" where it's off), saving at once through `NotificationPreferenceService` (see [notification-settings.md](../features/notification-settings.md#personal-choices)). Then **Tournaments you follow**: one level select per active tournament (`TournamentNotificationService`; Not following / Streamed matches / Streamed, plus possible streams / Every match), badged "Playing" where enrolled. An alert is a *follow* and does not require enrollment.
- Signing up for a tournament is **not** here. It used to be a checkbox card at the bottom of this tab; it now lives on [Tournaments](#tournaments-pageshome_tabstournamentspy), which can show the signup window, the entrants and the rules the checkbox could not.
- **Matcherino handle** — a card shown only while `FeatureFlag.PAYOUTS` is live; autosaved with the personal fields.
- **Your feedback** ([`my_feedback_section.py`](../../pages/home_tabs/my_feedback_section.py)) — the submitter's half of the feedback loop: what they sent through the sidebar form and whether staff have read it (*Read by staff* / *Not read yet*). Read-only by design; it is the acknowledgement, not a reply thread. Renders **nothing** when the person has sent none (an empty card is noise for the majority who never use the form) or when the community has `FeatureFlag.FEEDBACK` off — it catches the service's `FeatureDisabledError` rather than flag-checking itself.
- Saves go through `UserService.update_user_personal_info` and `TournamentNotificationService.upsert_preference`, with the user as their own audit actor.
- UX audit of this tab, with what was and wasn't changed: [../reviews/profile-page-ux-audit.md](../reviews/profile-page-ux-audit.md).

### Tournaments (`pages/home_tabs/tournaments.py`)

`tournaments_tab()` — where a player signs up to compete. Signed-in only; sits after My Schedule in the tab order.

- One card per active tournament, grouped into four sections and drawn in this order: **Open for signup**, **My tournaments**, **Opening soon**, **Signups closed**. A section with no cards is not drawn, so the usual view is the first two.
- Each card carries the name, a signup-state chip, the event-date range, the tournament's `description` rendered through the **safe-markdown** pipeline (`application.content.parse_blocks` → `theme.help.render.render_blocks` — the same closed-vocabulary renderer `/help` and `/event-info` use, never `ui.markdown`), a `rules_url` link, a collapsed **Randomizer notes** expansion (`randomizer_notes`, through the same safe-markdown renderer) when the tournament has any, an expansion listing the entrants, and one button.
- The button is **Sign up** or **Withdraw**, driven by `TournamentSignupCard.can_sign_up` / `can_withdraw` — never by re-reading the window on the page. Where neither applies the card says why in a caption rather than showing nothing: an enrolment whose window has since closed, or a Challonge bracket that has started (dropping out is then staff's call). A Challonge-linked card carries a **Challonge** tag; its **Sign up** opens `theme/dialog/challonge_signup_dialog.py` (bracket, the viewer's linked Challonge name, entrant count) before anything is sent, and a viewer who hasn't linked Challonge gets **Link Challonge to sign up** (`needs_challonge_link`), which runs `/challonge/link?next=tournaments` and returns to the tab.
- Everything the card claims comes from `TournamentService.list_signup_cards`, the same shape the REST payload and the MCP `list_tournament_signups` tool return, so the three surfaces cannot disagree about who may sign up.
- Both handlers run through `background_tasks.create(...)` with `context.client` captured at the call site, then `await cards.refresh()`.

### Player (`pages/home_tabs/player.py`)

`render_player_dashboard()` — "Your matches": the logged-in player's own matches, as one [section panel](#section-panels-themesectionpy) on the My Schedule stack.

- A `MatchTableView` (`admin_controls=False`, `player_discord_id` set). Columns: Tournament, Scheduled At, State, Players, Stage, Generated Seed, Stream, Change, Settings, Watch, using the same read-only state/seed `extra_slots` as the Schedule tab.
- The **Change** column is the player's own "ask staff to move or cancel this" control (`MatchRescheduleService`). It renders only for a player *in* that match, on a `Scheduled` row, and only when the service says a request is possible (`_can_reschedule`); a player who already asked sees an **Asked** chip instead. Opening it is `RescheduleRequestDialog`; the answers live in **Your change requests** below the board. See [match-participation.md](../features/match-participation.md#reschedule-requests).
- The **Stream** column is the player's own "offer this match for stream" toggle (`MatchStreamVolunteerService`). It renders only for a player *in* that match; everyone else sees a count chip. Advisory — it does not set `is_stream_candidate` — and the tooltip, the mobile card and the confirmation all say so. See [match-participation.md](../features/match-participation.md#stream-volunteering).
- The **Settings** column is the player's own private opt-in to the tournament's harder preset (`MatchHardPresetService`). It renders nothing unless the tournament has a `hard_preset` and this viewer plays in the match, and it never reports anybody else's answer — a player who is in and a player whose opponent is in see the same cell. Three renderings, in the order the template tests them: a `gavel` chip when staff overrode the match, the preset name once an agreed match has rolled it, and otherwise the toggle, which opens `HardPresetDialog` rather than flipping in place. `?hard=<match_id>` opens the same dialog for the DM's button. See [match-participation.md](../features/match-participation.md#harder-settings-opt-in).
- A **Request Match** button opens `UserMatchDialog` in create mode — but only when `TournamentService.list_player_requestable(viewer)` returns something. A bracket-run community has nothing to request, and a button whose only outcome is a dialog explaining that is a button that should not be there.
- **Render order is what the section owes the reader, not what it holds**: the "waiting on you to pick a time" cards, then the board, then the change requests. A matchup with no time on it is the only thing here waiting on *them*, and below the board and its filter card it was off-screen — which is where the "matchup ready" DM used to land.
- One `waiting_section` card, "Waiting on you to pick a time", lists both kinds of unbooked matchup (`_waiting_on_you`): Challonge ones (`ChallongeService.list_unscheduled_matches_for_user`, when Challonge is configured and `FeatureFlag.CHALLONGE` is live) with a Challonge tag and a **Schedule** button opening `ChallongeScheduleDialog`, and **native** ones (`BracketService.list_open_matches_for_user`, under `FeatureFlag.BRACKETS`) with a **Schedule game N** button opening [`BracketScheduleDialog`](../../theme/dialog/bracket_schedule_dialog.py). A Challonge row whose opponent isn't matched to an account has its button disabled and says why under the row. In a bracket-run tournament this is the player's *only* scheduling route.
- The count of bookable rows rides the **My Schedule** nav item (`'badge'` in the tab dict, drawn by `theme/nav_badges.py` in the drawer and the bottom bar); `waiting_section` calls `set_nav_count` on every refresh so booking a match takes it off the count. `?book=<challonge_match_id>` opens the Challonge picker (the ready-to-schedule DM's button); `?schedule=<matchup_id>` does the same for native matchups.

### Triforce Texts (`pages/home_tabs/triforce_texts.py`)

No longer a tab. `open_triforce_dialog(tournament, user)` is a dialog opened from the matching card on [Tournaments](#tournaments-pageshome_tabstournamentspy); `supporting_tournament_ids()` (from `TriforceTextService.list_supporting_tournaments`) says which cards get the button.

- The dialog gates on `tournament.is_active` + `SeedGenerationService.supports_triforce_texts` and on `AuthService.can_submit_triforce_text`; ineligible players see the tournament's `triforce_access_message` (rendered as plain text to avoid stored XSS) or a paid-option notice.
- Three `maxlength=19` line inputs feed `TriforceTextService.submit`; "Your Submissions" is a `@ui.refreshable` badged Pending/Approved/Rejected list from `list_user_submissions`.

### Equipment (`pages/home_tabs/equipment.py`)

`equipment_checkouts_section(user)` — "Equipment you have out", a full-width card above Profile's settings, drawn only while the viewer holds something (`/home/equipment` aliases to Profile). It used to be a panel on My Schedule. It used to be a whole tab with an inventory browser; browsing the register is now Admin → Equipment, and borrowing is scanning the QR into `/equipment/<id>`.

- One table of `EquipmentService.my_checkouts(user)`: #, Name, Checked out, and a **View** action navigating to `/equipment/{id}`. Empty state: "You have no equipment checked out."
- `.equipment-table` family with a bespoke `item` card, then `customize_table(..., key=TableKeys.EQUIPMENT_MINE)`; the gear sits in the panel's `aside`. The View button comes from one `_VIEW` spec rendered two ways — `_VIEW_ICON_BTN` for the desktop cell, `_VIEW_LABEL_BTN` (visible text) for the mobile card — and both carry `wiz-requires-socket` ([Offline honesty](#offline-honesty-themeconnectionpy)).

## Volunteer hub (`/volunteer`, `pages/volunteer.py`)

`volunteer_page(section=None, request=None, release=None, event=None)` is registered with `@protected_tab_page('/volunteer', roles=[Role.VOLUNTEER, Role.PROCTOR, Role.STAFF, Role.CHECK_IN_DESK], feature=(FeatureFlag.VOLUNTEERS, FeatureFlag.EVENT_CHECK_IN))` — a tuple `feature` opens the page when any one flag is live, so the hub 404s only where both are off. It resolves the user, loads roles via `AuthService.get_roles`, and builds tabs: the volunteer tabs when `AuthService.can_view_volunteer(user)`, the desk when `can_view_check_in_desk(user)` and an event is open (or the URL names the desk, or there is no other tab). No tab at all renders the themed 403. Then `BaseLayout` (`show_volunteer=can_view_volunteer`; `show_admin` resolved by the layout). The retired `my-shifts` slug redirects to `/home/my-schedule?shifts=1`, or `?release=<id>` when the reminder DM carried one.

| Tab | Shown to | Content function | Responsibility |
|---|---|---|---|
| My Availability | volunteer or staff | `volunteer_tabs/availability.py:availability_tab` | Volunteer availability windows, persisted via `VolunteerAvailabilityService.set_windows` |
| Full Schedule | volunteer, proctor or staff | `volunteer_tabs/full_schedule.py:full_schedule_tab` (drawn by [`theme/volunteer_grid.py`](../../theme/volunteer_grid.py), shared with Admin → Shift Board) | Read-only day grid: one column per position on an hour scale, names in each shift block (drafts hidden), the viewer's shifts outlined, a Now line with a time tag that keeps moving, and a dashed pointer line with its time (press and drag the hour column on touch; [`static/js/timeline.js`](../../static/js/timeline.js), also used by My Schedule → Timeline through `timeline_region`/`render_hour_column`/`render_column_markers`); scrolls sideways under a pinned hour column on a phone. **Columns** dialog hides and reorders roles per viewer (drag or ▲/▼), stored through `TablePreferenceService` under `TableKeys.VOLUNTEER_FULL_SCHEDULE` keyed by position id |
| Check-in Desk | staff or Check-in Desk, while an event is open | `check_in.py:check_in_desk_tab(user, event)` | The desk for `?event=<id>`, or the only open event, or a picker; see [Check-in desk](#check-in-desk-volunteercheck-in-desk-pagescheck_inpy) |
| Proctor Station | proctor or staff | `volunteer_tabs/proctor_station.py:proctor_station_tab(user)` | The on-site room board, a run sheet grouped by the next step (check in, start, finish) with the step's button on each line. Always `MatchBoardAccess(run=True)`: exactly what `can_run_match` admits them to |

My Availability reads the *signed-in* user's own data, so they are safe (and empty) for someone who volunteers for nothing; only the tab's role gate decides who can save availability. A volunteer's own shifts are on Home → My Schedule (`pages/home_tabs/my_volunteer_shifts.py`).

`proctor_station_tab()` is the admin Schedule's **run sheet** with the proctor's line, not a separate table: the same `MatchTableView(layout='run_sheet')`, `MatchSheet` and lifecycle dialogs (both boards build their callbacks from `theme/tables/match_lifecycle.py:MatchLifecycleHandlers`, so the two cannot drift). It passes `columns=PROCTOR_RUN_SHEET_COLUMNS` (Time, Match, Stage, Next step), `run_sheet_group=proctor_group` (Late, check in now → Ready to start → In play → Up next → Waiting for players → Done; earliest first within a group, Done newest first) and `proctor_run_sheet=True`, which registers the proctor variant of the slots: each name carries its station tag (`_seats`; the phone line uses `seat_line`), an ADA note names players with an arranged accommodation, each group header carries its count, and the line's **Next step** button (`next_step(row)`: Check in / Roll seed / Start / Finish, emitting the existing `seat`/`roll`/`start`/`finish` events; Roll seed sits between check-in and Start while `seed_pending(row)`, and a queued roll holds the button on its clock) renders only when the board can run matches. Under the names, `_seed_line` shows the rolled seed (link and code) or why there isn't one, from check-in on; on a phone it is a full-width strip below the card's two buttons. `MatchLifecycleHandlers.on_start` opens `StationAssignmentDialog(purpose='start')` instead of the plain confirm when the match has no stage and a player has no station. On a phone the line is a card holding two buttons (open, and the step), since the step can't sit inside the button that opens the match. `MatchSheet(show_crew=False)` drops the crew section and the crew item from **Ready to play?** — crew is not the proctor's to fix. `exclude_racetime=True` still drops racetime.gg matches. The table key is `volunteer.proctor_station_v2`; the tab slug stays `proctor-station`, so existing deep links keep working.

The shift helpers that used to make up **My Shifts** (`hours_message`, `shift_actions`, `shift_title`, `render_shift_brief`, `open_release_dialog`, the pure `release_warning(starts_at, lead_minutes)`) stay in `volunteer_tabs/my_shifts.py` and serve My Schedule's **Your volunteer shifts** section, which shows upcoming shifts as rows and folds ended ones into a **Finished shifts** expansion. `?release=<assignment id>` opens that shift's give-back dialog and `?shifts=1` scrolls to the section.

## Admin dashboard (`/admin`, `pages/admin.py`)

`admin_dashboard_page(...)` is registered with `@protected_tab_page('/admin')` — login is enforced by the middleware, with no route-level role. The function resolves the user, loads roles via `AuthService.get_roles` (Stream Manager, Volunteer Coordinator, Equipment Manager, Preset Manager, Sync Admin, Qualifier Admin), checks Tournament-Admin / Crew-Coordinator (`AuthService.tournament_scope`, which returns the tournament ids) and async-qualifier-admin membership **tenant-filtered** (those reverse relations hang off the global `User`, so unfiltered they would admit an admin of one community into every other), and folds it all into a frozen `AdminAccess`. It loads the live flag set and the setup checklist (`TenantSetupService().status()`) once for all tabs, then hands them to the module-level, pure `build_admin_tabs(access, live, reports_kwargs, setup_steps, base_path, …)`, which is what the ordering tests exercise. A user matching none of the roles gets a themed 403 through `render_error_page`. Proctors are **not** admitted — their race/schedule workflow lives on `/volunteer`.

`is_staff` throughout is `AuthService.is_staff` (staff-*equivalence*), so a platform super-admin has full authority in any tenant.

Tab visibility — a user sees the union of every row they match. "(any)" means membership in at least one tenant-scoped tournament / qualifier:

| Tab | Group | Visible to | Flag |
|---|---|---|---|
| Setup | Operations | Staff, Tournament Admin (any) — only while `TenantSetupService.is_ready` is false, and placed **first** so a new community lands on the checklist | — |
| Schedule | Operations | Staff, Tournament Admin (any), Crew Coordinator (any), Broadcast Tech (`STREAM_MANAGER`) | — |
| Users | Operations | Staff | — |
| Tournaments | Operations | Staff, Tournament Admin (any) | — |
| Seed Presets | Operations | Staff, Preset Manager | — |
| Stages | Operations | Staff, Broadcast Tech (`STREAM_MANAGER`) | — |
| Payouts | Operations | Staff, Tournament Admin (any) | `PAYOUTS` |
| Check-in | Operations | Staff | `EVENT_CHECK_IN` |
| Qualifiers | Online play | Staff, Qualifier Admin, qualifier admin (any) | `ASYNC_QUALIFIERS` |
| Racetime | Online play | Staff, Sync Admin | `RACETIME_ROOMS` |
| SpeedGaming | Online play | Staff, Sync Admin | `SPEEDGAMING_ETL` |
| Challonge | Online play | Staff | `CHALLONGE` |
| Brackets | Online play | Staff | `BRACKETS` |
| Triforce Texts | Community | Staff, Tournament Admin (any) | `TRIFORCE_TEXTS` |
| Volunteers | Community | Staff, Volunteer Coordinator | `VOLUNTEERS` |
| Volunteer Shifts | Community | Staff, Volunteer Coordinator | `VOLUNTEERS` |
| Shift Board | Community | Staff, Volunteer Coordinator | `VOLUNTEERS` |
| Equipment | Community | Staff, Equipment Manager | `EQUIPMENT` |
| Feedback | Community | Staff | `FEEDBACK` |
| Reports | System | Staff, Tournament Admin (any), Crew Coordinator (any); a Volunteer Coordinator otherwise gets Volunteer Coverage only (`only=VOLUNTEER_REPORTS`, no dashboard) | — (Volunteer Coordinator path: `VOLUNTEERS`) |
| Service Health | System | Staff | — |
| Notifications | System | Staff | — |
| Settings | System | Anyone with at least one [settings pane](#admin-settings-pagesadmin_tabssettings): Staff, Sync Admin (Discord Events only), Preset Manager (Randomizer Keys only) | — |

Tabs are appended in role order, then **stable-sorted by `_ADMIN_GROUP_ORDER`** (Operations → Online play → Community → System), which the drawer renders as labeled sections.

A **`MatchBoardAccess`** is passed into the Schedule tab — one field per `AuthService` gate (`edit`/`run`/`confirm`/`assign_stream`/`approve_crew`), resolved from the viewer's roles. See [Match board capabilities](#match-board-capabilities-themetablesmatch_accesspy). STREAM_MANAGER (Broadcast Tech) reaches the tab for `assign_stream` plus `run` on rows that have a stage (`run_on_stage_only`); a crew coordinator for exactly one (`approve_crew`), and their board is narrowed to the tournaments they run. Proctors do **not** reach this tab at all: they get their own [Proctor Station](#volunteer-hub-volunteer-pagesvolunteerpy) board on `/volunteer`, which shares the lifecycle handlers but not the frame.

**Deep-link query params.** Besides the `section` path segment, the page declares every tab's params in one signature and forwards each as a kwargs dict to the tab that owns it:

| Tab | Params |
|---|---|
| Reports | `report`, `start`, `end`, `bucket`, `tournament_id`, `user_id`, `stage_id`, `state`, `approval`, `action`, `focus`, `category`, `page` |
| Schedule | `match_id` — focuses the board on one match (suspending its State filter) with a chip back to the full board; `reschedule_request` — opens that request's decision dialog once the board renders (the staff reschedule DM's button) |
| Qualifiers | `qualifier`, `tab` (forwarded as `open_tab`) — the "runs are waiting" DM lands on that qualifier's queue |
| Volunteer Shifts | `day` — opens on that event day, ignored unless it is one of them |
| Shift Board | `day` — as Volunteer Shifts; the two share the remembered day |

Every report filter change navigates to a new `/admin/reports?report=...` URL, so report state is fully URL-driven and shareable. [`pages/admin_tabs/links.py`](../../pages/admin_tabs/links.py) is the only place these URLs are built — `admin_url(section, **params)` owns the slug constants and the empty-param/date normalisation, and `reports_url` is a thin wrapper over it. Params are named per destination: `state` is the Reports tab's filter and is deliberately not reused for the board's.

Triforce texts has no standalone route: player submission is a dialog off the tournament's card on the home **Tournaments** tab and admin moderation in the admin **Triforce Texts** tab — behavior doc: [../features/triforce-texts.md](../features/triforce-texts.md).

## Admin tabs (`pages/admin_tabs/`)

| Module | Tab(s) | Responsibility |
|---|---|---|
| [`admin_schedule.py`](../../pages/admin_tabs/admin_schedule.py) | Schedule | Match CRUD and the full lifecycle (seed / seat / start / finish / confirm) |
| [`admin_users.py`](../../pages/admin_tabs/admin_users.py) | Users | User management with role filtering; with `ADA_ACCOMMODATIONS` live, Members / ADA requests sub-tabs |
| [`admin_accommodations.py`](../../pages/admin_tabs/admin_accommodations.py) | (Users sub-tab) | STAFF queue of ADA accommodation requests: status + staff-notes dialog, Show withdrawn, CSV — see [../features/ada-accommodations.md](../features/ada-accommodations.md) |
| [`admin_setup.py`](../../pages/admin_tabs/admin_setup.py) | Setup | The new-community checklist (`theme/setup_checklist.py`), derived from `TenantSetupService`; disappears once the required steps are done |
| [`admin_settings.py`](../../pages/admin_tabs/admin_settings.py) | Tournaments, Stages | Tournament CRUD; stage CRUD |
| [`admin_payouts.py`](../../pages/admin_tabs/admin_payouts.py) | Payouts | Prize pool and placement splits per tournament via `PayoutService` (`PAYOUTS` flag); a TA is scoped to their own events by the service |
| [`admin_check_in.py`](../../pages/admin_tabs/admin_check_in.py) | Check-in | A **Matcherino login** card ([`theme/matcherino_login_card.py`](../../theme/matcherino_login_card.py): write-only refresh-token input that checks the token with Matcherino before saving, the saved account and when, Remove, a **DM when Matcherino refuses it** Staff picker, and a "Where do I get one?" expansion with the draggable **Copy Matcherino login** bookmarklet and the private-window steps), then the check-in events table (`TableKeys.ADMIN_CHECK_IN_EVENTS`): New/Edit via `open_event_dialog` (with a **Look up** button that confirms the Matcherino venue by title and lists its badge types, plus the event's comp rules: a comp-roles multi-select and a comp-volunteers switch naming the hour threshold, and once synced a lanyard dropdown per badge type), last-sync status, and a **Desk** shortcut per event (STAFF, `EVENT_CHECK_IN` flag) — see [../features/event-check-in.md](../features/event-check-in.md#admin) |
| [`triforce_texts.py`](../../pages/admin_tabs/triforce_texts.py) | Triforce Texts | Moderation queue for triforce-text submissions |
| [`admin_volunteer_roster.py`](../../pages/admin_tabs/admin_volunteer_roster.py) | Volunteers | Coordinator volunteer roster |
| [`admin_volunteers.py`](../../pages/admin_tabs/admin_volunteers.py) | Volunteer Shifts | Coordinator shift grid: positions, shifts, assignment & auto-scheduling |
| [`admin_shift_board.py`](../../pages/admin_tabs/admin_shift_board.py) | Shift Board | The Full Schedule day grid, editable: drafts, open slots and arrivals on each shift; a tap opens `VolunteerShiftSheet`, a tap on an empty hour adds a shift there. Columns stored under `TableKeys.ADMIN_SHIFT_BOARD` |
| [`reports/`](../../pages/admin_tabs/reports/__init__.py) | Reports | Read-only analytics — see [Reports subsystem](#reports-subsystem-pagesadmin_tabsreports) |
| [`admin_presets.py`](../../pages/admin_tabs/admin_presets.py) | Presets | Seed-rolling presets as a list grouped by randomizer, each line showing how many tournaments use it (`PresetService.usage`), with Test roll, Edit and a confirmed Delete in its menu; `PRESET_MANAGER`/STAFF. Import menu for built-in `presets/` files and a randomizer's catalogue. Test roll dialog (`PresetService.test_roll`) — see [seed-generation.md](seed-generation.md#presets-db-backed) |
| [`admin_randomizer_keys.py`](../../pages/admin_tabs/admin_randomizer_keys.py) | Settings → Integrations | This community's own randomizer API credentials (`PRESET_MANAGER`/STAFF). One card per declared credential with a **write-only** password input — the stored value is never rendered back, and a blank submit is rejected (clearing is an explicit button). Cards, not a `ui.table`, so the per-credential help text fits — see [seed-generation.md](seed-generation.md#per-tenant-credentials) |
| [`admin_racetime.py`](../../pages/admin_tabs/admin_racetime.py) | Racetime | Reusable race-room profile CRUD (`SYNC_ADMIN`/STAFF) via `RaceRoomProfileService`; the bots themselves are platform-managed on `/platform` |
| [`admin_speedgaming.py`](../../pages/admin_tabs/admin_speedgaming.py) | SpeedGaming | SG→app schedule ETL: event-link CRUD + "Sync now" (`SYNC_ADMIN`) — see [online-tournaments.md](../features/online-tournaments.md) |
| [`admin_discord_events.py`](../../pages/admin_tabs/admin_discord_events.py) | Settings → Discord | Per-tournament Discord Scheduled Events opt-in + "Sync now" (`SYNC_ADMIN`) |
| [`admin_qualifiers/`](../../pages/admin_tabs/admin_qualifiers/) | Qualifiers | Async-qualifier authoring (pools/permalinks), the reviewer queue, and a Live Races sub-tab (`QUALIFIER_ADMIN`) — see [online-tournaments.md](../features/online-tournaments.md#async-qualifiers). A package: `page` owns the state and the **per-tab loaders**, `shared` the columns and paging bounds, and one module per tab whose content is substantial: `pools` (seed authoring — URL validation, the per-line paste report, per-permalink edit/remove, and the Roll refusal shown before the button), `review_queue` (the reviewer's cards and the claim lock), `reviewers` (the roster), `live_races` (the one tab backed by its own service and an external system) |
| [`admin_service_health.py`](../../pages/admin_tabs/admin_service_health.py) | Service Health | Read-only subset of the platform health board for the tenant's own services (STAFF) |
| [`admin_notifications.py`](../../pages/admin_tabs/admin_notifications.py) | Notifications | Who gets each Discord DM and whether it pings (STAFF; slug `notifications`). One row per catalogued message, grouped as `GROUPS` orders them, one column per `NotificationRecipient`; tapping a cell offers Off, Silent and Ping (and back to the default), saves at once through `NotificationSettingsService.set_mode` and redraws only that cell. On a phone each row is a card of chips. Reset in the Actions menu, confirmed first — see [notification-settings.md](../features/notification-settings.md) |
| [`admin_features.py`](../../pages/admin_tabs/admin_features.py) | Settings → Features | Per-tenant feature-flag enable/disable (STAFF) — see [feature-flags.md](../features/feature-flags.md) |
| [`admin_theme.py`](../../pages/admin_tabs/admin_theme.py) | Settings → General | Per-tenant logo and brand colours (STAFF); see [per-tenant theme](#per-tenant-theme-colours) |
| [`admin_timezone.py`](../../pages/admin_tabs/admin_timezone.py) | Settings → General | Pin one zone for the community or follow each viewer's own (STAFF) via `TimezoneService`; saving reloads the page — see [../timezone-handling.md](../timezone-handling.md) |
| [`admin_profile_reminders.py`](../../pages/admin_tabs/admin_profile_reminders.py) | Settings → Members | Pick the profile items members are asked to complete, whether each can be dismissed, and a banner note (STAFF) via `ProfileNudgeService` — see [profile-reminders.md](../features/profile-reminders.md) |
| [`admin_challonge.py`](../../pages/admin_tabs/admin_challonge.py) | Settings → Integrations | The shared Challonge connection card (STAFF, `CHALLONGE` flag); per-tournament bracket actions live in the Tournaments row ⋮ |
| [`admin_brackets/`](../../pages/admin_tabs/admin_brackets/) | Brackets | Native bracket authoring, roster/seeding, start, results/overrides, complete & advance (STAFF, `BRACKETS` flag) — see [Admin brackets](#admin-brackets-pagesadmin_tabsadmin_brackets) |
| [`admin_discord_roles.py`](../../pages/admin_tabs/admin_discord_roles.py) | Settings → Discord | Map Discord roles (sign-in sync) and volunteer positions (assignment sync) to application roles |
| [`volunteer_role_mappings.py`](../../pages/admin_tabs/volunteer_role_mappings.py) | Settings → Discord | The volunteer-position section, rendered when `VOLUNTEERS` is live |
| [`admin_webhooks.py`](../../pages/admin_tabs/admin_webhooks.py) | Settings → Integrations | Staff-managed outbound webhooks as full-width rows (events grouped and wrapped): add/edit (URL, event multiselect, active), pause/resume, regenerate secret, recent deliveries, delete; payload reference under Actions — see [../features/webhooks.md](../features/webhooks.md) |
| [`admin_equipment.py`](../../pages/admin_tabs/admin_equipment.py) | Equipment | Asset CRUD, checkout/checkin, and QR-page links |
| [`admin_community_events.py`](../../pages/admin_tabs/admin_community_events.py) | Community Events | STAFF review queue for member-submitted events: filter by status (opens on **Waiting for review** when any are), **Review** opens a dialog to approve, reject or take down with a reason, or edit. `?community_event=<id>` (the staff DM's link) opens that event's dialog on arrival. Behind `FeatureFlag.COMMUNITY_EVENTS`, as is Settings → Community Events (the reminder lead) |
| [`admin_feedback.py`](../../pages/admin_tabs/admin_feedback.py) | Feedback | Review queue for user-submitted feedback; **reversible** (Reopen puts a row back in the queue). Behind `FeatureFlag.FEEDBACK`, as are the drawer's Feedback item and the profile's "Your feedback" card |
| [`settings/`](../../pages/admin_tabs/settings/__init__.py) | Settings | The settings area: a pane menu over the native panes (`event`, `venue`, `members`, `volunteers`) and the former tabs it embeds — see [Admin settings](#admin-settings-pagesadmin_tabssettings) |
| [`links.py`](../../pages/admin_tabs/links.py) | — | `admin_url(section, **params)`, the one builder for admin deep links |

### Admin schedule (`pages/admin_tabs/admin_schedule.py`)

`admin_schedule_page(access, match_id=None, tournament_ids=None, reschedule_request=None)` — the operational heart of the app: a `MatchTableView` (`table_key=TableKeys.ADMIN_SCHEDULE`, `searchable=True`, default state filter Scheduled + Checked In + Started + **Finished**) with `admin_controls=True` and `layout='run_sheet'`. Its `get_query` is the tenant-scoped `Match.filter(tenant_id=require_tenant_id())`, narrowed to `tournament_ids` when set; the rows themselves come from `MatchDisplayService`, so the same scope is also passed as `scope_tournament_ids`. **Run-sheet layout** (`theme/tables/match_run_sheet.py`): `RUN_SHEET_COLUMNS` (Time, Match, Stage, Status, none sortable), one line per match. `arrange_run_sheet` decorates and orders the rows into groups (Needs a decision, Live now, Up next, Waiting for players, Confirmed) and flags each group's first row (`_group_start`), which the custom `body` slot (desktop) and `item` slot (phone) draw as a header; `MatchTableView` re-runs it after every refresh and single-row update, since a state change moves a match between groups. A line emits `open_match`, which opens **`MatchSheet`** (`theme/dialog/match_sheet.py`): a side sheet on desktop, a bottom sheet on a phone (`:position` bound to `Quasar.Screen.lt.sm`), holding the next lifecycle step, a **Ready to play?** checklist (`readiness(row)`: players confirmed, stations, stage, seed, crew, each with its fix inline), crew approval split into *Waiting for you* (Approve buttons) and approved pills (⋯ menu: profile, Un-approve), and Edit match. Every control calls the existing `MatchLifecycleHandlers` / view crew handlers and is gated by `MatchBoardAccess`; the sheet repaints from the board's row via `sync` on `on_rows_changed`. (The Proctor Station uses the same run sheet with its own grouping and line; see the volunteer hub.) The lifecycle handlers themselves live in `theme/tables/match_lifecycle.py:MatchLifecycleHandlers` (shared with the volunteer Proctor Station board, so the two surfaces cannot diverge on lifecycle behaviour); the page keeps only `submit_admin_match`, since creating a match is a scheduling action rather than a lifecycle one. Every handler calls `MatchScheduleService` with the current user as `actor`, catches `PermissionError` (negative notify) and `ValueError` (warning notify) through `notify_error`, and refreshes just the affected row via `table_view.update_row_by_id`. Construction is two-phase — `MatchLifecycleHandlers(page_container, access=...)`, then `MatchTableView(..., access=..., **handlers.callbacks())`, then `handlers.table_view = table_view` — because the view needs the callbacks and the callbacks need the view. `callbacks()` returns one *group* per capability and **omits** the rest rather than passing `None`, since `MatchTableView` keys its event wiring and part of its slot registration off callback presence.

**Needs you** (`theme/match_inbox.py:MatchInbox`) sits beside the board on desktop and above it on a phone (`.schedule-layout`), with one group per kind of waiting work, each item carrying its action and each group shown only to whoever can act on it:

- **reschedule requests** (`access.edit` — `MatchRescheduleService.list_pending`, narrowed like the board): *Approve* calls `approve` directly for a move with a proposed time; *Decline*, a cancellation, or a request with no time opens `RescheduleDecisionDialog`;
- **results** (`access.confirm` — Finished rows, disputed first): *Confirm* calls `confirm_confirming` directly, *Review* / *Record winner* / *Change* go through the lifecycle dialogs;
- **running late** (`access.run` — overdue Scheduled rows): *Check in*.

It refreshes from `on_rows_changed` (results and late come from the rows on screen) and from its own `register_view` subscription (requests can sit on a match the board's filters hide). It renders into an anchor by a background task, which then opens the deep-linked `reschedule_request` dialog, if any. **New match** sits in the page header.

`tournament_ids` narrows the board for a TA or crew coordinator, whose authority is per tournament, and a chip says so; staff and the stream manager operate community-wide and get the whole board.

| Action | Trigger | Dialog | Service call |
|---|---|---|---|
| Create match | "Create Match" button (`access.edit`) | `AdminMatchDialog` | `MatchService.create_match` (via dialog) |
| Edit match | Edit pencil (`access.edit`) | `AdminMatchDialog` | `MatchService.update_match` (via dialog) |
| Generate seed | "Generate" button in Seed column | — | `MatchScheduleService.generate_seed` |
| Check In (seat) | "Check In" button in State column | `StationAssignmentDialog`, then confirm | `MatchScheduleService.seat_match` |
| Start | "Start" button | `ConfirmationDialog` | `MatchScheduleService.start_match` |
| Finish | "Finish" button | `MatchResultDialog` | `MatchScheduleService.finish_match` |
| Confirm | "Confirm" button | `ConfirmationDialog` | `MatchScheduleService.confirm_match` |
| Change recorded winner | Pencil beside Confirm / "Change Winner" on the card (`access.confirm`) | `MatchResultDialog(mode='edit')` | `MatchService.record_match_result` (via dialog) |
| Set stage | Stage-column select, in the row (`access.assign_stream`) | none — writes on change | `MatchService.assign_stage`, or `set_stream_candidate` for the `Candidate` pseudo-stage |
| Assign stations | Button next to players | `StationAssignmentDialog` | `MatchService.assign_stations` (via dialog) |

**The Stage cell is one select, written in place.** Its options are the tenant's stages plus a hardcoded **Candidate** pseudo-stage that stands for `Match.is_stream_candidate` — "this is going out, the stage is not decided yet". Choosing a stage assigns it; choosing Candidate clears the stage and sets the flag; clearing the select clears both. A match on a stage stops *showing* as a candidate (the stage is the more specific answer) but keeps the flag, because the crew-coverage reports count candidates. The options ride on each row (`stage_options`, attached by `MatchTableView._stage_options`) rather than being baked into the slot template, since the stages load after the templates are registered.

On-site-only controls are hidden for racetime.gg tournaments (`Tournament.is_racetime_enabled`): the display row carries an `is_racetime` flag, and the slot templates replace **Check In** with a muted "racetime.gg" note and drop **Assign stations** — the race room drives the lifecycle. The services enforce the same rule (`seat_match` / `assign_stations` raise `ValueError`), so the guard holds for API callers too.

Seed generation suppresses the notification when the service reports a generation "already in progress" (the row refresh clears the button spinner either way) and downgrades "already been generated" to a warning. The tab registers a `ui.on('selected_tab', …)` listener that refreshes on `'Schedule'`.

### Admin users (`pages/admin_tabs/admin_users.py`)

`admin_users_page(accommodations=False)` — membership and role management for this community. `pages/admin.py` passes `accommodations=FeatureFlag.ADA_ACCOMMODATIONS in live`; when true the page renders **Members** and **ADA requests (n)** sub-tabs (the second is `render_accommodation_panel` from `admin_accommodations.py`), the member table gains an **ADA** column and a **Needs ADA accommodation** filter. Both load the open-request user ids through `UserTableView`'s `before_refresh` / `decorate_row` hooks.

Clicking a member's username or display name opens the **member sheet** ([`member_sheet.py`](../../pages/admin_tabs/member_sheet.py), `UserTableView(name_event='member_details')`): a right side sheet on desktop, a bottom sheet on a phone, built from `MemberDetailsService.get`. It shows identity and role chips, how DMs reach them, notifications ("Quieter than the community" by default, or "All messages", each with its effective mode and reason), the last 10 sent messages with a link to Reports → Sent messages filtered to them, and their footprint here. The footer has Edit member plus a ⋮ with the audited Emergency contact… and Remove from community…. `/admin/members?user_id=<id>` opens it on load.

- A `@ui.refreshable` **join-request queue** above the table (`TenantMembershipService.list_pending`) with Approve / Decline per request; the requester's message renders as plain text.
- A `UserTableView` with the toolbar suppressed (`show_toolbar=False`); the page renders its own row with **Add Member** (brings an existing account into the community), **Add User** (opens `AdminUserDialog` to create a brand-new one) and a refresh button.
- Columns: Username, Display Name, Pronouns, Challonge (linked account, `-` when unlinked), Roles, and a row **Remove** action (remove from this community).
- A filter card with a multi-select over the `Role` enum (see [authentication.md § Roles](authentication.md#roles)) plus two pseudo-filters, `_tournament_admin` and `_crew_coordinator`. `get_query()` starts from this community's members (`User.filter(tenant_memberships__tenant_id=tid)`, system account excluded) and composes: roles via `roles__role__in` + `roles__tenant_id`, the pseudo-filters via `admin_tournaments__tenant_id` / `crew_coordinated_tournaments__tenant_id`, finished with `.distinct()`.
- Username clicks open `AdminUserDialog` for that user (wired inside `UserTableView`).

### Admin settings (`pages/admin_tabs/admin_settings.py`)

Two tab functions live in this module.

**`admin_tournaments_page()`** — `TournamentTableView` over the tenant-scoped `Tournament.filter(tenant_id=…)` (`table_key=TableKeys.ADMIN_TOURNAMENTS`, `searchable=True`, `can_delete` for staff). Columns come from `tournament_columns()`: Tournament (name link with the format on a second line and an Inactive chip), Signups (state chip plus when the window next moves), Seed, Match length (average, with the maximum underneath; sorts by the average), Players (link), and a ⋮ row menu. Format, Description, Active and Players per match are hidden by default and offered in the gear. The toolbar is **Add tournament** (staff only, opens `TournamentDialog`), search, an **Actions** menu (Export CSV), the gear and refresh. Listens for `selected_tab == 'Tournaments'` to refresh.

**`admin_stages_page()`** — a hand-rolled `ui.table` (not a shared table view). Rows from `StageService().get_all_stages()`: Name, Stream URL (as a link), Active (icon) — no ID column. **Add Stage** renders only when `AuthService.can_manage_stages(actor)`; the name link and the mobile-card edit button emit an `edit` event opening `StageEditDialog`. Mobile grid mode via `:grid="Quasar.Screen.lt.md"` with a custom `item` card slot.

### Admin triforce texts (`pages/admin_tabs/triforce_texts.py`)

`admin_triforce_texts_page()` — moderation queue for triforce-text submissions.

- Staff see all active tournaments; non-staff see only active tournaments they administer (`Tournament.filter(is_active=True, admins__id=actor.id, tenant_id=…)`); users administering none get an explanatory message instead of the queue.
- Filters: tournament select and status select (Pending / Approved / Rejected / All; default Pending).
- Each submission card shows the monospace text, the author, a status badge, **Approve** / **Reject** buttons (disabled when already in that state), and a delete button guarded by `ConfirmationDialog`. The list is a `@ui.refreshable` section re-rendered after every action.
- Services: `TriforceTextService.list_for_moderation`, `.moderate(text_id, approved, actor)`, `.delete(text_id, actor)`.

### Admin volunteer roster (`pages/admin_tabs/admin_volunteer_roster.py`)

`admin_volunteer_roster_page()` — the "Volunteers" tab (refuses unless `AuthService.can_manage_volunteers(actor)`).

- A `ui.table` (grid-card mode on small screens) of the assignable pool (`VolunteerPoolService.assignable_volunteers`): Name, **Hours**, Qualifications (position-name chips from `VolunteerQualificationService.list_all_qualifications`), declared Availability windows (display-local, from `VolunteerAvailabilityService.availability_map`), and a per-row manage button.
- **Hours** comes from `VolunteerHoursService.roster()`, keyed by user id, with a positive chip naming the highest comp tier cleared (`8h`, `12h`, …). The column sorts on the raw number, never the rendered string, so 10 does not fall between 1 and 2.
- The manage button emits `manage_volunteer`, opening `VolunteerProfileDialog` (active positions from `VolunteerPositionService.list_active`) to view availability and edit qualifications; the table reloads on submit and via a refresh button.
- An **Export data** button opens `VolunteerExportDialog`; the same button sits on the Volunteer Shifts tab, since the coordinator may start from either.

### Admin volunteers (`pages/admin_tabs/admin_volunteers.py`)

`admin_volunteers_page()` — the "Volunteer Shifts" tab, the coordinator scheduling grid (refuses unless `AuthService.can_manage_volunteers(actor)`).

- A `@ui.refreshable` **coverage strip** above the controls, from `VolunteerScheduleService.day_summary`: `14/20 slots filled` · `6 open` (or `Fully staffed`) · `3 awaiting acknowledgment` · `4 draft`, the last two hidden at zero. It answers the on-site question that previously took 5.8 screenfuls of adding up. The chips wrap rather than scroll — the page has no horizontal overflow and must keep it.
- The event-day select is **sticky**, stored under the tenant-namespaced session key `volunteers:day` (the match board's `_skey` pattern, so a coordinator working two communities does not carry day 3 of one into the other) and validated against the current event window by the module-level `stored_or_default_day`. No URL parameter: `BaseLayout` owns the path segment, and a query string would be a second source of truth.
- One `refresh_all()` refreshes the strip, banner, panel and grid together; every handler on the page changes at least one of them, and three separate `.refresh()` calls at a dozen sites is how a banner ends up claiming drafts that are gone.
- A controls card: **Auto-fill from availability** (`VolunteerAutoscheduleService.generate_draft`), **Manage positions** (an inline dialog wiring `VolunteerPositionDialog`), **Export data**, and a guarded **Reset all volunteer data** dialog (type-to-confirm → `VolunteerScheduleService.reset_all_shifts`).
- A `@ui.refreshable` **draft banner** below it, rendered only when the selected day holds drafts (`VolunteerScheduleService.count_drafts`): it names the count, says the volunteers have not been told, and carries the day's two decisions — **Publish draft** (`publish_draft`, behind a `ConfirmationDialog` because it sends DMs) and **Clear draft** (silent, so no confirmation).
- **Auto-fill** opens [`VolunteerAutofillDialog`](../../theme/dialog/volunteer_autofill_dialog.py) rather than running immediately: the hour ceiling and the "also use volunteers who have not marked this time" opt-in are the coordinator's call, and the dialog hands back a `DraftPolicy` for the page to run. Its module-level `build_policy` is where the defaults live, so they are unit-testable without a slot context.
- The run's output lands in a dismissible `@ui.refreshable` **result panel** (held in the page's `state['last_run']`, never a module global) listing each open slot with the sentence explaining why plus an **Assign** button into the existing picker, everyone placed outside their stated availability with a **Remove**, and any heavy loads. The shift card's `filled/needed` badge carries the same sentence as its tooltip, so the grid and the panel agree. The panel does not survive a reload — the audit row is the durable record.
- A `@ui.refreshable` `grid()` renders one card per active position, each with **Generate standard shifts** and **Add shift** (→ `VolunteerShiftDialog`) and a shift card per shift (filled/needed badge, edit/delete, and an **Assign** picker dialog). The position header carries its own aggregate `filled/needed` badge and open count, and on phones a **fully-staffed** position's shift cards are folded away behind a `lt-md` *Show N shift(s)* toggle — so folding hides finished work and never hidden work, and the header still states the position's state. The fold is the `.wiz-position-folded` class, whose `display: none` lives only inside the `max-width: 1023.98px` media query in `static/css/styles.css`: the cards render **once** and the desktop grid is untouched by construction. A per-width `ui.expansion` would double the DOM and register every row action twice. Measured at 390×844: a day with open slots is ~4,970 px (the audit's 4,891 plus the strip and the position briefs, which is what makes the first screenful answer the day), a fully-staffed day folds to ~2,120 px.
- The assign picker pulls the assignable pool (every VOLUNTEER or STAFF role holder in the tenant, `VolunteerPoolService.assignable_volunteers`) with a name search box and per-volunteer availability badges (`VolunteerAvailabilityService.availability_map` / `covers`; a person with no window covering the shift shows a grey **Not stated** badge, the same label the availability editor gives an undeclared span); assigning goes through `VolunteerScheduleService.assign` (surfacing returned warnings); assignment chips remove via `unassign`.
- The two write-only text fields are now readable from the grid: a position's `description` renders as a caption under its name, and a shift with `notes` carries a `sticky_note_2` icon whose tooltip is the notes text — so a coordinator can check what the volunteer will read.

### Volunteer data export (`theme/dialog/volunteer_export_dialog.py`)

`VolunteerExportDialog` — a start/end date pair (defaulting to `SystemConfigService.get_event_window()`, end inclusive) drives `VolunteerExportService.build`, and `bundle_to_zip_bytes` renders the bundle as `README.txt` plus one CSV per sheet (`volunteers`, `availability`, `positions`, `shifts`, `assignments`, `schedule`), downloaded as one ZIP via `ui.download`. The point is to hand the coordinator every input needed to draft a schedule in a spreadsheet — including each volunteer's qualifications and the *open* slots, which the normalized assignment table cannot show. `ValueError`/`PermissionError` surface through `notify_error`.

### Challonge connection (`pages/admin_tabs/admin_challonge.py`)

`admin_challonge_connection_page()` — the first card of Settings → Integrations when `CHALLONGE` is live (`/admin/challonge` is a legacy slug that opens that pane). A `@ui.refreshable` card from `ChallongeService.get_connection_status` (configured/connected state, username, scopes, token expiry, monthly request quota, a count of linked tournaments linking to Tournaments) with **Connect** / **Reconnect** (→ `/challonge/connect`, plus `?community=<subdomain>` when staff fill the optional **Add a community** field) and **Disconnect…** in its ⋮. A **Communities** chip row lists `ChallongeService.remembered_communities()`; a chip with no linked tournaments has an × for staff. Connect walks the whole list: each community gets its own Challonge consent (`community_id`), with the rest of the queue carried in the session through `/challonge/oauth/callback`, which returns to Settings → Integrations.

### Bracket links on Admin → Tournaments (`theme/tables/tournament.py`)

The **Bracket** column shows the live site a tournament follows (`bracket_sources.live_sources()`), Challonge's stage label and the last sync. Beside the stage it shows an "N unmatched" chip while linked-bracket entrants have no member (`bracket_sources.unmatched_entrant_counts`; the same chip rides on the menu's matching item). The ⋮ is two groups headed with `SlotHeader` (`theme/menu.py`): **Tournament** (Edit, Players, Round schedule, Open rules, and for an unlinked row Open bracket / Link a bracket…) and **<site> bracket**. On a linked row that second group carries, for staff and the tournament's admins (`can_sync`), **Sync now**, the one Challonge stage step `stage_actions_for` offers (confirmed, then `advance_stage`; refused with a reconnect notice without `tournaments:write`) **Match entrants to members…** (`ChallongeParticipantsDialog` / `StartggEntrantsDialog`) and **Open on <site>**. Linking and unlinking are staff-only on both sites (`can_link`): **Unlink <site> bracket…** (`bracket_sources.unlink`) on a linked row, and on an unlinked row **Link a bracket…** (`theme/dialog/link_bracket_dialog.py`), one field that reads the site off the pasted link (`bracket_sources.site_for_link`) and calls `bracket_sources.link`.

### Admin brackets (`pages/admin_tabs/admin_brackets/`)

`admin_brackets_page()` — the staff surface over `BracketService`, a package: `page.py` (selector, table, row dispatch), `stage_form.py` (create/edit/delete), `manage.py`, `draw_preview.py`, `round_editor.py`, `results.py` and `shared.py`. A tournament selector drives a `ui.table` of that tournament's stages (Stage, Name, Format, State) with a `body-cell-actions` slot + `enable_mobile_grid`. Because row-action handlers fire from detached client events that have lost the tenant contextvar, every scoped call is wrapped in `tenant_scope(tenant_id)` captured while the request context was live, and `context.client` is captured and re-entered for each dialog. All dialogs use the house chrome (`form_dialog` + `dialog_actions`) so their actions stay reachable on a phone. `wire_tab_refresh('Brackets', …)` refreshes the table on tab entry; handlers catch service `ValueError`/`PermissionError` and surface them via `notify_error`.

The selector lists only tournaments with no Challonge link (a native bracket cannot coexist with one, so the rest could only ever answer "Create bracket" with an error), sorts inactive ones last with an `(inactive)` suffix, and auto-selects when there is exactly one.

Each row shows **one primary button — the step that stage is actually up to** — plus its readiness under the state badge ("2 enrolled", "7/15 matches reported", "Won by Alice", "Abandoned"), with every other legal action in an overflow menu. Each entry is `v-if`-gated on the row's own state, so the table never offers a transition the service would refuse; `can_advance` checks every guard the advance itself enforces (a successor that exists, is DRAFT, is unseeded and carries a rule). A `Setup` column renders `config_summary`, so a stage's rules are readable without opening anything. The selected tournament is remembered in `app.storage.user`.

| Row action | Shown when | Dialog | Service calls |
|---|---|---|---|
| **Create stage** | always (toolbar) | name, format, stage order (defaulting to `max + 1`, with the 1-based label it will be shown under) + an options panel **bound to the format select**: default best-of everywhere; Swiss rounds on Swiss; groups on round robin; grand-final reset on double elim; scoring + tiebreaker order on the two points-ranked formats; and an advancement block only when `stage_order > 0` | `create_bracket` |
| **Edit stage** | DRAFT | the same form, prefilled — including the **format**, which a DRAFT stage can still change because it has no match graph. Config keys the form does not own (round chrome) are carried through untouched | `update_bracket` |
| **Cancel stage** | ACTIVE | `ConfirmationDialog` naming what survives (the played results) and what does not (any ranking, any champion, any advance out of it) | `cancel_stage` |
| **Delete stage** | DRAFT / CANCELLED | `ConfirmationDialog` | `delete_bracket` |
| **Entrants & seeding** (`tune`) | DRAFT / ACTIVE | roster / enroll / seed / start, the per-round best-of + scheduled-time editor, and — while DRAFT — the **draw preview**: the real bracket rendered from `preview_draw`, plus a seeding toolbar (Number 1..N, Reverse, Shuffle, Clear, From qualifier). **Import roster** creates a linked entrant per enrolled player; a per-entrant `link`/`link_off` button picks the user by preferred name (never a raw id); the roster filters and hides retired entrants; an entry can be un-enrolled while DRAFT and retired once running; **Start** confirms, naming the publish, the DM count and any unlinked entrants | `preview_draw`, `import_entrants_from_roster`, `add_entrant`, `set_entrant_user`, `enroll`, `unenroll`, `retire_entry`, `drop_entrant`, `set_seeds`, `set_round_metadata`, `start_bracket` |
| **Results** (`scoreboard`) | ACTIVE / COMPLETE / CANCELLED | the embedded bracket for elimination formats, **standings** for the points-ranked ones (with the advancement cut line, a tie warning and per-entry rank inputs feeding `tie_breaks`), and match lists where **every** row opens the shared `build_match_dialog` — which is what finally gives Swiss and round robin score and forfeit entry | `standings`, `complete_stage(tie_breaks)`, `report_result`, `override_result` |
| **Complete stage** (`flag`) | ACTIVE, non-elimination | quick confirm; the tie-resolving path is in Results next to the standings it rules on | `complete_stage` |
| **Advance into next stage** (`fast_forward`) | COMPLETE, successor DRAFT + unseeded + carrying a rule | `advancement_plan` renders `Seed N — Name (was #rank, Group G)` under both stage names; an already-seeded chain is shown as information rather than discovered as an error | `advancement_plan`, then `advance_stage` |
| **Open public bracket** | not DRAFT | — | navigates to `/brackets/{id}` |

Dialog chrome detail: [brackets.md → Admin dialogs](../features/brackets.md#presentation).

### The join page (`theme/join_page.py`)

What `enforce_membership` renders for anyone who is not a member of the
community, anonymous visitors included — so it is the whole first impression a
new attendee gets. It is **synchronous** (like `theme/error_page.py`) so the
middleware decorator can call it without restructuring; everything needing an
`await` is resolved first by `resolve_join_preview(tenant_id)`, which never
raises — a community whose bracket list or schedule read fails must still be able
to take a join request. The caller also hands in a prepared layout, so the door
wears the community's name, palette and public drawer (Event Information, Help)
rather than Wizzrobe's defaults.

**Signed out is not the same as not a member.** Home applies the membership gate
itself, so a member opening a DM's button on a phone with no session lands here
too. The signed-out copy therefore leads with "Already a member? Sign in and
you'll land right back on this page." and the **Sign in** button carries the page
(path and query) as `/login?next=…` — see
[Coming back to the page](authentication.md#coming-back-to-the-page). It used to
navigate to the relative `'login'`, which from `/home/<section>` resolved to
`/home/login`: the door again.

Beyond the Request access form it carries two previews with deliberately
different rules:

- **Watch live** — first on the card, for everyone: a primary button to the
  static `/live` Watch page, because most people reaching the door from a tweet,
  the stream or a QR code came to watch and need no account.
- **Brackets** — one button per tournament with published stages, into the
  static twin `/live/tournament/{id}/brackets` (the door is where a crowd lands,
  and every interactive tab costs a websocket). No opt-in: those pages are already
  `@public_page`, they were merely unreachable without knowing the URL. Gated
  only on `FeatureFlag.BRACKETS`, and read through
  `BracketService.list_all_visible_brackets(None)`, so DRAFT and CANCELLED
  stages stay hidden.
- **Today's matches** — times and player names, **behind a staff opt-in**
  (`SystemConfiguration` key `join_page_match_preview`, default off, on Admin →
  Settings). This re-opens exactly what the membership gate was added to close,
  so it is a decision each community makes rather than a default. Times render on
  the **community's** clock (`TimezoneService.tenant_timezone_name` + `tz_scope`),
  not the visitor's: "today" here means the event's day, and someone reading two
  zones over must not see a different list from the person in the venue. Finished
  matches are dropped, the list is capped at `PREVIEW_LIMIT` (12) with an
  "…and N more today" line, and a roster longer than two renders as
  "A vs B +N more" so a ten-racer play-in does not run off the card.

It also carries the community's Discord door, both halves staff-set on Admin →
Settings:

- **Join the Discord server** — a button to `discord_invite_url`, opened in a
  new tab, shown to signed-in and signed-out visitors alike. The setter
  (`SystemConfigService.set_discord_invite_url`) refuses anything that is not a
  Discord invite and stores it as `https://discord.gg/<code>`.
- **Open copy** — with `open_membership` on (`JoinPreview.open_membership`), a
  signed-out visitor is told signing in is enough; a signed-in visitor only
  reaches the door if `join_openly` failed, and is told to reload.
- **Auto-join copy** — when `discord_auto_join` is on *and* a guild is linked
  (`JoinPreview.discord_auto_join`), the copy says server members get in
  automatically and a signed-in visitor gets an **I've joined, check again**
  button that reloads the page, re-running the gate. The gate itself does the
  joining ([multitenancy.md](../features/multitenancy.md)); the door only ever
  sees people the bot did not find in the server.
- **Join requests off** — when `join_requests_enabled` is `false`
  (`JoinPreview.join_requests`), a signed-in non-member gets no message box and
  no Request access button, and the copy (`_signed_out_message` /
  `_non_member_message`) offers only what is still open: the Discord server if
  auto-join is on, otherwise "its staff can add you directly". A visitor with a
  request already pending still sees the pending copy, since staff can still
  decide it.
- **Declined, cooling down** — for `JOIN_REQUEST_COOLDOWN` (7 days) after a
  decline, `resolve_ask_again_at` hands `render_join_page(ask_again_at=…)` the
  date, and the door says when they can ask again instead of showing the form
  (the service refuses meanwhile too). The Discord invite and auto-join's
  check-again button still show; with auto-join on, the copy leads with joining
  the server, since that gets them in whatever the cooldown says.

Not a `FeatureFlag`: one display toggle does not need two-tier availability, a
`FeatureFlagSpec`, `service_modules`, or `@requires_feature`.

### The tournament-room seeds board (`pages/room_seeds.py`)

`GET /t/<slug>/room/<token>/seeds` — a read-only, live board of matches whose
seed is rolled and which nobody has started, for a PC in the tournament room.

**Inside the tenant prefix on purpose.** The middleware there has already
resolved the community and bound the display clock, so the token only has to
authorize; outside the prefix the page would have to resolve a tenant from the
token itself and enter `tenant_scope` by hand, and every scoped read would raise
until it did.

- **Authorization** is a [`RoomToken`](data-model.md#roomtoken) via
  `RoomTokenService.resolve`, not a session. Unknown, revoked, malformed and
  wrong-community all render the same not-found page, status and copy as a
  route that never existed (`render_not_found`) — "the link's out of date" is
  also the true thing to tell staff at a room PC whose token was just revoked.
  The token is redacted out of the page-view telemetry row (`_tracked_params`).
- **Read-only.** Rolling and re-rolling stay signed-in staff work: an anonymous
  token that could spend randomizer API calls and replace the seed for a match
  about to be played is not a trade worth making for a machine in a room the
  public walks through.
- **Chromeless.** `BaseLayout(user=None, chromeless=True)` — no header, drawer
  or bottom nav, so the board fills the screen it is displayed on. Nothing is
  lost: the header's only working control for an anonymous kiosk was the login
  button, and the dark-mode toggle survives as a floating corner button.
- **Built on `MatchTableView`** with `row_filter=is_seeded_and_unplayed`, the
  readonly `state`/`seed` slots, and no action callbacks — so it inherits the
  live wiring, the mobile card, and the flash-on-change for free.
- **Table preferences: exempt.** An anonymous kiosk has no user row to key a
  saved layout to, and the columns are fixed. Rearranging them would need a
  general anonymous-preferences path, not a second persistence backend for one
  page.
- **Not linked from anywhere**, including the join page. A token URL that is
  publicly linked is not unlisted; the room PC gets it by bookmark. Staff issue,
  revoke and copy the URL on Admin → Settings → Venue.

### Public bracket view (`pages/brackets.py`)

Two read-only `@public_page(..., feature=FeatureFlag.BRACKETS)` routes rendering through `BaseLayout` and `BracketService` (with a load-or-404 `Tournament` lookup). **Public here means anonymous** — a bracket is the spectator-facing artefact of a tournament, so neither route joins `protected_routes` and a link to one works signed out (see [`public_page`](authentication.md#public_page)). The feature and tenant gates still apply, and every staff affordance sits behind `is_staff`, which is `False` for an anonymous visitor. The browse path in is the home **Brackets** tab.

- **`/tournament/{tournament_id}/brackets`** (`bracket_index`) — a card per stage with a **View** button into the detail route. A tournament with no published stage is a **404 to everyone but staff**, so walking ids signed out does not list a community's unannounced events.
- **`/brackets/{bracket_id}`** (`bracket_detail`) — a `@ui.refreshable` body that renders **by format** through the shared [`theme/brackets/`](#bracket-renderer-themebrackets) renderer, with a match-detail dialog on click (staff get inline report/override) and live refresh from the event bus. A signed-in viewer's own entry is marked (**You**, `viewer_entries`) on every card it appears on, and an unfinished matchup shows its next booked time on the card's bottom edge (`booked_times`, on the viewer's clock).

Both take the id as a string and parse it with `parse_route_id`; anything that cannot name a row, and any stage that is not visible to this viewer, renders the shared not-found page (HTTP 404). Titles name the stage, tournament and community (`Championship — Bracket Demo — Default`) and carry `og:title`/`og:description` for link unfurls. The stage's configured rules (`config_summary`) are shown to staff only — "1 round(s) with their own best-of" is a setup check, not something a player acts on. The **Shareable spectator view** link appears only when the twin would show something; for a draft, staff see "The spectator view goes live once a stage starts." instead of a URL that 404s for everyone they paste it to.

DRAFT visibility (staff-only, enforced by `BracketService`'s viewer-aware reads), the mobile List/Bracket toggle, the derived live-status pills, and the standings/crosstable presentation are all covered in [brackets.md](../features/brackets.md#presentation).

#### Bracket renderer (`theme/brackets/`)

One in-house renderer, consumed by the public pages, the admin Results dialog, and (through `static_view/`) the socket-free `/live/...` spectator views.

| Module | Role |
|---|---|
| `layout.py` | Pure, ORM-free layout walker: winner-link tree → absolute pixel placements + elbow connectors, handling byes and the double-elim losers bracket. Unit-tested in [`tests/theme/`](../../tests/theme/) |
| `cards.py` | Absolute-positioned match cards + sticky round headers; `render_avatar(name, url)` paints the entrant disc (Discord avatar layered over the initial, which is the fallback when the cached hash 404s) |
| `tables.py` | Swiss/group data tables (standings with tiebreaker columns + advancement tint, pairings, crosstable) as **NiceGUI elements, never `ui.html`** — entrant names are user-controlled |
| `labels.py` | Format/state chrome shared by those surfaces and the admin format select |
| `render.py` | Whole-bracket helpers (`render_elimination`, mobile accordion, `build_context`, `detect_finals`, `entry_avatars`) |
| `dialog.py` | The shared match report/override dialog |
| `live.py` | Bridges `BRACKET_*` and `MATCH_*` bus events to a refresh (`register_bracket_view`) |
| `static_view/` | The server-rendered, cacheable HTML twin for [`pages/static_brackets.py`](../../pages/static_brackets.py): `markup.py` (escaping + match cards / elimination sections, mirroring `cards.py`), `data_tables.py` (mirroring `tables.py`), `document.py` (page shell + the two whole-page renderers). Pure and I/O-free |

All colour/size routes through `--bracket-*` custom properties in [`static/css/brackets.css`](../../static/css/brackets.css), linked per page via `add_head_html`.

### Admin role mappings (`pages/admin_tabs/admin_discord_roles.py`)

`admin_discord_roles_page(volunteer_mappings=False)` — the **Role Mappings** tab: Discord-role → app-role mappings for sign-in role sync, then (when `admin.py` passes `volunteer_mappings=True` because `VOLUNTEERS` is live) the volunteer-position section from `volunteer_role_mappings.volunteer_role_mappings_section`.

- Requires the tenant to have a connected Discord server (`Tenant.discord_guild_id`); otherwise the tab offers the "Connect Discord server" flow instead of the mapping table.
- A hand-rolled `ui.table` of mappings (`DiscordRoleMappingService.list_mappings`) with a search box (`search_input`), a per-row delete (`remove_mapping`) and, for staff, row checkboxes plus a **Remove N selected** button (`remove_mappings`, behind a `ConfirmationDialog`). `enable_mobile_grid(..., selectable=True)` puts the checkbox on each phone card too.
- **Add Mappings** (gated on `AuthService.can_grant_roles`) is many-to-many: two `theme.pickers.FilterMultiSelect`s (type-to-filter, chips) for Discord roles and grants; every picked role gets every picked grant via `add_mappings`. The Discord list comes from `DiscordService.list_guild_roles`, drops non-`mappable` roles (`@everyone`, bot roles), sorts by name, and shows each role's colour plus a "Grants …" caption for what it already maps to (and its id when two roles share a name). A live line (`bulk_add_summary`) says how many mappings Add will create and how many already exist.
- Refreshes on tab entry via `wire_tab_refresh('Role Mappings', …)`.
- The volunteer section is its own table (`TableKeys.ADMIN_VOLUNTEER_ROLE_MAPPINGS`) of position → role rows from `VolunteerRoleMappingService`; its **Add Mappings** dialog is the same many-to-many shape (positions × `Role.volunteer_mappable()` roles, via `add_mappings`), with the same search box and bulk remove (`remove_mappings`). Shown with or without a connected Discord server.

### Admin equipment (`pages/admin_tabs/admin_equipment.py`)

`admin_equipment_page()` — full asset management.

- A `@ui.refreshable` `ui.table` of all assets (`EquipmentService.list_assets` + `open_loans_by_equipment_id`): #, Name, Owner, Status badge, current holder, with grid-card mode on small screens.
- Per-row buttons: **Check out** / **Check in** (`open_checkout` with `can_manage=True` / `quick_checkin`), **Open asset page**, **Edit** (`EquipmentDialog`), **Delete** (guarded by `ConfirmationDialog` → `EquipmentService.delete_asset`). **Add Asset** opens `EquipmentDialog` in create mode.
- The desktop `body-cell-actions` cell and the mobile `item` card are module-level constants (`_ACTIONS_CELL`, `_GRID_CARD`) built from the same class name; every emitting button in both carries `wiz-requires-socket`, so a dropped socket refuses the tap out loud instead of eating it ([Offline honesty](#offline-honesty-themeconnectionpy)). `tests/theme/test_equipment_offline_guard.py` asserts the mark count equals the `$emit` count in each copy.
- **The card's buttons are labelled; the desktop row's are not.** Text on a phone, icons + `q-tooltip` on a mouse — the same split, for the same reason, as the proctor card (`theme/tables/match_slots.py`: *"the proctor reads this board on a tablet, where a tooltip never opens"*). **Delete sits on its own line** below the others, `outline negative`, so the destructive control is not a thumb-width from Edit; the `ConfirmationDialog` still guards it. `tests/theme/test_equipment_card_labels.py` locks both halves.
- **Print QR labels** opens `QrLabelDialog` — a checklist of assets (all pre-selected), quick-select filters, a template picker (plain grid with Letter/A4 + column count, or an Avery peel-off preset), and "Also show" toggles. It opens [`/equipment/qr-labels`](../../pages/equipment_labels.py) in a new tab (a bare path, so `ui.navigate.to` re-adds the tenant `root_path`) with the chosen `ids`/`template`/`show`. The single-asset page also has a manager-only **Print label** button for one asset.

### Encoded-host check

Both equipment surfaces encode a **tenant-qualified absolute** link in their QR codes (`tenant_url(tenant, f'/equipment/{id}')` — the tenant's custom domain when set, else `{BASE_URL}/t/<slug>/…`). Against a stale `BASE_URL` that produces thirty stickers nobody can scan, and the mistake only surfaces at the venue.

`encoded_host_mismatch(canonical_url, browsing_host)` in [`tenant_urls.py`](../../application/utils/tenant_urls.py) is the pure check: it returns the canonical host when it differs from the host in the address bar, else `None`. The subtlety a naive check gets wrong is that a **custom-domain tenant's encoded host is supposed to differ from `BASE_URL`** — so the comparison is against `tenant_base_url(tenant)`, not `BASE_URL`, and both sides go through `normalize_hostname` (case, default `:80`/`:443`, trailing dot, scheme/path). A missing or malformed host returns `None`: an absent `Host` header must never read as a misconfiguration.

Rendered as a `no-print` warning **above** the Print button on the label sheet (a warning below the action is a warning after the fact), and manager-only under the QR caption on the asset page — a volunteer scanning a label cannot act on a `BASE_URL` misconfiguration. It never blocks printing: the operator may legitimately be printing at home for a venue on another host.

### Check-in desk (`/volunteer/check-in-desk`, `pages/check_in.py`)

A Volunteer hub tab, `check_in_desk_tab(user, event)`: `?event=<id>` opens that event's desk, otherwise the only open event's, otherwise a picker of open events. The desk renders through `CheckInService`; its sticky toolbar needs the tab panel's overflow left visible (`.q-panel:has(.checkin-toolbar)`). `/checkin` and `/checkin/{event_id}` keep their `@protected_page(roles=[STAFF, CHECK_IN_DESK], feature=FeatureFlag.EVENT_CHECK_IN)` gate and redirect here. Feature doc: [event-check-in.md](../features/event-check-in.md#the-desk).

- **Phone-first.** The search box, progress bar, the All / Not yet / Checked in switch (`.wiz-segmented`, like the Event page's view switcher; membership and counts from `check_in_rules.entrant_filters`, with refunded rows listed under All only) and the sync-status line stick under the app header. Below `md` each person is a card with a full-width 48 px **Check in** button and Link/Unlink/Undo/Remove behind a ⋮ menu; on desktop it is a table (`customize_table(..., key=TableKeys.CHECK_IN_DESK)`) with the same actions.
- Each card and row shows one coloured **lanyard** chip (Staff / Volunteer / VIP / Base / Day Pass, from `check_in_rules.lanyard_for`) and, for someone holding several badges, a note naming the extra ones; a refunded buyer shows **Refunded**. The desktop table's columns are Name, Lanyard, Wizzrobe account, Status and actions.
- Search runs in the browser (Quasar `filter-method`), so it doesn't wait on the server. It matches badge codes as well as names.
- After a check-in an **Undo** bar shows for eight seconds, with "Hand over: <lanyard> lanyard" plus any extra badges. Staff get an **Add walk-up** button in the toolbar beside Sync; **Export CSV** downloads the whole roster.
- The header shows the last good sync and any `last_sync_error`, so volunteers can see a stale roster.
- **Several phones at once.** `register_view(on_live_change, channel=check_in_live)` re-reads the roster on any change to this event and flashes the changed card; `refresh_on_reconnect` catches a locked phone up after a blip.

### Asset detail (`/equipment/{asset_id}`, `pages/equipment.py`)

`equipment_detail(asset_id)` — the QR target. A `@ui.refreshable` body: name + status badge, description/owner/holder, manager-only private notes, the QR image with its tenant-qualified link (`tenant_url`), Download QR / Print label, the action row, a guidance line, and the loan history.

- **Actions**: **Check out…** / **Check out to me** (`open_checkout`, wording depends on `can_manage`), **Check in** (`quick_checkin`, manager-only gate), **Edit** (`EquipmentDialog`). All three carry `wiz-requires-socket`; Download QR (a data URI already in the page) and Print label (a new tab) deliberately do not.
- A manager also gets the [encoded-host check](#encoded-host-check) under the QR caption when the link does not match the host they are on.
- **A viewer with no button gets a sentence**, from the pure `equipment_guidance(...)` in [`theme/equipment_copy.py`](../../theme/equipment_copy.py). The check-in gate is asymmetric on purpose — `can_checkout_equipment` admits any signed-in member borrowing to themselves, `can_checkin_equipment` is manager-only — and this line is the one place that *reason* is written for users: *"Returns are recorded by staff or an equipment manager — hand it to them to check in."*, prefixed with *"You have this checked out."* when the viewer is the holder. It also covers a retired asset and a signed-in user who cannot borrow. A manager never sees it.
- **Loan history is bounded at the read**: the page fetches the most recent 5 (`loan_history(asset, limit=5)`) plus `loan_count(asset)`, shows "5 of N", and a **Show all N** `ui.expansion` fetches the remainder on first open — not at build time. An open loan carries an *Out now* badge instead of the old `→ still out` text. The limit belongs to the repository; the "5 of N" string and the expansion are presentation.
- **On reconnect it re-reads.** `theme/realtime.refresh_on_reconnect` registers a `client.on_connect` handler that refreshes the body after a short blip (the first handshake is skipped, so the initial render is not doubled). A blip longer than `reconnect_timeout` is the framework's own page reload.

### Admin feedback (`pages/admin_tabs/admin_feedback.py`)

`admin_feedback_page()` — one `ui.table` built once and reloaded in place from `FeedbackService.list_recent`: Submitted, From (name + category icon), Message (wraps, with the sender's page under it as a link only when `safe_local_path` accepts it; it is player input), Status, and a **Mark reviewed** / **Reopen** button plus a ⋮ (Copy message, Copy page path) per row, both buttons calling `FeedbackService.set_reviewed(actor, id, reviewed=…)`. A New · n / Reviewed / All switch (opens on New), a category picker and a search box filter the loaded rows client-side. Below `lt.md` the rows become cards with the same actions.

### Admin settings (`pages/admin_tabs/settings/`)

One **Settings** entry in the admin drawer opens a settings area. `/admin/settings` is a home page of tiles (`.wiz-settings-grid`: three columns, two below `md`, one on a phone), one per pane with its icon and one-line description; a tile opens its pane in place, and each pane starts with an **All settings** link back. A viewer with only one pane (a sync admin, a preset manager) skips the home and gets no back link. Each pane is a `page_header` plus a stack of `section_panel` cards. The page used to put a menu of panes in a second column beside the drawer; the project owner picked the tiles over top tabs, a nested drawer group and a title picker on 2026-10-13.

| Pane | Slug | Holds | Visible to |
|---|---|---|---|
| General | `general` | Appearance (`admin_theme_page`), Timezone (`admin_timezone_page`) | Staff |
| Event | `event` | Event dates, daily hours (one row per event day, on the community's clock), capacity | Staff |
| Venue | `venue` | Stations (edited in place), station names format, room screens (`room_tokens_section.py`) | Staff |
| Members | `members` | Joining this community (who can join as one `JoinMode` radio, written through `TenantMembershipService.set_join_mode`; the join page's today's-matches toggle; the Discord invite link), then Profile reminders (`admin_profile_reminders_page`) | Staff |
| Volunteers | `volunteers` | Reminder lead, reward tiers | Staff, `VOLUNTEERS` live |
| Discord | `discord` | Role Mappings (Staff), Discord Events (Staff, Sync Admin) | either |
| Integrations | `integrations` | Webhooks (Staff), Randomizer Keys (Staff, Preset Manager) | either |
| Features | `features` | `admin_features_page` | Staff |

- **Routing.** A pane is a path segment: `/admin/settings/<pane>`, registered in `pages/admin.py` as its own `protected_page` that renders the admin page with `section='settings'`. The retired tab slugs (`LEGACY_TAB_PANES`: `appearance`, `timezone`, `features`, `role-mappings`, `discord-events`, `webhooks`, `randomizer-keys`) are `aliases` on the Settings tab and resolve to the pane that holds them. No pane, or one that no longer exists (with a warning notify), opens the home. Opening a pane or going back rewrites the URL with `history.replace`.
- **Who sees what.** `build_settings_panes(access, live)` is pure, like `build_admin_tabs`, and keeps each former tab's role gate per card; the Settings tab is offered only when it returns at least one pane.
- **Embedded pages.** A former tab renders inside `.wiz-settings-embed` under `nested_page_headers()`, which turns its `page_header` into the card's header band. Panes are built on first open, with the tenant, display zone and table layouts rebound (the build runs in a click handler). Reopening a pane, or returning to the Settings tab, re-emits `selected_tab` with each embedded page's old tab label, so its `wire_tab_refresh` still fires.
- **Saving.** Native cards own their values: each has a `SaveFooter` (`theme/save_footer.py`) that stays hidden until a watched field changes, then shows **Unsaved changes**, Discard and Save. Discard puts every watched field back to its last saved value. A refused save keeps the bar up. The page collects its footers in an `UnsavedChanges` group: **All settings** with a dirty card asks *Leave without saving?* (Keep editing, Discard, Save), and while any card is dirty the browser's own leave-page prompt is armed. Embedded former tabs (Appearance, Timezone, the Discord and Integrations cards) keep their own Save buttons and aren't covered. Saving new event dates refreshes the daily-hours rows. Station rows and room tokens save as you edit them, as before.

## Reports subsystem (`pages/admin_tabs/reports/`)

Behavior doc: [../features/admin-reports.md](../features/admin-reports.md). Data aggregation lives in `ReportsService`, `AnalyticsService`, `TelemetryService`, `AuditService`, and `SystemConfigService` (see [services.md](services.md)); the UI layer here is read-only.

`_REPORT_HANDLERS` maps the `report` query param to a page coroutine; `reports_page(report=None, **params)` is the tab entry point. It loads the live flag set once and renders a `@ui.refreshable` `report_body`, which is the refresh boundary for the whole subsystem. An unknown or missing `report` renders the dashboard, and so does a report whose subsystem flag is off (`_REPORT_FEATURES`: `volunteers` needs `VOLUNTEERS`); otherwise the handler receives all forwarded query params (each swallows extras via `**_unused`) and an interaction row is tracked fire-and-forget.

| `report=` | Handler | Params consumed | Panels |
|---|---|---|---|
| _(none)_ | `dashboard.dashboard_page` | `start`, `end` | Four KPI cards (peak players vs capacity, peak stages vs max, match counts, stream-candidate crew coverage) + one clickable card per `REPORT_CARDS` entry |
| `insights` | `insights.insights_page` | `start`, `end`, `bucket`, `tournament_id` | KPI strip + crew / volunteer-hours / tournament-health / admin-activity sections, each bucketed by week or month |
| `capacity` | `capacity.capacity_page` | `start`, `end`, `tournament_id`, `focus` | ECharts line chart (active + on-stream players, dashed capacity line, dataZoom); "Top 5 peak times" with **Inspect** links; matches-active-at table when focused; raw per-interval forecast table |
| `match_ops` | `match_ops.match_ops_page` | `start`, `end`, `tournament_id`, `state` | Per-tournament aggregates (matches, started, finished, avg start delay, avg/expected duration, on-time %) + per-match detail (start delay, duration, confirmation lag, stage, player count) |
| `crew` | `crew.crew_page` | `start`, `end`, `tournament_id`, `user_id`, `approval` | Coverage by match (approved/total commentators + trackers, stream-candidate flag, GAP marker) + contribution by person (signups, approvals, percentages, hours); a row click filters both tables by `user_id` |
| `stages` | `stages.stages_page` | `start`, `end`, `tournament_id`, `stage_id` | Stacked horizontal bar of scheduled vs gap hours + per-stage summary (matches, hours, back-to-back <15 min); a row click drills into that stage's match list |
| `volunteers` | `volunteers.volunteers_page` | `start`, `end` | Understaffed-shift summary card + all-shifts table (position, label, start, filled/needed, status) |
| `audit` | `audit.audit_page` | `start`, `end`, `user_id`, `action` (search text), `category` (Area), `page` | Server-side paginated log over `AuditService.count_logs` / `list_logs`. Each row reads in plain language (`application/utils/audit_display.py`: one label and Area per action, plus a describer for the summary, before/after changes and facts); the selected row's detail sits in a side panel at 1024px+ and opens as a dialog/bottom sheet below. Search matches the action key or its label; "More by …" filters to one person |
| `telemetry` | `telemetry.telemetry_page` | `start`, `end`, `user_id`, `action`, `category`, `page` | Staff-only engagement view over `TelemetryService`: leaderboards plus the same paginated event log |

Every panel above carries a CSV export unless noted. Three behaviours are not derivable from the table:

- **Capacity `focus`** is an ISO timestamp that pre-zooms the chart to a ±2-hour window and reveals the matches-active-at-that-instant table.
- **Insights** defaults to a trailing 90-day window rather than the event window, because a single event weekend collapses to one bucket.
- **Audit and telemetry** paginate server-side at `PAGE_SIZE = 50`, and their CSV export covers the **current page only**.

### URL-driven state and CSV export

Reports hold no client-side state. Every filter widget's change handler calls `navigate_with_params(report=..., **filters)`, which re-renders the report body in place through the refresh the dispatcher registered with `bind_report_refresh`, and rewrites the URL with `history.replaceState` — so filters stay linkable without a navigation, a fresh websocket, and a rebuild of every other admin tab. [`static/js/report-nav.js`](../../static/js/report-nav.js) holds the scroll position across the rebuild. With no refresh bound (a report body rendered outside the dispatcher) it falls back to `show_navigating()` + `ui.navigate.to`. Date ranges default to the configured event window (`SystemConfigService.get_event_window()`) when not in the URL.

CSV export (`csv_export_button`) downloads the currently rendered rows using `rows_to_csv_bytes` / `timestamped_filename` from [`application/utils/csv_export.py`](../../application/utils/csv_export.py) — UTF-8 with BOM, formula-injection prefixes escaped, hidden columns skipped.

### Shared helpers (`reports/shared.py`)

| Helper | Purpose |
|---|---|
| `REPORT_KEYS` | Tuple of report keys — currently unreferenced; the dashboard cards are driven by `dashboard.REPORT_CARDS` |
| `async chart_palette()` / `chart_palette_for(accent)` | The main series in the community's colour, moved by `TenantThemeService.chart_series_colour` just far enough to clear 3:1 on **both** the light and dark card surfaces (canvas charts are painted once), its area and slider washes, and a second-series colour that is never the same hue (teal, or amber for a teal community) |
| `CHART_TEAL` / `CHART_AMBER` / `CHART_RED` / `CHART_NEUTRAL` / `CHART_TEXT` / `CHART_GRID` | Fixed colours that carry a meaning (threshold, idle, a middling score) and chart chrome; assigned **by role**, never per chart |
| `themed_chart_option(option)` | Overlay the mode-neutral chrome colours onto an ECharts option without clobbering what the chart already sets |
| `reports_url(report=None, **params)` | Build `/admin/reports[?report=…]` URLs — `admin_url('reports', …)` from [`pages/admin_tabs/links.py`](../../pages/admin_tabs/links.py), which drops empty params and ISO-formats dates |
| `parse_date(value)` / `parse_int(value)` | Lenient parsing; `None` on garbage |
| `display_bounds(start_d, end_d)` | picked date range → half-open UTC bounds on the display clock (end clamped ≥ start) |
| `async default_date_range(start_param, end_param)` | The URL dates if both parse, else `SystemConfigService.get_event_window()` |
| `report_page_shell(title, subtitle=None)` | Context manager: page container, an "All reports" button back to the dashboard, then `page_header` |
| `report_filter_bar(active=0)` | Context manager: the match boards' filter card (`match-filters-card--compact`). One row of outlined fields on desktop; below 1024px it folds behind a Filters button whose badge shows `active`, and its open state survives a filter refresh |
| `filter_field(element)` | Style a report filter like a match-board field (`match-filter-field`, outlined, dense) |
| `report_jumps(options, start_d, end_d, on_pick)` / `async event_jump_options()` | A segmented "Jump to" row of preset ranges beside the fields on desktop (under them on a phone); the event window plus each of its days (up to a week), or Insights' last 30/90/365 days |
| `async is_custom_range(start_d, end_d)` | Whether the range differs from the event window, for the filter badge |
| `date_range_filter(default_start, default_end, on_change)` | From/To filter fields built with `native_date_input`, so a phone opens its own date picker; fires `on_change(start, end)` once typing settles (a 0.8s trailing throttle, and `parse_date` drops years outside 2000–2100, so a year typed digit by digit never re-runs the report) |
| `async tournament_filter(current_id, on_change)` | Tournament select with an "All tournaments" (`0`) option |
| `kpi_card(title, value, subtitle, color, href, href_label)` / `kpi_grid()` | One KPI tile, inside the `wiz-tile-grid--sm` grid `kpi_grid` returns (dashboard, insights, telemetry); `href` makes it a route to the report the number comes from |
| `enable_drill_link(table, columns, rows, url_for, enabled=True, hint=…)` | Per-row navigating control — desktop `body-cell-drill` slot **and** the string to pass as `enable_mobile_grid(actions=…)`, in one call. Renders nothing when `enabled` is false. Returns the card-action markup |
| `parse_details(raw)` | `(parsed_json_or_none, display_text)` for an audit/telemetry blob; legacy plain-text rows pass through unchanged |
| `clicked_row(e)` | Extract the row dict from a NiceGUI `row-click` event |
| `async paginated_event_log(title=, icon=, ...)` | The shared server-side-paginated log panel (audit + telemetry): a `section_panel` with CSV in its header, the page range as its subtitle (`count_noun='entries'` → `Showing 51–100 of 124 entries`), the table and the pager |
| `show_navigating()` | The overlay that acknowledges a click costing a full navigation — now only the per-row drill-out, since filter changes refresh in place. A NiceGUI element, not `run_javascript` — the outbox emits element updates before messages, so this reaches the browser ahead of the `open` |
| `bind_report_refresh(refresh)` | Registers the dispatcher's in-place re-render on the client (never module state); what turns every `navigate_with_params` into a refresh |
| `csv_export_button(filename_prefix, columns_provider, rows_provider, label='Export CSV')` | Download button; notifies on failure. Re-exported from [`theme/tables/export.py`](../../theme/tables/export.py) |
| `navigate_with_params(report=None, **params)` | Apply new filters: in-place refresh + `replaceState`, falling back to `ui.navigate.to(reports_url(...))` |

## Dialog catalog (`theme/dialog/`)

All dialogs follow the same shape: a `ui.dialog` + `.dialog-card`, a title row with a close button, a body, and an action row; most submit on Enter via a `keydown` handler and report errors with `ui.notify` (`PermissionError` → negative, `ValueError` → warning/negative). [`theme/dialog/__init__.py`](../../theme/dialog/__init__.py) re-exports `CatFactDialog`, `CheckoutDialog` (plus `open_checkout` / `quick_checkin`), `ConfirmationDialog`, `EquipmentDialog`, `FeedbackDialog`, `HardPresetDialog`, `MatchResultDialog`, `QrLabelDialog`, `RescheduleDecisionDialog`, `RescheduleRequestDialog`, `SendMessageDialog`, `StationAssignmentDialog`, `TournamentDialog`, `UserDialog`, and `AdminUserDialog`; the rest are imported by module path.

| File | Class(es) | Purpose | Opened from |
|---|---|---|---|
| [`_helpers.py`](../../theme/dialog/_helpers.py) | `form_dialog`, `dialog_header`, `dialog_actions`, `mobile_sheet`, `native_date_input`, `native_time_input`, `native_datetime_input`, `datetime_to_local_input` / `local_input_to_datetime`, `render_suggest_time_button`, `submit_on_enter` | The house dialog chrome every dialog builds on — keeps actions reachable on a phone. `render_suggest_time_button` is the "Suggest a time" button shared by every match-scheduling dialog (`UserMatchDialog`/`AdminMatchDialog`, `BracketScheduleDialog`, `ChallongeScheduleDialog`) — it fills Date/Time from `MatchSuggestionService.suggest_match_time` on click rather than pre-filling silently on open | (imported) |
| [`secret_dialog.py`](../../theme/dialog/secret_dialog.py) | `show_secret_dialog(title, secret, noun=…)` | Shows a freshly minted credential once, read-only, with Copy and Done | New API token (Profile); new or rotated webhook signing secret |
| [`match_dialog.py`](../../theme/dialog/match_dialog.py) | `AdminMatchDialog`, `UserMatchDialog` | Create/edit matches | Admin schedule; home My Schedule → matches |
| [`match_dialog_base.py`](../../theme/dialog/match_dialog_base.py) | `BaseMatchDialog` (+ pure copy helpers `enrolment_preview`, `enrolment_report`, `acknowledgment_empty_state`) | Shared state and behaviour for the two match dialogs, split out when `match_dialog.py` hit the 800-line guideline | (subclassed) |
| [`reschedule_request_dialog.py`](../../theme/dialog/reschedule_request_dialog.py) | `RescheduleRequestDialog` | The player's ask to move or cancel a match — worded throughout as a *request*, not a booking | My Schedule's ⋮ menu (Ask to change); `?reschedule=` deep link |
| [`reschedule_decision_dialog.py`](../../theme/dialog/reschedule_decision_dialog.py) | `RescheduleDecisionDialog` | Staff's answer to a reschedule request, with everything needed to decide on the card; Approve performs the change (`update_match` / `cancel_match`) | Admin schedule's reschedule queue strip; `?reschedule_request=` deep link |
| [`hard_preset_dialog.py`](../../theme/dialog/hard_preset_dialog.py) | `HardPresetDialog` | The player's private opt-in to the tournament's harder preset; copy readable without revealing the opponent's answer | My Schedule's ⋮ menu (Harder settings); `?hard=` deep link |
| [`table_preferences_dialog.py`](../../theme/dialog/table_preferences_dialog.py) | `open_table_preferences` | The staged Preferences modal behind every table's gear (Cancel discards); ▲/▼ and width steppers are the keyboard/touch equivalents of drag | `preferences_button` |
| [`volunteer_autofill_dialog.py`](../../theme/dialog/volunteer_autofill_dialog.py) | `VolunteerAutofillDialog` (+ pure `build_policy`) | Collect the `DraftPolicy` for one auto-fill run | Volunteer Shifts "Auto-fill from availability" |
| [`volunteer_assign_dialog.py`](../../theme/dialog/volunteer_assign_dialog.py) | `VolunteerAssignDialog` | Pick a volunteer for one shift (search, qualified first, availability badges); `on_pick` does the assigning or swapping and returns whether to close | Volunteer Shifts "Assign"; Shift Board sheet's Assign and Swap |
| [`volunteer_shift_sheet.py`](../../theme/dialog/volunteer_shift_sheet.py) | `VolunteerShiftSheet` | One shift's side sheet (bottom sheet on a phone): people with arrival state and a ⋮ each (Check in, Swap, Move, Remove), Assign volunteer, and Edit/Delete in the footer ⋮. Reads the board's grid through `lookup`, so `sync()` repaints it after any change | Shift Board |
| [`_match_bracket_link.py`](../../theme/dialog/_match_bracket_link.py) | `render_select`, `render_matchup_panel`, `apply_link`, `matchup_label`, `linked_matchup_id` | The admin editor's bracket-matchup panel + picker | `AdminMatchDialog` |
| [`bracket_schedule_dialog.py`](../../theme/dialog/bracket_schedule_dialog.py) | `BracketScheduleDialog` | Schedule one game of a native-bracket matchup; player mode gets the shared Suggest a time button (round-window clamped) | Home My Schedule → matches (player mode); bracket match dialog (staff mode) |
| [`confirmation_dialog.py`](../../theme/dialog/confirmation_dialog.py) | `ConfirmationDialog` | Generic confirm/cancel; the negative-styled confirm button runs the caller's `on_confirm`. `title=` names what is being confirmed (default `'Confirm'`), and the body renders `white-space: pre-line`, so `'\n\n'`-separated paragraphs survive. `require_phrase=` adds a text box for the irreversible ones: confirm stays disabled until the reader types the phrase back, and what they typed is on `.typed_phrase` when `on_confirm` runs, so the service re-checks the real input | Start/Confirm actions, crew signup/undo, crew approve/un-approve, match/triforce-text/equipment/shift deletes |
| [`match_result_dialog.py`](../../theme/dialog/match_result_dialog.py) | `MatchResultDialog` | Pick a winner → `MatchService.record_match_result`; the Finish callback then calls `MatchScheduleService.finish_match`. Two players (the common case) get one big button each, then a **second step** naming the pick before it commits — recording a winner settles the match and a proctor cannot undo it; three or more get a required Winner select and a Submit button | Admin schedule & proctor board Finish button |
| [`station_assignment_dialog.py`](../../theme/dialog/station_assignment_dialog.py) | `StationAssignmentDialog` | One Station input per player (≤50 chars, blank clears); submits the full `{player_id: station}` map to `MatchService.assign_stations`. A **Randomize** button (two-player matches on a community with a station pool) fills the pickers from `MatchService.suggest_stations` — opposite sides, spread out — and notifies each rule the draw could not honour; the proctor still submits | Admin schedule Check In flow & players-column button |
| [`stage_edit_dialog.py`](../../theme/dialog/stage_edit_dialog.py) | `StageEditDialog` | Create/edit a `Stage` | Admin Stages tab |
| [`tournament_edit_dialog.py`](../../theme/dialog/tournament_edit_dialog.py) | `TournamentDialog` | Create/edit a tournament. Name, description and Active are always visible; everything else lives in four `ui.expansion` sections — **Scheduling**, **Entry & administration**, **Seeds & randomizer**, **Integrations** — collapsed on create and expanded on edit, because exactly one field is required and the rest are defaulted. Seed-generator options come from `SeedGenerationService.AVAILABLE_RANDOMIZERS` ([seed-generation.md](seed-generation.md)); the **Racetime** block inside Integrations appears only when the actor passes `AuthService.can_manage_sync`, and its bot select offers only categories the tenant is authorized for. On edit, staff also get a collapsed **Danger zone** holding **Delete tournament** — disabled with an explanation while the stored row is still active, and otherwise opening a `ConfirmationDialog` that names what cascades away (matches, entrants, bracket stages, triforce texts, from `TournamentService.deletion_preview`) and requires `permanently delete` typed in | Admin Tournaments tab |
| [`tournament_players_dialog.py`](../../theme/dialog/tournament_players_dialog.py) | `TournamentPlayersDialog` | The tournament's entrants, **read-write**: Remove per row and an Enrol select over `UserService.get_community_people_summaries()`. The service refuses a non-member, so the member-scoped picker is a convenience rather than the gate | Player-count link in tournament table |
| [`send_message_dialog.py`](../../theme/dialog/send_message_dialog.py) | `SendMessageDialog` | Send a Discord DM (required textarea; default callback is `DiscordService.send_dm`) | `AdminUserDialog` ⋮ → "Send message" |
| [`user_edit_dialog.py`](../../theme/dialog/user_edit_dialog.py) | `BaseUserDialog`, `UserDialog`, `AdminUserDialog` | Profile / full user editing | Admin Users tab (`AdminUserDialog`); crew-name link in match tables (`UserDialog`, `access.approve_crew`) |
| [`challonge_schedule_dialog.py`](../../theme/dialog/challonge_schedule_dialog.py) | `ChallongeScheduleDialog` | Date/Time only — tournament and both players come from the bracket; the shared Suggest a time button fills them from `MatchSuggestionService.suggest_match_time` on click, submitted via `ChallongeService.schedule_challonge_match` | Home My Schedule → matches, "Schedule" button |
| [`challonge_participants_dialog.py`](../../theme/dialog/challonge_participants_dialog.py) | `ChallongeParticipantsDialog` | A linked bracket's entrants, unlinked first, each with a status badge (Not linked / Assigned by hand / Challonge account), a member picker + Assign, and Clear on hand assignments. Flags a hand assignment that overrides a different verified account | Admin → Tournaments, **Match entrants to members…** in a linked tournament's ⋮ |
| [`volunteer_position_dialog.py`](../../theme/dialog/volunteer_position_dialog.py) | `VolunteerPositionDialog` | Create/edit a `VolunteerPosition` | Admin Volunteers "Manage positions" |
| [`volunteer_shift_dialog.py`](../../theme/dialog/volunteer_shift_dialog.py) | `VolunteerShiftDialog` | Create/edit a `VolunteerShift` (end date auto-tracks the start until edited) | Admin Volunteers Add/Edit shift |
| [`volunteer_profile_dialog.py`](../../theme/dialog/volunteer_profile_dialog.py) | `VolunteerProfileDialog` | View a volunteer's availability, edit their qualifications | Volunteers manage button |
| [`volunteer_export_dialog.py`](../../theme/dialog/volunteer_export_dialog.py) | `VolunteerExportDialog` | Date-ranged ZIP export of the volunteer bundle | Both volunteer admin tabs |
| [`checkout_dialog.py`](../../theme/dialog/checkout_dialog.py) | `CheckoutDialog` (+ `open_checkout` / `quick_checkin`) | Check an asset out. `open_checkout` shows the borrower picker only when `can_manage`; otherwise it self-checks-out immediately. The picker lists **this community's people** (`UserService.get_community_people_summaries`, actor force-included), with an **Include people outside this community** checkbox that refills from the global list — a widening, not a filter. Either way `_borrower_options` drops the system account and deactivated users | Admin Equipment tab, asset detail page |
| [`equipment_dialog.py`](../../theme/dialog/equipment_dialog.py) | `EquipmentDialog` | Create / edit a lending asset; create mode adds a Quantity field for bulk creation (`bulk_create_assets`). The **Owner** select uses the same per-community read (community sentinel first, actor and the stored owner force-included, and an unavailable stored owner kept selectable so saving cannot silently clear it) | Admin Equipment tab; asset detail Edit |
| [`qr_label_dialog.py`](../../theme/dialog/qr_label_dialog.py) | `QrLabelDialog` | Pick/filter assets, choose a template (plain or Avery) + content, then open the printable sheet in a new tab | Admin Equipment tab "Print QR labels" |
| [`check_in_dialogs.py`](../../theme/dialog/check_in_dialogs.py) | `open_link_dialog`, `open_walk_up_dialog`, `open_event_dialog` | Link a registrant to a member (`suggest_users` first, then `search_members`); add a walk-up as a member or a bare name; create/edit a check-in event with a **Look up** button over `preview_bounty` | Check-in desk ⋮ menu and **Add walk-up** toolbar button; Admin Check-in tab |
| [`feedback_dialog.py`](../../theme/dialog/feedback_dialog.py) | `FeedbackDialog` | Submit feedback, capturing `window.location` via `ui.run_javascript` | Drawer "Feedback" item (any page, logged in); the 500 error page |
| [`cat_fact_dialog.py`](../../theme/dialog/cat_fact_dialog.py) | `CatFactDialog` | Easter egg — five taps on the header wordmark, or the Konami code. "Meow another" re-rolls (never to the fact already on screen); "See them all" opens [`/cat-facts`](#pages--routes) | `BaseLayout._render_header` |

### Match dialogs (`theme/dialog/match_dialog.py`)

**`BaseMatchDialog(match=None, on_submit=None)`** (in `match_dialog_base.py`) holds shared state and helpers: it captures `match.updated_at` at construction for optimistic-concurrency checks, supplies the tournament/date/time/comment/stage defaults, the date+time inputs with popup pickers, the five **Clear** buttons (Check In / Started / Finish / Confirmed / Seed — each disabled when the corresponding timestamp or seed relation is already empty, setting `_clear_*` flags that `MatchService.update_match` consumes), the watch toggle (`MatchWatcherService.watch` / `unwatch`, reverting itself on `ValueError`), the stream-volunteer toggle beside it (`MatchStreamVolunteerService`, same revert-on-`ValueError` shape, with the advisory sentence rendered under it), and the `ConfirmationDialog`-guarded delete.

**`AdminMatchDialog`** — the full create/edit form. Beyond the obvious fields:

- The Players multi-select is restricted to players enrolled in the selected tournament unless "Choose any players" is set; options reload whenever the tournament or checkbox changes.
- **Bracket matchup** (optional, only when `FeatureFlag.BRACKETS` is live) — when the match already backs a game, a read-only matchup panel sits above the picker (`render_matchup_panel` → `BracketService.matchup_summary`) showing stage · round, series position and score, both entrants with seeds, and a button into the bracket view, so staff editing a game can see *what it is for*. The picker below lists the tournament's OPEN matchups with a free game slot (`BracketService.list_linkable_matches`); in edit mode the current link is preselected by hand (a linked matchup is no longer "linkable") and `(None)` unlinks. Lives in [`_match_bracket_link.py`](../../theme/dialog/_match_bracket_link.py).
- **Edit mode only** adds the footer ⋮ items **Rewind…** (a step picker calling `MatchService.rewind_match`, which clears every step after the chosen one together; targets come from `application/services/match/match_rewind.py`), **Call off match…**, **Clear seed…** (confirms, then `MatchService.clear_match_seed`) and **Delete match…**, plus a **Racetime Room** section when the tournament has a racetime bot. With no room, STAFF/`SYNC_ADMIN` get a "Create racetime room" button calling `RaceRoomService.manual_create_room` (a manual open independent of the auto-open toggle). With a room, the slug links to `RacetimeRoom.url` (a derived property the bracket watch-link shares, so the two cannot drift), the status renders as a labelled chip rather than its raw enum value, and a live room offers **Cancel race room** behind a confirmation — `RaceRoomService.cancel_room` was reachable over REST and from no web surface at all. A finished or already-cancelled room gets the chip and nothing else, since there is nothing left to stop. The confirmation says what it does *and does not* do: Wizzrobe stops treating the room as live, and the room itself stays open on racetime.gg.

On save it validates required fields, runs `MatchService.ensure_players_enrolled`, then:

- *Edit*: re-fetches the match and aborts with a warning if `updated_at` changed since the dialog opened (another admin edited it); otherwise `MatchService.update_match(...)` with the clear flags, plus `assign_stage` / `set_stream_candidate` only when those changed.
- *Create*: `MatchService.create_match(...)`, then `assign_stage` if a stage was chosen.
- Either way, a changed bracket-matchup selection is reconciled by `BracketService.unlink_match` and/or `link_match_to_bracket_match` **after** the match write — the `Match` has to exist before it can be linked.

**`UserMatchDialog(discord_id, match=None, on_submit=None)`** — the player-facing variant. Tournament options default to the player's enrolled tournaments; a "Show all tournaments" checkbox widens the list, and submitting auto-enrolls if needed. Both lists come from `TournamentService.list_player_requestable`, which excludes bracket-run and archived tournaments (see [Who may schedule](../features/brackets.md#who-may-schedule-and-the-manual-request-lockout)); `submit_match_request` refuses both, and refuses a time that has already passed. A new match's date and time default to the next whole hour at least 30 minutes out (`next_whole_hour_local`), never to now. A single required Opponent select lists the other enrolled players. With no selectable tournament the form short-circuits with a message — "opt into a tournament", or a pointer to My Schedule when every one of their tournaments runs on a bracket. Submission uses the same `updated_at` concurrency check, then `update_match` (with empty crew lists) or `submit_match_request`, with the player as `actor`.

### User dialogs (`theme/dialog/user_edit_dialog.py`)

**`BaseUserDialog`** captures `user.updated_at` for concurrency checks and provides tournament-enrollment helpers built on `TournamentRepository` and `UserService.update_user_tournament_registrations` / `manage_tournament_enrollments`. Only `AdminUserDialog` uses them now.

**`UserDialog`** — self-edit: read-only Username, Display Name, Pronouns; saves via `UserService.update_user_profile` (with `check_concurrency=True` and the captured `initial_updated_at`). No role editing, and no tournament multi-select — that was a second, even less findable route to the opt-in the Profile checkbox offered, reachable only by clicking your own name in a match table. Signing up lives on the Tournaments tab.

**`AdminUserDialog`** — verifies `AuthService.is_staff(actor)` on open and refuses otherwise.

- Username is required and editable only on create (read-only on edit); Discord ID is create-only. On edit, when Challonge is configured, the dialog manages the user's link: a linked user gets an editable "Challonge username" saved via `ChallongeService.set_player_username`; an unlinked user gets "Challonge account id" + optional username inputs Staff can fill to link manually via `link_player_manually` (the id must be unique across users).
- A Roles multi-select over the `Role` enum, and three tournament multi-selects: Player enrollments, "Tournament Admin of", "Crew Coordinator of".
- On save it diffs each selection against current state: `UserService.grant_role` / `revoke_role` per added/removed role, and `TournamentService.add_admin` / `remove_admin` and `add_crew_coordinator` / `remove_crew_coordinator` per membership change. Create mode goes through `UserService.create_user` first, then applies the same syncs. See [authentication.md § Roles](authentication.md#roles).
- Edit mode adds **Send message** to the footer's ⋮ menu, opening `SendMessageDialog`.

## Shared presentation components (`theme/`)

| Module | Purpose |
|---|---|
| [`availability_editor.py`](../../theme/availability_editor.py) | `render_availability_editor(service, *, help_text, help_snippet='', title='My Availability', title_class='page-title', mode=DECLARE_MODE)` — the window-row + effective-graph UI behind **both** availability tabs. A `@ui.refreshable` row per window (Date / Start / End + a status select + delete), an add button, and a save through the injected service's `set_windows`; plus a second `@ui.refreshable` per-day coloured bar from `effective_segments(...)`, refreshed live as any input changes. Both span the configured event window (`SystemConfigService.get_event_window()`), one local calendar day per graph row. The `EditorMode` decides the direction and must match the injected service's own reading: `DECLARE_MODE` (volunteer tab, `VolunteerAvailabilityService`) offers Preferred / Available / Unavailable and paints an unfilled stretch as "Not stated"; `BLOCK_MODE` (home tab, `PlayerAvailabilityService`) offers only **Can't play** / **Prefer**, paints unfilled as Available, and greys the hours outside `SystemConfigService.get_tournament_hours()` — resolved to instants on the *community's* clock, because the graph is drawn on the viewer's |
| [`realtime.py`](../../theme/realtime.py) | `register_view(on_change, channel=None)` subscribes a view to a live channel — [`application/events/match_live`](../features/event-system.md) by default, or `check_in_live` for the check-in desk — and installs the client-disconnect cleanup; the handler receives whatever the channel publishes. `refresh_on_reconnect(refresh)` re-reads the view after a short socket blip (skipping the first handshake); used by the asset detail page and the check-in desk |
| [`empty_state.py`](../../theme/empty_state.py) | `empty_state(message)`, `empty_panel(message, icon=…)` (the dashed panel a standalone empty list shows) and the `no_data_slot(...)` Quasar template, used by the table views |
| [`waiting.py`](../../theme/waiting.py) | `waiting_panel(message)` — spinner + message + a cat fact, for a wait the app cannot shorten. Returns the element so the caller removes it when the work lands; used by `BaseLayout._build_tab` (deferred tab builds). `await waiting_screen(message)` is the whole-page form for a route that is only a wait — the two OAuth screens in [`pages/auth.py`](../../pages/auth.py), which render outside `BaseLayout`. It resolves the in-scope community's palette and name (the platform's when there is no tenant) via `apply_brand_palette`, centres the panel in the viewport, and returns a `WaitingScreen` whose `navigate_to(target)` holds the panel for `MIN_WAIT_SECONDS` first, so the fact is readable instead of a flash between redirects. Build it **before** `await client.connected()` so the spinner paints while the work runs |
| [`notify.py`](../../theme/notify.py) | `notify_error(exc)` — the one place service `ValueError`/`PermissionError` map to a coloured toast |
| [`clipboard.py`](../../theme/clipboard.py) | `copy_to_clipboard(value, label)` — writes to the clipboard and toasts "<label> copied to clipboard."; every Copy button (API tokens, the MCP URL, room links, payout blocks, one-time secrets) goes through it |
| [`connection.py`](../../theme/connection.py) | `install_connection_watch()` — the client-side offline state, installed for every page by `BaseLayout.render_chrome`. See [Offline honesty](#offline-honesty-themeconnectionpy) |
| [`chrome.py`](../../theme/chrome.py) | See [Tenant-less chrome](#tenant-less-chrome-themechromepy) |
| [`notice.py`](../../theme/notice.py) | `stash_notice(message, color=…)` / `drain_notice()` — a toast that survives a redirect: stash it before `ui.navigate.to`, and the next framed page shows it while it builds. Drained by `BaseLayout.render`, `render_error_page`, and the mock-Discord `/login` picker. Only for a notify that precedes a navigation |
| [`section.py`](../../theme/section.py) | `section_panel(...)` — see [Section panels](#section-panels-themesectionpy) |
| [`matcherino_login_card.py`](../../theme/matcherino_login_card.py) | `matcherino_login_card()` — the Matcherino login card on Admin → Check-in (see [event-check-in.md](../features/event-check-in.md#the-stored-login)); `bookmarklet_href()` builds the **Copy Matcherino login** `javascript:` URL, rendered with `ui.link` so no raw HTML is involved |
| [`setup_checklist.py`](../../theme/setup_checklist.py) | `render_setup_checklist(steps, on_go)` — the new-community checklist behind Admin → Setup, derived from `TenantSetupService` |
| [`join_page.py`](../../theme/join_page.py) | See [The join page](#the-join-page-themejoin_pagepy) |
| [`error_page.py`](../../theme/error_page.py) | `render_error_page(...)` — see [Error pages](#error-pages-middlewareerror_handlerspy) |
| [`equipment_copy.py`](../../theme/equipment_copy.py) | `equipment_guidance(...)` — the asset page's why-no-button sentence; see [Asset detail](#asset-detail-equipmentasset_id-pagesequipmentpy) |
| [`qualifier_copy.py`](../../theme/qualifier_copy.py) | Shared explanatory copy (Score, Estimate) for the player and admin async-qualifier surfaces, so the two never explain a column differently |
| [`timer_teardown.py`](../../theme/timer_teardown.py) | `PageTimer` — use it instead of `ui.timer`. It re-checks `is_deleted` after waiting for the client, so a page closed before its socket settles stops the timer quietly instead of raising from a dead slot. The backstop filters live in `application/utils/timer_teardown.py` |
| [`assets.py`](../../theme/assets.py) | `asset_url(path)` and `cache_control(...)` — see [Static assets](#static-assets--styling-static) |
| [`markdown_editor/`](../../theme/markdown_editor/__init__.py) | `MarkdownEditor(label, value=..., placeholder=..., compact=False)` — a WYSIWYG markdown editor (vendored Tiptap bundle in `dist/`) whose toolbar is limited to what `render_blocks` can show; value is markdown. `compact=True` is for a line or two of notes: a shorter box and only bold, italic, code, link and bullets. Used by community event, tournament (description full; randomizer notes and triforce access message compact) and async qualifier descriptions; every display of those fields renders through `parse_blocks` + `render_blocks`. Rebuild with `npm ci && npm run build` in that directory. See [community-events.md](../features/community-events.md#descriptions-and-the-editor) |
| [`community_events.py`](../../theme/community_events.py) | The community-events disclaimer banner, status chips and time lines shared by the page and the admin tab |
| [`help/`](../../theme/help/__init__.py) | `help_icon(...)` / `resolve_help` + `help_button` (one "?" per heading or dialog title), `render_blocks` / `render_spans` — the safe-markdown renderer shared by `/help`, `/event-info` and tournament descriptions. See [../features/help.md](../features/help.md) |

## Table views (`theme/tables/`)

Three view classes — `MatchTableView`, `TournamentTableView`, `UserTableView` — share a pattern: the constructor takes `columns` (NiceGUI/Quasar column dicts) and `get_query` (a callable returning a queryset or row provider), builds a `ui.table` with custom cell slots, switches to card-grid rendering on small screens via `:grid="Quasar.Screen.lt.md"` with a hand-built `item` slot, and exposes `refresh()` plus `update_row_by_id()` for single-row updates. Tables start empty; the view owns its first load. Paging is client-side (`pagination={'rowsPerPage': 25, 'page': 1}`) over rows already loaded — there is no `update:pagination` handler, so a page turn never re-queries. Each takes an optional `table_key` for [saved column layouts](#data-tables). The fourth **family table**, equipment, has no view class: each equipment surface builds its own `ui.table` with a bespoke `item` slot.

### `MatchTableView` (`theme/tables/match.py`)

The largest UI component, used by the home Schedule and Player boards, the admin Schedule tab, the volunteer Proctor Station, and the tournament-room seeds board. Split across `match.py` (view), `match_filters.py` (the filter strip and its stored state), `match_slots.py` (Vue templates), `match_grid.py` (cards), `match_handlers.py` (event-handler coroutines), `match_access.py` (capabilities), `match_lifecycle.py` (the shared lifecycle handlers), and `hard_preset_rows.py` (the per-viewer harder-preset row fields).

**Constructor flags**: `admin_controls` (this is an operator's board — station chips, no self-signup), `access` (a `MatchBoardAccess`; see below), `extra_slots` (caller-supplied cell templates), `submit_match_callback` (renders the Create Match / Request Match button), `player_discord_id` (scopes data to one player), and per-action callbacks `on_edit`, `on_generate_seed`, `on_seat`, `on_start`, `on_finish`, `on_confirm`, `on_edit_result`, `on_set_stage`, `on_assign_stations` — slots and event handlers are registered only for callbacks that are provided. Per-surface identity: `storage_key` (each board's own filter namespace — pass a distinct one per construction site), `default_state_filter`, `default_day_scope` (the Day filter's opening value; the admin board passes `'Today'`), `table_key` (saved column layout), `searchable` (a search box over the visible columns), and `grid_breakpoint` (default `lt.md`; the home Schedule uses `lt.lg`). Scoping: `match_ids` (a deep link focusing the board on specific matches — narrows the query and suspends the State and Day filters without touching the stored ones) and `scope_tournament_ids` (a hard bound for a per-tournament operator, intersected with the Tournament filter so choosing one cannot widen the board). Five **board-shaping** options exist for surfaces that need a different frame around the same rows: `row_sort` (a `list[dict] -> list[dict]` applied just before the rows reach the table), `row_filter` (a per-row predicate defining what the board *is*, rather than what its operator chose — the tournament-room board passes `is_seeded_and_unplayed`; it applies to a full refresh **and** to a single row arriving from a live update, so a match that starts leaves the screen and a match that gains a seed brings the board to a refresh), `exclude_racetime` (threaded down to `MatchRepository.get_all`, dropping matches whose tournament runs on racetime.gg), `on_rows_changed` (called with the visible rows after every `refresh()` and `update_row_by_id()`, for a summary strip), and `actions_first` (mobile card renders its actions row directly under the players). With `layout='run_sheet'`, `run_sheet_group` picks the grouping (`match_run_sheet.admin_group` by default, `proctor_group` on the Proctor Station) and `proctor_run_sheet=True` draws the proctor's line (stations on the names, the next step as a button).

**Filters** (`MatchFiltersMixin`, `theme/tables/match_filters.py`) — a card with a Day scope and three multi-selects (Tournament, Stage, State) plus a refresh button; options load asynchronously from `MatchDisplayService.get_tournaments_for_filter` / `get_stages_for_filter`. Values persist **per user per tenant** through `tenant_session_get` / `tenant_session_set` ([`application/utils/tenant_session.py`](../../application/utils/tenant_session.py)), which key under `app.storage.user['by_tenant'][<tenant_id>]` — a filter is meaningful only within its own community. Keys are namespaced per board by `_skey` (from `storage_key`). State defaults to the board's `default_state_filter`, else `DEFAULT_STATE_FILTER` (Scheduled + Checked In + Started). Below 1024px the filter card collapses behind a toggle with an active-filter count badge.

**Live updates are push, not polled.** `register_view(self._on_remote_change)` subscribes the view through [`theme/realtime.py`](../../theme/realtime.py): a remote `'changed'` / `'deleted'` updates that one row (with a flash), `CREATED` refreshes the table, since a new match has no row yet. On a `row_filter` board a `'changed'` for a match with **no** row also refreshes — an existing match can become that board's business (rolling a seed publishes `changed`, not `CREATED`), and an in-place update would no-op against a row that is not there yet.

**The player's own controls** — My Schedule's matches are the player run sheet plus `PLAYER_MENU_COLUMN` (named `my_actions`, label *More*): a ⋮ per line on the viewer's own matches holding Ask to change / Change requested, the harder-settings choice and Offer for stream / Withdraw stream offer, each item keeping the gate of its single-purpose slot. `register_player_run_sheet_slots(table, menu=True)` puts the same ⋮ on the phone card, and the view's bulk fetches gate on `_shows_column(name)`, which counts `my_actions`. Acknowledge sits under the status and Watch is the bell, as on Event › Schedule. `match_slots.MY_ACTIONS_SLOT` and the single-purpose slots (`watch`, `stream_volunteer`, `reschedule`, `hard_preset`) stay registered for table-layout boards that declare them.

**Data flow** — `refresh()` calls `MatchDisplayService.get_matches_for_display(tournament_ids, stage_ids, only_upcoming, user_discord_id, crew_discord_id, exclude_racetime, match_ids)` (`only_upcoming` is a fast path taken when the State filter excludes Finished and Confirmed), applies the state filter client-side (the day scope already went to the database as `scheduled_window`), merges per-row `_watching` flags from `MatchWatcherService.list_watched_match_ids`, `_stream_volunteer` from `MatchStreamVolunteerService.list_volunteered_match_ids`, `_can_reschedule`/`_reschedule_pending` from `MatchRescheduleService`, and — only on a board declaring a `hard_preset` or `my_actions` column (`_shows_column`) — the viewer's own harder-preset state from `MatchHardPresetService.board_states` (projected by `theme/tables/hard_preset_rows.py`; bulk queries, never per row), attaches `stage_options`, then applies `row_filter` and `row_sort` (if given) and notifies `on_rows_changed`. `update_row_by_id(match_id)` re-fetches one row via `get_match_for_display` (deleting the row if the match is gone, and carrying every `_`-prefixed per-viewer field across via `theme/tables/row_state.carry_viewer_row_state`, so a field added later is not silently dropped); `delete_row_by_id` removes a row from the UI only.

**Cell rendering** — the players cell shows acknowledgment icons (green check / orange clock with timestamp tooltips, `(auto)` markers) and bolds the winner; admin mode also shows each player's station in italics. Commentator/tracker cells colour names by approval. The admin Seed column shows a Generate button (with client-side `_generating_seed` spinner state) when the tournament has a seed generator and no seed exists; existing seeds render as truncated links. The admin State column renders the next lifecycle action per state — Scheduled → Check In, Checked In → Start, Started → Finish, Finished → Confirm — each with the previous transition's timestamp; Confirmed renders an icon + timestamp. The Stage column shows an Assign button for unassigned matches and an amber "candidate" badge for stream candidates.

**Event wiring** (table and grid templates emit the same events; `_event_match_id` normalizes the `{key}` / `{row}` payload shapes):

| Event | Emitted by | Handled by |
|---|---|---|
| `edit_match` | Edit pencil / `#id` link | `on_edit(match_id)` callback |
| `roll` | Generate button (Seed column) | `on_generate_seed(match_id)` |
| `seat` / `start` / `finish` / `confirm` | State-column lifecycle buttons | matching `on_*(match_id)` callback |
| `edit_result` | Change-winner pencil (`access.confirm`) | `on_edit_result(match_id)` |
| `set_stage` | Stage-column select | `on_set_stage(match_id, stage)` — `stage` is a `Stage` id, `'candidate'`, or `None` |
| `assign_stations` | Players-column button (admin + crud) | `on_assign_stations(match_id)` |
| `view_commentator` / `view_tracker` | Crew name link (admin + crud) | opens `UserDialog` for that crew member |
| `toggle_commentator` / `toggle_tracker` | Crew approval toggle icon (admin + crud) | `ConfirmationDialog` (both directions) → `CrewService.update_crew_approval`, row refresh |
| `signup_commentator` / `signup_tracker` | Sign Up button (non-admin) | `ConfirmationDialog` → `CrewService.signup_crew` |
| `undo_commentator` / `undo_tracker` | Undo button (non-admin) | `ConfirmationDialog` → `CrewService.undo_crew_signup` |
| `acknowledge_match` | Player's own Acknowledge button | `MatchService.acknowledge_match` (client captured via `context.client`, restored with `with client:`) |
| `acknowledge_commentator` / `acknowledge_tracker` | Crew member's own Acknowledge button | `CrewService.acknowledge_crew_assignment` |
| `toggle_watch` | Watch column button (logged-in) | `MatchWatcherService.watch` / `unwatch`, flips `_watching` locally |
| `request_reschedule` | **Ask to change** in My Schedule's ⋮ menu (a player in that match, row `Scheduled`) | Opens `RescheduleRequestDialog`; on submit flips `_can_reschedule`/`_reschedule_pending` locally so the button gives way to the **Change requested** chip without a reload. A staff decision arrives as a `match_live` push, and `update_row_by_id` recomputes the pair for that row (`_restamp_shared_state`), so approval brings Ask to change back without a reload too |
| `toggle_stream_volunteer` | **Offer for stream** in My Schedule's ⋮ menu (a player in that match) | `MatchStreamVolunteerService.volunteer` / `withdraw`, flips `_stream_volunteer` and patches `stream_volunteers` locally |
| `open_hard_preset` | **Harder settings** in My Schedule's ⋮ menu (a player in that match) | Opens `HardPresetDialog`; on change re-projects the row's harder-preset fields |
| `open_bracket` | Bracket-ref link under the tournament name | `ui.navigate.to` the bracket view |

Crew signup/undo and acknowledge buttons only render for the logged-in user's own entries; the templates compare row data against the user's `discord_id`, interpolated into the slot HTML at build time. See [../features/match-participation.md](../features/match-participation.md).

**Grid mode** — `match_grid.render_grid_slot()` generates a label/value card per row from the column list (JS field descriptors built in Python), re-implementing the ID link, acknowledgment icons, crew signup/acknowledge buttons, lifecycle state buttons, seed generation/links, watch toggle, stream-volunteer button, and stage assignment for mobile, with board-kind and capability behaviour baked in via template interpolation (`__IA__`, `__RUN__`, `__CONFIRM__`, `__CREW__`, `__STREAM__`, `__DID__`, `__WATCH__`, `__VOL__`, `__VOLACT__`, `__HARD__`, `__ACTCLS__`). Card order is headline → players → caption → details → actions; with `actions_first=True` it becomes headline → players → actions → caption → details, and the actions row gains `mgc-actions--first`, which moves its divider from top to bottom.

**Overdue rows** — `MatchDisplayService` puts `is_overdue` (scheduled time passed, not checked in, not finished) and `scheduled_ts` (an epoch sort key; the formatted `scheduled_at` string does not sort) on every row. The desktop `scheduled_at` cell and the mobile card headline tint the time amber and add a clock icon when it is set; `scheduled_ts` is what `row_sort` implementations order within a bucket.

**The Day filter** — `Day` sits first in the filter row: `All dates` (the default — nothing is hidden until asked), `Today`, `Tomorrow`, `Next 7 days`. Admin → Match Schedule opens on `Today` instead (`default_day_scope`): at 1,300 matches `All dates` sent every row, about 2MB, on each open. The scope is a database filter (`scheduled_window` on `MatchRepository.get_all`), not a client-side one. `day_scope_window()` resolves the scope on the *display* clock through `local_day_bounds`, so "today" follows the viewer, and the window is half-open. It counts toward the mobile filter badge and is suspended under a deep link, for the same reason the State filter is: a link to one match must not land on an empty board.

### Match board capabilities (`theme/tables/match_access.py`)

`MatchBoardAccess` is what the board may **offer**, one frozen field per gate in `AuthService`:

| Field | Gate | Controls |
|---|---|---|
| `edit` | `can_crud_match` | the id link, the match dialog, Create Match |
| `run` | `can_run_match` | Check In, Start, Finish, Generate, Stations |
| `confirm` | `can_confirm_match` | Confirm, Record/Change Winner |
| `assign_stream` | `can_assign_match_stream` | Assign (stage) |
| `approve_crew` | `can_approve_crew` | the crew approval toggle and name link |

Rows refine the page-level answer. Each capability has a scope (`edit_scope`, `run_scope`, `confirm_scope`, `stream_scope`, `crew_scope`; `None` = every row) and `run_on_stage` adds any match on a stage to `run`. `for_roles(..., admin_tournament_ids=, crew_tournament_ids=)` fills them for a non-staff viewer: a Broadcast Tech runs only staged matches (`run_on_stage_only`), a Broadcast Tech who is also a tournament admin edits and confirms only their tournaments' rows, and a TA of A who coordinates B runs A and approves crew on both. `can_*_row(row)` gates the Python controls (sheet, inbox) and `run_js()`/`confirm_js()`/`crew_js()`/`stream_js()`/`edit_slot_js()` fill the slot templates, reading `props.row.tournament_id` and `props.row.stage_id`.

These are **page-level approximations of per-match gates** — a tournament admin's board answers "may you do this to *some* match here", and the service re-checks the actual match on every call. That is deliberate: the board is the cheap filter, the service is the enforcement. It is also why a TA's or coordinator's board is *scoped* to the tournaments they run.

It replaced a single `can_crud = is_staff or is_ta_any`, which stood in for all five. The measured consequence: a crew coordinator was handed 37 lifecycle controls that every service refused and none of the one they were authorized for, and the STREAM_MANAGER — the role `assign_stage` names in its own docstring — had no surface that assigned a stage. `tests/theme/test_match_board_access.py::test_every_capability_matches_the_service_gate` pins each field to the `AuthService` predicate that decides the same question, for every role the board admits.

`MatchLifecycleHandlers.callbacks()` hands over one group per field. The slot templates branch on `__RUN__` / `__CONFIRM__` / `__CREW__` / `__STREAM__`, baked to `true`/`false` at registration — so *"is this control for me?"* is answered by reading the guard, never by whether the markup is present (`_guard_on` in the slot tests exists for that).

### `TournamentTableView` (`theme/tables/tournament.py`)

- Toolbar: optional **Add tournament** button (when `submit_tournament_callback` is provided); with a `table_key`, a search box, an **Actions** menu (Export CSV) and the preferences gear; then refresh. On a phone the search box and refresh drop to a second line.
- Slots: name link (emits `edit_tournament` → `TournamentDialog`), player-count link (emits `show_players` → `TournamentPlayersDialog`), a signup chip, and a ⋮ menu (`slot_more_menu`) with Edit, Players, Open bracket / Open rules (when the tournament has that link) and, with `can_delete`, Delete for an inactive tournament (`confirm_tournament_delete`, shared with the edit dialog's Danger zone). The phone `item` card shows the same four fields and the same ⋮.
- `refresh()` annotates the player count in SQL and builds each row with `_tournament_row`; `update_row_by_id` refreshes one row.

### `UserTableView` (`theme/tables/user.py`)

- Optional toolbar (`show_toolbar=False` lets the caller render its own, as the admin Users tab does).
- Slots: clickable Username (emits `edit_user` → `AdminUserDialog`), Active icon, truncated Discord ID, linked Challonge account, and Roles rendered as `q-chip`s.
- `refresh()` orders by username and prefetches `roles`, `admin_tournaments`, `crew_coordinated_tournaments`; `_format_user_row` title-cases role names and appends `TA(n)` / `CC(n)` markers for tournament-scoped roles. Timestamps format through `format_local_display`.

### Shared kit (`admin_crud.py`)

The helpers every service-backed admin tab uses:

- **`capture_render_context()`** / **`scoped_background(context, coro)`** — snapshot the tenant, display timezone and table layouts during the page build, and rebind all three around a background task. A detached task has none of them; without the zone a refresh repaints on the fallback clock, and without the layouts a customized table reverts to its shipped columns.
- **`wire_tab_refresh(tab_name, refresh)`** — the piece with a correctness reason to exist. It captures that render context during the page build and rebinds it around the refresh, because `selected_tab` is a client event whose handler runs in a detached background task that has lost the contextvars; without the rebind a scoped read raises `require_tenant_id()` (swallowed) and the tab never repaints.
- **`refresh_button(refresh)`** — see [The admin toolbar's Refresh control](#the-admin-toolbars-refresh-control).
- **`current_actor()`** — the logged-in `User`, or `None`.

The module previously also carried a `ServiceTableView` generic plus page-container, toolbar and actions-slot helpers, and a sibling `equipment.py` kit. Both were extracted in anticipation of adoption that never happened — after two audits recommending it, neither had a single importer — so they were deleted rather than left as an optional style choice. Recover them from git history if a genuine second caller appears.

## Data tables

Every table in the app shares one shape: a desktop `<table>`, a card view below
`lt.md`, and a per-viewer column layout. The three rules below are enforced by
guardrail hooks and source-scanning tests, because each one fails silently.

| Name | Where | Purpose |
|---|---|---|
| `enable_mobile_grid(table, columns, …, table_key=…)` | [`theme/tables/mobile_grid.py`](../../theme/tables/mobile_grid.py) | The chokepoint. Generates the mobile card slot, then applies the viewer's saved layout — in that order, which it owns |
| `customize_table(table, defaults, key=…)` | [`theme/tables/preferences.py`](../../theme/tables/preferences.py) | Applies a saved layout directly, for a table with a bespoke `item` slot. Call **after** that slot |
| `effective_columns(defaults, saved, required)` | same | The pure reconciler: order, visibility, widths, page size, density, wrap |
| `preferences_button(table)` | same | The gear that opens the Preferences modal; hidden below 1024px |
| `search_input(table)` | same | A box bound to Quasar's real `filter` prop |
| `row_count_label(table, noun)` | same | Live `124 matches` caption over a client-paginated board |
| `sticky_header(table)` | same | Bounded height + sticky `<th>`; the two only work together. The cap applies only outside grid mode (`:not(.q-table--grid)`), because nothing scrolls a phone grid's cards — capped, they overflowed past the third card |
| `csv_export_button(prefix, columns, rows)` | [`theme/tables/export.py`](../../theme/tables/export.py) | Downloads what is on screen — pass the plan's columns, not the defaults |
| `TableKeys` | [`theme/tables/preferences.py`](../../theme/tables/preferences.py) | Every table's stable key, namespaced `surface.table` |
| `apply_column_visibility(table, columns)` | [`theme/tables/mobile_grid.py`](../../theme/tables/mobile_grid.py) | Turns this app's `hidden` convention into Quasar's `visible-columns` |

**Call order is the mobile-card guarantee.** The card's `item` slot is a static
Vue string built from the *defaults* at build time, addressing `props.row.<field>`
directly — it never reads `table.columns` or `visible-columns`. So a saved layout
cannot reach it, provided `customize_table` runs *after* the slot is registered.
`enable_mobile_grid` owns both calls so a caller cannot get it wrong;
`test_preferences_do_not_touch_the_mobile_card` and an AST order scan pin it for
the bespoke tables that call it themselves.

**Resolution is async; reading it back is sync.** `BaseLayout.render()` awaits
`TablePreferenceService().prime(user)` once — one query for every table on the
page — and binds the result in `application/table_preferences_context.py`. Each
`customize_table` call reads it back synchronously, because a table build cannot
await. Two carriers rebind it or the layout silently reverts: the lazy tab build
(`BaseLayout._build_tab`) and `admin_crud.capture_render_context()`. An unprimed
context yields the shipped columns, which is a correct answer, not a failure.

**Defaults win for columns nobody has seen.** A saved list is a filter *over* the
current defaults, never a replacement: a default absent from it is appended,
visible. The opposite rule makes every newly added column invisible forever to
anyone who ever opened the gear. Required columns (`actions`, `edit`) cannot be
hidden, and a config that would leave nothing visible is discarded wholesale.

A new table needs a `table_key` or a `# table-prefs: exempt — <reason>` comment;
[`check_table_prefs.py`](../../.claude/scripts/check_table_prefs.py) and
`tests/theme/test_table_preferences_coverage.py` enforce both halves.

**Rows per page lives in the table footer, and only there.** `customize_table`
gives every customized table Quasar's footer with `ROWS_PER_PAGE_OPTIONS`
(10, 25, 50, 100, All — the same set `TablePreferenceService.ALLOWED_PAGE_SIZES`
accepts) and saves a pick there as the viewer's `page_size` for that table
(`set_page_size`). The footer stays on "All" too, since it is the only way back.
The Preferences gear has no page-size control: two controls that could disagree
is the bug this replaced. A table that pages on the server and draws its own pager
(the audit/telemetry event log) passes `pager=False`, which drops the footer and
shows every row it is handed; so does the check-in desk, which is search-first.
Ship only a size the footer offers — `test_every_shipped_page_size_is_one_the_footer_offers`
fails on anything else.

**A table with no `pagination=` renders every row it is given.** `page_size` falls
back to whatever the table was constructed with, and `ui.table(...)` with no
`pagination` argument means `0` — which Quasar reads as *all rows*. This is
not theoretical: the async-qualifier Runs tab shipped that way and put 3,129 rows and
43,714 DOM nodes into a **151,156-pixel** page, with the drill-down's own tab strip
scrolled far out of reach. Pass `pagination={'rowsPerPage': 25, 'page': 1}` (the
family boards' value) on any table whose row count is not bounded by construction,
and pair it with [`search_input`](../../theme/tables/preferences.py) — paging hides
the row someone came to read, so search is part of the bound rather than an extra.
Nothing enforces this yet; the guardrail that already checks `table_key` is the
natural place for it.

### Responsive tables — the mobile grid rule

**Every `ui.table` must have a mobile card view.** A bare HTML `<table>` overflows a phone: its columns and (worse) its row-action buttons scroll off the right edge. Quasar's *grid mode* fixes this — below a breakpoint the table renders an `item` slot per row instead of `<tr>`s. The mechanical guardrail [`check_table_grid.py`](../../.claude/scripts/check_table_grid.py) (PostToolUse) flags any presentation-layer `ui.table` that lacks it.

Two sanctioned ways to comply:

1. **`enable_mobile_grid(table, columns, …)`** ([`theme/tables/mobile_grid.py`](../../theme/tables/mobile_grid.py)) — the default for every admin / report / player table. It's column-driven: it adds `:grid="Quasar.Screen.lt.md"` and generates a `.wiz-grid-card` `item` slot from the same `columns` the desktop table uses (skipping `hidden` columns and the `actions` column). Pass `actions=` the **same** row-action button HTML used in the `body-cell-actions` slot (hoist it into one constant — don't duplicate), `field_slots={'status': '<q-badge …>'}` for any badge/chip/icon column (the snippet reads `props.row.<field>`, not the desktop cell's `props.value`), and `row_click_event='row-click'` to keep a whole-row drill-down tappable on mobile.

   ```python
   from theme.tables.mobile_grid import enable_mobile_grid

   table = ui.table(columns=_COLUMNS, rows=rows, row_key='id').classes('w-full wiz-table')
   table.add_slot('body-cell-status', f'<q-td :props="props">{_STATUS_BADGE}</q-td>')
   table.add_slot('body-cell-actions', f'<q-td :props="props">{_ROW_ACTIONS}</q-td>')
   enable_mobile_grid(table, _COLUMNS, actions=_ROW_ACTIONS, field_slots={'status': _STATUS_BADGE})
   ```

2. **A bespoke `item` slot** with an inline `:grid="Quasar.Screen.lt.md"` prop — reserved for the four purpose-built family tables (match / user / tournament / equipment), whose cards are too custom for the generic helper.

A table that legitimately must stay a table on mobile (a narrow 2-column reference) opts out with a `# mobile-grid: exempt` comment on the `ui.table` line. The generic card styling is `.wiz-grid-card` (the card counterpart of `.wiz-table`).

#### `hidden` columns, and why they need help

`{'hidden': True}` on a column is **this app's own convention, not Quasar's**. `QTable` has no such column property, so for a long time it did nothing on desktop: the mobile card skipped the column and the desktop table painted it anyway, which is how seven admin tables came to lead with a raw primary key on a wide screen while correctly hiding it on a phone.

`apply_column_visibility(table, columns)` translates the convention into Quasar's real mechanism, `visible-columns`. `enable_mobile_grid` calls it for you; a bespoke `:grid` table (path 2 above) calls it directly. The **row dicts are untouched**, which is the point — `props.row.id` stays available to every slot template and `row_key='id'` keeps working, so row-action handlers are unaffected by hiding the column they key on.

`tests/theme/test_admin_toolbar_wiring.py` fails a file that declares a `hidden` column without applying the visibility (or handing its columns to a `*TableView` that does).

The general lesson, worth applying beyond tables: **when you invent a flag a third-party component is also expected to honour, prove the third party honours it.** Half-honoured conventions fail in exactly one environment, which is the hardest kind to notice.

### The admin toolbar's Refresh control

Use [`refresh_button(refresh)`](../../theme/tables/admin_crud.py), never a hand-rolled `ui.button(icon='refresh', on_click=lambda: background_tasks.create(reload()))`.

The hand-rolled form reads fine and does nothing. A task spawned from a *click* has neither the tenant contextvar nor the client stash `get_current_tenant_id` falls back to, so the first scoped query inside raises, NiceGUI logs it, and the button fails **in silence** — eleven of the fifteen admin refresh controls were dead this way, and their server-side errors were worse than the silence (a staff member's click logged *"Only Staff can view webhooks"*; a community with SpeedGaming enabled logged *"not enabled for this community"*, because the actor and the flags both resolve against no tenant).

`refresh_button` captures the render context during the page build, where it is still live, and rebinds it around the call. It takes a **callable**, not a coroutine, and invokes it inside the task — which is also what makes it correct for a `@ui.refreshable`'s `.refresh()`, an `AwaitableResponse` that must be awaited immediately or not at all. Pass `lambda: view.refresh()` where the toolbar is laid out before the thing it refreshes exists.

```python
from theme.tables.admin_crud import refresh_button

with ui.row().classes('full-width'):
    ui.button('Add Thing', icon='add', on_click=…).props('color=primary')
    ui.space()
    refresh_button(refresh_table)          # or: refresh_button(lambda: _render_table.refresh())
```

`test_no_tab_hand_rolls_a_refresh_button` fails the hand-rolled shape. The one sanctioned exception is a board whose loader is genuinely tenant-agnostic (the `/platform` health probe), which opts out with a `# refresh-context: exempt` comment — the same idiom as `# mobile-grid: exempt`.

The sibling defect — reading `context.client` from *inside* a background coroutine, where it raises rather than returning `None` — is caught by [`check_slot_context.py`](../../.claude/scripts/check_slot_context.py)'s second check. Capture it at the call site and pass it in.

## Offline honesty (`theme/connection.py`)

`install_connection_watch()` — called by `BaseLayout.render_chrome()`, so every themed page has it. It injects one guarded `<script>` that gives the page a visible connection state and refuses the controls that cannot work without one.

Why it is client-side: while the socket is down the server can say nothing. `ui.notify`, a `refreshable.refresh()` and a disabled-state update all travel over the same dead socket. So detection, the card and the refusal all live in the browser, and the acceptance test is what an operator sees with the network off.

| Piece | Behaviour |
|---|---|
| Detection | `window`'s `offline`/`online` **and** `window.socket`'s `disconnect`/`connect`. The window events fire the instant a link drops; the socket events cover a live link to an unreachable server, where `navigator.onLine` stays `true`. `window.socket` is assigned in `nicegui.js`'s `mounted()`, so the binding retries briefly instead of assuming it exists |
| Card | One charcoal card (`.wiz-connection-card`, `role="status"`) pinned top-centre, 10px under the header, the same spot toasts open in. Not a toast: a toast fired while the phone was pocketed has expired by the time anyone looks. Top rather than bottom because a phone dialog is a bottom sheet, and a bottom card covers its Save button. Copy: *Connection lost* / *Anything you change now won't be saved.* / spinner *Trying to reconnect…*, then *Back online* for two seconds |
| Body classes | `wiz-offline` on `<body>`, the single hook the CSS and the click guard both read; `wiz-connection-card-shown` plus `--wiz-connection-card-h` while the card is on screen, which push `.q-notifications__list--top` below it so a toast fired offline stacks under the card instead of over it |
| Refusal | A capture-phase `click` listener cancels any control carrying `wiz-requires-socket` and explains with `Quasar.Notify.create` (client-side, so it still works): *"No connection, so that wasn't sent. Try again once you're back online."* The CSS deliberately does **not** set `pointer-events: none`; the tap has to reach the listener to be explained |

The framework's own `#popup` ("Connection lost / Trying to reconnect") is hidden in `styles.css`. It shows on the same socket `disconnect` the card reads, only later (`ping_interval + ping_timeout`, then a 2 s `transition-delay`), so it would only repeat the card. `#too_long_message_popup` keeps the framework look.

Marking a control is presentation's job: `.classes(REQUIRES_SOCKET_CLASS)` on a `ui.button`, or `class="wiz-requires-socket"` in a Quasar slot template. Mark anything whose effect is a round trip — including a "View"/"Open" button, whose navigation is a server `open` message rather than an `<a href>`. Do **not** mark what works offline: a `ui.download` of a data URI already in the page, or a `window.print()`.

## Static assets & styling (`static/`)

`static/` is served at `/static`. Every stylesheet and script the app links goes through **`theme.assets.asset_url(path)`**, which appends the file's mtime as `?v=<stamp>` — the URL changes exactly when the file does, so a deploy reaches a browser or proxy holding the previous copy. Skipping it is a real failure and not a visible one: `brackets.css` was linked bare, and a fix for group cards overflowing the page went unseen for days while the current markup rendered against the old rules. `VersionedStaticFiles` pairs with it — a stamped URL is `immutable` for a year, an unstamped one revalidates every time, and development stores nothing. `/sw.js` registers with `updateViaCache: 'none'` so the HTTP cache cannot answer for the worker's `importScripts`. `tests/theme/test_static_asset_versioning.py` scans `theme/` and `pages/` for a bare `/static/*.css|js` link. It holds the two stylesheets, the PWA assets ([`manifest.webmanifest`](../../static/manifest.webmanifest), `icons/`, `fonts/`), [`sw.js`](../../static/sw.js) (served from the site root `/sw.js`; a no-cache pass-through worker that satisfies Chrome's installability requirement and renders Web Push messages on browsers without Declarative Web Push — see [../features/web-push.md](../features/web-push.md)), [`js/web-push.js`](../../static/js/web-push.js) + `js/web-push-common.js` (shared with the worker's `importScripts`), `js/table-columns.js` (drag-to-resize table headers, linked by `render_chrome`), `js/report-nav.js` (the Reports tab's scroll keeper), and `triforce.svg`.

- [`css/styles.css`](../../static/css/styles.css) — the app stylesheet, loaded by `BaseLayout.render_chrome()` and `render_platform_chrome()`. Organized into class families (layout containers, headers/text, cards/forms/dialogs/buttons, the stage columns (Watch page), the four table families + their grid cards, dark-mode overrides, report chart sizing, the OAuth consent card) with section comments; grep the family prefix rather than a per-page rule.
### Design tokens (the Studio system)

The rules for *using* these (what to reach for, what not to do, and how a change is checked) are in [../style-guide.md](../style-guide.md); this section is the mechanics.

Every page reads the same small set of tokens from the top of `styles.css`, and new styling should too rather than reaching for a hex value or a Quasar colour name.

| Token family | Values | Used for |
|---|---|---|
| Neutrals | `--n-0` … `--n-900`, aliased as `--page-bg`, `--surface`, `--surface-alt`, `--surface-sunken`, `--border-subtle`, `--border-strong`, `--fg`, `--fg-secondary`, `--fg-muted` | Everything that is not an action or a status. The aliases flip under `.body--dark`, so a rule written once serves both modes |
| Accent | `--accent`, `--accent-soft` (the tenant's primary/accent) | Filled buttons, links, the active nav icon, focus rings. Not titles, table headers or timestamps |
| Status | `--status-ok` / `-pending` / `-live` / `-neutral` / `-cancelled` / `-info` | `.wiz-chip--*`, `.wiz-banner--*`, `.st-*` |
| Type | `--font-body` (Atkinson Hyperlegible, also titles), `--font-mono` (Plex Mono, times and IDs); `--text-xs` 12 · `-sm` 13 · `-md` 14 · `-lg` 16 · `-xl` 22 px | `.page-title` is `-xl`, `.section-title` and `.wiz-panel__title` are `-lg`. `--font-display` survives only as an alias of `--font-body` |
| Shape | `--radius-sm` 6 (chips), `-md` 8 (buttons, inputs), `-lg` 12 (cards, panels, dialogs); `--shadow-1..3` | Borders separate; shadows only lift |

Shared component rules that come with it: every Quasar "standard" (underline) field is drawn boxed to match the outlined ones; table headers are muted sentence case with no zebra striping; `.wiz-chip` is the one badge (tinted, sentence case); `.wiz-segmented` selects with a raised white tile; tab panels are transparent so cards sit on the page canvas. [`theme/banner.py`](../../theme/banner.py)'s `banner(message, title=, tone=)` context manager is the standard full-width notice (`info` / `ok` / `warning` / `danger`, with an action slot) — use it rather than a hand-coloured row.

- [`css/brackets.css`](../../static/css/brackets.css) — the bracket renderer's geometry and colour, linked per page by `theme/brackets/`. Its pixel constants mirror `theme/brackets/layout.py`; change one, change both.

Its neutrals (`--bracket-line`, `-card-bg`, `-text`, `-muted`, …) point at the app tokens above, so only the status tones carry a dark override. The one non-obvious rule: `--bracket-*` is declared on **`body`**, not `:root`, because `ui.colors()` writes the tenant `--q-primary` onto `body` while Quasar's stock blue sits on `:root`.

## NiceGUI patterns

Canonical rules: [`CLAUDE.md`](../../CLAUDE.md) § NiceGUI patterns. Worked examples here: `MatchTableView._bg` (background tasks), `theme/realtime.py` (client capture), `BaseLayout._build_tab` and `admin_crud.wire_tab_refresh` (deferred work rebinding the tenant), `theme/tables/match.py` (`tenant_session_get`/`set` for per-user-per-tenant state), the triforce-text lists (`@ui.refreshable`).

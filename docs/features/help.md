# In-App Help

A public `/help` section plus tappable help icons scattered through the
player-, crew-, volunteer- and proctor-facing surfaces. The everyone articles are
written for the people who turn up to an on-site tournament. Staff actions are
explained there from the outside ("staff approve your signup", "a proctor
fetches an admin") so a reader understands why they are waiting on something,
without documenting an admin surface they cannot reach. The admin surfaces get
their own articles in a **"For staff"** group (see [Staff articles](#staff-articles)),
which nobody else can see.

Help is **app mechanics only**. It used to carry a second thing: the proctor and
player articles also held **room procedure the code does not encode** — who runs
the countdown, what to do about a missing player, spectators, hardware failures,
clearing the seat. That content came from the community rather than the source
and went stale on the event's clock, so it now lives in
[Event Information](event-information.md), a per-community handbook behind its
own flag.

What that leaves here: `player.md` covers My Schedule, acknowledging,
booking a match, seeds, race rooms, change requests, availability and results; `proctor.md` covers the Proctor
Station board, the four steps of running a match, and recording a result;
`on-air.md` covers the Watch page and watching a match, and defers what a
room allows in person to the event. All three carry a pointer to Event
Information written **without a link** — a community without the flag has no
`/event-info` for one to reach.

The rule for a new paragraph: if it is true for every community, it goes here.

## Where the content lives

`application/help/content/*.md`, one file per article, in the repo, reviewed in PRs.

There is deliberately **no article table or count here**. Every help PR used to
append a row to one and bump the other, so parallel help PRs conflicted on this
file every time one merged. Each article's front matter (`title`, `summary`,
`feature`, `audience`, `roles`) is the catalogue; list it with:

```bash
grep -H -E '^(title|feature|audience|roles):' application/help/content/*.md
```

The same rule holds for the other lists help used to keep by hand: the
screenshot manifest is one file per article (see [Screenshots](#screenshots)),
the help icons are found by grepping (see [Help icons](#help-icons)), and a
changelog line is a file of its own (see [changelog.md](changelog.md)). A help
PR should only need to touch files that belong to its own articles.

The workflow for writing one, and the lessons from the first round of help PRs,
are in the [`help-article`](../../.claude/skills/help-article/SKILL.md) and
[`help-screenshots`](../../.claude/skills/help-screenshots/SKILL.md) skills.

### Article format

```markdown
---
title: Signing up as crew
slug: crew
icon: record_voice_over
order: 30
feature: VOLUNTEERS        # optional — omit for an ungated article
audience: staff            # optional — see "Staff articles"
roles: STAFF, VOLUNTEER_COORDINATOR   # optional — only holders may read it
summary: One line for the index card and the search index.
---

## A heading            <!-- becomes the anchor `a-heading` -->

Text with **bold**, *italic*, `code`, [a link](/help/player), a
{chip:pending:Awaiting approval} status chip and a {state:Confirmed} match state.

![Alt text a screen reader reads](/static/help/crew/my-crew-phone.webp "Optional caption")

:::snippet crew-status
Content a help icon elsewhere in the app also shows.
:::
```

A `:::snippet` region is **not** a second copy of the text — the markers are
stripped and the region renders as ordinary article content. That is what stops
a popup and its article drifting apart. Snippet names are global; a duplicate is
logged and the first wins.

An unknown `feature:`, chip tone, or state name degrades rather than raising: a
typo in an article header must not take the help page down.

## The safe document model

Articles are **never** rendered through `ui.markdown`. They are parsed into a
closed vocabulary — seven block kinds, seven span kinds
(`application/content/blocks.py`) — and each one is drawn with a native NiceGUI
element (`theme/help/render.py`). There is no path from article text to markup,
so an article can only produce elements the renderer itself creates.

The parser and the directory loader live in `application/content/` because
[Event Information](event-information.md) shares them; `application/help/` is a
thin binding that supplies its own root and re-exports the names its callers
already used.

This is not defensive theatre against the current authors. It is what makes the
surface safe by construction if the content ever becomes editable, and it is the
same hazard `check_markdown_xss` exists to stop. Two consequences worth knowing:

- **Link targets are allow-listed** (`/…`, `#…`, `https://`, `mailto:`).
  Anything else — `javascript:`, `data:`, plain `http://` — renders as the label
  in plain text, with no anchor.
- **Prose tables are hand-built**, not `ui.table`: their cells carry chips,
  icons and links, and they are five-row explanations rather than sortable data
  grids. They scroll inside their own `overflow-x` container.

- **Image sources are allow-listed tighter still.** An image is a line of its
  own, `![alt](/static/help/<slug>/<name>.webp "caption")`, and only that tree
  (one directory per article, `.webp` or `.png`, no `..`) is accepted, with alt
  text required. Anything else renders as the line the author typed. The image
  is lazy-loaded, height-capped in the column (a phone shot is taller than the
  column is wide), tap-to-zoom in a dialog, framed and slightly dimmed in dark
  mode. See [Screenshots](#screenshots).

Chips use `{chip:tone:Text}` with a **colon**, not a pipe, so they can live
inside the tables that explain what each status means.

`{state:…}` renders the board's own icon and colour class
(`STATE_STYLES`, mirroring `theme/tables/match_slots.py`), so the help shows the
reader the thing they will actually see rather than a stylised stand-in. A test
asserts the five names match `LEGACY_STATE_LABELS`.

## Feature gating

Help itself has **no** feature flag — it documents shipped behaviour, and there
is nothing to gate. Individual *articles* declare a flag, and `HelpService`
withholds them where the community has the feature off:

```python
from application.services import HelpService

await HelpService.list_articles()          # only what is live here
await HelpService.get_article('crew')      # None if missing *or* gated
await HelpService.get_snippet('crew-status')
await HelpService.search('crew withdraw')  # AND over terms
```

A gated article and a missing one give the **same** answer, deliberately: a
reader following a stale link should not learn which optional features their
community has turned off.

**Links to a gated article are pruned, not offered.** Articles link to each
other without knowing where they are read, so `getting-started`'s "depending on
why you are here" table pointed at `volunteering` and `proctor` in communities
where both are gated off. `HelpService.get_article` / `get_snippet` (and
`EventInfoService`'s, which call the same `HelpService.prune_links(blocks,
user)`) run the blocks through `application.content.prune_links` against the
articles this reader can open here: every such link — running text, list
items, table headers and cells — keeps its words and loses the anchor. Nothing
else is removed. A trailing slash or a query names the same article. Only
sections the blocks actually link into are looked up, so a snippet with no links
costs nothing, and `help_icon` resolves the viewer for a snippet that links into
the handbook so a role-gated link is not pruned for staff.
`tests/test_article_links_resolve.py` follows every internal link of every
article a reader is handed, in each shape of community (all flags, none, handbook
without volunteers) and for each kind of reader of the `sgl26` handbook. `help_icon` renders **nothing** when its snippet
resolves to `None` — an icon that opens an empty popup is worse than no icon.

**One known consequence.** `proctor-result` explains the *Flag for admin review*
checkbox, and it lives in the `VOLUNTEERS`-gated proctor article. The dispute
flag itself is ungated (it self-gates — a community that never ticks the box
never sees it), and `can_run_match` admits staff and tournament admins as well as
proctors. So in a community with volunteers switched off, a staff member
recording a result gets the checkbox with no help icon beside it. That is
consistent with the scope call above — the help is written for non-staff — and it
degrades to *no icon* rather than to a broken one. Writing a staff track is what
fixes it.

## Staff articles

Two front-matter keys gate an article by **reader**, on top of `feature:`:

| Key | Who may read it |
|---|---|
| `audience: staff` | Staff, a tournament admin or crew coordinator here, or a super-admin (`AuthService.can_view_admin`). The other admin roles (volunteer coordinator, equipment manager, broadcast tech, preset/sync/qualifier admin) do **not** pass it |
| `roles: A, B` | Only holders of one of those roles in this community; a super-admin always qualifies |

Use `audience: staff` for anything a tournament admin or crew coordinator also
works from (Admin → Match Schedule above all), since those are per-tournament
assignments, not roles. Use `roles:` alone for a topic owned by a role
(volunteer coordination is `STAFF, VOLUNTEER_COORDINATOR`, presets `STAFF,
PRESET_MANAGER`). Both together means both must pass, which would shut a
volunteer coordinator out of their own article, so don't combine them unless
the role list is only `STAFF`.

Either key puts the article in the **"For staff"** group, which
`HelpService.list_articles(user)` orders last and the index and sidebar label.
For anyone who fails the gate the article is absent everywhere: the listing,
search, a snippet's help icon, and by URL (the not-found page, the same answer
as a missing or feature-gated article). Links to it from an everyone article are
pruned for them like any other unreadable link. Both keys fail **closed**: an
unknown role name is dropped and an unknown `audience:` value means staff.

`HelpService` only queries the reader's roles when some shipped article is
gated, and `help_icon` resolves the viewer itself for a snippet whose article is
gated, so pass `user=` where the surface has one.

## Screenshots

Committed under `static/help/<slug>/<name>.webp` (`static/` ships in the image;
`docs/` doesn't) and regenerated by **`scripts/help_screenshots.js`** from the
manifest, **`scripts/help_screenshots/<slug>.json`**: one JSON array per
article, so two PRs adding shots to different articles never touch the same
file. The script is the
[`/ui-validation`](../../.claude/skills/ui-validation/SKILL.md) loop pointed at a
manifest: against the seeded dev app it signs in as each entry's mock user, opens
the page, runs its setup steps, crops to its selector, and writes WebP (encoded
by Chromium, so it needs nothing beyond the global Playwright).

```bash
setsid ./start.sh validate > /tmp/app.log 2>&1 < /dev/null &   # then wait for startup
poetry run python scripts/seed_dev.py
NODE_PATH=$(npm root -g) node scripts/help_screenshots.js                  # every shot
NODE_PATH=$(npm root -g) node scripts/help_screenshots.js player            # one article's
NODE_PATH=$(npm root -g) node scripts/help_screenshots.js player/board-phone
```

An entry in `scripts/help_screenshots/player.json` (the file name is the slug
and the output directory, so entries carry no `slug`):

```json
{
  "name": "board-phone",
  "user": "player_one",
  "path": "/home/schedule",
  "viewport": "phone",
  "selector": ".wiz-panel",
  "setup": [{ "click": "text=Request a match" }, { "waitFor": ".q-dialog" }]
}
```

`user` is a seeded mock-login username (`null` for signed out); `path` is
tenant-relative (`tenant` defaults to `default`); `viewport` is `phone`
(390×844 at 2×, the default) or `desktop` (1280×800) or `{width, height}`;
`selector` crops to the first match with `padding` px around it (default 8;
omit it for the viewport, or set `fullPage`); `hide` lists extra selectors to
hide (they keep their space); `remove` lists selectors taken out of the layout
entirely, for something time-dependent, such as a shift banner, that would
otherwise leave a gap; `setup` steps are `click`, `fill` + `value`, `press`, `waitFor` and `wait`;
`annotate` lists numbered call-outs, `{ "selector", "n", "tab"? }`, drawn as a
red outline with a number tab over each control (see the `help-screenshots`
skill for when to use them and how the article refers to the numbers).
Shots are light theme, reduced motion, `America/New_York`.

House rules: **phone** for player, crew, volunteer, proctor and check-in desk
shots (that's where those readers are), **desktop** for staff; crop to the part
the paragraph is about rather than a whole page; the article's own directory
only. When a shot needs a state the seed doesn't create, extend
`scripts/seed_dev.py` (or its `seed_*.py` helpers) rather than clicking it into
being, so the next regeneration finds it too.

**Device shots** are the one exception: a picture of the phone's own UI (the
iOS Share sheet and Add to Home Screen dialog in `notifications`) can't be
rendered here, so it's a photo from a real phone, cropped and annotated by hand
in the same style. Its manifest entry carries `"device": "<what it shows and how
to retake it>"` instead of `path`, and the script skips it. Crop out anything
personal (contacts in the Share sheet, the header avatar, the wallpaper and other
apps on a home screen) before it's committed.

`tests/test_help_images_and_staff.py` holds the tree, the manifest and the
articles together: the manifest is one non-empty array per article, every image
an article shows exists, every manifest entry is shown by some article, every
file under `static/help/` is in the manifest, and a help article shows only its
own directory.

## The surfaces

`/help` and `/help/{slug}` are `@public_page` (`pages/help.py`). Help has to
work for someone who cannot get in, which is exactly when they most need it.
The only reader-specific content is which staff articles are listed.

- **Index** — a search box (AND over whitespace-separated terms, matched against
  title + summary + flattened body) over article cards.
- **Article** — a sticky sidebar listing every article plus the current one's own
  `##` headings, and the rendered body (`theme/help/article.py`, shared with
  `/event-info/{slug}`). Both stack below 1024px, and there the **body is ordered
  first**: the sidebar is ~550px of links, which in source order put the title
  below the fold and the first sentence most of a second screen down — on the
  surface most of these readers are on. Stacked, the article list reads as a
  "more help" footer instead. The reader's *own* contents do not go with it:
  below 1024px "← All help" and a collapsed **On this page** expansion sit above
  the title (`.wiz-help-narrow-only`), and the sidebar's copies are hidden
  (`.wiz-help-wide-only`). They used to land at the foot of the article —
  1,867px down a 2,468px page on `getting-started`.
- **Missing article** — the shared not-found page (HTTP 404, the community's
  chrome) with an **All help** button. Titles name the article and the
  community (`Getting started — Help — Default`).

**Prose is block flow, not flex.** `render_blocks` output goes inside a
`.wiz-help-prose` div on both surfaces, because the containers around it are
`ui.column`s and a flex parent breaks prose two ways: its `gap` adds to every
block's own margin (so a heading sits equidistant from the section above and the
sentence it introduces, and the hierarchy flattens), and its children shrink
below their content when the container is height-capped — which silently cut a
help popup's table off mid-row rather than letting the popup scroll. Block flow
restores margin collapsing and lets the tuned margins set the rhythm. The
article's copy is additionally capped at `56ch` (~80 characters) so a paragraph
does not track the full width of a desktop monitor.

**Entry point: the drawer.** The Help item sits below Event Information (when
that flag is live) and above Feedback, and is *not* gated on `self.user`, so a signed-out visitor on any framed public surface can
reach it — the join door and the error pages included, since they now carry the
community's drawer. The real `/login` is a bare redirect to Discord with no page
to hang a link on — the drawer is the way in. Off any community (the bare host)
there is no `/help` to reach, so the item is omitted rather than pointing at the
page it sits on; the community picker links each community's help instead.

### Help icons

**One "?" per heading or dialog title, never beside a control.** Every surface
gets at most one help button, the same round `help_outline` everywhere, at the
far end of its heading row (in a dialog, beside the close button). With one
topic it opens that snippet; with several it lists them by name and each opens
in place, with "Read more" into the article. This replaced up to six icons and
labelled chips per section in three different looks, which wrapped to two rows
on a phone.

```python
from theme.help import help_icon, resolve_help

await help_icon('my-shifts')                                  # one topic
await help_icon('vol-roster', 'vol-hours', user=actor)        # one button, two topics
await help_icon(('schedule-columns', 'Reading the board'), 'match-states')  # relabel one

# Sync headers take resolved topics:
dialog_header(title, dialog, help=await resolve_help(['suggest-time']))
with form_dialog(title, help=await resolve_help(['proctor-result'])) as dialog: ...
await section_panel('Your matches', icon=..., help_topics=['player-schedule', ...])
```

Where it goes:

- **Page or tab header**: call `help_icon` inside `with page_header(...):`. It
  moves itself to the end of the row (CSS `order`), after any actions.
- **Section panel**: `help_topics=`; the button ends the title row.
- **Dialog**: `dialog_header(..., help=)` or `form_dialog(..., help=)`.
- **A heading row you built yourself**: give the row `wiz-help-row` and call
  `help_icon` in it.

Topic help that used to sit beside a control (Suggest a time, the review flag,
a shift's Give back) is a topic in the enclosing dialog or page instead.

A topic's menu name is its snippet's heading, falling back to the article
title; pass `(name, label)` where that heading reads badly out of context
("The board", "My Schedule").

**A topic names a snippet, not a section.** It resolves against help first and
then the community's [event handbook](event-information.md), so the room
procedure that moved out of the player and proctor articles kept every topic it
had and the popup links to `/event-info/…`. Pass `user=` where the surface
already resolved one: handbook articles can be role-gated, and without it each
topic looks the viewer up itself. A topic the reader can't open is dropped; a
button with no topics left renders nothing.

A tappable button with a `q-menu`, not a hover tooltip: a tooltip is unreachable
on a touch screen, and this help is read on phones in loud rooms.

The menu is a popover with a fixed reading width (`min(24rem, 100vw - 2rem)`) on
desktop and, below 600px, the same `q-menu` restyled as a bottom sheet over a
dimmed page, matching the phone dialogs (`.wiz-help-menu` in `styles.css`).
Without a set width it shrank to the snippet's narrowest word. One topic opens
straight to its snippet; several open on a list of names, each swapping the
popover to that topic with **‹ Help** back to the list (not accordions, which
crushed the text beside a scrollbar). Every view has a Close in its title row.

The shared availability editor takes `help_snippet=` because its two callers
render identical UI over different data (only the Volunteer tab passes one;
Home's copy sits inside a section panel that already carries
`player-availability`).

There is no list here of where icons are wired; it went stale with every help
PR and conflicted between them. Ask the code instead:

```bash
grep -rnE "help_icon\(|resolve_help\(|help_snippet=|help_topics=" pages theme
```

## Tests

`tests/test_help_content.py`, `tests/test_help_service.py` and
`tests/test_help_images_and_staff.py`.

The one worth knowing about: **`TestPagesAndArticlesAgree`** parses `pages/` and
`theme/` for every topic passed to `help_icon`, `resolve_help`, `help_topics=`
and `help_snippet=` and asserts the
snippet exists **in either section**, plus that the `default` dev handbook
carries every one help does not own — otherwise the dev loop and the browser
sweep quietly lose icons. A renamed article silently turns its icons into nothing —
correct at runtime, useless for catching the rename. Nothing else catches it.
Internal `/help/slug` and `#anchor` links are checked the same way.

---
name: help-screenshots
description: >-
  Add, retake or fix the screenshots in /help articles (static/help/<slug>/,
  scripts/help_screenshots.js and its per-article manifests). Use when a help
  article needs a picture of a screen, when a UI change has made existing help
  screenshots stale, or when tests/test_help_images_and_staff.py fails. Boots the
  seeded dev app and captures with Playwright, so shots are reproducible rather
  than hand-made.
---

# Help screenshots

A help screenshot is never taken by hand. Each one is an entry in a manifest
that `scripts/help_screenshots.js` replays against the seeded dev app, so after
any UI change one command makes every picture current again. Full reference:
[docs/features/help.md#screenshots](../../../docs/features/help.md#screenshots).

## Where things go

| What | Where |
|---|---|
| Manifest entries for article `<slug>` | `scripts/help_screenshots/<slug>.json` (a JSON array) |
| Image files | `static/help/<slug>/<name>.webp` |
| Reference in the article | `![Alt text](/static/help/<slug>/<name>.webp "Optional caption")` on a line of its own |

One manifest file and one image directory per article is what lets several
help PRs add screenshots at once without touching the same file. Never add your
entries to another article's file, and never show another article's images
(the test refuses it). An Event Information handbook image uses the same tree,
keyed by the directory name.

## 1. Boot and seed

The `/ui-validation` loop, unchanged:

```bash
bash scripts/setup_env.sh                       # once per container
setsid ./start.sh validate > /tmp/app.log 2>&1 < /dev/null &
# wait for "Application startup complete" in /tmp/app.log
poetry run python scripts/seed_dev.py
```

Use `validate`, not `dev`: `dev` reloads on every login and a reload mid-capture
gives half-built pages. Stop with `./start.sh stop`.

Seeded mock-login users: `staff_user`, `proctor_user`, `sm_user`,
`player_one`…`player_four`, `outsider`, `super_admin`, `equip_manager`,
`cc_user`, `vc_user`. Tenants are `default` and `second`. Shots show the dev
seed's names ("Player One", "Wizzrobe Dev Tournament"); that's agreed, don't
rename the seed for a screenshot.

## 2. Find the state on screen first

Sign in as the reader's role in a browser (or a throwaway Playwright script)
and get to the exact state the paragraph describes. If the seed doesn't create
it (an upcoming shift, a flagged result, a refunded badge), **extend
`scripts/seed_dev.py` or the matching `scripts/seed_*.py`** so it does,
idempotently and tenant-scoped. Never click a state into being for one capture:
the next regeneration won't find it.

Pick a `selector` that crops to the part the paragraph is about. Prefer stable
hooks: an `aria-label`, a `.wiz-*` class, `:has-text("…")` on a heading the code
renders. Avoid Quasar's generated ids and nth-child chains.

## 3. Add the entry

This is `scripts/help_screenshots/volunteering.json`:

```json
[
  {
    "name": "my-shifts-phone",
    "user": "sm_user",
    "path": "/volunteer/my-shifts",
    "selector": ".page-container:has-text('Show all shifts')"
  },
  {
    "name": "release-phone",
    "user": "sm_user",
    "path": "/volunteer/my-shifts",
    "setup": [{ "click": "text=Can't make it" }, { "waitFor": ".q-dialog" }, { "wait": 500 }],
    "selector": ".q-dialog .q-card"
  }
]
```

Only `name` and `path` are required; the file name is the slug. Defaults:
`tenant: "default"`, `viewport: "phone"`, `padding: 8`. Other keys: `user`
(`null` = signed out), `viewport: "desktop"` or `{width, height}`, `fullPage`,
`hide` (extra selectors to hide, keeping their space), `remove` (selectors
taken out of the layout, for a banner that only shows at some hours), `timezone`, and `setup` steps (`click`,
`fill` + `value`, `press`, `waitFor`, `wait`).

### Numbered call-outs

When the paragraph tells the reader to press or look at a specific control, mark
it with `annotate`: a list of `{ "selector": "…", "n": 1 }`, in the order the
text mentions them. Each one is drawn at capture time as a 3px `#D92D20`
outline, padded 4px clear of the control, with its number in a red tab on the
outline's top-left edge. The tab moves to the bottom edge when there's no room
above (the control is at the top of the crop); force it with `"tab": "bottom"`
when a tab would sit on another mark or on text the reader needs. The crop
widens just enough to show a mark on a control at its edge. The selectors are
Playwright selectors, like `setup`'s.

```json
"annotate": [
  { "selector": "button:has-text('Sign up as commentator')", "n": 1 },
  { "selector": ".q-dialog .q-checkbox:has-text('Flag for admin review')", "n": 2, "tab": "bottom" }
]
```

Usually one to three marks. Leave overview shots unmarked. Refer to each number
next to the image: in the prose (`press **Assign** (2)`) when the paragraph sits
outside a `:::snippet`, otherwise in the image's caption, since a snippet also
shows in a help popup that has no picture. Say what each number marks in the
alt text too ("the Send button marked 3").

House rules:

- **Phone** (`390×844` at 2×, the default) for players, crew, volunteers,
  proctors and the check-in desk; that's where they read help. **Desktop** for
  staff/Admin shots.
- Name files `<what>-phone` / `<what>` so the viewport is obvious.
- Crop. A whole page is rarely what the paragraph is about.
- Crop a dialog with `"padding": 0`, or the dimmed page behind it shows round the
  edges.
- Hide the bottom nav (`"hide": [".q-footer"]`) on a phone shot whose content
  reaches the bottom of the screen, and the header (`.q-header`) on a crop taller
  than the screen, or they land on top of the content.
- Alt text says what the shot shows for someone who can't see it; the caption
  (optional) says what to notice.

## 4. Capture

```bash
NODE_PATH=$(npm root -g) node scripts/help_screenshots.js <slug>               # your article
NODE_PATH=$(npm root -g) node scripts/help_screenshots.js <slug>/<name>        # one shot
```

It prints `ok`/`FAIL` per shot and exits non-zero on any failure. Then **look
at every image** (Read the `.webp`): a shot can succeed and still show a
spinner, an empty list, a notification toast, or the wrong role's view.

Re-run the whole set (`node scripts/help_screenshots.js` with no argument)
only when a shared component changed; commit just the files that moved
(`git status static/help`).

## 5. Reference and test

Put the image line in the article right after the paragraph it illustrates,
then:

```bash
poetry run pytest -q tests/test_help_images_and_staff.py
```

It fails when an article shows a file that isn't committed, a manifest entry no
article shows, a file under `static/help/` with no manifest entry, a manifest
file that isn't a non-empty array of entries without `slug`, or an article
showing another article's directory.

Open the article in the running app (`/help/<slug>`) on a phone viewport and a
desktop one: the image should load, cap its height, and zoom on tap.

## When the UI changes

A PR that changes a screen shown in help retakes those shots in the same PR:
`grep -rl "<page path>" scripts/help_screenshots/` finds the entries, then
capture just those slugs. If the change removes what the shot shows, update the
article text too.

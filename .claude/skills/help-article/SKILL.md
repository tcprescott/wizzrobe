---
name: help-article
description: >-
  Write a new in-app help article or refresh an existing one (/help, the
  application/help/content/*.md files) — player, crew, volunteer, proctor, desk
  or "For staff" topics. Use whenever a task adds or rewrites help text, wires a
  help icon, adds glossary terms, or documents a feature for the people who use
  the app. Covers checking every claim against the code, gating (feature:,
  audience:, roles:), snippets and help icons, links, the glossary, screenshots,
  tests, and the file layout that lets several help PRs run in parallel without
  merge conflicts.
---

# Writing a help article

Help is for the people at the event: players, crew, volunteers, proctors, the
check-in desk, and (in the **For staff** group) whoever runs Admin. It explains
**app mechanics** that are true in every community. Event procedure (who runs
the countdown, what to do about a no-show, where the lanyards are) belongs in
the community's Event Information handbook, not here. Read
[docs/features/help.md](../../../docs/features/help.md) once for the parser,
gating and icon mechanics; this skill is the workflow and the lessons from the
first round of help PRs.

## 1. Read the code before writing a word

Every help PR in the first round found the old text wrong somewhere, and every
error came from describing the app from memory:

- The button was **Request Match**, not "Submit Match".
- Booking a bracket matchup is final; the old text said the opponent confirms it.
- The Proctor Station board shows every on-site match in the community, not
  "every match in your room".
- The schedule board's State filter **hides finished matches by default**.
- The public Watch page (`/live`) needs no account; On Air said it did.

So, for each surface the article covers:

1. Open the page module (`pages/…`, `theme/…`) and copy button labels, tab
   names, chip text and dialog titles **exactly** as the code renders them.
2. Open the service the page calls and read the gate: which role, flag or
   per-tournament assignment (`is_staff`, `can_run_match`, tournament admin,
   crew coordinator) unlocks each control. Don't tell a reader to press a button
   their role can't see.
3. Note what happens next: which DM goes out, who has to approve, what state
   the match moves to. Readers mostly open help to learn why they're waiting.
4. When the code surprises you (a default that hides things, a role that can't
   do what its name suggests), say so plainly in the article and mention it in
   the PR description so a reviewer can decide whether it's a bug.

Then, ideally, look at it in the running app (`/ui-validation`) as the reader's
role. The screenshot pass (`/help-screenshots`) does this anyway.

## 2. Front matter: who can read it

```markdown
---
title: Borrowing equipment
slug: equipment
icon: inventory_2
order: 65
feature: EQUIPMENT                 # FeatureFlag member; omit if ungated
audience: staff                    # staff/TA/CC articles; optional
roles: STAFF, VOLUNTEER_COORDINATOR # a role's own topic, instead of audience; optional
summary: One line for the index card and search.
---
```

- `feature:` hides the article (and prunes links to it) where the community has
  that flag off. Use the flag the surface itself is gated on.
- `audience: staff` admits staff, **tournament admins and crew coordinators**
  (per-tournament assignments, not roles), and super-admins, but not the other
  admin roles (volunteer coordinator, equipment manager, broadcast tech…). Use it for anything those people also work from, Admin →
  Schedule above all. `roles: STAFF` alone would shut tournament admins out.
- `roles:` narrows to holders (super-admin always passes). Use it **without**
  `audience: staff` for a role's own topic, say volunteer coordination (`STAFF,
  VOLUNTEER_COORDINATOR`) or Challonge (`STAFF`).
- A section for a narrower role inside a public article (the equipment desk
  inside "Borrowing equipment") is fine when the rest of the article is for
  everyone; a whole staff topic gets its own `audience: staff` article.
- `order:` sorts the index. Pick a gap between neighbours rather than
  renumbering other articles.

A gated article and a missing one look the same to a reader. Check both sides:
a test (see `tests/test_help_images_and_staff.py`'s staff cases) or the running
app as a reader who should and one who shouldn't see it.

## 3. Write it

Follow "Writing user-facing text" in CLAUDE.md. For help specifically:

- Lead each section with what the reader does, in the order they do it.
- Name controls the way the screen does, in *italics* or **bold**, and match
  case.
- Explain staff actions from the outside in everyone-articles ("staff approve
  your signup"); the staff articles explain the inside.
- `{chip:tone:Text}` and `{state:Name}` render the app's own chips and match
  states. Use them in tables that explain statuses.
- `## Headings` become anchors (`/help/crew#signing-up`). Renaming a heading
  breaks every link to it; `tests/test_article_links_resolve.py` will say where.

## 4. Snippets and help icons

A `:::snippet name … :::` region is ordinary article text that a help icon can
also show in a popup. Put the icon on the surface:

```python
from theme.help import help_icon
await help_icon('equipment-borrow', 'equipment-return', user=user)  # one "?" listing both
```

- Pass `user=` when the page already has one; gated snippets need it.
- One button per heading or dialog title, never beside a control. Section panels take `help_topics=`, dialogs `dialog_header(..., help=await resolve_help([...]))`, the availability editor `help_snippet=`.
- Snippet names are global. Prefix with the article's topic (`crew-…`,
  `proctor-…`, `staff-…`).
- `TestPagesAndArticlesAgree` fails if an icon names a snippet that doesn't
  exist, which is how a renamed snippet gets caught.

## 5. Links

Link other articles as `/help/<slug>` or `/help/<slug>#<heading-anchor>`.
Links to gated articles are pruned (words kept, anchor dropped) for readers who
can't open them, so link freely.

**A link to an article that isn't on `main` yet fails the link test.** When two
help PRs are in flight and one needs to link the other, leave the link out (or
the plain words in) and add it in whichever PR merges second, after merging
`main` in. Don't block one PR on another.

## 6. Glossary

`application/help/content/glossary.md` has no owner: **add your own terms in
your own PR.** Put each one in its alphabetical slot under its letter heading
(`## A–C`, `## D–P`, `## R–Z`); `TestGlossaryOrder` enforces both. Alphabetical
placement is what keeps parallel PRs from conflicting there: two PRs only touch
the same lines when they add neighbouring words, and then the resolution is
"keep both, in order".

```markdown
**Walk-up**: someone checked in at the desk without a Matcherino badge. Only
staff add them. See [Walk-ups](/help/check-in#walk-ups).
```

Define the word the way the screen uses it, say where it doesn't apply, and
link the section that explains it (same-PR article, or leave the link for a
follow-up per step 5). When a term already exists with another meaning (check-in
means three different things), extend that entry instead of adding a twin.

## 7. Screenshots

Use the [`help-screenshots`](../help-screenshots/SKILL.md) skill. In short:
your article's entries go in `scripts/help_screenshots/<slug>.json` and its
images in `static/help/<slug>/`, both yours alone.

## 8. Files a help PR touches (and doesn't)

Stay inside your own article's files so parallel help PRs never collide:

| Touch | Don't touch |
|---|---|
| `application/help/content/<your-slug>.md` | other articles, except a link you're adding |
| `scripts/help_screenshots/<your-slug>.json` | another article's manifest file |
| `static/help/<your-slug>/` | another article's images |
| `application/changelog/changes/YYYY-MM-DD-<topic>.md` (new file) | `application/changelog/changelog.md` |
| `glossary.md`, alphabetically | the article table or count in `docs/features/help.md` (there isn't one; don't add it back) |
| the page that gets the help icon | a list of where icons are wired (grep instead) |
| `scripts/seed_dev.py` or a `seed_*.py` helper, when a shot needs a state | |

`docs/features/help.md` only changes when the help *system* changes (a new
block kind, a new gate), not for a new article.

When `main` moves and a conflict appears anyway, it's almost always two PRs
adding to the same list: keep both sides, then rerun the tests below.

## 9. Changelog

New or substantially rewritten help is user-visible: add a change file per the
[`changelog`](../changelog/SKILL.md) skill, one bullet saying what readers can
now find and where the help icon is.

## 10. Tests before the PR

```bash
poetry run pytest -q tests/test_help_content.py tests/test_help_service.py \
  tests/test_help_images_and_staff.py tests/test_article_links_resolve.py \
  tests/test_changelog.py
```

Then the full suite and `ruff check .` if you touched Python (help icons, seed).
In the PR description, list anything you found the code doing that a reviewer
might not expect, and anything you assumed rather than checked.

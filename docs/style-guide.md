# UI style guide

The rules every page follows so Wizzrobe looks like one person designed it. This
is the prescriptive half: what to reach for and what not to do. The mechanics
(token values, how the tenant palette is applied, each helper's signature) live in
[reference/frontend.md](reference/frontend.md#design-tokens-the-studio-system).

It exists because, before October 2026, each era of pages used a different slice
of the design system: three page-top styles, ~205 underline inputs against 52
boxed ones, four badge styles, nine dialog widths, and gold doing every job from
the header bar to table headers to timestamps. The style pass (PRs #305, #312,
#314) and the accessibility audit (PR #315,
[reviews/2026-10-accessibility-audit.md](reviews/2026-10-accessibility-audit.md))
unified it. Most rules below are checked automatically; see
[Checking your change](#checking-your-change).

## The direction: "Studio"

Chosen by the project owner over keeping the gold "Phoenix" chrome.

- **A neutral warm-grey shell.** White header, grey page canvas, white cards.
  Borders separate things; shadows only lift (menus, dialogs).
- **One accent, and it means "you can act here" or "you are here".** Filled
  buttons, links, the active nav item, focus rings. Never titles, table headers,
  timestamps or decoration, or it stops meaning anything.
- **The accent is the community's colour.** Admin → Settings → General can
  re-point it, so never assume gold. Read `--accent` / `--wiz-link`, never
  `#9C6B12`.
- **One type family.** Atkinson Hyperlegible for everything, titles included;
  IBM Plex Mono only for times and IDs. Fraunces is retired.
- **Status is a tint, not a fill.** Soft-tinted chips with a thin border, never
  solid saturated pills.
- **Public pages (bracket, join, Watch) follow the same system** with a little
  more brand colour.

## Before you build

The rules below came out of rounds where several people (and several Claude
sessions) built pages in parallel. Two process rules keep that from looking like
ten different apps:

- **Show options before building.** A UI change the project owner asks for
  starts with two to four mockups, drawn at phone and desktop width (light and
  dark where colour matters), and waits for a pick. A plain bug fix to a design
  that was already chosen skips this.
- **Phone and desktop are one design.** A change made for one width is checked
  at the other (360-440px and about 1440px), and desktop changes with it when it
  needs to. If a fix only makes sense at one width, ask rather than let them
  drift.

When a new page needs something this guide doesn't cover, copy the nearest page
that already does it, and add the pattern here once it's agreed.

## Colour

| Do | Don't |
|---|---|
| Read a token: `var(--fg)`, `--fg-secondary`, `--fg-muted`, `--surface`, `--border-subtle`, `--status-*`, `--accent`, `--wiz-link` | Write a hex, `rgb()` or `hsl()` in a page, a `.style()` call or a slot template's `style=` |
| Use a class: `.st-ok` / `.st-pending` / `.st-live` / `.st-cancelled` for status text, Quasar `text-grey`/`text-negative`/… (re-pointed at tokens) | Mute text with `opacity` (the cat fact under empty lists was 2.7:1 that way) |
| Put brand-coloured *text* in `--wiz-link` (the accent mixed 15% toward black in light mode) | Put raw `--accent` on text in light mode: the default gold is 4.3:1 on white |
| Add a new colour as a token in `styles.css`, with its dark value | Add a colour that only exists in light mode |

Dark mode has two traps, both of which shipped and were fixed:

1. **A `:root` alias resolves once, at `:root`.** `--wiz-section-title: var(--fg)`
   on `:root` holds the *light* ink forever, even under `.body--dark`. Any token
   that derives from a dark-aware token must be restated in the `.body--dark,
   .q-dark` block. That left settings card titles, and later the On Air stage
   names, near-black on dark.
2. **Quasar's palette classes win over unlayered `!important`.** Quasar declares
   `text-*` / `bg-*` `!important` in its last cascade layer, and for
   `!important` the *earliest* layer wins. To re-point one, put the rule in
   `@layer theme { … }` in `styles.css`, as the grey-caption and dark-mode
   status rules do.

Every text token must clear 4.5:1 on `--surface`, `--page-bg` and `--surface-alt`
in both modes. `tests/theme/test_style_guide.py` computes this from `styles.css`.

## Type and page structure

| Need | Use |
|---|---|
| Page or tab title, with optional lede | `page_header(title, subtitle)` (`theme/page_header.py`). Renders the page's one `<h1>`; inside a Settings pane it drops to `<h2>` on its own |
| Title row you build yourself | `page_title(text)` |
| A tab that opens on controls, not a title | wrap it in `heading_fallback(title)` so screen readers still get an `<h1>` |
| A titled block on a page with several | `section_panel(title, icon=…)` (`theme/section.py`) |
| Dialog title | `dialog_header(title, dialog)`, or `form_dialog(title)` which includes it. Its close button is `dialog_close_button(dialog)`: a "Close" pill on desktop, an X disc below 600px |
| Times, match IDs | `--font-mono` / `.cell-timestamp` |

A list of cards on a desktop page goes in `wiz-tile-grid` (short cards) or
`wiz-tile-grid wiz-tile-grid--md` (cards with prose), not a column of
full-width cards: in a 1200-1400px container a full-width card puts its name
and its button 1100px apart. The grid drops to one column on a phone by itself.
An empty list inside one spans the grid; a list with nothing to show yet that
stands alone is `empty_panel(message, icon=…)`: a dashed `wiz-empty-panel` with
an icon, the message and a fun fact.

Don't put a lede in a caption label after the header (it floated 40px below the
title on 20 pages), and don't size a title or a section heading with `text-h1`…`text-h6` (a section heading inside a card is `section-title`).

## Components: reach for these first

| Need | Use | Not |
|---|---|---|
| Status / count | `ui.badge(text, color='positive')`: the colour only picks the tone, CSS draws the chip. Or `.wiz-chip .wiz-chip--ok\|pending\|live\|neutral\|cancelled` in a template | `text-color=` on a solid chip, a hand-built pill |
| Tags that only name something (a member's roles) | Neutral `.wiz-chip` tags that wrap inside their cell | `<q-chip color="primary" text-color="white">`: a solid accent pill is decoration, and five of them pushed the Members table past a 2000px window |
| A titled block's own buttons (Export CSV on a report card) | The panel's `aside` slot: `panel = await section_panel(…)`, then `with panel.aside:` | A hand-built title row with a button beside it |
| Filters on a page that isn't a match board | The match board's compact card: `.match-filters-card.match-filters-card--compact` with `match-filter-field` fields, plus a `.wiz-filter-toggle` Filters button whose badge counts active filters (Admin → Members, Triforce Texts; reports use `report_filter_bar`) | Caption labels over the fields, or the card without its toggle: CSS hides it below 1024px, so a phone never sees the filters |
| A notice across the page | `banner(message, title=, tone=)` (`theme/banner.py`; `info`/`ok`/`warning`/`danger`) | A hand-coloured row |
| A toast (saved, failed) | `ui.notify(text, color=…)`, or `notify_error(e)` (`theme/notify.py`) for a caught service error. Every toast opens top-centre just under the header, on phone and desktop alike, so it never covers a bottom-sheet footer, a sticky action bar or the keyboard | `position=` on a toast: the spot is set once, in `install_toast_position` |
| Flash a message across a redirect | `stash_notice` / `drain_notice` (`theme/notice.py`) | |
| Empty list | `empty_panel(message, icon=…)` for a list that stands alone, `empty_state(message)` inside a board or column, `no_data_slot()` in a table | A bare grey caption where the list would be |
| Help for a page, section or dialog | One `help_icon(...)` / `help_topics=` / `dialog_header(..., help=)` per heading, listing every topic ([help.md](features/help.md#help-icons)) | A second "?" on the same heading, or one beside an individual control |
| A form in a dialog | `form_dialog(title)` + `dialog_actions()` + `submit_on_enter()` (`theme/dialog/_helpers.py`) | Hand-rolled `ui.dialog()` + `ui.card()` + title row (`test_dialogs_use_the_shared_header` fails on one) |
| Dialog width | `dialog-card dialog-card--sm` (520px: a short form, a confirmation), `dialog-card` (640px, the default: a long form such as Add Tournament or Create Match), `dialog-card--lg` (960px: a table or two columns). See [Dialogs](#dialogs) | `w-96`, `w-full`, `.style('max-width: …')` |
| Phone-sized dialog | `mobile_sheet(dialog)` (form_dialog does it): a bottom sheet as tall as its content | `maximized` (a short form floats over a screen of blank card) |
| A table | `enable_mobile_grid(table, columns, table_key=TableKeys.X, …)` | A bare `ui.table` (the `check_table_grid` and `check_table_prefs` hooks enforce this) |
| Text input / select | `.props('outlined dense')` | `filled`, `standout` |
| Two-to-four way toggle | `ui.toggle(...).props('no-caps dense unelevated').classes('wiz-segmented')` | A row of separate buttons, a bare `ui.toggle` |
| A switch between views (tabs, segments, a day) | Tie it to the address: `bind_url(key, switch, …)` or `url_param(…)` ([url_state](reference/frontend.md#addresses-titles-and-back)), so Back, reload and links keep the view | A switcher the address bar doesn't follow (the `check_view_urls` hook enforces this) |
| A Copy button | `copy_to_clipboard(value, label)` (`theme/clipboard.py`) | Inline `navigator.clipboard` JavaScript |
| A credential shown once | `show_secret_dialog(title, secret, noun=…)` (`theme/dialog/secret_dialog.py`) | A hand-built reveal dialog |
| Admin refresh | `refresh_button` (`theme/tables/admin_crud.py`) | |
| A new admin setting | A card in a Settings pane (`pages/admin_tabs/settings/`, `section_panel` + `SaveFooter`, whose Discard and Save appear only once something changed). Settings opens on a home page of tiles; a pane has an "All settings" link back, and leaving it with unsaved edits asks to save, discard or keep editing | A new drawer tab, a second side bar, a second Save button |
| Page toolbar actions | One main button plus one Actions menu, see [Actions and menus](#actions-and-menus) | A row of three or more loose buttons |
| Actions on a row or card | One button plus a ⋮ (`more_vert`) menu, see [Actions and menus](#actions-and-menus) | Open, Edit and Delete as separate buttons on every row |
| A list of matches | `MatchTableView(layout='run_sheet')`, see [Match boards](#match-boards) | A new card-per-match layout |
| The viewer's crew Sign up / Withdraw | `crew_signup_section` (`theme/tables/match_slots.py`) | A button placed per surface |

## Actions and menus

**A page toolbar has one main button and one Actions menu.** The main button is
the thing people do most on that page (Add asset, Add member). Everything else
goes in a single **Actions** menu at the right of the toolbar: a flat, primary
coloured `ui.button('Actions')` with `icon-right=expand_more`, holding one
`menu_action(icon, label, on_click)` (`theme/menu.py`) per item. A destructive
item goes last, under a `ui.separator()`, with `negative=True`. The menu is the same on a phone and on
desktop. The shared refresh icon at the far right doesn't count as a second
button. Volunteer Shifts (`pages/admin_tabs/admin_volunteers.py`) is the reference;
its four loose buttons used to wrap onto three lines on a phone.

**A row or card has one button and a ⋮ menu.** At most one action shows as a
button on a list row or card, the one people use most (Check in or Check out,
Test roll). Open, Edit and Delete go in a ⋮ menu at the end of the row: a flat
round `more_vert` button named for its row ("More for Ana"), holding
`menu_action` items, Delete last. Tapping the item's
name opens or edits it. Desktop rows and phone cards carry the same button and
the same menu. `more_menu(name)` (`theme/menu.py`) builds it for Python-built
rows; `slot_more_menu`, `slot_row_button` and `slot_name_link` do the same in a
`ui.table` slot. ⋮ is the only overflow icon; `more_horiz` (⋯) isn't used.

**Destructive actions confirm first.** Delete, reset and remove never fire on
one click. They sit behind a menu and open a `dialog-card--sm` confirmation that
says what else is affected (the tournaments still using a preset, the shifts a
reset clears). The confirm button is red and sits on the right. Use
`ConfirmationDialog`; for something that can't be undone and takes a lot with it
(a qualifier and all its runs, every shift), add `require_phrase` so the button
stays off until the phrase is typed.

**Negative on the left, positive on the right, always.** In any pair of actions
(a card, a sheet, a dialog footer) the negative or dismissive one (Can't make
it, Decline, Cancel) is on the left and the positive one on the right, in every
state. When a status replaces a button ("Acknowledged" after you acknowledge),
the status takes that button's slot and the other button stays put; swapping
sides between states makes people mis-tap. On a phone a card's pair splits the
row in equal halves; on desktop it's a right-aligned pair.
`test_negative_action_sits_left_of_the_positive_one` checks every pair whose
labels say which is which (Decline, Reject, Remove, Clear, Withdraw, Cancel
against Approve, Confirm, Save, Assign, Publish).

**A slow button says what it's doing.** Anything that waits on another
service (a randomizer, racetime.gg, Challonge, start.gg, SpeedGaming, Discord,
Matcherino) or loops over many rows can take seconds. Wire it with
`busy_click('Syncing…', handler)` (`theme/busy.py`): while it runs the button
swaps its content for a spinner and the busy words on a neutral fill, ignores
further presses, and shows a seconds count after three seconds
(`static/js/busy.js`). A job that knows its size reports it with
`current_busy().progress('Rolling', n, total)` ("Rolling 7 of 25…"). A ⋮ menu
item closes as it's tapped, so the same words go on a spinner toast instead
(`async with busy(None, …)`). A confirmation in front of a slow action passes
`busy_text=` to `ConfirmationDialog`, which keeps the dialog open with its
confirm button busy until the work is done. A `ui.table` slot button binds
`:loading` and `wiz-busy-btn` to a row flag and drops in
`slot_busy_template('Rolling…')`. Icon-only buttons keep a plain spinner.

**Help is one "?" at the end of the heading row.** See the Help row above; in a
dialog title it sits just before Close and matches it (a "Help" pill on desktop,
a grey disc on a phone). It opens as a 24rem popover on desktop and as a bottom
sheet below 600px, like a phone dialog. With several topics it opens on a list
of their names; tapping one swaps to that topic, with "‹ Help" back to the list
and Close in every title row. `help_button` (`theme/help/icon.py`) draws all of
it, so don't put topics in `ui.expansion` accordions or leave the menu without
a width (it shrank to one word per line).

## Dialogs

- **The header is title, "?", Close.** Close is a labelled grey pill on desktop
  and a grey X disc on a phone, so the dialog's primary button stays the only
  accent. `dialog_header` draws both. That's the one Close: a footer never
  repeats it, and a dialog with nothing to do but read has no footer at all.
- **On a phone every dialog is a bottom sheet** as tall as its content
  (`mobile_sheet`), never a full screen of blank card. While the on-screen
  keyboard is up, `static/js/dialog-keyboard.js` lifts every dialog (sheet or
  centred) into the area above it and scrolls the focused field into view, so
  don't add per-dialog keyboard workarounds.
- **Pick the narrowest size that fits**: 520px (`--sm`) for a short form or a
  confirmation, 640px for a long form, 960px (`--lg`) for a table or two columns.
- **Fields fill the dialog's width.** The card's own padding is the one 16px
  inset, so the body column doesn't add `q-pa-md` (CSS cancels it if it does).
  Two short fields that belong together, a date and a time, share a row in a
  `.form-pair`.
- **A field's hint wraps under its own field** and never runs into the next one.
- **The footer sits on the right** with the primary action last and Cancel just
  left of it, in a `dialog_actions()` bar that stays pinned to the bottom of the
  card while the body scrolls.
- **A footer is one line on a 360px phone.** It holds at most Cancel and the
  primary button. Anything else the dialog can do (Delete match, Cancel match,
  Send message, Reset to defaults) goes in a ⋮ menu at the far left,
  `dialog_more_menu()`, with `ui.space()` between it and Cancel; the destructive
  item is red and last, and still confirms first. Keep the two labels short
  (`test_dialog_footers_fit_one_line_on_a_phone` caps them at 30 characters
  together). A genuine three-way choice (Keep editing, Discard, Save) may keep
  three short buttons, marked exempt.

## Desktop layout

Desktop pages use the width rather than stretching one column across it:

- A list of cards is a `wiz-tile-grid`, described under
  [Type and page structure](#type-and-page-structure).
- A page with a list and the thing you picked from it shows them side by side:
  the run sheet and its match sheet, My Availability's windows beside the chart,
  On Air's stage columns (stacked one after another on a phone).
- A page of short forms (Profile) puts them in two columns
  (`wiz-profile-sections`) rather than capping the page at a narrow column.
- A page around a wide table (Admin → Members, `/platform`, the reports) uses
  `page-container-wide` (1600px). Let long cells wrap inside a bounded box
  (Users wraps roles in 9-20rem and names in 7-12rem) rather than leaving
  Quasar's `nowrap` to stretch one column: a 31-character Discord handle or six
  role tags otherwise push the table off the page.
- The side menu is 248px pinned on desktop and 85% of the screen (up to 340px) as the phone overlay, with 40px rows on desktop and 48px rows on a phone. Both come from `DRAWER_WIDTH_*` in `theme/base.py`. Don't widen it for one long label; shorten the label.
- The side menu is one system: Watch live is a link of its own at the top, then a Home / Volunteer / Admin strip whose choice swaps the page list under it in place (the menu stays open), and only picking a page navigates. Volunteer and Admin list their pages through `register_section_menu` (`theme/section_menu.py`); a new hub section registers one.
- The side menu marks two things differently. The section you're in (Home, Volunteer, Admin, Help…) gets an accent tint via `mark_nav_section()` in `theme/nav_section.py`; the page within it (a tab row) gets the grey `q-item--active` fill. Both fill the icon.

## Match boards

Every list of matches follows the Admin → Match Schedule run sheet
(`theme/tables/match_run_sheet.py`). Proctor Station uses it too. The member
boards (Event › Schedule, and My Schedule's Your matches and Crew you signed up
for) use its player version, `layout='player_run_sheet'`. A personal list of
matches is that board narrowed by a filter (`crew_discord_id=` for the crew
board), never a new card list.

- **One line per match, grouped by what it needs**: Needs a decision, Live now,
  Up next, Waiting for players, Confirmed. A line says only which match, when,
  where, crew counts and status. Anything wider belongs in the sheet.
- **Everything you can do to a match lives in its match sheet**
  (`theme/dialog/match_sheet.py`), which opens when you tap the line: a side
  sheet on desktop, a bottom sheet on a phone.
- **What a player can do to their own match** (Ask to change, Harder settings,
  Offer for stream) goes in one ⋮ on the line, the same on the phone card.
  Acknowledge sits under the status and the bell watches the match.
- **A "Needs you" inbox** beside the board (`theme/match_inbox.py`) lists what's
  waiting on a person rather than the clock, with the action on each item. Open
  leads each item's buttons, so the main action is always the last one.
- **One column per crew role** (Commentators, Trackers), on the admin and member
  boards. Each cell reads count, then the viewer's own Sign up / Withdraw tray,
  then the names (a tick for approved, a clock and "waiting for approval" or
  "to approve" for the rest). The tray is never empty: when there's nothing to
  press it holds a disabled Sign up with the reason beside it ("Signups closed",
  "You're playing"), so the button sits in the same spot on every line, on the
  phone card and the desktop cell alike. Withdraw is left of Confirm. A role
  nobody has signed up for says "No signups yet" in muted italics
  (`st-neutral italic-note`).
- **Amber means someone has to act.** In crew counts only "needs crew" is
  amber; "to approve" and plain counts stay neutral.
- **Filters label themselves** inside their box (no caption line above), a
  multi-select reads as one line of text rather than wrapping chips, and the
  filters sit in a 2×2 grid below 1024px and one row above it
  (`theme/tables/match_filters.py`).

## Reports

Every report page is built from the helpers in
`pages/admin_tabs/reports/shared.py`, so the nine reports and the dashboard
read as one set:

- `report_page_shell(title, subtitle)` for the top: an "All reports" link back,
  then `page_header`, in `page-container-wide`.
- `report_filter_bar(active)` for the filters: the match board's compact card,
  one row on desktop and folded behind a Filters button with a count badge
  below 1024px. Pass each field through `filter_field`, and add `report_jumps`
  for the whole-event and single-day shortcuts.
- `section_panel` for each chart or table, with Export CSV in the panel's
  `aside`. Two panels that are read together (Capacity's busiest times and its
  forecast) sit side by side on desktop.
- `kpi_grid()` for a row of `kpi_card` tiles.
- Charts take their colours from `chart_palette()`: the community's accent,
  lifted until it clears 3:1 on the light and dark surfaces, with amber as the
  second series when the accent is close to teal. Never a fixed gold.

## Accessibility (WCAG 2.1 AA)

- **Every control has a name.** An icon-only `ui.button` gets one from
  `.tooltip('Delete')` (`theme/a11y.py` copies it into `aria-label`). In a Vue
  slot template, a `<q-btn>` with only an icon spells out `aria-label` itself;
  a `<q-tooltip>` is not a name. `tests/test_a11y_names.py` enforces this.
- **Captions are labels.** A free-standing caption over a field becomes
  `label_field(caption, field)`; an unlabeled filter select or switch is a
  failure.
- **Click handlers go on buttons.** A clickable card or icon gets
  `make_activatable(el, label)` so it's in the tab order and fires on
  Enter/Space.
- **Never remove the focus ring.** It's a 2px `--accent` outline on everything,
  Quasar's `.no-outline` controls included.
- **Dialogs are named by their title and return focus** to whatever opened them
  (`static/js/a11y.js`). Use `dialog_header` and you get both.
- **Contrast is 4.5:1 for text, 3:1 for large text and icons**, in light and
  dark. Check both modes; most of the 2026-10 failures were dark-only.
- **Every page works at 390px wide** without sideways scrolling: tables become
  cards, dialogs become sheets. See [Phone layout](#phone-layout).

## Phone layout

Most people open this app on a phone at the venue, so about 390px is the width
that matters. CI checks four phones: 360px (Galaxy S24), 393px (iPhone 15 Pro),
412px (Pixel 7) and 440px (iPhone 16 Pro Max); a pull request checks 360px
only and main checks all four. The 2026-10 phone sweep
found the same handful of mistakes on page after page; each has a shared fix
in the "Phone layout" block of `styles.css`.

| Do | Don't |
|---|---|
| Let the page gutter be the one 16px `.nicegui-content` gives you | Pad a tab's content again (tabbed pages started 32px in), or nest a card in a card in a card on a phone |
| Show a record as label/value fields with `enable_mobile_grid`, or the `.wiz-grid-fields` / `.wiz-grid-field` / `__label` / `__value` classes in a bespoke card slot | Split a card into `col-4`/`col-8` or `col-5`/`col-7` (labels wrap, values get a strip) |
| Size a control with `width: 100%` and a `max-width`, or a flex basis that can shrink (`min-width: 0`) | Give a field a fixed width wider than about 300px (`.control-width` was 400px), or `no-wrap` a row of fixed-width fields |
| Put a card's buttons in one `.wiz-card-actions` no-wrap group so they wrap together | Leave Edit and Delete as loose row items that strand one on a line of its own |
| Let unbroken strings (URLs, seeds, tokens) wrap with `overflow-wrap: anywhere` or end in an ellipsis with the whole value in `title` | Let a long string push its card wider than the screen |
| Use the shared `search_input` for a table search; it shrinks to its row on a phone | Hard-code a `w-64` search box beside other toolbar buttons |
| Keep a lede to a sentence or two; `.text-muted` drops to body size on a phone | Write a paragraph above the first control, or set lede text above body size |
| Scope a width-meaning `flex-basis` to items whose parent is a row (`.row > .x`) | Set `flex: 1 1 12rem` on something that can land in a column, where the basis becomes a height (the check-in desk got a 192px gap under its search box) |

The phone pass in `scripts/ui_a11y_scan.js` (the `"phone"` block in
`scripts/ui_a11y_ci.json`) loads every page on each of those phones and
fails when the page scrolls sideways, an element runs off the screen or out of
its card, text is cut off without an ellipsis, text is squeezed to fewer than
eight characters a line over three or more lines (a chip or button beside a
flex item took its width), or a flex or grid item is stretched more than 96px
past its content. A target can set its own `"loginAs"` when only that user
sees the content (a player's own match cards). It can't judge ordinary spacing or
verbosity: look at the page at phone width yourself before calling it done.

## Hold still

Nothing on screen moves unless the person looking at it moved it. In the
2026-10-16 demo, buttons slid out from under people's fingers twice: the Match
Schedule board jumped sideways every time the Needs you list reloaded, and boards
reshuffled when someone else checked a match in. A page that moves between two
correct states is wrong for the frames in between, and screenshots never show it.

- **A section that reloads keeps its old content until the new is ready.** An
  async builder under `@ui.refreshable` blanks while it awaits, so everything
  below it jumps up and back. Use `@steady_refreshable` (`theme/steady.py`),
  which builds off-screen and swaps in one step; the `check_steady_refresh` hook
  blocks the old shape. When you hand-roll a refresh, query first, then clear and
  rebuild without awaiting in between (the Needs you inbox, `theme/match_inbox.py`).
- **Reserve the space for what will arrive.** A spinner, an empty state or a
  placeholder takes the height of what replaces it. A banner or count that can
  appear after load gets a slot that is already there.
- **Changes update rows where they sit, the viewer's own included.** On a match
  board (`MatchTableView`, via `theme/tables/match_hold.py`) a row that now
  belongs in another group stays put with a "Now Ready to start" tag, a removed
  match fades in place with a "Removed" tag and no working buttons, and a new
  match waits off the board. A "N changes held · Tidy up" pill counts them, and
  the board re-sorts in one step when the viewer taps it, scrolls, or leaves the
  board untouched for a minute. Never insert a row above what someone is
  looking at. A tag is absolutely placed (or sits under the time on desktop), so
  adding it never changes a row's height.
- **A row's state change never swaps its controls' sides.** A badge that replaces
  a button takes that button's slot (see [Actions and menus](#actions-and-menus)).

`scripts/ui_layout_shift.js` checks this in CI. It loads every page in
`scripts/ui_layout_shift_ci.json` at desktop and phone width, redraws every live
section with unchanged data (`theme/layout_probe.py`, only installed when the app
runs with `WIZ_LAYOUT_PROBE=1`), and fails any element that moves more than 2px.
A failure names the element and how far it moved; the usual cause is a section
that went empty, or changed height, while it rebuilt. Targets with `live` steps then
change matches from a second session (check one in, delete one) and fail when a
board row moves by more than the rows above it grew.

## Words

UI copy follows [CLAUDE.md § Writing user-facing text](../CLAUDE.md#writing-user-facing-text)
and [Calls to action](../CLAUDE.md#calls-to-action-must-land-on-the-control-that-performs-them).
Sentence case everywhere, badges and table headers included.

Call someone who's coming to or taking part in the event an **attendee**, not a
member: "side events attendees are running", "Attendees who asked for ADA
accommodation". Keep **member** for community membership itself, where it means
belonging to the community in Wizzrobe: joining, the join page, Admin → Members,
Add member, roles, and "Ask to join it first" gates. If a sentence would still be
true of someone who joined the community but isn't at the event, "member" is
right; if it's about the person at the event, it's "attendee".

Call the people who run the community **staff**, never "admins". Staff and
admins are the same people and are treated the same, so a sentence that means
"someone who runs things" says staff: "Check with staff", "Only staff can grant
roles", "Someone else on staff saved this match". **Admin** is only the title of
the Admin Desk, the overseer of the tournament room. Anything about the room and
how it runs (checking a match in, a flagged result, a no-show, a dispute, who
may come in) is the Admin Desk's call, so "tell the admin", "the on-call admin"
and "Flag for admin review" stay. So do the names of things: the Admin side-menu
section and its "Admin → X" pointers, the Tournament admin role (someone who runs
one tournament), and "platform admin" for the people who run Wizzrobe itself.

## Checking your change

Automated, on every PR:

| Check | Catches |
|---|---|
| `tests/theme/test_style_guide.py` | Dialog cards off the three sizes; colour literals in `.style()` / slot `style=`; `text-h1`…`h5` titles; hex colours in CSS rules outside token definitions; `:root` aliases not restated for dark mode; text tokens under 4.5:1 on any surface in either mode; Cancel or Close after the primary button in a dialog footer; a dialog footer with more than two buttons or over 30 characters of labels; a Decline/Reject/Remove/Withdraw-style button right of an Approve/Confirm/Save-style one in the same group; a `.match-filters-card` with no Filters toggle; a Close in a dialog footer; a hand-built dialog without `mobile_sheet`; an inline `min-width`/`max-width` inside a dialog; a `more_horiz` overflow icon; a row's ⋮ not named "More for …"; a red menu item that isn't last in its menu; an awaited `delete_*`/`remove_*`/`cancel_*`/`clear_*`/… service call that no `ConfirmationDialog` stands in front of; the side menu off its `DRAWER_WIDTH_*` sizes |
| `tests/test_a11y_names.py` | Icon-only `<q-btn>` in a slot template with no name |
| `check_table_grid`, `check_table_prefs` hooks | Tables with no phone card view or no `table_key` |
| `scripts/ui_a11y_scan.js` (CI `browser-a11y` job) | On every page in `scripts/ui_a11y_ci.json`, in light and dark: axe's WCAG 2.1 A/AA rules, and rendered text under 4.5:1 |
| `check_steady_refresh` hook | An async `@ui.refreshable` in `pages/` or `theme/`, which blanks while it loads |
| `scripts/ui_layout_shift.js` (CI `browser` job) | On every page in `scripts/ui_layout_shift_ci.json`, at desktop and phone width: anything that moves more than 2px when the page redraws its live sections with unchanged data. See [Hold still](#hold-still) |
| `scripts/ui_a11y_scan.js` phone pass | On the same pages at 360px on a pull request, and at 360, 393, 412 and 440px on main: sideways scrolling, elements running off the screen or out of their card, text cut off without an ellipsis, text squeezed to a few letters a line, empty bands over 96px |

The browser scan runs locally against the seeded app (boot and seed as in the
[ui-validation skill](../.claude/skills/ui-validation/SKILL.md)):

```bash
npm install --no-save playwright@1.56.1 axe-core@4.10.3   # once
node scripts/ui_a11y_scan.js scripts/ui_a11y_ci.json
```

It loads six pages at a time (`A11Y_CONCURRENCY=1` for one at a time, which
makes a flaky hit easier to read; `A11Y_PHONE_WIDTHS=360` checks one phone, as
a pull request does) and writes `report.json` under
`<outDir>/a11y/`. When it fails:

- **A contrast hit** names the element, its class and the ratio. Fix the colour
  at the token or the class, never by adding a one-off hex. If it fails only in
  dark mode, look for an un-restated alias (trap 1 above) or a Quasar class that
  needs an `@layer theme` rule (trap 2).
- **An axe hit** names the rule and the element. `button-name`, `label` and
  `aria-dialog-name` are the common ones; the helpers above fix each.
- **Axe's own `color-contrast` rule is off on purpose.** Badges are tinted with
  an inset box-shadow (Quasar's layered `!important` background beats both
  `background-color` and `background-image`), axe can't see it, and it reports
  every badge as failing. The scan's own tint-aware check replaces it.
- **`a11y.ignore` in the config is for a finding that is wrong**, not one that is
  inconvenient. Each entry says why.
- **A phone layout hit** names the element and how far it overflows. "Runs out
  of its card" usually means a fixed width or a `no-wrap` row: see
  [Phone layout](#phone-layout). An element inside a horizontal scroller (a wide
  table, a tab strip) is allowed to be wider and isn't reported. `phone.ignore`
  takes a CSS selector and, like `a11y.ignore`, a why. "Leaves an empty gap"
  names a flex or grid item that's taller than its content plus padding: look
  for a `flex-basis` or `flex-grow` meant for a row that applies in a column.
  An item that centres its content (an error page) isn't reported.

When a new page or a restyle lands, add it to `scripts/ui_a11y_ci.json`, then
look at it yourself in light, dark and at phone width (the ui-validation skill
screenshots all three). The scans prove the floor; they can't tell you that the
page looks like the rest of the app.

## Exceptions

A line that genuinely needs to break a mechanical rule says why in place:
`# style-guide: exempt — <reason>` in Python, `/* style-guide: exempt — <reason> */`
in CSS. The report stat figures (`text-h4`) and the charcoal offline banner are
the current examples. An exemption without a reason is a review comment waiting
to happen.

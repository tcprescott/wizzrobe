# Seed rolling rules

Status: Building · Size: full · PRs: (PR 1) · Requested: 2026-11-03, Thomas

## Problem

A tournament rolls one preset, or its hard preset when every player privately
opts in, or whatever staff force on one match. Events that want different
settings by round, by game in a set, by player choice or by chance can't say so,
so staff hand-edit matches or run a second tournament. SahasrahBot did all of
these, but each one was a bespoke Python class per event (title→preset maps,
game-number tables, player forms, mystery YAML), so nothing carried over.

## Scope

In:
- A tournament gets an ordered list of **seed rules**. Each rule is *when*
  (conditions about the match) → *then* (an outcome). The first rule that
  matches wins; a final "Otherwise" row is the tournament's preset.
- **Conditions:** bracket round (the same round labels Round schedule uses), game
  number in a set (Game 1 / 2 / 3, decider), match title contains, stage
  assigned / on stream, date range, and player input (below). Conditions in one
  rule are ANDed; "any of" lists inside one condition cover OR.
- **Outcomes:** one preset; or a weighted roll among presets (e.g. Open 60,
  Standard 40); either can add **setting tweaks** on top (set a key in the
  preset's settings, e.g. `swords: assured`), checked against the randomizer's
  known keys where we have them.
- **Match form (player input).** A tournament has one pre-match form, built
  from the survey question types, **choices only**: Yes/No, Pick one, Pick
  several, Number. Each question sets who answers (each player, the higher
  seed, the loser of the previous game), which matches it's asked on (every
  match, or only where a rule reads it), and when the opponent sees the answer
  (right away, when the seed rolls, never). A choice can link to a preset so a
  rule can roll "their answer". Opt-ins and picks are just questions: an opt-in
  is a hidden Yes/No that a rule reads as "everyone answered Yes"; a pick is a
  Pick one for one seat. Answers lock when the seed rolls; an unanswered
  question means its rule doesn't fire.
- **Rule conditions on answers:** every player / any player / a seat answered
  X, or answered at all; "roll their answer" as an outcome.
- **Hard mode folds in.** Existing `hard_preset` tournaments are migrated to a
  hidden Yes/No question "Play Hard mode?" plus the rule "everyone answered Yes
  → Hard preset". Staff's
  per-match override stays and still beats every rule.
- **Text view.** The same rules show as YAML you can copy, paste or import, so
  rule sets can be shared between tournaments and communities.
- **Preview.** Pick a match (or a made-up one: round, game, opt-ins) and see
  which rule fires and the settings it would send. A weighted outcome shows the
  odds.
- **Why this seed.** Every roll records the rule that fired, the weighted draw
  and the tweaks in `GeneratedSeeds`; staff see "Rule 3: Top 8, Game 3 → Open
  Hard" on the match.
- New app only (web/), REST for the app; MCP read-only `get_seed_rules`.

Out:
- Per-setting mystery weights inside a rule. ALTTPR mystery weightsets are a
  preset kind in the community presets work
  (`/mnt/project-files/community-presets/spec.md`); a rule points at one like
  any other preset, and its "hide rolled settings" behaviour applies.
- Pick/ban drafts between players.
- Pulling the mode from an outside site (the ALTTPR League API style).
- Rules on async qualifiers and `/seeds` test rolls (they keep rolling one
  preset).
- Spoiler-race timing.

## Flow

Staff (STAFF, PRESET_MANAGER, tournament admin):
1. Admin → Tournaments → a tournament → **Seed rules** tab.
2. The rule list sits beside the selected rule's editor (side list). **Add
   rule** → pick conditions from chips (Round, Game in set, Title, Stage,
   Date, Form answer) → pick an outcome (Preset / Weighted / From form answer, + Tweak).
   Questions are built on the **Match form** tab beside it (same side list);
   each question shows which rules use it.
3. Drag to reorder; the "Otherwise" row is pinned last and shows the
   tournament preset.
4. **Preview** on the toolbar tries a real or made-up match.
5. Actions menu: **View as text**, **Import text**, **Copy from tournament…**.
6. Save validates (unknown presets, presets of another randomizer, tweak keys,
   weights that sum to 0) and shows errors inline on the rule.

Player:
1. When a match has questions for them, the match card shows "Before your
   match" with those questions (one DM per match, its link button opens the
   card).
2. Each answer's visibility follows the question (hidden opt-ins stay private
   as hard mode is today).
3. Answers can change until the seed rolls, then lock.

Proctor / staff rolling a seed: no change. Roll seed resolves the rules; the
seed card says which rule fired (staff and proctors only).

## Mockup pick

B, side list: on desktop the rule list sits beside the open rule's editor with a live preview; on a phone the list is its own screen and a rule opens on its own page. The Match form tab (F) uses the same layout; the player card is P, the text view T. Images: `/mnt/project-files/seed-rolling-rules/mockups/{B,F,P,T}-{phone,desktop}-{light,dark}.png`.

## Data and permissions

- **Feature flag:** no (Thomas, 2026-11-03).
- **Models:** `Tournament.seed_rules` (ordered rule list, JSON; the
  tournament already carries tenant and event); `MatchFormQuestion` (tournament, order, kind,
  choices JSON with optional preset links, who, ask_on, visibility);
  `MatchFormAnswer` (match, user, question, value) replacing
  `MatchHardPresetOptIn`; `GeneratedSeeds.resolution`
  and `ProviderTask.resolution` JSON (source, rule id, draw, tweaks). `Tournament.hard_preset` migrated into a rule,
  then dropped in a follow-up.
- **Who can see / do what:** edit = STAFF, PRESET_MANAGER, tournament admin.
  Read rules = same plus proctors (for "why this seed"). Players see only the
  choices offered to them, never the rule list or weights (keeps hard-mode
  secrecy and doesn't spoil a mystery).
- **Other surfaces:** REST `/api/admin/tournaments/{id}/seed-rules` (get, put,
  preview), `/api/admin/tournaments/{id}/match-form`, player `/api/matches/{id}/form`; MCP `get_seed_rules`
  (read). Audit `tournament.seed_rules_updated`, `tournament.match_form_updated`, `match.form_answered`; the
  existing hard-preset DMs become one "Before your match" notice in the catalogue.
- **Player input:** players only choose among values staff listed; nothing
  free-typed. Rate-limited like the hard-mode toggle.

## PR plan

1. Spec + rule engine (pure, tested: parse, validate, resolve, YAML round-trip),
   `Tournament.seed_rules`, resolver wired into `generate_seed` behind "no rules =
   today's behaviour". Hard mode keeps working as a step before the rules.
2. New app staff screen (side list editor, preview, text view) + REST + MCP
   read + help article + changelog + seed row.
3. Match form: question builder tab, player "Before your match" card, REST,
   DM, answer conditions in the engine, replacing the hard-preset opt-in
   surfaces, and the hard-preset migration (hidden question + rule).

## Acceptance

- [ ] A tournament with no rules rolls exactly what it rolls today.
- [ ] A migrated hard-mode tournament behaves the same: unanimous, private, staff override wins.
- [ ] Rule order decides; the first match wins; "Otherwise" is the preset.
- [ ] A weighted outcome draws by weight (test over many draws) and records the draw.
- [ ] Tweaks reach the settings sent upstream and land in `settings_snapshot`.
- [ ] Rules ↔ YAML round-trips without loss; bad YAML shows the line that's wrong.
- [ ] Preview names the rule that fires for a chosen match.
- [ ] A player never sees another player's hidden answer or the rule list.
- [ ] A Pick one answer linked to a preset rolls that preset when its rule fires.
- [ ] An unanswered question makes its rule skip; the next rule decides.
- [ ] Answers lock once the seed rolls (REST refuses a change).
- [ ] On a phone, the rule list opens a rule full screen and back returns to the list.
- [ ] On desktop, the list and the editor sit side by side.

## Open questions

## Decisions

- 2026-11-03 — Thomas picked builder with a YAML text view.
- 2026-11-03 — Thomas picked outcomes = preset, weighted presets, plus setting tweaks (no per-setting mystery weights yet).
- 2026-11-03 — Thomas picked player input = opt-ins + picks (no pick/ban draft yet).
- 2026-11-03 — Thomas picked folding hard mode into rules (migrated).
- 2026-11-03 — Thomas picked no feature flag.
- 2026-11-03 — Thomas raised polling players for options, so a form builder may drive the rules.
- 2026-11-04 — Builds on community presets: rules name presets the tournament's community can attach (its own, by `space/preset`); mystery presets are outcomes like any preset, not a separate outcome type.
- 2026-11-04 — Thomas picked a match form, choices only, built from the survey question types; opt-ins and picks become questions.
- 2026-11-04 — Thomas picked layout B (side list); spec approved.
- 2026-11-04 — Build: rules live on `Tournament.seed_rules` (JSON) rather than a separate table; the hard-mode migration moves to PR 3 with the match form, so PR 1 keeps hard mode as a step before the rules (same behaviour).

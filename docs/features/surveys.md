# Post-event surveys

Staff ask the people who took part in an event how it went. Each person gets
only the parts that fit what they did, so the survey stays under ten minutes.
Behind `FeatureFlag.SURVEYS` (per community, off by default).

| Surface | Where |
|---|---|
| Builder, send, results | Admin → Surveys (`pages/admin_tabs/admin_surveys/`), STAFF |
| Respondent page | `/survey/{id}` (`pages/survey.py`, `theme/survey_form.py`), only for people it was sent to |
| Staff preview | `/survey/{id}/preview`, STAFF of this community; nothing is saved |
| REST | `/api/surveys` (`api/routers/surveys.py`), reads plus build-only writes |
| MCP | `mcpserver/tools/surveys.py`, same split as REST |
| Services | `application/services/survey/` |
| Models | `models/survey.py`: `Survey`, `SurveySection`, `SurveyQuestion`, `SurveyRecipient`, `SurveyResponse`, `SurveyAnswer`, `SurveySuggestionBatch`; `models/ai.py`: `AiProviderSetting` |

A survey is event-owned (`event_id` beside `tenant_id`). Its children are
tenant-scoped and reached through the survey.

## Building

A new survey starts from `template.py` unless staff untick it. Sections have an
**audience**:

| Audience | Shown to |
|---|---|
| `everyone` | everyone it was sent to |
| `each_tournament` | once per tournament they played, titled with its name |
| `each_position` | once per volunteer position they worked |
| `crew` | anyone approved to crew a match that was on a stage (comms, tracking, broadcast); crew on a match that never got a stage doesn't count |
| `commentators` / `trackers` / `broadcasters` | crew of that one role on a stage match (`Commentator`, `Tracker`, `Broadcaster` rows) |
| `checked_in` | anyone the check-in desk checked in |

Crew only counts matches that had a stage (Thomas, 2026-10-27: approved but
never given a stage "was as if they never did"). That holds for the audiences,
the `crewed` condition and the "Who gets it" counts alike;
`survey_participation_repository.crew_user_ids` filters on `stage_id`. Facts
carry `crew` plus `crew_roles` (`commentary`, `tracking`, `broadcast`), and the
preview's "Showing" switch has one persona per role.

A section can carry a one- or two-line **description** (240 characters) shown
under its title on the form.

Question kinds: `rating` (1–5), `nps` (0–10), `yes_no`, `choice_one`,
`choice_many` (2–10 choices), `comment` (plain text, 1000 characters) and
`agree_scale` (2–10 statements, each answered Strongly disagree to Strongly
agree, stored as `{statement: 1..5}`). A required agree scale needs every
statement answered. An agree scale can't be the target of an answer condition.

Choice extras, all checked in `_validation.clean_extras` and again on every
answer (`coerce_answer`), so REST and MCP can't get round them:

- `other_option` (pick one / pick many) adds **Other** with a write-in box that
  is always visible with its own label. Typing in it picks Other; Other with an
  empty box is an error. The answer is sent as `{"value": …, "other": "text"}`;
  the stored value holds `Other` and the write-in goes in `SurveyAnswer.text`
  (200 characters). Results list write-ins under the bar chart.
- `none_option` (pick many) is an exclusive choice such as "None of these",
  placed last after an "or". Ticking it clears the others; both together is
  refused. Results draw its bar apart from the rest.
- `min_picks` / `max_picks` (pick many). The hint states the limit; going over
  shows a red line under the question instead of blocking the tick. Ticking
  the none choice satisfies any minimum.
- `long_answer` (comment) raises the cap to 3000 characters, shows a taller
  box with a GOV.UK-style "You have N characters left" count, and doubles the
  question's time estimate. Results get a search box once there are more than
  three comments.

In the builder, every question row has a drag handle (Thomas picked A). Mouse
drags start after 8px of movement; a finger has to press and hold for 400ms so
a swipe still scrolls. Questions can land in another section.
`static/js/survey-drag.js` only moves a placeholder and fires `survey-drop`
on `.wiz-survey-builder`; `SurveyService.place_question` decides, and refuses a
drop that would put a question above the one it follows. Move up / Move down
stay in the row's ⋮ menu as the no-drag path (WCAG 2.5.7).

On the respondent form, `rating` and `nps` are sliders (`_scale_slider` in
`theme/survey_form.py`): no handle until the first touch, numbers under each
step, both ends labelled, and a Clear link. An agree scale is a grid of radios
on a wide sheet that turns into one card per statement below 36rem (a container
query, so it follows the sheet, not the window). Results draw one diverging bar
per statement with "% agree".

A part saves however its sheet closes: Done, Back to parts, the close button,
Escape or a tap outside (Thomas, 2026-10-28). If a close can't save (an answer
breaks a rule), the toast says why and reopening the part shows what was typed.

Once sent, the structure is frozen: answers belong to the exact questions they
were given to.

## Suggest questions (AI)

Each section of a draft has a **Suggest questions** button once the community
has an AI provider under Settings → Integrations (`pages/admin_tabs/admin_ai_provider.py`,
`AiProviderService`, STAFF). The community brings its own key: Claude
(Anthropic) or any OpenAI-compatible endpoint. There is no platform key.

- The key is sealed with AES-GCM under a key derived from `STORAGE_SECRET`
  (`application/utils/secret_box.py`), bound to `tenant:<id>`, so a copied row
  doesn't open in another community. Status and audit never carry it. If
  `STORAGE_SECRET` changes, the card says so and asks for the key again.
- The provider is called only when staff press the button, once per press.
  Nothing runs in the background. `daily_limit` (default 20, 1–200) caps
  requests per community per rolling 24 hours.
- The context (`survey/ai_context.py`) holds the survey so far, event facts
  (tournaments, formats, stage names, start delays, match lengths, reschedule
  counts, positions and hours, check-in rate) and last event's survey results.
  It never holds names, handles, comments, reschedule reasons or anything else
  a person typed, and results for groups under five are left out.
- Answers come back as JSON and go through the same checks as a
  staff-written question (`_clean_drafts`); at most five per press. They're
  stored as a `SurveySuggestionBatch` with the exact context sent, which staff
  read from **What was sent**. Keeping a draft calls `SurveyService.add_question`.
- In dev, any key starting `mock-` (or `MOCK_AI=1`) returns canned drafts from
  `application/utils/mocks/mock_ai.py`. Both are ignored in production.

## Conditions ("Only show if…")

`conditions.py` is a self-registering list. Each entry is `register(SurveyCondition(key, label, collect=…))`:
`collect` runs once at send time over the event's `Participation` and returns,
per user, `True` or the tournament ids it holds for. A *negation* (`negates=`)
is the opposite of another key. `answer` is decided on the page from the
respondent's own earlier answer.

A question stores up to **3** conditions and a section up to **1**, as a JSON
list on `conditions`:

```json
[{"key": "on_stage"}, {"key": "answer", "question_id": 12, "values": [1, 2]}]
```

All of them must hold. There is no OR and no nesting (Thomas, 2026-10-27), which
keeps this a list rather than a rules engine. Rules enforced in
`_validation.check_conditions` and `survey_service`:

- an `answer` condition waits on an *earlier* question with set answers (not a
  comment); a section's waits on a question in an earlier section;
- a key and its negation together are refused;
- moving a section or question so that it comes before the question it waits
  on is refused, and so is deleting a question something follows.

A per-tournament condition inside an `each_tournament` section checks that
tournament; anywhere else it holds when it holds for any of them. The
`on_stage_any` and `tournament_room_any` conditions (and their `not_` opposites)
always look at the whole event, so a tournament section can ask "were you on a
stage at all?" in every repeat (Thomas, 2026-10-27). Like the per-tournament
ones, "tournament room" means the tournament has `uses_tournament_room` set.

On the page, a part whose section waits on an answer appears and disappears as
the answer changes (`RespondentView.shown_parts`). On submit, saved answers to
questions or parts that ended up hidden are deleted, so results only count what
the person could see.

## Sending

Only from the app: Admin → Surveys → **Send…** with a close time (default seven
days out). `send` freezes each person's facts on a `SurveyRecipient` row and
DMs them (`SURVEY_INVITE`); **Remind** DMs people who haven't answered, at most
once a day and twice in all (`SURVEY_REMINDER`); the second is worded as a last
call, and the menu then reads "Two reminders sent". Both are in the notification
catalogue, so communities can silence them. The DM link goes straight to
`/survey/{id}`. **Change close date…** in an open survey's Actions menu moves or
clears `closes_at`.

The invite greets the reader by name ("Hi Sam,"), gives their own time to
answer (from `plan.seconds` over their parts) and what the questions are about,
and ends "We'll share what we change because of your answers." A reminder to
someone who saved a part but never sent it says they're partway through
instead. Both DMs carry a grey **Not interested** button
(`discordbot/survey_buttons.py`, `SurveyResponseService.decline`): pressing it
sets `declined_at`, greys the button out, and drops them from reminders; the
link keeps working.

The builder's time check warns over **10 minutes** (`plan.BUDGET_SECONDS`).

## Drop-off

The Results tab opens with a drop-off card (Thomas picked layout A plus a
median time, 2026-10-28): Sent, Opened, Started (saved at least one part),
Submitted, then each part in survey order with how many opened it, how many
answered it, and how many stopped there (opened it last, never sent it), plus
"Median time to finish: X (estimate was Y)" once five people have sent it.
Counts are of people, never answers. REST and MCP results carry the same
numbers under `dropoff`.

The survey page reports each part it opens (`SurveyResponseService.open_part`)
and `save_part` marks it done, on the recipient row's `progress`. That would
say which parts an anonymous respondent skipped, so `submit` adds the person's
parts to `SurveyPartTally` (running counts) and clears `progress`, `last_part`
and `started_at`, keeping only `finish_seconds`. The card adds the tallies to
the progress of people who haven't sent it.

Who gets it: everyone who took part in the event (played, volunteered, crewed a
stage match or was checked in).

## Anonymity

Answers are named by default; the respondent can tick **Answer anonymously** if
the survey allows it. An anonymous submit:

- nulls `SurveyResponse.user_id` for good and keeps only the groups (the
  tournaments and positions they were in);
- stores only the day on `submitted_on`, and the recipient row keeps only the
  day it was opened and answered, so the two can't be lined up by time.

The switch says so plainly: "We cannot guarantee anonymity based on your
answers: what you write may still identify you." (Thomas's wording.)

In results, a group with fewer than 5 answers shows its count only, and an
anonymous comment from a group that small carries no group name.

Known limits: response ids are sequential, and answering is blocked once the
event is archived.

## REST and MCP

Reads (STAFF_READ): list, get, conditions, results, responses. Writes
(STAFF_WRITE) build a draft only: create, duplicate, update, delete (drafts
only), add/update/delete/move sections and questions. Sending, reminding and
closing stay buttons in the app (Thomas's call). The MCP tools mirror the
routes one for one; see [mcp-server.md](mcp-server.md#surveys) and
[rest-api.md](../reference/rest-api.md#surveys-apisurveys--surveyspy).

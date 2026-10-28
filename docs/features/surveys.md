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
| `each_tournament_run` | once per tournament they're a tournament admin of (`Tournament.admins`), titled with its name; scope `o:<id>` |
| `each_position` | once per volunteer position they worked |
| `crew` | anyone approved to crew a match that was on a stage (comms, tracking, broadcast); crew on a match that never got a stage doesn't count |
| `commentators` / `trackers` / `broadcasters` | crew of that one role on a stage match (`Commentator`, `Tracker`, `Broadcaster` rows) |
| `checked_in` | anyone the check-in desk checked in |

Tournament admins of the event's non-test tournaments are among the people a
survey goes to, even if they did nothing else; their facts carry `organized`
(the tournaments they run), and they count in one results group, "Tournament
organizers" (Thomas, 2026-10-28).

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
were given to. Only wording can change (Thomas picked "wording only, logged",
2026-10-28): the survey's title, intro and after-send line, section titles and
descriptions, and question prompts. In the builder's Questions view each ⋮ has
**Fix wording…**; REST and MCP take the same fields and refuse anything else
with "only its wording can change". Every change on a sent survey writes an
audit row with `wording: {field: [old, new]}`. The first time a prompt changes
after answers came in, the question keeps `original_prompt`, `reworded_at` and
`reworded_after` (answers so far), and both the builder and results show an
**Edited** tag with them.

After someone sends, the page says what happens next: the survey's
`after_send` line (Edit details…, plain text, 500 characters), or
`plan.DEFAULT_AFTER_SEND` when blank, and whether it went anonymously or with
their name (`RespondentView.anonymous`, worked out on reload from whether a
named submitted response exists).

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
is the opposite of another key. A *parameterised* one (`check=`) is decided
from the recipient's facts and what the entry names: `positions` holds when they
worked any of its `position_ids`. `answer` is decided on the page from the
respondent's own earlier answer.

For one volunteer position, staff use an `everyone` section with the
`positions` condition rather than `each_position` (Thomas, 2026-10-28, mockup
A): the builder's Positions box picks one or more of the event's positions, and
the part shows once. `volunteered` holds for anyone with a shift;
`ran_tournament` / `not_ran_tournament` split tournament admins from everyone
else. A positions entry's `names` are copied from the event's schedule each
time it's saved, so the chips read "Only if worked as Race Proctor"; a position
from another event or community is refused, and a survey copied from another
event shows its stale positions part under the "nobody reaches this" check.

A question stores up to **3** conditions and a section up to **1**, as a JSON
list on `conditions`:

```json
[{"key": "on_stage"}, {"key": "answer", "question_id": 12, "values": [1, 2]}]
[{"key": "positions", "position_ids": [4, 7], "names": ["Race Proctor", "Admin Desk"]}]
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

The builder's time box (Thomas picked layout A, 2026-10-28) leads with "Most
people", the median of everyone's path (`BuilderSummary.typical_seconds`), and
puts the longest path and who it is on the second line. One bar on a 10-minute
scale carries both, with a tick at 5 (`plan.TARGET_SECONDS`): aim for most
people under 5, and the longest path warns over **10 minutes**
(`plan.BUDGET_SECONDS`).

**Checks** (Thomas picked layout A) is a card under the time box on a draft.
Each rule registers itself in `application/services/survey/checks.py` and runs
over the sections and the real paths of everyone who took part. The rules are:
an empty part, a part or question nobody who took part reaches, "and" in a
rating/recommend/yes-no prompt, more than 3 comment boxes on one path, a part
over 5 questions, an agree scale over 5 statements, and a required comment.
Each finding names its question or part and scrolls to it; checks that found
nothing fold into one grey line. They never block Send.

The question dialog shows a tip under Type when Agree scale is picked: agree
statements lean "agree", so "how good was it" is better as a Rating.

The **preview** opens with a paths table (Thomas picked layout A): one row per
kind of person (`SurveyService.preview_paths`, the personas of
`plan.preview_personas`: `PREVIEW_PERSONAS` bar "Every question", including a
tournament organizer, plus `position:<id>` for each of the event's volunteer
positions) with parts, questions and time.
Tapping a row switches the preview to them. The parts they won't see are
listed in grey with why (`plan.hidden_from`: the audience, or the first
condition that fails), and inside a part the questions they won't see, or
won't see until an earlier answer, show the same way.

**Missing answers** (Thomas picked layout A): Send with a required question
unanswered doesn't send. A red box above the parts lists each one
(`RespondentView.missing()`) as a link that opens its part at the question,
the part rows say "Needs an answer", and the question is marked in place. Each
entry drops off as it's answered. The service refuses the same send anyway.

**Duplicate** records each copy's origin in `SurveyQuestion.copied_from`. On
results, a question whose origin has answers shows a "Last time in <survey>"
line with that survey's average or count, linking to it (Thomas picked layout
A). REST serves `copied_from_id` on each question.

## Results report

Results (Thomas picked layout A, 2026-10-28) opens on
four tiles (responses, recommend score, overall rating, and the first
"Yes / …" question everyone saw), the response rate for players, volunteers
and crew, the group filter, the drop-off card, then the survey's own sections
(`pages/admin_tabs/admin_surveys/report.py`):

- Each question is one line: prompt and answer count, a stacked bar (1–5 and
  0–10 run red to green, choices in accent shades) and its headline number
  (`avg 4.1` with "% rated 4–5", the recommend score with its 9–10 / 0–6
  split under 50 answers, "% said yes"). A pick-many lists its choices in a
  line, since people pick several. A follow-up shows "↳" and who was asked.
- A section that repeats per tournament or position is a comparison table, one
  row per group (cards on a phone). A group 20 or more points under the
  average of the others (`OUTLIER_POINTS`, on "% rated 4–5" or "% yes") is
  flagged.
- A comment question collapses to "n comments ›", which opens
  `CommentSheet`: a side sheet (bottom sheet on a phone) with search and one
  chip per group. "Other" write-ins open the same way.
- The filter (`?group=` in the address, `group=` on REST and MCP) re-runs every
  number for one tournament's players, one position, crew or checked in. It
  lists only groups with 5 or more answers (`audiences.group_options`) and
  refuses others; a narrowed question under 5 answers shows its count only,
  and the drop-off card is left out.

## Comments inbox

The **Comments n** tab (Thomas picked B, 2026-10-28; `pages/admin_tabs/admin_surveys/inbox.py`,
`application/services/survey/comments.py`) lists every comment and "Other"
write-in, newest first. On desktop a question list sits on the left; on a phone
it's a dropdown. Above the list: search, **Unread n** / **Read n** chips, and a
chip per tag with its count; the chips' ⋮ adds or deletes a tag. Each comment
shows who (the results' rule: an anonymous one names its group only when the
group has 5 or more), its tournament or position and question, **Mark read**
(a green tick once read, which marks it unread again) and **+ Tag**.

- Read marks and tags are shared by all staff (Thomas's pick) and stored per
  answer in `SurveyCommentMark`; respondents never see them.
- Each survey starts with the starter tags Wizzrobe, Venue, Schedule, Staff and
  Praise (`STARTER_TAGS`, made the first time the inbox opens) and staff add
  their own, up to 20. Deleting a tag takes it off every comment. Adding or
  deleting a tag is audited; marking a comment isn't, since it would flood the log.
- REST `GET /api/surveys/{id}/comments` and `PATCH .../comments/{answer_id}`
  (`read`, `tags`); MCP `list_survey_comments` and `mark_survey_comment`.
  Staff only, behind the Surveys flag (no flag of its own, Thomas's pick).

## Drop-off

The Results tab shows a drop-off card (Thomas picked layout A plus a
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
the progress of people who haven't sent it. The list of parts is folded under
"Each part, in survey order (n)" so the report starts on the first screen.

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

In results, every group with fewer than 5 answers is folded into one "Other"
row (`results.fold_small`). When the folded groups still come to fewer than
5, the smallest shown groups join them, because the overall minus the shown
groups would otherwise be the small groups' answers. An anonymous comment from
a group under 5 carries no group name.

Known limits: response ids are sequential, and answering is blocked once the
event is archived.

## REST and MCP

Reads (STAFF_READ): list, get, conditions, results, responses, comments. The
conditions list carries `options` for `positions`: the current event's volunteer
positions.
Writes (STAFF_WRITE) mark a comment read or tag it, and build a draft: create, duplicate, update, delete (drafts
only), add/update/delete/move sections and questions. Sending, reminding and
closing stay buttons in the app (Thomas's call). The MCP tools mirror the
routes one for one; see [mcp-server.md](mcp-server.md#surveys) and
[rest-api.md](../reference/rest-api.md#surveys-apisurveys--surveyspy).

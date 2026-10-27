# Post-event surveys

Staff ask the people who took part in an event how it went. Each person gets
only the parts that fit what they did, so the survey stays under five minutes.
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
| `crew` | anyone who crewed a match (comms, tracking, broadcast) |
| `checked_in` | anyone the check-in desk checked in |

Question kinds: `rating` (1–5), `nps` (0–10), `yes_no`, `choice_one`,
`choice_many` (2–10 choices), `comment` (plain text, 1000 characters) and
`agree_scale` (2–10 statements, each answered Strongly disagree to Strongly
agree, stored as `{statement: 1..5}`). A required agree scale needs every
statement answered. An agree scale can't be the target of an answer condition.

On the respondent form, `rating` and `nps` are sliders (`_scale_slider` in
`theme/survey_form.py`): no handle until the first touch, numbers under each
step, both ends labelled, and a Clear link. An agree scale is a grid of radios
on a wide sheet that turns into one card per statement below 36rem (a container
query, so it follows the sheet, not the window). Results draw one diverging bar
per statement with "% agree".

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
tournament; anywhere else it holds when it holds for any of them.

On the page, a part whose section waits on an answer appears and disappears as
the answer changes (`RespondentView.shown_parts`). On submit, saved answers to
questions or parts that ended up hidden are deleted, so results only count what
the person could see.

## Sending

Only from the app: Admin → Surveys → **Send…** with a close time (default seven
days out). `send` freezes each person's facts on a `SurveyRecipient` row and
DMs them (`SURVEY_INVITE`); **Remind** DMs people who haven't answered, at most
once a day (`SURVEY_REMINDER`). Both are in the notification catalogue, so
communities can silence them. The DM link goes straight to `/survey/{id}`.

Who gets it: everyone who took part in the event (played, volunteered, crewed or
was checked in).

## Anonymity

Answers are named by default; the respondent can tick **Answer anonymously** if
the survey allows it. An anonymous submit:

- nulls `SurveyResponse.user_id` for good and keeps only the groups (the
  tournaments and positions they were in);
- stores only the day on `submitted_on`, and the recipient row keeps only the
  day it was opened and answered, so the two can't be lined up by time.

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

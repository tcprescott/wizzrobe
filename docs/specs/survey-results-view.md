# Survey results view

Status: Building · Size: full · PRs: — · Requested: 2026-10-28, Thomas Prescott

## Problem

Staff can collect survey answers but can't work through them. Today's Results
tab draws every question as a tall card and prints every comment in full, so
the SG Live survey with made-up answers runs about 13,800px on desktop. A
per-tournament or per-position question shows each group as one summary line,
so a weak tournament doesn't stand out. Comments can be searched inside one
card, but there's no way to read, sort or keep track of 250 of them.

## Scope

In:
- **Section report (A).** Results reads in the survey's own sections. Each
  question is one line with a stacked bar, its count (n) and its headline
  number (average, recommend score, or % for yes/no).
- A rating line also shows "% rated 4–5". Under about 50 answers, a recommend
  score shows its promoter / passive / detractor split rather than only the
  score.
- A question that repeats per tournament or position becomes a comparison
  table, one row per group, and a row well below the rest is flagged.
- Groups under 5 answers fold into one "Other (n)" row, so a hidden group's
  numbers can't be worked out by subtraction.
- A comment question collapses to "n comments ›". That opens a side sheet
  (a bottom sheet on phone) with search and group chips.
- A group filter at the top ("Everyone", a tournament, a position, Crew,
  Checked in) re-runs every number for that group. Groups under 5 can't be
  picked.
- The response rate shows per audience (players, volunteers, crew) under the
  headline tiles.
- The drop-off card from #749 stays, as the first card under the tiles.
- **Comments inbox (B).** A "Comments n" tab beside Results and Questions. On
  desktop a left list filters by question; on phone a dropdown does. It has
  search, Unread / Read chips and tag chips with counts.
- Each comment shows who (or "Anonymous", with its group only when that group
  has 5 or more), the question and the part. Each row has **Mark read** and
  its tags.
- Tags are staff-only and never shown to respondents. Each survey starts with a
  starter set (Praise, Bug, Venue, Schedule, Wizzrobe, Staffing) and staff can
  add their own.
- Read marks are staff-only and shared: one staff member marking a comment
  read marks it for everyone.
- Comments also include "Other" write-ins.
- REST and MCP read the tags and read state alongside each comment.

Out:
- AI themes and the "What stands out" card (option C), for now.
- Editing the DM wording (mockup D isn't picked yet).
- CSV export.
- Replying to a respondent.

## Flow

Staff:
1. Admin → Surveys → open a sent survey. It opens on **Results**: headline
   tiles, response rate by audience, the drop-off card, then the sections.
2. Pick a group in the filter to see the whole report for, say, "Super Metroid
   Any% players".
3. Press "45 comments ›" on a comment question. The side sheet lists them;
   close it to return to the same spot.
4. Open **Comments 251** to work through all of them. Filter to a question or
   Unread, tag each comment, and press **Mark read**. Tag counts show what
   comes up most.

Respondents: nothing changes.

## Mockup pick

A + B, picked 2026-10-28. A is the report by section, B is the comments inbox.
Images are in `/mnt/project-files/post-event-survey/results-ui/`:
`A-{desktop,phone}.png`, `A-sheet-{desktop,phone}.png` and
`B-{desktop,phone}.png`. Dark-mode shots will follow in the PR.

## Data and permissions

- **Feature flag:** no new flag. This rides `FeatureFlag.SURVEYS`.
- **Models (community-owned, tenant FK, cascade with the survey):**
  - `SurveyCommentTag`: survey, name (40), position. Unique per (survey, name).
  - `SurveyCommentMark`: answer, read_at, read_by, and tags as a list of tag
    ids. One row per answer. It is created on the first read or tag.
  - Both are in the leak tests.
- **Who:** STAFF reads and marks, the same gate as results today.
  `STAFF_READ` covers the REST/MCP reads and `STAFF_WRITE` covers marking.
  Non-staff get nothing.
- **Other surfaces:**
  - REST: `GET /api/surveys/{id}/comments`, plus `PATCH` to mark read and set
    tags.
  - MCP: `list_survey_comments` and `mark_survey_comment`.
  - Audit actions: none per mark, because that would be noisy. Creating or
    deleting a tag is audited.
  - No DMs and no webhooks.
- **Player input:** none new. Comment text still renders as plain text.

## PR plan

1. Report by section, group filter, folded small groups, per-audience response
   rate, and the comment side sheet. No model change. Seed: nothing new.
   Changelog file. Covers acceptance 1–7.
2. Comments inbox, with tags and read marks: models, migration, REST and MCP,
   seed rows (a few tagged or read comments), changelog, leak test. Covers
   8–12. Deletes this spec and folds it into `docs/features/surveys.md`.

## Acceptance

- [ ] 1. With the seeded survey, Results shows sections in survey order, one
  line per question, each with n and its headline number.
- [ ] 2. A per-tournament question shows a table with one row per tournament,
  and a row 20+ points under the average is flagged.
- [ ] 3. A group with under 5 answers never shows on its own. Groups like that
  are folded into "Other (n)" (test).
- [ ] 4. Choosing a group in the filter changes every number to that group's.
  Groups under 5 aren't offered.
- [ ] 5. "n comments ›" opens a side sheet with search, and closing it keeps
  the scroll position.
- [ ] 6. On a phone (393px), the report has no sideways scroll, tables become
  cards, and the side sheet is a bottom sheet.
- [ ] 7. On desktop (1440px), the seeded SG Live copy's Results is under a
  third of today's height.
- [ ] 8. The Comments tab lists every comment and "Other" write-in with counts
  per question.
- [ ] 9. Staff can tag a comment and mark it read, and the state survives a
  reload (shared by all staff).
- [ ] 10. Respondents and non-staff can't reach tags or read marks through the
  page, REST or MCP (tests).
- [ ] 11. On a phone, the question filter is a dropdown and each comment's
  actions fit on screen.
- [ ] 12. Light and dark both pass the CI a11y scan.

## Open questions

## Decisions

- 2026-10-28: Thomas picked "A + B comments inbox" for the results view.
- 2026-10-28: The research thread's recommendations are folded into A: fold
  groups under 5 into "Other", response rate per role, n beside every number,
  the recommend-score split under ~50, and "% rated 4–5".
- 2026-10-28: Thomas picked a starter tag set that staff can add to.
- 2026-10-28: Thomas picked shared read marks.
- 2026-10-28: Thomas picked no new flag; it rides `FeatureFlag.SURVEYS`.

# Survey parts for one position, and for tournament organizers

Status: Approved · Size: lite · PR: — · Requested: 2026-10-28, Thomas

## Problem

Building the SGL26 survey, Thomas wanted questions for one volunteer position
(a Proctor part, asked once) and questions for tournament organizers. Today a
volunteer part can only repeat for every position worked, and tournament
admins aren't among the people a survey goes to at all, so the organizers
can't be asked anything of their own.

## Scope

In:
- Two conditions for sections and questions: **Volunteered (any position)**
  and **Worked one of these positions**, which carries the positions picked
  (any of them).
- A repeating audience **Repeats per tournament run**: one part per
  tournament on the event someone is a tournament admin of, titled with its
  name.
- Two conditions: **Ran a tournament** and **Didn't run a tournament**.
- Tournament admins of the event's (non-test) tournaments are sent the survey,
  and their facts carry the tournaments they ran.
- The builder's preview gains a tournament organizer and one persona per
  position.
- Results: an organizer part reads as a per-tournament table like the
  per-tournament parts; the group filter gains "Tournament organizers"; the
  usual under-5 folding applies.
- REST and MCP: the new audience value and the `positions` condition shape
  (`{"key": "positions", "position_ids": [..]}`); `list_survey_conditions`
  lists the event's positions.
- A builder check that warns when a positions condition names a position
  that isn't on this event's schedule (a survey copied from another event).

Out:
- OR across different conditions (still all-must-hold).
- Per-position scoping inside a part that repeats per position.

## Mockup pick

A: the positions condition opens a Positions box like the answer follow-up,
and you can tick several. Organizer parts as in C.
`/mnt/project-files/post-event-survey/position-condition/{A,C-organizers}-{desktop,phone}.png`
(light and dark in each image).

## Acceptance

- [ ] 1. A section with "Worked one of these positions: Race Proctor" shows
  once to someone who worked a Race Proctor shift and to nobody else (test).
- [ ] 2. Picking two positions shows the part to someone who worked either
  (test).
- [ ] 3. "Volunteered (any position)" holds for anyone with a shift (test).
- [ ] 4. A tournament admin of a tournament in the event gets the survey even
  if they did nothing else, and a "Repeats per tournament run" section shows
  once per tournament they run, titled with its name (test).
- [ ] 5. "Ran a tournament" / "Didn't run a tournament" split organizers from
  everyone else (test).
- [ ] 6. A positions condition naming a position from another community or
  event is refused (test).
- [ ] 7. REST and MCP accept the new audience and the positions condition, and
  `list_survey_conditions` returns the event's positions (tests).
- [ ] 8. Preview offers "Tournament organizer" and a persona per position.
- [ ] 9. On a phone (393px) and desktop, the condition picker and the
  Positions box fit with no sideways scroll; light and dark pass the a11y scan.

## Open questions

## Decisions

- 2026-10-28 — Thomas picked "Position condition" (SGL26 survey thread): conditions, not a new audience, for one position.
- 2026-10-28 — Thomas picked "Both" for organizers: a repeating audience and a once-only condition.
- 2026-10-28 — Thomas picked A: one positions condition with a multi-pick box.
- No new flag; it rides SURVEYS.

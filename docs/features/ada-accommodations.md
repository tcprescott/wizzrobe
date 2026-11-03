# ADA accommodations

Members can ask a community for an ADA accommodation from their profile, and
that community's STAFF track each request with a status and private notes.
Behind `FeatureFlag.ADA_ACCOMMODATIONS` (ships dark).

## Key files

| File | Role |
|---|---|
| [`models/accommodation.py`](../../models/accommodation.py) | `AccommodationRequest` |
| [`models/enums.py`](../../models/enums.py) | `AccommodationStatus` |
| [`application/repositories/accommodation_repository.py`](../../application/repositories/accommodation_repository.py) | `AccommodationRepository` |
| [`application/services/accommodation_service.py`](../../application/services/accommodation_service.py) | `AccommodationService` — lifecycle rules, STAFF gate, audit |
| [`pages/home_tabs/accommodation_section.py`](../../pages/home_tabs/accommodation_section.py) | The profile card |
| [`pages/admin_tabs/admin_accommodations.py`](../../pages/admin_tabs/admin_accommodations.py) | The staff queue (a sub-tab of Admin → Members) |
| [`pages/admin_tabs/admin_users.py`](../../pages/admin_tabs/admin_users.py) | Sub-tabs, the "Needs ADA accommodation" filter, the ADA column |
| [`theme/accommodation_copy.py`](../../theme/accommodation_copy.py) | Status labels/chips and the privacy disclaimer, shared by both sides |

## Scope and visibility

- **Per community and event.** One `AccommodationRequest` per `(tenant, event, user)`. A person
  who belongs to two communities has two independent requests; neither
  community's staff can see the other's.
- **STAFF only** on the admin side (`AuthService.is_staff`, so super-admins
  too). The one exception is the run-sheet `ADA:` line below: **PROCTORs** see that a
  request is Arranged, and nothing else. They never get the staff notes:
  `arranged_notes_for` returns `None` per player for a proctor, so the text
  never reaches their browser. The Users tab is already STAFF-only; the
  service checks again on every read and write.
- **The requester** sees their own checkbox, details and status, never the staff
  notes.
- **No free text leaves the app.** Audit rows carry `request_id`, `user_id`,
  `status` (and `previous_status` on a status change) only. Nothing is
  published on the event bus, so webhooks never see it, and there is no REST
  or MCP surface. The staff DMs (below) name the member and nothing else.
- Both surfaces show the same disclaimer (`privacy_disclaimer(community)`):
  only that community's staff can read the notes, proctors see an `ADA:` line
  on their run sheets and a checklist button once it's arranged but not what
  the notes say, privacy can't be guaranteed, so don't
  write anything sensitive or personal; contact staff directly instead.

## Lifecycle

| From | Action | To |
|---|---|---|
| (none) | member ticks the box | `NEW` |
| `NEW` / `ACKNOWLEDGED` / `ARRANGED` | member unticks | `WITHDRAWN` (details cleared, staff notes kept) |
| any open status | staff remove the member from the community | `WITHDRAWN` (same clearing, audited with `source: member_removed`) |
| `WITHDRAWN` | member ticks again | `NEW` (same row) |
| `ACKNOWLEDGED` | member changes details | `NEW` |
| `ARRANGED` | member changes details | stays `ARRANGED`, `changed_since_arranged` set, `arranged_details` keeps the arranged text |
| any open status | staff Update | `NEW` / `ACKNOWLEDGED` / `ARRANGED`, and clears `changed_since_arranged` (audited `accommodation.change_reviewed`) |

An Arranged request keeps its run-sheet `ADA:` line through an edit on purpose: a typo
fixed on the morning of the event must not take the accommodation off the
proctors' view. Typing the details back to what was arranged clears the flag.
Withdrawing clears it too.

Staff cannot withdraw on a member's behalf or move a withdrawn request back
out; they can still edit its notes. Re-saving identical details is a no-op.
Details are capped at 2000 characters, staff notes at 4000. Creating a
request requires community membership.

## Surfaces

- **Profile** (`/home/profile`): an "ADA accommodation" card. The checkbox
  saves at once; details save on blur and after a pause in typing. Shows the
  staff status chip while the request is open.
- **Admin → Members**: with the flag live the tab gains **Members** and **ADA
  requests (n)** sub-tabs, where *n* counts requests needing action: New,
  Acknowledged, and Arranged-but-changed (`action_needed_count`), so it reaches
  zero once everything is arranged. The member table gains an ADA column (the
  request's status, "Arranged, changed" when flagged; clicking it opens the
  request) and a "Needs ADA accommodation" filter. The ADA requests sub-tab
  lists open requests oldest first, with "Show withdrawn", CSV export and an
  Update dialog for status and staff notes. A changed request shows a
  "Changed since arranged" chip, and its dialog shows the arranged text beside
  the current one with a **Save and mark reviewed** button.
  `/admin/members?ada_request=<id>` opens that request's dialog on arrival (a
  stale id says the request no longer exists; with the feature off, the
  Users tab says ADA requests aren't turned on).

- **Admin → Match Schedule and Volunteer → Proctor Station**: a match with a player
  whose request is **Arranged** shows an `ADA: <name>` line on its run-sheet
  row (`_ADA` in `theme/tables/match_run_sheet.py`, plain text, not a
  control). The match sheet's **Ready to play?** checklist adds a
  `<name>: ADA accommodation` button under Players
  (`theme/dialog/match_sheet.py`), which opens a popup with the **staff
  notes** only, never the member's own details. Only STAFF and PROCTORs see
  either; a tournament admin, crew coordinator or stream manager on the same
  admin board gets neither. A proctor's popup says staff have arranged
  something and to check with staff, with no notes. Other boards (home schedule, player
  dashboards, the room kiosk) never request the data
  (`MatchTableView(show_accommodations=True)` is opt-in).

## Notifications

Staff get a DM (with an **Open the request** button to
`/admin/members?ada_request=<id>`) when a request opens or reopens, at most once
per request per 24 h (`staff_notified_at`, `STAFF_DM_WINDOW`) so unticking and
re-ticking the box doesn't DM everyone again, and when a member first changes
an Arranged request. Later autosaves of the same edit stay
quiet. The DM names the member and never carries their details, since it's
mirrored to web-push too. The member isn't DM'd on status changes; their
profile card shows the current status, and a note when their change is waiting
for staff.

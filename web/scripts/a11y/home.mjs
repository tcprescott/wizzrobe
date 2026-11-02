/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'home', path: '/t/sgl/home' },
  { name: 'home-event', path: '/t/sgl/e/sgl26/home' },
  { name: 'home-pick-time', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: 'Pick a time' }).click(), wait: 'role=dialog' },
  { name: 'home-review', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: 'Review' }).click(), wait: 'role=dialog' },
  { name: 'home-running-late', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: 'Running late' }).first().click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/me/home": {
    "zone": "America/New_York",
    "next_up": {
      "assignment_id": null,
      "status": "scheduled",
      "stage": "Stage 2",
      "station": "14",
      "room_url": "https://racetime.gg/smz3/clever-link-1234",
      "needs_acknowledgment": false,
      "late_minutes": null,
      "can_report_late": true,
      "approved": null,
      "checked_in": false,
      "match_id": 101,
      "kind": "match",
      "starts_at": "{{now+20m}}",
      "ends_at": "{{now+80m}}",
      "title": "vs Kaizo",
      "detail": "SMZ3 · Round 2"
    },
    "today": [
      {
        "assignment_id": null,
        "status": "scheduled",
        "stage": "Stage 1",
        "station": null,
        "room_url": null,
        "needs_acknowledgment": false,
        "late_minutes": null,
        "can_report_late": false,
        "approved": false,
        "checked_in": false,
        "match_id": 102,
        "kind": "crew",
        "starts_at": "{{now+3h}}",
        "ends_at": "{{now+4h}}",
        "title": "Commentary",
        "detail": "Ellie vs Marth · OoTR Top 8"
      },
      {
        "assignment_id": 7,
        "status": null,
        "stage": null,
        "station": null,
        "room_url": null,
        "needs_acknowledgment": false,
        "late_minutes": null,
        "can_report_late": false,
        "approved": null,
        "checked_in": false,
        "match_id": null,
        "kind": "shift",
        "starts_at": "{{now+5h}}",
        "ends_at": "{{now+7h}}",
        "title": "Check-in desk",
        "detail": "Front desk"
      }
    ],
    "needs_you": [
      {
        "kind": "book",
        "title": "Pick a time vs Pidge",
        "detail": "ALTTPR Open · Round 3 · Book by Sat 6:00 PM",
        "match_id": null,
        "request_id": null,
        "bookable": {
          "source": "bracket",
          "key": 11,
          "tournament_id": 3,
          "tournament": "ALTTPR Open",
          "source_label": null,
          "opponent": "Pidge",
          "round_label": "Round 3",
          "window_text": "Book by Sat 6:00 PM",
          "blocked": null,
          "if_needed": false,
          "best_of": 1
        },
        "proposed_at": null,
        "original_at": null,
        "reason": null,
        "agreed": false
      },
      {
        "kind": "agree",
        "title": "Kaizo asked to move your match",
        "detail": "SMZ3",
        "match_id": 104,
        "request_id": 5,
        "bookable": null,
        "proposed_at": "{{now+2d}}",
        "original_at": "{{now+30h}}",
        "reason": "My flight lands late on Saturday.",
        "agreed": false
      },
      {
        "kind": "acknowledge",
        "title": "Confirm you can play vs Marth",
        "detail": "ALTTPR Open · Round 2",
        "match_id": 103,
        "request_id": null,
        "bookable": null,
        "proposed_at": null,
        "original_at": null,
        "reason": null,
        "agreed": false
      }
    ]
  },
  "/api/me/bookable/bracket/11/check": {
    "refusal": null,
    "clashes": [
      "Kaizo is playing SMZ3 then."
    ]
  }
}

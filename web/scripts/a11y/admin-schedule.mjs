/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-schedule', path: '/t/sgl/admin/match-schedule?day=2026-10-24' },
  { name: 'admin-schedule-match', path: '/t/sgl/admin/match-schedule/301?day=2026-10-24' },
  { name: 'admin-schedule-rewind', path: '/t/sgl/admin/match-schedule/301?day=2026-10-24', act: (p) => p.getByRole('button', { name: /^More for .* vs / }).click().then(() => p.getByRole('menuitem', { name: 'Rewind…' }).click()), wait: 'role=dialog' },
  { name: 'admin-schedule-request', path: '/t/sgl/admin/match-schedule?day=2026-10-24', act: (p) => p.getByRole('button', { name: 'Review' }).first().click(), wait: 'role=dialog' },
  { name: 'admin-schedule-decline', path: '/t/sgl/admin/match-schedule?day=2026-10-24', act: (p) => p.getByRole('button', { name: 'Decline' }).first().click(), wait: 'role=dialog' },
  { name: 'admin-schedule-unbooked', path: '/t/sgl/admin/match-schedule?day=2026-10-24&view=unbooked' },
  { name: 'admin-schedule-book', path: '/t/sgl/admin/match-schedule/unbooked/challonge/11?day=2026-10-24&view=unbooked', act: (p) => p.getByLabel('Date', { exact: true }).fill('2026-10-24').then(() => p.getByLabel('Time', { exact: true }).fill('15:30')), wait: 'text=Check this time' },
  { name: 'admin-schedule-link', path: '/t/sgl/admin/match-schedule/unbooked/challonge/11?day=2026-10-24&view=unbooked', act: (p) => p.getByRole('button', { name: /^More for / }).click().then(() => p.getByRole('menuitem', { name: 'Link an existing match…' }).click()), wait: 'role=dialog' },
  { name: 'admin-schedule-new', path: '/t/sgl/admin/match-schedule/new?day=2026-10-24', act: (p) => p.getByLabel('Players', { exact: true }).fill('a'), wait: 'role=button[name="Kai"]' },
  { name: 'admin-schedule-stations', path: '/t/sgl/admin/match-schedule/301?day=2026-10-24', act: (p) => p.getByRole('button', { name: 'Assign' }).click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/admin/match-schedule": {
    "zone": "America/New_York",
    "days": [
      "2026-10-22",
      "2026-10-23",
      "2026-10-24",
      "2026-10-25"
    ],
    "tournaments": [
      {
        "id": 1,
        "name": "ALTTPR Open"
      },
      {
        "id": 2,
        "name": "SMZ3"
      }
    ],
    "stages": [
      {
        "id": 1,
        "name": "Stage 1"
      },
      {
        "id": 2,
        "name": "Stage 2"
      },
      {
        "id": 3,
        "name": "Stage 3"
      }
    ],
    "scoped_tournaments": null,
    "can_create": true,
    "groups": [
      {
        "label": "Needs a decision",
        "folded_by_default": false,
        "rows": [
          {
            "id": 300,
            "tournament_id": 1,
            "tournament": "Super Metroid",
            "tournament_name": "Super Metroid",
            "bracket": "Round 1",
            "game": null,
            "best_of": null,
            "set_label": null,
            "scheduled_at": "2026-10-24T17:30:00Z",
            "started_at": null,
            "until_at": null,
            "until_estimated": false,
            "overrun": false,
            "state": "Finished",
            "status": "Played on Challonge",
            "tone": "pending",
            "group": "Needs a decision",
            "players": [
              {
                "id": 3001,
                "user_id": 3001,
                "name": "Kai",
                "station": null,
                "ada": false
              },
              {
                "id": 3002,
                "user_id": 3002,
                "name": "Morgan Wu",
                "station": null,
                "ada": false
              }
            ],
            "placeholders": [],
            "stage_id": 2,
            "stage": "Stage 2",
            "stream_candidate": false,
            "seat_line": null,
            "crew": [],
            "late": [],
            "comments": {},
            "winners": [],
            "pending_result": null,
            "next_step": null,
            "seed_url": null,
            "seed_code": null,
            "seed_rolling": false,
            "seed_error": null,
            "is_racetime": false,
            "played_on_site": {
              "site": "Challonge",
              "winner": "Kai",
              "winner_user_id": 3001,
              "no_winner": false
            },
            "racetime_held": null,
            "needs_review": false,
            "can": {
              "edit": true,
              "run": true,
              "confirm": true,
              "stage": true,
              "crew": true
            }
          }
        ]
      },
      {
        "label": "Live now",
        "folded_by_default": false,
        "rows": [
          {
            "id": 302,
            "tournament_id": 1,
            "tournament": "ALTTPR Open",
            "tournament_name": "ALTTPR Open",
            "bracket": "Quarterfinal",
            "game": null,
            "best_of": null,
            "set_label": null,
            "scheduled_at": "2026-10-24T18:00:00Z",
            "started_at": "2026-10-24T18:02:00Z",
            "until_at": null,
            "until_estimated": false,
            "overrun": false,
            "state": "Started",
            "status": "Live",
            "tone": "live",
            "group": "Live now",
            "players": [
              {
                "id": 3021,
                "user_id": 3021,
                "name": "Sam Ruiz",
                "station": null,
                "ada": false
              },
              {
                "id": 3022,
                "user_id": 3022,
                "name": "Alex Chen",
                "station": null,
                "ada": false
              }
            ],
            "placeholders": [],
            "stage_id": 1,
            "stage": "Stage 1",
            "stream_candidate": false,
            "seat_line": null,
            "crew": [],
            "late": [],
            "comments": {},
            "winners": [
              {
                "id": 3021,
                "name": "Sam Ruiz"
              },
              {
                "id": 3022,
                "name": "Alex Chen"
              }
            ],
            "pending_result": null,
            "next_step": null,
            "seed_url": null,
            "seed_code": null,
            "seed_rolling": false,
            "seed_error": null,
            "is_racetime": false,
            "played_on_site": null,
            "racetime_held": null,
            "needs_review": false,
            "can": {
              "edit": true,
              "run": true,
              "confirm": true,
              "stage": true,
              "crew": true
            }
          }
        ]
      },
      {
        "label": "Up next",
        "folded_by_default": false,
        "rows": [
          {
            "id": 301,
            "tournament_id": 1,
            "tournament": "SMZ3",
            "tournament_name": "SMZ3",
            "bracket": "Round 2",
            "game": null,
            "best_of": null,
            "set_label": null,
            "scheduled_at": "2026-10-24T18:30:00Z",
            "started_at": null,
            "until_at": null,
            "until_estimated": false,
            "overrun": false,
            "state": "Checked In",
            "status": "Checked in",
            "tone": "info",
            "group": "Up next",
            "players": [
              {
                "id": 3011,
                "user_id": 3011,
                "name": "Priya N.",
                "station": null,
                "ada": false
              },
              {
                "id": 3012,
                "user_id": 3012,
                "name": "Jordan Lee",
                "station": null,
                "ada": true
              }
            ],
            "placeholders": [],
            "stage_id": 3,
            "stage": "Stage 3",
            "stream_candidate": false,
            "seat_line": null,
            "crew": [
              {
                "key": "commentators",
                "label": "Commentary",
                "count": "1/2",
                "short": true,
                "unapproved": false,
                "approvable": true,
                "people": [
                  {
                    "id": 71,
                    "name": "Casey M.",
                    "approved": true,
                    "with": null
                  },
                  {
                    "id": 72,
                    "name": "Rin T.",
                    "approved": false,
                    "with": null
                  }
                ]
              }
            ],
            "late": [
              {
                "label": "Jordan Lee ~10 min late · said 1:22 PM",
                "note": "Bus"
              }
            ],
            "comments": {
              "player": 1,
              "staff": 1
            },
            "winners": [],
            "pending_result": null,
            "next_step": {
              "event": "start",
              "label": "Start",
              "primary": true,
              "rolling": false
            },
            "seed_url": null,
            "seed_code": null,
            "seed_rolling": false,
            "seed_error": null,
            "is_racetime": false,
            "played_on_site": null,
            "racetime_held": null,
            "needs_review": false,
            "can": {
              "edit": true,
              "run": true,
              "confirm": true,
              "stage": true,
              "crew": true
            }
          },
          {
            "id": 303,
            "tournament_id": 1,
            "tournament": "FF Randomizer",
            "tournament_name": "FF Randomizer",
            "bracket": null,
            "game": null,
            "best_of": null,
            "set_label": null,
            "scheduled_at": "2026-10-24T19:00:00Z",
            "started_at": null,
            "until_at": null,
            "until_estimated": false,
            "overrun": false,
            "state": "Scheduled",
            "status": "Scheduled",
            "tone": "neutral",
            "group": "Up next",
            "players": [
              {
                "id": 3031,
                "user_id": 3031,
                "name": "Lee",
                "station": null,
                "ada": false
              },
              {
                "id": 3032,
                "user_id": 3032,
                "name": "Marco P.",
                "station": null,
                "ada": false
              }
            ],
            "placeholders": [],
            "stage_id": null,
            "stage": null,
            "stream_candidate": false,
            "seat_line": "No stations yet",
            "crew": [],
            "late": [],
            "comments": {},
            "winners": [],
            "pending_result": null,
            "next_step": {
              "event": "seat",
              "label": "Check in",
              "primary": false,
              "rolling": false
            },
            "seed_url": null,
            "seed_code": null,
            "seed_rolling": false,
            "seed_error": null,
            "is_racetime": false,
            "played_on_site": null,
            "racetime_held": null,
            "needs_review": false,
            "can": {
              "edit": true,
              "run": true,
              "confirm": true,
              "stage": true,
              "crew": true
            }
          }
        ]
      },
      {
        "label": "Confirmed",
        "folded_by_default": true,
        "rows": [
          {
            "id": 304,
            "tournament_id": 1,
            "tournament": "OoTR",
            "tournament_name": "OoTR",
            "bracket": null,
            "game": null,
            "best_of": null,
            "set_label": null,
            "scheduled_at": "2026-10-24T16:00:00Z",
            "started_at": null,
            "until_at": null,
            "until_estimated": false,
            "overrun": false,
            "state": "Confirmed",
            "status": "Confirmed",
            "tone": "ok",
            "group": "Confirmed",
            "players": [
              {
                "id": 3041,
                "user_id": 3041,
                "name": "Riley",
                "station": null,
                "ada": false
              },
              {
                "id": 3042,
                "user_id": 3042,
                "name": "Dana Ortiz",
                "station": null,
                "ada": false
              }
            ],
            "placeholders": [],
            "stage_id": null,
            "stage": null,
            "stream_candidate": false,
            "seat_line": null,
            "crew": [],
            "late": [],
            "comments": {},
            "winners": [],
            "pending_result": null,
            "next_step": null,
            "seed_url": null,
            "seed_code": null,
            "seed_rolling": false,
            "seed_error": null,
            "is_racetime": false,
            "played_on_site": null,
            "racetime_held": null,
            "needs_review": false,
            "can": {
              "edit": true,
              "run": true,
              "confirm": true,
              "stage": true,
              "crew": true
            }
          }
        ]
      }
    ],
    "needs_you": [
      {
        "kind": "request",
        "match_id": 303,
        "has_result": null,
        "request": {
          "id": 41,
          "kind": "reschedule",
          "requested_by": "Lee",
          "players": [
            "Lee",
            "Morgan"
          ],
          "title": null,
          "tournament": "SMZ3",
          "scheduled_at": "2026-10-24T20:00:00Z",
          "proposed_at": "2026-10-24T23:00:00Z",
          "reason": "My flight lands at 2pm, so I can't make the morning slot.",
          "opponent_agreed": true,
          "created_at": "2026-10-24T15:00:00Z",
          "clashes": [
            "Morgan plays ALTTPR Open at 16:30."
          ]
        }
      },
      {
        "kind": "request",
        "match_id": 301,
        "has_result": null,
        "request": {
          "id": 42,
          "kind": "cancel",
          "requested_by": "Priya N.",
          "players": [
            "Priya N.",
            "Jordan Lee"
          ],
          "title": null,
          "tournament": "SMZ3",
          "scheduled_at": "2026-10-24T18:30:00Z",
          "proposed_at": null,
          "reason": "I'm sick and won't be at the venue today.",
          "opponent_agreed": false,
          "created_at": "2026-10-24T16:00:00Z",
          "clashes": []
        }
      },
      {
        "kind": "site",
        "match_id": 300,
        "has_result": null,
        "request": null
      },
      {
        "kind": "late",
        "match_id": 303,
        "has_result": null,
        "request": null
      }
    ]
  },
  "/api/admin/match-schedule/matches/301": {
    "id": 301,
    "tournament_id": 1,
    "tournament": "SMZ3",
    "tournament_name": "SMZ3",
    "bracket": "Round 2",
    "game": null,
    "best_of": null,
    "set_label": null,
    "scheduled_at": "2026-10-24T18:30:00Z",
    "started_at": null,
    "until_at": null,
    "until_estimated": false,
    "overrun": false,
    "state": "Checked In",
    "status": "Checked in",
    "tone": "info",
    "group": "Up next",
    "players": [
      {
        "id": 3011,
        "user_id": 3011,
        "name": "Priya N.",
        "station": null,
        "ada": false
      },
      {
        "id": 3012,
        "user_id": 3012,
        "name": "Jordan Lee",
        "station": null,
        "ada": true
      }
    ],
    "placeholders": [],
    "stage_id": 3,
    "stage": "Stage 3",
    "stream_candidate": false,
    "seat_line": null,
    "crew": [
      {
        "key": "commentators",
        "label": "Commentary",
        "count": "1/2",
        "short": true,
        "unapproved": false,
        "approvable": true,
        "people": [
          {
            "id": 71,
            "name": "Casey M.",
            "approved": true,
            "with": null
          },
          {
            "id": 72,
            "name": "Rin T.",
            "approved": false,
            "with": null
          }
        ]
      }
    ],
    "late": [
      {
        "label": "Jordan Lee ~10 min late · said 1:22 PM",
        "note": "Bus"
      }
    ],
    "comments": {
      "player": 1,
      "staff": 1
    },
    "winners": [],
    "pending_result": null,
    "next_step": {
      "event": "roll",
      "label": "Roll seed",
      "primary": true,
      "rolling": false
    },
    "seed_url": null,
    "seed_code": null,
    "seed_rolling": false,
    "seed_error": null,
    "is_racetime": false,
    "played_on_site": null,
    "racetime_held": null,
    "needs_review": false,
    "can": {
      "edit": true,
      "run": true,
      "confirm": true,
      "stage": true,
      "crew": true
    },
    "zone": "America/New_York",
    "description": null,
    "state_since": "2026-10-24T18:20:00Z",
    "checklist": [
      {
        "key": "players",
        "label": "Players confirmed",
        "ready": true,
        "detail": "Everyone has confirmed"
      },
      {
        "key": "stations",
        "label": "Stations",
        "ready": null,
        "detail": "Priya N. 4"
      },
      {
        "key": "stage",
        "label": "Stage",
        "ready": true,
        "detail": "Stage 3"
      },
      {
        "key": "seed",
        "label": "Seed",
        "ready": false,
        "detail": "Not rolled"
      },
      {
        "key": "crew",
        "label": "Crew",
        "ready": false,
        "detail": "1 more needed, 1 waiting for approval below"
      }
    ],
    "rewind_to": [
      {
        "step": "scheduled",
        "explanation": "Clears the check-in."
      }
    ],
    "seed_dm_blocked": [],
    "stage_url": null,
    "ada_notes": {
      "3012": "Needs a seat near the aisle."
    }
  },
  "/api/matches/301/comments": [
    {
      "id": 7,
      "kind": "player",
      "body": "Running about 10 late, bus.",
      "author": "Jordan Lee",
      "own": false,
      "created_at": "2026-10-24T18:05:00Z",
      "can_delete": true
    },
    {
      "id": 8,
      "kind": "staff_private",
      "body": "Hold Stage 3 if he's past 13:45.",
      "author": "Sam",
      "own": true,
      "created_at": "2026-10-24T18:10:00Z",
      "can_delete": true
    }
  ],
  "/api/admin/match-schedule/form": {
    "match_id": null,
    "synced_from_speedgaming": false,
    "tournaments": [
      {
        "id": 1,
        "name": "ALTTPR Open",
        "enrolled": [
          3001,
          3002,
          3003,
          3004
        ],
        "players_per_match": 2
      },
      {
        "id": 2,
        "name": "SMZ3",
        "enrolled": [
          3005,
          3006
        ],
        "players_per_match": 2
      }
    ],
    "stages": [
      {
        "id": 1,
        "name": "Stage 1"
      },
      {
        "id": 2,
        "name": "Stage 2"
      },
      {
        "id": 3,
        "name": "Stage 3"
      }
    ],
    "people": [
      {
        "id": 3001,
        "name": "Kai"
      },
      {
        "id": 3002,
        "name": "Morgan Wu"
      },
      {
        "id": 3003,
        "name": "Ana B."
      },
      {
        "id": 3004,
        "name": "Theo K."
      },
      {
        "id": 3005,
        "name": "Priya N."
      },
      {
        "id": 3006,
        "name": "Jordan Lee"
      },
      {
        "id": 3010,
        "name": "Casey M."
      }
    ],
    "broadcasters": false,
    "values": {
      "tournament_id": null,
      "stage_id": null,
      "scheduled_at": null,
      "player_ids": [],
      "commentator_ids": [],
      "tracker_ids": [],
      "broadcaster_ids": [],
      "title": "",
      "description": "",
      "comment": "",
      "is_stream_candidate": false
    }
  },
  "/api/admin/match-schedule/unbooked": [
    {
      "source": "challonge",
      "source_label": "Challonge",
      "key": 11,
      "tournament_id": 1,
      "tournament": "ALTTPR Open",
      "round_label": "Quarterfinal",
      "players": [
        "Ana B.",
        "Theo K."
      ],
      "player_ids": [
        3003,
        3004
      ],
      "state": "ready",
      "unlinked": [],
      "window_text": null,
      "best_of": 1,
      "games_booked": 0,
      "link_candidates": [
        {
          "id": 303,
          "scheduled_at": "2026-10-24T18:00:00Z",
          "stage": "Stage 1"
        }
      ],
      "bracket_url": "https://challonge.com/alttpr26"
    },
    {
      "source": "challonge",
      "source_label": "Challonge",
      "key": 12,
      "tournament_id": 1,
      "tournament": "ALTTPR Open",
      "round_label": "Quarterfinal",
      "players": [
        "Kai",
        "Rin T."
      ],
      "player_ids": [
        3001
      ],
      "state": "needs_account",
      "unlinked": [
        "Rin T."
      ],
      "window_text": null,
      "best_of": 1,
      "games_booked": 0,
      "link_candidates": [],
      "bracket_url": null
    },
    {
      "source": "challonge",
      "source_label": "Challonge",
      "key": 13,
      "tournament_id": 1,
      "tournament": "ALTTPR Open",
      "round_label": "Semifinal",
      "players": [],
      "player_ids": [],
      "state": "tbd",
      "unlinked": [],
      "window_text": null,
      "best_of": 1,
      "games_booked": 0,
      "link_candidates": [],
      "bracket_url": null
    },
    {
      "source": "bracket",
      "source_label": "Wizzrobe bracket",
      "key": 21,
      "tournament_id": 2,
      "tournament": "SMZ3",
      "round_label": "Round 3",
      "players": [
        "Priya N.",
        "Jordan Lee"
      ],
      "player_ids": [
        3005,
        3006
      ],
      "state": "ready",
      "unlinked": [],
      "window_text": "Round 3 books Fri 18:00 to Sat 22:00",
      "best_of": 3,
      "games_booked": 1,
      "link_candidates": [],
      "bracket_url": null
    }
  ],
  "/api/admin/match-schedule/check-time": {
    "window": null,
    "clashes": [
      "Theo K. plays SMZ3 Round 2 at 15:30"
    ]
  }
}

/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'work', path: '/t/sgl/work' },
  { name: 'work-desk', path: '/t/sgl/work/desk' },
  { name: 'work-desk-link', path: '/t/sgl/work/desk', act: (p) => p.getByRole('button', { name: 'Check in Alexis Moreau' }).click(), wait: 'role=dialog' },
  { name: 'work-desk-walk-up', path: '/t/sgl/work/desk', act: (p) => p.getByRole('button', { name: 'Walk-up' }).click(), wait: 'role=dialog' },
  { name: 'work-desk-menu', path: '/t/sgl/work/desk', act: (p) => p.getByRole('button', { name: 'More for Al Kim' }).click(), wait: 'role=menu' },
  { name: 'work-schedule', path: '/t/sgl/work/schedule' },
  { name: 'work-schedule-columns', path: '/t/sgl/work/schedule', act: (p) => p.getByRole('button', { name: /Columns/ }).click(), wait: 'role=dialog' },
  { name: 'work-schedule-shift', path: '/t/sgl/work/schedule', act: (p) => p.getByRole('button', { name: /^Check-in, .*Sam/ }).click(), wait: 'role=dialog' },
  { name: 'work-availability', path: '/t/sgl/work/availability' },
  { name: 'work-proctor', path: '/t/sgl/work/proctor?day=2026-10-24' },
  { name: 'work-proctor-match', path: '/t/sgl/work/proctor/301?day=2026-10-24' },
]

export const fixtures = {
  "/api/work/proctor": {
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
    "can_create": false,
    "groups": [
      {
        "label": "Late, check in now",
        "folded_by_default": false,
        "rows": [
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
            "group": "Late, check in now",
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
            "late": [
              {
                "label": "Not checked in, 10 min past",
                "note": null
              }
            ],
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
              "edit": false,
              "run": true,
              "confirm": false,
              "stage": false,
              "crew": false
            }
          }
        ]
      },
      {
        "label": "Ready to start",
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
            "group": "Ready to start",
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
            "crew": [],
            "late": [
              {
                "label": "Jordan Lee ~10 min late \u00b7 said 1:22 PM",
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
              "edit": false,
              "run": true,
              "confirm": false,
              "stage": false,
              "crew": false
            }
          }
        ]
      },
      {
        "label": "In play",
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
            "group": "In play",
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
              "edit": false,
              "run": true,
              "confirm": false,
              "stage": false,
              "crew": false
            }
          }
        ]
      },
      {
        "label": "Done",
        "folded_by_default": true,
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
            "group": "Done",
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
              "edit": false,
              "run": true,
              "confirm": false,
              "stage": false,
              "crew": false
            }
          },
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
            "group": "Done",
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
              "edit": false,
              "run": true,
              "confirm": false,
              "stage": false,
              "crew": false
            }
          }
        ]
      }
    ],
    "needs_you": []
  },
  "/api/work/proctor/matches/301": {
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
    "group": "Ready to start",
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
    "crew": [],
    "late": [
      {
        "label": "Jordan Lee ~10 min late \u00b7 said 1:22 PM",
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
      "edit": false,
      "run": true,
      "confirm": false,
      "stage": false,
      "crew": false
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
  "/api/work": {
    "zone": "America/Denver",
    "desk": {
      "open_events": 1,
      "event_id": 1,
      "event_name": "SpeedGaming Live",
      "checked_in": 412,
      "expected": 980
    },
    "schedule": {
      "on_shift_now": 6,
      "my_next_shift_at": "{{now+2h}}"
    },
    "availability": {
      "windows": 2
    },
    "proctor": true
  },
  "/api/check-in/events": [
    {
      "id": 1,
      "name": "SpeedGaming Live",
      "status": "open",
      "matcherino_venue_id": 55,
      "last_synced_at": "{{now-2m}}",
      "last_sync_error": null,
      "last_sync_count": 980
    }
  ],
  "/api/check-in/events/1/desk": {
    "zone": "America/Denver",
    "event": {
      "id": 1,
      "name": "SpeedGaming Live",
      "status": "open",
      "matcherino_venue_id": 55,
      "last_synced_at": "{{now-2m}}",
      "last_sync_error": null,
      "last_sync_count": 980
    },
    "can_manage": true,
    "rows": [
      {
        "id": 1,
        "event_id": 1,
        "display_name": "Alex Chen",
        "source": "matcherino",
        "user_id": 101,
        "user_name": "Alex Chen",
        "link_method": "discord_id",
        "badges": [
          "Base"
        ],
        "lanyard": "base",
        "comp_reasons": [],
        "withdrawn_at": null,
        "checked_in_at": null,
        "checked_in_by_name": null,
        "dm_status": null,
        "badge_codes": [
          "SGL-0001"
        ],
        "username": "alexc",
        "has_discord": true,
        "suggestions": 0,
        "signed_in_with": "discord"
      },
      {
        "id": 2,
        "event_id": 1,
        "display_name": "Alexis Moreau",
        "source": "matcherino",
        "user_id": null,
        "user_name": null,
        "link_method": null,
        "badges": [
          "VIP"
        ],
        "lanyard": "vip",
        "comp_reasons": [],
        "withdrawn_at": null,
        "checked_in_at": null,
        "checked_in_by_name": null,
        "dm_status": null,
        "badge_codes": [
          "SGL-0002"
        ],
        "username": null,
        "has_discord": false,
        "suggestions": 1,
        "signed_in_with": "discord"
      },
      {
        "id": 3,
        "event_id": 1,
        "display_name": "Alex “Zeldo” Park",
        "source": "comp",
        "user_id": 103,
        "user_name": "Zeldo",
        "link_method": "discord_id",
        "badges": [],
        "lanyard": "volunteer",
        "comp_reasons": [
          "volunteer"
        ],
        "withdrawn_at": null,
        "checked_in_at": "{{now-10m}}",
        "checked_in_by_name": "Sam",
        "dm_status": "sent",
        "badge_codes": [
          "SGL-0003"
        ],
        "username": "zeldo",
        "has_discord": true,
        "suggestions": 0,
        "signed_in_with": "discord"
      },
      {
        "id": 4,
        "event_id": 1,
        "display_name": "Alexandra Ruiz",
        "source": "matcherino",
        "user_id": 104,
        "user_name": "Alexandra",
        "link_method": "discord_id",
        "badges": [
          "Day Pass"
        ],
        "lanyard": "day_pass",
        "comp_reasons": [],
        "withdrawn_at": null,
        "checked_in_at": null,
        "checked_in_by_name": null,
        "dm_status": null,
        "badge_codes": [
          "SGL-0004"
        ],
        "username": "alexandra",
        "has_discord": true,
        "suggestions": 0,
        "signed_in_with": "discord"
      },
      {
        "id": 5,
        "event_id": 1,
        "display_name": "Al Kim",
        "source": "walk_up",
        "user_id": 105,
        "user_name": "Al Kim",
        "link_method": "discord_id",
        "badges": [],
        "lanyard": "staff",
        "comp_reasons": [
          "staff"
        ],
        "withdrawn_at": null,
        "checked_in_at": "{{now-8m}}",
        "checked_in_by_name": "Sam",
        "dm_status": "sent",
        "badge_codes": [
          "SGL-0005"
        ],
        "username": "alkim",
        "has_discord": true,
        "suggestions": 0,
        "signed_in_with": "discord"
      },
      {
        "id": 6,
        "event_id": 1,
        "display_name": "Refunded Rae",
        "source": "matcherino",
        "user_id": null,
        "user_name": null,
        "link_method": null,
        "badges": [],
        "lanyard": null,
        "comp_reasons": [],
        "withdrawn_at": "{{now-1d}}",
        "checked_in_at": null,
        "checked_in_by_name": null,
        "dm_status": null,
        "badge_codes": [
          "SGL-0006"
        ],
        "username": null,
        "has_discord": false,
        "suggestions": 0,
        "signed_in_with": "discord"
      }
    ]
  },
  "/api/check-in/entrants/2/suggestions": [
    {
      "id": 7,
      "name": "Alexis M",
      "username": "alexism"
    },
    {
      "id": 8,
      "name": "Lex",
      "username": "lexmoreau"
    }
  ],
  "/api/work/schedule": {
    "zone": "America/Denver",
    "days": [
      "2027-10-21",
      "2027-10-22",
      "2027-10-23",
      "2027-10-24"
    ],
    "day": "2027-10-23",
    "midnight": "{{now-13h}}",
    "first_hour": 9,
    "last_hour": 18,
    "columns": [
      {
        "position_id": 1,
        "position": "Admin Desk",
        "shifts": [
          {
            "starts_at": "{{now-3h}}",
            "ends_at": "{{now+1h}}",
            "label": "",
            "start_hour": 10,
            "end_hour": 14,
            "lane": 0,
            "lanes": 1,
            "slots_needed": 2,
            "open_slots": 0,
            "people": [
              {
                "name": "Morgan",
                "is_viewer": false,
                "called_off": false
              },
              {
                "name": "Kai",
                "is_viewer": false,
                "called_off": false
              }
            ]
          },
          {
            "starts_at": "{{now+1h}}",
            "ends_at": "{{now+5h}}",
            "label": "",
            "start_hour": 14,
            "end_hour": 18,
            "lane": 0,
            "lanes": 1,
            "slots_needed": 1,
            "open_slots": 0,
            "people": [
              {
                "name": "Dana",
                "is_viewer": false,
                "called_off": false
              }
            ]
          }
        ]
      },
      {
        "position_id": 2,
        "position": "Check-in",
        "shifts": [
          {
            "starts_at": "{{now-4h}}",
            "ends_at": "{{now-1h}}",
            "label": "",
            "start_hour": 9,
            "end_hour": 12,
            "lane": 0,
            "lanes": 1,
            "slots_needed": 2,
            "open_slots": 0,
            "people": [
              {
                "name": "Riley",
                "is_viewer": false,
                "called_off": false
              },
              {
                "name": "Priya",
                "is_viewer": false,
                "called_off": false
              }
            ]
          },
          {
            "starts_at": "{{now-1h}}",
            "ends_at": "{{now+2h}}",
            "label": "",
            "start_hour": 12,
            "end_hour": 15,
            "lane": 0,
            "lanes": 1,
            "slots_needed": 2,
            "open_slots": 0,
            "people": [
              {
                "name": "Sam",
                "is_viewer": true,
                "called_off": false
              },
              {
                "name": "Jordan",
                "is_viewer": false,
                "called_off": false
              }
            ]
          },
          {
            "starts_at": "{{now+2h}}",
            "ends_at": "{{now+5h}}",
            "label": "",
            "start_hour": 15,
            "end_hour": 18,
            "lane": 0,
            "lanes": 1,
            "slots_needed": 2,
            "open_slots": 1,
            "people": [
              {
                "name": "Lee",
                "is_viewer": false,
                "called_off": false
              }
            ]
          }
        ]
      },
      {
        "position_id": 3,
        "position": "Runner",
        "shifts": [
          {
            "starts_at": "{{now-2h}}",
            "ends_at": "{{now+3h}}",
            "label": "",
            "start_hour": 11,
            "end_hour": 16,
            "lane": 0,
            "lanes": 1,
            "slots_needed": 1,
            "open_slots": 0,
            "people": [
              {
                "name": "Chris",
                "is_viewer": false,
                "called_off": false
              }
            ]
          }
        ]
      }
    ],
    "roles": [
      {
        "position_id": 1,
        "name": "Admin Desk",
        "visible": true
      },
      {
        "position_id": 2,
        "name": "Check-in",
        "visible": true
      },
      {
        "position_id": 3,
        "name": "Runner",
        "visible": true
      },
      {
        "position_id": 4,
        "name": "Tech",
        "visible": false
      }
    ],
    "hidden_with_shifts": 0,
    "customized": true
  },
  "/api/volunteers/me/availability": [
    {
      "id": 1,
      "user_id": 1,
      "starts_at": "{{now+1h}}",
      "ends_at": "{{now+5h}}",
      "status": "available",
      "note": null
    },
    {
      "id": 2,
      "user_id": 1,
      "starts_at": "{{now+26h}}",
      "ends_at": "{{now+30h}}",
      "status": "preferred",
      "note": "Mornings are best"
    }
  ]
}

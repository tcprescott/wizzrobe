/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
/** Waits until every report chart has drawn and dropped its spinner. */
const chartsDrawn = (p) =>
  p.locator('.report-chart__canvas svg').first().waitFor().then(() => p.locator('.report-chart__wait').first().waitFor({ state: 'detached' }))

export const targets = [
  { name: 'admin-reports', path: '/t/sgl/admin/reports' },
  { name: 'admin-reports-capacity', path: '/t/sgl/admin/reports/capacity', act: chartsDrawn, wait: '.report-chart__canvas svg' },
  { name: 'admin-reports-insights', path: '/t/sgl/admin/reports/insights', act: chartsDrawn, wait: '.report-chart__canvas svg' },
  { name: 'admin-reports-match-ops', path: '/t/sgl/admin/reports/match_ops', act: chartsDrawn, wait: '.report-chart__canvas svg' },
  { name: 'admin-reports-crew', path: '/t/sgl/admin/reports/crew' },
  { name: 'admin-reports-stages', path: '/t/sgl/admin/reports/stages', act: chartsDrawn, wait: '.report-chart__canvas svg' },
  { name: 'admin-reports-volunteers', path: '/t/sgl/admin/reports/volunteers' },
  { name: 'admin-reports-audit', path: '/t/sgl/admin/reports/audit', act: (p) => p.getByText('Match rescheduled').click(), wait: '.report-changes' },
  { name: 'admin-reports-telemetry', path: '/t/sgl/admin/reports/telemetry' },
  { name: 'admin-reports-messages', path: '/t/sgl/admin/reports/messages', act: (p) => p.getByText('Rae').click(), wait: 'details[open] .report-message' },
  { name: 'admin-reports-exports', path: '/t/sgl/admin/reports/exports' },
  { name: 'admin-reports-dates', path: '/t/sgl/admin/reports/volunteers', act: (p) => p.getByRole('button', { name: 'Other dates' }).click(), wait: 'input[type=date]' },
]

export const fixtures = {
  "/api/admin/reports": {
    "overview": true,
    "reports": [
      {
        "key": "insights",
        "title": "Insights & Trends",
        "icon": "insights",
        "description": "Crew participation, volunteer hours, and tournament health trended over weeks or months."
      },
      {
        "key": "capacity",
        "title": "Capacity Forecast",
        "icon": "show_chart",
        "description": "Concurrent players across the event window vs configured capacity."
      },
      {
        "key": "match_ops",
        "title": "Match Operations",
        "icon": "timer",
        "description": "Start delays, durations, and on-time rate per tournament."
      },
      {
        "key": "crew",
        "title": "Staff / Crew Activity",
        "icon": "groups",
        "description": "Commentator and tracker coverage per match and per person."
      },
      {
        "key": "stages",
        "title": "Stage Utilization",
        "icon": "tv",
        "description": "Hours scheduled per stage, gaps, and unplaced candidates."
      },
      {
        "key": "volunteers",
        "title": "Volunteer Coverage",
        "icon": "volunteer_activism",
        "description": "Per-shift filled vs needed counts, highlighting understaffed shifts."
      },
      {
        "key": "audit",
        "title": "Audit Log",
        "icon": "fact_check",
        "description": "Filterable history of admin actions across the app."
      },
      {
        "key": "telemetry",
        "title": "Engagement Telemetry",
        "icon": "query_stats",
        "description": "How people use the tool: page views, interactions, and domain events."
      },
      {
        "key": "messages",
        "title": "Sent messages",
        "icon": "mail",
        "description": "Every DM sent in this community, and whether it got there."
      },
      {
        "key": "exports",
        "title": "Exports",
        "icon": "download",
        "description": "Download tournament participants and matches as CSVs or JSON, and anything you exported in the last 3 days."
      }
    ],
    "event_start": "2026-10-22",
    "event_end": "2026-10-25",
    "zone": "America/New_York",
    "jumps": [
      {
        "label": "Whole event",
        "start": "2026-10-22",
        "end": "2026-10-25"
      },
      {
        "label": "Thu 10-22",
        "start": "2026-10-22",
        "end": "2026-10-22"
      },
      {
        "label": "Fri 10-23",
        "start": "2026-10-23",
        "end": "2026-10-23"
      },
      {
        "label": "Sat 10-24",
        "start": "2026-10-24",
        "end": "2026-10-24"
      },
      {
        "label": "Sun 10-25",
        "start": "2026-10-25",
        "end": "2026-10-25"
      }
    ]
  },
  "/api/admin/reports/overview": [
    {
      "key": "peak_players",
      "label": "Peak players",
      "value": "212 / 240",
      "detail": "2026-10-24T18:00:00+00:00",
      "tone": "neutral",
      "report": "capacity"
    },
    {
      "key": "peak_stages",
      "label": "Peak stages used",
      "value": "5 / 4",
      "detail": "across the window",
      "tone": "bad",
      "report": "stages"
    },
    {
      "key": "matches",
      "label": "Matches",
      "value": "318",
      "detail": "6 in flight · 290 finished",
      "tone": "neutral",
      "report": "match_ops"
    },
    {
      "key": "stream_coverage",
      "label": "Stream candidate coverage",
      "value": "82%",
      "detail": "41/50 fully covered",
      "tone": "pending",
      "report": "crew"
    }
  ],
  "/api/admin/reports/capacity": {
    "interval_minutes": 120,
    "max_capacity": 240,
    "times": [
      "2026-10-22T00:00:00Z",
      "2026-10-22T02:00:00Z",
      "2026-10-22T04:00:00Z",
      "2026-10-22T06:00:00Z",
      "2026-10-22T08:00:00Z",
      "2026-10-22T10:00:00Z",
      "2026-10-22T12:00:00Z",
      "2026-10-22T14:00:00Z",
      "2026-10-22T16:00:00Z",
      "2026-10-22T18:00:00Z",
      "2026-10-22T20:00:00Z",
      "2026-10-22T22:00:00Z",
      "2026-10-23T00:00:00Z",
      "2026-10-23T02:00:00Z",
      "2026-10-23T04:00:00Z",
      "2026-10-23T06:00:00Z",
      "2026-10-23T08:00:00Z",
      "2026-10-23T10:00:00Z",
      "2026-10-23T12:00:00Z",
      "2026-10-23T14:00:00Z",
      "2026-10-23T16:00:00Z",
      "2026-10-23T18:00:00Z",
      "2026-10-23T20:00:00Z",
      "2026-10-23T22:00:00Z",
      "2026-10-24T00:00:00Z",
      "2026-10-24T02:00:00Z",
      "2026-10-24T04:00:00Z",
      "2026-10-24T06:00:00Z",
      "2026-10-24T08:00:00Z",
      "2026-10-24T10:00:00Z",
      "2026-10-24T12:00:00Z",
      "2026-10-24T14:00:00Z",
      "2026-10-24T16:00:00Z",
      "2026-10-24T18:00:00Z",
      "2026-10-24T20:00:00Z",
      "2026-10-24T22:00:00Z",
      "2026-10-25T00:00:00Z",
      "2026-10-25T02:00:00Z",
      "2026-10-25T04:00:00Z",
      "2026-10-25T06:00:00Z",
      "2026-10-25T08:00:00Z",
      "2026-10-25T10:00:00Z",
      "2026-10-25T12:00:00Z",
      "2026-10-25T14:00:00Z",
      "2026-10-25T16:00:00Z",
      "2026-10-25T18:00:00Z",
      "2026-10-25T20:00:00Z",
      "2026-10-25T22:00:00Z"
    ],
    "players": [
      200,
      199,
      185,
      159,
      102,
      60,
      15,
      51,
      105,
      153,
      191,
      196,
      209,
      208,
      173,
      147,
      111,
      69,
      3,
      60,
      114,
      141,
      179,
      205,
      218,
      196,
      182,
      156,
      99,
      57,
      12,
      69,
      102,
      150,
      188,
      193,
      206,
      205,
      191,
      144,
      108,
      66,
      0,
      57,
      111,
      159,
      176,
      202
    ],
    "on_stream": [
      50,
      49,
      46,
      39,
      25,
      15,
      3,
      12,
      26,
      38,
      47,
      49,
      52,
      52,
      43,
      36,
      27,
      17,
      0,
      15,
      28,
      35,
      44,
      51,
      54,
      49,
      45,
      39,
      24,
      14,
      3,
      17,
      25,
      37,
      47,
      48,
      51,
      51,
      47,
      36,
      27,
      16,
      0,
      14,
      27,
      39,
      44,
      50
    ],
    "off_stream": [
      150,
      150,
      139,
      120,
      77,
      45,
      12,
      39,
      79,
      115,
      144,
      147,
      157,
      156,
      130,
      111,
      84,
      52,
      3,
      45,
      86,
      106,
      135,
      154,
      164,
      147,
      137,
      117,
      75,
      43,
      9,
      52,
      77,
      113,
      141,
      145,
      155,
      154,
      144,
      108,
      81,
      50,
      0,
      43,
      84,
      120,
      132,
      152
    ]
  },
  "/api/reports/match-operations": {
    "aggregates": [
      {
        "tournament_id": 1,
        "tournament_name": "ALTTPR Main",
        "matches_total": 120,
        "matches_started": 113,
        "matches_finished": 112,
        "on_time_pct": 78.5,
        "avg_start_delay_min": 6.2,
        "avg_duration_min": 84.0,
        "avg_setup_min": 6.5,
        "avg_seat_delay_min": 3.0,
        "expected_avg_min": 90,
        "seated_on_time_pct": 80.0,
        "staged_avg_seat_delay_min": null,
        "staged_avg_setup_min": null,
        "staged_seated_on_time_pct": null,
        "unstaged_avg_seat_delay_min": null,
        "unstaged_avg_setup_min": null,
        "unstaged_seated_on_time_pct": null
      },
      {
        "tournament_id": 2,
        "tournament_name": "Super Metroid Rando",
        "matches_total": 64,
        "matches_started": 61,
        "matches_finished": 60,
        "on_time_pct": 91.0,
        "avg_start_delay_min": 2.1,
        "avg_duration_min": 71.5,
        "avg_setup_min": 6.5,
        "avg_seat_delay_min": 3.0,
        "expected_avg_min": 90,
        "seated_on_time_pct": 80.0,
        "staged_avg_seat_delay_min": null,
        "staged_avg_setup_min": null,
        "staged_seated_on_time_pct": null,
        "unstaged_avg_seat_delay_min": null,
        "unstaged_avg_setup_min": null,
        "unstaged_seated_on_time_pct": null
      },
      {
        "tournament_id": 3,
        "tournament_name": "OoTR",
        "matches_total": 40,
        "matches_started": 31,
        "matches_finished": 30,
        "on_time_pct": 55.0,
        "avg_start_delay_min": 14.8,
        "avg_duration_min": 140.2,
        "avg_setup_min": 6.5,
        "avg_seat_delay_min": 3.0,
        "expected_avg_min": 90,
        "seated_on_time_pct": 80.0,
        "staged_avg_seat_delay_min": null,
        "staged_avg_setup_min": null,
        "staged_seated_on_time_pct": null,
        "unstaged_avg_seat_delay_min": null,
        "unstaged_avg_setup_min": null,
        "unstaged_seated_on_time_pct": null
      }
    ],
    "rows": [
      {
        "match_id": 301,
        "tournament_id": 1,
        "tournament_name": "ALTTPR Main",
        "scheduled_at": "2026-10-24T18:00:00Z",
        "stage": "Main Stage",
        "staged": true,
        "state": "finished",
        "player_count": 2,
        "start_delay_min": 400,
        "duration_min": 90,
        "setup_min": 5,
        "seat_delay_min": 2,
        "confirmation_lag_min": null
      },
      {
        "match_id": 302,
        "tournament_id": 1,
        "tournament_name": "OoTR",
        "scheduled_at": "2026-10-24T20:00:00Z",
        "stage": "Main Stage",
        "staged": true,
        "state": "finished",
        "player_count": 2,
        "start_delay_min": 5,
        "duration_min": 410,
        "setup_min": 5,
        "seat_delay_min": 2,
        "confirmation_lag_min": null
      }
    ],
    "flagged_match_ids": [
      301
    ],
    "duration_outlier_ids": [
      302
    ]
  },
  "/api/reports/crew-coverage": {
    "coverage_rows": [
      {
        "match_id": 301,
        "tournament_name": "ALTTPR Main",
        "scheduled_at": "2026-10-24T19:00:00Z",
        "stage": "Main Stage",
        "is_stream_candidate": true,
        "coverage_gap": true,
        "commentators_approved": 1,
        "commentators_required": 2,
        "commentators_total": 3,
        "trackers_approved": 0,
        "trackers_required": 1,
        "trackers_total": 1,
        "broadcasters_approved": 1,
        "broadcasters_required": 1,
        "broadcasters_total": 1
      },
      {
        "match_id": 303,
        "tournament_name": "ALTTPR Main",
        "scheduled_at": "2026-10-24T19:00:00Z",
        "stage": "Main Stage",
        "is_stream_candidate": true,
        "coverage_gap": false,
        "commentators_approved": 1,
        "commentators_required": 2,
        "commentators_total": 3,
        "trackers_approved": 1,
        "trackers_required": 1,
        "trackers_total": 1,
        "broadcasters_approved": 1,
        "broadcasters_required": 1,
        "broadcasters_total": 1
      }
    ],
    "contribution_rows": [
      {
        "user_id": 5,
        "name": "Kara",
        "commentator_approved": 4,
        "commentator_total": 5,
        "tracker_approved": 1,
        "tracker_total": 1,
        "broadcaster_approved": 0,
        "broadcaster_total": 0,
        "hours_covered": 7.5,
        "hours_total": 9.0
      },
      {
        "user_id": 6,
        "name": "Theo",
        "commentator_approved": 2,
        "commentator_total": 2,
        "tracker_approved": 3,
        "tracker_total": 4,
        "broadcaster_approved": 1,
        "broadcaster_total": 1,
        "hours_covered": 5.0,
        "hours_total": 6.0
      }
    ],
    "tournament_rows": []
  },
  "/api/reports/stage-utilization": {
    "stages": [
      {
        "stage_id": 1,
        "stage_name": "Main Stage",
        "match_count": 14,
        "scheduled_hours": 20.5,
        "gap_hours": 3.0,
        "back_to_back_count": 6
      },
      {
        "stage_id": 2,
        "stage_name": "Side Stage",
        "match_count": 9,
        "scheduled_hours": 12.0,
        "gap_hours": 8.5,
        "back_to_back_count": 2
      }
    ],
    "unplaced_candidate_count": 3
  },
  "/api/admin/reports/stage-bookings": {
    "stages": {
      "1": [
        {
          "match_id": 301,
          "tournament_name": "ALTTPR Main",
          "start": "2026-10-23T15:00:00Z",
          "end": "2026-10-23T17:00:00Z"
        },
        {
          "match_id": 304,
          "tournament_name": "ALTTPR Main",
          "start": "2026-10-24T18:00:00Z",
          "end": "2026-10-24T20:00:00Z"
        }
      ],
      "2": [
        {
          "match_id": 305,
          "tournament_name": "ALTTPR Main",
          "start": "2026-10-24T14:00:00Z",
          "end": "2026-10-24T16:30:00Z"
        }
      ]
    }
  },
  "/api/admin/reports/volunteer-coverage": [
    {
      "shift_id": 1,
      "position": "Check-in desk",
      "label": "Morning",
      "starts_at": "2026-10-24T13:00:00Z",
      "ends_at": "2026-10-24T17:00:00Z",
      "filled": 2,
      "needed": 4,
      "understaffed": true,
      "drafts": 0,
      "acknowledged": 2
    },
    {
      "shift_id": 2,
      "position": "Proctor",
      "label": "",
      "starts_at": "2026-10-24T17:00:00Z",
      "ends_at": "2026-10-24T21:00:00Z",
      "filled": 3,
      "needed": 3,
      "understaffed": false,
      "drafts": 0,
      "acknowledged": 3
    }
  ],
  "/api/admin/reports/audit": {
    "total": 2,
    "limit": 50,
    "offset": 0,
    "areas": [
      {
        "key": "matches",
        "label": "Matches"
      },
      {
        "key": "people",
        "label": "People"
      }
    ],
    "items": [
      {
        "id": 901,
        "created_at": "2026-10-24T18:05:00Z",
        "actor_id": 5,
        "actor_name": "Kara",
        "action": "match.rescheduled",
        "label": "Match rescheduled",
        "area": "Matches",
        "subject": "Match #301",
        "summary": "Moved to Sat 3:00 PM",
        "changes": [
          {
            "field": "Start",
            "before": "Sat 2:00 PM",
            "after": "Sat 3:00 PM"
          }
        ],
        "changed_fields": [],
        "facts": [
          {
            "label": "Reason",
            "value": "Player running late"
          }
        ],
        "raw": "{\n  \"match_id\": 301\n}",
        "match_id": 301
      },
      {
        "id": 900,
        "created_at": "2026-10-24T17:40:00Z",
        "actor_id": 6,
        "actor_name": "Theo",
        "action": "role.granted",
        "label": "Role granted",
        "area": "People",
        "subject": "Sam",
        "summary": "Proctor",
        "changes": [],
        "changed_fields": [
          "roles"
        ],
        "facts": [],
        "raw": "",
        "match_id": null
      }
    ]
  },
  "/api/telemetry/summary": {
    "unique_users": 412,
    "unique_sessions": 1380,
    "page_views": 22410,
    "total_events": 61200,
    "rest_users": 4,
    "mcp_users": 2,
    "graphql_users": 0
  },
  "/api/telemetry/top": [
    {
      "path": "/t/sgl/home",
      "views": 8210,
      "users": 380
    },
    {
      "path": "/t/sgl/schedule",
      "views": 4102,
      "users": 290
    }
  ],
  "/api/sent-messages": {
    "total": 2,
    "limit": 50,
    "offset": 0,
    "counts": {
      "sent": 1240,
      "silent": 310,
      "off_or_blocked": 18,
      "failed": 2
    },
    "items": [
      {
        "id": 1,
        "recipient_id": 7,
        "recipient_name": "Sam",
        "discord_id": 1,
        "title": "Your match starts in 30 minutes",
        "body": "ALTTPR Main vs Kara at 3:00 PM on Main Stage.",
        "link_label": "Open match",
        "link_url": "https://onsite.example/t/sgl/home",
        "silent": false,
        "notice_key": "match_reminder",
        "audience": "players",
        "status": "delivered",
        "error": null,
        "push_devices": 2,
        "email_sent": null,
        "telegram_sent": null,
        "created_at": "2026-10-24T18:30:00Z"
      },
      {
        "id": 2,
        "recipient_id": 8,
        "recipient_name": "Rae",
        "discord_id": 2,
        "title": null,
        "body": "Your shift starts soon.",
        "link_label": null,
        "link_url": null,
        "silent": true,
        "notice_key": null,
        "audience": null,
        "status": "blocked",
        "error": "Cannot send messages to this user",
        "push_devices": 0,
        "email_sent": false,
        "telegram_sent": null,
        "created_at": "2026-10-24T17:00:00Z"
      }
    ]
  },
  "/api/admin/reports/exportable-tournaments": [
    {
      "id": 1,
      "name": "ALTTPR Main",
      "active": true,
      "test": false,
      "players": 64,
      "matches": 120,
      "finished": 112,
      "bracket_site": "Challonge"
    },
    {
      "id": 9,
      "name": "Challonge Integration Test",
      "active": true,
      "test": true,
      "players": 2,
      "matches": 1,
      "finished": 0,
      "bracket_site": null
    }
  ],
  "/api/exports": {
    "items": [
      {
        "id": 11,
        "kind": "tournaments",
        "title": "Tournaments (1)",
        "status": "ready",
        "created_at": "2026-10-24T18:00:00Z",
        "finished_at": "2026-10-24T18:00:20Z",
        "expires_at": "2026-10-27T18:00:00Z",
        "filename": "tournaments.zip",
        "has_json": true,
        "size_bytes": 48211,
        "progress_done": 1,
        "progress_total": 1,
        "error": null
      }
    ]
  },
  "/api/admin/reports/activity-extent": {
    "first": "2026-09-28",
    "last": "2026-10-25"
  },
  "/api/reports/activity-trends": {
    "bucket": "week",
    "bucket_labels": [
      "Sep 28",
      "Oct 5",
      "Oct 12",
      "Oct 19"
    ],
    "bucket_starts": [
      "2026-09-28",
      "2026-10-05",
      "2026-10-12",
      "2026-10-19"
    ],
    "total": 9100,
    "categories": [
      {
        "category": "page",
        "counts": [
          400,
          900,
          2100,
          4200
        ],
        "total": 7600
      },
      {
        "category": "interaction",
        "counts": [
          100,
          200,
          400,
          800
        ],
        "total": 1500
      }
    ]
  },
  "/api/reports/crew-participation-trends": {
    "bucket": "week",
    "bucket_labels": [
      "Sep 28",
      "Oct 5",
      "Oct 12",
      "Oct 19"
    ],
    "bucket_starts": [
      "2026-09-28",
      "2026-10-05",
      "2026-10-12",
      "2026-10-19"
    ],
    "commentator_signups": [
      2,
      5,
      12,
      30
    ],
    "commentator_approved": [
      1,
      4,
      10,
      25
    ],
    "tracker_signups": [
      0,
      2,
      6,
      14
    ],
    "tracker_approved": [
      0,
      1,
      5,
      12
    ],
    "unique_people": [
      2,
      6,
      14,
      31
    ],
    "top_contributors": [],
    "totals": {
      "commentator_signups": 49,
      "commentator_approved": 40,
      "tracker_signups": 22,
      "tracker_approved": 18,
      "unique_people": 38
    }
  },
  "/api/reports/volunteer-hour-trends": {
    "bucket": "week",
    "bucket_labels": [
      "Sep 28",
      "Oct 5",
      "Oct 12",
      "Oct 19"
    ],
    "bucket_starts": [
      "2026-09-28",
      "2026-10-05",
      "2026-10-12",
      "2026-10-19"
    ],
    "needed_hours": [
      0,
      0,
      10,
      220
    ],
    "scheduled_hours": [
      0,
      0,
      8,
      200
    ],
    "checked_in_hours": [
      0,
      0,
      8,
      180
    ],
    "fill_rate": [
      null,
      null,
      80,
      91
    ],
    "positions": [],
    "top_volunteers": [],
    "totals": {
      "needed_hours": 230,
      "scheduled_hours": 208,
      "checked_in_hours": 188,
      "volunteers": 45
    }
  },
  "/api/reports/tournament-health": [
    {
      "tournament_id": 1,
      "tournament_name": "ALTTPR Main",
      "health_score": 82.0,
      "completion_pct": 93.0,
      "on_time_pct": 78.5,
      "coverage_pct": 82.0,
      "avg_start_delay_min": 6.2,
      "avg_duration_min": 84.0,
      "expected_avg_min": 90,
      "matches_total": 120,
      "matches_past": 118,
      "matches_started": 113,
      "matches_finished": 112
    }
  ]
}

/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-qualifiers', path: '/t/sgl/admin/qualifiers/7' },
  { name: 'admin-qualifiers-reject', path: '/t/sgl/admin/qualifiers/7', act: (p) => p.getByRole('button', { name: 'Reject' }).first().click(), wait: 'role=dialog' },
  { name: 'admin-qualifiers-pools', path: '/t/sgl/admin/qualifiers/7/pools', wait: 'text=Rolls from sgl27-open' },
  { name: 'admin-qualifiers-reviewers', path: '/t/sgl/admin/qualifiers/7/reviewers', wait: 'text=Find a member' },
  { name: 'admin-qualifiers-settings', path: '/t/sgl/admin/qualifiers/7/settings', wait: 'text=Live races with a start time' },
]

export const fixtures = {
  "/api/async-qualifiers/7": {
    "id": 7,
    "name": "SGL27 ALTTPR Qualifier",
    "description": null,
    "event_name": null,
    "opens_at": "{{now-2d}}",
    "closes_at": "{{now+5d}}",
    "runs_per_pool": 1,
    "allowed_reattempts": 0,
    "config": null,
    "is_active": true,
    "live_race_bot_id": null,
    "live_race_room_open_minutes": 60,
    "live_race_seed_post_minutes": 10,
    "live_race_lock_at_seed": true,
    "live_race_announce_channel_id": "222",
    "live_race_announce_role_id": null,
    "created_at": "{{now-30d}}",
    "updated_at": "{{now-1d}}"
  },
  "/api/admin/qualifiers/board": {
    "qualifiers": [
      {
        "id": 7,
        "name": "SGL27 ALTTPR Qualifier",
        "state": "open",
        "state_label": "Open",
        "opens_at": "2027-01-10T17:00:00Z",
        "closes_at": "2027-03-01T05:00:00Z",
        "to_review": 3,
        "runs": 148
      },
      {
        "id": 8,
        "name": "SGL27 SM Qualifier",
        "state": "open",
        "state_label": "Open",
        "opens_at": "2027-01-10T17:00:00Z",
        "closes_at": "2027-03-01T05:00:00Z",
        "to_review": 0,
        "runs": 61
      },
      {
        "id": 9,
        "name": "SGL26 OoTR Qualifier",
        "state": "closed",
        "state_label": "Closed",
        "opens_at": "2027-01-10T17:00:00Z",
        "closes_at": "2027-03-01T05:00:00Z",
        "to_review": 0,
        "runs": 212
      }
    ],
    "can_create": true,
    "zone": "America/New_York"
  },
  "/api/admin/qualifiers/7": {
    "row": {
      "id": 7,
      "name": "SGL27 ALTTPR Qualifier",
      "state": "open",
      "state_label": "Open",
      "opens_at": "2027-01-10T17:00:00Z",
      "closes_at": "2027-03-01T05:00:00Z",
      "to_review": 3,
      "runs": 148
    },
    "settings": {
      "description": "Three pools, two runs each.",
      "event_name": null,
      "runs_per_pool": 2,
      "allowed_reattempts": 1,
      "is_active": true
    },
    "queue": [
      {
        "run_id": 1,
        "runner": "Morgan Wu",
        "runner_id": 101,
        "pool": "Open",
        "claimed_seconds": 6138,
        "measured_seconds": 6378,
        "implausible": false,
        "started_at": "2027-02-01T18:00:00Z",
        "finished_at": "2027-02-01T19:45:00Z",
        "permalink_url": "https://alttpr.com/h/abc",
        "vod_url": "https://twitch.tv/videos/1",
        "other_runs": "1 other run here (1 approved)",
        "notes": [],
        "held_by": null,
        "held_by_me": false
      },
      {
        "run_id": 2,
        "runner": "Dana Ortiz",
        "runner_id": 102,
        "pool": "Standard",
        "claimed_seconds": 5942,
        "measured_seconds": 6182,
        "implausible": false,
        "started_at": "2027-02-01T18:00:00Z",
        "finished_at": "2027-02-01T19:45:00Z",
        "permalink_url": "https://alttpr.com/h/abc",
        "vod_url": "https://twitch.tv/videos/1",
        "other_runs": "",
        "notes": [],
        "held_by": "Kai",
        "held_by_me": false
      },
      {
        "run_id": 3,
        "runner": "Jo Park",
        "runner_id": 103,
        "pool": "Keysanity",
        "claimed_seconds": 6400,
        "measured_seconds": 6640,
        "implausible": true,
        "started_at": "2027-02-01T18:00:00Z",
        "finished_at": "2027-02-01T19:45:00Z",
        "permalink_url": "https://alttpr.com/h/abc",
        "vod_url": "https://twitch.tv/videos/1",
        "other_runs": "",
        "notes": [
          "crashed at Mire"
        ],
        "held_by": null,
        "held_by_me": false
      }
    ],
    "leaderboard": [
      {
        "rank": 1,
        "name": "Dana Ortiz",
        "score": 108.2,
        "slots": "6/6"
      },
      {
        "rank": 2,
        "name": "Kai",
        "score": 104.9,
        "slots": "6/6"
      },
      {
        "rank": 3,
        "name": "Morgan Wu",
        "score": 101.3,
        "slots": "6/6"
      },
      {
        "rank": 4,
        "name": "Riley",
        "score": 99.0,
        "slots": "6/6"
      },
      {
        "rank": 5,
        "name": "Sam",
        "score": 97.4,
        "slots": "6/6"
      }
    ],
    "leaderboard_total": 41,
    "setup": {
      "pools": 3,
      "seeds": 24,
      "live_races": 2,
      "live_races_open": 1,
      "reviewers": 2
    }
  },
  "/api/admin/qualifiers/7/pools": [
    {
      "id": 71,
      "name": "Open",
      "preset_id": 3,
      "preset_name": "sgl27-open",
      "roll_refusal": null,
      "permalinks": [
        {
          "id": 711,
          "url": "https://alttpr.com/h/abc123",
          "notes": null,
          "live_race": false,
          "par_seconds": 6020
        },
        {
          "id": 712,
          "url": "https://alttpr.com/h/def456",
          "notes": "Swamp first",
          "live_race": true,
          "par_seconds": null
        }
      ]
    }
  ],
  "/api/admin/qualifiers/7/reviewers": {
    "reviewers": [
      {
        "id": 21,
        "name": "Kai",
        "username": "kai"
      },
      {
        "id": 22,
        "name": "Riley",
        "username": "riley"
      }
    ],
    "members": [
      {
        "id": 21,
        "name": "Kai",
        "username": "kai"
      },
      {
        "id": 22,
        "name": "Riley",
        "username": "riley"
      },
      {
        "id": 23,
        "name": "Sam",
        "username": "sam"
      }
    ]
  }
}

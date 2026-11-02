/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-tournaments', path: '/t/sgl/admin/tournaments/1/settings' },
  { name: 'admin-tournament-players', path: '/t/sgl/admin/tournaments/1/players' },
  { name: 'admin-tournament-bracket', path: '/t/sgl/admin/tournaments/1/bracket' },
  { name: 'admin-tournament-schedule', path: '/t/sgl/admin/tournaments/1/schedule' },
  { name: 'admin-tournament-link', path: '/t/sgl/admin/tournaments/2/bracket' },
  { name: 'admin-tournament-add', path: '/t/sgl/admin/tournaments', act: (p) => p.getByRole('button', { name: 'Add tournament' }).click(), wait: 'role=dialog' },
  { name: 'admin-tournament-drop', path: '/t/sgl/admin/tournaments/1/players', act: (p) => p.getByRole('button', { name: 'More for Ari' }).click(), wait: 'role=menu' },
]

export const fixtures = {
  "/api/admin/tournaments/board": {
    "can_create": true,
    "zone": "America/New_York",
    "linkable_sites": [
      {
        "key": "challonge",
        "label": "Challonge"
      },
      {
        "key": "startgg",
        "label": "start.gg"
      }
    ],
    "rows": [
      {
        "average_match_duration": 45,
        "max_match_duration": 60,
        "bracket": {
          "site": "challonge",
          "label": "Challonge",
          "stage": "Group stage",
          "unmatched": 1,
          "url": "https://challonge.com/sgl26alttpr",
          "synced_at": "{{now-30m}}",
          "stage_action": {
            "key": "finalize_groups",
            "label": "Finish group stage",
            "confirm": "Challonge moves the top players into the final bracket."
          }
        },
        "bracket_url": null,
        "can_delete": false,
        "can_link": true,
        "can_manage_bracket": true,
        "has_round_schedule": true,
        "is_active": true,
        "is_test": false,
        "player_count": 3,
        "players_per_match": 2,
        "rules_url": "https://example.com/rules",
        "seed_generator": "alttpr",
        "signup_state": "open",
        "signups_close_at": null,
        "signups_open_at": null,
        "tournament_format": "Double elimination",
        "id": 1,
        "name": "ALTTPR Main"
      },
      {
        "average_match_duration": 45,
        "max_match_duration": 60,
        "bracket": null,
        "bracket_url": null,
        "can_delete": false,
        "can_link": true,
        "can_manage_bracket": true,
        "has_round_schedule": false,
        "is_active": true,
        "is_test": false,
        "player_count": 0,
        "players_per_match": 2,
        "rules_url": null,
        "seed_generator": "sm",
        "signup_state": "upcoming",
        "signups_close_at": null,
        "signups_open_at": null,
        "tournament_format": "Double elimination",
        "id": 2,
        "name": "Super Metroid"
      },
      {
        "average_match_duration": 45,
        "max_match_duration": 60,
        "bracket": null,
        "bracket_url": null,
        "can_delete": true,
        "can_link": true,
        "can_manage_bracket": true,
        "has_round_schedule": false,
        "is_active": false,
        "is_test": true,
        "player_count": 0,
        "players_per_match": 2,
        "rules_url": null,
        "seed_generator": "alttpr",
        "signup_state": "closed",
        "signups_close_at": null,
        "signups_open_at": null,
        "tournament_format": "Double elimination",
        "id": 3,
        "name": "Old Showcase"
      }
    ]
  },
  "/api/tournaments/1": {
    "id": 1,
    "name": "ALTTPR Main",
    "short_name": "ALTTPR",
    "tournament_format": "Double elimination",
    "description": "Open bracket.",
    "seed_generator": "alttpr",
    "preset_id": null,
    "is_active": true,
    "is_test": false,
    "players_per_match": 2,
    "average_match_duration": 45,
    "max_match_duration": 60,
    "required_commentators": 2,
    "required_trackers": 1,
    "required_broadcasters": 1,
    "allow_player_match_requests": true,
    "signups_open_at": "{{now-2d}}",
    "signups_close_at": "{{now+5d}}",
    "rules_url": "https://example.com/rules",
    "bracket_url": null,
    "created_at": "{{now-30d}}",
    "updated_at": "{{now-1d}}"
  },
  "/api/seeds/randomizers": [
    {
      "randomizer": "alttpr",
      "supports_triforce_texts": true
    },
    {
      "randomizer": "sm",
      "supports_triforce_texts": false
    }
  ],
  "/api/presets/selectable": [
    {
      "id": 5,
      "name": "sgl2026",
      "randomizer": "alttpr",
      "settings": {},
      "created_at": "{{now-30d}}",
      "updated_at": "{{now-30d}}"
    }
  ],
  "/api/admin/tournaments/1/players": [
    {
      "user": {
        "id": 11,
        "name": "Ari"
      },
      "enrolled_at": "{{now-3d}}"
    },
    {
      "user": {
        "id": 12,
        "name": "Bex"
      },
      "enrolled_at": "{{now-2d}}"
    },
    {
      "user": {
        "id": 13,
        "name": "Cato"
      },
      "enrolled_at": "{{now-1d}}"
    }
  ],
  "/api/admin/tournaments/1/people": [
    {
      "id": 11,
      "name": "Ari",
      "username": "ari"
    },
    {
      "id": 14,
      "name": "Dee",
      "username": "deedee"
    }
  ],
  "/api/admin/tournaments/1/bracket/entrants": {
    "site": "challonge",
    "label": "Challonge",
    "entrants": [
      {
        "id": 101,
        "name": "ari_runs",
        "status": "linked",
        "user": {
          "id": 11,
          "name": "Ari"
        }
      },
      {
        "id": 102,
        "name": "DeeSpeed",
        "status": "suggested",
        "suggested_user": {
          "id": 14,
          "name": "Dee"
        }
      },
      {
        "id": 103,
        "name": "mystery",
        "status": "unmatched"
      }
    ]
  },
  "/api/tournaments/1/round-schedule": [
    {
      "key": "group",
      "label": "Group stage",
      "best_of": 1,
      "booked": 4,
      "total": 6,
      "detail": "4 of 6 booked",
      "placeholder": false,
      "opens_at": "{{now-2d}}",
      "closes_at": "{{now+3d}}",
      "daily_from": "10:00",
      "daily_until": "22:00"
    },
    {
      "key": "e0",
      "label": "Final",
      "best_of": 3,
      "booked": 0,
      "total": 0,
      "detail": "Not drawn yet",
      "placeholder": true
    }
  ],
  "/api/tournaments": [
    {
      "id": 1,
      "name": "ALTTPR Main",
      "is_active": true,
      "players_per_match": 2,
      "required_commentators": 2,
      "required_trackers": 1,
      "allow_player_match_requests": true,
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    },
    {
      "id": 2,
      "name": "Super Metroid",
      "is_active": true,
      "players_per_match": 2,
      "required_commentators": 2,
      "required_trackers": 1,
      "allow_player_match_requests": true,
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    }
  ]
}

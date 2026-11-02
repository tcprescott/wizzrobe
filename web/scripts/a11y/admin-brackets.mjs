/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-brackets-live', path: '/t/sgl/admin/brackets/5', wait: 'text=Open matches · 2' },
  { name: 'admin-brackets-draft', path: '/t/sgl/admin/brackets/6', wait: 'text=Roster · 6' },
  { name: 'admin-brackets-report', path: '/t/sgl/admin/brackets/5', act: (p) => p.getByRole('button', { name: 'Report a result' }).click(), wait: 'role=dialog' },
  { name: 'admin-brackets-seed-tools', path: '/t/sgl/admin/brackets/6', act: (p) => p.getByRole('button', { name: 'Seed tools' }).click(), wait: 'role=menu' },
]

export const fixtures = {
  "/api/admin/brackets/board": {
    "tournaments": [
      {
        "id": 1,
        "name": "ALTTPR Main",
        "is_active": true,
        "stages": [
          {
            "id": 5,
            "tournament_id": 1,
            "name": "Swiss",
            "stage_order": 0,
            "stage_label": "Stage 1",
            "format": "swiss",
            "format_label": "Swiss",
            "state": "active",
            "state_label": "In progress",
            "config": [
              "5 rounds",
              "Best of 1"
            ],
            "next_step": {
              "key": "report",
              "title": "Report results",
              "detail": "9 of 16 matches reported · 4 open",
              "done": 9,
              "total": 16
            },
            "can_complete": false,
            "can_advance": false
          },
          {
            "id": 6,
            "tournament_id": 1,
            "name": "Top 8",
            "stage_order": 1,
            "stage_label": "Stage 2",
            "format": "single_elim",
            "format_label": "Single elimination",
            "state": "draft",
            "state_label": "Draft",
            "config": [
              "Top 8 advance"
            ],
            "next_step": {
              "key": "start",
              "title": "Enroll and seed, then start",
              "detail": "4 enrolled · 2 on the roster aren't in yet",
              "done": 4,
              "total": 6
            },
            "can_complete": false,
            "can_advance": false
          }
        ]
      }
    ],
    "formats": [
      {
        "value": "single_elim",
        "label": "Single elimination"
      },
      {
        "value": "double_elim",
        "label": "Double elimination"
      },
      {
        "value": "swiss",
        "label": "Swiss"
      },
      {
        "value": "round_robin",
        "label": "Round robin"
      }
    ]
  },
  "/api/admin/brackets/5": {
    "row": {
      "id": 5,
      "tournament_id": 1,
      "name": "Swiss",
      "stage_order": 0,
      "stage_label": "Stage 1",
      "format": "swiss",
      "format_label": "Swiss",
      "state": "active",
      "state_label": "In progress",
      "config": [
        "5 rounds",
        "Best of 1"
      ],
      "next_step": {
        "key": "report",
        "title": "Report results",
        "detail": "9 of 16 matches reported · 4 open",
        "done": 9,
        "total": 16
      },
      "can_complete": false,
      "can_advance": false
    },
    "tournament_name": "ALTTPR Main",
    "entries": [],
    "roster": [],
    "matches": [
      {
        "id": 51,
        "label": "Round 3",
        "round": 3,
        "state": "open",
        "best_of": 1,
        "side1": {
          "entry_id": 1,
          "name": "Ari",
          "score": null
        },
        "side2": {
          "entry_id": 2,
          "name": "Bex",
          "score": null
        },
        "winner_entry_id": null,
        "forfeit": false
      },
      {
        "id": 52,
        "label": "Round 3",
        "round": 3,
        "state": "open",
        "best_of": 1,
        "side1": {
          "entry_id": 3,
          "name": "Cy",
          "score": null
        },
        "side2": {
          "entry_id": 4,
          "name": "Dee",
          "score": null
        },
        "winner_entry_id": null,
        "forfeit": false
      },
      {
        "id": 41,
        "label": "Round 2",
        "round": 2,
        "state": "complete",
        "best_of": 1,
        "side1": {
          "entry_id": 1,
          "name": "Ari",
          "score": 1
        },
        "side2": {
          "entry_id": 3,
          "name": "Cy",
          "score": 0
        },
        "winner_entry_id": 1,
        "forfeit": false
      }
    ]
  },
  "/api/admin/brackets/6": {
    "row": {
      "id": 6,
      "tournament_id": 1,
      "name": "Top 8",
      "stage_order": 1,
      "stage_label": "Stage 2",
      "format": "single_elim",
      "format_label": "Single elimination",
      "state": "draft",
      "state_label": "Draft",
      "config": [
        "Top 8 advance"
      ],
      "next_step": {
        "key": "start",
        "title": "Enroll and seed, then start",
        "detail": "4 enrolled · 2 on the roster aren't in yet",
        "done": 4,
        "total": 6
      },
      "can_complete": false,
      "can_advance": false
    },
    "tournament_name": "ALTTPR Main",
    "entries": [
      {
        "id": 61,
        "entrant_id": 61,
        "name": "Ari",
        "seed": 1,
        "group_number": null,
        "status": "active",
        "final_rank": null
      },
      {
        "id": 62,
        "entrant_id": 62,
        "name": "Bex",
        "seed": 2,
        "group_number": null,
        "status": "active",
        "final_rank": null
      },
      {
        "id": 63,
        "entrant_id": 63,
        "name": "Cy",
        "seed": 3,
        "group_number": null,
        "status": "active",
        "final_rank": null
      },
      {
        "id": 64,
        "entrant_id": 64,
        "name": "Dee",
        "seed": null,
        "group_number": null,
        "status": "active",
        "final_rank": null
      }
    ],
    "roster": [
      {
        "id": 61,
        "name": "Ari",
        "user_id": 10,
        "status": "active",
        "enrolled": true
      },
      {
        "id": 62,
        "name": "Bex",
        "user_id": 11,
        "status": "active",
        "enrolled": true
      },
      {
        "id": 63,
        "name": "Cy",
        "user_id": 12,
        "status": "active",
        "enrolled": true
      },
      {
        "id": 64,
        "name": "Dee",
        "user_id": 13,
        "status": "active",
        "enrolled": true
      },
      {
        "id": 65,
        "name": "Eli",
        "user_id": 14,
        "status": "active",
        "enrolled": false
      },
      {
        "id": 66,
        "name": "Fen",
        "user_id": null,
        "status": "active",
        "enrolled": false
      }
    ],
    "matches": []
  }
}

/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-payouts', path: '/t/sgl/admin/payouts/3', wait: 'text=left to place' },
  { name: 'admin-payouts-sglive', path: '/t/sgl/admin/payouts', act: (p) => p.getByRole('button', { name: 'Actions' }).click().then(() => p.getByRole('menuitem', { name: 'Link sglive pages…' }).click()), wait: 'text=Suggested from the name' },
]

export const fixtures = {
  "/api/admin/payouts/board": {
    "tournaments": [
      {
        "id": 3,
        "name": "ALTTPR Main",
        "status": "ready",
        "status_label": "Ready",
        "total": "1590.00",
        "has_pool": true,
        "place_count": 6,
        "sglive_slug": "alttpr"
      },
      {
        "id": 4,
        "name": "Super Metroid",
        "status": "unnamed",
        "status_label": "Winners not named",
        "total": "1120.00",
        "has_pool": true,
        "place_count": 4,
        "sglive_slug": null
      },
      {
        "id": 5,
        "name": "Zelda 1",
        "status": "none",
        "status_label": "No split",
        "total": "640.00",
        "has_pool": true,
        "place_count": 0,
        "sglive_slug": null
      },
      {
        "id": 6,
        "name": "Final Fantasy",
        "status": "none",
        "status_label": "No split",
        "total": "0.00",
        "has_pool": false,
        "place_count": 0,
        "sglive_slug": null
      }
    ],
    "zone": "America/New_York"
  },
  "/api/admin/payouts/3": {
    "id": 3,
    "name": "ALTTPR Main",
    "status": "ready",
    "status_label": "Ready",
    "pool": "1490.00",
    "bonus": "100.00",
    "lines": [
      {
        "place": 1,
        "percentage": "40.00",
        "amount": "636.00",
        "is_fixed": false,
        "entrant_id": 11,
        "note": null
      },
      {
        "place": 2,
        "percentage": "25.00",
        "amount": "397.50",
        "is_fixed": false,
        "entrant_id": 12,
        "note": null
      },
      {
        "place": 3,
        "percentage": "10.00",
        "amount": "159.00",
        "is_fixed": false,
        "entrant_id": 13,
        "note": null
      },
      {
        "place": 3,
        "percentage": "10.00",
        "amount": "159.00",
        "is_fixed": false,
        "entrant_id": 14,
        "note": null
      },
      {
        "place": 5,
        "percentage": null,
        "amount": "100.00",
        "is_fixed": true,
        "entrant_id": null,
        "note": null
      },
      {
        "place": 5,
        "percentage": null,
        "amount": "100.00",
        "is_fixed": true,
        "entrant_id": null,
        "note": null
      }
    ],
    "entrants": [
      {
        "id": 11,
        "name": "Dana Ortiz"
      },
      {
        "id": 12,
        "name": "Kai"
      },
      {
        "id": 13,
        "name": "Morgan Wu"
      },
      {
        "id": 14,
        "name": "Riley"
      }
    ],
    "sglive_slug": "alttpr",
    "synced_at": "2026-11-02T15:00:00Z"
  },
  "/api/admin/payouts/sglive": {
    "games": [
      {
        "slug": "alttpr",
        "name": "A Link to the Past Randomizer",
        "total": "1490.00",
        "tournament_id": 3,
        "suggested": false
      },
      {
        "slug": "sm",
        "name": "Super Metroid",
        "total": "1020.00",
        "tournament_id": 4,
        "suggested": true
      }
    ],
    "tournaments": [
      {
        "id": 3,
        "name": "ALTTPR Main"
      },
      {
        "id": 4,
        "name": "Super Metroid"
      },
      {
        "id": 5,
        "name": "Zelda 1"
      }
    ]
  }
}

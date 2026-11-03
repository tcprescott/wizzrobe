/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-equipment', path: '/t/sgl/admin/equipment' },
  { name: 'admin-equipment-asset', path: '/t/sgl/admin/equipment/51' },
  { name: 'admin-equipment-edit', path: '/t/sgl/admin/equipment/51', act: (p) => p.getByRole('button', { name: 'Edit' }).click(), wait: 'role=dialog' },
  { name: 'admin-equipment-labels', path: '/t/sgl/admin/equipment/labels?ids=51,52,53,54' },
]

export const fixtures = {
  "/api/admin/equipment": {
    "assets": [
      {
        "id": 51,
        "asset_number": 1,
        "name": "Retron 5 (A)",
        "status": "checked_out",
        "description": "Plays SNES, NES, Genesis.",
        "owner_user_id": null,
        "owner_name": null,
        "borrower_name": "Kirby",
        "private_notes": "Power brick is the grey one in the Stage 1 bin.",
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "{{now-2h}}"
      },
      {
        "id": 52,
        "asset_number": 2,
        "name": "Retron 5 (B)",
        "status": "available",
        "description": null,
        "owner_user_id": null,
        "owner_name": null,
        "borrower_name": null,
        "private_notes": null,
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "{{now-2h}}"
      },
      {
        "id": 53,
        "asset_number": 3,
        "name": "SNES Classic",
        "status": "checked_out",
        "description": null,
        "owner_user_id": 1,
        "owner_name": "Thomas",
        "borrower_name": "samus_main",
        "private_notes": null,
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "{{now-2h}}"
      },
      {
        "id": 54,
        "asset_number": 4,
        "name": "Capture card kit",
        "status": "available",
        "description": null,
        "owner_user_id": null,
        "owner_name": null,
        "borrower_name": null,
        "private_notes": null,
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "{{now-2h}}"
      },
      {
        "id": 55,
        "asset_number": 5,
        "name": "Everdrive N8 Pro",
        "status": "retired",
        "description": null,
        "owner_user_id": 4,
        "owner_name": "Juno",
        "borrower_name": null,
        "private_notes": null,
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "{{now-2h}}"
      },
      {
        "id": 56,
        "asset_number": 6,
        "name": "CRT 20\" Sony",
        "status": "available",
        "description": null,
        "owner_user_id": null,
        "owner_name": null,
        "borrower_name": null,
        "private_notes": null,
        "created_at": "2026-09-01T12:00:00Z",
        "updated_at": "{{now-2h}}"
      }
    ],
    "zone": "America/New_York"
  },
  "/api/equipment/51": {
    "id": 51,
    "asset_number": 1,
    "name": "Retron 5 (A)",
    "status": "checked_out",
    "description": "Plays SNES, NES, Genesis.",
    "owner_user_id": null,
    "owner_name": null,
    "borrower_name": "Kirby",
    "private_notes": "Power brick is the grey one in the Stage 1 bin.",
    "created_at": "2026-09-01T12:00:00Z",
    "updated_at": "{{now-2h}}",
    "current_loan": {
      "id": 91,
      "equipment_id": 51,
      "borrower_id": 2,
      "borrower_name": "Kirby",
      "checked_out_at": "{{now-3h}}",
      "checked_out_by_name": "Thomas",
      "checked_in_at": null,
      "checked_in_by_name": null
    }
  },
  "/api/equipment/51/loans": [
    {
      "id": 91,
      "equipment_id": 51,
      "borrower_id": 2,
      "borrower_name": "Kirby",
      "checked_out_at": "{{now-3h}}",
      "checked_out_by_name": "Thomas",
      "checked_in_at": null,
      "checked_in_by_name": null
    },
    {
      "id": 80,
      "equipment_id": 51,
      "borrower_id": 4,
      "borrower_name": "Juno",
      "checked_out_at": "{{now-1d}}",
      "checked_out_by_name": "Thomas",
      "checked_in_at": "{{now-20h}}",
      "checked_in_by_name": "Thomas"
    }
  ],
  "/api/admin/equipment/people": [
    {
      "id": 4,
      "name": "Juno",
      "username": "juno"
    },
    {
      "id": 2,
      "name": "Kirby",
      "username": "kirby"
    },
    {
      "id": 3,
      "name": "samus_main",
      "username": "samus_main"
    },
    {
      "id": 1,
      "name": "Thomas",
      "username": "thomas"
    }
  ],
  "/api/admin/equipment/labels": {
    "community": "SpeedGaming Live",
    "qr_logo": null,
    "assets": [
      {
        "id": 51,
        "asset_number": 1,
        "name": "Retron 5 (A)",
        "qr": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyOSAyOSI+PHJlY3Qgd2lkdGg9IjI5IiBoZWlnaHQ9IjI5IiBmaWxsPSIjZmZmIi8+PHBhdGggZD0iTTQgNGg3djdoLTd6TTE4IDRoN3Y3aC03ek00IDE4aDd2N2gtN3oiLz48L3N2Zz4="
      },
      {
        "id": 52,
        "asset_number": 2,
        "name": "Retron 5 (B)",
        "qr": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyOSAyOSI+PHJlY3Qgd2lkdGg9IjI5IiBoZWlnaHQ9IjI5IiBmaWxsPSIjZmZmIi8+PHBhdGggZD0iTTQgNGg3djdoLTd6TTE4IDRoN3Y3aC03ek00IDE4aDd2N2gtN3oiLz48L3N2Zz4="
      },
      {
        "id": 53,
        "asset_number": 3,
        "name": "SNES Classic",
        "qr": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyOSAyOSI+PHJlY3Qgd2lkdGg9IjI5IiBoZWlnaHQ9IjI5IiBmaWxsPSIjZmZmIi8+PHBhdGggZD0iTTQgNGg3djdoLTd6TTE4IDRoN3Y3aC03ek00IDE4aDd2N2gtN3oiLz48L3N2Zz4="
      },
      {
        "id": 54,
        "asset_number": 4,
        "name": "Capture card kit",
        "qr": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyOSAyOSI+PHJlY3Qgd2lkdGg9IjI5IiBoZWlnaHQ9IjI5IiBmaWxsPSIjZmZmIi8+PHBhdGggZD0iTTQgNGg3djdoLTd6TTE4IDRoN3Y3aC03ek00IDE4aDd2N2gtN3oiLz48L3N2Zz4="
      }
    ]
  }
}

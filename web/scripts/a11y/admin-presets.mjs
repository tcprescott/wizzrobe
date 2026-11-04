/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-presets', path: '/t/sgl/admin/seed-presets/5' },
  { name: 'admin-presets-paste', path: '/t/sgl/admin/seed-presets/6' },
  { name: 'admin-presets-import', path: '/t/sgl/admin/seed-presets', act: async (p) => { await p.getByRole('button', { name: 'Actions' }).first().click(); await p.getByRole('menuitem', { name: /Import from/ }).click() }, wait: 'role=dialog' },
]

export const fixtures = {
  "/api/presets": [
    {
      "id": 5,
      "name": "sgl2026",
      "randomizer": "alttpr",
      "description": "SGL 2026 standard",
      "listed": true,
      "settings": {
        "goal": "ganon",
        "mode": "open"
      },
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    },
    {
      "id": 6,
      "name": "sgl2026",
      "randomizer": "ffr",
      "listed": false,
      "settings": {
        "flags": "HACAAAAAAAAAAAA"
      },
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    }
  ],
  "/api/admin/presets/editor": {
    "randomizers": [
      {
        "slug": "alttpr",
        "name": "A Link to the Past",
        "available": true,
        "rolls_from_flags": false,
        "paste_field": null,
        "offers_remote_presets": true,
        "remote_branches": []
      },
      {
        "slug": "ffr",
        "name": "Final Fantasy",
        "available": true,
        "rolls_from_flags": true,
        "paste_field": {
          "key": "flags",
          "label": "Flag string",
          "placeholder": "Paste the flags from finalfantasyrandomizer.com",
          "hint": "Copy them from the site's Share button."
        },
        "offers_remote_presets": false,
        "remote_branches": []
      }
    ],
    "usage": [
      {
        "preset_id": 5,
        "tournaments": [
          "ALTTPR Main"
        ]
      }
    ]
  },
  "/api/admin/presets/check-paste": {
    "kind": "example",
    "example": "https://finalfantasyrandomizer.com/Randomize?s=1234&f=HACAAAAAAAAAAAA"
  },
  "/api/admin/presets/remote": [
    {
      "name": "open",
      "description": "Open mode"
    },
    {
      "name": "standard",
      "description": null
    }
  ]
}

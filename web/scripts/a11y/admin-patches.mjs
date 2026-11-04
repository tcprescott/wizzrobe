/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-patches', path: '/t/sgl/admin/patches' },
  { name: 'admin-patch', path: '/t/sgl/admin/patches/3' },
  { name: 'admin-patch-upload', path: '/t/sgl/admin/patches', act: (p) => p.getByRole('button', { name: 'Upload patch' }).click(), wait: 'role=dialog' },
  { name: 'admin-patch-edit', path: '/t/sgl/admin/patches/3', act: (p) => p.getByRole('button', { name: 'More for Super Metroid practice ROM' }).click().then(() => p.getByRole('menuitem', { name: 'Edit…' }).click()), wait: 'role=dialog' },
]

const patch = (over) => ({
  "token": "sm-practice",
  "title": "Super Metroid practice ROM",
  "game": "Super Metroid (JU)",
  "description": "Save states on Select + R, room timers on the HUD.",
  "format": "ips",
  "source_crc": "D63ED5F8",
  "tournament": "Super Metroid",
  "community": "SpeedGaming Live",
  "updated_at": "{{now-2d}}",
  "filename_base": "Super_Metroid_practice_ROM",
  "id": 3,
  "filename": "sm_practice_2.3.4.ips",
  "size": 182044,
  "tournament_id": 4,
  "download_count": 27,
  "uploaded_by": "Kirby",
  "created_at": "{{now-9d}}",
  ...over,
})

export const fixtures = {
  "/api/admin/patches": {
    "patches": [
      patch({}),
      patch({ "id": 4, "token": "z1-hack", "title": "Zelda 1 second quest swap", "game": "Legend of Zelda (U) PRG0", "format": "bps", "source_crc": "D7AE93DF", "tournament": null, "tournament_id": null, "download_count": 1, "description": null, "filename": "z1_swap.bps" }),
    ],
    "tournaments": [
      { "id": 4, "name": "Super Metroid" },
      { "id": 5, "name": "Zelda 1 Randomizer" },
    ],
  },
}

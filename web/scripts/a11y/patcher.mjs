/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'door-seed', path: '/t/sgl/door/dr-a11y' },
  { name: 'door-seed-signed-out', path: '/t/sgl/door/dr-a11y', signedIn: false },
  { name: 'door-seed-customize', path: '/t/sgl/door/dr-a11y', act: (p) => p.getByRole('button', { name: 'Customize' }).click(), wait: '#door-customize' },
  { name: 'door-seed-sprites', path: '/t/sgl/door/dr-a11y', act: async (p) => { await p.getByRole('button', { name: 'Customize' }).click(); await p.getByRole('button', { name: 'Choose sprite…' }).click() }, wait: 'role=dialog' },
  { name: 'hosted-patch', path: '/t/sgl/patch/sm-practice', signedIn: false },
  { name: 'rom-patcher', path: '/t/sgl/patcher' },
]

export const fixtures = {
  "/api/door-seeds/dr-a11y": {
    "token": "dr-a11y",
    "version": "1.5.7-u",
    "seed_code": ["Bow", "Lamp", "Hookshot", "Bombs", "Mushroom"],
    "created_at": "{{now-1h}}",
    "spoiler": true,
    "spoiler_staff_only": true,
    "filename": "DR_dr-a11y.sfc",
  },
  "/api/door-seeds/sprites": [
    { "name": "Mike Jones", "author": "Fish", "file": "https://alttpr-assets.s3.us-east-2.amazonaws.com/mike.zspr", "preview": null },
  ],
  "/api/patches/sm-practice": {
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
  },
}

/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-events', path: '/t/sgl/admin/events/sgl27' },
  { name: 'admin-events-draft', path: '/t/sgl/admin/events/sgl28' },
  {
    name: 'admin-events-new',
    path: '/t/sgl/admin/events',
    act: (p) => p.getByRole('button', { name: 'New event' }).click(),
    wait: 'role=dialog',
  },
  {
    name: 'admin-events-copy',
    path: '/t/sgl/admin/events/sgl27',
    act: (p) => p.getByRole('button', { name: /^More actions for / }).click().then(() => p.getByRole('menuitem', { name: 'New event from this…' }).click()),
    wait: 'role=dialog',
  },
  {
    name: 'admin-events-archive',
    path: '/t/sgl/admin/events/sgl27',
    act: (p) => p.getByRole('button', { name: 'Archive…' }).click(),
    wait: 'role=dialog',
  },
]

export const fixtures = {
  "/api/events": [
    {
      "id": 3,
      "slug": "sgl27",
      "name": "SGL 2027",
      "status": "live",
      "is_default": true,
      "starts_on": "2027-10-21",
      "ends_on": "2027-10-24"
    },
    {
      "id": 4,
      "slug": "sgl28",
      "name": "SGL 2028",
      "status": "draft",
      "is_default": false,
      "starts_on": null,
      "ends_on": null
    },
    {
      "id": 2,
      "slug": "sgl26",
      "name": "SGL 2026",
      "status": "archived",
      "is_default": false,
      "starts_on": "2026-10-22",
      "ends_on": "2026-10-25"
    }
  ],
  "/api/events/sgl27/summary": {
    "tournaments": 14,
    "matches": 1302,
    "open_matches": 4,
    "future_shifts": 2,
    "open_race_rooms": 0
  },
  "/api/events/sgl28/summary": {
    "tournaments": 3,
    "matches": 0,
    "open_matches": 0,
    "future_shifts": 0,
    "open_race_rooms": 0
  }
}

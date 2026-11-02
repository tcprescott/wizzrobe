/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'sign-in', path: '/sign-in', signedIn: false },
  { name: 'kit', path: '/kit', signedIn: false },
  { name: 'kit-dialog', path: '/kit', signedIn: false, act: (p) => p.getByRole('button', { name: 'Open dialog' }).click(), wait: 'role=dialog' },
  { name: 'kit-menu', path: '/kit', signedIn: false, act: (p) => p.getByRole('button', { name: 'More for this row' }).click(), wait: 'role=menu' },
  { name: 'communities', path: '/' },
  { name: 'picker', path: '/t/sgl/home', act: (p) => p.getByRole('button', { name: /Change event or community/ }).click(), wait: 'role=menu' },
  { name: 'player-no-work', path: '/t/second/home' },
  { name: 'not-a-member', path: '/t/elsewhere/home' },
  { name: 'not-found', path: '/nowhere' },
]

export const fixtures = {
  "/api/me/communities": [
    {
      "slug": "sgl",
      "name": "SpeedGaming Live",
      "roles": [
        "proctor",
        "staff"
      ],
      "events": [
        {
          "slug": "sgl27",
          "name": "SGL 2027",
          "status": "live",
          "is_default": true,
          "starts_on": "2027-10-21",
          "ends_on": "2027-10-24"
        },
        {
          "slug": "sgl26",
          "name": "SGL 2026",
          "status": "archived",
          "is_default": false,
          "starts_on": "2026-10-22",
          "ends_on": "2026-10-25"
        }
      ]
    },
    {
      "slug": "second",
      "name": "Second Community",
      "roles": [],
      "events": [
        {
          "slug": "season-1",
          "name": "Season 1",
          "status": "live",
          "is_default": true,
          "starts_on": null,
          "ends_on": null
        }
      ]
    }
  ],
  "/api/users/me": {
    "id": 1,
    "username": "thomas",
    "display_name": "Thomas",
    "pronouns": null,
    "discord_id": null,
    "dm_notifications": true,
    "is_active": true,
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z",
    "roles": [
      "staff",
      "proctor"
    ]
  }
}

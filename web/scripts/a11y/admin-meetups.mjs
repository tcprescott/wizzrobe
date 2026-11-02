/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-meetups', path: '/t/sgl/admin/meetups' },
  { name: 'admin-meetup', path: '/t/sgl/admin/meetups/21' },
  { name: 'admin-meetup-edit', path: '/t/sgl/admin/meetups/21', act: (p) => p.getByRole('button', { name: 'More for Speedrun trivia night' }).click().then(() => p.getByRole('menuitem', { name: 'Edit…' }).click()), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/admin/meetups": {
    "meetups": [
      {
        "id": 21,
        "title": "Speedrun trivia night",
        "description": "Bring your phone, we'll run it on Kahoot. Prizes are bragging rights and one shirt.",
        "location": "Room 204",
        "starts_at": "{{now+30h}}",
        "duration_minutes": 120,
        "status": "pending",
        "host_id": 8,
        "host_name": "Tavi",
        "review_note": null,
        "rsvp_enabled": true,
        "rsvp_capacity": 40
      },
      {
        "id": 22,
        "title": "Randomizer swap meet",
        "description": "Trade settings, trade stories.",
        "location": "Lobby bar",
        "starts_at": "{{now+2d}}",
        "duration_minutes": 120,
        "status": "approved",
        "host_id": 2,
        "host_name": "Kirby",
        "review_note": null,
        "rsvp_enabled": true,
        "rsvp_capacity": null
      },
      {
        "id": 23,
        "title": "Late-night Mario Kart",
        "description": "Eight players, four Switches.",
        "location": "Stage 3 after close",
        "starts_at": "{{now+3d}}",
        "duration_minutes": 120,
        "status": "rejected",
        "host_id": 4,
        "host_name": "Juno",
        "review_note": "Stage 3 is locked after close. Pick a room off the main floor.",
        "rsvp_enabled": false,
        "rsvp_capacity": null
      },
      {
        "id": 24,
        "title": "Breakfast with the commentators",
        "description": "",
        "location": "Hotel caf\u00e9",
        "starts_at": "{{now+4d}}",
        "duration_minutes": 120,
        "status": "cancelled",
        "host_id": 3,
        "host_name": "samus_main",
        "review_note": null,
        "rsvp_enabled": false,
        "rsvp_capacity": null
      }
    ],
    "zone": "America/New_York"
  },
  "/api/admin/meetups/21": {
    "id": 21,
    "title": "Speedrun trivia night",
    "description": "Bring your phone, we'll run it on Kahoot. Prizes are bragging rights and one shirt.",
    "location": "Room 204",
    "starts_at": "{{now+30h}}",
    "duration_minutes": 120,
    "status": "pending",
    "host_id": 8,
    "host_name": "Tavi",
    "review_note": null,
    "rsvp_enabled": true,
    "rsvp_capacity": 40
  },
  "/api/admin/meetups/21/attendees": [
    {
      "id": 2,
      "name": "Kirby"
    },
    {
      "id": 3,
      "name": "samus_main"
    }
  ]
}

/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  ...['dates', 'capacity', 'venue', 'timezone', 'crew', 'reward-tiers'].map((s) => ({
    name: `admin-event-settings-${s}`,
    path: `/t/sgl/admin/event-settings/${s}`,
  })),
  {
    name: 'admin-event-settings-station',
    path: '/t/sgl/admin/event-settings/venue',
    act: (p) => p.getByRole('button', { name: 'Add station' }).click(),
    wait: 'role=dialog',
  },
  {
    name: 'admin-event-settings-hours-unsaved',
    path: '/t/sgl/admin/event-settings/dates',
    act: (p) => p.getByLabel(/, opens$/).last().fill('12:00'),
    wait: 'text=Unsaved changes',
  },
]

export const fixtures = {
  "/api/event-settings/dates": {
    "starts_on": "2027-10-21",
    "ends_on": "2027-10-24",
    "days": [
      {
        "day": "2027-10-21",
        "opens": "10:00",
        "closes": "23:00"
      },
      {
        "day": "2027-10-22",
        "opens": "10:00",
        "closes": "23:00"
      },
      {
        "day": "2027-10-23",
        "opens": "10:00",
        "closes": "23:00"
      },
      {
        "day": "2027-10-24",
        "opens": null,
        "closes": null
      }
    ],
    "timezone_label": "Eastern Time (EDT)"
  },
  "/api/event-settings/capacity": {
    "max_players": 60,
    "max_stages": null
  },
  "/api/event-settings/timezone": {
    "follows_community": false,
    "mode": "pinned",
    "name": "America/New_York",
    "community_mode": "user",
    "community_name": "America/New_York",
    "options": [
      "America/New_York",
      "America/Chicago",
      "America/Los_Angeles",
      "Europe/London",
      "Europe/Berlin"
    ]
  },
  "/api/event-settings/crew": {
    "broadcasters": true
  },
  "/api/event-settings/reward-tiers": {
    "text": "",
    "tiers": [
      8,
      12,
      16
    ]
  },
  "/api/event-settings/station-format": {
    "format": "structured",
    "options": [
      {
        "value": "free",
        "label": "Anything (no check)"
      },
      {
        "value": "numeric",
        "label": "Numbers only (1, 2, 3…)"
      },
      {
        "value": "structured",
        "label": "Letter and number (A1, B12)"
      },
      {
        "value": "alphanumeric",
        "label": "Letters, numbers and hyphens"
      }
    ]
  },
  "/api/event-settings/stations": [
    {
      "id": 1,
      "name": "A1",
      "section": "Main hall",
      "side": "left",
      "position": 1,
      "sort_order": 0,
      "is_active": true
    },
    {
      "id": 2,
      "name": "A2",
      "section": "Main hall",
      "side": null,
      "position": 2,
      "sort_order": 1,
      "is_active": true
    },
    {
      "id": 3,
      "name": "B1",
      "section": "Side room",
      "side": "right",
      "position": null,
      "sort_order": 2,
      "is_active": false
    }
  ],
  "/api/event-settings/room-screens": [
    {
      "id": 1,
      "label": "Room A desk PC",
      "revoked": false,
      "last_used_at": "{{now-5m}}",
      "issued_by": "Thomas"
    },
    {
      "id": 2,
      "label": "Old laptop",
      "revoked": true,
      "last_used_at": null,
      "issued_by": null
    }
  ]
}

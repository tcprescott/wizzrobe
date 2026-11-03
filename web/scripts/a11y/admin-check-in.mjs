/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-check-in', path: '/t/sgl/admin/check-in-roster' },
  { name: 'admin-check-in-event', path: '/t/sgl/admin/check-in-roster/61' },
  { name: 'admin-check-in-edit', path: '/t/sgl/admin/check-in-roster/61', act: (p) => p.getByRole('button', { name: 'Edit' }).click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/admin/check-in": {
    "events": [
      {
        "id": 61,
        "name": "SGL27 badges",
        "status": "open",
        "matcherino_venue_id": 4417,
        "comp_roles": [
          "staff",
          "stream_manager"
        ],
        "comp_volunteers": true,
        "sync_interval_minutes": 5,
        "last_synced_at": "{{now-12m}}",
        "last_sync_error": null,
        "last_sync_count": 980
      },
      {
        "id": 62,
        "name": "Comps only",
        "status": "draft",
        "matcherino_venue_id": null,
        "comp_roles": [
          "staff"
        ],
        "comp_volunteers": false,
        "sync_interval_minutes": 5,
        "last_synced_at": null,
        "last_sync_error": null,
        "last_sync_count": null
      },
      {
        "id": 63,
        "name": "Dry run",
        "status": "closed",
        "matcherino_venue_id": 4380,
        "comp_roles": [],
        "comp_volunteers": false,
        "sync_interval_minutes": 10,
        "last_synced_at": "2026-10-18T19:00:00Z",
        "last_sync_error": "Matcherino refused the login.",
        "last_sync_count": 12
      }
    ],
    "login": {
      "configured": true,
      "matcherino_user_id": "88213",
      "updated_at": "{{now-2d}}",
      "updated_by": "Thomas",
      "alert_user_id": 1,
      "alert_user": "Thomas"
    },
    "comp_role_options": [
      {
        "value": "staff",
        "label": "Staff"
      },
      {
        "value": "proctor",
        "label": "Proctor"
      },
      {
        "value": "stream_manager",
        "label": "Broadcast Tech"
      },
      {
        "value": "volunteer_coordinator",
        "label": "Volunteer coordinator"
      }
    ],
    "lanyard_options": [
      {
        "value": "vip",
        "label": "VIP"
      },
      {
        "value": "base",
        "label": "Base"
      },
      {
        "value": "day_pass",
        "label": "Day pass"
      }
    ],
    "volunteer_comp_hours": 8,
    "zone": "America/New_York"
  },
  "/api/admin/check-in/events/61/tiers": [
    {
      "id": 71,
      "title": "Weekend badge",
      "amount_cents": 6500,
      "lanyard": "base"
    },
    {
      "id": 72,
      "title": "VIP badge",
      "amount_cents": 15000,
      "lanyard": "vip"
    }
  ]
}

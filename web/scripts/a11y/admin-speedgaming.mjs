/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-speedgaming', path: '/t/sgl/admin/speedgaming/1' },
  { name: 'admin-speedgaming-add', path: '/t/sgl/admin/speedgaming', act: (p) => p.getByRole('button', { name: 'Link an event' }).click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/speedgaming/links": [
    {
      "id": 1,
      "tournament_id": 1,
      "event_slug": "alttprleague",
      "content_type": null,
      "active": true,
      "sync_interval_minutes": 15,
      "lookahead_hours": 72,
      "last_status": "ok",
      "last_synced_at": "{{now-10m}}",
      "last_error": null,
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    },
    {
      "id": 2,
      "tournament_id": 2,
      "event_slug": "smleague",
      "active": false,
      "sync_interval_minutes": 15,
      "lookahead_hours": 72,
      "last_status": "error",
      "last_error": "SpeedGaming didn't answer.",
      "last_synced_at": "{{now-1d}}",
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    }
  ],
  "/api/speedgaming/links/1/episodes": [
    {
      "id": 1,
      "sg_episode_id": "4812",
      "title": "Ari vs Bex",
      "scheduled_at": "{{now+5h}}",
      "sync_status": "synced",
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    },
    {
      "id": 2,
      "sg_episode_id": "4813",
      "title": "Cato vs Dee",
      "scheduled_at": "{{now+1d}}",
      "sync_status": "error",
      "sync_error": "No such player",
      "created_at": "{{now-10d}}",
      "updated_at": "{{now-10d}}"
    }
  ]
}

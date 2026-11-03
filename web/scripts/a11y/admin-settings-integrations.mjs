/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-settings-integrations', path: '/t/sgl/admin/settings/integrations' },
  {
    name: 'admin-settings-integrations-webhook',
    path: '/t/sgl/admin/settings/integrations',
    act: (p) => p.getByRole('button', { name: 'Add', exact: true }).click(),
    wait: 'text=Events to send',
  },
  {
    name: 'admin-settings-integrations-key-unsaved',
    path: '/t/sgl/admin/settings/integrations',
    act: (p) => p.getByLabel('OoT Randomizer API key').fill('new-key'),
    wait: 'text=Unsaved changes',
  },
  {
    name: 'admin-settings-integrations-patreon-labels',
    path: '/t/sgl/admin/settings/integrations',
    act: (p) => p.getByRole('button', { name: 'Badge labels…' }).click(),
    wait: 'role=dialog',
  },
]

export const fixtures = {
  "/api/settings/integrations": {
    "challonge": true,
    "startgg": true,
    "webhooks": true,
    "randomizer_keys": true,
    "ai": true,
    "patreon": true
  },
  "/api/settings/integrations/patreon": {
    "configured": true,
    "connected": true,
    "campaign_name": "SpeedGaming",
    "connected_by": "Thomas (you)",
    "connected_at": "{{now-2d}}",
    "last_synced_at": "{{now-12m}}",
    "last_sync_error": null,
    "sync_pending": false,
    "linked_members": 37,
    "patrons": 31,
    "tiers": [
      { "tier_id": "t-hero", "title": "Hero", "badge_label": "Hero patron" },
      { "tier_id": "t-supporter", "title": "Supporter", "badge_label": null }
    ]
  },
  "/api/settings/integrations/challonge": {
    "configured": true,
    "connected": true,
    "username": "sgl_brackets",
    "can_write_signups": true,
    "linked_tournaments": 8,
    "request_usage": 1204,
    "auto_sync_minutes": 30,
    "auto_sync_choices": [0, 15, 30, 60, 120, 360],
    "auto_sync_brackets": 6,
    "auto_sync_requests_per_day": 576,
    "communities": [{ "slug": "speedgaming", "tournaments": 8 }]
  },
  "/api/settings/integrations/startgg": {
    "configured": true,
    "platform_token": false,
    "connected": false,
    "username": null,
    "expired": false,
    "renewed_at": null,
    "linked": []
  },
  "/api/settings/integrations/webhooks": [
    {
      "id": 1,
      "name": "SG Schedule Update",
      "url": "https://hooks.speedgaming.org/wizzrobe",
      "event_types": ["match.rescheduled", "match.stage_assigned", "match.stage_cleared", "match.deleted", "match.cancelled", "crew.approval_changed"],
      "event_ids": null,
      "is_active": true,
      "delivered": 42,
      "failed": 0,
      "last_sent_at": "{{now-2m}}"
    },
    {
      "id": 2,
      "name": "Restream bot",
      "url": "https://bot.example.net/hook",
      "event_types": ["*"],
      "event_ids": [1],
      "is_active": false,
      "delivered": 3,
      "failed": 2,
      "last_sent_at": "{{now-3h}}"
    }
  ],
  "/api/settings/integrations/webhook-options": {
    "event_types": ["*", "crew.approval_changed", "match.cancelled", "match.created", "match.rescheduled"],
    "events": [{ "id": 1, "name": "SGL 2027" }, { "id": 2, "name": "SGL 2026 (Archived)" }]
  },
  "/api/settings/integrations/randomizer-keys": [
    { "randomizer": "ootr", "key": "api_key", "label": "OoT Randomizer API key", "help_text": "Sent as the \"key\" parameter to ootrandomizer.com. Request one from the Ocarina of Time Randomizer team.", "configured": true, "updated_at": "{{now-30d}}" },
    { "randomizer": "smmap", "key": "spoiler_token", "label": "Map Rando spoiler token", "help_text": "Sent to maprando.com with each roll. Issued by the Map Rando team.", "configured": false, "updated_at": null },
    { "randomizer": "dk64r", "key": "api_key", "label": "DK64 Randomizer API key", "help_text": "Sent as the X-API-Key header to api.dk64rando.com.", "configured": false, "updated_at": null }
  ],
  "/api/settings/integrations/ai": {
    "configured": true,
    "provider": "anthropic",
    "model": "claude-opus-5-5",
    "base_url": null,
    "daily_limit": 20,
    "key_readable": true,
    "updated_at": "{{now-5d}}",
    "default_models": { "anthropic": "claude-opus-5-5", "openai_compatible": "" }
  }
}

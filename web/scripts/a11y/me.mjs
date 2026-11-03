/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'me', path: '/t/sgl/me' },
  ...['about', 'notifications', 'availability', 'accounts', 'staff-alerts', 'equipment', 'accommodation', 'emergency-contact', 'feedback', 'webhooks', 'apps', 'tokens', 'appearance'].map(
    (s) => ({ name: `me-${s}`, path: `/t/sgl/me/${s}` }),
  ),
  { name: 'me-customize', path: '/t/sgl/me/notifications', act: (p) => p.getByRole('button', { name: 'Show' }).click(), wait: 'role=group' },
  { name: 'me-notifications-app', path: '/t/sgl/me/notifications', native: 'ios' },
  { name: 'me-send-test', path: '/t/sgl/me/notifications', act: (p) => p.getByRole('button', { name: 'Send a test…' }).click(), wait: 'role=dialog' },
  { name: 'me-block-time', path: '/t/sgl/me/availability', act: (p) => p.getByRole('button', { name: 'Block a time' }).click(), wait: 'role=dialog' },
  { name: 'me-add-webhook', path: '/t/sgl/me/webhooks', act: (p) => p.getByRole('button', { name: 'Add webhook' }).click(), wait: 'role=dialog' },
  { name: 'me-register-app', path: '/t/sgl/me/apps', act: (p) => p.getByRole('button', { name: 'Register an app' }).click(), wait: 'role=dialog' },
  { name: 'me-patreon-unlink', path: '/t/sgl/me/accounts', act: (p) => p.getByRole('button', { name: 'Patreon actions' }).click(), wait: 'role=menu' },
  { name: 'me-sign-out', path: '/t/sgl/me/appearance', act: (p) => p.getByRole('button', { name: 'Sign out' }).click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/me/profile": {
    "display_name": "Sam Ruiz",
    "preferred_name": "Sam Ruiz",
    "pronouns": "they/them",
    "discord_username": "samruns",
    "avatar_url": null,
    "timezone": "America/Chicago",
    "hour_cycle": "12",
    "community_zone": "America/New_York",
    "zone_pinned": true,
    "matcherino_username": "samruns#123456",
    "payouts": true,
    "dm_notifications": true,
    "sections": [
      {
        "key": "about",
        "title": "About you",
        "summary": "Sam Ruiz"
      },
      {
        "key": "notifications",
        "title": "Notifications",
        "summary": "Discord DMs, 2 devices"
      },
      {
        "key": "availability",
        "title": "Availability",
        "summary": "3 blocked times"
      },
      {
        "key": "accounts",
        "title": "Connected accounts",
        "summary": "Challonge, racetime.gg"
      },
      {
        "key": "staff-alerts",
        "title": "Staff alerts",
        "summary": "On duty now: Admin Desk"
      },
      {
        "key": "equipment",
        "title": "Equipment",
        "summary": "1 checked out"
      },
      {
        "key": "accommodation",
        "title": "Accommodations",
        "summary": "Arranged"
      },
      {
        "key": "emergency-contact",
        "title": "Emergency contact",
        "summary": "Jordan Ruiz"
      },
      {
        "key": "feedback",
        "title": "My feedback",
        "summary": "2 notes sent"
      },
      {
        "key": "webhooks",
        "title": "Webhooks",
        "summary": "1 webhook"
      },
      {
        "key": "apps",
        "title": "Developer apps",
        "summary": "1 app"
      },
      {
        "key": "tokens",
        "title": "API tokens",
        "summary": "1 token"
      }
    ]
  },
  "/api/me/channels": {
    "discord": true,
    "dm_notifications": true,
    "push_configured": true,
    "push_public_key": "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LFgDzkrxZJjSgSnfckjBJuBkr3qBUYIHBQFLXYp5Nksh8U",
    "devices": [
      {
        "id": 1,
        "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
        "created_at": "{{now-3d}}",
        "last_used_at": "{{now-1h}}",
        "endpoint_host": "web.push.apple.com"
      },
      {
        "id": 2,
        "user_agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36",
        "created_at": "{{now-10d}}",
        "last_used_at": null,
        "endpoint_host": "fcm.googleapis.com"
      }
    ],
    "native_push_platforms": [
      "ios",
      "android"
    ],
    "native_devices": [
      {
        "id": 7,
        "platform": "ios",
        "device_name": "iPhone 16",
        "created_at": "{{now-2d}}",
        "last_used_at": "{{now-2h}}"
      }
    ],
    "registered_device_id": null,
    "channels_enabled": true,
    "email_available": true,
    "email": "sam@example.com",
    "email_notifications": false,
    "telegram_available": true,
    "telegram_username": null,
    "telegram_connected": false,
    "telegram_notifications": false
  },
  "/api/notifications/messages": {
    "preset": "needs_me",
    "changed": 1,
    "messages": [
      {
        "notice": "match_scheduled",
        "recipient": "players",
        "section": "Your matches",
        "title": "Match booked or moved",
        "when": "When a time is set or changes",
        "community": "ping",
        "preset": "ping",
        "override": null,
        "mode": "ping",
        "changed": false,
        "opt_in": false
      },
      {
        "notice": "match_reminder",
        "recipient": "players",
        "section": "Your matches",
        "title": "Match about to start",
        "when": "30 minutes before",
        "community": "ping",
        "preset": "ping",
        "override": null,
        "mode": "ping",
        "changed": false,
        "opt_in": false
      },
      {
        "notice": "watch_update",
        "recipient": "watchers",
        "section": "Following",
        "title": "Matches you watch",
        "when": "When a watched match changes",
        "community": "silent",
        "preset": "silent",
        "override": "off",
        "mode": "off",
        "changed": true,
        "opt_in": false
      }
    ]
  },
  "/api/me/followable-tournaments": [
    {
      "id": 1,
      "name": "ALTTPR Open",
      "playing": true,
      "level": "all"
    },
    {
      "id": 2,
      "name": "SMZ3",
      "playing": false,
      "level": "none"
    }
  ],
  "/api/users/me/availability": [
    {
      "id": 1,
      "starts_at": "{{now+1h}}",
      "ends_at": "{{now+4h}}",
      "status": "unavailable",
      "note": "Flight in"
    },
    {
      "id": 2,
      "starts_at": "{{now+28h}}",
      "ends_at": "{{now+30h}}",
      "status": "unavailable",
      "note": "Dinner with the team"
    },
    {
      "id": 3,
      "starts_at": "{{now+50h}}",
      "ends_at": "{{now+53h}}",
      "status": "preferred",
      "note": null
    }
  ],
  "/api/me/patreon": {
    "configured": true,
    "linked": true,
    "username": "Sam R.",
    "linked_at": "{{now-30d}}",
    "badge_hidden": false,
    "connected_here": true,
    "here": { "is_patron": true, "tier_ids": ["t-supporter"], "badge_label": "Patron", "badge_hidden": false, "last_synced": "{{now-1h}}" },
    "communities": [
      { "community": "SpeedGaming Live", "is_patron": true, "badge_label": "Patron" },
      { "community": "ALTTPR Community", "is_patron": false, "badge_label": null }
    ]
  },
  "/api/me/accounts": [
    {
      "key": "challonge",
      "title": "Challonge",
      "description": "Link your Challonge account so we can find your bracket matches for scheduling.",
      "linked": true,
      "username": "samruns",
      "linked_at": "{{now-30d}}",
      "link_path": "/challonge/link",
      "sign_in_first_url": "https://challonge.com/user_session/new",
      "unlink_warning": "We'll stop matching you to Challonge bracket matches until you link again."
    },
    {
      "key": "twitch",
      "title": "Twitch",
      "description": "Link your Twitch account so restreams and overlays can show your channel.",
      "linked": false,
      "username": null,
      "linked_at": null,
      "link_path": "/twitch/link",
      "sign_in_first_url": null,
      "unlink_warning": null
    },
    {
      "key": "racetime",
      "title": "racetime.gg",
      "description": "Link your racetime.gg account so we can attribute your race results.",
      "linked": true,
      "username": "Sam#1234",
      "linked_at": "{{now-60d}}",
      "link_path": "/racetime/link",
      "sign_in_first_url": null,
      "unlink_warning": null
    }
  ],
  "/api/me/staff-alerts": {
    "duty_position": "Admin Desk",
    "on_duty_now": true,
    "next_duty_start": null,
    "tournaments": [
      {
        "id": 1,
        "name": "ALTTPR Open",
        "on": true
      },
      {
        "id": 2,
        "name": "SMZ3",
        "on": false
      }
    ]
  },
  "/api/equipment/me/checkouts": [
    {
      "loan_id": 1,
      "equipment_id": 4,
      "asset_number": 14,
      "name": "Wireless controller",
      "checked_out_at": "{{now-2h}}"
    }
  ],
  "/api/me/accommodation": {
    "requested": true,
    "details": "A chair at the station, please.",
    "status": "arranged",
    "changed_since_arranged": false,
    "max_length": 2000
  },
  "/api/me/emergency-contact": {
    "name": "Jordan Ruiz",
    "relationship": "Sibling",
    "phone": "+1 312 555 0142",
    "last_viewed_at": "{{now-1d}}",
    "last_viewed_by": "Ellie"
  },
  "/api/feedback/mine": [
    {
      "id": 1,
      "category": "suggestion",
      "status": "reviewed",
      "message": "Could the schedule show my stage too?",
      "page_url": null,
      "submitted_by_id": 1,
      "submitted_by_name": "Sam Ruiz",
      "created_at": "{{now-5d}}"
    },
    {
      "id": 2,
      "category": "bug",
      "status": "new",
      "message": "The map wouldn't load.",
      "page_url": null,
      "submitted_by_id": 1,
      "submitted_by_name": "Sam Ruiz",
      "created_at": "{{now-1d}}"
    }
  ],
  "/api/me/webhooks": {
    "areas": [
      {
        "key": "matches",
        "label": "Matches",
        "hint": "Booked, moved, started, finished"
      },
      {
        "key": "crew",
        "label": "Crew",
        "hint": "Your signups and approvals"
      }
    ],
    "events": [
      {
        "event_type": "match.created",
        "area": "matches",
        "label": "Booked",
        "mine_only": false
      },
      {
        "event_type": "match.rescheduled",
        "area": "matches",
        "label": "Moved",
        "mine_only": false
      },
      {
        "event_type": "crew.signup_created",
        "area": "crew",
        "label": "Signed up",
        "mine_only": true
      }
    ],
    "webhooks": [
      {
        "id": 1,
        "name": "Team channel",
        "url_masked": "discord.com/api/webhooks/123456…/••••",
        "discord": true,
        "event_types": [
          "match.created",
          "match.rescheduled"
        ],
        "only_mine": true,
        "is_active": true,
        "paused_reason": null,
        "last_delivery_at": "{{now-3h}}",
        "last_status": 204,
        "last_error": null,
        "delivered_count": 12,
        "failed_count": 0
      }
    ],
    "max_webhooks": 3
  },
  "/api/me/apps": [
    {
      "id": 1,
      "name": "Stream overlay",
      "description": "",
      "homepage_url": null,
      "client_type": "confidential",
      "client_id": "wz_app_Q2xpZW50SWQxMjM0",
      "secret_prefix": "wzs_abc123",
      "redirect_uris": [
        "https://overlay.example.com/callback"
      ],
      "allowed_scopes": [
        "profile",
        "schedule:read"
      ],
      "disabled_reason": null,
      "created_at": "{{now-20d}}"
    }
  ],
  "/api/tokens": [
    {
      "id": 1,
      "name": "OBS overlay",
      "token_prefix": "wz_ab12",
      "read_only": true,
      "last_used_at": "{{now-2d}}",
      "expires_at": null,
      "created_at": "{{now-40d}}"
    }
  ]
}

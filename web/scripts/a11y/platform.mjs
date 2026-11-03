/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'platform', path: '/platform' },
  ...['communities', 'users', 'features', 'bots', 'apps', 'health', 'logging', 'monitoring'].map((s) => ({ name: `platform-${s}`, path: `/platform/${s}` })),
  { name: 'platform-new-community', path: '/platform/communities', act: (p) => p.getByRole('button', { name: 'New community' }).click(), wait: 'role=dialog' },
  { name: 'platform-community-features', path: '/platform/communities', act: (p) => p.getByRole('button', { name: 'Features' }).first().click(), wait: 'role=dialog' },
  { name: 'platform-staff', path: '/platform/communities', act: async (p) => { await p.getByRole('button', { name: 'More for SpeedGaming Live' }).click(); await p.getByRole('menuitem', { name: 'Staff' }).click() }, wait: 'role=dialog' },
  { name: 'platform-new-account', path: '/platform/users', act: (p) => p.getByRole('button', { name: 'New account' }).click(), wait: 'role=dialog' },
  { name: 'platform-set-password', path: '/platform/users', act: async (p) => { await p.getByRole('button', { name: 'More for checkraise' }).click(); await p.getByRole('menuitem', { name: 'Change password…' }).click() }, wait: 'role=dialog' },
  { name: 'platform-group', path: '/platform/features', act: (p) => p.getByRole('button', { name: 'Edit Standard' }).click(), wait: 'role=dialog' },
  { name: 'platform-bot-grants', path: '/platform/bots', act: (p) => p.getByRole('button', { name: 'Communities for ALTTPR bot' }).click(), wait: 'role=dialog' },
  { name: 'platform-disable-app', path: '/platform/apps', act: (p) => p.getByRole('button', { name: 'Disable Stream Deck' }).click(), wait: 'role=dialog' },
  { name: 'platform-ignore', path: '/platform/monitoring', act: (p) => p.getByRole('button', { name: 'Ignore an error' }).click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/me/platform": {
    "user_id": 1,
    "super_admin": true
  },
  "/api/platform/communities": [
    {
      "id": 1,
      "name": "SpeedGaming Live",
      "slug": "sgl",
      "domain": null,
      "discord_guild_id": null,
      "discord_guild_id_str": "123456789012345678",
      "is_active": true,
      "feature_group_id": 1,
      "feature_group": "Standard",
      "setup_ready": true,
      "setup_missing": [],
      "created_at": "2025-01-01T00:00:00Z"
    },
    {
      "id": 2,
      "name": "ALTTPR Leagues",
      "slug": "alttpr",
      "domain": null,
      "discord_guild_id": null,
      "discord_guild_id_str": null,
      "is_active": true,
      "feature_group_id": null,
      "feature_group": null,
      "setup_ready": true,
      "setup_missing": [],
      "created_at": "2025-01-01T00:00:00Z"
    },
    {
      "id": 3,
      "name": "NMG Weekly",
      "slug": "nmg",
      "domain": null,
      "discord_guild_id": null,
      "discord_guild_id_str": null,
      "is_active": false,
      "feature_group_id": null,
      "feature_group": null,
      "setup_ready": false,
      "setup_missing": [
        "Add a staff member",
        "Create a tournament"
      ],
      "created_at": "2025-01-01T00:00:00Z"
    }
  ],
  "/api/platform/communities/1": {
    "id": 1,
    "name": "SpeedGaming Live",
    "slug": "sgl",
    "domain": null,
    "discord_guild_id": null,
    "discord_guild_id_str": "123456789012345678",
    "is_active": true,
    "feature_group_id": 1,
    "feature_group": "Standard",
    "setup_ready": true,
    "setup_missing": [],
    "created_at": "2025-01-01T00:00:00Z",
    "setup": [],
    "staff": [
      {
        "id": 1,
        "username": "tcprescott",
        "display_name": "Thomas"
      },
      {
        "id": 5,
        "username": "checkraise",
        "display_name": null
      }
    ]
  },
  "/api/platform/communities/1/features": [
    {
      "flag": "async_qualifiers",
      "label": "Async qualifiers",
      "description": "Run qualifier races people play on their own time.",
      "category": "Online",
      "available": true,
      "enabled": true,
      "live": true,
      "group_available": true,
      "override": null
    },
    {
      "flag": "startgg",
      "label": "start.gg brackets",
      "description": "Mirror and book start.gg brackets.",
      "category": "Brackets",
      "available": true,
      "enabled": false,
      "live": false,
      "group_available": false,
      "override": true
    },
    {
      "flag": "surveys",
      "label": "Surveys",
      "description": "Post-event surveys.",
      "category": "Community",
      "available": false,
      "enabled": false,
      "live": false,
      "group_available": false,
      "override": null
    }
  ],
  "/api/platform/feature-groups": [
    {
      "id": 1,
      "name": "Standard",
      "description": "Most communities",
      "flags": [
        "async_qualifiers"
      ],
      "is_default": true,
      "community_count": 2
    },
    {
      "id": 2,
      "name": "Event",
      "description": "",
      "flags": [
        "async_qualifiers",
        "startgg",
        "surveys"
      ],
      "is_default": false,
      "community_count": 1
    }
  ],
  "/api/platform/features": [
    {
      "flag": "async_qualifiers",
      "label": "Async qualifiers",
      "description": "Run qualifier races people play on their own time.",
      "category": "Online"
    },
    {
      "flag": "startgg",
      "label": "start.gg brackets",
      "description": "Mirror and book start.gg brackets.",
      "category": "Brackets"
    },
    {
      "flag": "surveys",
      "label": "Surveys",
      "description": "Post-event surveys.",
      "category": "Community"
    }
  ],
  "/api/platform/users": [
    {
      "id": 1,
      "username": "tcprescott",
      "display_name": "Thomas",
      "discord_id": 1,
      "login_name": null,
      "is_active": true,
      "is_super_admin": true,
      "communities": [
        "SpeedGaming Live",
        "ALTTPR Leagues"
      ]
    },
    {
      "id": 5,
      "username": "checkraise",
      "display_name": null,
      "discord_id": 2,
      "login_name": "checkraise",
      "is_active": true,
      "is_super_admin": false,
      "communities": [
        "SpeedGaming Live"
      ]
    },
    {
      "id": 9,
      "username": "spammer",
      "display_name": "Spam Bot",
      "discord_id": 3,
      "login_name": null,
      "is_active": false,
      "is_super_admin": false,
      "communities": []
    }
  ],
  "/api/racetime-bots": [
    {
      "id": 1,
      "name": "ALTTPR bot",
      "category": "alttpr",
      "client_id": "abc",
      "description": "",
      "handler_class": "",
      "is_active": true,
      "status": "connected",
      "status_message": ""
    },
    {
      "id": 2,
      "name": "SMR bot",
      "category": "smr",
      "client_id": "def",
      "description": "",
      "handler_class": "",
      "is_active": true,
      "status": "error",
      "status_message": "Token rejected by racetime.gg"
    }
  ],
  "/api/racetime-bots/1/grants": [
    {
      "id": 1,
      "bot_id": 1,
      "tenant_id": 1,
      "created_at": "2025-01-01T00:00:00Z"
    }
  ],
  "/api/platform/apps": [
    {
      "id": 1,
      "name": "Wizzrobe",
      "owner": "Thomas",
      "kind": "device",
      "first_party": true,
      "people": 120,
      "communities": 3,
      "disabled": false,
      "disabled_reason": null,
      "disabled_at": null
    },
    {
      "id": 2,
      "name": "Seed Helper",
      "owner": "checkraise",
      "kind": "website",
      "first_party": false,
      "people": 4,
      "communities": 1,
      "disabled": true,
      "disabled_reason": "Phishing",
      "disabled_at": "2025-06-01T00:00:00Z"
    },
    {
      "id": 3,
      "name": "Stream Deck",
      "owner": "checkraise",
      "kind": "device",
      "first_party": false,
      "people": 2,
      "communities": 1,
      "disabled": false,
      "disabled_reason": null,
      "disabled_at": null
    }
  ],
  "/api/platform/log-levels": [
    {
      "logger": "",
      "description": "Everything not set below",
      "effective": "INFO",
      "override": null,
      "updated_by": null,
      "updated_at": null
    },
    {
      "logger": "discordbot",
      "description": "Discord bot and DMs",
      "effective": "DEBUG",
      "override": "DEBUG",
      "updated_by": "tcprescott",
      "updated_at": "2025-06-01T00:00:00Z"
    }
  ],
  "/api/platform/browser-monitoring": {
    "enabled": true,
    "replay_on_error": true,
    "sample_rate": 1.0,
    "sample_rates": [
      1.0,
      0.5,
      0.25,
      0.1
    ],
    "updated_by": "tcprescott",
    "updated_at": "2025-06-01T00:00:00Z"
  },
  "/api/platform/browser-monitoring/rules": [
    {
      "id": 4,
      "pattern": "ResizeObserver loop",
      "match": "message",
      "builtin": false,
      "created_by": "tcprescott",
      "created_at": "2025-06-01T00:00:00Z"
    },
    {
      "id": null,
      "pattern": "chrome-extension://",
      "match": "url",
      "builtin": true,
      "created_by": null,
      "created_at": null
    }
  ],
  "/api/service-health/board": [
    {
      "key": "discord",
      "label": "Discord bot",
      "category": "discord",
      "status": "healthy",
      "message": "Connected to 3 servers.",
      "checked_at": "{{now-2m}}"
    },
    {
      "key": "matcherino",
      "label": "Matcherino",
      "category": "integrations",
      "status": "down",
      "message": "Token rejected 12 minutes ago.",
      "checked_at": "{{now-2m}}"
    }
  ]
}

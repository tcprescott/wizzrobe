/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-settings-general', path: '/t/sgl/admin/settings/general' },
  { name: 'admin-settings-members', path: '/t/sgl/admin/settings/members' },
  { name: 'admin-settings-watch', path: '/t/sgl/admin/settings/watch' },
  { name: 'admin-settings-volunteers', path: '/t/sgl/admin/settings/volunteers' },
  { name: 'admin-settings-meetups', path: '/t/sgl/admin/settings/community-events' },
  { name: 'admin-settings-apps', path: '/t/sgl/admin/settings/apps' },
  { name: 'admin-settings-features', path: '/t/sgl/admin/settings/features' },
  {
    name: 'admin-settings-unsaved',
    path: '/t/sgl/admin/settings/volunteers',
    act: (p) => p.getByLabel('Reminder lead (minutes)').fill('30'),
    wait: 'text=Unsaved changes',
  },
]

export const fixtures = {
  "/api/settings/appearance": {
    "colours": {
      "primary": "#9c6b12",
      "secondary": "#c24e12",
      "accent": "#ffff00",
      "header": "#9c6b12"
    },
    "defaults": {
      "primary": "#9c6b12",
      "secondary": "#c24e12",
      "accent": "#e0a82e",
      "header": "#9c6b12"
    },
    "presets": [
      {
        "name": "Phoenix (default)",
        "colours": {
          "primary": "#9c6b12",
          "secondary": "#c24e12",
          "accent": "#e0a82e",
          "header": "#9c6b12"
        }
      },
      {
        "name": "Ocean",
        "colours": {
          "primary": "#1d5e8c",
          "secondary": "#0e7470",
          "accent": "#5fb3e6",
          "header": "#1d5e8c"
        }
      }
    ],
    "issues": {
      "accent": [
        "Links and highlights in light mode would be 1.1:1, under the 4.5:1 AA minimum."
      ]
    },
    "logo_url": null
  },
  "/api/settings/timezone": {
    "mode": "pinned",
    "name": "America/New_York",
    "options": [
      "America/New_York",
      "America/Chicago",
      "America/Los_Angeles",
      "Europe/London"
    ]
  },
  "/api/settings/joining": {
    "join_mode": "request",
    "guild_linked": true,
    "discord_invite_url": "https://discord.gg/sglive",
    "modes": [
      {
        "value": "open",
        "label": "Anyone who signs in",
        "help": "Signing in with Discord makes someone a member on the spot."
      },
      {
        "value": "discord_or_request",
        "label": "Discord server members, and others can ask",
        "help": "Members of the linked Discord server get in the first time they open the community."
      },
      {
        "value": "discord",
        "label": "Discord server members only",
        "help": "Members of the linked Discord server get in. Nobody else can ask."
      },
      {
        "value": "request",
        "label": "Everyone asks, and staff approve",
        "help": "People ask from the join page and Staff approve or decline them on Members."
      },
      {
        "value": "staff_only",
        "label": "Only people staff add",
        "help": "The join page takes no requests. Staff add people from Members."
      }
    ]
  },
  "/api/settings/profile-reminders": {
    "max_note_length": 200,
    "reminders": [
      {
        "check": "challonge",
        "label": "Challonge account linked",
        "prompt": "Link your Challonge account so your bracket matches show up here and you can schedule them.",
        "enabled": true,
        "dismissible": false,
        "note": "Required for the brackets.",
        "unavailable": null
      },
      {
        "check": "twitch",
        "label": "Twitch account linked",
        "prompt": "Link your Twitch account so we know which channel is yours.",
        "enabled": false,
        "dismissible": true,
        "note": "",
        "unavailable": null
      },
      {
        "check": "racetime",
        "label": "racetime.gg account linked",
        "prompt": "Link your racetime.gg account so your race results are credited to you.",
        "enabled": false,
        "dismissible": true,
        "note": "",
        "unavailable": "Racetime isn't set up for this community."
      }
    ]
  },
  "/api/settings/watch": {
    "mode": "live",
    "static_signed_out": false,
    "auto_static": true,
    "threshold": 300,
    "audience": {
      "live_viewers": 142,
      "static_readers": 38,
      "arrivals_last_minute": 12,
      "admissions_per_minute": 600,
      "peak": 417,
      "peak_at": "{{now-90m}}",
      "tripped": true,
      "tripped_at": "{{now-3m}}"
    }
  },
  "/api/settings/volunteers": {
    "reminder_lead_minutes": 45
  },
  "/api/settings/meetups": {
    "require_approval": true,
    "reminder_lead_minutes": null
  },
  "/api/settings/features": {
    "tier": "Event",
    "features": [
      {
        "flag": "volunteers",
        "label": "Volunteers",
        "description": "Volunteer positions, shifts and the shift board.",
        "category": "People",
        "available": true,
        "enabled": true
      },
      {
        "flag": "check_in",
        "label": "Event check-in",
        "description": "Matcherino roster sync and the check-in desk.",
        "category": "People",
        "available": true,
        "enabled": false
      },
      {
        "flag": "startgg",
        "label": "start.gg",
        "description": "Mirror start.gg brackets and book their matches.",
        "category": "Brackets",
        "available": false,
        "enabled": false
      }
    ]
  },
  "/api/settings/apps": [
    {
      "app_id": 1,
      "name": "Bracket Bot",
      "owner_name": "Ava",
      "homepage_host": "bracketbot.example",
      "members": 12,
      "blocked": false,
      "disabled": false
    },
    {
      "app_id": 2,
      "name": "Stream Deck Helper",
      "owner_name": "Kai",
      "homepage_host": null,
      "members": 1,
      "blocked": true,
      "disabled": false
    }
  ]
}

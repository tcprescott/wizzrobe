/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin', path: '/t/sgl/admin' },
  { name: 'admin-setup', path: '/t/sgl/admin/setup' },
  { name: 'admin-not-built', path: '/t/sgl/admin/service-health' },
]

export const fixtures = {
  "/api/admin/nav": {
    "areas": [
      {
        "slug": "setup",
        "label": "Setup",
        "icon": "checklist",
        "group": "Run the event",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "match-schedule",
        "label": "Match Schedule",
        "icon": "view_timeline",
        "group": "Run the event",
        "half": "event",
        "aliases": [
          "schedule"
        ]
      },
      {
        "slug": "tournaments",
        "label": "Tournaments",
        "icon": "trophy",
        "group": "Run the event",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "brackets",
        "label": "Brackets",
        "icon": "schema",
        "group": "Run the event",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "qualifiers",
        "label": "Qualifiers",
        "icon": "timer",
        "group": "Run the event",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "stages",
        "label": "Stages",
        "icon": "tv",
        "group": "Run the event",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "triforce-texts",
        "label": "Triforce Texts",
        "icon": "change_history",
        "group": "Run the event",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "check-in-roster",
        "label": "Check-in Roster",
        "icon": "fact_check",
        "group": "Run the event",
        "half": "event",
        "aliases": [
          "check-in"
        ]
      },
      {
        "slug": "event-settings",
        "label": "Event settings",
        "icon": "event",
        "group": "Run the event",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "volunteers",
        "label": "Volunteers",
        "icon": "groups",
        "group": "People",
        "half": "event",
        "aliases": [
          "vol-roster"
        ]
      },
      {
        "slug": "meetups",
        "label": "Meetups",
        "icon": "celebration",
        "group": "People",
        "half": "event",
        "aliases": [
          "community-events"
        ]
      },
      {
        "slug": "members",
        "label": "Members",
        "icon": "manage_accounts",
        "group": "Community",
        "half": "community",
        "aliases": [
          "users"
        ]
      },
      {
        "slug": "equipment",
        "label": "Equipment",
        "icon": "inventory_2",
        "group": "Community",
        "half": "community",
        "aliases": []
      },
      {
        "slug": "events",
        "label": "Events",
        "icon": "calendar_month",
        "group": "Configuration",
        "half": "community",
        "aliases": []
      },
      {
        "slug": "settings",
        "label": "Settings",
        "icon": "settings",
        "group": "Configuration",
        "half": "community",
        "aliases": []
      },
      {
        "slug": "service-health",
        "label": "Service Health",
        "icon": "monitor_heart",
        "group": "System",
        "half": "community",
        "aliases": []
      },
      {
        "slug": "speedgaming",
        "label": "SpeedGaming",
        "icon": "sync_alt",
        "group": "Online play",
        "half": "event",
        "aliases": []
      },
      {
        "slug": "racetime",
        "label": "Racetime",
        "icon": "flag",
        "group": "Community",
        "half": "community",
        "aliases": []
      },
      {
        "slug": "seed-presets",
        "label": "Seed Presets",
        "icon": "tune",
        "group": "Configuration",
        "half": "community",
        "aliases": [
          "presets"
        ]
      }
    ],
    "settings_panes": [
      {
        "slug": "general",
        "label": "General",
        "icon": "tune",
        "summary": "How the community looks, and the clock it tells time by."
      },
      {
        "slug": "members",
        "label": "Members",
        "icon": "group_add",
        "summary": "Who can join, what everyone else sees, and what members are asked to finish."
      },
      {
        "slug": "watch",
        "label": "Watch page",
        "icon": "live_tv",
        "summary": "Who is watching at /live, and whether it runs live or static."
      },
      {
        "slug": "volunteers",
        "label": "Volunteers",
        "icon": "volunteer_activism",
        "summary": "When volunteers hear about their next shift."
      },
      {
        "slug": "community-events",
        "label": "Meetups",
        "icon": "groups",
        "summary": "Whether staff approve meetups first, and how early subscribers get a reminder."
      },
      {
        "slug": "discord",
        "label": "Discord",
        "icon": "forum",
        "summary": "The linked server, which of its roles mean what here, and the events mirrored into it."
      },
      {
        "slug": "integrations",
        "label": "Integrations",
        "icon": "extension",
        "summary": "Other services this community sends to or signs in to."
      },
      {
        "slug": "apps",
        "label": "Connected apps",
        "icon": "apps",
        "summary": "Outside apps members have connected here, and blocking one."
      },
      {
        "slug": "features",
        "label": "Features",
        "icon": "toggle_on",
        "summary": "Turn parts of the app on or off for this community."
      }
    ],
    "event_settings_panes": [
      {
        "slug": "dates",
        "label": "Dates & hours",
        "icon": "date_range",
        "summary": "When the event runs, and the hours matches can start each day."
      }
    ],
    "setup": [
      {
        "key": "staff",
        "label": "Give the community a staff member",
        "done": true,
        "hint": "",
        "tab": "Members",
        "required": true
      },
      {
        "key": "tournament",
        "label": "Add a tournament",
        "done": false,
        "hint": "Players need something to sign up for.",
        "tab": "Tournaments",
        "required": true
      },
      {
        "key": "dates",
        "label": "Set the event dates",
        "done": false,
        "hint": "Schedules and reminders use them.",
        "tab": "Event settings",
        "required": false
      }
    ],
    "setup_ready": false
  }
}

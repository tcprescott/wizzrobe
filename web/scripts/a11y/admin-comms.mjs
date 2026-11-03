/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'admin-notifications', path: '/t/sgl/admin/notifications' },
  { name: 'admin-notifications-sheet', path: '/t/sgl/admin/notifications', act: (p) => p.getByRole('button', { name: /^Player comment on a match/ }).click(), wait: 'role=radiogroup' },
  { name: 'admin-feedback', path: '/t/sgl/admin/feedback' },
  { name: 'admin-feedback-open', path: '/t/sgl/admin/feedback/30' },
  { name: 'admin-feedback-menu', path: '/t/sgl/admin/feedback/31', act: (p) => p.getByRole('button', { name: 'More for feedback from Kayla R.' }).click(), wait: 'role=menu' },
  { name: 'admin-service-health', path: '/t/sgl/admin/service-health' },
]

export const fixtures = {
  "/api/admin/notifications": {
    "cards": [
      {
        "group": "Matches",
        "section": "Booking",
        "columns": [
          "players",
          "crew",
          "watchers",
          "followers"
        ],
        "messages": [
          {
            "notice": "match_scheduled",
            "title": "Match scheduled or rescheduled",
            "when": "A match is booked, its time changes, or players are added to it",
            "changed": true,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "Players who haven't acknowledged yet (with Acknowledge)",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "off",
                "default": "silent",
                "changed": true,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "followers",
                "label": "Followers",
                "who": "People following the tournament, at their follow level",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "opponent_acknowledged",
            "title": "Opponent acknowledged",
            "when": "Another player in your match acknowledges its time",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "Only people who turned it on in their Profile",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "stream_candidate",
            "title": "Stream candidate",
            "when": "A match without a stage is flagged as a possible stream",
            "changed": false,
            "cells": [
              {
                "column": "followers",
                "label": "Followers",
                "who": "Followers on \"Streamed, plus possible streams\"",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "match_cancelled",
            "title": "Match cancelled",
            "when": "Staff cancel the match (deleting one sends nothing)",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "match_cancelled_on_request",
            "title": "Match cancelled on request",
            "when": "Staff approve a player's cancel request",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "series_game_cancelled",
            "title": "Leftover series game cancelled",
            "when": "A best-of series is decided early and its booked games are called off",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "removed_from_match",
            "title": "Removed from a match",
            "when": "Staff take a player off a match",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          }
        ]
      },
      {
        "group": "Matches",
        "section": "Match day",
        "columns": [
          "players",
          "crew",
          "watchers"
        ],
        "messages": [
          {
            "notice": "match_checked_in",
            "title": "Match checked in",
            "when": "A proctor or staff member checks the match in",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "opponent_seed_ready",
            "title": "Opponent ready for the seed",
            "when": "Another player in your checked-in match taps Ready for seed, on a tournament where players release their own seed",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "Players who haven't tapped Ready yet (with Ready for seed)",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "seed_ready",
            "title": "Seed ready",
            "when": "The match's seed is rolled",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "station_assigned",
            "title": "Station assigned",
            "when": "Staff set or change a match's stations",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "Only people who turned it on in their Profile",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "race_room_open",
            "title": "Race room open",
            "when": "The match's racetime.gg room opens",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "stage_call",
            "title": "On stage",
            "when": "A match's stage is set or changed",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "stage_cleared",
            "title": "Stage call cancelled",
            "when": "A match's stage is cleared, so it's back in the tournament room",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "stage_reminder",
            "title": "Stage match coming up",
            "when": "Before a stage match, at the tournament's reminder lead",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "match_started",
            "title": "Match started",
            "when": "The match is started",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "off",
                "default": "off",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "match_finished",
            "title": "Match finished",
            "when": "The match is finished",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "off",
                "default": "off",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          }
        ]
      },
      {
        "group": "Matches",
        "section": "Results",
        "columns": [
          "players",
          "crew",
          "watchers",
          "followers",
          "staff_alerts"
        ],
        "messages": [
          {
            "notice": "match_confirmed",
            "title": "Result confirmed",
            "when": "Staff confirm the result",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "match_forfeited",
            "title": "Match forfeited",
            "when": "Staff drop a player and their opponent wins the match by forfeit",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "crew",
                "label": "Crew",
                "who": "",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "watchers",
                "label": "Watchers",
                "who": "",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "match_result",
            "title": "Match result",
            "when": "Staff confirm the result, with who placed where",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "Every player in the match, only if they turned it on in their Profile",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "followers",
                "label": "Followers",
                "who": "People following the tournament, at their follow level, only if they turned it on in their Profile",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "result_in",
            "title": "Result in",
            "when": "A result is recorded, comes from the race room, or is reported on Challonge or start.gg",
            "changed": false,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "Admins: The tournament's admins, unless they turned its alerts off",
                "mode": "silent",
                "default": "silent",
                "changed": false,
                "staff_choice": "silent",
                "staff_label": "Silent",
                "staff_default": "silent",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping",
                    "help": "The tournament's admins, unless they turned its alerts off"
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "Admins: Silent"
              }
            ]
          }
        ]
      },
      {
        "group": "Matches",
        "section": "Comments and staff alerts",
        "columns": [
          "players",
          "crew",
          "staff_alerts"
        ],
        "messages": [
          {
            "notice": "staff_comment",
            "title": "Staff comment on a match",
            "when": "Staff post a \"Message players\" comment",
            "changed": false,
            "cells": [
              {
                "column": "players",
                "label": "Players",
                "who": "Players with \"Staff comments on my matches\" on",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              }
            ]
          },
          {
            "notice": "player_comment",
            "title": "Player comment on a match",
            "when": "A player comments on their match",
            "changed": true,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Whoever's on a shift with staff alerts on (the Admin Desk) and can act on it; Admins: The tournament's admins, unless they turned its alerts off; All staff: Every staff member, only when nobody is on shift",
                "mode": "ping",
                "default": "ping",
                "changed": true,
                "staff_choice": "ping_on_duty",
                "staff_label": "Ping on-duty",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · Admins: Off · All staff: Ping"
              }
            ]
          },
          {
            "notice": "player_late",
            "title": "Player running late",
            "when": "A player taps Tell staff I'm late on their match",
            "changed": false,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Whoever's on a shift with staff alerts on (the Admin Desk) and can act on it; Admins: The tournament's admins, unless they turned its alerts off; All staff: Every staff member, only when nobody is on shift",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": "ping_all",
                "staff_label": "Ping all",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · Admins: Ping · All staff: Ping"
              }
            ]
          },
          {
            "notice": "player_late_on_stage",
            "title": "Player running late (Broadcast Techs)",
            "when": "A player taps Tell staff I'm late on a match that has a stage",
            "changed": false,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Broadcast Techs holding a volunteer shift right now; All staff: Every Broadcast Tech, only when none is on shift",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": "ping_all",
                "staff_label": "Ping all",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · All staff: Ping"
              }
            ]
          },
          {
            "notice": "racetime_fpa",
            "title": "Fair play pause called",
            "when": "A runner types !fpa in a racetime room the bot runs",
            "changed": false,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Whoever's on a shift with staff alerts on (the Admin Desk) and can act on it; Admins: The tournament's admins, unless they turned its alerts off; All staff: Every staff member, only when nobody is on shift",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": "ping_all",
                "staff_label": "Ping all",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · Admins: Ping · All staff: Ping"
              }
            ]
          },
          {
            "notice": "racetime_broadcaster_waiting",
            "title": "Seed waiting for the broadcaster",
            "when": "A restreamed match reaches its start time and the broadcaster hasn't typed !ready",
            "changed": false,
            "cells": [
              {
                "column": "crew",
                "label": "Crew",
                "who": "The match's approved broadcasters",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": null,
                "staff_label": "",
                "staff_default": null,
                "staff_options": [],
                "staff_mix": ""
              },
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Whoever's on a shift with staff alerts on (the Admin Desk) and can act on it; Admins: The tournament's admins, unless they turned its alerts off; All staff: Every staff member, only when nobody is on shift",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": "ping_all",
                "staff_label": "Ping all",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · Admins: Ping · All staff: Ping"
              }
            ]
          },
          {
            "notice": "match_flagged",
            "title": "Match flagged for review",
            "when": "Someone flags a match for staff review",
            "changed": false,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Whoever's on a shift with staff alerts on (the Admin Desk) and can act on it, only if they turned it on in their Profile; Admins: The tournament's admins, unless they turned its alerts off, only if they turned it on in their Profile; All staff: Every staff member, only when nobody is on shift, only if they turned it on in their Profile",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": "ping_all",
                "staff_label": "Ping all",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · Admins: Ping · All staff: Ping"
              }
            ]
          },
          {
            "notice": "stream_offer",
            "title": "Match offered for stream",
            "when": "Crew offer to stream a match",
            "changed": false,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Whoever's on a shift with staff alerts on and can schedule streams, only if they turned it on in their Profile; Admins: The tournament's admins and Broadcast Techs, only if they turned it on in their Profile; All staff: Every staff member, only when nobody is on shift, only if they turned it on in their Profile",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": "ping_all",
                "staff_label": "Ping all",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · Admins: Ping · All staff: Ping"
              }
            ]
          },
          {
            "notice": "seed_failed",
            "title": "Seed didn't roll",
            "when": "Rolling a seed in the background fails",
            "changed": false,
            "cells": [
              {
                "column": "staff_alerts",
                "label": "Staff",
                "who": "On shift: Whoever's on a shift with staff alerts on (the Admin Desk) and can act on it, only if they turned it on in their Profile; Admins: The tournament's admins, unless they turned its alerts off, only if they turned it on in their Profile; All staff: Every staff member, only when nobody is on shift, only if they turned it on in their Profile",
                "mode": "ping",
                "default": "ping",
                "changed": false,
                "staff_choice": "ping_all",
                "staff_label": "Ping all",
                "staff_default": "ping_all",
                "staff_options": [
                  {
                    "choice": "ping_all",
                    "label": "Ping all",
                    "help": "Whoever's on shift, plus the tournament's own admins or coordinators. All staff if nobody's on shift."
                  },
                  {
                    "choice": "ping_on_duty",
                    "label": "Ping on-duty",
                    "help": "Only whoever's on shift. All staff if nobody's on shift."
                  },
                  {
                    "choice": "silent",
                    "label": "Silent",
                    "help": "The same people, without a sound or buzz."
                  },
                  {
                    "choice": "off",
                    "label": "Off",
                    "help": "Sends nothing."
                  }
                ],
                "staff_mix": "On shift: Ping · Admins: Ping · All staff: Ping"
              }
            ]
          }
        ]
      }
    ],
    "changed": 2
  },
  "/api/feedback": [
    {
      "id": 31,
      "category": "bug",
      "status": "new",
      "message": "The Pick a time button on my schedule did nothing on my phone the first time I tapped it. Second tap worked.",
      "page_url": "/t/sgl/home/player",
      "submitted_by_id": 12,
      "submitted_by_name": "Kayla R.",
      "created_at": "{{now-40m}}"
    },
    {
      "id": 30,
      "category": "suggestion",
      "status": "new",
      "message": "Could the run sheet show which stage is up next? We kept asking the desk.",
      "page_url": "/t/sgl/volunteer/proctor",
      "submitted_by_id": 13,
      "submitted_by_name": "Marcus T.",
      "created_at": "{{now-200m}}"
    },
    {
      "id": 29,
      "category": "praise",
      "status": "new",
      "message": "Check-in at the door took ten seconds. Great job.",
      "page_url": "",
      "submitted_by_id": 14,
      "submitted_by_name": "Ash",
      "created_at": "{{now-900m}}"
    },
    {
      "id": 28,
      "category": "other",
      "status": "reviewed",
      "message": "Is there a way to change my display name after signing up?",
      "page_url": "https://evil.example/x",
      "submitted_by_id": 15,
      "submitted_by_name": "Devon P.",
      "created_at": "{{now-1500m}}"
    }
  ],
  "/api/service-health": [
    {
      "key": "racetime_bots",
      "label": "racetime bot: smz3",
      "category": "racetime",
      "status": "down",
      "message": "Can't reach racetime.gg (timed out). Platform admins have been told.",
      "checked_at": "{{now-4m}}"
    },
    {
      "key": "challonge",
      "label": "Challonge",
      "category": "integrations",
      "status": "credential_warning",
      "message": "Token expires in 3 days. Reconnect before it stops syncing.",
      "checked_at": "{{now-4m}}"
    },
    {
      "key": "racetime_bots",
      "label": "racetime bot: alttpr",
      "category": "racetime",
      "status": "healthy",
      "message": "Connected, 3 rooms open.",
      "checked_at": "{{now-4m}}"
    }
  ]
}

/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'event-info', path: '/t/sgl/event-info' },
  { name: 'event-info-article', path: '/t/sgl/event-info/schedule' },
  { name: 'event-guide', path: '/t/sgl/event-info/guide', wait: '.guide-page', act: async () => {} },
]

export const fixtures = {
  "/api/event-info": {
    "articles": [
      {
        "slug": "schedule",
        "title": "What's on, and when",
        "summary": "Dates, room hours, the on-site tournaments, prize pools and your own match times.",
        "icon": "event",
        "for_staff": false
      },
      {
        "slug": "attending",
        "title": "Attending the event",
        "summary": "The venue, check-in and badges, what to bring, playing in the tournament room or on stage, food and medical.",
        "icon": "how_to_reg",
        "for_staff": false
      },
      {
        "slug": "community-events",
        "title": "Community events",
        "summary": "The side tournaments, races, card and board games and meetups attendees run, how to join one and how to post your own.",
        "icon": "groups",
        "for_staff": false
      },
      {
        "slug": "who-to-ask",
        "title": "Who to ask",
        "summary": "Who makes which call, staff, volunteering and crew, and lost property.",
        "icon": "contact_support",
        "for_staff": false
      }
    ],
    "guide": {
      "filename": "SGLive-2026-Event-Guide.pdf",
      "size_bytes": 4400000,
      "size_label": "4.2 MB"
    }
  },
  "/api/event-info/schedule": {
    "slug": "schedule",
    "title": "What's on, and when",
    "summary": "Dates, room hours, the on-site tournaments, prize pools and your own match times.",
    "icon": "event",
    "for_staff": false,
    "headings": [
      {
        "anchor": "hours",
        "text": "Hours"
      },
      {
        "anchor": "on-site-tournaments",
        "text": "On-site tournaments"
      },
      {
        "anchor": "prize-pools",
        "text": "Prize pools"
      },
      {
        "anchor": "your-own-times",
        "text": "Your own times"
      },
      {
        "anchor": "watching",
        "text": "Watching"
      },
      {
        "anchor": "online-tournaments",
        "text": "Online tournaments"
      }
    ],
    "blocks": [
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "SpeedGaming Live 2026 runs Thursday 22 to Sunday 25 October at the DoubleTree by Hilton Washington Dulles Airport in Herndon, Virginia. All times here are Eastern Daylight Time (EDT, UTC−4), the venue's local time.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "Hours",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "hours",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Doors open Thursday 22 October at 8 AM. Wednesday is for getting in and picking up your badge.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "table",
        "spans": [],
        "items": [],
        "headers": [
          [
            {
              "kind": "text",
              "text": "Room",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "text",
              "text": "Where",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "text",
              "text": "Open",
              "href": "",
              "tone": ""
            }
          ]
        ],
        "rows": [
          [
            [
              {
                "kind": "text",
                "text": "Tournament room",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "Sully, 1st floor, near check-in",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "8 AM to midnight Thursday to Saturday, 8 AM to 6 PM Sunday",
                "href": "",
                "tone": ""
              }
            ]
          ],
          [
            [
              {
                "kind": "text",
                "text": "Broadcast room (the stages)",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "Piedmont I + II, 2nd floor, above the fitness center",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "9 AM to midnight daily",
                "href": "",
                "tone": ""
              }
            ]
          ],
          [
            [
              {
                "kind": "text",
                "text": "Casual room",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "Corcoran / Veritas / Chrysalis, lower level",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "7 AM to midnight daily",
                "href": "",
                "tone": ""
              }
            ]
          ],
          [
            [
              {
                "kind": "text",
                "text": "Board games",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "Tarara, basement",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "11 AM to around midnight daily",
                "href": "",
                "tone": ""
              }
            ]
          ],
          [
            [
              {
                "kind": "text",
                "text": "Check-in desk",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "1st floor, outside the fitness center",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "4 to 10 PM Wednesday, 10 AM to 8 PM Thursday and Friday, 10 AM to 4 PM Saturday",
                "href": "",
                "tone": ""
              }
            ]
          ],
          [
            [
              {
                "kind": "text",
                "text": "Breakfast",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "Old Ox on Wednesday, Colvin Thursday to Monday",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "6:30 to around 10 AM daily",
                "href": "",
                "tone": ""
              }
            ]
          ]
        ],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Pick a match time that lets you finish before the room closes, and remember Sunday wraps up at 6 PM. Suggest a time in the app already sticks to these hours. If you need to play after close, that has to be cleared with SGL staff ahead of time. Tournament admins can't approve it.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "The casual room is for pick-up races, side events and practice, and one of its rooms hosts the Best of NES High Score Challenge. The tournament room is only for scheduled matches.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "On-site tournaments",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "on-site-tournaments",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Each tournament's rules have its settings. You'll see a Sign up button while a tournament is still taking entries.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "embed",
        "spans": [],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": "tournaments"
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "A few have something extra first:",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "bullets",
        "spans": [],
        "items": [
          [
            {
              "kind": "text",
              "text": "A Link to the Past Randomizer Glitched (HMG) has an async qualifier, open from Friday 16 October until noon on Thursday 22 October.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "text",
              "text": "A Link to the Past any% runs qualifier races on site. The times are in its rules.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "text",
              "text": "For the Best of NES High Score Challenge, check in at the Best of NES room.",
              "href": "",
              "tone": ""
            }
          ]
        ],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Most tournaments play groups on Thursday and Friday, then brackets on Saturday and Sunday. You and your opponent schedule group matches yourselves once groups are out. Bracket matches are scheduled for you, and you'll get a DM when yours is ready.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "Prize pools",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "prize-pools",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Prize pools come from community contributions plus a bonus from SpeedGaming, which doesn't take a cut. To chip in, ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "contribute on Matcherino",
            "href": "https://matcherino.com/t/sglive26?contribute=true",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " and put the game's name in the comment, plus whether it's for on-site or online.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "Your own times",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "your-own-times",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "My Schedule shows your matches and Schedule shows everyone's, both in your own time zone. You'll also get a Discord DM whenever one of your matches is scheduled, moved or cancelled.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "If a time doesn't work for you, use Ask to change on My Schedule, and ask early. Once a match has a stage and crew, it's a lot harder to move. Until staff reply, plan on the original time. If it's the day of the match, skip the request and find an admin in the tournament room.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "Watching",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "watching",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "The four stages are in the broadcast room, with seats for the audience. They stream on ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "twitch.tv/SpeedGaming",
            "href": "https://twitch.tv/SpeedGaming",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " and ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "twitch.tv/SpeedGaming2",
            "href": "https://twitch.tv/SpeedGaming2",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ", and Watch in the side menu shows what's on each one. The tournament room isn't streamed and isn't open to spectators.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "Online tournaments",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "online-tournaments",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "The online side of SGLive is free to enter and runs on its own dates. You'll find the list on ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "sglive.speedgaming.org",
            "href": "https://sglive.speedgaming.org",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ".",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "",
        "alt": "",
        "name": ""
      }
    ],
    "tournaments": [
      {
        "id": 1,
        "name": "ALTTPR Open",
        "format": "Double elimination",
        "rules_url": "https://example.org/rules",
        "bracket_url": "/live/tournament/1/brackets",
        "signups": "open",
        "signups_close_at": "2026-10-21T23:00:00Z",
        "prize_total": "1500",
        "prize_synced_at": null
      },
      {
        "id": 2,
        "name": "Super Metroid",
        "format": "Swiss",
        "rules_url": "",
        "bracket_url": "",
        "signups": "closed",
        "signups_close_at": null,
        "prize_total": null,
        "prize_synced_at": null
      }
    ],
    "venue_timezone": "America/Denver"
  }
}

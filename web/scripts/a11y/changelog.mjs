/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'changelog', path: '/t/sgl/changelog' },
]

export const fixtures = {
  "/api/changelog": {
    "weeks": [
      {
        "start": "2026-10-26",
        "end": "2026-11-01",
        "title": "Updates",
        "period": "Oct 26 – Nov 1, 2026",
        "anchor": "2026-10-26",
        "sections": [
          {
            "category": "New",
            "count": 41,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "An archived event is read-only: its pages say so under the title, its add buttons and ⋮ menus are gone, and anything that tries to change it is turned away. Staff can unarchive it from the same notice.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Archiving an event closes its open race rooms, drops seed rolls still waiting, and takes back the Discord roles its rules gave.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Emergency contacts and accommodation requests for an archived event are deleted 30 days after it was archived.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Matches can have broadcasters as well as commentators and trackers. Staff turn them on per event under Event settings → Crew; anyone in the community can then sign up from the schedule or the crew DM, and staff approve them the same way. Each tournament sets how many it needs (1 by default, 0 for none), and the boards, match editor and crew report gain a Broadcasters column.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Commentators and trackers can sign up for bracket matches nobody has booked yet, like a final whose players aren't decided. They're under ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "strong",
                      "text": "Not scheduled yet",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " at the bottom of Event › Schedule. When the match is booked the signups move onto it, approvals included, and staff hear if an approved person is busy at the booked time.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The REST API and the Wizzrobe MCP connector work in any event: pass ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "event",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " with its link name (",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "?event=sgl27",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": ", or an ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "event",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " argument on a tool), or leave it out for the default event. A call about one match or shift works in that row's own event. New ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "/api/events",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " routes and event tools list, create, copy, go live, archive and configure events, and ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "whoami",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " lists each community's events.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The audit log, sent messages and engagement telemetry can be narrowed to one event over the API and the connector.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Webhooks can be limited to some events: the webhook dialog's new From events choice is All events or Only these events, and each webhook's row names its events under the URL. The old Events field is now called Updates. Existing webhooks keep sending every event's updates, except SG Schedule Update, which sends SGL 2026's only.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A new staff help article, \"Running more than one event\", covers starting next year's event, going live, the default event and archiving. Its help icon is on Admin → Events.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A community can now run several events. Staff create, copy, take live, archive and set the default one under Admin → Events, and the event button beside the community name switches between them.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Each event has its own dates, hours, capacity, venue, timezone and volunteer reward tiers under Admin → Event settings.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "My Schedule, My Shifts and Home › Tournaments show every live event you're in, with the event's name on a row once two events share the list, and links back to past events.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "AI assistants connected to Wizzrobe can read a tournament's whole bracket in one go, for native, start.gg and Challonge brackets: every round and set, who feeds into what, scores, booked matches and standings. It's the same view anyone can already see on the bracket page.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A Link to the Past matches have an item tracker for restreams. The match's approved trackers, staff and broadcast techs open it from the match sheet, tap items and dungeons as the runner finds them, and see what viewers see after the stream delay. Each runner gets a transparent board to add to OBS as a browser source. Communities turn it on under Admin → Features.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Commentators can bring a partner. Sign up on a match that needs two commentators, tap ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "strong",
                      "text": "Invite a partner",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": ", and the person you pick gets Accept and Decline on Discord and on My Schedule. Staff still approve each of you on your own.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Members can set up their own webhooks under Profile → Webhooks (when the community turns it on): up to three, sending their matches, crew signups, shifts and qualifier runs, or anything else they can see, to a Discord channel or their own app. Each message is checked against what they can see when it's sent, and staff-only details never go out.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "On a restreamed match in a room the racetime bot runs, the seed now waits for the broadcaster. Once the match's approved broadcaster types ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "!ready",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " in the room, the seed goes out (at its usual time, or straight away if that's passed). If nobody has typed ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "!ready",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " by the start time, the bot says so in the room and DMs the broadcaster and on-duty staff, and staff can still send the seed with ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "!forceroll",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": ". This only applies on events with Broadcasters turned on.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Online tournaments can let the racetime bot run their rooms: tick ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "strong",
                      "text": "Bot runs the room",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " under Racetime on the tournament. The bot invites the players, posts the seed and its code 10 minutes before the start (you pick the minutes), and answers ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "!seed",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": ", ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "!forceroll",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " (monitors and staff) and ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "!fpa",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " (runners, which tells on-duty staff). Clean results record and go to the bracket on their own; a close finish, a forfeit, an FPA call or a runner it can't match waits for staff under ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "strong",
                      "text": "Racetime results held",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " in Needs you, with a Check result button. Players without racetime linked get a Link racetime button when their room opens, and staff see \"not linked\" on the match.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Players can release their own seed. Turn on \"Players release their own seed\" under a tournament's Seeds & randomizer settings, and once a proctor checks a match in, each player taps Ready for seed on My Schedule or on their check-in DM. The seed rolls and goes to everyone the moment the last player is ready, and anyone can take Ready back until then. Proctors and staff can still roll it whenever they like, and their boards show \"1 of 2 ready\" on the seed line.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Tournament admins now get a Discord DM the moment a match result comes in, with who won, the time or score, when it was played and where it was reported, including results reported straight on Challonge or start.gg. They arrive silently; change them under Notifications (\"Result in\").",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Stream layouts: staff and Broadcast Techs can design each event's broadcast overlay under Admin → Stream Layouts (start from a stock layout on a plain colour, with the same box positions as the SGL SpeedGaming and FFR layouts, or upload your own art, then place player names, round, commentators, seed code, up next and more on it). Each stage gets an OBS link that follows the match on that stage, and you can download ready-made OBS scenes with every video hole wired to a shared feed, and a match can be pinned from its match sheet. Communities turn it on under Admin → Features once it's made available.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Outside apps can now sign you in with Wizzrobe, where a community has Third-party Apps turned on. One card shows who made the app, where it sends you and what it asks for: reads start ticked, changes start unticked, and it only ever works in the community you pick.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Apps you've connected sit with your API tokens on Profile, tagged App, and disconnect with one press. Profile also has a Developer apps section for registering an app of your own.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Staff can see which apps members connected under Admin → Settings → Connected apps, and block one for the whole community.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Help has a new article for developers, Building an app on Wizzrobe. It covers registering an app, the sign-in steps, what each permission reaches, limits and errors, plus an example script you can download and run.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Developers can try a read-only GraphQL API at ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "/api/graphql",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " with the same tokens as the REST API. It answers who you are and which events you can see for now; matches, tournaments and brackets come next. Open the address in a browser to explore it in GraphiQL.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The GraphQL API now reads tournaments, stages, the match schedule (players, approved crew, stage and seed, a page at a time) and each tournament's bracket, whether it runs in Wizzrobe, on start.gg or on Challonge.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Post-event surveys: staff build one under Admin → Surveys, starting from a template, and send it by DM to everyone who took part. Each person only gets the parts that fit what they did: one per tournament played, one per volunteer position, plus crew and check-in. Answers can be anonymous. Turn it on under Admin → Features.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Surveys can ask an agree scale: several statements, each answered from Strongly disagree to Strongly agree. It's a grid on a computer and one card per statement on a phone, and results show a bar per statement.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Staff can press Suggest questions on a draft survey section to get a few AI-drafted questions built from what happened at the event. The community uses its own Claude or OpenAI-compatible key under Settings → Integrations, nothing is sent until someone presses the button, and no names or anything people typed are ever sent.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey choice questions can add \"Other\" with a write-in box, a \"None of these\" answer that clears the other ticks, and a limit on how many people pick. Comments can be switched to a long answer with a character count.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey sections can have a short description, and commentators, trackers and broadcast crew can each get their own section.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Drag questions by their handle to reorder a survey, including into another section. On a phone, press and hold the handle first.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey questions can now show only to people who had a match on a stage, or played in the tournament room, in any tournament. Inside a per-tournament section, these look at the whole event rather than that one tournament.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Event › Schedule has a Friends switch: pick your friends once in the Friends filter and one tap shows only their matches, across every tournament. Your list is saved to your account, and friends' names are underlined on the board.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Surveys have a ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "strong",
                      "text": "Comments",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " tab: every comment and \"Other\" answer in one list, filtered by question, with search. Mark each one read and tag it (Wizzrobe, Venue, Schedule, Staff, Praise, or your own). Read marks and tags are shared by all staff, and the tag counts show what comes up most. People who answered never see them.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey results open with a drop-off card: how many people opened, started and sent the survey, which parts they reached, where the rest stopped, and the median time to finish against the estimate.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey invites and reminders have a ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "strong",
                      "text": "Not interested",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " button. Pressing it stops the reminders; the survey link still works.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "An open survey's Actions menu has ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "strong",
                      "text": "Change close date…",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": ".",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey parts can be aimed at one volunteer position: \"Worked one of these positions\" lets you pick one or more positions, and \"Volunteered (any position)\" covers everyone with a shift.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Surveys now reach tournament organizers. A section can repeat for each tournament someone runs, and \"Ran a tournament\" asks organizers something once.",
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
              }
            ]
          },
          {
            "category": "Improved",
            "count": 32,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "The Admin menu is split in two: what belongs to the event you're looking at, then what's the same across the community.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Community Events are now called Meetups.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Connecting an AI client like Claude now shows the same checklist as outside apps: pick what it can see and which changes it can make, instead of one read-or-write switch. Connections you've already made keep working as they are.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "When a message link opens your crew slots, your matches or the admin Match Schedule on a single match, a \"One match ✕\" chip in the filter row says so; tap it to see everything again. Your Withdraw and Sign up buttons on crew cards lost the dashed box around them.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "On-site roles (Proctor, Broadcast Tech, Triforce Submitter, Volunteer Coordinator, Equipment Manager, Volunteer and Check-in Desk) are now held in one event, so a proctor for this year's event isn't automatically one for next year's. Staff, Preset Manager, Sync Admin and Qualifier Admin still cover every event. Existing grants moved to each community's default event.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Edit member shows community-wide roles and roles in one event (pick the event beside the label), with a line naming what the person holds in other events. The Members list names the event for roles held elsewhere.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Discord role mappings for on-site roles name the event they grant in, the default event unless you pick another.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Seed DMs for A Link to the Past, AVIANART and Donkey Kong 64 now show the file-select code as the game's own icons, with the item names underneath.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Volunteer shift reminders and \"Your shift is starting\" DMs now say who you're taking over from and who's taking over from you at the end, on the same position.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The Running late button is now called \"Tell staff I'm late\", on My Schedule, the stage reminder DM and the volunteer shift-start DM. Two players read the old label as a status rather than a button. Once you've sent it, your match reads \"Staff know: ~20 min\".",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Tournament filters on the match boards, Reports and Triforce Texts now show each tournament's short name (OoTR rather than Ocarina of Time Randomizer), so several picks fit on one line. Tournaments without a short name keep their full name.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Webhook messages now say which event they're from, with an ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "event",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": " field holding its id, link name and name. Messages about the whole community have ",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "code",
                      "text": "event: null",
                      "href": "",
                      "tone": ""
                    },
                    {
                      "kind": "text",
                      "text": ".",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A payout place can now be a fixed dollar amount instead of a percent, so splits announced in dollars (like $650 for first) record to the cent.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "1–5 and 0–10 survey questions are now sliders, so they fit on a phone screen.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Surveys only count someone as crew if the match they crewed was on a stage.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The anonymous option now says plainly that what you write may still identify you.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Reporting a result is one tap: a live two-player match shows both names under \"Who won?\" on the board and in the match, and a short Undo replaces the confirm steps. A result staff or the tournament admin records is confirmed in the same tap.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Approving a commentator, tracker or broadcaster is one tap with Undo, and their DM waits until Undo runs out. Confirming a proctor's result is one tap with Undo too.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Sending a survey with a required question left blank now lists what's missing at the top, marks those parts, and jumps straight to each question.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The survey builder shows how long most people will take beside the longest path, plus a Checks list of things worth a second look, like a rating that asks two things at once or a part nobody will see.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The survey preview starts with a table of what each kind of person sees, and greys out what they won't see, with the reason.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A duplicated survey's results show last time's numbers under each carried question.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey DMs greet people by name and say how long their own survey takes. A reminder to someone who saved answers but never sent them says they're partway through.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A survey can send two reminders, and the second is worded as a last call.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The builder's \"too long\" warning now starts at 10 minutes instead of 5.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Survey results read in the survey's own sections, one line per question with a bar and its key number, so a big survey fits on a few screens instead of dozens.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A section asked once per tournament or volunteer position is a comparison table, and a tournament or position well below the rest is flagged.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Comments open in a side sheet with search and a chip per tournament or position.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Filter the whole results page to one tournament's players, one volunteer position, crew or people who checked in. Results also show the response rate for players, volunteers and crew.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "After sending a survey, people see what happens next (staff can write their own line under Edit details) and whether their answers went anonymously.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Staff can fix a typo in a sent survey's wording with \"Fix wording…\" in each question's and section's ⋮. Questions reworded after answers came in are tagged \"Edited\" in results, with the old wording.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A Discord DM that doesn't go through because the bot is offline or Discord is having trouble is now tried again for up to 15 minutes, instead of being dropped. Sent messages shows it as pending until it lands.",
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
              }
            ]
          },
          {
            "category": "Fixed",
            "count": 5,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "The Match Schedule's \"Needs you\" list and \"Not booked yet\" view only show the event you're looking at.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "An export started while looking at another event, or at an archived one, now exports that event's tournaments and volunteers instead of the default event's.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Match boards, tournament filters and other lists that refresh themselves (paging, switching tabs, the Refresh button) no longer fail with an error and stay empty.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The board's Approve button no longer resets to Approve when another match changes, which made the second tap look like it did nothing.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Closing a survey part with Back to parts, the close button or a tap outside now keeps what you typed, instead of only Done saving it.",
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
              }
            ]
          },
          {
            "category": "Security",
            "count": 2,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "Third-party apps now have a request ceiling shared by all their users, and you can register up to five new apps a day.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Apps that run in a browser can call Wizzrobe from the address they registered, and from nowhere else.",
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
              }
            ]
          },
          {
            "category": "Behind the scenes",
            "count": 1,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "The API docs now list the scope a third-party app needs for each endpoint, ready for \"Sign in with Wizzrobe\". Personal API tokens work exactly as before.",
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
              }
            ]
          }
        ]
      },
      {
        "start": "2026-10-19",
        "end": "2026-10-25",
        "title": "SG Live 2026",
        "period": "Oct 19 – 25, 2026",
        "anchor": "2026-10-19",
        "sections": [
          {
            "category": "New",
            "count": 14,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "Staff decide how every message Wizzrobe sends arrives (ping, silent or off) for each kind of recipient, under Admin → Notifications. On Profile → Notifications you pick Everything, Only what needs me or Quiet, adjust any single message, and can turn on sixteen extra ones that are off by default.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Messages sent to you, on Profile → Notifications, lists every message Wizzrobe sent you. The line at the bottom of each notification opens that exact message so you can change how you get it.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Staff can see every DM sent in the community and whether it arrived, under Admin → Reports → Sent messages, and clicking a name on Admin → Members opens everything about that person, including how messages reach them.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Prize pools follow sglive.speedgaming.org every 15 minutes and show beside each tournament on the event info list and the Watch page.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Best-of-3 and best-of-5 sets on Challonge and start.gg are booked as one match per game, and the set result goes to the bracket with its game score.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Staff can book a bracket match before its players are known (\"Andy or Benny vs Cleo or Dash\"), or create a placeholder match with just a title to hold a slot.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Players can tell staff they're running late from My Schedule or the stage reminder DM. The admin on duty hears about it; the opponent doesn't.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Dropping a player from a tournament forfeits or calls off their open matches in one go, and takes them off the Challonge bracket too.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Admin → Reports → Exports downloads a tournament's players and matches, and keeps every export for 3 days.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Challonge brackets can sync on a schedule, and a match someone already reported on Challonge or start.gg shows under Needs you to record in one tap.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Times now show on the clock your browser prefers: 9:30 PM if it uses 12-hour time, 21:30 if it uses 24-hour, and 9:30 PM when it doesn't say. If the guess is wrong, pick 12-hour or 24-hour under Profile → Time format, next to Timezone. Discord DMs follow your own Discord setting; email, Telegram and phone notifications, CSV exports and the Watch page use 12-hour.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Staff can call off a volunteer's shift from the Shift Board, for one person or the whole shift, even once it's started. The volunteer gets a DM saying they don't need to come (or can head out), skips check-in, and still gets full credit for the hours. Undo puts them back and tells them they're needed after all.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Admin → Reports → Staff / Crew Activity has a Crew by tournament table: how many matches each person commentated and tracked, grouped under each tournament. It only counts approved crew on finished matches that went to a stage, and exports to CSV.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Proctor Station finds a station: type a number like 7 in the search box and you'll see the unfinished matches with someone at station 7, their station lit up. On a phone the search box now sits above Filters instead of inside it.",
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
              }
            ]
          },
          {
            "category": "Improved",
            "count": 22,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "The Watch page shows each stage as a tile with a live picture of its stream, and a bell on any upcoming match DMs you before it goes on stage. Staff can send signed-out viewers to a lightweight version so a crowd can't slow the app down.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Back and Forward step through tabs and views, a copied link opens the exact view you were on, and every section heading, match and shift has a link you can copy.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Match boards can fold any group (Finished starts folded), reset filters in one tap and approve crew in place. Big races show three names and \"+N more\" and finish without picking a winner, and Broadcast Techs get Proctor Station for stage matches.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "My Schedule only shows what applies to you, and your Timeline adds community events and the crew slots still waiting on a stage.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The Capacity Forecast splits off-stream from on-stream players, keeps late matches on the clock, and leaves out tournaments played outside the tournament room.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Crew hear about a match only once it's on a stage, and the signup DM staff get updates itself when someone approves or withdraws the signup.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The side menu is bigger on a phone, with Home, Volunteer and Admin in a strip at the top that swaps the page list without leaving your page. Plenty of other screens got tidier on a phone too.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The event guide opens inside the app and has a Community events page, and staff can post a community event on another member's behalf.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Once a match starts, the schedule boards and My Schedule show when it actually started and when it should end (start plus the tournament's match length), turning amber if it runs over. Finished matches show their real start and finish.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The capacity forecast now expects a match to start about 15 minutes after its players check in, so it no longer frees their seats a quarter of an hour early.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Tournament admins and crew coordinators now see only their own tournaments on Reports → Staff / Crew Activity, including Crew by tournament. Staff still see the whole community.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "When a player taps Running late on a match that's on a stage, the Broadcast Techs on shift hear about it too (every Broadcast Tech, if none is on shift). Staff can change or mute it on Admin › Notifications under \"Player running late (Broadcast Techs)\".",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The match operations report now judges lateness by check-in as well as race start, shows how long matches take from check-in to start (split by staged and unstaged), and lists matches with impossible times, such as ones closed as played on Challonge, instead of letting them skew the averages.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "On a phone, Proctor Station cards now show the tournament, bracket and game under the player names, like the desktop board.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "A side the bracket hasn't decided yet (\"TBD\", \"Winner of C\", \"Andy or Benny\") now shows in grey italics on the match schedule, My Schedule, Not booked yet, the match details and the Watch page, so it no longer reads like a real player.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Matches under Up next on the run sheets now show an estimated end (\"to ~22:25\"): the booked time plus 15 minutes of setup plus the tournament's match length.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The fun facts under empty lists, loading spinners and the 404 page are about Hadestown this Saturday, switch to rollercoasters (Adventureland, Iowa included) at 6am Sunday, and go back to cats at 6am Monday. They're all listed at /facts, and the old /cat-facts link takes you there.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Players book a best-of-three's games one at a time, and in any order: \"Waiting on you to pick a time\" shows a row for each game left, with the deciding game marked \"If needed\". Schedules number the games by when they're played, so moving game 3 ahead of game 2 swaps their numbers.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Calling off one game of a best-of-three no longer takes the rest of the set with it.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Match cards on Match Schedule, Proctor Station, Event › Schedule and My Schedule have a new look. A coloured edge and a status word show each match's state at a glance (green live, amber late, blue checked in), and on a phone the time sits on its own line so names get the card's full width.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Every run sheet's Stage filter now has a \"No stage\" choice, so you can list just the matches that aren't on a stage, or mix it with a stage.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Checking in a match that's on a stage no longer asks for stations: one tap checks it in. Stage matches also stop showing \"No stations yet\" and \"No station\" tags, and the match sheet's checklist no longer flags their stations as missing.",
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
              }
            ]
          },
          {
            "category": "Fixed",
            "count": 12,
            "blocks": [
              {
                "kind": "bullets",
                "spans": [],
                "items": [
                  [
                    {
                      "kind": "text",
                      "text": "On iPhone, links and buttons work on the first tap in the Home Screen app, turning on notifications no longer crashes Safari (Help now shows how to add Wizzrobe to the Home Screen), the bottom bar stays put, and older Safari gets an update message instead of a blank page.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "The brief whole-app freezes during the event are much shorter, busy pages no longer stall everyone while they load, and schedules stop jumping back to page 1.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Resetting a bracket keeps the matches players already booked, a result already entered on Challonge is never overwritten, a result Challonge drops gets retried, and re-matching an entrant moves their booked matches with them.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Booking a best-of-three Challonge matchup from your Home page now asks for a time for every game and books them all, instead of booking game 1 only and leaving nowhere to roll game 2's seed.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Reloading a page you opened from a Discord link no longer reopens the dialog or repeats \"That request has already been dealt with.\"",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Average match length on Reports and Insights skips matches that were finished hours late (over three times the tournament's match length) or show zero minutes, so one late Finish click no longer drags a tournament's average up.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "On the narrowest phones, Proctor Station no longer squeezes a match's names and ADA note into a thin column when a Roll seed or Start button sits beside them. The button moves under the names instead.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Challonge sign-ins now renew a few days before they lapse, so Service Health only warns when a renewal actually fails, and start.gg no longer says a sign-in lapsed when nobody had used it that week.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Booking another game for two players who already have an unfinished Challonge or start.gg match now offers to count it as the next game of that set, ticked by default. Challonge and start.gg then get the set's result instead of game 1's. This works even after game 1 has been played.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "When a result is corrected on Challonge or start.gg and a different player moves up, both players in the new matchup now get the \"ready to schedule\" message. Before, the player who'd been moved up by mistake got it and the right one heard nothing.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "On a phone, the Match Schedule and Event › Schedule cards now say which game of a best-of-three it is (\"Game 2 of 3\"), like the desktop rows, and the estimated end time (\"to ~12:00 PM\") no longer runs into the text beside it.",
                      "href": "",
                      "tone": ""
                    }
                  ],
                  [
                    {
                      "kind": "text",
                      "text": "Broadcast Techs on Proctor Station now see only matches on a stage, as intended, instead of every match.",
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
              }
            ]
          }
        ]
      }
    ]
  }
}

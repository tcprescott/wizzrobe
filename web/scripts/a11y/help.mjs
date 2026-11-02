/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'help', path: '/t/sgl/help' },
  { name: 'help-article', path: '/t/sgl/help/crew' },
  { name: 'help-zoom', path: '/t/sgl/help/crew', act: (p) => p.getByRole('button', { name: /^Enlarge/ }).first().click(), wait: 'role=dialog' },
  { name: 'help-signed-out', path: '/t/sgl/help', signedIn: false },
  { name: 'help-article-signed-out', path: '/t/sgl/help/crew', signedIn: false },
]

export const fixtures = {
  "/api/help": {
    "articles": [
      {
        "slug": "getting-started",
        "title": "Getting started",
        "summary": "Signing in, joining the community, finishing your profile, and where to go next.",
        "icon": "flag",
        "for_staff": false
      },
      {
        "slug": "schedule-board",
        "title": "Reading the schedule board",
        "summary": "What each line on the Schedule board tells you, what each match state tells you, and how to filter it down to what you need.",
        "icon": "schedule",
        "for_staff": false
      },
      {
        "slug": "crew",
        "title": "Signing up as crew",
        "summary": "Commentating or tracking a match, from signing up and getting approved through confirming and withdrawing.",
        "icon": "record_voice_over",
        "for_staff": false
      },
      {
        "slug": "tournament-signup",
        "title": "Signing up for a tournament",
        "summary": "How to enter a tournament, when signups close, and how to back out.",
        "icon": "emoji_events",
        "for_staff": false
      },
      {
        "slug": "player",
        "title": "Being a player at the event",
        "summary": "My Schedule, acknowledging, getting a match booked, seeds and race rooms, asking for a different time, and your result.",
        "icon": "videogame_asset",
        "for_staff": false
      },
      {
        "slug": "suggest-a-time",
        "title": "How Suggest a time works",
        "summary": "How Suggest a time picks a slot, why it fills the room early instead of spreading matches out, and what it doesn't check.",
        "icon": "lightbulb",
        "for_staff": false
      },
      {
        "slug": "on-air",
        "title": "Watching the event",
        "summary": "The Watch page, the stream player, and how to follow a match you aren't in.",
        "icon": "live_tv",
        "for_staff": false
      },
      {
        "slug": "notifications",
        "title": "Notifications and Discord DMs",
        "summary": "What the bot DMs you, what each button does, and what to do when nothing arrives.",
        "icon": "notifications",
        "for_staff": false
      },
      {
        "slug": "glossary",
        "title": "Glossary",
        "summary": "Every term the app uses, defined once.",
        "icon": "menu_book",
        "for_staff": false
      },
      {
        "slug": "staff-schedule",
        "title": "Running the schedule",
        "summary": "For staff. Admin → Match Schedule, creating and editing matches, cancelling one, rewinding a match, stages and stream candidates, and approving crew.",
        "icon": "edit_calendar",
        "for_staff": true
      },
      {
        "slug": "staff-match-day",
        "title": "Match day queues",
        "summary": "For staff. What's waiting on you on Admin → Match Schedule: reschedule and cancellation requests, results to confirm, disputes, seeds, race rooms and the room screens.",
        "icon": "pending_actions",
        "for_staff": true
      },
      {
        "slug": "staff-events",
        "title": "Running more than one event",
        "summary": "Starting next year's event from this one, going live, the default event, archiving last year's, and the event argument in the API.",
        "icon": "calendar_month",
        "for_staff": true
      },
      {
        "slug": "staff-tournaments",
        "title": "Setting up a tournament",
        "summary": "Creating a tournament, its signup window and match hours, entrants, and who can run it.",
        "icon": "emoji_events",
        "for_staff": true
      },
      {
        "slug": "staff-presets",
        "title": "Seed presets and test rolls",
        "summary": "Adding and importing seed presets, checking them with a test roll, and attaching them to a tournament.",
        "icon": "tune",
        "for_staff": true
      },
      {
        "slug": "staff-exports",
        "title": "Exporting tournament data",
        "summary": "Downloading participants and matches for your tournaments as CSV or JSON, where every export lands, and how long it's kept.",
        "icon": "download",
        "for_staff": true
      }
    ]
  },
  "/api/help/crew": {
    "slug": "crew",
    "title": "Signing up as crew",
    "summary": "Commentating or tracking a match, from signing up and getting approved through confirming and withdrawing.",
    "icon": "record_voice_over",
    "for_staff": false,
    "headings": [
      {
        "anchor": "signing-up",
        "text": "Signing up"
      },
      {
        "anchor": "signing-up-before-a-match-is-booked",
        "text": "Signing up before a match is booked"
      },
      {
        "anchor": "commentating-as-a-pair",
        "text": "Commentating as a pair"
      },
      {
        "anchor": "signing-up-from-discord",
        "text": "Signing up from Discord"
      },
      {
        "anchor": "getting-approved",
        "text": "Getting approved"
      },
      {
        "anchor": "confirming",
        "text": "Confirming"
      },
      {
        "anchor": "my-schedule",
        "text": "My Schedule"
      },
      {
        "anchor": "reading-the-crew-columns",
        "text": "Reading the crew columns"
      },
      {
        "anchor": "withdrawing",
        "text": "Withdrawing"
      },
      {
        "anchor": "if-nobody-approves-you",
        "text": "If nobody approves you"
      },
      {
        "anchor": "on-the-day",
        "text": "On the day"
      }
    ],
    "blocks": [
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Crew covers the two jobs behind a streamed match. Commentators talk over the match on stream. Trackers run the item and progress tracking viewers see next to the game, the overlay showing what each player has found and how far along they are.",
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
            "text": "Some events add a third job, ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Broadcaster",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ": running the stream for that match. It only shows up where staff have turned it on.",
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
            "text": "They all work the same way: sign up, get approved, confirm, turn up. Anyone in the community can do it, and you don't need a volunteer role.",
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
            "text": "Signing up",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "signing-up",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Open Event, stay on the Schedule view, and turn on ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Needs crew",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ". That hides every match that's already covered or under way, which on a busy day is most of the board. Then tap ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " in the match's Commentators or Trackers column. On a phone each role sits under the match name with its own button, ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up as commentator",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " or ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up as tracker",
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
            "text": "There's no confirm step. If you tapped the wrong one, ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Withdraw",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " is in the same spot.",
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
            "text": "Signing up tells staff you're available for that slot. A staff member decides who actually gets it.",
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
            "text": "Every streamed match has commentary. Some tournaments don't use trackers, and for those the Trackers column is empty on purpose.",
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
        "kind": "image",
        "spans": [
          {
            "kind": "text",
            "text": "A match that still needs crew: tap ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up as commentator",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " (1) or ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up as tracker",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " (2).",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "/static/help/crew/signup-phone.webp",
        "alt": "A match on the board on a phone: Racer Five vs Racer Six on Stage 3, Commentary 0/1 and Tracking 0/1 both marked needs crew, with Sign up as commentator marked 1 and Sign up as tracker marked 2",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "If you can't sign up, the ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " button is greyed out with the reason next to it.",
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
              "kind": "em",
              "text": "You're playing",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " means it's your own match, and you can't crew your own game.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "em",
              "text": "Signups closed",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " means the match has started. Signups close when a proctor starts it. A match that's only checked in is still open, since that's exactly when a stream finds out it's short a commentator.",
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
            "text": "If you've already signed up for that role on that match, you'll see ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Withdraw",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " instead.",
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
            "text": "Signing up before a match is booked",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "signing-up-before-a-match-is-booked",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Want the final while the semifinals haven't been played? Open ",
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
            "text": " at the bottom of Event › Schedule. It lists every bracket match nobody has booked a time for, with the sides it's still waiting on (\"Andy or Benny\", \"Winner of C\", \"TBD\"). Sign up there the same way.",
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
            "text": "Your signup waits on the match. When it gets booked, your signup moves onto the booking, approved or not, and the match shows up on your list with its time. Staff can approve you before then. If you turn out to be busy at the time it's booked for, the staff who approve crew get told so they can sort it out.",
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
            "text": "You won't get a new DM when it's booked. As with every match, you hear about it once it's on a stage. If you end up playing in it, your crew signup is dropped.",
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
            "text": "Commentating as a pair",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "commentating-as-a-pair",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "On a match that needs two commentators, sign up first, then tap ",
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
            "text": " next to your ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Withdraw",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " and pick who you want on the mic with you. They get a Discord DM with ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Decline",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " and ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Accept",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ", and the same pair under Commentary invites at the top of My Schedule.",
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
            "text": "Until they answer, your tray says who you invited, with ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Cancel invite",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " if you change your mind. Once they accept, they're signed up as commentator with \"with ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "em",
            "text": "your name",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": "\" beside them. You get one partner per match.",
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
            "text": "Staff still approve each of you on their own, so they can take one of you and not the other. If you withdraw, a partner who already accepted stays signed up, and an invite nobody has answered yet is cancelled.",
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
            "text": "Signing up from Discord",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "signing-up-from-discord",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "If you follow a tournament, the DM announcing each new match has ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up as Commentator",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " and ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up as Tracker",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " buttons. They do the same thing as ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " on the board. The DM's ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "View the schedule",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " button opens the board if you want to see who's playing first.",
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
            "text": "You pick which tournaments DM you, and how much, under Profile → Notifications → Tournaments you follow. ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "Notifications and Discord DMs",
            "href": "/help/notifications#where-your-settings-live",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " explains the levels. Most crew want ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "em",
            "text": "Streamed, plus possible streams",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ", which covers matches with a stage and the ones staff are thinking about streaming.",
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
            "text": "Getting approved",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "getting-approved",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Staff have the final say on every crew slot. A staff member, the tournament's admin or its crew coordinator decides who covers each one. Signing up puts your name forward but doesn't reserve anything.",
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
            "text": "It isn't first come, first served. Staff can see what else you're doing at that time, including matches you're playing in, and a clash is the usual reason a signup gets passed over.",
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
            "text": "Staff get a Discord DM as soon as you sign up. When they approve you, you get a DM too, with an ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Acknowledge",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " button and a ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "View your crew slot",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " button that opens your slot on My Schedule.",
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
            "text": "Staff can also put you on a match you never signed up for. You get the same DM, and the slot shows up on My Schedule ready to confirm.",
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
            "text": "They can withdraw an approval, right up to the day of the match, if plans for the stream change or someone better suited frees up. You'll get a DM, your confirmation is cleared, and you go back to waiting for approval. If they take you off the match completely, it disappears from your list. Neither is a judgement on you.",
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
            "text": "Confirming",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "confirming",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Once you're approved, you still need to confirm you can cover the slot. Until you do, staff can't tell whether you've seen the approval.",
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
            "text": "You can confirm in any of these places, and they all do the same thing:",
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
              "kind": "strong",
              "text": "Confirm",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", next to ",
              "href": "",
              "tone": ""
            },
            {
              "kind": "strong",
              "text": "Withdraw",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " in your role's column on My Schedule",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "text",
              "text": "the same ",
              "href": "",
              "tone": ""
            },
            {
              "kind": "strong",
              "text": "Confirm",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " on the schedule board",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Acknowledge",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", on the DM you were sent",
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
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "My Schedule",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "my-schedule",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Crew you signed up for, on Home's My Schedule tab, is the schedule board cut down to the matches you're crewing. If you aren't playing in any tournament it's at the top of the tab. If you are, it comes after your own matches. Each line looks the same as on the schedule, with your ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Withdraw",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " (and ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Confirm",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ", once you're approved) in your role's column, so the names tell you where you stand:",
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
              "text": "Beside your name",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "text",
              "text": "What it means",
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
                "text": "A clock, ",
                "href": "",
                "tone": ""
              },
              {
                "kind": "em",
                "text": "waiting for approval",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "You've signed up. Staff haven't decided yet.",
                "href": "",
                "tone": ""
              }
            ]
          ],
          [
            [
              {
                "kind": "text",
                "text": "A tick, with ",
                "href": "",
                "tone": ""
              },
              {
                "kind": "strong",
                "text": "Confirm",
                "href": "",
                "tone": ""
              },
              {
                "kind": "text",
                "text": " in your tray",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "You have the slot. Confirm that you can still cover it.",
                "href": "",
                "tone": ""
              }
            ]
          ],
          [
            [
              {
                "kind": "text",
                "text": "A tick, and no ",
                "href": "",
                "tone": ""
              },
              {
                "kind": "strong",
                "text": "Confirm",
                "href": "",
                "tone": ""
              }
            ],
            [
              {
                "kind": "text",
                "text": "You have the slot and you've confirmed. Nothing left to do.",
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
            "text": "Finished matches drop off. Add ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "em",
            "text": "Finished",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " and ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "em",
            "text": "Confirmed",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " to the State filter to see the ones you've already worked.",
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
        "kind": "image",
        "spans": [
          {
            "kind": "text",
            "text": "Crew you signed up for, on My Schedule.",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "",
        "src": "/static/help/crew/my-crew-phone.webp",
        "alt": "Crew you signed up for on a phone: a live match where your tracking signup is waiting for approval, a match where you have the commentary slot with Withdraw as commentator and Confirm, and tomorrow's match where your commentary signup is waiting",
        "name": ""
      },
      {
        "kind": "heading",
        "spans": [
          {
            "kind": "text",
            "text": "Reading the crew columns",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "reading-the-crew-columns",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "The board has a Commentators column and a Trackers column. Each starts with approved crew against what the match needs, so \"1/2\" means one approved commentator out of two. People still waiting for approval don't count, so a match can have three signups and still say \"needs crew\". ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Needs crew",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " uses the same rule to decide whether a match is covered. Crew only works matches on a stage, so a match with no stage yet just shows how many have signed up and never says \"needs crew\". On a phone, each card names the role instead (\"Commentary 1/2\").",
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
            "text": "Your own ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Sign up",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " or ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Withdraw",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " sits in a shaded box under the count, in the same place on every match. When there's nothing you can do, the button is greyed out with the reason, like \"Signups closed\" or \"You're playing\".",
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
            "text": "The names come after that. A tick means approved, and a clock with \"waiting for approval\" means not yet. Your own name has \"(you)\" after it. \"No signups yet\" means nobody's asked for that slot.",
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
            "text": "Withdrawing",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "withdrawing",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "strong",
            "text": "Withdraw",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " takes you off a slot. You can do it from My Schedule or the schedule board, before or after approval, even once the match has started.",
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
            "text": "Withdrawing before anyone's approved you costs nobody anything. Withdrawing from a slot you were approved for means staff have to find cover, so they're told right away, and the app warns you about that before you confirm.",
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
            "text": "If you'd already confirmed, withdrawing clears that too. Do it as early as you can.",
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
            "text": "If nobody approves you",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "if-nobody-approves-you",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Signups wait until staff get to them, which at a busy event might be shortly before the match. If a match you signed up for has started and you never heard back, the slot went to someone else. You can withdraw the old signup or leave it. Either way it drops off your list once the match ends.",
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
            "text": "On the day",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "on-the-day",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Where to be and when, who runs the countdown and how a stage works are up to the event, not the app. If your community publishes them, they're under Event Guide in the sidebar.",
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
    "tournaments": null,
    "venue_timezone": null
  }
}

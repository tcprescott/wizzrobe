/** a11y checks for this screen, with the stub API responses they need. See ../a11y.mjs. */
export const targets = [
  { name: 'about', path: '/t/sgl/about' },
  { name: 'about-privacy', path: '/t/sgl/about/privacy' },
  { name: 'about-notices', path: '/t/sgl/about/notices' },
  { name: 'about-notice', path: '/t/sgl/about/notices', act: (p) => p.locator('.read-notices button').first().click(), wait: 'role=dialog' },
]

export const fixtures = {
  "/api/about": {
    "intro": [
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Wizzrobe runs the schedule for speedrun and randomizer events: matches, brackets, crew, volunteers, equipment and check-in, with Discord sign-in and notifications. Each community gets its own space, run by its own staff.",
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
            "text": "It's free software under the ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "GNU General Public License v3",
            "href": "https://www.gnu.org/licenses/gpl-3.0.html",
            "tone": ""
          },
          {
            "kind": "text",
            "text": ", and the source is on ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "GitHub",
            "href": "https://github.com/tcprescott/wizzrobe",
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
    "built_with_claude": "Much of Wizzrobe's code was written with Claude, an AI model made by Anthropic. A person reviews every change before it ships, but if something looks wrong, tell us through Send Feedback or your community's staff.",
    "version": "2026.11.02",
    "commit_short": "f77d360",
    "source_url": "https://github.com/tcprescott/wizzrobe",
    "notice_count": 145
  },
  "/api/about/privacy": {
    "updated": "2026-10-17",
    "blocks": [
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Wizzrobe runs schedules, brackets, crew and volunteers for speedrun and randomizer events. This page says what it keeps about you, who can see it, who else it goes to, and how to get it changed or removed. Each community on Wizzrobe is run by its own staff, who decide how they use what you share with their community.",
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
            "text": "What we collect",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "what-we-collect",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "When you sign in with Discord, Wizzrobe asks Discord for your basic profile only (the ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "code",
            "text": "identify",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " permission) and keeps your Discord ID, username and avatar. It doesn't keep your Discord password or your sign-in token, and it can't read your messages or servers.",
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
            "text": "Everything else is something you, or a community's staff, add:",
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
              "text": "Your profile:",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " a display name, pronouns and timezone.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Linked accounts",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " you choose to connect: Twitch, racetime.gg, Challonge, start.gg and Matcherino. We keep each account's ID and username, not its password or sign-in token, and you can unlink any of them from your profile.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Notification settings:",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " an email address and a Telegram chat, only if you turn those on, and a push subscription for each device you allow notifications on (with a short description of the browser, so you can tell your devices apart).",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Event activity:",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " matches you play or crew, results and times, comments, reschedule requests, availability, tournament sign-ups, volunteer shifts, equipment you lend or borrow, and feedback you send.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Emergency contact and accessibility requests",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", if you give them. These are only visible to the community's staff, and every time staff open your emergency contact you can see that they did.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Event check-in:",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " if a community sells badges through Matcherino, staff import its roster (name, Matcherino account and the Discord or Twitch account used to buy) to check you in. We drop the buyer's email, phone, address and payment details when we import it.",
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
            "text": "Some people get an account before they ever sign in: a community can bring players in from the SpeedGaming schedule, a start.gg bracket, or its Discord server's roles. Those accounts hold the name and Discord ID those services published.",
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
            "text": "How the app uses cookies",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "how-the-app-uses-cookies",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Wizzrobe sets three cookies, all for running the app, none for advertising:",
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
              "kind": "code",
              "text": "session",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " keeps you signed in, for 90 days after your last visit.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "code",
              "text": "wiz_tz",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " remembers your device's timezone, so times show in your local time.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "code",
              "text": "wiz_dark",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " remembers whether you picked dark mode.",
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
            "text": "The app also records which pages and buttons get used (never what you type into them), tied to your account if you're signed in. Staff read it to learn which parts of the app people use. We don't store your IP address in the database.",
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
            "text": "Who can see it",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "who-can-see-it",
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
              "text": "Anyone",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", signed in or not, can see schedules, brackets, results and the names of the players in them.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Members of your community",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " can see what the app shows on its schedule and crew pages, such as who's crewing a match.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Your community's staff and volunteers",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " can see what you've shared with that community, to run the event. Staff also see a log of changes made in the app.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Staff of other communities can't",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " see what you've shared with a community they don't run.",
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
            "text": "We don't sell your information or use it for advertising.",
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
            "text": "Services we share it with",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "services-we-share-it-with",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Wizzrobe hands the minimum each service needs to do its job:",
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
              "text": "Discord",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", for sign-in, direct messages, server roles and event listings.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "SendGrid",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", to send email, if you turn email on.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Telegram",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", to send messages, if you connect it.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Your browser's push service",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": " (Google, Apple or Mozilla), to deliver notifications to devices you allow.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Sentry",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", which receives error reports and performance data to help us fix problems. A report can include your Discord ID and username.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "racetime.gg, Twitch, Challonge, start.gg and Matcherino",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", when you link those accounts or a community uses them for its tournaments.",
              "href": "",
              "tone": ""
            }
          ],
          [
            {
              "kind": "strong",
              "text": "Webhooks and AI assistants a community's staff connect",
              "href": "",
              "tone": ""
            },
            {
              "kind": "text",
              "text": ", which receive the community's event data. Webhooks include the username of whoever made a change.",
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
            "text": "Where it's stored",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "where-it-s-stored",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Your information is stored on servers in the United States, and the services above process it in the United States too. If you use Wizzrobe from outside the US, your information is transferred to and stored there.",
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
            "text": "How long we keep it",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "how-long-we-keep-it",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "We keep your account and activity for as long as Wizzrobe runs your community's events, so past results and schedules stay accurate. You can remove your email, Telegram, push devices, linked accounts, emergency contact, accessibility request and availability yourself at any time from your profile. Emergency contacts and accessibility requests belong to one event: 30 days after staff archive that event, they're deleted.",
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
            "text": "The change log and usage records keep a copy of your username even after your account is gone, so the history of an event stays readable.",
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
            "text": "Your choices",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "your-choices",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "To see what we hold about you, correct it, or have your account deleted, use ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "strong",
            "text": "Send Feedback",
            "href": "",
            "tone": ""
          },
          {
            "kind": "text",
            "text": " in the side menu when you're signed in. If you can't sign in, ask your community's staff on their Discord server. Leaving a community removes your emergency contact and closes any accessibility request you had open there.",
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
            "text": "Children",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "children",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "Wizzrobe isn't meant for children under 13, and we don't knowingly keep information about them. If you think a child has signed up, tell us through Send Feedback and we'll remove the account.",
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
            "text": "Changes to this policy",
            "href": "",
            "tone": ""
          }
        ],
        "items": [],
        "headers": [],
        "rows": [],
        "level": 2,
        "anchor": "changes-to-this-policy",
        "src": "",
        "alt": "",
        "name": ""
      },
      {
        "kind": "para",
        "spans": [
          {
            "kind": "text",
            "text": "When this policy changes, the date at the top of the page changes with it, and a bigger change gets a line on ",
            "href": "",
            "tone": ""
          },
          {
            "kind": "link",
            "text": "What's new",
            "href": "/changelog",
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
    ]
  },
  "/api/about/notices": {
    "groups": [
      {
        "key": "web",
        "heading": "Web libraries",
        "blurb": "Code that runs in your browser.",
        "notices": [
          {
            "key": "web:@tiptap/core",
            "name": "@tiptap/core",
            "version": "3.31.4",
            "license": "MIT",
            "url": "https://www.npmjs.com/package/@tiptap/core"
          },
          {
            "key": "web:@tiptap/extension-blockquote",
            "name": "@tiptap/extension-blockquote",
            "version": "3.31.4",
            "license": "MIT",
            "url": "https://www.npmjs.com/package/@tiptap/extension-blockquote"
          },
          {
            "key": "web:@tiptap/extension-bold",
            "name": "@tiptap/extension-bold",
            "version": "3.31.4",
            "license": "MIT",
            "url": "https://www.npmjs.com/package/@tiptap/extension-bold"
          },
          {
            "key": "web:@tiptap/extension-code",
            "name": "@tiptap/extension-code",
            "version": "3.31.4",
            "license": "MIT",
            "url": "https://www.npmjs.com/package/@tiptap/extension-code"
          },
          {
            "key": "web:@tiptap/extension-code-block",
            "name": "@tiptap/extension-code-block",
            "version": "3.31.4",
            "license": "MIT",
            "url": "https://www.npmjs.com/package/@tiptap/extension-code-block"
          },
          {
            "key": "web:@tiptap/extension-document",
            "name": "@tiptap/extension-document",
            "version": "3.31.4",
            "license": "MIT",
            "url": "https://www.npmjs.com/package/@tiptap/extension-document"
          }
        ]
      },
      {
        "key": "fonts",
        "heading": "Fonts and icons",
        "blurb": "Typefaces and icon sets the pages are drawn with.",
        "notices": [
          {
            "key": "fonts:Atkinson Hyperlegible",
            "name": "Atkinson Hyperlegible",
            "version": "",
            "license": "OFL-1.1",
            "url": "https://www.brailleinstitute.org/freefont/"
          },
          {
            "key": "fonts:IBM Plex Mono",
            "name": "IBM Plex Mono",
            "version": "",
            "license": "OFL-1.1",
            "url": "https://github.com/IBM/plex"
          },
          {
            "key": "fonts:Material Icons",
            "name": "Material Icons",
            "version": "",
            "license": "Apache-2.0",
            "url": "https://github.com/google/material-design-icons"
          },
          {
            "key": "fonts:Material Symbols",
            "name": "Material Symbols",
            "version": "",
            "license": "Apache-2.0",
            "url": "https://github.com/google/material-design-icons"
          },
          {
            "key": "fonts:Roboto",
            "name": "Roboto",
            "version": "",
            "license": "OFL-1.1",
            "url": "https://github.com/googlefonts/roboto-3-classic"
          }
        ]
      },
      {
        "key": "python",
        "heading": "Python packages",
        "blurb": "Code that runs on the server.",
        "notices": [
          {
            "key": "python:aerich",
            "name": "aerich",
            "version": "0.9.3",
            "license": "Apache-2.0",
            "url": "https://github.com/tortoise/aerich"
          },
          {
            "key": "python:aiofiles",
            "name": "aiofiles",
            "version": "25.1.0",
            "license": "Apache-2.0",
            "url": "https://github.com/Tinche/aiofiles"
          },
          {
            "key": "python:aiohappyeyeballs",
            "name": "aiohappyeyeballs",
            "version": "2.6.2",
            "license": "PSF-2.0",
            "url": "https://github.com/aio-libs/aiohappyeyeballs"
          },
          {
            "key": "python:aiohttp",
            "name": "aiohttp",
            "version": "3.14.5",
            "license": "Apache-2.0 AND MIT",
            "url": "https://github.com/aio-libs/aiohttp"
          },
          {
            "key": "python:aiosignal",
            "name": "aiosignal",
            "version": "1.4.0",
            "license": "Apache 2.0",
            "url": "https://github.com/aio-libs/aiosignal"
          },
          {
            "key": "python:aiosqlite",
            "name": "aiosqlite",
            "version": "0.22.1",
            "license": "MIT",
            "url": "https://aiosqlite.omnilib.dev"
          }
        ]
      }
    ]
  },
  "/api/about/notice": {
    "key": "web:@tiptap/core",
    "name": "@tiptap/core",
    "version": "3.31.4",
    "license": "MIT",
    "url": "https://www.npmjs.com/package/@tiptap/core",
    "text": "MIT License\n\nCopyright (c) 2025, Tiptap GmbH\n\nPermission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the \"Software\"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE."
  }
}

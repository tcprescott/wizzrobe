### New

- Sign in from Discord: type `/login` in your community's server and the bot DMs you a one-time link. Staff can post a permanent Log in button with `/wizzrobe login-panel`, and you now stay signed in for 90 days.
- Community Events lists the meetups, panels and side events members run during the event, under Home → Event → Community. Members submit their own (staff review them unless they turn that off), tap Remind me for a DM before one starts, or RSVP when the host caps the numbers.
- Matches have comments. Staff can leave a private note or message both players, and players can leave a note for staff on their own matches that the opponent never sees.
- More games roll seeds: Wind Waker (as a permalink), AVIANART, Super Metroid, SMZ3, Super Metroid VARIA, Zelda 2, Super Mario Bros. 3 and Aria of Sorrow. Final Fantasy and Zelda 1 now roll from a seed preset like the rest, with the SGL 2026 settings built in.
- Seeds show their file-select code, the symbols players compare before a race, under the seed link and in the seed DM, for every randomizer that shares one.
- Volunteers get a Full Schedule grid of every role's shifts for a day, can check themselves in from 30 minutes before a shift (or say they're running late or can't make it from the start-of-shift DM), and find their shifts on My Schedule. Coordinators run the day from Admin → Shift Board.
- My Schedule has a Timeline view that puts a day's matches, crew slots and shifts on a clock, with clashes side by side and the times you can't play hatched out.
- Notifications can reach you by email and Telegram as well as Discord and your phone, where the community turns them on, and "Send a test…" on Profile → Notifications checks each one gets through.
- start.gg brackets: link a tournament to a start.gg event and its bracket shows in the app, players get a DM when their match is ready to book, and results can go back to start.gg. Staff sign in under Settings → Integrations.
- Players can sign up for a Challonge tournament from the Tournaments tab, and each sync now mirrors the Challonge bracket fully. Challonge's sign-in moved to Settings → Integrations, and each bracket is managed from its tournament's ⋮ on Admin → Tournaments.
- Bracket tournaments have a Round schedule: staff set when each round opens and closes for booking, plus daily hours, and players can only book inside it.
- Match Schedule's Not booked yet view lists every bracket matchup that's ready to play with no game booked, with Book… on each and a one-tap Link for a match someone booked by hand.
- Booking checks the time before it saves. A Check this time sheet warns when a player is busy, the room is full or the match runs past closing, and lets you book anyway. Suggest a time no longer double-books a player or overfills the room.
- Staff can bring everyone in the community's Discord server in as members in one go, and Wizzrobe can hand out Discord roles to tournament entrants, players in a live match and badge holders.
- Attendees can add an emergency contact that only their community's staff can open, and every look is logged.
- People without Discord can get a password account for staff and desk work.
- Communities can upload a logo. It replaces the coloured square in the header, becomes the tab icon, and can sit in the middle of equipment QR codes.
- Watch, in the side menu, has the Twitch player, what's on each stage and what's coming up on stream, in Twitch channel order.
- Staff can run tournament and event-day admin from the Wizzrobe connector and the REST API, with the same permissions as the Admin pages.

### Improved

- Match boards are run sheets: Admin → Match Schedule, Proctor Station, Event › Schedule and My Schedule show one line per match grouped by what's next, with the next step as a button on the line. The admin board has a Needs you list for reschedule requests, results to confirm and late matches, and Stream Manager is now Broadcast Tech, who runs stage matches from it.
- Fewer pings. Watchers and followers hear about changes quietly, players aren't DMed when their match starts or finishes, player comments and reschedule requests go to whoever's on the Admin Desk shift, and when one staff member handles a request everyone else's DM updates to say so.
- Most screens got a phone and desktop pass: dialogs slide up from the bottom on a phone, rows have one button plus a ⋮ menu, slow buttons say what they're doing, deletes always ask first, and boards hold still while matches change instead of jumping. Admin → Tournaments fits on the screen, and every menu item has its own name and icon.
- Event info follows the organisers' SGLive 2026 guide, with the full guide as a PDF and the on-site tournament list read straight from Admin → Tournaments. Help for players, volunteers and proctors is up to date for SGL 2026.
- Schedule boards load about twice as fast with a full event's matches, and member lists and pickers no longer freeze the app in a community with thousands of members.

### Fixed

- When Discord, Challonge, start.gg, racetime or a randomizer is down, buttons say what went wrong instead of spinning, a half-broken sync no longer wipes a bracket, and a phone that drops Wi-Fi for a moment keeps its page. Two people changing the same thing at once no longer undo each other.

### Security

- The REST API and the Wizzrobe connector show no more than the app does for each role, and links in player-written comments and event descriptions always open in a new tab, marked as leaving the app.

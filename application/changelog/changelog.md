## 2026-10-19/2026-10-25 SG Live 2026

## 2026-10-12/2026-10-18 start.gg, email and Telegram, and run sheets everywhere

## 2026-10-05/2026-10-11 Event check-in, race rooms and a Challonge overhaul

### New

- The new check-in desk works from Matcherino venue badge sales. Staff sync the buyers, match each one to a member (or link them on the spot), and hand out one lanyard per person. Tiers, comped volunteers, walk-ups and refunds are all handled, and each community keeps its own Matcherino login in the app.
- Checking in at the desk now sends you a Discord DM, so the desk can see straight away if your DMs are closed.
- Help has a Checking in at the event page. It says what to give the desk when you arrive, and walks check-in desk volunteers through finding people, linking accounts, lanyards, the DM check, refunds and a stale roster. The desk links to it from a help icon beside the event name.
- Staff can ask every member to finish parts of their profile, like linking Challonge or adding pronouns, from Settings under Members. Anyone who hasn't sees a red banner on every page with a button that opens the right spot on their profile, and staff decide whether each reminder can be dismissed.
- Race rooms open on racetime.gg, and players get a link straight into theirs.
- Volunteer positions can grant app roles, Check-in Desk included, to whoever is assigned. Discord role mappings stay workable with many roles and grants.
- Help has two articles for volunteer coordinators: building the shift schedule with the auto-scheduler, and the roster, hours, reward tiers and export. Vol. Schedule and Vol. Roster have help icons that open them.
- The SGL 2026 tournaments were imported from the planning sheet with their randomizer presets and notes, and the Presets tab gained a Test roll button.
- Spectators get a cached Watch page, and the join page points at it.
- Help has a new article on borrowing equipment: scanning a loaner's QR code, giving it back, and running the equipment desk. The asset page, the equipment section on My Schedule and Admin → Equipment link to it.
- This page: What's new, at the top of Help, lists the bigger changes a week at a time.
- Help now covers tournament setup for staff: creating a tournament and its signup window, Challonge brackets, seed presets and test rolls, prize payouts, and the ADA queue. The Tournaments, Challonge, Presets, Payouts and ADA requests pages each have a help icon that opens the right part.
- Help's For staff section has two articles on running match day from the Schedule tab: booking and editing matches, stages and crew approval, then reschedule requests, confirming results, disputes, seeds and race rooms. The Schedule tab's title links to them.

### Improved

- A community can let anyone who signs in join on the spot. Staff now pick who can join with one setting instead of two switches.
- Challonge entrants map to members, bracket stages advance from Wizzrobe, and final-stage players are read correctly. Tournaments hosted on an organization subdomain or a custom domain link properly now, and players are asked to sign in to Challonge before linking.
- Crew hear about it when an admin assigns or removes them, crew signup lost several waits and taps, and the match buttons on the floor are safe against stale pages and double taps.
- Help for crew and spectators is up to date: signing up from a Discord DM, what the names in a crew cell mean, the board's filters (finished matches are hidden by default), what On Air shows, and the public Watch page, which On Air now links from its help. On Air's Watch Stream link is readable again in the light theme.
- Equipment checkout can't double-book a device, and equipment managers can retire assets.
- Screen readers and keyboards get further. Icon buttons say what they do instead of just "button", every page has a title heading and a Skip to content link, filter boxes and switches are named, and Help cards, bracket matches and date pickers open with Enter. The phone tab bar and the cat facts under empty lists are darker and easier to read.
- Admin settings live in one place. Settings in the admin menu opens its own list of sections (General, Event, Venue, Members, Volunteers, Discord, Integrations, Features), and each card saves on its own, with a Save button that only appears once you've changed something. Old links to Appearance, Timezone, Role Mappings and the rest still land on the right section.
- The player help is rewritten for SGL 2026. It now covers joining a community, the profile reminder banner, booking a bracket match, seeds, harder settings, race rooms and change requests, and a new Help article explains ADA accommodation requests. The glossary picked up the words those screens use.

### Fixed

- Volunteer shift and availability times now show the timezone they're actually in (EDT, AEST and so on) instead of always saying ET, and the capacity chart's time axis is on your clock rather than UTC.

### Security

- Removing someone from a community now keeps them out, even where anyone can join or Discord server members get in automatically. They can still ask to join, and staff can add them back.
- A login only works on the site it started on, the check-in desk can only link people in its own community, staff can't change the Challonge link of someone who also plays elsewhere, names can't carry links or Discord mentions, a deactivated account's old Discord buttons stop working, and asking for a match can't sign other people up for a tournament.

## 2026-09-28/2026-10-04 Joining a community, ADA requests and boards that fit a phone

### New

- Members of a community's Discord server get in without a join request, and staff can switch join requests off without touching that.
- Members can request ADA accommodations. Staff work them from a queue under Users, and an ADA icon shows on the Schedule and Proctor Station boards.

### Improved

- The match boards and the player Home page work at both phone and desktop width.
- Match DMs are written for the person reading them, and an opponent can agree to a reschedule from the web as well as from Discord.
- Signing in brings you back to the page you were on, and every missing page shows the same not-found screen.

### Security

- Export CSV no longer includes private ADA notes or Discord IDs.

## 2026-08-31/2026-09-27 Harder presets, staff on volunteer shifts and Discord-provisioned accounts

### New

- Players can privately opt in to a harder preset. The agreement is asked again whenever the match changes.
- Discord members who hold a mapped role get an account before they ever sign in, so they can be scheduled straight away.
- Approved triforce texts are built into ALTTPR match seeds.

### Improved

- Staff can be assigned to volunteer shifts, missing availability is flagged, and the shift picker has search. A stretch nobody declared reads "Not stated".
- The volunteer role on its own decides who can set volunteer availability.

### Behind the scenes

- The documentation was realigned with the code, and anyio was bumped for two security advisories.

## 2026-08-10/2026-08-23 Async qualifier fixes

### Improved

- Availability reads are batched and tallied in the database, so the boards load faster.

### Fixed

- Staff can record a live race that racetime.gg never reported, qualifiers publish the window they open in, and four live-race bugs were fixed.

### Behind the scenes

- Oversized files split and guardrails tightened.

## 2026-08-03/2026-08-09 Seed presets, payouts and a simpler Home

### New

- Prize splits are recorded in the app instead of a Discord thread, runners and crew are told which stage they're on, and volunteer hours are totalled against the comp tiers.
- Any member can borrow loaner equipment for themselves by scanning the device's QR code, and proctors can lend a device when the desk is busy.
- The room PC can show the seeds board full screen, without the header or drawer.
- The sign-in wait screens carry the brand, and the cat facts grew to 226.

### Improved

- Seed presets come from each randomizer's own preset list, and choosing a preset is now the only randomizer control. A long seed roll survives a restart, and a failed roll tells the admin.
- Home went from ten tabs to four.
- Player availability is opt-out now.

### Security

- Fixed a security hole that let a community's staff grant themselves super-admin.

## 2026-07-27/2026-08-02 Help, Event Information, reschedule requests and Claude access

### New

- In-app Help explains how the app works for players, crew and volunteers. Event Information is a separate per-community handbook for the event itself.
- Players can ask staff to move or call off a match, from the web or from Discord, and staff work the requests from a queue that updates live.
- Times show in each viewer's own timezone unless the community pins one.
- Every desktop table can be customized (columns, order, widths, page size), and your layout is saved.
- Discord role mappings can target one tournament's grants, "stream room" is called "stage" everywhere, randomizer API keys are set per community, and the bot moved from py-cord to discord.py.

### Improved

- An MCP server lets Claude Desktop read community data, and change it when you allow that at sign-in.
- Proctor Station got its own board, a dispute flag, a confirmation before recording a winner, and a way for admins to correct a recorded result.
- Check-in draws random seats on opposite sides of the room. The tournament room is closed to spectators, and check-in moved to the admin desk.
- Spectator bracket views are cached pages, and Discord avatars show in the bracket.
- Community membership is real now. Everyone else gets a join page, and a new community's first admin gets a setup checklist.
- Crew approval is a toggle with a queue, crew are told when approval is withdrawn, and a tournament can say how much crew a stream needs.
- Equipment, async qualifiers, the volunteer hub, account linking and the admin reports each got a usability pass.

## 2026-07-20/2026-07-26 Native brackets

### New

- Wizzrobe runs its own brackets: elimination, Swiss and group/round-robin, with a phone layout and best-of-N series. The bracket and the schedule are one system, a bracket-run tournament schedules only from its bracket, and a published bracket is open to anyone.
- Pages build their tabs only when opened and load the match table once, so they open faster.

### Improved

- Each tournament can set its own event window and match hours.
- The profile page was redesigned for phones first.
- Equipment QR labels print in bulk, and unowned equipment carries the community's name.
- Signing in works across a community's own host name, with account links handed between hosts.
- Super-admins have full authority inside any community, and feature flags are enforced behind the scenes as well as in the menus.

## 2026-07-13/2026-07-19 Online tournaments, feature flags and the Wizzrobe name

### New

- Online tournaments arrived: async qualifiers, racetime.gg integration and Discord scheduled events.
- Each community can switch optional features on and off.
- Every table has a card view on phones, and Discord messages became embed cards.
- Tabs have their own addresses, so you can link straight to one.

### Improved

- Several communities can share one Discord server, and a community can be reached on its own host name as well as its /t/ address.
- The app was renamed from "SGL On Site" to Wizzrobe, and communities can pick their own theme colours with a contrast check.
- New randomizers: Majora's Mask, Super Metroid DASH, Wind Waker and DK64, with DK64 rolling real seeds.

## 2026-07-06/2026-07-12 One app, many communities

### New

- Wizzrobe can host more than one community, each with its own data and its own /t/ address.
- Twitch accounts can be linked.
- Cat facts appeared around the app.

### Improved

- Staff got analytics: trends across events, tournament health, and an engagement report.
- The volunteer Schedule tab is now Proctor Station, the watch toggle uses a notification bell, and the phone header and match cards were tidied.

### Behind the scenes

- Protection against server-side request forgery, a rate-limit ceiling, input size limits, faster queries on busy pages, and a GPL v3 license.

## 2026-06-29/2026-07-05 Works like an app on your phone

### New

- Wizzrobe can be installed to a phone's home screen, with a bottom tab bar, swipeable tabs, full-screen dialogs, native date and time pickers, and a match card built for small screens.
- Outbound webhooks fire for match, crew, seed and volunteer events, and staff have an in-app reference for the payloads.
- Device notifications arrive through web push.
- The interactive API docs are available in production.

## 2026-06-22/2026-06-28 Volunteer roles and friendlier errors

### New

- Volunteer access is role-based, with a roster page and qualification management. Shift assignment filters by qualification.
- Discord roles can be synced for every user at once.

### Improved

- The admin dashboard is limited to admins, and the proctor workflow moved to the volunteer page.
- Error pages match the app and show an error ID you can quote when reporting a problem.

### Behind the scenes

- A large service-layer test suite and automated checks that keep the code's structure, dates and migrations safe.

## 2026-06-15/2026-06-21 Feedback, equipment and Challonge accounts

### New

- Members can send feedback from the app, and staff review it.
- Equipment management arrived, with QR codes, checkout and loan history.
- Challonge tracks API usage, manages tournament opt-in for linked tournaments, and lets admins link or edit a member's Challonge account.
- Volunteer shifts got management tools and a guarded reset-all.

### Improved

- Availability windows are checked for order, with a graph of the effective availability.

### Security

- API rate limiting, and an audit that fixed stored XSS.

## 2026-06-08/2026-06-14 Volunteers, Challonge and the REST API

### New

- On-site volunteer scheduling: positions, shifts (staggered ones too), availability, auto-fill and reminders.
- Challonge integration lets a bracket drive match scheduling.
- Match time suggestions use player availability and tournament hours.
- Discord roles sync to app roles at sign-in and whenever they change.
- A REST API with personal bearer tokens covers everything the web app can do.
- Triforce text submissions need a paid role, and a discovery page lists the tournaments that take them.

### Improved

- Open pages update live when a match changes, and Discord messages name the players, time and stage instead of a match ID.
- Profile settings save as you change them, notification preferences moved to the profile, and the theme follows your system's light or dark setting.
- The app got its brand colours.

## 2026-05-18/2026-05-31 Discord notifications and role-based access

### New

- Discord DMs for match changes, a way to watch any match, and match and crew acknowledgment from the DM.
- Triforce texts were ported from SahasrahBot.

### Improved

- Access is role-based instead of a single permission switch.
- Exported CSVs are protected against formula injection, and reports split into detail pages with a capacity forecast.
- Deleting a triforce text asks first, icon buttons have tooltips, and required fields are marked.

### Behind the scenes

- The database moved from MySQL to PostgreSQL, and the app ships as a container image.

## 2025-10-27/2025-11-02 Rebuilt on a cleaner structure

### Improved

- Admins and players got separate match dialogs, matches record finish rank and assigned station, and dialogs fit phones better.

### Fixed

- Timezone handling and sign-in protection were fixed.

### Behind the scenes

- The code was reorganized into page, service and data layers, the tables and dialogs were rebuilt, and all styling moved into one stylesheet.

## 2025-10-20/2025-10-26 Ready for SGL 2025

### New

- Seed rolling for several randomizers, one roll at a time per match, with the seed sent by DM and a roll button that works on a phone.
- The first REST API, reports and match durations.
- The schedule filters by tournament and stage, and a stage timeline shows what's on where.
- Admins can edit stages, record seats and finishes, and create a match before its players are known.
- Dark mode.

### Improved

- Crew signup works on phones and asks before undoing a signup.

## 2025-09-29/2025-10-19 Room usage and randomizers

### New

- Room usage tracking, a shared table view, and the first randomizer support.

## 2025-09-22/2025-09-28 Crew signups

### New

- Crew can sign up for matches and admins approve them.
- Results can be submitted from a QR code.

### Improved

- Profiles have pronouns, attendee pages are separate from admin pages, and tabs and the schedule work on phones.

### Behind the scenes

- The app runs in Docker and builds on GitHub Actions.

## 2025-09-15/2025-09-21 Matches and the admin page

### New

- Matches can be submitted and races finished, the admin page took shape, and seed rolling appears only when a seed roller is set.

## 2025-02-10/2025-09-07 The first prototype

### New

- Work started in February 2025 as SGLMan, importing SpeedGaming schedule data. Discord sign-in and the core data model followed in September.

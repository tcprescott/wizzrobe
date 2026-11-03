# Restreaming (Wizzrobe drives the broadcaster's OBS)

Status: Draft (phase 0 spike in progress) · Size: full · PRs: spike draft · Requested: 2026-11-02, Thomas

## Problem

A restream today needs a broadcaster who knows the layout, finds each runner's
feed, crops it by hand, levels the audio and switches scenes, all inside their
own OBS. Wizzrobe already knows the match, the runners, the stage, the layout
and the crew, but can only hand OBS a browser-source overlay and a scene
collection to import. Thomas wants the web app to be the puppet master: the
broadcaster lends their machine and OBS does the encoding, while Broadcast
Techs drive everything inside OBS from Wizzrobe, including previews, crops that
are remembered per runner, audio, and a real-time stream for commentators that
comes from the broadcaster's machine.

## Scope

In:
- A small installed agent (Windows and Mac) on the broadcaster's machine that
  connects out to Wizzrobe and drives the local OBS through obs-websocket
  (OBS 28+), running only allowlisted commands.
- Pairing a machine to a stage, and that stage's OBS health (connected, live,
  dropped frames, CPU, scene on air).
- Preview thumbnails of the program and each feed (source screenshots).
- Scenes built in OBS from the stage's stream layout (replacing the scene
  collection download), runner feeds filled when a match goes on the stage.
- Runner feeds from the runner's linked Twitch first; direct push to a
  Wizzrobe ingest later.
- Crop editor on a fresh screenshot; crops, feed source and audio level
  remembered per runner (and per game for crops).
- Audio: volume, mute, meters, follow-the-runner.
- Commentator stream: a second output from the broadcaster's OBS to a media
  server, played in the browser over WebRTC, for that match's approved
  commentators, staff, Broadcast Techs and the broadcaster. Its audio never
  includes commentary: the agent puts commentary inputs on the Twitch track
  only and game/runner audio on the commentator track too.
- Program control: scene switching, run of show, start/stop stream, Twitch
  title.
- Stream key brokering: the channel owner connects the Twitch channel to
  Wizzrobe once; at go-live Wizzrobe fetches the key and the agent sets it in
  OBS, and clears it when the stream stops.
- Hold screens so the stream never drops between matches: a stream layout
  (like the Break starter) with "Up next" (players, round, start time,
  countdown) and "Now playing", music from a community playlist uploaded to
  Wizzrobe that the hold screen's browser source plays on a loop, and an
  optional camera hole the agent fills with a camera the tech picks. The
  stage cuts to the hold screen by itself shortly after a match finishes
  (per-stage switch to make it manual); the tech cuts back. The upload
  screen states the community needs the rights to its tracks.
- Twitch chat bot on the same channel connection (EventSub chat in, Helix
  send out, on the bot service): a shared Wizzrobe bot account by default, a
  community's own bot account optional later. Built-in commands read the
  match on air: `!runners` (runners' Twitch links), `!comms`, `!tracker`,
  `!match` (tournament, round, seed code), `!next`, `!bracket`. Staff add
  custom text commands per community or event using the same template
  fields.
- Stream title from a template (stream layout fields, e.g.
  `{tournament} {round}: {players} | {event_name}`), set automatically when a
  match goes on air on the stage; a Broadcast Tech can override it for the
  rest of that match.

Out:
- Wizzrobe encoding or compositing video itself.
- Linux agent builds.
- Building any of the UI in the NiceGUI app (new UI goes in `web/`).

## Flow

To be written with the phase 1 mockups.

## Mockup pick

None yet. Phase 1 brings mockups (side-list layout: stages listed, the selected
stage's OBS beside it, tap-in on phone) before any screen is built.

## Data and permissions

- **Feature flag:** yes, its own (`FeatureFlag.RESTREAMING`, off by default),
  separate from Stream layouts.
- **Models (proposed):** `BroadcastAgent` (community-owned: name, token hash,
  paired stage, last seen, OBS version), per-runner `RunnerFeedProfile`
  (community-owned: user, game, crop, feed source, volume).
- **Who:** Staff and Broadcast Tech drive a stage's OBS; the broadcaster sees
  what's happening on their own machine and can stop the agent at any time.
- **Agent safety:** the obs-websocket password never leaves the machine; the
  agent refuses any request type not on its allowlist, any input kind outside
  browser/colour/media/text sources, any local file path, and any non-https
  browser-source URL. Every command is audited server-side. Stream service
  settings stay refused until phase 5, which allows them only for a Twitch
  ingest server and only from the go-live/stop flow. Camera capture inputs
  (`dshow_input` on Windows, `macos-avcapture`/`av_capture_input` on Mac)
  join the allowed kinds with the hold screen work; they can't read files.
- **Twitch stream keys:** a per-stage channel connection (Twitch OAuth with
  `channel:read:stream_key`), token stored encrypted; the key is fetched at
  go-live, never stored by Wizzrobe, never shown in the app, and sits in the
  broadcaster's OBS only while the stream runs.
- **Other surfaces:** REST for the app; the agent connection lives on the
  always-on bot service (the `bots` container); the agent reconnects with
  backoff anyway.

## PR plan

0. **Spike** (this PR, draft, not for merge as product): `agent/` Go module
   with the obs-websocket client, allowlist, outbound relay client with
   reconnect, a standalone spike relay + console, and a MediaMTX setup with a
   WHEP player to measure commentator latency. Write-up of results in
   `agent/SPIKE.md`.
1. Connect and watch: flag, `BroadcastAgent`, pairing, status, previews.
2. Scenes and feeds from stream layouts; Twitch runner feeds.
3. Crop and audio, with per-runner profiles.
4. Commentator stream (hosting picked from the spike numbers).
5. Program control and run of show, hold screens with playlist and camera,
   stream key brokering.
6. Twitch bot: title templates, built-in and custom chat commands.

## Acceptance

Phase 0 (spike):
- [ ] The agent authenticates to a real OBS 30+ on Windows and on Mac and runs
      scene list, scene switch, screenshot, crop, volume and record commands
      sent from the spike console.
- [ ] The agent refuses a non-allowlisted request, a local file path and a
      non-https browser source (unit tests).
- [ ] The agent reconnects to the relay after the relay drops it, and to OBS
      after OBS restarts.
- [ ] The commentator stream carries game audio and no commentary audio
      (agent sets input audio tracks; checked by ear on the real-OBS run).
- [ ] Commentator stream delay from OBS to a browser is measured through
      MediaMTX (and a managed service if tried), with numbers in SPIKE.md.

Later phases get their acceptance items with their mockups.

## Open questions

- Commentator stream hosting: own MediaMTX VM or a managed service (decided
  from the spike numbers).
- Agent packaging: tray app vs. console window; code-signing certificates.

## Decisions

- 2026-11-02 — Thomas picked runner feeds "Both, Twitch first": pull runners'
  Twitch now, add direct push later.
- 2026-11-02 — Thomas picked "An installed agent" over a dock inside OBS.
- 2026-11-02 — Thomas picked "Decide after the spike" for commentator stream
  hosting.
- 2026-11-02 — Thomas picked its own feature flag.
- 2026-11-02 — Thomas picked "Spike now, build later": build phases 1 to 5
  after the app rebuild's admin screens land.
- 2026-11-02 — Thomas picked Windows and Mac for the agent.
- 2026-11-02 — Thomas picked "Connect Twitch, push key" for stream keys: the
  agent sets the key at go-live and clears it after.
- 2026-11-02 — Thomas asked for a Twitch bot. Picked: shared Wizzrobe bot
  with a community's own bot optional; fixed commands plus staff custom
  commands; title set automatically on air, with an override.
- 2026-11-02 — Thomas asked for hold screens. Picked: music uploaded to
  Wizzrobe; the agent adds the camera the tech picks; auto-switch to the hold
  screen after a match, with a per-stage switch to turn that off.
- 2026-11-02 — Thomas: the commentator stream must exclude commentary audio
  (a requirement, not a default).

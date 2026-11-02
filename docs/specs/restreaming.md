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
  browser-source URL. Every command is audited server-side.
- **Other surfaces:** REST for the app; the agent connection lives on the
  always-on bot service (Cloud Run caps a websocket at the request timeout, so
  the agent reconnects with backoff anyway).

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
5. Program control and run of show.

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
- 2026-11-02 — Thomas: the commentator stream must exclude commentary audio
  (a requirement, not a default).

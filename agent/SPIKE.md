# Restreaming phase 0 spike

Spec: [docs/specs/restreaming.md](../docs/specs/restreaming.md). This folder
proves the risky parts before any product work: can a small agent on the
broadcaster's machine drive OBS for Wizzrobe, and how fast is a commentator
stream that comes from that machine.

## What's here

| Path | What |
|---|---|
| `obs/` | obs-websocket v5 client: hello, password auth, requests, events |
| `policy/` | The allowlist. Request types Wizzrobe may send; refuses local files, non-https browser sources, other input kinds, stream-key changes, vendor requests, screenshots to disk |
| `relay/` | Keeps both connections up: out to Wizzrobe (bearer token, pings every 20s, backoff to 30s) and to OBS (reconnects after OBS restarts). Runs commands through the policy |
| `cmd/wizzrobe-agent/` | The agent binary. Builds for Windows and Mac (amd64 + arm64) with no cgo |
| `obstest/` | A fake OBS for tests and the console's demo mode |
| `spike/relay/` | Stand-in for Wizzrobe: takes one agent and serves a console page (scene buttons, live program preview, crop, audio, any request, round-trip times) |
| `spike/commentator/` | MediaMTX (SRT in, WebRTC out), a WHEP viewer page and a latency clock |

## Results so far (cloud container, no real OBS)

- Agent ↔ fake OBS ↔ stand-in Wizzrobe: commands, events, refusals and both
  reconnects pass under `go test -race`.
- The obs-websocket auth matches the protocol docs' worked example.
- The agent cross-compiles for `windows/amd64`, `darwin/arm64` and
  `darwin/amd64` as single static binaries.
- Commentator path, localhost only: ffmpeg pushing H.264 + Opus over SRT into
  MediaMTX 1.9.3 plays in Chromium over WHEP. The burned-in timestamp and the
  wall clock were within a few hundred ms of each other, but ffmpeg's own
  pacing drifts by about that much, so this only shows the server side adds
  well under a second. The real number needs OBS and the internet (below).

## Real OBS on Windows (2026-11-04, NotMinime)

OBS 33.0.1, obs-websocket 5.7.6, Windows 11 25H2, agent and relay built with
Go 1.27 on the machine, everything on localhost.

- Auth from the OBS config password worked first time.
- Every command passed: GetVersion, GetStats, GetSceneList, CreateScene,
  CreateInput (`color_source_v3`), SetCurrentProgramScene, GetSourceScreenshot
  (960 px JPEG, ~10 KB, 7 ms), GetSceneItemList, SetSceneItemTransform crop
  (the second screenshot showed it), GetInputList, Get/SetInputVolume,
  SetInputMute, StartRecord/StopRecord. Round trips were 1–16 ms.
- Refused by the agent, never reaching OBS: SetStreamServiceSettings,
  CreateInput `image_source`, SetInputSettings with an http URL.
- Killing and restarting the relay: the agent was back 3.1 s after the kill.

## Still to prove on a real machine

1. Agent against real OBS on a Mac, when Mac support is picked up (Windows
   first, per Thomas 2026-11-04).
2. Commentator stream end to end from OBS over the internet, with the clock.
3. A connection held for hours through Cloud Run (it caps one websocket at
   the request timeout, so expect a clean reconnect at most every hour).

## Running it

### Agent and console

```sh
cd agent
SPIKE_TOKEN=pick-something go run ./spike/relay -listen 0.0.0.0:8787
# console: http://<relay host>:8787/?key=pick-something
```

On the OBS machine: OBS → Tools → WebSocket Server Settings → Enable, set a
password. Then:

```sh
# Windows (PowerShell)
$env:OBS_WEBSOCKET_PASSWORD = "<obs password>"
.\wizzrobe-agent.exe -server ws://<relay host>:8787/agent -token pick-something

# Mac
OBS_WEBSOCKET_PASSWORD='<obs password>' ./wizzrobe-agent -server ws://<relay host>:8787/agent -token pick-something
```

Build the binaries with
`GOOS=windows GOARCH=amd64 go build ./cmd/wizzrobe-agent` (or
`GOOS=darwin GOARCH=arm64`). They're unsigned, so Windows SmartScreen and
macOS Gatekeeper will warn; that's one of the phase 1 packaging jobs.

No OBS handy? `go run ./spike/relay -demo` starts a fake OBS and an
in-process agent.

### Commentator stream

1. On a test VM with a public IP: `cd agent/spike/commentator`, put the IP in
   `webrtcAdditionalHosts` and change the publish password in
   `mediamtx.yml`, then `docker compose up -d`. Open UDP 8890 and 8189 and
   TCP 8889.
2. In OBS: Settings → Output → Output Mode **Advanced** → Recording →
   Type **Custom Output (FFmpeg)** → **Output to URL**:
   `srt://<vm>:8890?streamid=publish:commentary:obs:<password>&pkt_size=1316`,
   container `mpegts`, video encoder x264 (or NVENC) with
   `tune=zerolatency bf=0`, keyframe interval 1–2 s, audio encoder
   **libopus** (browsers' WebRTC can't play AAC and MediaMTX doesn't
   transcode). Use audio track 2 for this output. In Edit → Advanced Audio
   Properties, untick track 2 on every commentary input (Discord, mics) and
   leave game audio on it, so commentators never hear commentary back. The
   agent can set this itself with `SetInputAudioTracks`, which is allowlisted.
3. Add `clock.html` as a Browser source in the program scene.
4. Start it from the console's **Start record** (that's the point: Wizzrobe
   starts it remotely).
5. Open `viewer.html?whep=http://<vm>:8889/commentary/whep` on the OBS
   machine, press Play, and screenshot both clocks. The gap is the delay.

## What the spike changes in the plan

- The agent can do more than a dock could: if Twitch pulls through a Media
  source turn out flaky, the agent can run Streamlink locally and hand OBS a
  local stream URL. That's not built here.
- Using the recording output for the commentator stream means the broadcaster
  can't also record locally. If that matters, the agent can start ffmpeg
  itself on an OBS virtual camera or a second output plugin instead.

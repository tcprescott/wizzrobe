# Production stack

Production as Docker containers, built from GHCR images and restarted by the
self-hosted GitHub runner on the server. Nothing here runs until someone presses
**Actions → Deploy production → Run workflow**; until then production keeps
deploying by git pull + restart.

| Container | Runs | Port |
|---|---|---|
| `api` | `main.py`: the website, REST, GraphQL, MCP, Discord DMs (REST sign-in), every background worker except the racetime room opener. Applies migrations on start. | 127.0.0.1:8000 |
| `bots` | `bots.py`: the Discord gateway (buttons, member updates, slash commands) and the racetime room bot. | none |
| `redis` | Sign-in sessions, so a redeploy doesn't sign anyone out. | none |
| `app` | The new app (`web/`), only from the cutover on (the `app` profile). | 127.0.0.1:8001 |

Postgres stays on the host. The API and the bots talk through it (the live
relay, `SEPARATE_BOTS=on` in both): a seed the API rolls still posts to its
racetime room, the platform "Restart bot" button reaches the bots, and what the
bots change refreshes open screens. Restarting either container leaves the
other running.

**Never two backends on one database.** Stop the old git-pull process before the
first deploy here, or both would send every DM, run every worker and hold the
Discord gateway twice.

## Moving production over (once)

Run on the production machine.

### 1. Stack folder

```bash
sudo mkdir -p /opt/wizzrobe
sudo cp <production's current .env> /opt/wizzrobe/production.env
sudo chmod 600 /opt/wizzrobe/production.env
```

Read `production.env.example` beside this file: drop `DB_HOST`,
`NICEGUI_REDIS_URL`, `DISCORD_GATEWAY` from the copy (compose sets them), and
keep `RACETIME_BOT_ENABLED` as production has it, since the bots container reads
it. A different folder works: set the repository variable `PROD_STACK_DIR`.

### 2. Let the stack reach the host's Postgres

The compose file pins the stack's network to `172.30.43.0/24` (beta uses
`.42`). Add to `pg_hba.conf`, with production's database and user names:

```
host  wizzrobe  wizzrobe  172.30.43.0/24  scram-sha-256
```

`listen_addresses` already covers Docker if beta is set up; otherwise see step 1
of [the beta README](../beta/README.md#1-database-on-the-hosts-postgres). Reload
Postgres.

### 3. The runner

The runner beta uses works for production too. Repo → Settings → Actions →
Runners → that runner → add the label `wizzrobe-prod`. Then Settings →
Environments → **production** (created on the first run, or create it now):
limit it to the `main` branch and add yourself as a required reviewer, so the
deploy job waits for your OK.

### 4. Reverse proxy

Point production's existing proxy at `127.0.0.1:8000` (the port the git-pull
process used, so it's likely unchanged). At the cutover, add the app's name on
`127.0.0.1:8001` and set the repository variable `PROD_API_ORIGIN` to the API's
public address before building the app image.

### 5. Switch

1. Stop the old process (however it runs today: its compose stack, systemd unit
   or screen session) and keep it from starting on boot.
2. Actions → **Deploy production** → Run workflow on `main`, app box unticked.
   It runs the full test suite, builds the image (about 10 minutes the first
   time), waits for your approval, then starts the stack.
3. Check: `docker compose -f /opt/wizzrobe/docker-compose.yml ps` shows `api`
   and `bots` healthy, the site loads, a Discord button works, and Platform →
   Racetime bots shows the bots connected.

The new Redis starts empty, so everyone signs in again once. To roll back, stop
this stack and start the old process again; the database is the same.

## Day to day

```bash
cd /opt/wizzrobe
docker compose ps
docker compose logs -f api
docker compose logs -f bots
docker compose restart bots      # the API stays up
```

Each deploy pins `.env` in the folder to the exact build (`WIZZROBE_TAG`), so a
manual `docker compose up -d` restarts the same version. To go back a version,
run the workflow on an older commit, or set `WIZZROBE_TAG=prod-<sha>` in `.env`
and `docker compose up -d`.

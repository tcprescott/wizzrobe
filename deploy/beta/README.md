# Beta stack

A separate copy of Wizzrobe for trying the new app (`web/`) before cutover. It
runs on the production machine but shares nothing with production except the
Postgres server, where it has its own database and login.

| Address | Serves | Container (host port) |
|---|---|---|
| `https://wizzrobe-beta.synack.live` | the new app | `app` (127.0.0.1:8101) |
| `https://wizzrobe-beta-api.synack.live` | API, sign-in, and the current site on beta data | `api` (127.0.0.1:8100) |

- **Data:** the dev seed (`scripts/seed_dev.py`), re-run after every deploy. It is
  idempotent, so what testers add stays and new features' fixtures appear.
- **Sign-in:** real Discord sign-in through production's Discord application.
  People arrive with no roles; grant them on beta (Admin → Members, or
  `/platform`).
- **Nothing leaves it:** `SANDBOX_MODE=on` keeps Discord signed out (no DMs, no
  bot) and stops email, Telegram, web and native push and both kinds of webhook
  at their last step. The racetime bot, SpeedGaming sync and Discord-events
  workers stay off. The seed leaves out every fixture whose secret is in this
  repo (dev password, bearer and room tokens, OAuth app secret), since the
  address is public.
- **Deploys:** every push to main builds both images to GHCR (`:beta`) and the
  self-hosted runner on the server pulls them, restarts the stack and re-seeds.
  Actions → **Deploy beta** → Run workflow deploys any branch by hand.
- **Never two backends on one database.** This stack's database is its own; don't
  point it at production's.

## One-time setup

Run these on the production machine unless a step says otherwise.

### 1. Database on the host's Postgres

```sql
-- as the postgres superuser
CREATE ROLE wizzrobe_beta LOGIN PASSWORD '<a new password>';
CREATE DATABASE wizzrobe_beta OWNER wizzrobe_beta;
```

Let the stack's Docker network in. The compose file pins it to `172.30.42.0/24`:

- `postgresql.conf`: `listen_addresses = '*'` (or add the Docker bridge address,
  usually `172.17.0.1`, beside `localhost`). `*` is safest across reboots, because
  Postgres often starts before Docker has created the bridge.
- `pg_hba.conf`:
  `host  wizzrobe_beta  wizzrobe_beta  172.30.42.0/24  scram-sha-256`
- Restart Postgres, and make sure the firewall still blocks 5432 from outside.

### 2. Stack folder

```bash
sudo mkdir -p /opt/wizzrobe-beta
sudo cp deploy/beta/beta.env.example /opt/wizzrobe-beta/beta.env   # from a checkout of the repo
sudo chmod 600 /opt/wizzrobe-beta/beta.env
```

Fill in `beta.env`: the database password, a new `STORAGE_SECRET`, and
production's `DISCORD_CLIENT_ID` and `DISCORD_CLIENT_SECRET`. Leave out every
other credential. A different folder works too: set the repository variable
`BETA_STACK_DIR` to it.

### 3. DNS, TLS and the reverse proxy

Point both names at the machine and proxy them through whatever already fronts
production, with TLS. For nginx:

```nginx
server {
    server_name wizzrobe-beta.synack.live;
    location / { proxy_pass http://127.0.0.1:8101; proxy_set_header Host $host; }
}
server {
    server_name wizzrobe-beta-api.synack.live;
    client_max_body_size 25m;
    location / {
        proxy_pass http://127.0.0.1:8100;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;     # NiceGUI's websocket
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

Caddy is two lines: `wizzrobe-beta.synack.live { reverse_proxy 127.0.0.1:8101 }`
and the same for the API name on 8100.

### 4. Discord

In the Discord developer portal, open production's application → OAuth2 →
Redirects and add `https://wizzrobe-beta-api.synack.live/oauth/callback`. No bot
token goes to beta.

### 5. The GitHub runner

Repo → Settings → Actions → Runners → **New self-hosted runner** (Linux) and run
the commands it shows, as a user in the `docker` group (not root). When
`config.sh` asks for labels, add `wizzrobe-beta`. Then install it as a service
so it survives reboots:

```bash
sudo ./svc.sh install <that user>
sudo ./svc.sh start
```

The repository is public, and a self-hosted runner runs whatever job reaches it.
Before registering it, set Settings → Actions → General → **Fork pull request
workflows from outside collaborators** to *Require approval for all outside
collaborators*, so a stranger's pull request can't run a job on your server.
The deploy job also only runs from this repository, and its `beta` environment
(created automatically on the first run) can be limited to the `main` branch
under Settings → Environments.

### 6. First deploy and your admin account

1. Actions → **Deploy beta** → Run workflow (main). The first run builds both
   images (about 10 minutes), migrates the empty database and seeds it.
2. Sign in once at `https://wizzrobe-beta-api.synack.live` with Discord, so your
   account exists on beta.
3. Make yourself platform admin there:
   `docker compose -f /opt/wizzrobe-beta/docker-compose.yml exec api python scripts/grant_super_admin.py <your Discord id>`
4. Open `https://wizzrobe-beta.synack.live` and sign in.

## Day to day

```bash
cd /opt/wizzrobe-beta
docker compose ps
docker compose logs -f api
docker compose exec api python scripts/seed_dev.py   # re-seed by hand
```

To start over with fresh data, drop and recreate the `wizzrobe_beta` database
and run the workflow again.

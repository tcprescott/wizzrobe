# Cloud Run deploy

Config for running Wizzrobe on Google Cloud Run (step 5 of
[the mobile app spec](../../docs/specs/mobile-app.md)). Today's production server
doesn't use any of this: it stays a `git pull` and restart until the switch-over.

One `<environment>.toml` per environment describes everything; `staging.toml` is
the only one so far. `scripts/cloudrun.py` turns it into:

| What | Name | Notes |
|---|---|---|
| Cloud Run service | `<name>-api` | `./start.sh prod` (`main.py`). `WORKER_MODE=scheduled`, `DISCORD_GATEWAY=off`, `RACETIME_BOT_ENABLED=off`. Public. Session affinity on, because NiceGUI pages hold a websocket on one instance until the switch-over. |
| Cloud Run service | `<name>-bots` | `./start.sh bots` (`bots.py`): Discord gateway and racetime room bot. Exactly one instance, CPU always on, internal ingress. |
| Cloud Scheduler job | `<name>-<worker>` | One per `Placement.SCHEDULED` worker in `application/worker_registry.py`, cron from `scheduler_cron`. Signed with an OIDC token for the invoker account. A job whose worker is gone is deleted on the next apply. |
| Cloud Tasks queue | `<name>-seed-roll`, `<name>-data-export` | One per task-backed worker (`task_handler` on its `WorkerSpec`). Exports dispatch one at a time. |

Both services run the same image, with a Cloud SQL Auth Proxy sidecar on
`127.0.0.1:5432` when `cloudsql_instance` is set. The API applies migrations at
boot under the advisory lock, as it does today; the bot service only connects.
CPU stays allocated outside requests on both, because DMs, event-bus subscribers
and webhook sends finish after the response that caused them. With
`min_instances = 0` the API still shuts down when idle.

## Commands

```bash
# Print the service YAML and every gcloud command, touching nothing:
poetry run python scripts/cloudrun.py plan deploy/cloudrun/staging.toml --image <image>:<tag>

# Do it (refuses while the config has TODO values):
poetry run python scripts/cloudrun.py apply deploy/cloudrun/staging.toml --image <image>:<tag> --yes
```

The **Deploy to Cloud Run** workflow (Actions tab, Run workflow) runs the test
suite, builds the image, pushes it to Artifact Registry tagged with the commit,
and runs `apply`. It never runs on its own.

## One-time setup (not done yet)

Nothing below exists yet. Run it once per environment, then replace the TODO
values in the toml. `P` is the project id, `R` the region.

1. **APIs:** `gcloud services enable run.googleapis.com cloudscheduler.googleapis.com cloudtasks.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com --project P`
2. **Image registry:** `gcloud artifacts repositories create wizzrobe --repository-format=docker --location R --project P`
3. **Database:** a Cloud SQL Postgres 16 instance, a `wizzrobe` database and user. Staging runs against a copy of production data: restore a `pg_dump` from the production server into it ([backup and restore](../../docs/deployment.md#backup-and-restore)).
4. **Service accounts:**
   - `wizzrobe-run`, what both services run as: `roles/cloudsql.client`, `roles/secretmanager.secretAccessor`, `roles/cloudtasks.enqueuer`, and `roles/iam.serviceAccountUser` **on `wizzrobe-invoker`** (so the tasks it creates can carry that account's token).
   - `wizzrobe-invoker`, what Scheduler and Cloud Tasks sign their calls as. `/internal/*` checks for this exact account, so it needs no role on the service (the API is public anyway).
   - `wizzrobe-deploy`, what the GitHub workflow signs in as: `roles/run.admin`, `roles/cloudscheduler.admin`, `roles/cloudtasks.queueAdmin`, `roles/artifactregistry.writer`, and `roles/iam.serviceAccountUser` on `wizzrobe-run` and `wizzrobe-invoker`.
5. **Secrets:** one Secret Manager secret per `[secrets]` line in the toml, with the value from the production `.env` (or a staging Discord application's, so staging doesn't answer for the real bot).
6. **GitHub sign-in:** a Workload Identity pool and provider for `token.actions.githubusercontent.com` limited to `repo:tcprescott/wizzrobe`, allowed to impersonate `wizzrobe-deploy`. Then set the repository variables `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_DEPLOY_SERVICE_ACCOUNT`, and create a `staging` environment in the repository settings (add a required reviewer there if you want a second click).
7. **First deploy**, then put the API's URL (or a custom domain mapped to it) in `api_url` and `BASE_URL` and deploy again, so the Scheduler jobs and tasks target it.
8. **Discord:** for the staging application, set the Interactions Endpoint URL to `<api_url>/discord/interactions`.

## Things to know

- **Client IPs.** Cloud Run appends the caller's address to `X-Forwarded-For`, so
  the config sets `TRUST_PROXY_FORWARDED_FOR=last` and the rate limiter reads the
  last entry. Put a Google load balancer in front and the last entry becomes the
  balancer's own address; change the setting then.
- **Sentry environment.** `start.sh prod` forces `ENVIRONMENT=production`, so
  staging errors arrive tagged `production`. Give staging its own Sentry DSN.
- **Per-instance loops.** In scheduled mode the API still runs the `bot` and
  `per_process` placements in-process (`service_health`, `telegram_bot`, the
  metrics sampler, the watchdog). They stop when an instance scales to zero.
  Set `TELEGRAM_WEBHOOK_SECRET` so Telegram uses the webhook rather than polling.
- **Sessions.** Without `NICEGUI_REDIS_URL` NiceGUI keeps sign-ins in a file on
  the instance, so a new revision signs everyone out. Fine for staging; add a
  Redis (Memorystore) URL if that gets in the way.

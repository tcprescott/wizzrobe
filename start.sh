#!/usr/bin/env bash

# use path of this example as working directory; enables starting this script from anywhere
cd "$(dirname "$0")"

# load .env file if it exists (auto-export each assignment; avoids word-splitting)
#
# This is the only place .env is sourced. Running `poetry run uvicorn main:app`
# by hand therefore starts a server with none of it — which fails on the Discord
# token rather than falling back to MOCK_DISCORD, because the mock is a .env
# setting. Always start through this script.
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

# Files the running app writes into its own directory. Watching them means the
# app reloads itself: `.nicegui/` gets a storage-user-<id>.json per session, so
# every single login restarts the server. Left unexcluded that produced a reload
# every few seconds during a validation run, which orphans the timers on any
# open page and fills the log with teardown noise.
# uvicorn waits for every open request before running the app's shutdown, and
# with no deadline one request that never ends (a stream, a hung upstream call)
# holds the process until systemd's stop timeout (90s by default) SIGKILLs it,
# skipping the lifespan shutdown entirely. This deadline cancels what is left
# and lets shutdown run; middleware/inflight_requests.py logs what it cancelled.
GRACEFUL_SHUTDOWN=(--timeout-graceful-shutdown 10)

RELOAD_EXCLUDES=(
    --reload-exclude '.nicegui/*'
    --reload-exclude '*.log'
    --reload-exclude '.pytest_cache/*'
)

if [ "$1" = "stop" ]; then
    # Stop whatever this script started.
    #
    # Use this instead of `pkill -f "uvicorn main:app"` from a shell. `pkill -f`
    # matches against *full command lines*, and the shell running your pkill has
    # the pattern in its own — including inside a heredoc that merely mentions
    # it — so the command reliably kills itself before reaching the server (exit
    # 143/144, no output, server still up). Keeping the pattern inside this file
    # means the caller's command line is just `./start.sh stop`, which cannot
    # match.
    pattern='uvicorn main:app'
    pids=$(pgrep -f "$pattern" || true)
    if [ -z "$pids" ]; then
        echo "No uvicorn server running."
        exit 0
    fi
    # shellcheck disable=SC2086
    kill $pids 2>/dev/null
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        pgrep -f "$pattern" >/dev/null 2>&1 || { echo "Stopped."; exit 0; }
        sleep 1
    done
    echo "Still running after 10s; sending SIGKILL."
    # shellcheck disable=SC2086
    kill -9 $(pgrep -f "$pattern") 2>/dev/null
    exit 0
elif [ "$1" = "prod" ]; then
    echo "Starting Uvicorn server in production mode..."
    # Force production security posture (HSTS, strict
    # STORAGE_SECRET/DB-credential checks, mock-mode refusal) rather than
    # depending on the operator's .env to set it.
    export ENVIRONMENT=production
    # exec: as the container's PID 1, bash never forwards SIGTERM, so without it
    # every stop was a SIGKILL that skipped shutdown and dropped queued DMs.
    exec poetry run uvicorn main:app "${GRACEFUL_SHUTDOWN[@]}" --workers 1 --log-level info --port 8000 --host 0.0.0.0
elif [ "$1" = "bots" ]; then
    # The always-on bot service (bots.py) beside an API started with
    # DISCORD_GATEWAY=off and RACETIME_BOT_ENABLED=off. Cloud Run's bots service
    # runs this; today's single-process deploy never does.
    echo "Starting the bot service in production mode..."
    export ENVIRONMENT=production
    exec poetry run uvicorn bots:app "${GRACEFUL_SHUTDOWN[@]}" --workers 1 --log-level info --port "${PORT:-8000}" --host 0.0.0.0
elif [ "$1" = "dev" ]; then
    echo "Starting Uvicorn server in development mode..."
    poetry run uvicorn main:app "${GRACEFUL_SHUTDOWN[@]}" --reload "${RELOAD_EXCLUDES[@]}" --log-level info --port 8000
elif [ "$1" = "mock" ]; then
    echo "Starting Uvicorn server in development mode with mocked Discord, seedgen, Challonge, Twitch, racetime.gg, and Matcherino..."
    # Mock bypasses are refused unless we're explicitly not in production.
    export ENVIRONMENT=development
    export MOCK_DISCORD=true
    export MOCK_SEEDGEN=true
    export MOCK_CHALLONGE=true
    export MOCK_TWITCH=true
    export MOCK_RACETIME=true
    export MOCK_MATCHERINO=true
    export MOCK_STARTGG=true
    poetry run uvicorn main:app "${GRACEFUL_SHUTDOWN[@]}" --reload "${RELOAD_EXCLUDES[@]}" --log-level info --port 8000
elif [ "$1" = "validate" ]; then
    # Same environment as `mock`, but **no `--reload` at all**. This is the mode
    # to boot for /ui-validation, the flag sweep, or any Playwright run.
    #
    # A reload mid-run tears down every open page, so a screenshot can catch a
    # half-built DOM and the server log fills with teardown tracebacks that look
    # like findings. Excluding the noisy paths above helps; not watching at all
    # is the only way to be sure the log slice you are reading is about your
    # change. Restart it by hand after an edit.
    echo "Starting Uvicorn server for validation (mocked services, no auto-reload)..."
    export ENVIRONMENT=development
    export MOCK_DISCORD=true
    export MOCK_SEEDGEN=true
    export MOCK_CHALLONGE=true
    export MOCK_TWITCH=true
    export MOCK_RACETIME=true
    export MOCK_MATCHERINO=true
    export MOCK_STARTGG=true
    poetry run uvicorn main:app "${GRACEFUL_SHUTDOWN[@]}" --log-level info --port 8000
else
    echo "Invalid parameter. Use 'prod', 'bots', 'dev', 'mock', 'validate', or 'stop'."
    exit 1
fi

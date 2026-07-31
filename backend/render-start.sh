#!/bin/sh
# Run the API and one RQ worker in a single container.
#
# **This is a deviation, and it is forced.** PLAN.md 9.1 puts the worker in its
# own container, which is right: a job runs in a forked child with its own
# memory ceiling, and scaling workers is then just running more of them.
# Render's free tier has no free Background Worker -- they start at $7/mo -- so
# on that tier the only way to have a queue consumer at all is to run it beside
# the web process. Everywhere else, keep them separate.
#
# What this costs, stated plainly:
#
#   - The API and the worker share 512 MB and 0.1 CPU. A job that would take
#     11 s on a dedicated box takes longer here, and it competes with request
#     handling while it runs.
#   - Render stops a free web service after 15 minutes with no inbound HTTP,
#     and a stopped service has no worker. Submitting a job is itself a
#     request, so it wakes the service; the UI then polls every 2 s (PLAN.md
#     7.4) which keeps it awake for the duration. A job submitted by a client
#     that then goes away can sit queued until the next request arrives.
#   - The container's disk is ephemeral. Outputs written locally are gone after
#     a redeploy or a spin-down, so a download link is good until then. Object
#     storage fixes this and nothing else does; see docs/deploy-render.md.
#
# **Migrations run here, and that is not belt-and-braces.** This script first
# shipped assuming backend/entrypoint.sh had already applied them, because the
# Dockerfile sets it as ENTRYPOINT and Render's `dockerCommand` is documented as
# overriding CMD. It does not behave that way: dockerCommand replaces the
# entrypoint, so entrypoint.sh never ran, `alembic upgrade head` never ran, and
# the service came up healthy with no schema. /health passed, scene search
# passed -- it falls back to querying STAC directly when the cache is
# unavailable -- and the failure surfaced only on the first job submission, as
# `relation "jobs" does not exist` behind a 500.
#
# `alembic upgrade head` is idempotent, so running it here is safe whether or
# not the entrypoint also ran. There is no race: this container is the only
# thing that migrates, because the worker is inside it rather than beside it.
set -e

if [ -n "$BHOOMI_DATABASE_URL" ]; then
    echo "render-start: applying migrations"
    alembic upgrade head
else
    echo "render-start: BHOOMI_DATABASE_URL unset -- no scene cache, and jobs will fail"
fi

# Restart the worker if it dies rather than leaving a live API with a queue
# nobody is draining -- which looks, from the browser, exactly like a job that
# is merely slow. Backgrounded so uvicorn can hold PID 1's foreground: when the
# API exits, the container exits and Render restarts the whole thing.
(
    while true; do
        python -m backend.queue.worker || \
            echo "render-start: worker exited ($?), restarting in 2s" >&2
        sleep 2
    done
) &

# Render assigns the port and expects the service to bind it. Falling back to
# 8000 keeps this script usable locally.
exec uvicorn backend.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"

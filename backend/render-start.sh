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
# Migrations are not run here -- the image's entrypoint has already done that
# by the time this script starts, and it is the same single container, so there
# is no race of the kind entrypoint.sh warns about.
set -e

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

#!/bin/sh
# Bring the schema up to date, then hand off to the real command.
#
# Migrating on start is safe here because compose runs exactly one backend. With
# more than one replica this needs to move to a one-shot job instead: alembic
# takes no lock of its own, so two containers starting together would both try
# to create the same table.
#
# The worker shares this image and sets BHOOMI_SKIP_MIGRATIONS=1: it starts
# after the backend is healthy, so the schema is already current, and two
# containers racing to create the same table is the exact failure the note
# above describes.
set -e

if [ -n "$BHOOMI_SKIP_MIGRATIONS" ]; then
    echo "entrypoint: skipping migrations (BHOOMI_SKIP_MIGRATIONS set)"
elif [ -n "$BHOOMI_DATABASE_URL" ]; then
    echo "entrypoint: applying migrations"
    alembic upgrade head
else
    echo "entrypoint: BHOOMI_DATABASE_URL unset -- running without the scene cache"
fi

exec "$@"

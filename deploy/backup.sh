#!/bin/sh
set -eu
umask 077
ACTI_ENV_FILE=${ACTI_ENV_FILE:-/etc/acti-v3/runtime.env}
set -a; . "$ACTI_ENV_FILE"; set +a
"$ACTI_RELEASE_DIR/deploy/stack.sh" preflight
mkdir -p /srv/acti/postgresql/backups
backup="/srv/acti/postgresql/backups/acti-$(date -u +%Y%m%dT%H%M%SZ).dump"
temporary="$backup.partial"
trap 'rm -f "$temporary"' EXIT HUP INT TERM
docker compose --env-file "$ACTI_RELEASE_DIR/deploy/images.lock.env" --env-file "$ACTI_ENV_FILE" \
  -f "$ACTI_RELEASE_DIR/deploy/compose.yaml" exec -T postgres pg_dump -U postgres -Fc acti > "$temporary"
mv "$temporary" "$backup"
printf '%s\n' "$backup"

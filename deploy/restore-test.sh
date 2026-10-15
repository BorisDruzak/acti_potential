#!/bin/sh
set -eu
ACTI_ENV_FILE=${ACTI_ENV_FILE:-/etc/acti-v3/runtime.env}
set -a; . "$ACTI_ENV_FILE"; set +a
"$ACTI_RELEASE_DIR/deploy/stack.sh" preflight
test "$#" -eq 1 && test -f "$1" || { printf 'Pass one existing dump file\n' >&2; exit 2; }
compose() { docker compose --env-file "$ACTI_RELEASE_DIR/deploy/images.lock.env" --env-file "$ACTI_ENV_FILE" -f "$ACTI_RELEASE_DIR/deploy/compose.yaml" "$@"; }
# Fixed disposable name: caller cannot redirect restore into the live database.
compose exec -T postgres createdb -U postgres -T template0 acti_restore_test
trap 'compose exec -T postgres dropdb -U postgres acti_restore_test' EXIT HUP INT TERM
compose exec -T postgres pg_restore -U postgres --no-owner --exit-on-error -d acti_restore_test < "$1"
for database in acti acti_restore_test; do
  compose exec -T postgres psql -U postgres -d "$database" -Atc "SELECT version_num FROM alembic_version; SELECT md5(string_agg(id::text, ',' ORDER BY id)) FROM users; SELECT count(*) FROM audit_events;"
done

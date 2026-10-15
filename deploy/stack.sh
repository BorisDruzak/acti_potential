#!/bin/sh
set -eu
ACTI_ENV_FILE=${ACTI_ENV_FILE:-/etc/acti-v3/runtime.env}
set -a
. "$ACTI_ENV_FILE"
set +a
: "${ACTI_RELEASE_DIR:?Set the committed release directory}"
compose() {
    docker compose --env-file "$ACTI_RELEASE_DIR/deploy/images.lock.env" --env-file "$ACTI_ENV_FILE" \
        -f "$ACTI_RELEASE_DIR/deploy/compose.yaml" "$@"
}
preflight() { python3 "$ACTI_RELEASE_DIR/deploy/preflight.py"; }
case "${1:-status}" in
    preflight) preflight ;;
    up) preflight; compose up -d --wait --wait-timeout 180 ;;
    down) compose down ;;
    status) compose ps --all ;;
    *) printf 'Usage: stack.sh preflight|up|down|status\n' >&2; exit 2 ;;
esac

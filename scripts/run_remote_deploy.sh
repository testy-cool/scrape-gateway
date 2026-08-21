#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "usage: run_remote_deploy.sh DIGEST [VERSION]" >&2
  exit 2
fi

digest=$1
version=${2:-}
image=$(python3 scripts/validate_image_digest.py "$digest")
image=${image%@*}

: "${PRODUCTION_SSH_HOST:?required}"
: "${PRODUCTION_SSH_PORT:?required}"
: "${PRODUCTION_SSH_USER:?required}"
: "${PRODUCTION_SSH_KEY:?required}"
: "${PRODUCTION_DEPLOY_DIR:?required}"

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT INT TERM
key_file="$tmp_dir/key"
known_hosts="$tmp_dir/known_hosts"
printf '%s\n' "$PRODUCTION_SSH_KEY" >"$key_file"
chmod 600 "$key_file"
ssh-keyscan -p "$PRODUCTION_SSH_PORT" "$PRODUCTION_SSH_HOST" >"$known_hosts" 2>/dev/null
test -s "$known_hosts"

ssh \
  -i "$key_file" \
  -o BatchMode=yes \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=yes \
  -o "UserKnownHostsFile=$known_hosts" \
  -p "$PRODUCTION_SSH_PORT" \
  "$PRODUCTION_SSH_USER@$PRODUCTION_SSH_HOST" \
  sh -s -- "$PRODUCTION_DEPLOY_DIR" "$image" "$digest" "$version" \
  <scripts/deploy_image.sh

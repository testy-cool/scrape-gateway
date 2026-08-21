#!/bin/sh
set -eu

if [ "$#" -lt 3 ] || [ "$#" -gt 4 ]; then
  echo "usage: deploy_image.sh DEPLOY_DIR IMAGE DIGEST [VERSION]" >&2
  exit 2
fi

deploy_dir=$1
image=$2
digest=$3
expected_version=${4:-}
service=sgw
compose_file=compose.production.yml
image_env=.sgw-image.env
health_attempts=${SGW_HEALTH_ATTEMPTS:-30}
health_interval=${SGW_HEALTH_INTERVAL:-2}

echo "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' || {
  echo "invalid image digest" >&2
  exit 2
}

cd "$deploy_dir"
test -f "$compose_file"

compose() {
  docker compose --env-file "$image_env" -f "$compose_file" "$@"
}

write_reference() {
  target_digest=$1
  tmp_file=$(mktemp ".sgw-image.env.XXXXXX")
  printf 'SGW_IMAGE=%s@%s\n' "$image" "$target_digest" >"$tmp_file"
  mv "$tmp_file" "$image_env"
}

configured_digest() {
  sed -n "s|^SGW_IMAGE=$image@\(sha256:[0-9a-f]\{64\}\)$|\1|p" "$image_env" 2>/dev/null | head -1
}

verify_running() {
  target_digest=$1
  target_reference="$image@$target_digest"
  container_id=$(compose ps -q "$service")
  test -n "$container_id" || return 1

  attempt=0
  while [ "$attempt" -lt "$health_attempts" ]; do
    health=$(docker inspect --format '{{.State.Health.Status}}' "$container_id" 2>/dev/null || true)
    if [ "$health" = healthy ]; then
      break
    fi
    attempt=$((attempt + 1))
    sleep "$health_interval"
  done
  test "${health:-}" = healthy || return 1

  test "$(docker inspect --format '{{.Config.Image}}' "$container_id")" = "$target_reference" || return 1
  image_id=$(docker inspect --format '{{.Image}}' "$container_id")
  docker image inspect "$image_id" --format '{{range .RepoDigests}}{{println .}}{{end}}' \
    | grep -Fx "$target_reference" >/dev/null || return 1

  running_version=$(
    docker image inspect "$image_id" \
      --format '{{ index .Config.Labels "org.opencontainers.image.version" }}'
  )
  if [ -n "$expected_version" ]; then
    test "$running_version" = "$expected_version" || return 1
  else
    echo "$running_version" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' || return 1
  fi
  echo "deployed_digest=$target_digest version=$running_version"
}

deploy_digest() {
  target_digest=$1
  write_reference "$target_digest" || return 1
  compose pull "$service" || return 1
  compose up -d --no-deps "$service" || return 1
  verify_running "$target_digest"
}

old_digest=$(configured_digest || true)
if [ -n "$old_digest" ]; then
  echo "previous_digest=$old_digest"
else
  echo "previous_digest=none"
fi

if deploy_digest "$digest"; then
  echo "deployment=healthy"
  exit 0
fi

echo "deployment failed; restoring previous digest" >&2
if [ -n "$old_digest" ] && [ "$old_digest" != "$digest" ] && deploy_digest "$old_digest"; then
  echo "rollback=restored digest=$old_digest"
else
  echo "rollback=failed" >&2
fi
exit 1

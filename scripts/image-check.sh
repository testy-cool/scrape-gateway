#!/bin/sh
set -eu

image=${SGW_IMAGE_CHECK_TAG:-scrape-gateway:image-check}
container="sgw-image-check-$$"
tmp_dir=$(mktemp -d)
cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
}
trap cleanup EXIT INT TERM

version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
revision=$(git rev-parse HEAD)

docker build \
  --label "org.opencontainers.image.version=$version" \
  --label "org.opencontainers.image.revision=$revision" \
  --tag "$image" .

test "$(docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.version" }}')" = "$version"
test "$(docker image inspect "$image" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')" = "$revision"

docker run --detach --name "$container" --publish 127.0.0.1::8100 "$image" >/dev/null
port=$(docker port "$container" 8100/tcp | sed 's/.*://')

attempt=0
while [ "$attempt" -lt 30 ]; do
  if curl --fail --silent --show-error "http://127.0.0.1:$port/health" >"$tmp_dir/health.json"; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

test -s "$tmp_dir/health.json"
curl --fail --silent --show-error "http://127.0.0.1:$port/api/status" >"$tmp_dir/status.json"
STATUS_PATH="$tmp_dir/status.json" EXPECTED_VERSION="$version" python3 - <<'PY'
import json
import os

with open(os.environ["STATUS_PATH"], encoding="utf-8") as handle:
    status = json.load(handle)
assert status["version"] == os.environ["EXPECTED_VERSION"], status
PY

echo "image_check=ok version=$version revision=$revision"

#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: verify_package_artifacts.sh DIST_DIR VERSION" >&2
  exit 2
fi

dist_dir=$1
project_version=$2
uv=${UV:-uv}
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

set -- "$dist_dir"/*.whl "$dist_dir"/*.tar.gz
if [ "$#" -ne 2 ] || [ ! -f "$1" ] || [ ! -f "$2" ]; then
  echo "expected exactly one wheel and one source distribution" >&2
  exit 1
fi

index=0
for artifact in "$@"; do
  index=$((index + 1))
  environment="$tmp_dir/venv-$index"
  "$uv" venv --quiet "$environment"
  "$uv" pip install --quiet --python "$environment/bin/python" "$artifact"
  installed_version=$(
    "$environment/bin/python" -c 'from importlib.metadata import version; print(version("scrape-gateway"))'
  )
  test "$installed_version" = "$project_version"
  "$environment/bin/sgw" --help >/dev/null
done

echo "package_artifacts=ok version=$project_version artifacts=2"

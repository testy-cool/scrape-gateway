#!/bin/sh
set -eu

UV=${UV:-uv}
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

project_version=$(
  "$UV" run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
)

"$UV" build --out-dir "$tmp_dir/dist"
UV="$UV" scripts/verify_package_artifacts.sh "$tmp_dir/dist" "$project_version"

echo "package_check=ok version=$project_version artifacts=2"

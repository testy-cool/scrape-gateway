#!/bin/sh
set -eu

UV=${UV:-uv}
tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT INT TERM

project_version=$(
  "$UV" run python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
)

"$UV" build --out-dir "$tmp_dir/dist"

set -- "$tmp_dir"/dist/*.whl "$tmp_dir"/dist/*.tar.gz
if [ "$#" -ne 2 ]; then
  echo "expected exactly one wheel and one source distribution" >&2
  exit 1
fi

index=0
for artifact in "$@"; do
  index=$((index + 1))
  environment="$tmp_dir/venv-$index"
  "$UV" venv --quiet "$environment"
  "$UV" pip install --quiet --python "$environment/bin/python" "$artifact"
  installed_version=$(
    "$environment/bin/python" -c 'from importlib.metadata import version; print(version("scrape-gateway"))'
  )
  test "$installed_version" = "$project_version"
  "$environment/bin/sgw" --help >/dev/null
done

echo "package_check=ok version=$project_version artifacts=2"

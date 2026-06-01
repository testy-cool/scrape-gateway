#!/usr/bin/env bash
set -euo pipefail

SSH_HOST="${SGW_MCP_SSH_HOST:-coolify-gen2}"
RESOURCE_LABEL="${SGW_MCP_RESOURCE_LABEL:-sgw-mcp}"
MCP_URL="${SGW_MCP_URL:-https://sgw.voidxd.cloud/mcp}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

need curl
need python3
need ssh

echo "Checking unauthenticated endpoint..."
status="$(
  curl -sS -o /tmp/sgw-mcp-smoke-unauth.json -w '%{http_code}' \
    -H 'Accept: application/json, text/event-stream' \
    "$MCP_URL"
)"

if [ "$status" != "401" ]; then
  echo "expected unauthenticated status 401, got $status" >&2
  cat /tmp/sgw-mcp-smoke-unauth.json >&2 || true
  exit 1
fi

if [ -z "${SGW_MCP_TOKEN:-}" ]; then
  echo "Fetching token from $SSH_HOST container env..."
  SGW_MCP_TOKEN="$(
    ssh "$SSH_HOST" \
      "C=\$(docker ps --filter label=coolify.resourceName=$RESOURCE_LABEL --format '{{.Names}}' | head -1); test -n \"\$C\"; docker exec \"\$C\" printenv SGW_MCP_TOKEN"
  )"
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp" /tmp/sgw-mcp-smoke-unauth.json' EXIT

echo "Checking tools/list..."
curl -sS "$MCP_URL" \
  -H "Authorization: Bearer $SGW_MCP_TOKEN" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
  > "$tmp"

python3 - "$tmp" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
tools = [tool["name"] for tool in data["result"]["tools"]]
expected = {"search", "scrape", "links", "detect", "extract", "sitemap"}
missing = sorted(expected.difference(tools))
if missing:
    raise SystemExit(f"missing tools: {missing}; got {tools}")
print("tools:", ", ".join(tools))
PY

echo "Checking search tool call..."
curl -sS "$MCP_URL" \
  -H "Authorization: Bearer $SGW_MCP_TOKEN" \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search","arguments":{"query":"scrape gateway test","max_results":1}}}' \
  > "$tmp"

python3 - "$tmp" <<'PY'
import json
import sys

path = sys.argv[1]
data = json.load(open(path))
result = data.get("result", {})
if result.get("isError"):
    raise SystemExit(data)
content = result.get("structuredContent", {}).get("result", [])
if not content:
    raise SystemExit(f"empty search result: {data}")
print("search: ok")
PY

echo "MCP smoke test passed: $MCP_URL"

# Scrape Gateway MCP on Coolify

Date: 2026-05-31

## Service

- Browser console: `https://sgw.voidxd.cloud/`
- Public endpoint: `https://sgw.voidxd.cloud/mcp`
- Coolify app name: `sgw-mcp`
- Coolify app UUID: `hxz97tein5tfpmw2n3ur9uza`
- Host: `coolify-gen2`
- Current production image tag should track the deployed git commit.

## Secrets

Runtime secrets live in the Coolify container environment:

- `SGW_MCP_TOKEN`
- `SGW_MCP_URL`
- `SCRAPE_PROXY_URL`
- scrape provider API keys

Fetch the bearer token without printing it in docs:

```bash
ssh coolify-gen2 'C=$(docker ps --filter label=coolify.resourceName=sgw-mcp --format "{{.Names}}" | head -1); docker exec "$C" printenv SGW_MCP_TOKEN'
```

## Persistence

The live container must have this bind mount:

```text
/opt/sgw-mcp/data -> /data
```

Scrape Gateway state is stored under:

```text
/data/.scrape-gateway/
```

On the host this is:

```text
/opt/sgw-mcp/data/.scrape-gateway/
```

This preserves cache artifacts, domain memory, telemetry runs, and logs across normal Coolify redeploys.

## Caddy Routing

Do not route Caddy to a container IP or timestamped container name. Coolify changes container names on redeploy.

Set the Coolify application `custom_network_aliases` to:

```json
["sgw-mcp"]
```

The Caddy dynamic file should route to the stable alias:

```caddy
https://sgw.voidxd.cloud {
    reverse_proxy sgw-mcp:8100
    encode zstd gzip
}
```

File on the server:

```text
/data/coolify/proxy/caddy/dynamic/sgw-mcp.caddy
```

After editing dynamic Caddy config, restart the proxy so `caddy-docker-proxy` rebuilds the full config including Docker-label routes:

```bash
ssh coolify-gen2 'docker restart coolify-proxy'
```

Avoid loading only `/dynamic/Caddyfile` manually; that can temporarily drop generated Coolify routes such as Langfuse.

## Expected Checks

The browser console should load without a token. Its operational API should
require the same bearer token as MCP:

```bash
curl -fsS https://sgw.voidxd.cloud/ | grep "Scrape Gateway"
curl -i https://sgw.voidxd.cloud/api/session
curl -fsS https://sgw.voidxd.cloud/api/session \
  -H "Authorization: Bearer $SGW_MCP_TOKEN"
```

The second command should return `401`. The authenticated response should show
the enabled evaluation mode and provider names. The console stores the token
only in browser `sessionStorage`. It reads run history and evidence from the
persistent `/data/.scrape-gateway/` directory.

Unauthenticated MCP should return `401`, not `502`:

```bash
curl -i https://sgw.voidxd.cloud/mcp \
  -H 'Accept: application/json, text/event-stream'
```

Authenticated `tools/list` should include:

```text
search, scrape, links, detect, extract, sitemap
```

Use the helper:

```bash
scripts/sgw-mcp-smoke.sh
```

## Neighbor Service Sanity Check

Langfuse shares the proxy. After Caddy changes, verify it still reaches Cloudflare Access:

```bash
curl -i https://langfuse-f6tim406p7bpze5wutvcm4jv.voidxd.cloud | head
```

Expected external result is a Cloudflare Access `302`, and the Langfuse container should be healthy:

```bash
ssh coolify-gen2 'docker ps --format "{{.Names}}\t{{.Status}}" | grep langfuse'
```

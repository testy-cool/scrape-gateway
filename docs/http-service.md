# HTTP service

Install the service dependencies and start FastAPI through the CLI:

```bash
pip install -e ".[server]"
SGW_SERVICE_TOKEN=choose-a-token sgw serve --host 0.0.0.0 --port 8100
```

`SGW_SERVICE_TOKEN` is optional. When set, every `/v1` request must send
`Authorization: Bearer <token>`. `/health` stays public for container health checks.
The persistent MCP/console process exposes the same endpoints and protects them with
`SGW_MCP_TOKEN`.

## Scrape

```bash
curl -X POST http://localhost:8100/v1/scrape \
  -H "Authorization: Bearer $SGW_SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "country": "US",
    "render_js": false,
    "premium": false,
    "formats": ["html", "markdown"],
    "use_cache": true,
    "use_memory": true
  }'
```

`formats` accepts `html`, `markdown`, and `screenshot`. Screenshots are base64 encoded.
The response includes provider, route, validation, error, cost, telemetry metadata, and
the `cache_key` used by the cache endpoint.

## Cache, stats, and health

```bash
curl -H "Authorization: Bearer $SGW_SERVICE_TOKEN" \
  http://localhost:8100/v1/cache/0123456789abcdef01234567

curl -H "Authorization: Bearer $SGW_SERVICE_TOKEN" \
  http://localhost:8100/v1/stats/example.com

curl http://localhost:8100/health
```

`GET /v1/cache/{url_hash}` returns the saved HTML, Markdown, metadata, and base64
screenshot for a cache key. `GET /v1/stats/{domain}` returns learned provider outcomes.
Interactive OpenAPI documentation is available at `/docs` in standalone service mode
and `/v1/docs` in the combined MCP/console deployment.

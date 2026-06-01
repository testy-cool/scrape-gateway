FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY src/ src/
COPY extensions/sg-sitemap/ extensions/sg-sitemap/
COPY extensions/sg-cache/ extensions/sg-cache/

RUN uv pip install --system ".[mcp]" ./extensions/sg-sitemap/ ./extensions/sg-cache/

# Doppler CLI for secrets injection
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg \
    && curl -sLf --retry 3 --tlsv1.2 --proto "=https" 'https://packages.doppler.com/public/cli/gpg.DE2A7741A397C129.key' | gpg --dearmor -o /usr/share/keyrings/doppler-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/doppler-archive-keyring.gpg] https://packages.doppler.com/public/cli/deb/debian any-version main" > /etc/apt/sources.list.d/doppler-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends doppler \
    && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

VOLUME /data
WORKDIR /data
EXPOSE 8100

# If DOPPLER_TOKEN is set, secrets are injected at runtime.
# Otherwise, pass env vars directly (e.g. via Coolify).
CMD ["sh", "-c", "if [ -n \"$DOPPLER_TOKEN\" ]; then exec doppler run -- python -m scrape_gateway.mcp_server; else exec python -m scrape_gateway.mcp_server; fi"]

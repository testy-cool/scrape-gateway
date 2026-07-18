from __future__ import annotations

import asyncio
import json
import sys
import time

from scrape_gateway import FailureReason, ProviderAdapter, ScrapeRequest, ScrapeResult
from scrape_gateway.errors import classify_exception, classify_failure


class ScrapyProvider(ProviderAdapter):
    name = "scrapy"
    cost_rank = 4
    capabilities = frozenset({"html"})

    async def scrape(self, request: ScrapeRequest) -> ScrapeResult:
        start = time.perf_counter()
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "sg_scrapy._worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            payload = {
                "url": request.url,
                "headers": request.headers,
                "referer": request.referer,
                "timeout_seconds": request.timeout_seconds,
            }
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode()),
                timeout=request.timeout_seconds + 10,
            )
            if process.returncode != 0:
                detail = stderr.decode(errors="replace").strip()
                raise RuntimeError(detail or f"Scrapy worker exited {process.returncode}")
            response = json.loads(stdout)
            status = int(response["status_code"])
            html = str(response["html"])
            failure = classify_failure(status, html)
            return ScrapeResult(
                request.url,
                self.name,
                200 <= status < 400 and failure is None,
                status_code=status,
                html=html,
                failure_reason=failure,
                latency_ms=int((time.perf_counter() - start) * 1000),
                route="scrapy:spider",
                metadata={"final_url": str(response["final_url"])},
            )
        except Exception as exc:  # noqa: BLE001
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            reason = classify_exception(exc)
            if reason == FailureReason.UNKNOWN:
                reason = FailureReason.PROVIDER_ERROR
            return ScrapeResult(
                request.url,
                self.name,
                False,
                error=str(exc),
                failure_reason=reason,
                latency_ms=int((time.perf_counter() - start) * 1000),
            )

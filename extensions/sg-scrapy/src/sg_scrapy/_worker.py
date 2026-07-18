from __future__ import annotations

import json
import sys

import scrapy
from scrapy.crawler import CrawlerProcess


def main() -> None:
    request = json.loads(sys.stdin.read())
    result: dict[str, object] = {}
    headers = dict(request.get("headers") or {})
    referer = request.get("referer")
    if referer:
        headers.setdefault("Referer", str(referer))

    class SinglePageSpider(scrapy.Spider):
        name = "scrape_gateway_single_page"
        custom_settings = {
            "LOG_ENABLED": False,
            "ROBOTSTXT_OBEY": False,
            "HTTPERROR_ALLOW_ALL": True,
            "DOWNLOAD_TIMEOUT": float(request["timeout_seconds"]),
        }

        async def start(self):
            yield scrapy.Request(str(request["url"]), headers=headers, dont_filter=True)

        def parse(self, response):
            result.update(
                {
                    "status_code": int(response.status),
                    "html": response.text,
                    "final_url": response.url,
                }
            )

    process = CrawlerProcess(settings={"LOG_ENABLED": False})
    process.crawl(SinglePageSpider)
    process.start(install_signal_handlers=False)
    if not result:
        raise RuntimeError("Scrapy completed without returning a response")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

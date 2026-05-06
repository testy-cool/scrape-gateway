import asyncio

from scrape_gateway import ScrapeGateway, ScrapeRequest


async def main() -> None:
    gateway = ScrapeGateway()
    result = await gateway.scrape(ScrapeRequest("https://example.com"))
    print(result.provider, result.success, result.route)
    print((result.markdown or "")[:500])


asyncio.run(main())

# sg-requests

Scrape Gateway provider for the Python Requests HTTP baseline used by
ScrapingEvals.

```bash
sgw extensions sg-requests
sgw url https://example.com -p requests
```

Requests is synchronous, so the adapter runs it in a worker thread without
blocking Scrape Gateway's async router.

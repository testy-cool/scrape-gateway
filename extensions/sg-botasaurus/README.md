# sg-botasaurus

Scrape Gateway provider for Botasaurus's fingerprint-aware request client.

```bash
sgw extensions sg-botasaurus
sgw url https://example.com -p botosaurus
```

The adapter uses `botasaurus.request.Request` and runs its blocking request in a
worker thread.

# sg-nodriver

Adds Nodriver's direct Chrome DevTools browser automation to `sgw`.

```bash
uv pip install -e . -e extensions/sg-nodriver
sgw url https://example.com -p nodriver --render-js --screenshot
```

Chrome must be available on the host. Screenshots are returned in memory as base64
by Nodriver and decoded without writing temporary files.

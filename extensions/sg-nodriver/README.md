# sg-nodriver

Adds Nodriver's direct Chrome DevTools browser automation to `sgw`.

```bash
uv pip install -e . -e extensions/sg-nodriver
sgw url https://example.com -p nodriver --render-js --screenshot
```

Chrome must be available on the host. Screenshots are returned as bytes after reading
Nodriver's temporary PNG output. Install this extension with Python 3.11, 3.12, or
3.13; Nodriver's generated protocol sources do not parse on Python 3.14.

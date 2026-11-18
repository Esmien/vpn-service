#!/usr/bin/env python3
"""Fetch provider subscriptions to scratch for inspection, never change production."""
import base64
import collections
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request

root = Path('/root/vpn-router')
dest = Path(os.environ['TMPDIR']) / 'provider-subscriptions'
dest.mkdir(mode=0o700, exist_ok=True)
hwid = Path('/root/.secrets/vpn-provider-hwid').read_text().strip()
for index, entry in enumerate(json.loads((root / 'subs.json').read_text())):
    try:
        req = urllib.request.Request(entry['url'], headers={'User-Agent': 'v2rayTun/4.7.0', 'x-hwid': hwid})
        with urllib.request.urlopen(req, timeout=25) as response:
            raw = response.read(2_000_001).strip()
        if len(raw) > 2_000_000:
            raise ValueError('response too large')
        if b'://' not in raw:
            raw = base64.b64decode(raw + b'=' * (-len(raw) % 4))
        lines = raw.decode('utf-8', 'strict').splitlines()
        links = [line.strip() for line in lines if line.strip().startswith(('vless://', 'trojan://', 'hysteria2://', 'hy2://', 'vmess://', 'ss://'))]
        if not links or any(urllib.parse.urlparse(link).hostname in (None, '0.0.0.0') for link in links):
            raise ValueError('empty subscription or placeholder entries')
        target = dest / f'subscription-{index}.json'
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(links, output, ensure_ascii=False)
        protocols = collections.Counter(urllib.parse.urlparse(link).scheme for link in links)
        print(index, entry['name'], 'nodes', len(links), 'protocols', dict(protocols), 'scratch', target, flush=True)
    except Exception as exc:
        print(index, entry['name'], 'fetch failed:', type(exc).__name__, flush=True)

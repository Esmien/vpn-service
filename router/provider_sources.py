"""Provider subscriptions: fetch privately and enumerate non-LTE links."""
import base64
import json
import os
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


def entries(base: Path):
    result = []
    root = base / 'provider-subscriptions'
    if root.exists():
        for path in sorted(root.glob('subscription-*.json')):
            for link in json.loads(path.read_text()):
                u = urllib.parse.urlsplit(link)
                name = urllib.parse.unquote(u.fragment).strip()
                if 'lte' not in name.casefold() and u.scheme in {'vless', 'trojan', 'hysteria2'}:
                    result.append({'link': link, 'name': name})
    return result


def refresh(base: Path, cancel_event=None):
    sources = json.loads((base / 'subs.json').read_text())
    hwid = Path('/root/.secrets/vpn-provider-hwid').read_text().strip()
    directory = base / 'provider-subscriptions'
    directory.mkdir(mode=0o700, exist_ok=True)
    failures = []
    for index, source in enumerate(sources):
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            request = urllib.request.Request(source['url'], headers={'User-Agent':'v2rayTun/4.7.0', 'x-hwid':hwid})
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = response.read(2_000_001).strip()
            if len(raw) > 2_000_000:
                raise ValueError('too large')
            if b'://' not in raw:
                raw = base64.b64decode(raw + b'=' * (-len(raw) % 4))
            links = [line.strip() for line in raw.decode('utf-8', 'strict').splitlines()
                     if line.strip().startswith(('vless://', 'trojan://', 'hysteria2://'))]
            if not links or any(urllib.parse.urlsplit(link).hostname in (None, '0.0.0.0') for link in links):
                raise ValueError('invalid subscription')
            fd, name = tempfile.mkstemp(prefix='.subscription-', dir=directory)
            try:
                with os.fdopen(fd, 'w') as out:
                    json.dump(links, out, ensure_ascii=False)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(name, directory / f'subscription-{index}.json')
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        except (OSError, ValueError, UnicodeError):
            failures.append(index)
    return failures

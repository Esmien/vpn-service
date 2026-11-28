"""Create a private point-in-time preview snapshot without modifying VPN files."""
import json
import os
from pathlib import Path


def create_snapshot(source: Path, target: Path):
    cfg = json.loads((source / 'config.json').read_text())
    data = {'subscriptions': json.loads((source / 'subs.json').read_text()),
            'policy': json.loads((source / 'policy.json').read_text()),
            'outbounds': [o for o in cfg['outbounds'] if o.get('tag') in ('direct', 'nl', 'ru1')]}
    if {o['tag'] for o in data['outbounds']} != {'direct', 'nl', 'ru1'}:
        raise ValueError('missing own exits')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return len(data['subscriptions']), sum(len(s.get('nodes', [])) for s in data['subscriptions'])


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--target', type=Path, required=True)
    args = p.parse_args()
    sources, nodes = create_snapshot(args.source, args.target)
    print(f'Private snapshot created: {sources} subscriptions, {nodes} nodes')

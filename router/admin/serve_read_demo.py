"""Run the read API on the test domain using a private synthetic SQLite database."""
import argparse
from pathlib import Path

import uvicorn

from . import read_store as store
from .read_api import create_app


def seed(engine):
    with engine.begin() as db:
        db.execute(store.sources.insert(), dict(
            sub_id='demo', version=1, name='Демо · тестовые данные', fetch_status='active',
            secret_url='https://provider.example.invalid/demo',
        ))
        for nid, name in [('demo-a', 'Демо-узел A'), ('demo-b', 'Демо-узел B')]:
            db.execute(store.nodes.insert(), dict(
                node_id=nid, sub_id='demo', original_name=name, secret_link='test-only', transport_fingerprint=nid,
            ))
        db.execute(store.pools.insert(), [dict(node_id='demo-a', pool='lte'), dict(node_id='demo-a', pool='general'),
                                         dict(node_id='demo-b', pool='gemini')])
        for scenario, tag in [('general', 'direct'), ('gemini', 'nl'), ('ru', 'ru1')]:
            db.execute(store.own.insert(), dict(tag=tag, name=f'Демо · {tag}'))
            db.execute(store.routes.insert(), dict(scenario=scenario, policy_version=1, primary=tag, active=tag,
                                                   active_role='primary', health_state='unknown'))
            db.execute(store.route_own.insert(), dict(scenario=scenario, tag=tag))
        db.execute(store.fallbacks.insert(), dict(scenario='general', position=0, exit_id='demo-a'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, required=True)
    parser.add_argument('--operator-file', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    (args.socket.parent / 'private').mkdir(mode=0o700, exist_ok=True)
    path = args.database or (args.socket.parent / 'private' / ('snapshot.sqlite3' if args.snapshot else 'demo.sqlite3'))
    if args.database and args.database != Path('/var/lib/vpn-admin-preview-test/snapshot.sqlite3'):
        raise ValueError('mock database must be the dedicated test path')
    initialize = not path.exists()
    engine = store.open_store(path, initialize=initialize)
    if initialize:
        if args.snapshot:
            from .snapshot_import import import_snapshot
            import_snapshot(engine, args.snapshot)
        else:
            seed(engine)
    operator = args.operator_file.read_text(encoding='ascii').strip()
    assets = Path(__file__).resolve().parents[2] / 'web' / 'admin_panel'
    if args.snapshot:
        from .preview_probe import ProbeService
        probe_service = ProbeService(args.snapshot)
    else:
        probe_service = None
    if args.database:
        from .mock_store import MockStore
        mock_store = MockStore(engine)
    else:
        mock_store = None
    app = create_app(engine, operator=operator, preview_assets=assets, probe_service=probe_service, mock_store=mock_store)
    uvicorn.run(app, uds=str(args.socket), proxy_headers=False, access_log=False)


if __name__ == '__main__':
    main()

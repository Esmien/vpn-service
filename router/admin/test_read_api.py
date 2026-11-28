from pathlib import Path
import stat

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.exc import IntegrityError

from router.admin import read_store as store
from router.admin.read_api import create_app
from router.admin.read_models import SCENARIOS

STAMP = '2026-11-23T21:00:00Z'
AUTH = {'X-VPN-Operator': 'owner'}


@pytest.fixture
def db(tmp_path: Path):
    path = tmp_path / 'private' / 'test.sqlite3'
    db = store.open_store(path, initialize=True)
    with db.begin() as c:
        c.execute(store.sources.insert().values(
            sub_id='s', version=1, name='Demo', secret_url='https://example.test/secret-fixture',
            fetch_status='error', error_code='provider_unavailable', expires_at=STAMP,
        ))
        for node in ('n1', 'n2'):
            c.execute(store.nodes.insert().values(
                node_id=node, sub_id='s', original_name=node, secret_link='vless://secret-fixture',
                transport_fingerprint='private-fingerprint',
            ))
        c.execute(store.pools.insert(), [dict(node_id='n1', pool='lte'), dict(node_id='n1', pool='general'),
                                         dict(node_id='n2', pool='general')])
        for scenario, tag in zip(SCENARIOS, ('direct', 'nl', 'ru1'), strict=True):
            c.execute(store.own.insert().values(tag=tag, name=tag, connection_version=2))
            c.execute(store.routes.insert().values(scenario=scenario, policy_version=3, primary=tag, active=tag,
                                                   active_role='primary', health_state='unknown'))
            c.execute(store.route_own.insert().values(scenario=scenario, tag=tag))
        c.execute(store.fallbacks.insert(), [dict(scenario='general', position=1, exit_id='n1'),
                                             dict(scenario='general', position=0, exit_id='n2')])
        c.execute(store.own_egress.insert(), [dict(tag='direct', connection_version=2, ip='8.8.4.4', checked_at=STAMP),
                                              dict(tag='nl', connection_version=1, ip='8.8.8.8', checked_at=STAMP),
                                              dict(tag='ru1', connection_version=2, ip='127.0.0.1', checked_at=STAMP)])
    yield db, path
    db.dispose()


def test_subs_filters_and_redaction(db):
    r = TestClient(create_app(db[0], operator='owner')).get('/api/subs', headers=AUTH)
    assert r.status_code == 200
    row, = r.json()['subscriptions']
    assert [n['pools'] for n in row['nodes']] == [
        {'general': True, 'gemini': False, 'ru': False, 'lte': True},
        {'general': True, 'gemini': False, 'ru': False, 'lte': False},
    ]
    assert row['fetch_status'] == 'error' and row['expires_at'] == STAMP
    assert len(row['error_snippet']) == 29 and row['error_snippet'].endswith('…')
    assert all(marker not in r.text for marker in ('secret-fixture', 'private-fingerprint', 'egress_ip', 'secret_link'))


def test_routes_keep_order_and_only_own_confirmed_ip(db):
    r = TestClient(create_app(db[0], operator='owner')).get('/api/routes', headers=AUTH)
    assert r.status_code == 200
    result = r.json()
    assert set(result['scenarios']) == set(SCENARIOS)
    assert result['scenarios']['general']['fallbacks'] == ['n2', 'n1']
    assert result['own_egress'] == {'direct': {'ip': '8.8.4.4', 'checked_at': STAMP},
                                    'nl': {'ip': None, 'checked_at': None},
                                    'ru1': {'ip': None, 'checked_at': STAMP}}


def test_operator_and_no_writes(db):
    client = TestClient(create_app(db[0], operator='owner'))
    for path in ('/api/subs', '/api/routes'):
        assert client.get(path).status_code == 403
        assert client.get(path, headers={'X-VPN-Operator': 'someone'}).status_code == 403
        assert client.post(path, headers=AUTH).status_code == 405
    assert client.post('/api/subs/node', headers=AUTH).status_code == 404


def test_database_constraints_and_pragmas(db):
    engine, path = db
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with engine.connect() as a, engine.connect() as b:
        for conn in (a, b):
            assert conn.exec_driver_sql('PRAGMA journal_mode').scalar() == 'wal'
            assert conn.exec_driver_sql('PRAGMA busy_timeout').scalar() == 5000
            assert conn.exec_driver_sql('PRAGMA foreign_keys').scalar() == 1
    for node, pool in [('missing', 'lte'), ('n1', 'invalid'), ('n1', 'lte')]:
        with pytest.raises(IntegrityError), engine.begin() as c:
            c.execute(store.pools.insert().values(node_id=node, pool=pool))


def test_missing_snapshot_returns_safe_error(tmp_path):
    engine = store.open_store(tmp_path / 'private' / 'empty.sqlite3', initialize=True)
    try:
        r = TestClient(create_app(engine, operator='owner')).get('/api/routes', headers=AUTH)
        assert r.status_code == 503 and r.json() == {'error': {'code': 'snapshot_unavailable'}}
    finally:
        engine.dispose()

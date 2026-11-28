"""SQLite storage and read repository; no routing or delivery side effects."""
from ipaddress import ip_address
import os
from pathlib import Path
import stat

from sqlalchemy import (
    CheckConstraint, Column, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint,
    create_engine, event, select,
)
from sqlalchemy.engine import Engine, URL

from .read_models import Health, NodeView, OwnEgress, Pools, RouteView, RoutesView, SCENARIOS, SubscriptionView, SubscriptionsView

schema = MetaData()
sources = Table(
    'provider_sources', schema,
    Column('sub_id', String, primary_key=True), Column('version', Integer, nullable=False),
    Column('name', String, nullable=False), Column('secret_url', String, nullable=False), Column('tg_url', String),
    Column('fetch_status', String, nullable=False), Column('expires_at', String), Column('expiry_checked_at', String),
    Column('last_success_at', String), Column('error_code', String),
    CheckConstraint("fetch_status in ('active', 'error')"),
)
nodes = Table(
    'provider_nodes', schema,
    Column('node_id', String, primary_key=True),
    Column('sub_id', ForeignKey('provider_sources.sub_id', ondelete='CASCADE'), nullable=False, index=True),
    Column('original_name', String, nullable=False), Column('custom_name', String),
    Column('secret_link', String, nullable=False), Column('transport_fingerprint', String, nullable=False),
    Column('connection_version', Integer, nullable=False, default=1),
)
pools = Table(
    'node_pools', schema,
    Column('node_id', ForeignKey('provider_nodes.node_id', ondelete='CASCADE'), primary_key=True),
    Column('pool', String, primary_key=True), CheckConstraint("pool in ('general', 'gemini', 'ru', 'lte')"),
)
own = Table(
    'own_names', schema, Column('tag', String, primary_key=True), Column('name', String, nullable=False),
    Column('connection_version', Integer, nullable=False, default=1),
)
own_egress = Table(
    'own_egress', schema, Column('tag', ForeignKey('own_names.tag', ondelete='CASCADE'), primary_key=True),
    Column('connection_version', Integer, nullable=False), Column('ip', String), Column('checked_at', String, nullable=False),
)
routes = Table(
    'route_policies', schema, Column('scenario', String, primary_key=True),
    Column('policy_version', Integer, nullable=False), Column('runtime_generation', Integer, nullable=False, default=1),
    Column('primary', String, nullable=False), Column('active', String, nullable=False), Column('active_role', String, nullable=False),
    Column('health_state', String, nullable=False), Column('health_reason', String), Column('health_checked_at', String),
    CheckConstraint("scenario in ('general', 'gemini', 'ru')"),
)
route_own = Table(
    'route_own_exits', schema, Column('scenario', ForeignKey('route_policies.scenario', ondelete='CASCADE'), primary_key=True),
    Column('tag', ForeignKey('own_names.tag'), primary_key=True),
)
fallbacks = Table(
    'route_fallbacks', schema, Column('scenario', ForeignKey('route_policies.scenario', ondelete='CASCADE'), primary_key=True),
    Column('position', Integer, primary_key=True), Column('exit_id', String, nullable=False),
    UniqueConstraint('scenario', 'exit_id'), CheckConstraint('position >= 0'),
)

FETCH_ERRORS = {'provider_unavailable': 'Провайдер не ответил. Показана последняя рабочая копия.',
                'mock_unfetched': 'Мок: данные провайдера не загружались.'}


def open_store(path: Path, *, initialize: bool = False) -> Engine:
    """Initialize explicitly, never at API startup. Pass a dedicated private directory."""
    if not path.is_absolute():
        raise ValueError('absolute database path required')
    if initialize:
        path.parent.mkdir(mode=0o700, parents=False, exist_ok=True)
    if not _private(path.parent, directory=True):
        raise PermissionError('database directory must be owned by the service and mode 0700')
    if initialize:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_RDWR, 0o600)
        os.close(fd)
    if not _private(path, directory=False):
        raise PermissionError('database file must be owned by the service and mode 0600')
    engine = create_engine(URL.create('sqlite+pysqlite', database=str(path)), hide_parameters=True)

    @event.listens_for(engine, 'connect')
    def set_sqlite(dbapi_connection, _record):
        dbapi_connection.isolation_level = None  # SQLAlchemy owns BEGIN, including SELECT-only transactions.
        dbapi_connection.execute('PRAGMA foreign_keys=ON')
        dbapi_connection.execute('PRAGMA busy_timeout=5000')
        mode = dbapi_connection.execute('PRAGMA journal_mode=WAL' if initialize else 'PRAGMA journal_mode').fetchone()[0]
        if mode != 'wal':
            raise RuntimeError('database is not initialized for WAL')

    @event.listens_for(engine, 'begin')
    def begin(connection):
        connection.exec_driver_sql('BEGIN')

    if initialize:
        schema.create_all(engine)
    return engine


def _private(path: Path, *, directory: bool) -> bool:
    st = path.lstat()
    expected = 0o700 if directory else 0o600
    return (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)) and (
        stat.S_IMODE(st.st_mode) == expected and st.st_uid == os.geteuid()
    )


class ReadUnitOfWork:
    def __init__(self, engine: Engine):
        self.engine = engine

    def __enter__(self):
        self.connection = self.engine.connect()
        return ReadRepository(self.connection)

    def __exit__(self, *_exc):
        self.connection.close()


class ReadRepository:
    def __init__(self, connection):
        self.connection = connection

    def subscriptions(self) -> SubscriptionsView:
        db = self.connection
        memberships: dict[str, dict[str, bool]] = {}
        for node, pool in db.execute(select(pools)):
            memberships.setdefault(node, {})[pool] = True
        by_sub: dict[str, list[NodeView]] = {}
        for nid, sid, original, custom in db.execute(select(
            nodes.c.node_id, nodes.c.sub_id, nodes.c.original_name, nodes.c.custom_name,
        ).order_by(nodes.c.node_id)):
            by_sub.setdefault(sid, []).append(NodeView(
                node_id=nid, original_name=original, custom_name=custom, pools=Pools(**memberships.get(nid, {})),
            ))
        cols = ('sub_id', 'version', 'name', 'tg_url', 'fetch_status', 'expires_at', 'expiry_checked_at', 'last_success_at')
        views = []
        for row in db.execute(select(*(sources.c[c] for c in cols), sources.c.error_code).order_by(sources.c.sub_id)).mappings():
            error = None
            if row['fetch_status'] == 'error':
                error = FETCH_ERRORS.get(row['error_code'], 'Не удалось загрузить подписку.')
            views.append(SubscriptionView(
                **{k: row[k] for k in cols}, nodes=by_sub.get(row['sub_id'], []), error_full=error,
                error_snippet=(error[:28] + '…' if len(error) > 28 else error) if error else None,
            ))
        return SubscriptionsView(subscriptions=views)

    def routes(self) -> RoutesView | None:
        db = self.connection
        names = dict(db.execute(select(own.c.tag, own.c.name)).tuples().all())
        egress = {tag: OwnEgress() for tag in names}
        observed = select(own_egress.c.tag, own_egress.c.ip, own_egress.c.checked_at).join(
            own, (own.c.tag == own_egress.c.tag) & (own.c.connection_version == own_egress.c.connection_version),
        )
        for tag, raw, when in db.execute(observed):
            ip = _public_ip(raw)
            egress[tag] = OwnEgress(ip=ip, checked_at=when)
        allowed: dict[str, list[str]] = {}
        for scenario, tag in db.execute(select(route_own).order_by(route_own.c.tag)):
            allowed.setdefault(scenario, []).append(tag)
        ordered: dict[str, list[str]] = {}
        for scenario, tag in db.execute(select(fallbacks.c.scenario, fallbacks.c.exit_id).order_by(fallbacks.c.position)):
            ordered.setdefault(scenario, []).append(tag)
        views = {}
        for item in db.execute(select(routes)).mappings():
            scenario = item['scenario']
            views[scenario] = RouteView(
                policy_version=item['policy_version'], own_exit_ids=allowed.get(scenario, []), primary=item['primary'],
                fallbacks=ordered.get(scenario, []), active=item['active'], active_role=item['active_role'],
                health=Health(state=item['health_state'], reason=item['health_reason'], checked_at=item['health_checked_at']),
            )
        if set(views) != set(SCENARIOS):
            return None
        return RoutesView(own_names=names, own_egress=egress, scenarios=views)


def _public_ip(raw: str | None):
    if not raw or '%' in raw:
        return None
    try:
        ip = ip_address(raw)
    except ValueError:
        return None
    if ip.is_global and not ip.is_multicast:
        return ip
    return None

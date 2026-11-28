"""SQLite-only mock operations: no production files, network refresh or Xray apply."""
from urllib.parse import urlsplit
import secrets
from sqlalchemy import select, delete, update
from . import read_store as s


class MockError(ValueError):
    pass


def text(value, limit=200):
    v = value.strip()
    if not v or len(v) > limit or '://' in v:
        raise MockError('Некорректное название')
    return v


def url(value):
    value = value.strip()
    u = urlsplit(value)
    if u.scheme != 'https' or not u.hostname or len(value) > 4096 or u.username or u.password:
        raise MockError('Нужен HTTPS URL без имени пользователя')
    return value


def tg(value):
    if not value.strip(): return None
    v = value.strip()
    if v.startswith('@'): v = 'https://t.me/' + v[1:]
    if v.startswith('t.me/'): v = 'https://' + v
    u = urlsplit(v)
    if u.scheme != 'https' or u.hostname != 't.me' or u.username or u.password or not u.path.strip('/') or len(v) > 300:
        raise MockError('Некорректная ссылка Telegram')
    return v


class MockStore:
    def __init__(self, engine): self.engine = engine

    def node(self, sub_id, node_id, pools, custom_name, rename):
        with self.engine.begin() as c:
            if not c.execute(select(s.nodes.c.node_id).where(s.nodes.c.sub_id==sub_id, s.nodes.c.node_id==node_id)).first():
                raise MockError('Узел не найден')
            if rename:
                c.execute(update(s.nodes).where(s.nodes.c.node_id==node_id).values(custom_name=text(custom_name) if custom_name else None))
            for pool, enabled in pools.items():
                if pool not in ('general','gemini','ru','lte') or not isinstance(enabled, bool): raise MockError('Некорректный пул')
                c.execute(delete(s.pools).where(s.pools.c.node_id==node_id, s.pools.c.pool==pool))
                if enabled: c.execute(s.pools.insert().values(node_id=node_id,pool=pool))
                elif pool != 'lte': self._remove_from_route(c,pool,{node_id})

    def _remove_from_route(self, c, scenario, ids):
        row=c.execute(select(s.routes).where(s.routes.c.scenario==scenario)).mappings().one()
        fs=c.execute(select(s.fallbacks.c.exit_id).where(s.fallbacks.c.scenario==scenario).order_by(s.fallbacks.c.position)).scalars().all()
        new=[x for x in fs if x not in ids]
        primary=row['primary']
        if primary in ids: primary={'general':'direct','gemini':'nl','ru':'ru1'}[scenario]
        new=[x for x in new if x!=primary]
        if primary!=row['primary'] or new!=list(fs): self._write_route(c,scenario,primary,new,row['policy_version'])

    def _write_route(self,c,scenario,primary,fallbacks,version):
        c.execute(update(s.routes).where(s.routes.c.scenario==scenario).values(primary=primary,policy_version=version+1))
        c.execute(delete(s.fallbacks).where(s.fallbacks.c.scenario==scenario))
        for i, value in enumerate(fallbacks): c.execute(s.fallbacks.insert().values(scenario=scenario,position=i,exit_id=value))

    def route(self,scenario,primary,fallbacks,version):
        if scenario not in ('general','gemini','ru') or len(fallbacks)>100 or len(set([primary,*fallbacks]))!=len(fallbacks)+1:
            raise MockError('Некорректный список выходов')
        with self.engine.begin() as c:
            row=c.execute(select(s.routes).where(s.routes.c.scenario==scenario)).mappings().one()
            if version != row['policy_version']: raise MockError('Настройки уже изменились, обновите страницу')
            valid=set(c.execute(select(s.route_own.c.tag).where(s.route_own.c.scenario==scenario)).scalars())
            valid.update(c.execute(select(s.pools.c.node_id).where(s.pools.c.pool==scenario)).scalars())
            # Preserve legacy IDs already in the imported policy, but do not admit new ones.
            valid.add(row['primary']);valid.update(c.execute(select(s.fallbacks.c.exit_id).where(s.fallbacks.c.scenario==scenario)).scalars())
            if not set([primary,*fallbacks]) <= valid: raise MockError('Узел не включён в пул')
            self._write_route(c,scenario,primary,fallbacks,version)

    def add(self,name,secret_url,tg_url):
        name=text(name); secret_url=url(secret_url); tg_url=tg(tg_url)
        sid='mock-'+secrets.token_hex(12)
        with self.engine.begin() as c:
            c.execute(s.sources.insert().values(sub_id=sid,version=1,name=name,secret_url=secret_url,tg_url=tg_url,fetch_status='error',error_code='mock_unfetched'))
        return sid

    def rename_subscription(self, sub_id, name, version):
        name = text(name)
        with self.engine.begin() as c:
            res = c.execute(update(s.sources).where(s.sources.c.sub_id == sub_id, s.sources.c.version == version)
                            .values(name=name, version=s.sources.c.version + 1))
            if res.rowcount != 1:
                raise MockError('Подписка не найдена или уже изменена; обновите страницу')

    def replace_url(self,sub_id,new_url):
        value=url(new_url)
        with self.engine.begin() as c:
            res=c.execute(update(s.sources).where(s.sources.c.sub_id==sub_id).values(secret_url=value,version=s.sources.c.version+1))
            if res.rowcount!=1: raise MockError('Подписка не найдена')

    def refresh(self,sub_id):
        with self.engine.begin() as c:
            res=c.execute(update(s.sources).where(s.sources.c.sub_id==sub_id).values(version=s.sources.c.version+1))
            if res.rowcount!=1: raise MockError('Подписка не найдена')

    def delete(self,sub_id,confirm):
        if confirm != 'DELETE': raise MockError('Введите DELETE')
        with self.engine.begin() as c:
            ids=set(c.execute(select(s.nodes.c.node_id).where(s.nodes.c.sub_id==sub_id)).scalars())
            for scenario in ('general','gemini','ru'): self._remove_from_route(c,scenario,ids)
            res=c.execute(delete(s.sources).where(s.sources.c.sub_id==sub_id))
            if res.rowcount != 1: raise MockError('Подписка не найдена')

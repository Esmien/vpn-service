"""Read-only snapshot import for the isolated preview; never writes source files."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import read_store as store


def stamp(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).isoformat()
    except ValueError:
        return None


def import_snapshot(engine, path: Path):
    data = json.loads(path.read_text())
    sources = data['subscriptions']
    policy = data['policy']
    with engine.begin() as db:
        for i, sub in enumerate(sources):
            sid = str(sub.get('id') or f'source-{i}')
            status = sub.get('status')
            db.execute(store.sources.insert().values(sub_id=sid, version=1,
                name=str(sub.get('name') or sid), secret_url=sub.get('url') or '',
                tg_url=sub.get('tg_url') or None, fetch_status='active' if status == 'active' else 'error',
                expires_at=stamp(sub.get('expire')), last_success_at=stamp(sub.get('updated_at'))))
            for node in sub.get('nodes', []):
                link = node.get('link', '')
                nid = str(node['node_id'])
                db.execute(store.nodes.insert().values(node_id=nid, sub_id=sid,
                    original_name=str(node.get('original_name') or 'Без названия'),
                    custom_name=node.get('custom_name') or None, secret_link=link,
                    transport_fingerprint=hashlib.sha256(link.encode()).hexdigest()))
                for pool in ('general', 'gemini', 'ru', 'lte'):
                    if node.get('pools', {}).get(pool) is True:
                        db.execute(store.pools.insert().values(node_id=nid, pool=pool))
        names = {'direct':'Эстония (основной)', 'nl':'Нидерланды (Gemini)', 'ru1':'Москва (НАШ)'}
        for tag, name in names.items():
            db.execute(store.own.insert().values(tag=tag, name=name))
        for scenario, tag in [('general','direct'), ('gemini','nl'), ('ru','ru1')]:
            selected = policy.get(scenario) or [tag]
            # Xray uses leastPing; the active member is not observable here.
            db.execute(store.routes.insert().values(scenario=scenario, policy_version=1,
                primary=selected[0], active='не определён', active_role='primary', health_state='unknown'))
            db.execute(store.route_own.insert().values(scenario=scenario, tag=tag))
            for index, exit_id in enumerate(selected[1:]):
                db.execute(store.fallbacks.insert().values(scenario=scenario, position=index, exit_id=exit_id))

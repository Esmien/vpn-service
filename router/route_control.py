"""Backend for route selection, staged only; do not run on production yet."""
import concurrent.futures
import importlib.util
import hashlib
import itertools
import json
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from route_probe import run_probe
from route_policy import render_policy
from route_transaction import set_policy, recover

BASE = Path('/root/vpn-router')
LOCK = Path('/var/lock/vpn-config.lock')
PROBE_LOCK = threading.Lock()
CANCEL = threading.Event()
RESULTS = {'gemini': {}, 'ru': {}}
RUNNING = set()
RUN_SCOPE = {}
OWN_TAGS = frozenset({'nl', 'ru1', 'direct'})
PROBE_ERRORS = {'gemini': None, 'ru': None}
SEQUENCE = itertools.count(1)
EPOCH = os.urandom(8).hex()


def load_config():
    return json.loads((BASE / 'config.json').read_text())


def candidates(config):
    import urllib.parse
    from probe_sub import build
    from provider_sources import entries
    import hy2_bridge
    relay_file = BASE / 'relay_nodes.json'
    lte_tags = set()
    if relay_file.exists():
        lte_tags = {'p'+str(n.get('index')) for n in json.loads(relay_file.read_text()) if 'lte' in str(n.get('name','')).casefold()}
    result = {item['tag']: item for item in config.get('outbounds', [])
              if item.get('protocol') in ('freedom', 'vless', 'trojan') and item.get('tag') and item['tag'] not in lte_tags}
    sources = entries(BASE)
    white = BASE / 'whitelist_links.json'
    if white.exists():
        sources += [n for n in json.loads(white.read_text()) if 'lte' not in str(n.get('name','')).casefold()]
    for node in sources:
        try:
            link = node['link']
            tag = 'wl-' + hashlib.sha256(link.encode()).hexdigest()[:24]
            if tag in result:
                continue
            scheme = urllib.parse.urlsplit(link).scheme
            if scheme == 'hysteria2':
                hy2_bridge.config(link, hy2_bridge.port_for(tag))
                out = hy2_bridge.outbound(tag)
                out['_hy2_link'] = link
            else:
                built = build(link, 1)
                if not built:
                    continue
                out = built['outbounds'][0]
                out['tag'] = tag
            result[tag] = out
        except (ValueError, KeyError, TypeError):
            continue
    bridge_file = BASE / 'hy2-bridges.json'
    if bridge_file.exists():
        for tag, link in json.loads(bridge_file.read_text()).items():
            if tag not in result and any(o.get('tag') == tag for o in config.get('outbounds', [])):
                out = hy2_bridge.outbound(tag)
                out['_hy2_link'] = link
                result[tag] = out
    return result


def current_policy(cfg):
    policy_file = BASE / 'policy.json'
    if policy_file.exists():
        return json.loads(policy_file.read_text())
    rules = cfg['routing']['rules']
    groups = {b['tag']: b for b in cfg['routing']['balancers']}
    result = {}
    for name, marker in [('gemini', 'domain:google.com'), ('ru', 'domain:ru')]:
        matches = [r for r in rules if not r.get('inboundTag') and marker in r.get('domain', [])]
        if len(matches) != 1:
            raise ValueError('не удалось определить действующий маршрут')
        rule = matches[0]
        result[name] = [rule['outboundTag']] if rule.get('outboundTag') else groups[rule['balancerTag']]['selector']
    return result


def snapshot():
    cfg = load_config()
    pol = current_policy(cfg)
    nodes = []
    names = {}
    for fn, prefix in [('relay_nodes.json', 'p'), ('whitelist_links.json', 'wl-')]:
        path = BASE / fn
        if path.exists():
            for item in json.loads(path.read_text()):
                idx = item.get('index')
                tag = ('wl-' + hashlib.sha256(item['link'].encode()).hexdigest()[:24]) if prefix == 'wl-' else prefix + str(idx)
                names[tag] = str(item.get('name', ''))[:100]
    from provider_sources import entries
    for item in entries(BASE):
        tag = 'wl-' + hashlib.sha256(item['link'].encode()).hexdigest()[:24]
        names[tag] = item['name'][:100]
    import urllib.parse
    groups = {}
    for tag, outbound in candidates(cfg).items():
        name = names.get(tag) or tag
        if outbound.get('_hy2_link'):
            u = urllib.parse.urlsplit(outbound['_hy2_link'])
            endpoint = ('hysteria2', u.hostname, u.port)
        else:
            settings = outbound.get('settings', {})
            servers = settings.get('vnext') or settings.get('servers') or []
            endpoint = (outbound.get('protocol'), servers[0].get('address'), servers[0].get('port')) if servers else ('unique', tag)
        key = (endpoint, name.casefold().strip())
        groups.setdefault(key, []).append(tag)
    for (_, _), tags in groups.items():
        representative = tags[0]
        ids, results = {}, {}
        for kind in ('gemini', 'ru'):
            selected = set(pol[kind])
            def rank(tag):
                result = RESULTS[kind].get(tag)
                status = result.get('status') if result else None
                return (0 if tag in selected and status != 'bad' else 1 if status == 'ok' else 2 if result is None else 3)
            best = min(tags, key=rank)
            ids[kind], results[kind] = best, RESULTS[kind].get(best)
        nodes.append({'id': representative, 'ids': ids, 'name': names.get(representative) or representative,
                      'source': 'свой' if representative in OWN_TAGS else 'подписка', 'results': results})
    return {'ok': True, 'epoch': EPOCH, 'seq': next(SEQUENCE), 'policy': pol, 'nodes': nodes, 'pending': (BASE/'pending.json').exists(), 'probing': list(RUNNING), 'probing_scope': dict(RUN_SCOPE), 'stopping': CANCEL.is_set() and bool(RUNNING), 'probe_errors': dict(PROBE_ERRORS)}


def probe(scenario, scope='all'):
    if scenario not in RESULTS or scope not in ('all', 'own', 'subscription'):
        raise ValueError('неизвестный сценарий или группа')
    if scenario in RUNNING and RUN_SCOPE.get(scenario) == scope:
        CANCEL.set()
        return snapshot()
    if not PROBE_LOCK.acquire(False):
        raise RuntimeError('проверка уже выполняется')
    CANCEL.clear()
    RUN_SCOPE[scenario] = scope
    RUNNING.add(scenario)
    def worker():
        try:
            from provider_sources import refresh
            if scope != 'own':
                try:
                    failed = refresh(BASE, CANCEL)
                    PROBE_ERRORS[scenario] = 'Не удалось обновить часть подписок; проверена сохранённая копия' if failed else None
                except (OSError, ValueError, KeyError):
                    PROBE_ERRORS[scenario] = 'Не удалось обновить подписки; проверена сохранённая копия'
            if CANCEL.is_set():
                return
            outs = candidates(load_config())
            if scope != 'all':
                outs = {tag: out for tag, out in outs.items() if (tag in OWN_TAGS) == (scope == 'own')}
            for tag in outs:
                RESULTS[scenario].pop(tag, None)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                jobs = {pool.submit(run_probe, out, scenario, cancel_event=CANCEL): tag for tag, out in outs.items()}
                for future in concurrent.futures.as_completed(jobs):
                    tag = jobs[future]
                    try:
                        result = future.result()
                        if result.get('status') == 'cancelled':
                            continue
                        result['updated'] = datetime.now(timezone.utc).isoformat()
                        RESULTS[scenario][tag] = result
                    except Exception:
                        if not CANCEL.is_set():
                            RESULTS[scenario][tag] = {'tag': tag, 'status': 'bad', 'error': 'проверка не удалась'}
        except Exception:
            PROBE_ERRORS[scenario] = 'Не удалось выполнить проверку'
        finally:
            RUNNING.discard(scenario)
            RUN_SCOPE.pop(scenario, None)
            PROBE_LOCK.release()
    threading.Thread(target=worker, daemon=True).start()
    return snapshot()


def validate(cfg):
    spec = importlib.util.spec_from_file_location('vpn_bluegreen', str(BASE / 'vpn-bluegreen.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.lint(cfg):
        return False
    # Validate both port layouts before touching the bind-mounted live file.
    with tempfile.TemporaryDirectory(prefix='vpn-validation-', dir=os.environ.get('TMPDIR', '/root/.hermes/cache/scratch')) as td:
        for i, value in enumerate((cfg, module.derive_b(cfg))):
            file = Path(td) / f'{i}.json'
            fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(value, f)
            if not module.validate(file)[0]:
                return False
    return True


def apply_config(_cfg):
    # Caller already holds the config flock; do not use vpn-cfg which locks again.
    try:
        env = dict(os.environ, VPN_POLICY_TRANSACTION='1')
        result = subprocess.run(['python3', str(BASE/'vpn-bluegreen.py'), 'apply'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, env=env)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


BRIDGE_LOCK = threading.RLock()
BRIDGES_IN_FLIGHT = set()

def ensure_bridges(policy):
    import hy2_bridge
    path = BASE / 'hy2-bridges.json'
    saved = json.loads(path.read_text()) if path.exists() else {}
    selected = {tag for tags in policy.values() for tag in tags}
    for tag in selected & saved.keys():
        hy2_bridge.start(tag, saved[tag], BASE / 'hy2-runtime')
    return selected & saved.keys()


def start_bridge_supervisor():
    import hy2_bridge
    import time
    def worker():
        while True:
            try:
                with BRIDGE_LOCK:
                    selected = ensure_bridges(current_policy(load_config()))
                    hy2_bridge.stop_unused(selected | BRIDGES_IN_FLIGHT)
            except Exception:
                pass
            time.sleep(10)
    threading.Thread(target=worker, daemon=True).start()


def apply_selection(policy):
    # Test the pure renderer first. Watchdogs and crash recovery must be wired
    # before enabling this endpoint on a live system.
    if not isinstance(policy, dict) or any(not isinstance(v, list) for v in policy.values()):
        raise ValueError('неверная политика')
    def prepare(cfg):
        allowed = candidates(cfg)
        selected = {tag for values in policy.values() for tag in values}
        if not selected.issubset(allowed):
            raise ValueError('неизвестный выход')
        present = {out.get('tag') for out in cfg['outbounds']}
        bridge_file = BASE / 'hy2-bridges.json'
        saved = json.loads(bridge_file.read_text()) if bridge_file.exists() else {}
        saved.update({tag: allowed[tag]['_hy2_link'] for tag in selected if allowed[tag].get('_hy2_link')})
        ports = [__import__('hy2_bridge').port_for(tag) for tag in selected if tag in saved]
        if len(ports) != len(set(ports)):
            raise ValueError('конфликт локальных портов выходов')
        if saved:
            from route_transaction import write_atomic
            write_atomic(bridge_file, json.dumps(saved).encode())
            BRIDGES_IN_FLIGHT.update(selected & saved.keys())
            ensure_bridges(policy)
        for tag in sorted(selected - present):
            cfg['outbounds'].append({k:v for k,v in allowed[tag].items() if not k.startswith('_')})
        return cfg
    import hy2_bridge
    with BRIDGE_LOCK:
        try:
            set_policy(BASE, policy, validate, apply_config, LOCK, prepare)
        finally:
            BRIDGES_IN_FLIGHT.clear()
            if not (BASE / 'pending.json').exists():
                try: hy2_bridge.stop_unused(ensure_bridges(current_policy(load_config())))
                except Exception: pass
    return snapshot()

"""Ephemeral network probes against private snapshot copies; never applies policy."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
import copy
import json
import secrets
import sys
from urllib.parse import urlsplit

ROUTER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROUTER))
import route_probe
import probe_sub


class ProbeService:
    def __init__(self, path):
        self.snapshot = json.loads(Path(path).read_text())
        self.lock = Lock()
        self.jobs = {}
        self.busy = False
        self.nodes = {str(n['node_id']): n for s in self.snapshot['subscriptions'] for n in s.get('nodes', [])}
        self.own = {o['tag']: o for o in self.snapshot['outbounds'] if o.get('tag') in ('direct', 'nl', 'ru1')}

    def launch(self, scenario, ids):
        if scenario not in ('general', 'gemini', 'ru') or not ids or len(ids) > 100 or len(set(ids)) != len(ids):
            raise ValueError('invalid selection')
        if not all(isinstance(i, str) and (i in self.nodes or i in self.own) for i in ids):
            raise ValueError('unknown node')
        with self.lock:
            if self.busy:
                raise RuntimeError('busy')
            self.busy = True
            job = secrets.token_urlsafe(16)
            self.jobs = {job: {'status':'running', 'scenario':scenario, 'results':{}}}
        Thread(target=self._worker, args=(job, scenario, ids), daemon=True).start()
        return job

    def read(self, job):
        with self.lock:
            return copy.deepcopy(self.jobs.get(job))

    def _one(self, node_id, scenario):
        try:
            if node_id in self.own:
                outbound = copy.deepcopy(self.own[node_id])
            else:
                link = self.nodes[node_id].get('link', '')
                if urlsplit(link).scheme == 'hysteria2':
                    outbound = {'tag':node_id, '_hy2_link':link}
                else:
                    built = probe_sub.build(link, 1)
                    if not built:
                        raise ValueError('unsupported')
                    outbound = built['outbounds'][0]
                    outbound['tag'] = node_id
            if scenario == 'general':
                with route_probe.probe_proxy(outbound) as port:
                    rc, code, _, latency = route_probe.fetch(port, 'https://www.gstatic.com/generate_204')
                    result = {'status':'ok' if rc == 0 and code == 204 else 'unknown', 'latency_ms':latency}
                    if node_id in self.own:
                        ip_rc, ip_code, ip_body, _ = route_probe.fetch(port, 'https://api.ipify.org/')
                        if ip_rc == 0 and ip_code == 200:
                            result['egress_ip'] = ip_body.strip()
            else:
                result = route_probe.run_probe(outbound, scenario)
                # These older checks cannot distinguish filtering from network failure.
                if result.get('status') != 'ok':
                    result['status'] = 'unknown'
            safe = {'status':result.get('status','unknown'), 'latency_ms':result.get('latency_ms'),
                    'checked_at':datetime.now(timezone.utc).isoformat()}
            if node_id in self.own:
                from ipaddress import ip_address
                try:
                    ip = ip_address(result.get('egress_ip') or '')
                    safe['ip'] = str(ip) if ip.is_global and not ip.is_multicast else None
                except ValueError:
                    safe['ip'] = None
            return safe
        except Exception:
            return {'status':'unknown','latency_ms':None,'checked_at':datetime.now(timezone.utc).isoformat()}

    def _worker(self, job, scenario, ids):
        try:
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(self._one, nid, scenario):nid for nid in ids}
                for future in as_completed(futures):
                    with self.lock:
                        self.jobs[job]['results'][futures[future]] = future.result()
        finally:
            with self.lock:
                self.jobs[job]['status'] = 'complete'
                self.busy = False

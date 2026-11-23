"""Isolated Hysteria2 SOCKS clients, one per selected subscription exit."""
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
from pathlib import Path

CLIENT = '/usr/local/bin/hysteria'
PROCESSES = {}
MUTEX = threading.RLock()


def config(link, port):
    u = urllib.parse.urlsplit(link)
    if u.scheme != 'hysteria2' or not u.hostname or not u.port:
        raise ValueError('invalid Hysteria2 link')
    query = urllib.parse.parse_qs(u.query)
    host = f'[{u.hostname}]' if ':' in u.hostname else u.hostname
    cfg = {'server': f'{host}:{u.port}', 'auth': urllib.parse.unquote(u.username or ''),
           'tls': {'sni': query.get('sni', [u.hostname])[0]}, 'socks5': {'listen': f'127.0.0.1:{port}'}}
    if query.get('obfs', [''])[0] == 'salamander':
        cfg['obfs'] = {'type': 'salamander', 'salamander': {'password': query.get('obfs-password', [''])[0]}}
    return cfg


def port_for(tag):
    return 30000 + int(tag[-8:], 16) % 25000


def outbound(tag):
    return {'tag': tag, 'protocol': 'socks', 'settings': {'servers': [{'address': '127.0.0.1', 'port': port_for(tag)}]}}


def start(tag, link, directory):
    with MUTEX:
        old = PROCESSES.get(tag)
        if old and old.poll() is None:
            return
        port = port_for(tag)
        with socket.socket() as s:
            s.bind(('127.0.0.1', port))
        directory.mkdir(mode=0o700, exist_ok=True)
        fd, filename = tempfile.mkstemp(prefix='hy2-', suffix='.json', dir=directory)
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(config(link, port), f)
                f.flush()
                os.fsync(f.fileno())
            proc = subprocess.Popen([CLIENT, 'client', '-c', filename], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError('Hysteria client failed to start')
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.2):
                        PROCESSES[tag] = proc
                        return
                except OSError:
                    time.sleep(.05)
            proc.terminate()
            proc.wait(timeout=3)
            raise RuntimeError('Hysteria SOCKS did not start')
        finally:
            try:
                os.unlink(filename)
            except FileNotFoundError:
                pass


def stop(tag):
    with MUTEX:
        p = PROCESSES.pop(tag, None)
        if p and p.poll() is None:
            p.terminate()
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired: p.kill(); p.wait()


def stop_unused(tags):
    with MUTEX:
        for tag in list(PROCESSES):
            if tag not in tags and not tag.startswith('probe-'):
                p = PROCESSES.pop(tag)
                if p.poll() is None:
                    p.terminate()
                    try: p.wait(timeout=3)
                    except subprocess.TimeoutExpired: p.kill(); p.wait()

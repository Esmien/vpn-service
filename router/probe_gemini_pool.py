#!/usr/bin/env python3
"""Probe saved provider nodes with Google's response-body marker; not an account test."""
import argparse
import concurrent.futures
import json
import os
import pathlib
import re
import socket
import subprocess
import tempfile
import time
import urllib.parse
from probe_sub import build

ROOT = pathlib.Path('/root/vpn-router')
MARKER = re.compile(r'this\.gbar_=\{CONFIG:\[\[\[0,"www\.gstatic\.com","[^"]*","([a-z]{2,3}(?:\.[a-z]{2})?)","([a-z]{2})"')


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snapshot', required=True, help='saved Xray config with provider outbounds')
    args = ap.parse_args()
    saved = json.loads(pathlib.Path(args.snapshot).read_text())
    by_tag = {o['tag']: o for o in saved['outbounds']}
    active = {o['tag']: o for o in json.loads((ROOT/'config.json').read_text())['outbounds']}
    by_tag.update(active)
    nodes = []
    for n in json.loads((ROOT/'relay_nodes.json').read_text()):
        tag = 'p'+str(n['index'])
        nodes.append((f"relay {tag} {n['name']}", by_tag.get(tag)))
    for n in json.loads((ROOT/'whitelist_links.json').read_text()):
        try:
            cfg = build(n['link'], 1)
            out = cfg['outbounds'][0] if cfg else None
        except (ValueError, KeyError):
            out = None
        nodes.append((f"whitelist {n['index']} {n['name']}", out))
    for tag in ('fr','fi'):
        nodes.append((f'exit {tag}',by_tag.get(tag)))
    results=[]
    def probe(item):
        label, out = item
        if not out:
            return (label, 'unsupported or no saved config')
        sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]; sock.close()
        cfg={'log':{'loglevel':'none'},'inbounds':[{'listen':'127.0.0.1','port':port,'protocol':'socks','settings':{'udp':False}}], 'outbounds':[out]}
        with tempfile.NamedTemporaryFile(mode='w',dir=os.environ['TMPDIR'],suffix='.json') as f:
            os.chmod(f.name,0o600);json.dump(cfg,f);f.flush()
            p=subprocess.Popen(['/usr/local/bin/xray','run','-config',f.name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                time.sleep(1)
                if p.poll() is not None: return (label,'xray config/start failed')
                r=subprocess.run(['curl','-sS','--max-time','18','-H','Accept-Language: en','--socks5-hostname',f'127.0.0.1:{port}','https://gemini.google.com/app'],capture_output=True,text=True)
                m=MARKER.search(r.stdout)
                return (label, f'marker={m.group(1)} language={m.group(2)}' if m else f'no marker (curl exit {r.returncode}, body {len(r.stdout)} chars)')
            finally:
                p.terminate()
                try: p.wait(timeout=3)
                except subprocess.TimeoutExpired: p.kill();p.wait()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(probe,nodes))
    for label, result in results: print(label,':',result,flush=True)
    print('TOTAL',len(results),'marker found',sum('marker=' in r for _,r in results))
    print('Markers are an unauthenticated response heuristic, not proof an account can send prompts.')

if __name__=='__main__': run()

#!/usr/bin/env python3
"""Serve parallel-domain views of existing VPN pages and subscriptions on loopback."""
import base64
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OLD = 'vpn.esmien.ru'
NEW = 'share.esmien.com'
SUBS = Path('/var/www/esmien-sub')
PAGES = Path('/var/www/esmien-pages')
ALLOWED_FILES = {'list.txt', 'clash.yaml', 'singbox.json'}
PATH = re.compile(r'^/(sub|u)/([A-Za-z0-9_-]+)/?$')

class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        match = PATH.fullmatch(self.path)
        if not match:
            return self.send_error(404)
        kind, token = match.groups()
        if kind == 'u':
            file = PAGES / (token + '.html')
            content_type = 'text/html; charset=utf-8'
        else:
            filename = self.headers.get('X-Sub-File', '')
            if filename not in ALLOWED_FILES:
                return self.send_error(404)
            file = SUBS / token / filename
            content_type = 'text/plain; charset=utf-8'
        try:
            content = file.read_bytes()
            if kind == 'sub' and file.name == 'list.txt':
                content = base64.b64encode(base64.b64decode(content, validate=False).replace(OLD.encode(), NEW.encode())) + b'\n'
            else:
                content = content.replace(OLD.encode(), NEW.encode())
                if kind == 'u':
                    content = content.replace((NEW + ':8443/sub/').encode(), (NEW + '/sub/').encode())
        except (OSError, ValueError):
            return self.send_error(404)
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    def log_message(self, *_args):
        pass

if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', 18744), Handler).serve_forever()

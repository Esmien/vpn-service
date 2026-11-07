#!/usr/bin/env python3
"""Local-only API for the stats dashboard: create a subscription profile.

Listens on 127.0.0.1:9198 behind nginx (basic auth on the stats vhost), so it is
never reachable from the internet directly. A name goes in, users.py add does the
whole job (uuid, both tokens, config, blue/green apply, nginx reload) and the
three links come back.
"""
import json
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "/root/vpn-router"
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9 _-]{2,24}$")
LOCK = threading.Lock()
HOST, PORT = "127.0.0.1", 9198
PROTECTED = {"Владислав"}  # owner: no delete, reissue only


def load_users() -> dict:
    with open(f"{BASE}/users.json") as fh:
        return json.load(fh)


def links(u: dict) -> dict:
    tok, lte = u.get("token", ""), u.get("token_lte", "")
    return {
        "page": f"https://vpn.esmien.ru:8443/u/{tok}" if tok else "",
        "sub": f"https://vpn.esmien.ru:8443/sub/{tok}" if tok else "",
        "lte": f"https://vpn.esmien.ru:8443/sub/{lte}" if lte else "",
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "subgen"

    def log_message(self, fmt, *args):  # keep nginx error.log clean
        pass

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/users", ""):
            try:
                return self._send(200, {"ok": True, "users": sorted(load_users())})
            except Exception as exc:
                return self._send(500, {"ok": False, "error": str(exc)[:200]})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        route = self.path.rstrip("/")
        if route not in ("/new", "/del", "/reissue", "/refresh"):
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) or b"{}"
            data = json.loads(raw)
        except Exception:
            return self._send(400, {"ok": False, "error": "bad request"})

        if route == "/refresh":
            try:
                r = subprocess.run(["python3", f"{BASE}/stats_collect.py"],
                                   cwd=BASE, capture_output=True, text=True, timeout=90)
            except subprocess.TimeoutExpired:
                return self._send(504, {"ok": False, "error": "сбор не уложился в 90 с"})
            return self._send(200 if r.returncode == 0 else 500,
                              {"ok": r.returncode == 0,
                               "error": "" if r.returncode == 0 else (r.stderr or "")[-300:]})

        name = str(data.get("name", "")).strip()
        if not NAME_RE.match(name):
            return self._send(400, {"ok": False,
                                    "error": "имя: 2–24 символа, буквы/цифры/пробел/дефис"})

        with LOCK:
            try:
                existing = load_users().get(name)
            except Exception as exc:
                return self._send(500, {"ok": False, "error": str(exc)[:200]})

            if route == "/del":
                if name in PROTECTED:
                    return self._send(403, {"ok": False,
                                            "error": "этот ключ не удаляется — только перевыпуск"})
                if not existing:
                    return self._send(404, {"ok": False, "error": "такого пользователя нет"})
                try:
                    r = subprocess.run(["python3", f"{BASE}/users.py", "rm", name],
                                       cwd=BASE, capture_output=True, text=True, timeout=600)
                except subprocess.TimeoutExpired:
                    return self._send(504, {"ok": False, "error": "apply не уложился в 10 минут"})
                out = (r.stdout or "") + (r.stderr or "")
                if r.returncode != 0:
                    return self._send(500, {"ok": False, "error": out.strip()[-400:] or "users.py вернул ошибку"})
                return self._send(200, {"ok": True, "name": name,
                                        "hint": "ключ удалён: токены отозваны, конфиг применён"})

            if route == "/reissue":
                if not existing:
                    return self._send(404, {"ok": False, "error": "такого пользователя нет"})
                for step in (["rm", name], ["add", name]):
                    try:
                        r = subprocess.run(["python3", f"{BASE}/users.py", *step],
                                           cwd=BASE, capture_output=True, text=True, timeout=600)
                    except subprocess.TimeoutExpired:
                        return self._send(504, {"ok": False, "error": "apply не уложился в 10 минут"})
                    if r.returncode != 0:
                        out = ((r.stdout or "") + (r.stderr or "")).strip()[-400:]
                        return self._send(500, {"ok": False, "error": out or "users.py вернул ошибку"})
                fresh = load_users().get(name) or {}
                return self._send(200, {"ok": True, "name": name, "links": links(fresh),
                                        "hint": "ключ перевыпущен: старые ссылки мертвы, вот новые"})

            if existing:
                return self._send(409, {"ok": False, "exists": True, "name": name,
                                        "links": links(existing),
                                        "error": "такой пользователь уже есть"})
            try:
                r = subprocess.run(["python3", f"{BASE}/users.py", "add", name],
                                   cwd=BASE, capture_output=True, text=True, timeout=600)
            except subprocess.TimeoutExpired:
                return self._send(504, {"ok": False, "error": "apply не уложился в 10 минут"})
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode != 0:
                return self._send(500, {"ok": False, "error": out.strip()[-400:] or "users.py вернул ошибку"})
            created = load_users().get(name) or {}
            return self._send(200, {"ok": True, "name": name, "links": links(created),
                                    "hint": "профиль создан и применён — можно отправлять ссылки"})


if __name__ == "__main__":
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

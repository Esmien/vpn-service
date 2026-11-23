"""Изолированная проверка VPN-выходов. Не изменяет конфигурацию маршрутов."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

XRAY = "/usr/local/bin/xray"
MARKER = re.compile(r'this\.gbar_=\{CONFIG:\[\[\[0,"www\.gstatic\.com","[^"]*","([a-z]{2,3}(?:\.[a-z]{2})?)"')
SITES = {
    "Озон": "https://www.ozon.ru/",
    "Госуслуги": "https://www.gosuslugi.ru/",
    "Сбер": "https://sber.ru/",
    "Wildberries": "https://www.wildberries.ru/",
    "Авито": "https://www.avito.ru/",
}
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"


def classify_http(code: int, exit_code: int) -> str:
    return "bad" if code == 0 else "ok"


def classify_gemini(body: str, code: int, exit_code: int) -> dict:
    if exit_code != 0 or code != 200:
        return {"status": "bad", "region": None}
    match = MARKER.search(body)
    if not match:
        return {"status": "bad", "region": None}
    region = match.group(1)
    # Маркер Google — эвристика, а не доказательство доступа аккаунта.
    return {"status": "bad" if region in {"ru", "by", "ir", "kp", "sy", "cu", "cn", "mm", "sd", "tm", "ve", "af"} else "ok", "region": region}


@contextmanager
def probe_proxy(outbound: dict):
    """Локальный временный Xray; credentials не выдаются в логах и API."""
    tag = outbound.get("tag")
    if not isinstance(tag, str) or not tag:
        raise ValueError("missing outbound tag")
    if outbound.get('_hy2_link'):
        import hy2_bridge
        probe_tag = 'probe-' + os.urandom(8).hex()
        try:
            hy2_bridge.start(probe_tag, outbound['_hy2_link'], Path(os.environ.get('TMPDIR', '/root/.hermes/cache/scratch')))
            yield hy2_bridge.port_for(probe_tag)
        finally:
            hy2_bridge.stop(probe_tag)
        return
    if outbound.get("protocol") not in {"freedom", "vless", "trojan", "socks"}:
        raise ValueError("unsupported outbound")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = {"log": {"loglevel": "none"}, "inbounds": [{"listen": "127.0.0.1", "port": port,
            "protocol": "socks", "settings": {"udp": False}}], "outbounds": [outbound]}
    tmpdir = os.environ.get("TMPDIR", "/root/.hermes/cache/scratch")
    with tempfile.TemporaryDirectory(prefix="vpn-probe-", dir=tmpdir) as td:
        path = Path(td) / "config.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(cfg, fh)
        proc = subprocess.Popen([XRAY, "run", "-config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            end = time.monotonic() + 3
            while True:
                if proc.poll() is not None:
                    raise RuntimeError("не удалось запустить тестовый Xray")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                        break
                except OSError:
                    if time.monotonic() >= end:
                        raise RuntimeError("тестовый Xray не открыл порт")
                    time.sleep(0.05)
            yield port
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def fetch(port: int, url: str, cancel_event=None) -> tuple[int, int, str, float | None]:
    """Для теста ограничиваем размер ответа и время, не отключаем TLS-проверку."""
    try:
        proc = subprocess.Popen(["curl", "--silent", "--show-error", "--compressed", "--max-time", "15",
            "--max-filesize", "2000000", "--user-agent", UA, "--header", "Accept-Language: en",
            "--proxy", f"socks5h://127.0.0.1:{port}", "--write-out", "\n%{http_code} %{time_starttransfer}", url],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    proc.terminate()
                    proc.communicate(timeout=3)
                    return 1, 0, "", None
                try:
                    stdout, _ = proc.communicate(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        body, sep, info = stdout.rpartition(b"\n")
        fields = info.split() if sep else []
        code = int(fields[0]) if len(fields) == 2 and fields[0].isdigit() else 0
        try:
            latency = round(float(fields[1]) * 1000) if code else None
        except (ValueError, IndexError):
            latency = None
        return proc.returncode, code, body.decode("utf-8", "replace"), latency
    except (OSError, subprocess.TimeoutExpired):
        return 1, 0, "", None


def run_probe(outbound: dict, scenario: str, request: Callable = fetch, cancel_event=None) -> dict:
    if scenario not in {"gemini", "ru"}:
        raise ValueError("unknown scenario")
    result = {"tag": outbound.get("tag"), "status": "bad", "egress_ip": None}
    if cancel_event is not None and cancel_event.is_set():
        return {"status": "cancelled"}
    if request is fetch:
        request = lambda port, url: fetch(port, url, cancel_event)
    try:
        with probe_proxy(outbound) as port:
            rc, code, ip, _ = request(port, "https://api.ipify.org/")
            if rc == 0 and code == 200:
                import ipaddress
                try:
                    result["egress_ip"] = str(ipaddress.ip_address(ip.strip()))
                except ValueError:
                    pass
            if scenario == "gemini":
                rc, code, body, latency = request(port, "https://gemini.google.com/app")
                result.update(classify_gemini(body, code, rc))
                result['latency_ms'] = latency
            else:
                sites = {}
                failed = 0
                for name, url in SITES.items():
                    if cancel_event is not None and cancel_event.is_set():
                        return {"status": "cancelled"}
                    rc, code, _, latency = request(port, url)
                    failed += code == 0
                    sites[name] = {"status": classify_http(code, rc), "http": code or None}
                    if name == "Госуслуги":
                        result['latency_ms'] = latency
                result["sites"] = sites
                result["status"] = 'bad' if failed else 'ok'
    except (RuntimeError, OSError, ValueError):
        result["status"] = "bad"
        result["error"] = "проверку не удалось выполнить"
    if cancel_event is not None and cancel_event.is_set():
        return {"status": "cancelled"}
    return result

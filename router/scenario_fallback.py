#!/usr/bin/env python3
"""Watchdog for the scenario routes: probe the provider nodes, prune dead ones.

Every run it measures each scenario node the way a client would — a TCP handshake
first (what Happ's own ping shows), then one HTTP 204 through the node — and
rewrites the balancer selectors so a dead node leaves the rotation and the whole
group falls back to our Estonian exit. Everything is jittered (start delay,
probe order, target URL, user agent) because a fixed rhythm hammering provider
nodes looks exactly like the abuse their panels ban for.

    scenario_fallback.py            # one pass (timer runs this)
    scenario_fallback.py --dry-run  # log the verdict, change nothing
"""
import json
import random
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root/vpn-router")
from scenario_routes import GROUPS, PREFERENCE, SINGLE  # noqa: E402

BASE = Path("/root/vpn-router")
CONFIG = BASE / "config.json"
STATE = BASE / "scenario_state.json"
LOG = Path("/var/log/scenario-fallback.log")
XRAY = "/usr/local/bin/xray"
PROBE_BASE_PORT = 10920
PROBE_URLS = ["https://www.gstatic.com/generate_204",
              "https://cp.cloudflare.com/generate_204",
              "http://connectivitycheck.gstatic.com/generate_204"]
UAS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"]


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        with LOG.open("a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def endpoint(outbound: dict):
    if outbound.get("protocol") == "vless":
        v = outbound["settings"]["vnext"][0]
        return v["address"], int(v["port"])
    if outbound.get("protocol") == "trojan":
        s = outbound["settings"]["servers"][0]
        return s["address"], int(s["port"])
    return None


def tcp_ping(addr: str, port: int, timeout: float = 3.0):
    start = time.monotonic()
    try:
        with socket.create_connection((addr, port), timeout=timeout):
            return int((time.monotonic() - start) * 1000)
    except OSError:
        return None


def probe_client(cfg: dict, targets: list, ports: dict) -> Path:
    """Throwaway xray client: one socks inbound per node, routed by inboundTag."""
    outbounds, rules = [], []
    for tag in targets:
        out = next(o for o in cfg["outbounds"] if o["tag"] == tag)
        outbounds.append(out)
        rules.append({"type": "field", "inboundTag": [f"probe-{tag}"], "outboundTag": tag})
    outbounds.append({"protocol": "freedom", "tag": "direct"})
    client = {
        "log": {"level": "error"},
        "inbounds": [{"listen": "127.0.0.1", "port": ports[t], "protocol": "socks",
                      "tag": f"probe-{t}", "settings": {"udp": False}} for t in targets],
        "outbounds": outbounds,
        "routing": {"domainStrategy": "AsIs", "rules": rules},
    }
    path = BASE / ".scenario_probe.json"
    path.write_text(json.dumps(client, indent=2))
    return path


def http_through(port: int, timeout: int = 8) -> bool:
    url = random.choice(PROBE_URLS)
    try:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                            "--max-time", str(timeout), "-A", random.choice(UAS),
                            "--proxy", f"socks5h://127.0.0.1:{port}", url],
                           capture_output=True, text=True, timeout=timeout + 4)
        return r.stdout.strip() in ("200", "204")
    except subprocess.TimeoutExpired:
        return False


def main() -> int:
    cfg = json.loads(CONFIG.read_text())
    groups = {b["tag"]: b for b in cfg["routing"]["balancers"]}
    targets = {}
    for group in GROUPS:
        for tag in PREFERENCE[group]:
            if tag == "direct":
                continue
            out = next((o for o in cfg["outbounds"] if o["tag"] == tag), None)
            if out:
                targets[tag] = endpoint(out)

    if not targets:
        log("нет сценариев в конфиге — выходим")
        return 0

    random.seed()
    time.sleep(random.uniform(0, 20))  # джиттер старта: не бьём по расписанию

    ports = {t: PROBE_BASE_PORT + i for i, t in enumerate(targets)}
    order = list(targets)
    random.shuffle(order)
    client_path = probe_client(cfg, order, ports)
    proc = subprocess.Popen([XRAY, "run", "-c", str(client_path)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    alive, report = {}, []
    try:
        time.sleep(2.5)
        for tag in order:
            addr, port = targets[tag]
            ping = tcp_ping(addr, port)
            ok = ping is not None and http_through(ports[tag])
            alive[tag] = ok
            report.append(f"{tag}:{'ok' if ok else 'DEAD'}({ping if ping is not None else '-'}ms)")
            time.sleep(random.uniform(0.3, 1.5))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        client_path.unlink(missing_ok=True)

    wanted = {}
    for group in GROUPS:
        pref = [t for t in PREFERENCE[group] if t != "direct"]
        live = [t for t in pref if alive.get(t)]
        if not live:
            wanted[group] = ["direct"]
        elif group in SINGLE:
            wanted[group] = [live[0]]
        else:
            wanted[group] = live

    current = {tag: list(b["selector"]) for tag, b in groups.items()}
    changed = {tag: sel for tag, sel in wanted.items() if current.get(tag) != sel}
    log(" ".join(report) + " | " + " ".join(f"{t}={s}" for t, s in wanted.items()))

    if not changed:
        return 0
    if "--dry-run" in sys.argv:
        log(f"dry-run: изменил бы {changed}")
        return 0

    for tag, sel in changed.items():
        groups[tag]["selector"] = sel
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    STATE.write_text(json.dumps({"selectors": wanted,
                                 "updated": datetime.now(timezone.utc).isoformat()}, indent=2))
    log(f"применяю {changed}")
    r = subprocess.run(["vpn-cfg", "apply"], capture_output=True, text=True, timeout=300)
    tail = (r.stdout or "").strip().splitlines()[-2:]
    log(f"apply rc={r.returncode}: {' / '.join(tail)}")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())

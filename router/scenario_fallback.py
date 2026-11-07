#!/usr/bin/env python3
"""Watchdog for the scenario routes: probe the nodes, log the verdict, write the draft.

Every run it measures each scenario node the way a client would — a TCP handshake
first (what Happ's own ping shows), then one HTTP request through the node — and
writes the resulting balancer selectors into `config.json`.

The file it writes is a DRAFT: xray reads a config only at start, so nothing changes
for live traffic until someone runs `vpn-cfg apply`. This script deliberately does
NOT apply anything (it used to call `vpn-cfg apply` while holding the same flock that
apply takes, i.e. it deadlocked against itself for 300 s on every change).

Live failover is handled inside xray by the balancer `fallbackTag` (see
scenario_routes.py): when the group's only node stops answering the observatory,
traffic moves to the fallback node in ~30–60 s without touching files.

Probes are jittered (start delay, order, URL, user agent) because a fixed rhythm
hammering provider nodes looks exactly like the abuse their panels ban for.

    scenario_fallback.py            # one pass (timer runs this)
    scenario_fallback.py --dry-run  # log the verdict, change nothing
"""
import fcntl
import json
import random
import re
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

# RU probes must answer from a Russian datacenter and must be the services the RU
# group exists for. Never probe a RU exit against a foreign endpoint (Google,
# Cloudflare): that path is slow from Russia on its own and used to flip the
# selector to `direct` on a false "dead".
# Measured from ru1: plain HTTP is what to use — tbank/avito answer 301 in ~0.2 s,
# while their HTTPS pages either fail curl's trust check (Russian roots) or take >15 s
# to render; gosuslugi answers 200 over HTTPS in <1 s.
PROBE_URLS_RU = ["http://www.tbank.ru/",
                 "http://www.avito.ru/",
                 "https://www.gosuslugi.ru/"]
PROBE_URLS = ["https://www.gstatic.com/generate_204",
              "https://cp.cloudflare.com/generate_204",
              "http://connectivitycheck.gstatic.com/generate_204"]
UAS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
       "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"]

# --- Gemini geo verdict -------------------------------------------------------
# The unauthenticated /app shell is byte-identical from every country and carries no
# "not available in your country" text, so a body-string check cannot work. What does
# differ is Google's own country for the connection:
#   this.gbar_={CONFIG:[[[0,"www.gstatic.com","<build>","<cc>","<lang>"
# EE reads "ru" (blocked), FR reads "co.uk" (supported) — hence the permissive regex.
GEMINI_URL = "https://gemini.google.com/app"
GEMINI_MARKER = re.compile(
    r'this\.gbar_=\{CONFIG:\[\[\[0,"www\.gstatic\.com","[^"]*","([a-z]{2,3}(?:\.[a-z]{2})?)"')
GEMINI_BLOCKED = {"ru", "by", "ir", "kp", "sy", "cu", "cn", "mm", "sd", "tm", "ve", "af"}
GEMINI_EVERY_MIN = 600          # check at most once per 10–15 minutes
GEMINI_EVERY_MAX = 900

FAIL_THRESHOLD = 2              # a node leaves the rotation only after N fails in a row


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


def tcp_ping(addr: str, port: int, timeout: float = 5.0):
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
        "log": {"loglevel": "error"},
        "inbounds": [{"listen": "127.0.0.1", "port": ports[t], "protocol": "socks",
                      "tag": f"probe-{t}", "settings": {"udp": False}} for t in targets],
        "outbounds": outbounds,
        "routing": {"domainStrategy": "AsIs", "rules": rules},
    }
    path = BASE / ".scenario_probe.json"
    path.write_text(json.dumps(client, indent=2))
    return path


def http_through(port: int, timeout: int = 8, ru: bool = False) -> bool:
    """Alive = the node returned any HTTP answer. Only a dead connection (000) is a fail:
    banks and marketplaces answer 403 to a datacenter IP and that still proves the path."""
    url = random.choice(PROBE_URLS_RU if ru else PROBE_URLS)
    try:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                            "--max-time", str(timeout), "-A", random.choice(UAS),
                            "--proxy", f"socks5h://127.0.0.1:{port}", url],
                           capture_output=True, text=True, timeout=timeout + 4)
        code = (r.stdout or "").strip()
        return r.returncode == 0 and code not in ("", "000")
    except subprocess.TimeoutExpired:
        return False


def gemini_geo(port: int, timeout: int = 30):
    """Google's country for this exit, or None when the verdict could not be read."""
    try:
        r = subprocess.run(["curl", "-s", "--compressed", "-o", "-", "--max-time", str(timeout),
                            "-A", random.choice(UAS), "--proxy", f"socks5h://127.0.0.1:{port}",
                            GEMINI_URL], capture_output=True, text=True, timeout=timeout + 5)
        m = GEMINI_MARKER.search(r.stdout or "")
        return m.group(1) if m else None
    except subprocess.TimeoutExpired:
        return None


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def main() -> int:
    lock = open("/var/lock/vpn-config.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
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

    state = load_state()
    fails = {k: int(v) for k, v in (state.get("fails") or {}).items()}

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
            ok = ping is not None and http_through(ports[tag], ru=(tag in PREFERENCE.get("bal-ru", [])))
            alive[tag] = ok
            report.append(f"{tag}:{'ok' if ok else 'DEAD'}({ping if ping is not None else '-'}ms)")
            time.sleep(random.uniform(0.3, 1.5))

        # Gemini geo verdict, at most once per 10–15 min: the landing page is heavy
        # (~850 KB, ~170 KB gzipped) and there is no reason to poll it every minute.
        now = time.time()
        due = now - float(state.get("gemini_last") or 0) >= random.uniform(GEMINI_EVERY_MIN, GEMINI_EVERY_MAX)
        geo = dict(state.get("gemini") or {})
        if due:
            for group, pref in PREFERENCE.items():
                if group != "bal-google":
                    continue
                for tag in pref:
                    if tag == "direct" or not alive.get(tag) or tag not in ports:
                        continue
                    cc = gemini_geo(ports[tag])
                    geo[tag] = cc
                    if cc is None:
                        report.append(f"gemini[{tag}]:verdict?-")
                    else:
                        bad = cc in GEMINI_BLOCKED
                        report.append(f"gemini[{tag}]:{cc if not bad else cc + '!BLOCKED'}")
                        if bad:
                            alive[tag] = False
                    time.sleep(random.uniform(0.5, 1.5))
                    if cc is not None and cc not in GEMINI_BLOCKED:
                        break  # primary answers and passes — no need to check the spare
            state["gemini_last"] = now
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        client_path.unlink(missing_ok=True)

    # Hysteresis: one bad probe is not a dead node. A tag leaves the rotation only
    # after FAIL_THRESHOLD failures in a row, so a single hiccup cannot flip the group.
    effective = {}
    for tag, ok in alive.items():
        fails[tag] = 0 if ok else fails.get(tag, 0) + 1
        effective[tag] = ok or fails[tag] < FAIL_THRESHOLD

    wanted = {}
    for group in GROUPS:
        pref = [t for t in PREFERENCE[group] if t != "direct"]
        live = [t for t in pref if effective.get(t)]
        if not live:
            wanted[group] = ["direct"]
        elif group in SINGLE:
            wanted[group] = [live[0]]
        else:
            wanted[group] = live

    current = {tag: list(b["selector"]) for tag, b in groups.items()}
    changed = {tag: sel for tag, sel in wanted.items() if current.get(tag) != sel}
    log(" ".join(report) + " | " + " ".join(f"{t}={s}" for t, s in wanted.items())
        + " | fails=" + json.dumps({k: v for k, v in fails.items() if v}))

    state.update({"selectors": wanted,
                  "gemini": geo,
                  "fails": fails,
                  "updated": datetime.now(timezone.utc).isoformat()})
    STATE.write_text(json.dumps(state, indent=2))

    if not changed:
        return 0
    if "--dry-run" in sys.argv:
        log(f"dry-run: записал бы {changed}")
        return 0

    fresh = json.loads(CONFIG.read_text())
    fresh_groups = {b["tag"]: b for b in fresh["routing"].get("balancers", [])}
    applied = {}
    for tag, sel in changed.items():
        b = fresh_groups.get(tag)
        if not b or b["selector"] == sel:
            continue  # the group was removed or already fixed by someone else
        b["selector"] = sel
        applied[tag] = sel
    if not applied:
        log("после перечитки конфига писать нечего")
        return 0
    CONFIG.write_text(json.dumps(fresh, indent=2, ensure_ascii=False) + "\n")
    log(f"черновик записан: {applied} (в живой трафик не идёт — применяется 'vpn-cfg apply')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

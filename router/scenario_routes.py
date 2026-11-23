#!/usr/bin/env python3
"""Scenario routing for the entry: Google -> FR/FI, YouTube -> AL, RU -> RU pool.

Each scenario is an Xray balancer over the provider nodes that can serve it, with
`leastPing` so a dead node drops out of rotation by itself. The watchdog
(`scenario_fallback.py`) swaps a group's selector to `direct` (our Estonian exit)
when every node of the group is dead, and restores it afterwards — that is the
"fallback to Estonia" the subscription promises.

    scenario_routes.py install                 # write observatory + balancers + rules
    scenario_routes.py set-selector bal-yt direct
"""
import json
import sys
from pathlib import Path

BASE = Path("/root/vpn-router")
CONFIG = BASE / "config.json"

GOOGLE_GROUP = "bal-google"
RU_GROUP = "bal-ru"

GROUPS = {
    # Gemini goes out through the French relay: phone works on it. Estonia shows the
    # "not supported in your country" stub, FI does not load the page at all.
    GOOGLE_GROUP: ["fr"],
    RU_GROUP: ["ru1", "p15", "p17"],
}

# Native xray failover. The balancer only holds one node per group, so when that node
# stops answering the observatory there is nothing left to pick — `fallbackTag` is what
# keeps the group alive, and xray does it himself in ~30–60 s (probeInterval), without
# rewriting configs or restarting a container. This is the real fallback; the watchdog
# only writes a draft and no longer applies anything.
FALLBACK = {GOOGLE_GROUP: "fi", RU_GROUP: "p15"}

# Ordered preference used by the watchdog when it prunes/restores a group.
PREFERENCE = {
    GOOGLE_GROUP: ["fr"],
    RU_GROUP: ["ru1", "p15"],
}

# Google falls back to a single node; RU keeps the whole pool in the balancer and lets
# leastPing pick the fastest alive relay. YouTube is deliberately absent: the owner
# decided it stays on the Estonian exit rather than spending provider traffic.
SINGLE = {GOOGLE_GROUP}

# Manual, temporary overrides ({"bal-yt": ["p16", "direct"]}). Kept in a separate file so the
# owner can re-point a scenario without touching code, and so the watchdog reads the same
# preference instead of reverting it on its next pass.
OVERRIDE_FILE = BASE / "scenario_override.json"
if OVERRIDE_FILE.exists():
    try:
        for _g, _tags in json.loads(OVERRIDE_FILE.read_text()).items():
            if _g in PREFERENCE and isinstance(_tags, list) and _tags:
                PREFERENCE[_g] = _tags
    except (ValueError, OSError):
        pass

# Only Gemini-specific hosts; shared Google assets cannot be distinguished by app.
# Add shared hosts only on evidence and with explicit acceptance of their wider impact.
GOOGLE_API_DOMAINS = ["domain:google.com", "domain:googleapis.com", "domain:gstatic.com",
                      "domain:googleusercontent.com", "domain:withgoogle.com", "domain:google.dev"]

# Heavy media stays off the relay (provider traffic is the expensive part): video and
# thumbnails go straight out of Estonia, while every Google frontend behind Gemini shares
# one country with it. Splitting Google across two countries is what made Gemini answer
# the send with a generic error even though the page itself rendered fine.
MEDIA_DOMAINS = ["domain:googlevideo.com", "domain:ytimg.com", "domain:ggpht.com",
                 "domain:gvt1.com", "domain:gvt2.com", "domain:youtube.com"]
GEMINI_DOMAINS = GOOGLE_API_DOMAINS
GOOGLE_DOMAINS = GOOGLE_API_DOMAINS

# Probe URL must answer from every exit we own: gstatic times out from the RU box,
# which used to make leastPing treat the RU group as empty and stall .ru traffic.
OBSERVATORY = {
    "subjectSelector": ["fr", "fi", "p15", "p16", "p17"],
    "probeUrl": "https://cp.cloudflare.com/generate_204",
    "probeInterval": "30s",
    "enableConcurrency": True,
}


def balancers(selectors: dict) -> list:
    return [{"tag": tag, "selector": selectors[tag], "strategy": {"type": "leastPing"},
             "fallbackTag": FALLBACK[tag]}
            for tag in (GOOGLE_GROUP, RU_GROUP)]


def install(cfg: dict) -> dict:
    routing = cfg.setdefault("routing", {})
    rules = routing.get("rules", [])

    ru_domains = []
    google_ips = []
    inbound_rules = []
    api_rule = None
    for r in rules:
        if "inboundTag" in r:
            # api-in (StatsService) must be matched BEFORE the geoip:private block:
            # its destination is 127.0.0.1, so a later block rule swallows it and
            # every stats query dies with "failed to dial 127.0.0.1:<port>".
            if r.get("inboundTag") == ["api-in"]:
                api_rule = r
            else:
                inbound_rules.append(r)
        elif r.get("ip") == ["geoip:private"]:
            continue
        elif r.get("protocol") == ["bittorrent"]:
            continue
        elif "domain" in r and any(d in ("domain:ru", "domain:su") for d in r["domain"]):
            ru_domains = r["domain"]
        elif "ip" in r:
            google_ips = r["ip"]

    head = ([api_rule] if api_rule else []) + [
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked"},
        # The FR/FI relays are trojan and cannot carry UDP at all, so QUIC dies silently
        # there and the browser hangs on its own timeout. Refusing UDP/443 makes clients
        # fall back to TCP at once; without this rule Gemini web reports a generic error.
        {"type": "field", "network": "udp", "port": 443, "outboundTag": "blocked"},
        {"type": "field", "domain": MEDIA_DOMAINS, "outboundTag": "direct"},
        {"type": "field", "domain": GOOGLE_DOMAINS, "balancerTag": GOOGLE_GROUP},
        {"type": "field", "domain": ru_domains, "balancerTag": RU_GROUP},
    ]
    # Всё, что не попало в правила, идёт через эстонский выход (direct), а не через
    # релей: fr/fi обслуживают только Gemini и .ru-сайты.
    tail = [{"type": "field", "network": "tcp,udp", "outboundTag": "direct"}]

    routing["rules"] = head + inbound_rules + tail
    routing["balancers"] = balancers(GROUPS)
    obs = dict(OBSERVATORY)
    obs["subjectSelector"] = sorted({t for tags in PREFERENCE.values() for t in tags if t != "direct"})
    cfg["observatory"] = obs
    return cfg


def main() -> int:
    cfg = json.loads(CONFIG.read_text())
    cmd = sys.argv[1] if len(sys.argv) > 1 else "install"
    if (BASE / 'policy.json').exists() and cmd in ('install', 'set-selector'):
        print('маршрутами управляет админка', file=sys.stderr)
        return 1

    if cmd == "install":
        install(cfg)
        CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        print("installed: observatory + 3 balancers + scenario rules")
        return 0

    if cmd == "set-selector" and len(sys.argv) >= 4:
        group, tags = sys.argv[2], sys.argv[3:]
        for b in cfg["routing"]["balancers"]:
            if b["tag"] == group:
                if b["selector"] == tags:
                    print(f"{group}: already {tags}")
                    return 0
                b["selector"] = tags
                CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
                print(f"{group}: selector -> {tags}")
                return 0
        print(f"no such group: {group}")
        return 1

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
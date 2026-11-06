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
YT_GROUP = "bal-yt"
RU_GROUP = "bal-ru"

GROUPS = {
    # Google: France is primary, Finland only takes over when France is dead
    # (both pass the Gemini geo verdict, but France is the one we sell).
    GOOGLE_GROUP: ["fr"],
    RU_GROUP: ["p15", "p16", "p17"],
    YT_GROUP: ["p20"],
}

# Ordered preference used by the watchdog when it prunes/restores a group.
PREFERENCE = {
    GOOGLE_GROUP: ["fr", "fi", "direct"],
    RU_GROUP: ["p15", "p16", "p17", "direct"],
    YT_GROUP: ["p20", "direct"],
}

# Google and YouTube painlessly fall back to a single node; RU keeps the whole
# pool in the balancer and lets leastPing pick the fastest alive relay.
SINGLE = {GOOGLE_GROUP, YT_GROUP}

GOOGLE_DOMAINS = ["domain:google.com", "domain:googleapis.com", "domain:gstatic.com",
                  "domain:googleusercontent.com", "domain:withgoogle.com", "domain:google.dev"]
YOUTUBE_DOMAINS = ["domain:youtube.com", "domain:googlevideo.com", "domain:ytimg.com",
                   "domain:youtu.be", "domain:youtube-nocookie.com", "domain:ggpht.com",
                   "domain:yt.be"]

OBSERVATORY = {
    "subjectSelector": ["fr", "fi", "p15", "p16", "p17", "p20"],
    "probeUrl": "https://www.gstatic.com/generate_204",
    "probeInterval": "30s",
    "enableConcurrency": True,
}


def balancers(selectors: dict) -> list:
    return [{"tag": tag, "selector": selectors[tag], "strategy": {"type": "leastPing"}}
            for tag in (GOOGLE_GROUP, RU_GROUP, YT_GROUP)]


def install(cfg: dict) -> dict:
    routing = cfg.setdefault("routing", {})
    rules = routing.get("rules", [])

    ru_domains = []
    google_ips = []
    inbound_rules = []
    for r in rules:
        if "inboundTag" in r:
            inbound_rules.append(r)
        elif r.get("ip") == ["geoip:private"]:
            continue
        elif r.get("protocol") == ["bittorrent"]:
            continue
        elif "domain" in r and any(d in ("domain:ru", "domain:su") for d in r["domain"]):
            ru_domains = r["domain"]
        elif "ip" in r:
            google_ips = r["ip"]

    head = [
        {"type": "field", "ip": ["geoip:private"], "outboundTag": "blocked"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "blocked"},
        {"type": "field", "domain": GOOGLE_DOMAINS, "balancerTag": GOOGLE_GROUP},
        {"type": "field", "domain": YOUTUBE_DOMAINS, "balancerTag": YT_GROUP},
        {"type": "field", "domain": ru_domains, "balancerTag": RU_GROUP},
    ]
    tail = []
    if google_ips:
        tail.append({"type": "field", "ip": google_ips, "balancerTag": GOOGLE_GROUP})
    tail.append({"type": "field", "network": "tcp,udp", "outboundTag": "fr"})

    routing["rules"] = head + inbound_rules + tail
    routing["balancers"] = balancers(GROUPS)
    cfg["observatory"] = OBSERVATORY
    return cfg


def main() -> int:
    cfg = json.loads(CONFIG.read_text())
    cmd = sys.argv[1] if len(sys.argv) > 1 else "install"

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

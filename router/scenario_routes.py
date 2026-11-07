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
    # Google: France is primary, Finland only takes over when France is dead
    # (both pass the Gemini geo verdict, but France is the one we sell).
    GOOGLE_GROUP: ["fr"],
    RU_GROUP: ["ru1"],
}

# Ordered preference used by the watchdog when it prunes/restores a group.
PREFERENCE = {
    GOOGLE_GROUP: ["fr", "fi", "direct"],
    RU_GROUP: ["ru1", "direct"],
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

GOOGLE_DOMAINS = ["domain:google.com", "domain:googleapis.com", "domain:gstatic.com",
                  "domain:googleusercontent.com", "domain:withgoogle.com", "domain:google.dev"]

# Probe URL must answer from every exit we own: gstatic times out from the RU box,
# which used to make leastPing treat the RU group as empty and stall .ru traffic.
OBSERVATORY = {
    "subjectSelector": ["fr", "fi", "p15", "p16", "p17", "ru1"],
    "probeUrl": "https://cp.cloudflare.com/generate_204",
    "probeInterval": "30s",
    "enableConcurrency": True,
}


def balancers(selectors: dict) -> list:
    return [{"tag": tag, "selector": selectors[tag], "strategy": {"type": "leastPing"}}
            for tag in (GOOGLE_GROUP, RU_GROUP)]


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
        {"type": "field", "domain": ru_domains, "balancerTag": RU_GROUP},
    ]
    tail = []
    if google_ips:
        tail.append({"type": "field", "ip": google_ips, "balancerTag": GOOGLE_GROUP})
    tail.append({"type": "field", "network": "tcp,udp", "outboundTag": "fr"})

    routing["rules"] = head + inbound_rules + tail
    routing["balancers"] = balancers(GROUPS)
    obs = dict(OBSERVATORY)
    obs["subjectSelector"] = sorted({t for tags in PREFERENCE.values() for t in tags if t != "direct"})
    cfg["observatory"] = obs
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

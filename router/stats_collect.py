#!/usr/bin/env python3
"""Per-user VPN traffic stats.

Polls xray's StatsService on the ACTIVE blue/green color, converts the
monotonic counters into deltas (counters reset on every container restart,
which `vpn-cfg apply` does), accumulates them and writes data.json for the
dashboard page.

State:  /var/lib/vpn-stats/state.json   (0630, accumulate + last counters)
Output: /var/www/vpn-stats/data.json    (what the page fetches)
"""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/root/vpn-router")
STATE = Path("/var/lib/vpn-stats/state.json")
OUT = Path("/var/www/vpn-stats/data.json")
XRAY = "/usr/local/bin/xray"
LEGACY = "Главный ключ (мой + Саша)"
WINDOW = 7 * 86400
ROUTE_LABELS = {
    "direct": "Эстония (direct)",
    "ru1": "RU-хост",
    "fr": "Франция (Gemini)",
    "fi": "Финляндия (Gemini)",
}
SKIP_OUT = {"blocked", "api", "warp"}


def active():
    color = (BASE / "vpn-color").read_text().strip()
    name = "vpn-router-b" if color == "b" else "vpn-router"
    port = 11185 if color == "b" else 10185
    return color, name, port


def started_at(container: str) -> str:
    r = subprocess.run(["docker", "inspect", container, "--format", "{{.State.StartedAt}}"],
                       capture_output=True, text=True, timeout=20)
    return r.stdout.strip() if r.returncode == 0 else ""


def query(port: int) -> list:
    r = subprocess.run([XRAY, "api", "statsquery", f"--server=127.0.0.1:{port}", "-pattern", ""],
                       capture_output=True, text=True, timeout=25)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200])
    return json.loads(r.stdout).get("stat") or []


def parse(stats: list) -> tuple[dict, dict]:
    users: dict[str, dict] = {}
    routes: dict[str, int] = {}
    for s in stats:
        parts = str(s.get("name", "")).split(">>>")
        if len(parts) < 4:
            continue
        kind, who, _, direction = parts[0], parts[1], parts[2], parts[3]
        val = int(s.get("value") or 0)
        if kind == "user":
            name = who.split(":", 1)[-1] if ":" in who else LEGACY
            users.setdefault(name, {"up": 0, "down": 0})["up" if direction == "uplink" else "down"] = val
        elif kind == "outbound":
            if who in SKIP_OUT:
                continue
            routes[who] = routes.get(who, 0) + val
    return users, routes


def label(tag: str) -> str:
    if tag in ROUTE_LABELS:
        return ROUTE_LABELS[tag]
    if tag.startswith("p"):
        return "Подписка (узлы провайдера)"
    return tag


def main() -> int:
    color, container, port = active()
    now = int(time.time())
    boot = started_at(container)

    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    st = {
        "version": 1,
        "since": state.get("since", now),
        "boot": state.get("boot", ""),
        "counters": state.get("counters", {}),
        "users": state.get("users", {}),
        "routes": state.get("routes", {}),
        "updated": now,
    }

    try:
        users_now, routes_now = parse(query(port))
    except Exception as exc:  # keep the previous picture instead of blanking the page
        st["error"] = f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {exc}"
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(st, ensure_ascii=False))
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(render(st), ensure_ascii=False))
        return 1

    # A restarted container means every counter went back to zero: take it as a
    # fresh baseline for the user counters instead of counting them twice. Route
    # totals are additive by nature, so they survive the reset.
    rebooted = st["boot"] != boot
    st["boot"] = boot

    for name, cur in users_now.items():
        rec = st["users"].setdefault(name, {"total": 0, "last": 0, "samples": []})
        delta = 0
        for key in ("up", "down"):
            ck = f"u:{name}:{key}"
            prev = st["counters"].get(ck, 0)
            if rebooted:
                delta_add = 0
            else:
                delta_add = cur[key] - prev if cur[key] >= prev else cur[key]
            st["counters"][ck] = cur[key]
            delta += max(delta_add, 0)
        rec["total"] += delta
        if delta > 0:
            rec["last"] = now
            rec["samples"].append([now, delta])
        rec["samples"] = [s for s in rec["samples"] if now - s[0] <= WINDOW]

    for tag, cur in routes_now.items():
        ck = f"r:{tag}"
        prev = st["counters"].get(ck, 0)
        add = 0 if rebooted else (cur - prev if cur >= prev else cur)
        st["counters"][ck] = cur
        key = label(tag)
        st["routes"][key] = st["routes"].get(key, 0) + max(add, 0)

    st.pop("error", None)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(render(st), ensure_ascii=False))
    return 0


def mb(n: float) -> float:
    return round(n / 1048576, 2)


def render(st: dict) -> dict:
    now = int(time.time())
    users = []
    for name, rec in st["users"].items():
        h24 = sum(d for ts, d in rec["samples"] if now - ts <= 86400)
        d7 = sum(d for ts, d in rec["samples"] if now - ts <= WINDOW)
        users.append({
            "name": name,
            "h24": mb(h24),
            "d7": mb(d7),
            "total": mb(rec["total"]),
            "last": rec["last"] or 0,
        })
    users.sort(key=lambda u: (-u["h24"], -u["d7"]))
    routes = [{"tag": t, "label": t, "total": mb(v)} for t, v in st["routes"].items() if v > 0]
    routes.sort(key=lambda r: -r["total"])
    return {
        "generated_at": now,
        "since": st["since"],
        "updated": st["updated"],
        "color": (BASE / "vpn-color").read_text().strip(),
        "users": users,
        "routes": routes,
        "error": st.get("error", ""),
    }


if __name__ == "__main__":
    raise SystemExit(main())

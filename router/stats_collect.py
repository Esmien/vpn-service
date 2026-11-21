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
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/root/vpn-router")
STATE = Path("/var/lib/vpn-stats/state.json")
OUT = Path("/var/www/vpn-stats/data.json")
SUBS = BASE / "subs.json"
XRAY = "/usr/local/bin/xray"
UA = "Happ/2.11.0"
LEGACY_KEY = "@legacy"          # bare client (email == inbound tag) = main shared key
LABELS = {                       # display names only; state keys stay stable
    LEGACY_KEY: "общий ключ (без имени)",
    "Владислав": "Владислав (главный)",
}
WINDOW = 7 * 86400
ROUTE_LABELS = {
    "direct": "Эстония (основной)",
    "nl": "Нидерланды (Gemini)",
    "fr": "Gemini (UI VPN)",
    "fi": "Gemini (UI VPN) — резерв",
    "p15": "Москва (UI VPN)",
    "p17": "Новосибирск (UI VPN)",
    "ru1": "Москва (НАШ)",
    "ru": "Москва (НАШ) — резерв",
}
# Подписи маршрутов переименованы в формат «Город (чье)»; state ключуется подписью,
# поэтому старые ключи надо слить с новыми, иначе накопленный итог обнулится.
RENAMED = {
    "UI VPN — Москва": "Москва (UI VPN)",
    "UI VPN — Новосибирск": "Новосибирск (UI VPN)",
    "ru1": "Москва (НАШ)",
    "RU-хост": "Москва (НАШ) — резерв",
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
            # One person can hold a client in several inbounds, and every inbound
            # keeps its own counter. Keying by name alone used to overwrite one
            # counter with another and then count that foreign value as traffic.
            inbound, name = (who.split(":", 1) if ":" in who else (who, LEGACY_KEY))
            users.setdefault(name, {})[f"{inbound}:{direction}"] = val
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

    # Миграция старых подписей маршрутов: суммы складываются, ключ уходит.
    for old, new in RENAMED.items():
        if old in st["routes"]:
            st["routes"][new] = st["routes"].get(new, 0) + st["routes"].pop(old)

    # Счётчик провайдера обновляем каждый прогон; недоступен — держим прошлый.
    st["provider"] = provider_usage() or st.get("provider", {})

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
        for key, val in cur.items():
            # The counter key carries the container and the inbound, so a value
            # from another process or another inbound can never be mistaken for
            # growth of this one.
            ck = f"u:{container}:{name}:{key}"
            prev = st["counters"].get(ck)
            st["counters"][ck] = val
            if prev is None or rebooted:
                continue          # first sight or a fresh container: baseline only
            if val >= prev:
                delta += val - prev
            # val < prev без смены контейнера значит, что счётчик обнулили: это
            # тоже baseline, а не «вся накопленная величина разом».
        rec["total"] += delta
        if delta > 0:
            rec["last"] = now
            rec["samples"].append([now, delta])
        rec["samples"] = [s for s in rec["samples"] if now - s[0] <= WINDOW]

    for tag, cur in routes_now.items():
        ck = f"r:{container}:{tag}"
        prev = st["counters"].get(ck)
        st["counters"][ck] = cur
        key = label(tag)
        if prev is None or rebooted or cur < prev:
            continue
        st["routes"][key] = st["routes"].get(key, 0) + (cur - prev)

    st.pop("error", None)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(render(st), ensure_ascii=False))
    return 0


def mb(n: float) -> float:
    return round(n / 1048576, 2)


def provider_usage() -> dict:
    """Расход по подписке провайдера из заголовка `subscription-userinfo`.

    По белому списку это единственная цифра вообще: клиент в таком режиме
    ходит на узлы провайдера напрямую, поэтому ни `user>>>`, ни `outbound>>>`
    его трафик не видят. Счётчик аккаунтный: не по маршрутам и не по людям.
    """
    out = {}
    try:
        subs = json.loads(SUBS.read_text())
    except (OSError, ValueError):
        return out
    for item in subs:
        name, url = item.get("name", ""), item.get("url", "")
        if not url:
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                info = resp.headers.get("subscription-userinfo", "")
        except Exception:
            continue
        vals = {}
        for part in info.split(";"):
            key, _, val = part.partition("=")
            key, val = key.strip(), val.strip()
            if key and val.isdigit():
                vals[key] = int(val)
        if not vals:
            continue
        slot = "whitelist" if "глушил" in name.lower() else "relays"
        out[slot] = {
            "name": name,
            "used": vals.get("download", 0) + vals.get("upload", 0),
            "total": vals.get("total", 0),
            "expire": vals.get("expire", 0),
        }
    return out


def page_links() -> dict:
    """token -> /u/ page, so the dashboard can hand out links in one click."""
    try:
        users = json.loads((BASE / "users.json").read_text())
    except (OSError, ValueError):
        return {}
    return {name: f"https://share.esmien.com/u/{u['token']}"
            for name, u in users.items() if u.get("token")}


def render(st: dict) -> dict:
    now = int(time.time())
    pages = page_links()
    for name in pages:  # показываем всех, кому выдан ключ, даже если трафика ещё не было
        st["users"].setdefault(name, {"total": 0, "last": 0, "samples": []})
    users = []
    for name, rec in st["users"].items():
        h24 = sum(d for ts, d in rec["samples"] if now - ts <= 86400)
        d7 = sum(d for ts, d in rec["samples"] if now - ts <= WINDOW)
        users.append({
            "key": name,
            "deletable": not name.startswith("@"),
            "page": pages.get(name, ""),
            "name": LABELS.get(name, name),
            "h24": mb(h24),
            "d7": mb(d7),
            "total": mb(rec["total"]),
            "last": rec["last"] or 0,
        })
    users.sort(key=lambda u: (-u["h24"], -u["d7"]))
    routes = [{"tag": t, "label": t, "total": mb(v)} for t, v in st["routes"].items() if v > 0]
    routes.sort(key=lambda r: -r["total"])
    provider = [{"key": k, "name": v.get("name", ""), "used": mb(v.get("used", 0)),
                 "total": mb(v.get("total", 0)), "expire": v.get("expire", 0)}
                for k, v in st.get("provider", {}).items()]
    return {
        "generated_at": now,
        "since": st["since"],
        "updated": st["updated"],
        "color": (BASE / "vpn-color").read_text().strip(),
        "users": users,
        "routes": routes,
        "provider": provider,
        "error": st.get("error", ""),
    }


if __name__ == "__main__":
    import fcntl
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open("/var/lib/vpn-stats/lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(0)  # другой сбор уже идёт — молча уступаем
        raise SystemExit(main())

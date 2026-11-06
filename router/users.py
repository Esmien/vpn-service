#!/usr/bin/env python3
"""Per-user VPN keys: one UUID + one subscription token per person.

users.json (0600) is the source of truth. Each user's UUID is injected as a
client into EVERY vless inbound, so the person's link works on every route.
The legacy global UUID stays in place untouched — already-issued links keep
working, so the rollout is additive and reversible.

Usage:
    users.py list
    users.py add <name> [--title "Esmien VPN (Имя)"]
    users.py url  <name>
    users.py rm   <name>          # drop clients + park the token dir (no data loss)
    users.py sync                 # re-apply users.json to config.json (after import_sub)
"""
import json
import secrets
import subprocess
import sys
from datetime import date
from pathlib import Path

BASE = Path("/root/vpn-router")
CONFIG = BASE / "config.json"
USERS = BASE / "users.json"
SUB_ROOT = Path("/var/www/esmien-sub")
PARKED = SUB_ROOT / ".disabled"
IMAGE = "ghcr.io/xtls/xray-core:latest"

sys.path.insert(0, str(BASE))


def load() -> dict:
    if USERS.exists():
        return json.loads(USERS.read_text())
    return {}


def save(users: dict) -> None:
    USERS.write_text(json.dumps(users, ensure_ascii=False, indent=2) + "\n")
    USERS.chmod(0o600)


def clients_of(inbound: dict) -> list:
    return inbound.setdefault("settings", {}).setdefault("clients", [])


def apply_users(cfg: dict, users: dict) -> int:
    """Ensure every user has a client in every vless inbound. Returns count added."""
    added = 0
    for ib in cfg.get("inbounds", []):
        if ib.get("protocol") != "vless":
            continue
        cl = clients_of(ib)
        emails = {c.get("email") for c in cl}
        for name, u in users.items():
            email = f"{ib['tag']}:{name}"
            if email in emails:
                continue
            cl.append({"id": u["uuid"], "email": email})
            added += 1
    return added


def drop_user_clients(cfg: dict, name: str) -> int:
    removed = 0
    for ib in cfg.get("inbounds", []):
        cl = clients_of(ib)
        keep = [c for c in cl
                if c.get("email") != f"{ib['tag']}:{name}"
                and c.get("id") != load().get(name, {}).get("uuid")]
        removed += len(cl) - len(keep)
        ib["settings"]["clients"] = keep
    return removed


def validate() -> tuple[bool, str]:
    r = subprocess.run(
        ["docker", "run", "--rm", "--user", "0:0", "--entrypoint", "/usr/local/bin/xray",
         "-v", f"{CONFIG}:/etc/xray/config.json:ro", IMAGE, "run", "-test", "-c", "/etc/xray/config.json"],
        capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-600:]


def write_sub(title: str) -> None:
    import apply_brand
    apply_brand.write_title_map()


def render_subs() -> None:
    subprocess.run(["python3", str(BASE / "make_sub.py")], check=True,
                   capture_output=True, text=True)


def apply_live() -> None:
    subprocess.run(["vpn-cfg", "apply"], check=False)


def url_of(token: str) -> str:
    brand = json.loads((BASE / "brand.json").read_text())
    return f"https://{brand['host']}:{brand['port']}/sub/{token}"


def cmd_add(name: str, title: str | None) -> int:
    users = load()
    if name in users:
        print(f"уже есть: {name} -> {url_of(users[name]['token'])}")
        return 1
    cfg = json.loads(CONFIG.read_text())
    token = secrets.token_urlsafe(14).replace("-", "").replace("_", "")[:18]
    users[name] = {"uuid": str(__import__("uuid").uuid4()), "token": token,
                   "title": title or f"Esmien VPN ({name})", "created": date.today().isoformat()}
    added = apply_users(cfg, users)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    ok, out = validate()
    if not ok:
        print("xray -test провалился, откат не нужен: config.json не применён")
        print(out)
        return 1
    d = SUB_ROOT / token
    d.mkdir(parents=True, exist_ok=True)
    (d / "user").write_text(name + "\n")
    (d / "title").write_text(users[name]["title"] + "\n")
    save(users)
    render_subs()
    write_sub(users[name]["title"])
    subprocess.run(["nginx", "-t"], check=False, capture_output=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=False)
    apply_live()
    print(f"+ {name}: клиентов добавлено {added}, token {token}")
    print(f"  ссылка: {url_of(token)}")
    return 0


def cmd_rm(name: str) -> int:
    users = load()
    if name not in users:
        print(f"нет такого: {name}")
        return 1
    cfg = json.loads(CONFIG.read_text())
    removed = drop_user_clients(cfg, name)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    ok, out = validate()
    if not ok:
        print("xray -test провалился — ничего не применено")
        print(out)
        return 1
    token = users[name]["token"]
    src = SUB_ROOT / token
    if src.exists():
        PARKED.mkdir(exist_ok=True)
        dst = PARKED / f"{token}.{name}"
        if dst.exists():
            dst = PARKED / f"{token}.{name}.{secrets.token_hex(3)}"
        src.rename(dst)
        print(f"  подписка убрана в {dst}")
    del users[name]
    save(users)
    render_subs()
    write_sub(name)
    subprocess.run(["systemctl", "reload", "nginx"], check=False)
    apply_live()
    print(f"- {name}: клиентов удалено {removed}")
    return 0


def cmd_sync() -> int:
    users = load()
    cfg = json.loads(CONFIG.read_text())
    added = apply_users(cfg, users)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    ok, out = validate()
    if not ok:
        print("xray -test провалился — config.json не применён")
        print(out)
        return 1
    render_subs()
    print(f"sync: клиентов добавлено {added}, подписки перегенерированы")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    if not args or args[0] == "list":
        users = load()
        if not users:
            print("пользователей нет (только глобальный ключ)")
            return 0
        for n, u in users.items():
            print(f"{n:<12} uuid …{u['uuid'][-6:]}  token {u['token']}  {u['title']}")
        return 0
    cmd = args[0]
    if cmd == "add":
        title = None
        if "--title" in args:
            title = args[args.index("--title") + 1]
        return cmd_add(args[1], title)
    if cmd == "rm":
        return cmd_rm(args[1])
    if cmd == "url":
        u = load().get(args[1])
        print(url_of(u["token"]) if u else f"нет такого: {args[1]}")
        return 0 if u else 1
    if cmd == "sync":
        return cmd_sync()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())

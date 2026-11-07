#!/usr/bin/env python3
"""Per-user VPN keys: each person gets a PAIR of subscriptions.

users.json (0600) is the source of truth. Every user's UUID is injected as a
client into EVERY vless inbound, so the person's link works on every route.
The legacy global UUID stays in place untouched — already-issued links keep
working, so the rollout is additive and reversible.

The pair is: main bundle (our routes, the person's own UUID) + LTE bundle
(whitelist / «глушилки» nodes, reached by clients directly — its content is the
provider's links, so those keys are shared no matter how many tokens exist).

Usage:
    users.py list
    users.py add <name> [--title "Esmien VPN (Имя)"] [--uuid U] [--token T] [--lte-token T] [--no-lte]
    users.py url  <name>
    users.py rm   <name>          # drop clients + park both token dirs (no data loss)
    users.py sync                 # re-apply users.json to config.json (after import_sub)
"""
import json
import secrets
import subprocess
import sys
import uuid as uuidlib
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
        ids = {c.get("id") for c in cl}
        for name, u in users.items():
            email = f"{ib['tag']}:{name}"
            if email in emails or u["uuid"] in ids:  # legacy client already carries this uuid
                continue
            cl.append({"id": u["uuid"], "email": email})
            added += 1
    return added


def drop_user_clients(cfg: dict, name: str) -> int:
    users = load()
    uid = users.get(name, {}).get("uuid")
    removed = 0
    for ib in cfg.get("inbounds", []):
        if ib.get("protocol") != "vless":
            continue
        cl = clients_of(ib)
        keep = [c for c in cl if c.get("email") != f"{ib['tag']}:{name}" and c.get("id") != uid]
        removed += len(cl) - len(keep)
        ib["settings"]["clients"] = keep
    return removed


def validate() -> tuple[bool, str]:
    r = subprocess.run(
        ["docker", "run", "--rm", "--user", "0:0", "--entrypoint", "/usr/local/bin/xray",
         "-v", f"{CONFIG}:/etc/xray/config.json:ro", IMAGE, "run", "-test", "-c", "/etc/xray/config.json"],
        capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[-600:]


def write_title_map() -> None:
    import apply_brand
    apply_brand.write_title_map()


def render_subs() -> None:
    subprocess.run(["python3", str(BASE / "make_sub.py")], check=True,
                   capture_output=True, text=True)
    subprocess.run(["python3", str(BASE / "make_pages.py")], check=False,
                   capture_output=True, text=True)


def apply_live() -> None:
    subprocess.run(["vpn-cfg", "apply"], check=False)


def url_of(token: str) -> str:
    brand = json.loads((BASE / "brand.json").read_text())
    return f"https://{brand['host']}:{brand['port']}/sub/{token}"


def page_of(token: str) -> str:
    brand = json.loads((BASE / "brand.json").read_text())
    return f"https://{brand['host']}:{brand['port']}/u/{token}"


def new_token() -> str:
    return secrets.token_urlsafe(14).replace("-", "").replace("_", "")[:18]


def make_dir(token: str, name: str, title: str, kind: str | None = None) -> Path:
    d = SUB_ROOT / token
    d.mkdir(parents=True, exist_ok=True)
    (d / "user").write_text(name + "\n")
    (d / "title").write_text(title + "\n")
    if kind:
        (d / "kind").write_text(kind + "\n")
    return d


def lte_title(title: str) -> str:
    """«Esmien VPN (Наташа)» → «Esmien VPN LTE (Наташа)»: LTE впереди, чтобы имя было видно в списке профилей."""
    return title.replace(" (", " LTE (", 1) if " (" in title else f"{title} LTE"


def cmd_add(name: str, title: str | None, uid: str | None,
            token: str | None, lte_token: str | None, with_lte: bool = True) -> int:
    users = load()
    if name in users:
        print(f"уже есть: {name}")
        print_pair(name, users[name])
        return 1
    title = title or f"Esmien VPN ({name})"
    uid = uid or str(uuidlib.uuid4())
    token = token or new_token()
    lte_token = lte_token if lte_token else (new_token() if with_lte else "")

    cfg = json.loads(CONFIG.read_text())
    users[name] = {"uuid": uid, "token": token, "token_lte": lte_token,
                   "title": title, "created": date.today().isoformat()}
    added = apply_users(cfg, users)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    ok, out = validate()
    if not ok:
        print("xray -test провалился — config.json не применён, ничего не меняю")
        print(out)
        return 1

    make_dir(token, name, title, kind="meta")
    if lte_token:
        make_dir(lte_token, name, lte_title(title), kind="whitelist")
    save(users)
    render_subs()
    write_title_map()
    subprocess.run(["nginx", "-t"], check=False, capture_output=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=False)
    apply_live()
    print(f"+ {name}: клиентов добавлено {added}")
    print_pair(name, users[name])
    return 0


def print_pair(name: str, u: dict) -> None:
    print(f"  {name} — страница: {page_of(u['token'])}")
    print(f"  {name} — основная: {url_of(u['token'])}")
    if u.get("token_lte"):
        print(f"  {name} — LTE:      {url_of(u['token_lte'])}")


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
    PARKED.mkdir(exist_ok=True)
    for token in (users[name]["token"], users[name].get("token_lte")):
        if not token:
            continue
        src = SUB_ROOT / token
        if src.exists():
            dst = PARKED / f"{token}.{name}"
            if dst.exists():
                dst = PARKED / f"{token}.{name}.{secrets.token_hex(3)}"
            src.rename(dst)
            print(f"  подписка убрана в {dst}")
    del users[name]
    save(users)
    render_subs()
    write_title_map()
    subprocess.run(["systemctl", "reload", "nginx"], check=False)
    apply_live()
    print(f"- {name}: клиентов удалено {removed}")
    return 0


def cmd_retitle() -> int:
    """Rewrite the LTE profile titles of existing users to the current scheme."""
    users = load()
    n = 0
    for name, u in users.items():
        token = u.get("token_lte")
        if not token or not (SUB_ROOT / token).exists():
            continue
        (SUB_ROOT / token / "title").write_text(lte_title(u["title"]) + "\n")
        n += 1
    write_title_map()
    subprocess.run(["systemctl", "reload", "nginx"], check=False)
    print(f"retitle: обновлено {n} LTE-подписок")
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


def arg(args: list, flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args and len(args) > args.index(flag) + 1 else None


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] == "list":
        users = load()
        if not users:
            print("пользователей нет (только глобальный ключ)")
            return 0
        for n, u in users.items():
            lte = u.get("token_lte") or "—"
            print(f"{n:<12} uuid …{u['uuid'][-6:]}  main {u['token']}  lte {lte}  {u['title']}")
        return 0
    cmd = args[0]
    if cmd == "add":
        return cmd_add(args[1], arg(args, "--title"), arg(args, "--uuid"),
                       arg(args, "--token"), arg(args, "--lte-token"), "--no-lte" not in args)
    if cmd == "rm":
        return cmd_rm(args[1])
    if cmd == "url":
        u = load().get(args[1])
        if not u:
            print(f"нет такого: {args[1]}")
            return 1
        print_pair(args[1], u)
        return 0
    if cmd == "sync":
        return cmd_sync()
    if cmd == "retitle":
        return cmd_retitle()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())

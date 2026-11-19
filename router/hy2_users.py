#!/usr/bin/env python3
"""Give every meta subscription an extra Hysteria2 node (UDP/443).

One password per person, so a single node can be revoked without touching the
rest. State lives in /root/.secrets/hysteria-users.json (0600) — it is the source
of truth: existing passwords are kept, new users get one on the first run.

What it rewrites:
  * the `auth:` block of /etc/hysteria/config.yaml (type userpass);
  * /var/www/esmien-sub/<token>/extra_nodes.json for every `meta` token.

Then run `python3 /root/vpn-router/make_sub.py` (or use --apply, which does it)
and `systemctl restart hysteria-server`.

Usage:
    python3 hy2_users.py            # sync config + extra nodes
    python3 hy2_users.py --apply    # also rebuild subscriptions and restart
"""
import json
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path("/root/vpn-router")
SUBS = Path("/var/www/esmien-sub")
HY_CFG = Path("/etc/hysteria/config.yaml")
STATE = Path("/root/.secrets/hysteria-users.json")
HOST = "vpn.esmien.ru"
PORT = 8443
NODE = "🇪🇪 Эстония (запасной)"

TRANSLIT = {
    "Владислав": "vladislav", "Игорь": "igor", "Оля": "olya", "Антон": "anton",
    "Юля": "yulya", "Наташа": "natasha", "Валера": "valera", "Даша": "dasha",
}


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    STATE.chmod(0o600)


def username_for(name: str, token: str) -> str:
    return TRANSLIT.get(name) or token[:10]


def obfs_password() -> str:
    """The salamander password is global (shared by every client), keep it stable."""
    cfg = HY_CFG.read_text()
    i = cfg.find("\nobfs:")
    if i < 0:
        raise SystemExit("no obfs block in hysteria config")
    tail = cfg[i:]
    j = tail.find('password: "')
    return tail[j + len('password: "'):].split('"')[0]


def write_auth_block(users: dict) -> None:
    cfg = HY_CFG.read_text()
    start = cfg.find("auth:")
    end = cfg.find("\n\n", start)
    if start < 0 or end < 0:
        raise SystemExit("cannot locate the auth block")
    lines = ["auth:", "  type: userpass", "  userpass:"]
    for user, data in users.items():
        lines.append(f'    {user}: "{data["password"]}"')
    HY_CFG.write_text(cfg[:start] + "\n".join(lines) + cfg[end:])


def link_for(password: str, obfs: str, user: str) -> str:
    import urllib.parse
    q = urllib.parse.urlencode(
        {"sni": HOST, "obfs": "salamander", "obfs-password": obfs})
    name = urllib.parse.quote(NODE)
    return f"hysteria2://{user}:{password}@{HOST}:{PORT}/?{q}#{name}"


def clash_for(password: str, obfs: str, user: str) -> str:
    return (f"  - name: {json.dumps(NODE, ensure_ascii=False)}\n"
            "    type: hysteria2\n"
            f"    server: {HOST}\n"
            f"    port: {PORT}\n"
            f'    password: "{password}"\n'
            f'    username: "{user}"\n'
            f"    sni: {HOST}\n"
            "    obfs: salamander\n"
            f'    obfs-password: "{obfs}"\n'
            '    up: "200 Mbps"\n'
            '    down: "500 Mbps"\n')


def main() -> int:
    users_meta = {name: data for name, data in
                  json.loads((ROOT / "users.json").read_text()).items()
                  if data.get("token")}
    state = load_state()
    obfs = obfs_password()
    changed = False
    for name, data in users_meta.items():
        token = data["token"]
        user = username_for(name, token)
        if user not in state:
            state[user] = {"password": secrets.token_urlsafe(18), "name": name,
                           "token": token}
            changed = True
    if changed:
        save_state(state)
    write_auth_block(state)

    for name, data in users_meta.items():
        token = data["token"]
        d = SUBS / token
        if not d.is_dir():
            continue
        user = username_for(name, token)
        pw = state[user]["password"]
        node = {"name": NODE, "link": link_for(pw, obfs, user),
                "clash": clash_for(pw, obfs, user)}
        # Чужие доп. узлы (владельцу выдаются вручную: FR/NL для Gemini) не затираем:
        # пересборка UDP-узла не должна сносить то, что добавлено не ей.
        keep = []
        ep = d / "extra_nodes.json"
        if ep.exists():
            try:
                keep = [e for e in json.loads(ep.read_text())
                        if isinstance(e, dict) and not str(e.get("link", "")).startswith("hysteria2://")]
            except (ValueError, OSError):
                keep = []
        ep.write_text(json.dumps([node] + keep, ensure_ascii=False, indent=1))
        print(f"{name:12s} {token}  -> {user}")

    if "--apply" in sys.argv:
        subprocess.run([sys.executable, str(ROOT / "make_sub.py")],
                       check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["systemctl", "restart", "hysteria-server"], check=False)
        print("subscriptions rebuilt, hysteria restarted")
    return 0


if __name__ == "__main__":
    sys.exit(main())

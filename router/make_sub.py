#!/usr/bin/env python3
"""Generate the Esmien VPN subscription bundle (clash.yaml / list.txt / singbox.json).

Nodes:
  * our own routes on HOST (/general, /france, /fi, /test) plus every upstream node
    relayed through our server (/p<i>, see relay_nodes.json);
  * whitelist (RU entry) nodes taken verbatim from whitelist_links.json — those
    bypass our server on purpose, so they survive when only RU addresses pass.

Usage:
    python3 make_sub.py            # rewrite existing token dirs
    python3 make_sub.py --new      # create a new token dir
"""
import base64
import json
import sys
import urllib.parse
from pathlib import Path

_BRAND = json.loads(Path("/root/vpn-router/brand.json").read_text())
HOST = _BRAND["host"]
PORT = _BRAND["port"]
ROOT = Path("/var/www/esmien-sub")
UID_FILE = Path("/root/mtproto-faketls/ws-client-uuid")
RELAY_FILE = Path("/root/vpn-router/relay_nodes.json")
WHITE_FILE = Path("/root/vpn-router/whitelist_links.json")
USERS_FILE = Path("/root/vpn-router/users.json")

EE = "🇪🇪 Эстония (General)"
FR = "🇫🇷 Франция (Gemini)"
FI = "🇫🇮 Финляндия (Gemini резерв)"
TEST = "🛠 Тест (Develop)"
IP = "141.98.166.213"  # вход по IP: работает, даже если DNS домена лежит
WHITE_GROUP = "🛡 Белые списки (LTE)"

# --- Подписка «одна модель»: всё, что видит пользователь — один узел ---
# Сценарии (Google -> FR/FI, YouTube -> AL, RU -> Москва) живут на сервере,
# в выдаче их нет: лишний узел в списке = чужой трафик через провайдерскую
# подписку и риск бана, а не удобство.
META_TAG = "🇪🇪 Эстония (все сервисы)"

META_ANNOUNCE = """\
🇪🇪 Esmien VPN — Эстония
✨ Один узел, все сценарии внутри: Gemini · 🇷🇺 RU-сайты без выключения VPN · 📺 YouTube без рекламы · 💬 TG, WhatsApp, Instagram и прочее
👀 Нет списка моделей — так задумано: маршруты решает сервер, выбирать нечего
🐢 RU-сайты открываются чуть медленнее: запрос идёт через несколько серверов, это норма
🛟 Сценарий отвалился? 5–10 секунд — и автоматика вернёт вас на Эстонию\
"""

META_FULL = """\
🇪🇪 Esmien VPN — одна модель, все сценарии внутри

Сервер «Эстония» покрывает всё:
• 🤖 Gemini
• 🇷🇺 Российские сайты — БЕЗ выключения VPN
  (открытие может быть чуть медленнее: запрос идёт через несколько серверов, это нормально)
• 📺 YouTube без рекламы
• 💬 Заблокированные сервисы — TG, WhatsApp, Instagram и прочее

👀 Не переживайте, что нет кучи моделей, как в других подписках.
Они есть — просто логика спрятана в сценариях, выбирать ничего не нужно.

🛟 Если какой-то сценарий отвалится, вы увидите задержку 5–10 секунд:
автоматика переключит вас на Эстонию, и всё продолжит работать.\
"""

# --- RU-трафик (банки, госуслуги, VK/MAX, OZON, WB) идёт через московский узел пула ---
# Домены, которые не попадают под .ru-суффикс, но живут только в РФ:
RU_DOMAINS = ["vk.com", "vk.me", "vk-cdn.net", "userapi.com", "mycdn.me", "max.ru",
              "ozon.com", "gosuslugi.ru", "sberbank.ru", "tbank.ru", "vtb.ru",
              "alfabank.ru", "wildberries.ru", "wb.ru", "wbstatic.net"]


def ru_node() -> str:
    """Имя московского узла из пула («Москва - YouTube/ГосУслуги/Банки»)."""
    relay, _ = load_nodes()
    for n in relay:
        if "Москва" in n["name"].replace("\u200b", ""):
            return n["name"]
    return EE


def vless(uid: str, path: str, tag: str, addr: str = HOST) -> str:
    query = {"type": "ws", "security": "tls", "sni": HOST, "host": HOST, "path": path, "encryption": "none"}
    return f"vless://{uid}@{addr}:{PORT}?" + urllib.parse.urlencode(query) + "#" + urllib.parse.quote(tag)


def clash_proxy(uid: str, name: str, path: str, addr: str = HOST) -> str:
    return f"""  - name: {json.dumps(name, ensure_ascii=False)}
    type: vless
    server: {addr}
    port: {PORT}
    uuid: {uid}
    network: ws
    tls: true
    udp: true
    sni: {HOST}
    servername: {HOST}
    client-fingerprint: chrome
    alpn: [http/1.1]
    ws-opts:
      path: "{path}"
      headers:
        Host: {HOST}
"""


def clash_proxy_from_link(link: str, name: str) -> str:
    """Proxy entry for an upstream link the client must reach directly (whitelist)."""
    u = urllib.parse.urlparse(link)
    q = dict(urllib.parse.parse_qsl(u.query))
    net = q.get("type", "tcp")
    sec = q.get("security", "none")
    body = (f"  - name: {json.dumps(name, ensure_ascii=False)}\n    type: {u.scheme}\n"
            f"    server: {u.hostname}\n    port: {u.port}\n")
    if u.scheme == "vless":
        body += f"    uuid: {urllib.parse.unquote(u.username or '')}\n    udp: true\n"
        if q.get("flow"):
            body += f"    flow: {q['flow']}\n"
    else:
        body += f"    password: {urllib.parse.unquote(u.username or '')}\n    udp: true\n"
    body += f"    network: {net}\n    tls: {str(sec in ('tls', 'reality')).lower()}\n"
    if sec in ("tls", "reality"):
        body += f"    sni: {q.get('sni') or u.hostname}\n    client-fingerprint: {q.get('fp', 'chrome')}\n"
        if q.get("alpn"):
            body += f"    alpn: [{q['alpn']}]\n"
    if sec == "reality":
        body += f"    reality-opts:\n      public-key: {q.get('pbk', '')}\n      short-id: {q.get('sid', '')}\n"
    if net == "grpc":
        body += f"    grpc-opts:\n      grpc-service-name: {q.get('serviceName', '')}\n"
    elif net == "ws":
        body += (f"    ws-opts:\n      path: {json.dumps(q.get('path', '/'))}\n"
                 f"      headers:\n        Host: {q.get('host', u.hostname)}\n")
    return body


def sb_link(link: str, name: str) -> dict:
    u = urllib.parse.urlparse(link)
    q = dict(urllib.parse.parse_qsl(u.query))
    net = q.get("type", "tcp")
    out = {"type": u.scheme, "tag": name, "server": u.hostname, "server_port": u.port}
    if u.scheme == "vless":
        out["uuid"] = urllib.parse.unquote(u.username or "")
    else:
        out["password"] = urllib.parse.unquote(u.username or "")
    if q.get("flow"):
        out["flow"] = q["flow"]
    if net == "grpc":
        out["transport"] = {"type": "grpc", "service_name": q.get("serviceName", "")}
    elif net == "ws":
        out["transport"] = {"type": "ws", "path": q.get("path", "/"),
                            "headers": {"Host": q.get("host", u.hostname)}}
    if q.get("security") in ("tls", "reality"):
        out["tls"] = {"enabled": True, "server_name": q.get("sni") or u.hostname,
                      "utls": {"enabled": True, "fingerprint": q.get("fp", "chrome")}}
        if q.get("security") == "reality":
            out["tls"]["reality"] = {"enabled": True, "public_key": q.get("pbk", ""),
                                     "short_id": q.get("sid", "")}
    return out

def load_nodes():
    relay = json.loads(RELAY_FILE.read_text()) if RELAY_FILE.exists() else []
    white = json.loads(WHITE_FILE.read_text()) if WHITE_FILE.exists() else []
    return relay, white


def token_uid(token_dir: Path) -> str:
    """UUID for this token dir: per-user key when the dir names its user, else the global one."""
    uf = token_dir / "user"
    if uf.exists():
        name = uf.read_text().strip()
        users = json.loads(USERS_FILE.read_text()) if USERS_FILE.exists() else {}
        if name in users:
            return users[name]["uuid"]
        print(f"warn: {token_dir.name}: пользователь «{name}» не найден в users.json — глобальный uid")
    return UID_FILE.read_text().strip()


def build_whitelist(token_dir: Path, uid: str) -> None:
    """Bundle with the whitelist (RU entry) nodes only — clients reach them directly."""
    _, white = load_nodes()
    links = [w["link"] for w in white]
    (token_dir / "list.txt").write_text(base64.b64encode(("\n".join(links) + "\n").encode()).decode() + "\n")

    proxies = "".join(clash_proxy_from_link(w["link"], w["name"]) for w in white)
    names = "".join(f'      - {json.dumps(w["name"], ensure_ascii=False)}\n' for w in white)
    (token_dir / "clash.yaml").write_text(f"""# Esmien VPN (LTE) — белые списки
# Узлы подключаются напрямую, мимо нашего сервера: работают, когда провайдер режет всё, кроме РФ-адресов
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false

dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver: [1.1.1.1, 8.8.8.8]

proxies:
{proxies}proxy-groups:
  - name: "{WHITE_GROUP}"
    type: select
    proxies:
{names}
rules:
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - MATCH,{WHITE_GROUP}
""")

    outs = [sb_link(w["link"], w["name"]) for w in white] + [{"type": "direct", "tag": "direct"}]
    sing = {"log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 2080}],
            "outbounds": outs,
            "route": {"rules": [{"outbound": white[0]["name"] if white else "direct"}]}}
    (token_dir / "singbox.json").write_text(json.dumps(sing, indent=2, ensure_ascii=False))
    token_dir.chmod(0o755)


def build_meta(token_dir: Path, uid: str) -> None:
    """One visible node. Every scenario is decided by the server, not by the client."""
    path = _BRAND["paths"]["general"]
    link = vless(uid, path, META_TAG)
    (token_dir / "list.txt").write_text(base64.b64encode((link + "\n").encode()).decode() + "\n")
    (token_dir / "announce").write_text(META_ANNOUNCE + "\n", encoding="utf-8")

    header = "\n".join("# " + line for line in META_FULL.splitlines())
    proxies = clash_proxy(uid, META_TAG, path)
    (token_dir / "clash.yaml").write_text(f"""{header}
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false

dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver: [1.1.1.1, 8.8.8.8]

proxies:
{proxies}proxy-groups:
  - name: "Esmien VPN"
    type: select
    proxies:
      - {json.dumps(META_TAG, ensure_ascii=False)}
rules:
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
  - MATCH,Esmien VPN
""", encoding="utf-8")

    out = {"type": "vless", "tag": META_TAG, "server": HOST, "server_port": PORT, "uuid": uid,
           "transport": {"type": "ws", "path": path, "headers": {"Host": HOST}},
           "tls": {"enabled": True, "server_name": HOST}}
    sing = {"log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1",
                          "listen_port": 2080}],
            "outbounds": [out, {"type": "direct", "tag": "direct"}],
            "route": {"rules": [{"outbound": META_TAG}]}}
    (token_dir / "singbox.json").write_text(json.dumps(sing, indent=2, ensure_ascii=False),
                                            encoding="utf-8")
    token_dir.chmod(0o755)


def build(token_dir: Path, uid: str) -> None:
    kind_file = token_dir / "kind"
    kind = kind_file.read_text().strip() if kind_file.exists() else ""
    if kind == "whitelist":
        return build_whitelist(token_dir, uid)
    if kind == "meta":
        return build_meta(token_dir, uid)
    relay, white = load_nodes()
    ours = [(EE, _BRAND["paths"]["general"], HOST), (FR, _BRAND["paths"]["france"], HOST),
            (FI, _BRAND["paths"]["fi"], HOST), (TEST, _BRAND["paths"]["test"], HOST)]
    # дубли основных маршрутов по IP: спасают, когда DNS домена не отвечает
    ours += [(f"{EE} по IP", _BRAND["paths"]["general"], IP),
             (f"{FR} по IP", _BRAND["paths"]["france"], IP)]
    ours += [(n["name"], n["path"], HOST) for n in relay]

    links = [vless(uid, path, name, addr) for name, path, addr in ours]
    (token_dir / "list.txt").write_text(base64.b64encode(("\n".join(links) + "\n").encode()).decode() + "\n")

    ours_proxies = "".join(clash_proxy(uid, name, path, addr) for name, path, addr in ours)
    ours_names = "".join(f'      - {json.dumps(n, ensure_ascii=False)}\n' for n, _, _ in ours)
    ru = ru_node()
    ru_rules = "".join(f"  - DOMAIN-SUFFIX,{d},{ru}\n" for d in RU_DOMAINS)
    ru_rules += f"  - DOMAIN-SUFFIX,ru,{ru}\n  - DOMAIN-SUFFIX,su,{ru}\n"

    (token_dir / "clash.yaml").write_text(f"""# Esmien VPN
# Маршруты через наш сервер (белые списки — отдельная подписка)
mixed-port: 7890
allow-lan: false
mode: rule
log-level: info
ipv6: false

dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  nameserver: [1.1.1.1, 8.8.8.8]

proxies:
{ours_proxies}proxy-groups:
  - name: "Esmien VPN"
    type: select
    proxies:
{ours_names}
rules:
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
  - DOMAIN-SUFFIX,google.com,{FR}
  - DOMAIN-SUFFIX,googleapis.com,{FR}
  - DOMAIN-SUFFIX,gstatic.com,{FR}
  - DOMAIN-SUFFIX,googleusercontent.com,{FR}
  - DOMAIN-SUFFIX,withgoogle.com,{FR}
  - DOMAIN-SUFFIX,google.dev,{FR}
{ru_rules}  - GEOIP,RU,{ru}
  - MATCH,{EE}
""")

    def sb_ours(name: str, path: str, addr: str = HOST) -> dict:
        return {"type": "vless", "tag": name, "server": addr, "server_port": PORT, "uuid": uid,
                "transport": {"type": "ws", "path": path, "headers": {"Host": HOST}},
                "tls": {"enabled": True, "server_name": HOST}}


    sing = {"log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 2080}],
            "outbounds": [sb_ours(n, p, a) for n, p, a in ours] + [{"type": "direct", "tag": "direct"}],
            "route": {"rules": [
                {"domain_suffix": ["google.com", "googleapis.com", "gstatic.com",
                                   "googleusercontent.com", "withgoogle.com"], "outbound": FR},
                {"domain_suffix": [".ru", ".su"] + RU_DOMAINS, "outbound": ru},
                {"geoip": ["ru"], "outbound": ru},
                {"outbound": EE}]}}
    (token_dir / "singbox.json").write_text(json.dumps(sing, indent=2, ensure_ascii=False))
    token_dir.chmod(0o755)


def main() -> int:
    uid = UID_FILE.read_text().strip()
    if "--new-whitelist" in sys.argv:
        import secrets
        token = secrets.token_urlsafe(14).replace("-", "").replace("_", "")[:18]
        target = ROOT / token
        target.mkdir(parents=True, exist_ok=True)
        (target / "kind").write_text("whitelist\n")
        build_whitelist(target, uid)
        print(f"https://{HOST}:{PORT}/sub/{token}")
        return 0
    if "--new" in sys.argv or not any(ROOT.iterdir() if ROOT.is_dir() else []):
        import secrets
        token = secrets.token_urlsafe(14).replace("-", "").replace("_", "")[:18]
        ROOT.mkdir(parents=True, exist_ok=True)
        target = ROOT / token
        target.mkdir(exist_ok=True)
        build(target, uid)
        print(f"https://{HOST}:{PORT}/sub/{token}")
        return 0
    for target in sorted(p for p in ROOT.iterdir() if p.is_dir() and not p.name.startswith(".")):
        build(target, token_uid(target))
        print(f"https://{HOST}:{PORT}/sub/{target.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

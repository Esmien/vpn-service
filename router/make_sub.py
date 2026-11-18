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
UID_FILE = Path("/root/.secrets/ws-client-uuid")
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

META_ANNOUNCE = (
    "🇪🇪 Эстония — всё внутри: 🤖 Gemini, 🇷🇺 RU-сайты без выключения VPN, "
    "📺 YouTube, 💬 TG/WhatsApp/Instagram · 🛟 Отказ сценария — авто-переключение до минуты"
)

META_FULL = """\
🇪🇪 Esmien VPN — одна модель, все сценарии внутри

Сервер «Эстония» покрывает всё:
• 🤖 Gemini
• 🇷🇺 Российские сайты — БЕЗ выключения VPN
  (открытие может быть чуть медленнее: запрос идёт через несколько серверов, это нормально)
• 📺 YouTube — как обычно, через Эстонию
• 💬 Заблокированные сервисы — TG, WhatsApp, Instagram и прочее

👀 Не переживайте, что нет кучи моделей, как в других подписках.
Они есть — просто логика спрятана в сценариях, выбирать ничего не нужно.

🛟 Если какой-то сценарий отвалится, переключение займёт до минуты:
автоматика вернёт вас на Эстонию, и всё продолжит работать.\
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


# --- REALITY (XTLS-Vision) ------------------------------------------------
# Keys live outside git; if the file is missing (fresh checkout) the node is simply
# not emitted, so subscriptions keep working on the old WS entry.
REALITY_FILE = Path("/root/.secrets/reality.key")


def reality_params() -> dict | None:
    try:
        kv = dict(line.split("=", 1) for line in REALITY_FILE.read_text().split() if "=" in line)
        return {k: v.strip() for k, v in kv.items()}
    except FileNotFoundError:
        return None


REALITY_SEEN = reality_params()
REALITY_TAG = "🇪🇪 Эстония (все сервисы)"
WS_COMPAT_TAG = "🇪🇪 Эстония (совместимость)"
WS_COMPAT_DEFAULT = True


REALITY_PORT = 4443  # 443 режут у части операторов; nginx stream слушает оба
VISION_TAG = "🇪🇪 Эстония (Vision)"
VISION_HOST = "edge.esmien.ru"  # SNI совпадает с доменом, который реально указывает на наш IP


def vless_reality(uid: str, tag: str = REALITY_TAG, addr: str = IP) -> str | None:
    """REALITY link: address by IP on purpose — no DNS lookup for the ISP to see or poison."""
    p = REALITY_SEEN
    if not p:
        return None
    query = {"type": "tcp", "security": "reality", "sni": p["camo"], "fp": "chrome",
             "pbk": p["public"], "sid": p["shortId"], "flow": "xtls-rprx-vision",
             "spx": "/", "encryption": "none"}
    return f"vless://{uid}@{addr}:{REALITY_PORT}?" + urllib.parse.urlencode(query) + "#" + urllib.parse.quote(tag)


def vless_vision(uid: str, tag: str = VISION_TAG, addr: str = IP) -> str:
    """VLESS+Vision+TLS на нашем собственном домене.

    Камо-домен на нашем IP — это несовпадение SNI и адреса, которое ТСПУ ловит первым
    делом (соединение живёт минуту и замерзает). Здесь SNI `vpn.esmien.ru` действительно
    указывает на этот IP: придраться не к чему, а Vision скрывает TLS-in-TLS.
    """
    query = {"type": "tcp", "security": "tls", "sni": VISION_HOST, "fp": "chrome",
             "alpn": "h2,http/1.1", "flow": "xtls-rprx-vision", "encryption": "none"}
    return f"vless://{uid}@{addr}:{REALITY_PORT}?" + urllib.parse.urlencode(query) + "#" + urllib.parse.quote(tag)


def clash_proxy_vision(uid: str, name: str = VISION_TAG, addr: str = IP) -> str:
    return f"""  - name: {json.dumps(name, ensure_ascii=False)}
    type: vless
    server: {addr}
    port: {REALITY_PORT}
    uuid: {uid}
    network: tcp
    tls: true
    udp: true
    flow: xtls-rprx-vision
    servername: {VISION_HOST}
    client-fingerprint: chrome
    alpn: [h2, http/1.1]
"""


def sing_vision(uid: str, name: str = VISION_TAG, addr: str = IP) -> dict:
    return {"type": "vless", "tag": name, "server": addr, "server_port": REALITY_PORT, "uuid": uid,
            "flow": "xtls-rprx-vision",
            "tls": {"enabled": True, "server_name": VISION_HOST,
                    "alpn": ["h2", "http/1.1"],
                    "utls": {"enabled": True, "fingerprint": "chrome"}}}


def clash_proxy_reality(uid: str, name: str = REALITY_TAG, addr: str = IP) -> str | None:
    p = REALITY_SEEN
    if not p:
        return None
    return f"""  - name: {json.dumps(name, ensure_ascii=False)}
    type: vless
    server: {addr}
    port: {REALITY_PORT}
    uuid: {uid}
    network: tcp
    tls: true
    udp: true
    flow: xtls-rprx-vision
    servername: {p['camo']}
    client-fingerprint: chrome
    reality-opts:
      public-key: {p['public']}
      short-id: {p['shortId']}
"""


def sing_reality(uid: str, name: str = REALITY_TAG, addr: str = IP) -> dict | None:
    p = REALITY_SEEN
    if not p:
        return None
    return {"type": "vless", "tag": name, "server": addr, "server_port": REALITY_PORT, "uuid": uid,
            "flow": "xtls-rprx-vision",
            "tls": {"enabled": True, "server_name": p["camo"],
                    "utls": {"enabled": True, "fingerprint": "chrome"},
                    "reality": {"enabled": True, "public_key": p["public"],
                                "short_id": p["shortId"]}}}


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
  - DOMAIN-SUFFIX,esmien.ru,DIRECT
  - MATCH,{WHITE_GROUP}
""")

    outs = [sb_link(w["link"], w["name"]) for w in white] + [{"type": "direct", "tag": "direct"}]
    sing = {"log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 2080}],
            "outbounds": outs,
            "route": {"rules": [{"domain_suffix": [".esmien.ru"], "outbound": "direct"}, {"outbound": white[0]["name"] if white else "direct"}]}}
    (token_dir / "singbox.json").write_text(json.dumps(sing, indent=2, ensure_ascii=False))
    token_dir.chmod(0o755)


def _extra_nodes(token_dir: Path) -> list:
    """Доп. узлы (UDP-резерв и пр.) — только для токенов с маркером «compat»:
    лишний узел в списке = клиент выбирает его и решает, что VPN не работает."""
    pass  # UDP-резерв и WS выдаются всем: узлы разных эшелонов страхуют друг друга
    """Manual, per-token extra nodes (owner-only tests). Never part of the default build."""
    path = token_dir / "extra_nodes.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    return data if isinstance(data, list) else []


def build_meta(token_dir: Path, uid: str) -> None:
    """One visible node. Every scenario is decided by the server, not by the client.

    REALITY (443, XTLS-Vision) is the primary entry; the old WS node stays next to it
    as «совместимость» until every client is known to speak REALITY.
    """
    path = _BRAND["paths"]["general"]
    r_link = vless_reality(uid)
    # WS-узел выдаётся только по явному маркеру «compat» в каталоге токена: по умолчанию
    # подписка несёт один REALITY-узел, а старые WS-входы (2087/8443) остаются живыми,
    # чтобы уже скачанные профили не отваливались в момент обновления.
    # WS-узел в списке по умолчанию: если REALITY у конкретного клиента не поднимется,
    # у него есть рабочий вход без правки подписки. Выключатель — WS_COMPAT_DEFAULT.
    compat = bool(r_link) and (WS_COMPAT_DEFAULT or (token_dir / "compat").exists())
    ws_tag = (WS_COMPAT_TAG if compat else META_TAG)
    ws_link = vless(uid, path, ws_tag)
    extra = _extra_nodes(token_dir)
    v_link = None  # Vision снят: у владельца он не проходит
    # REALITY-вход с камо снят: чужой SNI на нашем IP — подпись подмены, из-за неё
    # оператор и морозил поток. Остались входы на своём домене.
    pairs = [(v_link, VISION_TAG)] if v_link else []
    if compat or not r_link:
        pairs.append((ws_link, WS_COMPAT_TAG if compat else META_TAG))
    pairs = [(l, n) for l, n in pairs if l]
    links = [l for l, n in pairs]
    names = [n for l, n in pairs]
    links = links + [e["link"] for e in extra if e.get("link")]
    names = names + [e["name"] for e in extra if e.get("name")]
    (token_dir / "list.txt").write_text(
        base64.b64encode(("\n".join(links) + "\n").encode()).decode() + "\n")
    names_yaml = "".join(f'      - {json.dumps(n, ensure_ascii=False)}\n' for n in names)
    (token_dir / "announce").write_text(META_ANNOUNCE + "\n", encoding="utf-8")

    # FlClash renders the YAML comments as the profile description and mangles non-ASCII
    # there (UTF-8 shown as mojibake), so the file carries one ASCII marker only. The
    # human-readable copy lives in the `announce` header and on /u/<token>.
    header = "# Esmien VPN"
    proxies = clash_proxy_vision(uid) if v_link else ""
    if compat or not r_link:
        proxies += clash_proxy(uid, WS_COMPAT_TAG if compat else META_TAG, path)
    proxies += "".join(e.get("clash", "") for e in extra)
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
{names_yaml}rules:
  - IP-CIDR,127.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - DOMAIN-SUFFIX,local,DIRECT
  - DOMAIN-SUFFIX,esmien.ru,DIRECT
  - MATCH,Esmien VPN
""", encoding="utf-8")

    out = {"type": "vless", "tag": META_TAG, "server": HOST, "server_port": PORT, "uuid": uid,
           "transport": {"type": "ws", "path": path, "headers": {"Host": HOST}},
           "tls": {"enabled": True, "server_name": HOST}}
    if r_link and not compat:
        outs = [sing_vision(uid)] if v_link else []
        match_tag = REALITY_TAG
    elif r_link:
        out["tag"] = WS_COMPAT_TAG
        outs = ([sing_vision(uid)] if v_link else []) + [out]
        match_tag = REALITY_TAG
    else:
        outs = [out]
        match_tag = META_TAG
    sing = {"log": {"level": "warn"},
            "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1",
                          "listen_port": 2080}],
            "outbounds": [o for o in outs if o] + [e["sing"] for e in extra if e.get("sing")]
                         + [{"type": "direct", "tag": "direct"}],
            "route": {"rules": [{"domain_suffix": [".esmien.ru"], "outbound": "direct"}, {"outbound": match_tag}]}}
    (token_dir / "singbox.json").write_text(json.dumps(sing, indent=2, ensure_ascii=False),
                                            encoding="utf-8")
    token_dir.chmod(0o755)


def build(token_dir: Path, uid: str) -> None:
    kind_file = token_dir / "kind"
    kind = kind_file.read_text().strip() if kind_file.exists() else ""
    if kind == "whitelist":
        return build_whitelist(token_dir, uid)
    if kind != "full":  # один узел — норма, полный список — только явным kind=full
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
  - DOMAIN-SUFFIX,esmien.ru,DIRECT
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
                {"domain_suffix": [".esmien.ru"], "outbound": "direct"},
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

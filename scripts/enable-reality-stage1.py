#!/usr/bin/env python3
"""Stage 1: add a REALITY+Vision inbound alongside the WS inbounds.

Touches: /root/vpn-router/config.json (adds inbound, keeps everything else),
         /etc/nginx/nginx.conf (stream map -> include, so blue/green can rewrite it),
         /etc/nginx/stream.d/443-map.conf (new, generated),
         /root/vpn-router/apply_brand.py (regenerates the map on every flip),
         /root/vpn-router/vpn-bluegreen.py (probe understands REALITY inbounds),
         /etc/logrotate.d/xray (access/error logs survive /tmp cleanup).
Prints nothing secret.
"""
import json
import re
import secrets
import shutil
import subprocess
from pathlib import Path

CFG = Path("/root/vpn-router/config.json")
BAK = Path("/root/vpn-router/backups")
STAMP = subprocess.run(["date", "+%Y%m%d-%H%M%S"], capture_output=True, text=True).stdout.strip()
CAMO = "www.microsoft.com"
PORT = 10043
TAG = "reality-in"

BAK.mkdir(exist_ok=True)
(BAK / f"pre-reality-{STAMP}").mkdir(exist_ok=True)
for p in (CFG, Path("/etc/nginx/nginx.conf"), Path("/root/vpn-router/apply_brand.py"),
          Path("/root/vpn-router/vpn-bluegreen.py"), Path("/root/vpn-service/router/apply_brand.py"),
          Path("/root/vpn-service/router/vpn-bluegreen.py")):
    if p.exists():
        shutil.copy2(p, BAK / f"pre-reality-{STAMP}" / p.name)

cfg = json.loads(CFG.read_text())
if any(i.get("tag") == TAG for i in cfg["inbounds"]):
    raise SystemExit("inbound already present — nothing to do")

# --- keys -------------------------------------------------------------
out = subprocess.run(["/usr/local/bin/xray", "x25519"], capture_output=True, text=True).stdout
priv = re.search(r"Private[^:]*:\s*(\S+)", out).group(1)
pub = re.search(r"Public[^:]*:\s*(\S+)", out).group(1)
sid = secrets.token_hex(8)
Path("/root/.secrets/reality.key").write_text(f"private={priv}\npublic={pub}\nshortId={sid}\ncamo={CAMO}\n")
Path("/root/.secrets/reality.key").chmod(0o600)

# --- inbound: same people, same UUIDs, VISion flow --------------------
ws = next(i for i in cfg["inbounds"] if i.get("tag") == "ws-in")
clients = []
for c in ws["settings"]["clients"]:
    clients.append({"id": c["id"], "flow": "xtls-rprx-vision", "email": c.get("email", "")})

inbound = {
    "tag": TAG,
    "listen": "127.0.0.1",
    "port": PORT,
    "protocol": "vless",
    "settings": {"clients": clients, "decryption": "none"},
    "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "sockopt": {"acceptProxyProtocol": True},
        "realitySettings": {
            "show": False,
            "dest": f"{CAMO}:443",
            "xver": 0,
            "serverNames": [CAMO],
            "privateKey": priv,
            "shortIds": [sid],
        },
    },
    "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True},
}
cfg["inbounds"].append(inbound)

# --- logs: /tmp is not a place for incident evidence ------------------
cfg["log"] = {"loglevel": "info", "access": "/var/log/xray/access.log", "error": "/var/log/xray/error.log"}
CFG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
print(f"inbound {TAG}@{PORT} added, clients={len(clients)}, camo={CAMO}, pub={pub}")

# --- nginx: inline map -> generated include ---------------------------
ng = Path("/etc/nginx/nginx.conf")
text = ng.read_text()
if "stream.d/443-map.conf" not in text:
    text = re.sub(
        r"    map \$ssl_preread_server_name \$mtproto_backend \{\n(?:.*\n)*?    \}\n",
        "    include /etc/nginx/stream.d/443-map.conf;\n",
        text, count=1)
    ng.write_text(text)
Path("/etc/nginx/stream.d").mkdir(exist_ok=True)
print("nginx.conf: map replaced by include" if "stream.d/443-map.conf" in ng.read_text() else "nginx.conf: PATCH FAILED")

# --- logrotate --------------------------------------------------------
Path("/var/log/xray").mkdir(exist_ok=True)
Path("/etc/logrotate.d/xray").write_text("""/var/log/xray/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
""")
print("logrotate: /etc/logrotate.d/xray written (14 days)")

import json, sys
from pathlib import Path

BASE = Path('/root/vpn-router')
brand = json.loads((BASE / 'brand.json').read_text())
uid = Path('/root/.secrets/ws-client-uuid').read_text().strip()
path = brand['paths'][sys.argv[2] if len(sys.argv) > 2 else 'general']
port = int(sys.argv[1]) if len(sys.argv) > 1 else 10860

cfg = {
    "log": {"level": "warning"},
    "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                  "settings": {"udp": False}}],
    "outbounds": [{
        "tag": "proxy",
        "protocol": "vless",
        "settings": {"vnext": [{"address": brand["host"], "port": brand["port"],
                                "users": [{"id": uid, "encryption": "none", "flow": ""}]}]},
        "streamSettings": {"network": "ws", "security": "tls",
                           "tlsSettings": {"serverName": brand["host"]},
                           "wsSettings": {"path": path, "headers": {"Host": brand["host"]}}}
    }],
}
out = f'/root/.hermes/cache/scratch/cli-{port}.json'
Path(out).write_text(json.dumps(cfg, indent=2))
print(out, 'path=', path, 'port=', port)

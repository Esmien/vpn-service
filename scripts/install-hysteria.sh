#!/usr/bin/env bash
# Install the Hysteria2 backup entry (UDP/8443) on a fresh server.
#
# Idempotent: re-running keeps the existing secrets in /etc/hysteria/config.yaml
# and only refreshes the binary and the systemd unit.
#
# Why it exists: TSPU «заморозка» counts packets per TCP connection (~25 packets,
# ~16 KB) and freezes it; WireGuard/Hysteria2 ride on UDP and pass where TCP is
# frozen. The TCP VLESS+WS entry stays primary, this one is the fallback.
set -euo pipefail

HY_VERSION="${HY_VERSION:-app/v2.14.0}"
DOMAIN="${DOMAIN:-vpn.esmien.ru}"
CF_INI="${CF_INI:-/root/.secrets/cf-esmien-com.ini}"
CONF=/etc/hysteria/config.yaml
SECRETS=/root/.secrets/hysteria-owner.txt
UNIT=/etc/systemd/system/hysteria-server.service
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ARCH=amd64
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "== binary: hysteria $HY_VERSION"
curl -fsSL -o "$TMP/hysteria" \
  "https://github.com/apernet/hysteria/releases/download/${HY_VERSION//\//%2F}/hysteria-linux-$ARCH"
install -m 755 "$TMP/hysteria" /usr/local/bin/hysteria

if [[ -f "$CONF" && -f "$SECRETS" ]]; then
  echo "== config exists, keeping secrets: $CONF"
else
  echo "== generating secrets"
  mkdir -p /etc/hysteria "$(dirname "$SECRETS")"
  PW=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')
  OBFS=$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')
  sed -e "s|__AUTH_PASSWORD__|$PW|" -e "s|__OBFS_PASSWORD__|$OBFS|" \
      "$SRC_DIR/hysteria/config.example.yaml" > "$CONF"
  CF_INI="$CF_INI" CONF="$CONF" /usr/bin/python3 -c 'import os,pathlib; p=pathlib.Path(os.environ["CONF"]); token=pathlib.Path(os.environ["CF_INI"]).read_text().split("=",1)[1].strip(); s=p.read_text(); assert "__CLOUDFLARE_API_TOKEN__" in s; p.write_text(s.replace("__CLOUDFLARE_API_TOKEN__", token))'
  chmod 600 "$CONF"
  umask 077
  printf 'password=%s\nobfs=%s\nport=8443/udp\n' "$PW" "$OBFS" > "$SECRETS"
  chmod 600 "$SECRETS"
fi

# Hysteria ACME selects its own separate certificate by client SNI.
sed -i "s|^    url: .*|    url: http://127.0.0.1|" "$CONF"

echo "== systemd unit"
install -m 644 "$SRC_DIR/systemd/hysteria-server.service" "$UNIT"
install -m 755 "$SRC_DIR/scripts/vpn-socks-relay.py" /usr/local/bin/vpn-socks-relay.py
install -m 644 "$SRC_DIR/systemd/vpn-socks-relay.service" /etc/systemd/system/vpn-socks-relay.service
systemctl daemon-reload
systemctl enable --now vpn-socks-relay
systemctl enable --now hysteria-server
sleep 2
systemctl is-active hysteria-server vpn-socks-relay

echo "== xray socks inbound (routing entry for the UDP path)"
python3 - <<'PY'
import json, pathlib
p = pathlib.Path('/root/vpn-router/config.json')
if not p.exists():
    raise SystemExit('no /root/vpn-router/config.json — apply the xray config first')
c = json.loads(p.read_text())
if not any(i.get('tag') == 'udp-in' for i in c.get('inbounds', [])):
    c['inbounds'].append({"tag": "udp-in", "protocol": "socks", "listen": "127.0.0.1",
                          "port": 10089, "settings": {"auth": "noauth", "udp": True}})
    p.write_text(json.dumps(c, ensure_ascii=False, indent=2))
    print('  udp-in 127.0.0.1:10089 added')
else:
    print('  udp-in already present')
PY
vpn-cfg apply | tail -3

cat <<EOF

Done. Verify from a client (Hiddify / NekoBox / sing-box; v2rayNG cannot do hy2):
  hysteria2://<password>@$DOMAIN:8443/?sni=$DOMAIN&obfs=salamander&obfs-password=<obfs>#...
  password/obfs: $SECRETS
The node is added per-token through /var/www/esmien-sub/<token>/extra_nodes.json,
then: cd /root/vpn-router && python3 make_sub.py
EOF

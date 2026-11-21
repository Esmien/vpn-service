#!/usr/bin/env bash
# Verify published VPN routes without printing any subscription tokens.
set -euo pipefail
ROOT=/root/vpn-router
read -r HOST EDGE PUB < <(python3 -c 'import json; from pathlib import Path; r=Path("/root/vpn-router"); b=json.loads((r/"brand.json").read_text()); a=json.loads((r/"addresses.json").read_text()); print(b["host"], a["edge"]["ip"], b["paths"]["public_general"])')
check() {
  local addr=$1 host=$2 port=$3 path=$4 expected=$5 code
  code=$(curl --noproxy '*' --max-time 5 --silent --output /dev/null --write-out '%{http_code}' --resolve "$host:$port:$addr" "https://$host:$port$path" || true)
  printf 'TCP/%s %s: %s (expected %s)\n' "$port" "$path" "$code" "$expected"
  [[ $code == "$expected" ]]
}
check "$EDGE" "$HOST" 443 "$PUB" 400
check "$EDGE" "$HOST" 8443 /general 400
check "$EDGE" "$HOST" 443 /ws 404
check "$EDGE" nosuch.invalid 443 / 000

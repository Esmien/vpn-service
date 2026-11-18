#!/usr/bin/env bash
# Проверка всех входов через :443 после любой правки фронта.
# Печатает только коды: 200/301/404/400/000 — это и есть «живо/мертво».
set -uo pipefail
HOST=vpn.esmien.ru
fails=0
chk() {  # name, expected-regex, url-template...
  local name=$1 expect=$2; shift 2
  local code
  code=$(curl -sk -o /dev/null -m 8 -w '%{http_code}' "$@" 2>/dev/null)
  if [[ "$code" =~ $expect ]]; then printf '  ok   %-28s %s\n' "$name" "$code"
  else printf '  FAIL %-28s %s\n' "$name" "$code"; fails=$((fails+1)); fi
}

echo "== сайты (через :443, default-ветка -> nginx :9443)"
for h in ai.esmien.ru llm.esmien.ru app.esmien.ru mon.esmien.ru search.esmien.ru stats.esmien.ru; do
  chk "$h" '^(200|301|302|401|403|404)$' --resolve "$h:443:127.0.0.1" "https://$h/"
done
chk "bot.esmien.ru (webhook)" '^(200|401|403|404|405)$' --resolve "bot.esmien.ru:443:127.0.0.1" "https://bot.esmien.ru/"

echo "== VPN WS-входы"
chk "vpn :443 /general" '^(200|400)$' --resolve "$HOST:443:127.0.0.1" "https://$HOST/general"
chk "vpn :8443 /ws"     '^(200|400)$' --resolve "$HOST:8443:127.0.0.1" "https://$HOST:8443/ws"
chk "vpn :2087 /ws"     '^(200|400)$' --resolve "$HOST:2087:127.0.0.1" "https://$HOST:2087/ws"
chk "подписка :8443"    '^200$'       --resolve "$HOST:8443:127.0.0.1" "https://$HOST:8443/sub/$(ls /var/www/esmien-sub | head -1)"

echo "== mtg (SNI www.cloudflare.com)"
if echo | timeout 6 openssl s_client -connect 127.0.0.1:443 -servername www.cloudflare.com 2>/dev/null \
   | openssl x509 -noout -subject 2>/dev/null | grep -q 'cloudflare'; then
  echo "  ok   mtg fake-TLS"
else echo "  FAIL mtg fake-TLS"; fails=$((fails+1)); fi

echo "== REALITY (SNI camo -> reality-in)"
if [[ -f /root/.secrets/reality.key ]]; then
  camo=$(grep '^camo=' /root/.secrets/reality.key | cut -d= -f2-)
  if timeout 6 bash -c "echo > /dev/tcp/127.0.0.1/443" 2>/dev/null; then
    echo "  ok   :443 принимает соединения (camo $camo)"
  else echo "  FAIL :443 не слушает"; fails=$((fails+1)); fi
fi

[[ $fails -eq 0 ]] && echo "итог: всё живо" || echo "итог: провалов $fails"
exit $(( fails > 0 ))

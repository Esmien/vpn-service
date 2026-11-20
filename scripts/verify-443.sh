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
  chk "$h" '^(200|301|302|401|403|404)$' --resolve "$h:443:127.0.0.1" "https://$h/"
done
chk "bot.esmien.ru (webhook)" '^(200|401|403|404|405)$' --resolve "bot.esmien.ru:443:127.0.0.1" "https://bot.esmien.ru/"

echo "== VPN WS-входы (опубликовано: /general, публичный alias на 443, снят /ws)"
chk "vpn :8443 /general" '^(200|400)$' --resolve "$HOST:8443:127.0.0.1" "https://$HOST:8443/general"
chk "vpn :2087 /general" '^(200|400)$' --resolve "$HOST:2087:127.0.0.1" "https://$HOST:2087/general"
chk "подписка :8443"    '^200$'       --resolve "$HOST:8443:127.0.0.1" "https://$HOST:8443/sub/$(ls /var/www/esmien-sub | head -1)"
PUB=$(python3 -c "import json;print(json.load(open('/root/vpn-router/brand.json'))['paths']['public_general'])" 2>/dev/null)
[[ -n "$PUB" ]] && chk "vpn :443 публичный alias" '^(200|400)$' --resolve "$HOST:443:127.0.0.1" "https://$HOST:443$PUB"

echo "== снятые пути не должны апгрейдиться"
for hp in "8443 /ws" "8443 /fi" "8443 /p15" "443 /ws"; do
  port=${hp%% *}; path=${hp##* }
  code=$(curl -sk -o /dev/null -m 8 -w '%{http_code}' --resolve vpn.esmien.ru:${port}:127.0.0.1 "https://vpn.esmien.ru:${port}${path}" 2>/dev/null)
  if [[ "$code" == "404" || "$code" == "000" ]]; then echo "  ok   ${port}${path} -> $code (снят)"
  else echo "  FAIL ${port}${path} -> $code (ещё отвечает)"; fails=$((fails+1)); fi
done

echo "== палец по IP (unknown SNI должен молчать)"
code=$(curl -sk -o /dev/null -m 8 -w '%{http_code}' --resolve nosuch.invalid:443:127.0.0.1 https://nosuch.invalid/ 2>/dev/null)
if [[ "$code" == "000" ]]; then echo "  ok   unknown SNI -> 000 (рукопожатие сброшено)"
else echo "  FAIL unknown SNI -> $code (наш vhost отвечает)"; fails=$((fails+1)); fi
hdr=$(curl -skI -m 8 --resolve "ai.esmien.ru:443:127.0.0.1" "https://ai.esmien.ru/" 2>/dev/null | grep -i '^server:' | tr -d '\r')
if [[ "$hdr" == "server: nginx" ]]; then echo "  ok   версия nginx скрыта"
else echo "  FAIL $hdr"; fails=$((fails+1)); fi

[[ $fails -eq 0 ]] && echo "итог: всё живо" || echo "итог: провалов $fails"
exit $(( fails > 0 ))

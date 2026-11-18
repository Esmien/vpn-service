#!/usr/bin/env bash
# Полный откат :443 c REALITY на прежнюю схему (nginx stream + WS-инбаунды).
#
#   rollback-443-reality.sh            — откат на последний снимок pre-reality-*
#   rollback-443-reality.sh --list     — какие снимки есть
#   rollback-443-reality.sh --dry-run  — показать шаги, ничего не менять
#
# Что восстанавливается:
#   1. /root/vpn-router/config.json            (без reality-in)
#   2. /root/vpn-router/apply_brand.py         (без генерации stream-карты)
#   3. /root/vpn-router/vpn-bluegreen.py       (проба без reality-ветки)
#   4. /etc/nginx/nginx.conf                   (карта mtproto обратно inline)
#   5. /var/www/esmien-sub/**                  (подписки без REALITY-узла)
#   6. blue/green apply + nginx reload, затем проверка входов.
set -euo pipefail

BAKDIR_ROOT=/root/vpn-router/backups
SUBBACK_ROOT=/root/backups
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
if [[ "${1:-}" == "--list" ]]; then
  ls -1dt "$BAKDIR_ROOT"/pre-reality-* 2>/dev/null || echo "снимков нет"
  exit 0
fi

SNAP=$(ls -1dt "$BAKDIR_ROOT"/pre-reality-* 2>/dev/null | head -1 || true)
[[ -n "$SNAP" ]] || { echo "нет снимка pre-reality-* — откатывать не на что"; exit 1; }
SUBBAK=$(ls -1dt "$SUBBACK_ROOT"/esmien-sub-pre-reality-* 2>/dev/null | head -1 || true)
echo "снимок: $SNAP"
[[ -n "$SUBBAK" ]] && echo "подписки: $SUBBAK"

run() { echo "  \$ $*"; [[ $DRY -eq 1 ]] || "$@"; }

echo "1/6 конфиг роутера"
run cp -a "$SNAP/config.json" /root/vpn-router/config.json
[[ -f "$SNAP/apply_brand.py" ]] && run cp -a "$SNAP/apply_brand.py" /root/vpn-router/apply_brand.py
[[ -f "$SNAP/vpn-bluegreen.py" ]] && run cp -a "$SNAP/vpn-bluegreen.py" /root/vpn-router/vpn-bluegreen.py

echo "2/6 nginx.conf"
run cp -a "$SNAP/nginx.conf" /etc/nginx/nginx.conf

echo "3/6 подписки"
if [[ -n "$SUBBAK" ]]; then
  run rsync -a --delete "$SUBBAK/" /var/www/esmien-sub/
else
  echo "  снимка подписок нет — пропускаю (в подписке останется узел REALITY)"
fi

echo "4/6 blue/green apply"
run flock -w 300 /var/lock/vpn-config.lock python3 /root/vpn-router/vpn-bluegreen.py apply

echo "5/6 nginx -t + reload"
if [[ $DRY -eq 0 ]]; then
  nginx -t
  systemctl reload nginx
fi

echo "6/6 проверка"
if [[ $DRY -eq 0 ]]; then
  bash /root/vpn-service/scripts/verify-443.sh || true
fi
echo "готово: :443 снова обслуживает nginx stream, REALITY выключен"

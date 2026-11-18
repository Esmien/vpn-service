#!/usr/bin/env bash
# Откат «закрытия пальца по IP» (18.11.2026):
#   1. /etc/nginx/nginx.conf          — вернуть как было (server_tokens)
#   2. 99-default-ssl-reject.conf     — удалить (unknown SNI снова отдаёт наш vhost)
#   3. make_sub.py                    — вернуть версию, которая всегда кладёт WS-узел
#   4. /var/www/esmien-sub/**         — вернуть снимок подписок (с WS-узлом у всех)
#
#   rollback-443-hardening.sh [--list] [--dry-run]
set -euo pipefail
SNAP_ROOT=/root/backups
DRY=0
[[ "${1:-}" == "--dry-run" ]] && DRY=1
if [[ "${1:-}" == "--list" ]]; then ls -1dt "$SNAP_ROOT"/hardening-* 2>/dev/null; exit 0; fi
SNAP=$(ls -1dt "$SNAP_ROOT"/hardening-* 2>/dev/null | head -1 || true)
[[ -n "$SNAP" ]] || { echo "нет снимка hardening-*"; exit 1; }
echo "снимок: $SNAP"
run() { echo "  \$ $*"; [[ $DRY -eq 1 ]] || "$@"; }

run cp -a "$SNAP/nginx.conf" /etc/nginx/nginx.conf
run rm -f /etc/nginx/conf.d/99-default-ssl-reject.conf
[[ -f "$SNAP/make_sub.py" ]] && run cp -a "$SNAP/make_sub.py" /root/vpn-router/make_sub.py
[[ -d "$SNAP/esmien-sub" ]] && run rsync -a --delete "$SNAP/esmien-sub/" /var/www/esmien-sub/

if [[ $DRY -eq 0 ]]; then
  nginx -t && systemctl reload nginx
  bash /root/vpn-service/scripts/verify-443.sh || true
fi
echo "готово: unknown SNI снова отдаёт vhost, WS-узел вернулся в подписки"

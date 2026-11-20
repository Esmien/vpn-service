#!/usr/bin/env bash
# Пересобрать заглушку «панель LiteLLM» на публичных местах VPN-входа.
#
# Что делает:
#   1. копирует статику панели из живого контейнера litellm в /var/www/llm-decoy/ui
#      (файлы те же, что отдаёт настоящая сборка, поэтому визуально неотличимо);
#   2. ставит /etc/nginx/conf.d/llm-decoy.conf (loopback 127.0.0.1:8092);
#   3. проверяет конфиг и перезагружает nginx.
#
# Рабочий LiteLLM (127.0.0.1:4000) при этом не участвует: заглушка отвечает на любой
# вход тем же 401, что отдаёт настоящая панель на неверный пароль.
#
# Запускать после пересоздания контейнера litellm (новая версия = новая сборка
# статики) и после переустановки nginx-конфигов.
set -euo pipefail

CONTAINER="${CONTAINER:-litellm}"
DEST="${DEST:-/var/www/llm-decoy}"
CONF_SRC="$(dirname "$0")/../nginx/llm-decoy.conf"

case "$DEST" in
    /var/www/llm-decoy|/var/www/llm-decoy/) ;;
    *) echo "refusing DEST=$DEST (expected /var/www/llm-decoy)" >&2; exit 2 ;;
esac

src=$(docker exec "$CONTAINER" python -c '
import os
base = "/app"
for root, dirs, files in os.walk(base):
    if root.endswith("litellm/proxy/_experimental/out") and "index.html" in files:
        print(root); break
')
[ -n "$src" ] || { echo "static build not found inside $CONTAINER" >&2; exit 1; }
echo "static: $src"

install -d "$DEST"
rm -rf "$DEST/ui.new"
docker cp "$CONTAINER:$src" "$DEST/ui.new"
rm -rf "$DEST/ui.old"
[ -d "$DEST/ui" ] && mv "$DEST/ui" "$DEST/ui.old"
mv "$DEST/ui.new" "$DEST/ui"
rm -rf "$DEST/ui.old"
ln -sfn "$DEST/ui" "$DEST/litellm-asset-prefix"

install -m 644 "$CONF_SRC" /etc/nginx/conf.d/llm-decoy.conf
nginx -t
systemctl reload nginx

echo "decoy: $(du -sh "$DEST/ui" | cut -f1), $(find "$DEST/ui" -type f | wc -l) files"
curl -s -o /dev/null -w 'GET  /ui/   -> %{http_code}\n' http://127.0.0.1:8092/ui/
curl -s -o /dev/null -w 'POST /login -> %{http_code}\n' -X POST http://127.0.0.1:8092/login

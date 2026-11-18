#!/bin/sh
# vision-in terminates TLS in Xray: the cert lives in each colour's data volume, not on /etc/letsencrypt.
set -e
for c in vpn-router vpn-router-b; do
    VOL=$(docker inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/usr/local/etc/xray"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)
    [ -n "$VOL" ] || continue
    cp -f /etc/letsencrypt/live/esmien-wildcard/fullchain.pem "$VOL/vision.crt"
    cp -f /etc/letsencrypt/live/esmien-wildcard/privkey.pem "$VOL/vision.key"
    chmod 600 "$VOL/vision.key"
done
docker restart vpn-router >/dev/null 2>&1 || true

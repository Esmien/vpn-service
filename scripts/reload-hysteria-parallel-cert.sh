#!/bin/sh
set -eu
if [ "${RENEWED_LINEAGE:-}" = /etc/letsencrypt/live/vpn-parallel-domains ]; then
    systemctl try-restart hysteria-server
fi

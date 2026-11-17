#!/usr/bin/env python3
"""Stable SOCKS entry for the UDP (Hysteria2) path.

Hysteria2 cannot speak VLESS and knows nothing about the server-side routing,
so it forwards every stream here and Xray decides where it goes — exactly like
the VLESS `/general` entry (Google -> FR/FI, .ru -> Moscow, everything else ->
Estonia). Xray's SOCKS inbound runs inside the blue/green container pair, so its
port flips between 10089 (colour a) and 11089 (colour b); this relay reads
`/root/vpn-router/vpn-color` per connection, so a blue/green flip needs no change
on the Hysteria side.

SOCKS5 UDP ASSOCIATE is carried inside the TCP stream, so a plain TCP relay is
enough — no UDP socket here.
"""
import socket
import threading
from pathlib import Path

LISTEN = ("127.0.0.1", 10809)
COLOR_FILE = Path("/root/vpn-router/vpn-color")
PORTS = {"a": 10089, "b": 11089}


def target_port() -> int:
    try:
        return PORTS.get(COLOR_FILE.read_text().strip(), PORTS["a"])
    except OSError:
        return PORTS["a"]


def pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(client: socket.socket) -> None:
    try:
        upstream = socket.create_connection(("127.0.0.1", target_port()), timeout=5)
    except OSError:
        client.close()
        return
    upstream.settimeout(None)
    threading.Thread(target=pump, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pump, args=(upstream, client), daemon=True).start()


def main() -> None:
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(128)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()

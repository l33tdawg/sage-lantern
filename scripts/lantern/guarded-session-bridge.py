#!/usr/bin/env python3
"""User-unit readiness bridge to root launch authority. No launch parameters."""
import json
import os
import socket
import struct
import time

CONTROL = '/run/sage-lantern-desktop/start.sock'


def notify_ready():
    address = os.environ.get('NOTIFY_SOCKET', '')
    # Only this user unit's inherited manager address; never sent to root.
    if not address or address[0] not in ('/', '@'):
        raise ValueError('held')
    if address[0] == '@':
        address = '\0' + address[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as notify:
        notify.sendto(b'READY=1', address)


def main():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(45)
            connection.connect(CONTROL)
            _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET,
                socket.SO_PEERCRED, struct.calcsize('3i')))
            if uid != 0:
                raise ValueError('held')
            connection.sendall(b'{"action":"start"}\n')
            with connection.makefile('rb') as stream:
                raw = stream.readline(128)
                if raw != b'{"ready":true}\n':
                    raise ValueError('held')
                notify_ready()
                connection.settimeout(None)
                # Root EOF means held, never user-unit failure/session shutdown.
                while stream.readline(128) == b'{"held":true}\n':
                    pass
    except Exception:
        pass
    while True:
        time.sleep(30)  # Preserve apps after authority loss; owner recovery required.


if __name__ == '__main__':
    main()

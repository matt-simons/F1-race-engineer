"""UDP listener: receives packets from the game and hands them on."""

from __future__ import annotations

import socket
import time
from typing import Callable

DEFAULT_PORT = 20777


def listen(handler: Callable[[bytes, float], None], host: str = "0.0.0.0",
           port: int = DEFAULT_PORT, idle_callback: Callable[[], None] | None = None,
           idle_interval: float = 0.25) -> None:
    """Block forever, calling handler(datagram, recv_time) for every packet.
    idle_callback runs at least every idle_interval seconds (used to refresh
    the console even when the game is paused). Ctrl+C to stop."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # A big receive buffer avoids dropped packets at 60 Hz with all types on.
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
    except OSError:
        pass
    sock.bind((host, port))
    sock.settimeout(idle_interval)
    last_idle = 0.0
    try:
        while True:
            try:
                data, _ = sock.recvfrom(2048)
                handler(data, time.time())
            except socket.timeout:
                pass
            if idle_callback and time.monotonic() - last_idle >= idle_interval:
                idle_callback()
                last_idle = time.monotonic()
    finally:
        sock.close()

"""Record raw telemetry to disk and play it back.

A recording is a gzip stream of records:  <double recv_time><uint16 length><bytes>
Raw bytes are kept (not decoded data) so recordings stay useful as the
decoders and analysis improve. Roughly 2-4 MB per minute once compressed.
"""

from __future__ import annotations

import gzip
import socket
import struct
import time
from pathlib import Path
from typing import Iterator

RECORD_HEAD = struct.Struct("<dH")
MAGIC = b"F1RE23\x00\x01"


class Recorder:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = gzip.open(self.path, "wb", compresslevel=5)
        self._f.write(MAGIC)
        self.count = 0
        self._last_flush = time.monotonic()

    def write(self, data: bytes, recv_time: float | None = None) -> None:
        self._f.write(RECORD_HEAD.pack(time.time() if recv_time is None else recv_time, len(data)))
        self._f.write(data)
        self.count += 1
        # Flush every couple of seconds so a crash loses at most that much.
        if time.monotonic() - self._last_flush > 2.0:
            self._f.flush()
            self._last_flush = time.monotonic()

    def close(self) -> None:
        self._f.close()

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_recording(path: str | Path) -> Iterator[tuple[float, bytes]]:
    """Yield (recv_time, datagram) from a recording. Stops cleanly at a
    truncated end (e.g. the app was killed mid-write)."""
    with gzip.open(path, "rb") as f:
        try:
            if f.read(len(MAGIC)) != MAGIC:
                raise ValueError(f"{path} is not an F1 race engineer recording")
            while True:
                head = f.read(RECORD_HEAD.size)
                if len(head) < RECORD_HEAD.size:
                    return
                t, n = RECORD_HEAD.unpack(head)
                data = f.read(n)
                if len(data) < n:
                    return
                yield t, data
        except EOFError:
            return  # file wasn't closed properly; keep what was flushed


def replay(path: str | Path, speed: float = 1.0) -> Iterator[bytes]:
    """Yield datagrams with their original timing (speed=2 is twice as fast,
    speed=0 is as fast as possible)."""
    start_wall = None
    start_rec = None
    for t, data in read_recording(path):
        if speed > 0:
            if start_wall is None:
                start_wall, start_rec = time.monotonic(), t
            target = start_wall + (t - start_rec) / speed
            delay = target - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        yield data


def replay_to_udp(path: str | Path, host: str = "127.0.0.1", port: int = 20777,
                  speed: float = 1.0) -> int:
    """Send a recording to a UDP port as if the game were running."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    n = 0
    try:
        for data in replay(path, speed):
            sock.sendto(data, (host, port))
            n += 1
    finally:
        sock.close()
    return n

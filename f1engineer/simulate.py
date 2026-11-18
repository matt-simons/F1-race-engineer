"""Build F1 23 packets from scratch, for tests and for trying the app
without the game running.

    python -m f1engineer.simulate            send a fake 3-lap session to UDP 20777
    python -m f1engineer.simulate --file x   write it as a recording instead
"""

from __future__ import annotations

import argparse
import math
import socket
import struct
import time

from . import packets as P
from .recording import Recorder

UID = 0x1234_5678_9ABC_DEF0


def header(pid: int, session_time: float, frame: int, player: int = 0, uid: int = UID) -> bytes:
    return P.HEADER.pack(2023, 23, 1, 0, 1, pid, uid, session_time, frame, frame, player, 255)


def car_telemetry(h: bytes, speed=200, throttle=1.0, steer=0.0, brake=0.0, gear=7, rpm=11000,
                  brakes=(500, 500, 520, 520), surf=(90, 90, 88, 88), inner=(100, 100, 98, 98),
                  pressure=(21.5, 21.5, 23.0, 23.0)) -> bytes:
    car = P.CAR_TELEMETRY.pack(speed, throttle, steer, brake, 0, gear, rpm, 0, 50, 0,
                               *brakes, *surf, *inner, 105, *pressure, 0, 0, 0, 0)
    return h + car * P.NUM_CARS + struct.pack("<BBb", 255, 255, 0)


def lap_data(h: bytes, lap_num=1, lap_distance=0.0, current_ms=0, last_ms=0, position=1,
             sector=0, invalid=0, s1=0, s2=0) -> bytes:
    car = P.LAP_DATA.pack(last_ms, current_ms, s1 % 60000, s1 // 60000, s2 % 60000, s2 // 60000,
                          0, 0, lap_distance, lap_distance, 0.0,
                          position, lap_num, 0, 0, sector, invalid, 0, 0, 0, 0, 0, 1, 1, 2, 0,
                          0, 0, 0)
    return h + car * P.NUM_CARS + struct.pack("<BB", 255, 255)


def session(h: bytes, track_id=7, session_type=1, track_length=5891, weather=0) -> bytes:
    head = P.SESSION_HEAD.pack(weather, 30, 22, 0, track_length, session_type, track_id, 0,
                               3600, 3600, 80, 0, 0, 255, 0, 0)
    zones = P.MARSHAL_ZONE.pack(0.0, 0) * 21
    mid = struct.pack("<BBB", 0, 0, 0)
    fc = P.FORECAST.pack(0, 0, 0, 0, 0, 0, 0, 0) * 56
    tail = P.SESSION_TAIL.pack(0, 90, 0, 0, 0, 0, 0, 0, *([0] * 9), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    return h + head + zones + mid + fc + tail


def car_status(h: bytes, tc=1, abs_on=0, fuel=50.0, tyre_age=0, visual=16, ers=3.0e6) -> bytes:
    car = P.CAR_STATUS.pack(tc, abs_on, 1, 56, 0, fuel, 110.0, 2.5, 13000, 4000, 8, 0, 0,
                            visual, visual, tyre_age, 1, 0.0, 0.0, ers, 1, 0.0, 0.0, 0.0, 0)
    return h + car * P.NUM_CARS


def car_damage(h: bytes, wear=(2.0, 2.0, 1.5, 1.5)) -> bytes:
    car = P.CAR_DAMAGE.pack(*wear, *([0] * 8), *([0] * 18))
    return h + car * P.NUM_CARS


def motion(h: bytes, pos=(0.0, 0.0, 0.0), g_lat=0.0, g_long=0.0) -> bytes:
    car = P.CAR_MOTION.pack(*pos, 0.0, 0.0, 0.0, 0, 0, 32767, 32767, 0, 0,
                            g_lat, g_long, 1.0, 0.0, 0.0, 0.0)
    return h + car * P.NUM_CARS


def motion_ex(h: bytes, wheel_speed=(55.0,) * 4, slip=(0.0,) * 4) -> bytes:
    vals = [0.0] * 47
    vals[12:16] = wheel_speed
    vals[16:20] = slip
    return h + P.MOTION_EX.pack(*vals)


def event(h: bytes, code: str, payload: bytes = b"") -> bytes:
    return h + code.encode("ascii") + payload.ljust(12, b"\x00")


def button(h: bytes, mask: int) -> bytes:
    return event(h, "BUTN", struct.pack("<I", mask))


def participants(h: bytes, names=("PLAYER",)) -> bytes:
    cars = b""
    for i in range(P.NUM_CARS):
        name = (names[i] if i < len(names) else f"AI {i}").encode()
        cars += P.PARTICIPANT.pack(i != 0, i, 0, 0, 0, i + 1, 0, name, 1, 1, 1)
    return h + bytes([P.NUM_CARS]) + cars


def session_history(h: bytes, car_idx=0, laps=()) -> bytes:
    """laps: sequence of (lap_time_ms, valid)."""
    head = struct.pack("<7B", car_idx, len(laps), 1, 1, 1, 1, 1)
    body = b""
    for i in range(100):
        if i < len(laps):
            t, valid = laps[i]
            s = t // 3
            body += P.LAP_HISTORY.pack(t, s % 60000, s // 60000, s % 60000, s // 60000,
                                       (t - 2 * s) % 60000, (t - 2 * s) // 60000,
                                       0x0F if valid else 0x0E)
        else:
            body += P.LAP_HISTORY.pack(0, 0, 0, 0, 0, 0, 0, 0)
    stints = P.STINT_HISTORY.pack(255, 16, 16) + b"\x00" * 21
    return h + head + body + stints


def fake_session(laps: int = 3, hz: int = 20, track_length: float = 2000.0):
    """Yield (session_time, datagram) for a short fake practice session on a
    simple oval with two braking zones. Lap 2 has a front-left lock-up."""
    lap_time = 60.0
    frame = 0
    t = 0.0
    last_ms = 0
    done = []
    dt = 1.0 / hz
    yield t, session(header(P.PACKET_SESSION, t, frame), track_length=int(track_length))
    yield t, participants(header(P.PACKET_PARTICIPANTS, t, frame))
    for lap in range(1, laps + 1):
        lt = 0.0
        while lt < lap_time:
            dist = lt / lap_time * track_length
            frac = dist / track_length
            in_brake = 0.2 < frac < 0.25 or 0.7 < frac < 0.75
            in_corner = 0.25 < frac < 0.35 or 0.75 < frac < 0.85
            speed = 120 if in_corner else (180 if in_brake else 290)
            brake = 0.9 if in_brake else 0.0
            throttle = 0.0 if in_brake else (0.6 if in_corner else 1.0)
            steer = 0.5 if in_corner else 0.0
            slip_fl = -0.35 if (lap == 2 and 0.21 < frac < 0.23) else 0.0
            ang = frac * 2 * math.pi
            pos = (math.cos(ang) * 300, 0.0, math.sin(ang) * 300)
            for pkt in (
                lap_data(header(P.PACKET_LAP_DATA, t, frame), lap, dist, int(lt * 1000), last_ms,
                         sector=min(int(frac * 3), 2)),
                car_telemetry(header(P.PACKET_CAR_TELEMETRY, t, frame), speed, throttle, steer, brake,
                              gear=4 if in_corner else 7),
                motion(header(P.PACKET_MOTION, t, frame), pos, g_lat=2.5 if in_corner else 0.0,
                       g_long=-4.0 if in_brake else 0.5),
                motion_ex(header(P.PACKET_MOTION_EX, t, frame), slip=(0.0, 0.0, slip_fl, 0.0)),
            ):
                yield t, pkt
            if frame % hz == 0:
                yield t, car_status(header(P.PACKET_CAR_STATUS, t, frame), tyre_age=lap - 1)
                yield t, car_damage(header(P.PACKET_CAR_DAMAGE, t, frame),
                                    wear=tuple(1.5 * lap for _ in range(4)))
            frame += 1
            t += dt
            lt += dt
        last_ms = int(lap_time * 1000) - lap * 150  # each lap a bit quicker
        done.append((last_ms, True))
        yield t, session_history(header(P.PACKET_SESSION_HISTORY, t, frame), laps=done)
        lap_time -= 0.15
    # One final lap-data packet so the last lap is closed off.
    yield t, lap_data(header(P.PACKET_LAP_DATA, t, frame), laps + 1, 0.0, 0, last_ms)
    yield t, button(header(P.PACKET_EVENT, t, frame), 0x00100000)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Send or record a fake F1 23 session")
    ap.add_argument("--file", help="write a recording instead of sending UDP")
    ap.add_argument("--port", type=int, default=20777)
    ap.add_argument("--laps", type=int, default=3)
    ap.add_argument("--speed", type=float, default=1.0, help="0 = as fast as possible")
    args = ap.parse_args(argv)
    if args.file:
        with Recorder(args.file) as rec:
            for t, pkt in fake_session(args.laps):
                rec.write(pkt, 1_700_000_000 + t)
        print(f"Wrote {rec.count} packets to {args.file}")
        return 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start = time.monotonic()
    for t, pkt in fake_session(args.laps):
        if args.speed > 0:
            delay = start + t / args.speed - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        sock.sendto(pkt, ("127.0.0.1", args.port))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

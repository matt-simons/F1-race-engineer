"""Command line entry point.

    python -m f1engineer live              show live telemetry and record the session
    python -m f1engineer live --no-record  show live telemetry only
    python -m f1engineer replay FILE       play a recording through the console
    python -m f1engineer replay FILE --udp send a recording to port 20777 (test other tools)
    python -m f1engineer inspect FILE      summary of what a recording contains
    python -m f1engineer export FILE       write each lap of the player's inputs to CSV
"""

from __future__ import annotations

import argparse
import csv
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from . import console
from .listener import DEFAULT_PORT, listen
from .recording import Recorder, read_recording, replay, replay_to_udp
from .state import SessionState
from . import packets as P


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def cmd_live(args) -> int:
    # Treat closing the window / being killed like Ctrl+C so the file is saved.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _raise_interrupt)
    state = SessionState()
    rec = None
    if not args.no_record:
        path = Path(args.out_dir) / f"session_{datetime.now():%Y%m%d_%H%M%S}.f1rec"
        rec = Recorder(path)
        print(f"Recording to {path}")

    def on_lap(kind, payload):
        if kind == "lap_complete":
            lap = payload["lap"]
            print(f"\nLap {lap.lap_num} complete: {console._ms(lap.lap_time_ms)}"
                  f"{' (invalid)' if lap.invalid else ''}, {len(lap.samples)} samples")

    state.subscribe(on_lap)

    def handle(data: bytes, t: float) -> None:
        if rec:
            rec.write(data, t)
        state.feed(data)

    last = [0.0]

    def tick():
        if args.quiet:
            return
        now = time.monotonic()
        if now - last[0] >= 0.2:
            console.draw(state)
            last[0] = now

    print(f"Listening on UDP port {args.port}. In F1 23: Settings > Telemetry > UDP Telemetry On, "
          f"UDP Format 2023, port {args.port}. Ctrl+C to stop.")
    try:
        listen(handle, port=args.port, idle_callback=tick, idle_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if rec:
            rec.close()
            print(f"\nSaved {rec.count} packets to {rec.path}")
    return 0


def cmd_replay(args) -> int:
    if args.udp:
        n = replay_to_udp(args.file, port=args.port, speed=args.speed)
        print(f"Sent {n} packets")
        return 0
    state = SessionState()
    last = 0.0
    try:
        for data in replay(args.file, args.speed):
            state.feed(data)
            now = time.monotonic()
            if now - last >= 0.2:
                console.draw(state)
                last = now
    except KeyboardInterrupt:
        pass
    console.draw(state)
    return 0


def _load(path) -> SessionState:
    state = SessionState()
    for _, data in read_recording(path):
        state.feed(data)
    return state


def cmd_inspect(args) -> int:
    first = last = None
    n = 0
    for t, _ in read_recording(args.file):
        first = first if first is not None else t
        last = t
        n += 1
    state = _load(args.file)
    dur = (last - first) if n else 0
    print(f"{args.file}: {n} packets over {dur / 60:.1f} min")
    if state.session:
        s = state.session
        print(f"Track: {s['track_name']}  Session: {s['session_type_name']}  Weather: {s['weather_name']}")
    if state.status:
        print(f"Assists: TC {state.status['traction_control_name']}, "
              f"ABS {'on' if state.status['anti_lock_brakes'] else 'off'}")
    print("Packets: " + ", ".join(f"{k} {v}" for k, v in sorted(state.packet_counts.items())))
    if state.errors:
        print(f"Bad packets: {state.errors} (last: {state.last_error})")
    best = state.best_lap()
    print("Laps (last session in file):")
    for num in sorted(state.laps):
        lap = state.laps[num]
        flag = " *best*" if best is lap else (" invalid" if lap.invalid else "")
        print(f"  Lap {num:2d}: {console._ms(lap.lap_time_ms)}  {len(lap.samples):5d} samples{flag}")
    return 0


CSV_FIELDS = ["session_time", "lap_distance", "speed_kph", "throttle", "brake", "steer", "gear",
              "engine_rpm", "g_lat", "g_long", "pos_x", "pos_z"]


def cmd_export(args) -> int:
    state = _load(args.file)
    out_dir = Path(args.out_dir or Path(args.file).with_suffix(""))
    out_dir.mkdir(parents=True, exist_ok=True)
    for num, lap in sorted(state.laps.items()):
        path = out_dir / f"lap_{num:02d}.csv"
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(CSV_FIELDS
                       + [f"brake_temp_{x}" for x in P.WHEELS]
                       + [f"tyre_surface_{x}" for x in P.WHEELS]
                       + [f"tyre_inner_{x}" for x in P.WHEELS]
                       + [f"slip_ratio_{x}" for x in P.WHEELS])
            for s in lap.samples:
                w.writerow([getattr(s, k) for k in CSV_FIELDS]
                           + list(s.brakes_temperature)
                           + list(s.tyres_surface_temperature)
                           + list(s.tyres_inner_temperature)
                           + list(s.wheel_slip_ratio or ("",) * 4))
        print(f"Wrote {path} ({len(lap.samples)} rows)")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="f1engineer", description="F1 23 race engineer")
    sub = p.add_subparsers(dest="cmd", required=True)

    live = sub.add_parser("live", help="show live telemetry and record it")
    live.add_argument("--port", type=int, default=DEFAULT_PORT)
    live.add_argument("--no-record", action="store_true")
    live.add_argument("--out-dir", default="recordings")
    live.add_argument("--quiet", action="store_true", help="don't draw the live readout")
    live.set_defaults(fn=cmd_live)

    rp = sub.add_parser("replay", help="play back a recording")
    rp.add_argument("file")
    rp.add_argument("--speed", type=float, default=1.0, help="0 = as fast as possible")
    rp.add_argument("--udp", action="store_true", help="send to UDP instead of the console")
    rp.add_argument("--port", type=int, default=DEFAULT_PORT)
    rp.set_defaults(fn=cmd_replay)

    ins = sub.add_parser("inspect", help="summarise a recording")
    ins.add_argument("file")
    ins.set_defaults(fn=cmd_inspect)

    ex = sub.add_parser("export", help="export player laps to CSV")
    ex.add_argument("file")
    ex.add_argument("--out-dir")
    ex.set_defaults(fn=cmd_export)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

"""Command line entry point.

    python -m f1engineer live              show live telemetry and record the session
    python -m f1engineer live --no-record  show live telemetry only
    python -m f1engineer replay FILE       play a recording through the console
    python -m f1engineer replay FILE --udp send a recording to port 20777 (test other tools)
    python -m f1engineer inspect FILE      summary of what a recording contains
    python -m f1engineer export FILE       write each lap of the player's inputs to CSV
    python -m f1engineer say "Box box"     test the engineer's voice
    python -m f1engineer download-voice    fetch the Piper voice model
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
from .calls import CallManager
from .engineer import PRERENDER, Engineer
from .voice import (DEFAULT_VOICE, DEFAULT_VOICE_DIR, Voice, download_voice, make_player,
                    make_tts, wav_duration)


def _raise_interrupt(*_):
    raise KeyboardInterrupt


def _make_engineer(args, state: SessionState, log):
    """Build voice + call manager + engineer, or return (None, None) if muted."""
    if getattr(args, "mute", False):
        return None, None
    tts = make_tts(args.voice_engine, args.voice_model, log=print)
    print(f"[voice] using {tts.name}")
    voice = Voice(tts, radio=not args.no_radio)
    calls = CallManager(state, voice, make_player(), log=log, mode=args.mode)
    eng = Engineer(state, calls, repeat_button=args.repeat_button,
                   status_button=args.status_button)
    voice.prerender(PRERENDER)
    calls.start()
    return calls, eng


def cmd_live(args) -> int:
    # Treat closing the window / being killed like Ctrl+C so the file is saved.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _raise_interrupt)
    state = SessionState()
    # The live readout shows recent calls, so only print them when it's off.
    calls, eng = _make_engineer(args, state, log=print if args.quiet else (lambda _m: None))
    rec = None
    if not args.no_record:
        path = Path(args.out_dir) / f"session_{datetime.now():%Y%m%d_%H%M%S}.f1rec"
        rec = Recorder(path)
        print(f"Recording to {path}")

    def handle(data: bytes, t: float) -> None:
        if rec:
            rec.write(data, t)
        state.feed(data)
        if eng:
            eng.update()

    last = [0.0]

    def tick():
        if args.quiet:
            return
        now = time.monotonic()
        if now - last[0] >= 0.2:
            console.draw(state, calls)
            last[0] = now

    print(f"Listening on UDP port {args.port}. In F1 23: Settings > Telemetry > UDP Telemetry On, "
          f"UDP Format 2023, port {args.port}. Ctrl+C to stop.")
    try:
        listen(handle, port=args.port, idle_callback=tick, idle_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        if calls:
            calls.stop()
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
    calls, eng = (_make_engineer(args, state, log=lambda _m: None) if args.speak
                  else (None, None))
    last = 0.0
    try:
        for data in replay(args.file, args.speed):
            state.feed(data)
            if eng:
                eng.update()
            now = time.monotonic()
            if now - last >= 0.2:
                console.draw(state, calls)
                last = now
    except KeyboardInterrupt:
        pass
    console.draw(state, calls)
    if calls:
        time.sleep(1.0)
        calls.stop()
    return 0


def cmd_say(args) -> int:
    tts = make_tts(args.voice_engine, args.voice_model)
    print(f"[voice] using {tts.name}")
    voice = Voice(tts, radio=not args.no_radio)
    t0 = time.monotonic()
    wav = voice.render(args.text)
    print(f"Rendered in {time.monotonic() - t0:.2f} s")
    if wav:
        player = make_player()
        player.play(wav)
        time.sleep(wav_duration(wav) + 0.3)
        if args.save:
            Path(args.save).write_bytes(wav)
            print(f"Saved {args.save}")
    else:
        print(args.text)
    return 0


def cmd_download_voice(args) -> int:
    download_voice(args.name, Path(args.dest))
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


def _voice_args(p) -> None:
    p.add_argument("--voice-engine", default="auto", choices=["auto", "piper", "espeak", "sapi", "print"],
                   help="auto tries Piper, then espeak-ng (Linux) or the Windows voice, then text only")
    p.add_argument("--voice-model", help=f"Piper .onnx model (default "
                   f"{DEFAULT_VOICE_DIR / (DEFAULT_VOICE + '.onnx')})")
    p.add_argument("--no-radio", action="store_true", help="turn off the team-radio effect")


def _engineer_args(p) -> None:
    p.add_argument("--mode", choices=["practice", "qualifying", "race"],
                   help="override the mode picked from the session type")
    p.add_argument("--repeat-button", default="UDP_ACTION_1",
                   help="button that repeats the last call (default UDP_ACTION_1)")
    p.add_argument("--status-button", default="UDP_ACTION_2",
                   help="button that asks for a status update (default UDP_ACTION_2)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="f1engineer", description="F1 23 race engineer")
    sub = p.add_subparsers(dest="cmd", required=True)

    live = sub.add_parser("live", help="show live telemetry and record it")
    live.add_argument("--port", type=int, default=DEFAULT_PORT)
    live.add_argument("--no-record", action="store_true")
    live.add_argument("--out-dir", default="recordings")
    live.add_argument("--quiet", action="store_true", help="don't draw the live readout")
    live.add_argument("--mute", action="store_true", help="no engineer, telemetry only")
    _voice_args(live)
    _engineer_args(live)
    live.set_defaults(fn=cmd_live)

    rp = sub.add_parser("replay", help="play back a recording")
    rp.add_argument("file")
    rp.add_argument("--speed", type=float, default=1.0, help="0 = as fast as possible")
    rp.add_argument("--udp", action="store_true", help="send to UDP instead of the console")
    rp.add_argument("--port", type=int, default=DEFAULT_PORT)
    rp.add_argument("--speak", action="store_true", help="run the engineer over the replay")
    _voice_args(rp)
    _engineer_args(rp)
    rp.set_defaults(fn=cmd_replay)

    ins = sub.add_parser("inspect", help="summarise a recording")
    ins.add_argument("file")
    ins.set_defaults(fn=cmd_inspect)

    ex = sub.add_parser("export", help="export player laps to CSV")
    ex.add_argument("file")
    ex.add_argument("--out-dir")
    ex.set_defaults(fn=cmd_export)

    say = sub.add_parser("say", help="speak a test phrase")
    say.add_argument("text")
    say.add_argument("--save", help="also save the audio to a .wav file")
    _voice_args(say)
    say.set_defaults(fn=cmd_say)

    dv = sub.add_parser("download-voice", help="download a Piper voice model")
    dv.add_argument("name", nargs="?", default=DEFAULT_VOICE,
                    help=f"voice name (default {DEFAULT_VOICE})")
    dv.add_argument("--dest", default=str(DEFAULT_VOICE_DIR))
    dv.set_defaults(fn=cmd_download_voice)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())

# F1 23 Race Engineer

A race engineer for F1 23 that reads the game's live telemetry and coaches you over voice: tyre and brake temperatures, wear, lock-ups, traction, braking points, turn-in and race strategy. Urgent calls come from fast local rules; deeper coaching comes from Claude Haiku; speech is a free local voice (Piper).

**Status: Milestone 1 (telemetry in) done.** It decodes the game's telemetry, shows it live, records sessions and exports laps. Voice and coaching come next. The full plan is in [`docs/PLAN.md`](docs/PLAN.md).

## Requirements
- Windows PC running F1 23
- Python 3.10 or newer ([python.org](https://www.python.org/downloads/), tick "Add Python to PATH")
- No extra packages needed for Milestone 1

## Game setup
In F1 23: **Settings → Telemetry Settings**
| Setting | Value |
|---|---|
| UDP Telemetry | On |
| UDP Broadcast Mode | Off |
| UDP IP Address | 127.0.0.1 |
| UDP Port | 20777 |
| UDP Send Rate | 60 Hz |
| UDP Format | 2023 |
| Your Telemetry | Public |

**Push-to-talk (used from Milestone 2):** in **Settings → Controls**, bind a spare Xbox controller button (e.g. an unused D-pad direction) to **UDP Action 1**. A second one to **UDP Action 2** is optional. The live readout's "Last event" line shows the press arriving.

## Use
From this folder in a terminal:

```
python -m f1engineer live                 # live readout, records to recordings\
python -m f1engineer live --no-record     # live readout only
python -m f1engineer inspect recordings\session_XXXX.f1rec   # laps, assists, packet counts
python -m f1engineer export  recordings\session_XXXX.f1rec   # one CSV per lap
python -m f1engineer replay  recordings\session_XXXX.f1rec   # replay through the readout
python -m f1engineer replay  FILE --udp   # resend to port 20777 as if the game were running
```

No game handy? Fake a 3-lap session:
```
python -m f1engineer.simulate --file demo.f1rec
python -m f1engineer inspect demo.f1rec
```

## Tests
```
python -m unittest discover -s tests -t .
```

## Layout
| File | What it does |
|---|---|
| `f1engineer/packets.py` | Decodes all F1 23 packet types the engineer needs |
| `f1engineer/state.py` | Live session state + per-lap traces keyed by lap distance |
| `f1engineer/listener.py` | UDP listener |
| `f1engineer/recording.py` | Crash-safe session recording and replay |
| `f1engineer/console.py` | Live text readout |
| `f1engineer/simulate.py` | Builds fake packets for tests and demos |

## Roadmap
1. ~~Telemetry in~~ ✅
2. Voice out (Piper) + call queue + quiet zones + controller push-to-talk
3. Rules engine: lock-ups, TC intervention, temps, wear, fuel, ERS
4. Corner model + reference lap
5. Haiku coach
6. Race strategy
7. Post-session review dashboard

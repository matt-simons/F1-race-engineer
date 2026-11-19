# F1 23 Race Engineer

A race engineer for F1 23 that reads the game's live telemetry and coaches you over voice: tyre and brake temperatures, wear, lock-ups, traction, braking points, turn-in and race strategy. Urgent calls come from fast local rules; deeper coaching comes from Claude Haiku; speech is a free local voice (Piper).

**Status: Milestones 1–2 done.** It reads the game's telemetry, records sessions, and the engineer talks: radio check, lap times against your best, track-limit deletions, position changes, safety car and flags, penalties, and answers to your controller buttons. It only talks on straights, never in braking zones. Coaching (lock-ups, temps, wear) comes next. The full plan is in [`docs/PLAN.md`](docs/PLAN.md).

## Requirements
- **Linux (Bazzite, Fedora, SteamOS…)** with F1 23 running through Steam/Proton, or Windows
- Python 3.10 or newer (already on Bazzite)

## Setup on Bazzite / Linux (one-off)
In Desktop Mode, open a terminal (Konsole):
```
git clone https://github.com/matt-simons/F1-race-engineer.git
cd F1-race-engineer
./scripts/setup-linux.sh
```
This makes a private Python environment in `.venv` (nothing is installed system-wide, so it's fine on Bazzite's read-only system), installs the free Piper voice, downloads a British male voice (`en_GB-alan-medium`, ~60 MB) into `voices/`, and says a test phrase. Sound plays through PipeWire to your default output, the same place as the game audio.

Then start it with `./engineer.sh`, before or after launching the game.

**Gaming Mode:** in Steam, F1 23 → Properties → Launch Options:
```
/home/YOUR_USER/F1-race-engineer/engineer.sh --with-game %command%
```
(Put any launch options you already use just before `%command%`.) The engineer starts with the game and stops when you quit; its output goes to `recordings/engineer.log`.

## Setup on Windows (one-off)
```
pip install piper-tts
python -m f1engineer download-voice
python -m f1engineer say "Radio check. Box box, box this lap."
```

## Voices
The default is a British male voice (`en_GB-alan-medium`). Other voices: browse [Piper voices](https://rhasspy.github.io/piper-samples/) and pass the name, e.g. `python -m f1engineer download-voice en_GB-northern_english_male-medium`, then `--voice-model voices/en_GB-northern_english_male-medium.onnx`.

If Piper isn't available, the engineer falls back to espeak-ng on Linux (if installed) or the built-in Windows voice, both more robotic, and failing that prints calls as text.

Voice options (for `live`, `replay --speak` and `say`):
| Option | What it does |
|---|---|
| `--voice-engine piper\|espeak\|sapi\|print` | Force an engine (`print` = text only) |
| `--voice-model PATH` | Use a different Piper voice |
| `--no-radio` | Turn off the team-radio effect |

The engineer plays through your default audio output (PipeWire on Linux, the default device on Windows). If you play on a headset, make it the default output.

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

**Controller buttons:** in **Settings → Controls**, bind two spare Xbox controller buttons to **UDP Action 1** and **UDP Action 2** (a free D-pad direction or stick click works well).
| Button | Engineer |
|---|---|
| UDP Action 1 | Repeats the last call |
| UDP Action 2 | Status update: position and gap (race) or best lap (practice), tyres and wear, fuel (race), battery |

Want different buttons? `--repeat-button` / `--status-button` take any name shown in the live readout's "Last event" line (e.g. `DPAD_DOWN`).

## Use
From this folder in a terminal. On Linux, `./engineer.sh` = `live`; for the other commands use `.venv/bin/python -m f1engineer …` (or run `source .venv/bin/activate` first, then `python -m f1engineer …` as below).

```
python -m f1engineer live                 # engineer + live readout, records to recordings/
python -m f1engineer live --mute          # telemetry only, no engineer
python -m f1engineer live --mode race     # force a mode (normally picked from the session)
python -m f1engineer inspect recordings/session_XXXX.f1rec   # laps, assists, packet counts
python -m f1engineer export  recordings/session_XXXX.f1rec   # one CSV per lap
python -m f1engineer replay  recordings/session_XXXX.f1rec --speak   # rerun the engineer on a recording
```

No game handy? Fake a 3-lap session (in a second terminal while `live` is running):
```
python -m f1engineer.simulate
```

## When the engineer talks
| Priority | Examples | When |
|---|---|---|
| P1 urgent | Safety car, red flag, penalty, button answers | Straight away, cuts off anything less urgent |
| P2 info | Lap time, position, track limits, DRS | On a straight with time to finish before the next braking zone |
| P3 coaching | (coming in milestone 3) | Same as P2, plus a per-lap limit |

The next braking zone comes from your best valid lap (or your last lap until you've set one). Modes come from the session type:
- **Practice:** everything, up to 3 coaching calls a lap.
- **Qualifying:** silent on a push lap except urgent calls; lap time held until the in-lap.
- **Race:** position changes, DRS, fastest lap, chequered flag; 1 coaching call a lap.

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
| `f1engineer/voice.py` | Piper / espeak / Windows voice, radio effect, PipeWire or Windows playback |
| `f1engineer/calls.py` | Call manager: priorities, quiet zones, cool-downs, budgets, modes |
| `f1engineer/engineer.py` | What the engineer says and when (events, laps, buttons) |
| `f1engineer/speech.py` | Radio-style numbers ("1 29.4", "four tenths") |
| `f1engineer/simulate.py` | Builds fake packets for tests and demos |
| `engineer.sh` | Linux launcher (terminal or Steam launch option) |
| `scripts/setup-linux.sh` | One-off Linux setup |

## Roadmap
1. ~~Telemetry in~~ ✅
2. ~~Voice out + call queue + quiet zones + controller buttons~~ ✅
3. Rules engine: lock-ups, TC intervention, temps, wear, fuel, ERS
4. Corner model + reference lap
5. Haiku coach
6. Race strategy
7. Post-session review dashboard

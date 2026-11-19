# F1 23 Race Engineer — Project Plan (v0.3)

## Decisions
| Topic | Decision |
|---|---|
| Platform | **PC**, app runs on the same machine as the game, telemetry on localhost |
| Use | **Practice and races equally**, so all modes are first-class |
| Input | **Xbox controller (pad)**, coaching tuned for triggers and stick |
| Assists | **ABS off, partial traction control** |
| Push-to-talk | **Xbox controller button**, via the game's UDP Action bindings |
| Voice | **Free, local TTS (Piper)** |
| Language | Python 3.10+ |

## 1. Goal
A desktop app that listens to F1 23's live telemetry, works out what you're doing well and badly, and talks to you over the game audio like a real race engineer. It gives short, timely calls during the lap and deeper coaching between laps, so you actually get faster.

**Success looks like:**
- Urgent calls (lock-up, overheating brakes, box this lap) heard within ~0.5 s of the event.
- Coaching calls name a specific corner and a specific fix ("Turn 4, you're braking 15 metres early versus your best lap").
- It never talks over you in a braking zone, and never repeats the same nag every lap.
- Lap times drop measurably over a practice session; fewer lock-ups and mistakes in races.

## 2. How it works (architecture)

```
F1 23 ──UDP 127.0.0.1:20777──► Ingest ──► State store ──► Analysis ──► Call queue ──► Piper TTS ──► Headset
                               (decode)   (per-lap,        ├ Rules engine (real-time, no AI)
                                           per-corner)     └ Haiku coach (between laps / on straights)
        Controller button ──► Event packet ──► "Ask engineer" / "Repeat"
```

**Two-speed brain, the key design decision:**
| Layer | Speed | Handles | Why |
|---|---|---|---|
| Rules engine (local code) | <50 ms | Lock-ups, TC intervention, temp warnings, tyre/fuel/ERS thresholds, pit window, flags, gaps | An AI round trip is too slow and costly to run 60 times a second. Urgent calls must be instant. |
| Claude Haiku coach | 1–3 s, a few times a lap | Interpreting a lap or stint vs your reference, picking the 1–2 most valuable fixes, phrasing it like an engineer | Good at prioritising and explaining; fed a compact summary, not raw data. |

Haiku never sees raw 60 Hz data. The app turns each lap into a small JSON summary (per-corner brake point, min speed, throttle pickup, lock-ups, slip events, temps), and Haiku works on that.

## 3. Components

### 3.1 Telemetry ingest ✅ (M1)
- In game: Settings → Telemetry → UDP On, format **2023**, IP 127.0.0.1, port 20777, send rate 60 Hz, broadcast off.
- Decoded packets: Car Telemetry, Motion, Motion Ex, Lap Data, Car Status, Car Damage, Session, Event (incl. button presses), Participants, Session History.

### 3.2 State store
- Per-lap trace keyed by **lap distance**, so laps can be compared metre by metre. ✅ (M1)
- **Track map / corner model**: auto-detected on your first clean lap (corners = big braking + steering zones), saved per track. Optional hand-edited names.
- **Reference lap**: your personal best clean lap, updated automatically. Later: import a faster ghost.

### 3.3 Analysis: what it coaches
| Area | What we measure | Example call |
|---|---|---|
| Braking | Brake point vs reference, peak trigger pressure, release (trail braking), lock-ups per wheel | "Front-left locking into Turn 1. Less trigger on the initial hit." |
| Turn-in & apex | Steering start vs reference, minimum corner speed, line | "8 km/h down at the apex of Turn 9. Carry more entry speed." |
| Traction & exit | Throttle pickup point, rear slip, TC cutting power, time to full throttle | "TC working hard out of the hairpin. Squeeze the throttle." |
| Tyres | Surface vs inner temp, temp window per compound, wear rate, balance, flat-spots from lock-ups | "Rears overheating. Manage the exits for two laps." |
| Brakes | Brake temps vs window (start: cold <~400°C, hot >~1000°C; calibrated from your data) | "Brakes cooling behind the safety car. Weave and stab them." |
| Race craft | Gaps, DRS, fuel delta, ERS, pit window, undercut threat | "Gap to Norris 1.2, he's pitting. Box this lap?" |
| Consistency | Lap/sector variance, where time is lost most often | "Sector 2 is where you're dropping it, 0.4 every lap." |

**ABS off: lock-ups are the top coaching priority.** Detected per wheel from slip ratio at 60 Hz, logged per corner and wheel with trigger pressure. Repeats at the same corner become a P1 call; the safe brake pressure there is learned from your clean laps.

**Partial traction control:** flag **TC cutting power** (rear slip rising, high throttle, acceleration short of your best lap there). Fix: smoother throttle or short-shifting. Stretch goal: say which corners you're ready to take with TC off.

**Pad-specific coaching:** trigger advice about how fast you squeeze and release; steering advice about when you turn in and how long you hold lock. Later: suggest controller settings (trigger saturation, steering linearity).

### 3.4 Call manager ✅ (M2)
- Priority queue: P1 urgent (can interrupt), P2 race info, P3 coaching.
- Quiet zones: P2/P3 only on straights.
- De-duplication, cool-downs, escalation, per-lap talk budget.
- Modes chosen from session type: Practice (chatty, debriefs), Qualifying (silent on push lap), Race (strategy first).

### 3.5 Push-to-talk (Xbox controller) ✅ (M2)
- Bind spare buttons to **UDP Action 1/2** in game controls. Action 1: repeat last call. Action 2: status update.
- Later: hold to ask a spoken question (local Whisper → Haiku).

### 3.6 Text-to-speech: Piper ✅ (M2, Windows voice as fallback)
- Free, local, low delay. Optional radio effect. Common phrases pre-rendered for zero delay.

### 3.7 Haiku integration
- End-of-lap (and on push-to-talk) summary → Haiku → structured calls with priority and corner.
- Terse radio persona, one fix at a time. Hard monthly cost cap. Rules engine keeps working offline.

## 4. Tech stack
Python, stdlib for M1; later NumPy, SQLite, Anthropic SDK, Piper, sounddevice.

## 5. Milestones
| # | Milestone | Status |
|---|---|---|
| M1 | Telemetry in: decode, live readout, record/replay, CSV export | ✅ Done |
| M2 | Voice out + call queue + quiet zones + controller push-to-talk | ✅ Done |
| M3 | Rules engine: lock-ups, TC intervention, temps, wear, fuel, ERS | Next |
| M4 | Corner model + reference lap | |
| M5 | Haiku coach | |
| M6 | Race strategy | |
| M7 | Review dashboard (optional) | |

## 6. Risks
- Talking too much or at the wrong time (biggest UX risk).
- Bad corner detection on some tracks → manual edits.
- TC activity is inferred, needs calibration.
- Lock-up false alarms from kerbs → require several frames + brake input.
- Game-specific thresholds → calibrate from your data.
- API delay spikes → Haiku only for non-urgent calls.
